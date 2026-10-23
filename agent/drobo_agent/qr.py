"""
A small QR encoder,
so pairing is a camera scan instead of typing a token.

Standard library only -- no pillow, no qrcode package. Byte mode, error
correction level M (~15% recoverable, which is the right trade-off for
scanning off a screen), versions 1-10. That holds 213 bytes
(MAX_PAYLOAD_BYTES) -- 213 ASCII characters, fewer of anything wider
in UTF-8 -- far more than a pairing URL needs.

Implemented from the specification, ISO/IEC 18004 -- nobody's source
code was read to write this. The Reed-Solomon error correction below
runs over GF(256) with primitive polynomial 0x11D; that polynomial and
the GF arithmetic are the standard's own mathematics, not anyone's
implementation. EC = error correction.

    from drobo_agent import qr
    svg = qr.svg("http://10.0.0.50:7420/?token=...")

DroboPix paired exactly this way -- a QR code shown in Drobo Dashboard
that you scanned with the phone. It was the right call then and it still
is: nobody should be typing a 32-character token on a phone keyboard.
"""

from __future__ import annotations


# --------------------------------------------------------------------------
# GF(256) arithmetic, primitive polynomial 0x11D
# --------------------------------------------------------------------------
def _build_gf_tables() -> tuple[list[int], list[int]]:
    """The antilog (alpha^i) and log tables. The antilog table holds 512
    entries so _gf_multiply can index it with a sum of two logs."""
    antilog_table = [0] * 512
    log_table = [0] * 256
    element = 1
    for exponent in range(255):
        antilog_table[exponent] = element
        log_table[element] = exponent
        element <<= 1
        if element & 0x100:
            element ^= 0x11D
    for exponent in range(255, 512):
        antilog_table[exponent] = antilog_table[exponent - 255]
    return antilog_table, log_table


_GF_ANTILOG_TABLE, _GF_LOG_TABLE = _build_gf_tables()


def _gf_multiply(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _GF_ANTILOG_TABLE[_GF_LOG_TABLE[left] + _GF_LOG_TABLE[right]]


def _generator_polynomial(ec_codeword_count: int) -> list[int]:
    """
    Product of (x - alpha^i) for i in 0..ec_codeword_count-1, highest
    degree first so that polynomial[0] == 1. _ec_codewords below depends
    on that ordering.
    """
    polynomial = [1]
    for i in range(ec_codeword_count):
        product = [0] * (len(polynomial) + 1)
        for term_index, coefficient in enumerate(polynomial):
            product[term_index] ^= coefficient          # c * x
            product[term_index + 1] ^= _gf_multiply(    # c * alpha^i
                coefficient, _GF_ANTILOG_TABLE[i])
        polynomial = product
    return polynomial


def _ec_codewords(data: list[int], ec_codeword_count: int) -> list[int]:
    generator = _generator_polynomial(ec_codeword_count)
    remainder = list(data) + [0] * ec_codeword_count
    for i in range(len(data)):
        lead_coefficient = remainder[i]
        if lead_coefficient:
            for j in range(1, len(generator)):
                remainder[i + j] ^= _gf_multiply(generator[j],
                                                 lead_coefficient)
    return remainder[len(data):]


# --------------------------------------------------------------------------
# Version tables, error-correction level M
#   version -> (ec per block, group1 blocks, group1 data cw,
#                             group2 blocks, group2 data cw)
# --------------------------------------------------------------------------
_LEVEL_M_BLOCK_LAYOUT = {
    1:  (10, 1, 16, 0, 0),
    2:  (16, 1, 28, 0, 0),
    3:  (26, 1, 44, 0, 0),
    4:  (18, 2, 32, 0, 0),
    5:  (24, 2, 43, 0, 0),
    6:  (16, 4, 27, 0, 0),
    7:  (18, 4, 31, 0, 0),
    8:  (22, 2, 38, 2, 39),
    9:  (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
}

_ALIGNMENT_PATTERN_CENTRES = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}

_LEVEL_M_INDICATOR_BITS = 0b00          # level M
_FORMAT_INFO_MASK = 0b101010000010010


def _data_codeword_capacity(version: int) -> int:
    (ec_per_block, group1_blocks, group1_data_codewords,
     group2_blocks, group2_data_codewords) = _LEVEL_M_BLOCK_LAYOUT[version]
    return (group1_blocks * group1_data_codewords
            + group2_blocks * group2_data_codewords)


# What version 10 holds: its data codewords, less byte mode's 4-bit
# indicator and 16-bit count. ISO/IEC 18004 tabulates 213 for 10-M.
MAX_PAYLOAD_BYTES = (_data_codeword_capacity(10) * 8 - 4 - 16) // 8


def _pick_version(payload_length: int) -> int:
    for version in range(1, 11):
        # 4 bits mode + count field + payload,
        # rounded up to whole codewords
        count_bits = 8 if version < 10 else 16
        needed_codewords = (4 + count_bits + payload_length * 8 + 7) // 8
        if needed_codewords <= _data_codeword_capacity(version):
            return version
    raise ValueError(f"payload too long for this encoder: "
                     f"{payload_length} bytes, "
                     f"the most it holds is {MAX_PAYLOAD_BYTES}")


# --------------------------------------------------------------------------
# Data encoding
# --------------------------------------------------------------------------
def _encode_data(payload: bytes, version: int) -> list[int]:
    count_bits = 8 if version < 10 else 16
    bits: list[int] = []

    def append_bits(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    append_bits(0b0100, 4)                      # byte mode
    append_bits(len(payload), count_bits)
    for byte in payload:
        append_bits(byte, 8)

    capacity_bits = _data_codeword_capacity(version) * 8
    append_bits(0, min(4, capacity_bits - len(bits)))   # terminator
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    pad_codewords = [0xEC, 0x11]
    i = 0
    while len(codewords) < _data_codeword_capacity(version):
        codewords.append(pad_codewords[i % 2])
        i += 1
    return codewords


def _interleave(codewords: list[int], version: int) -> list[int]:
    (ec_per_block, group1_blocks, group1_data_codewords,
     group2_blocks, group2_data_codewords) = _LEVEL_M_BLOCK_LAYOUT[version]
    data_blocks, ec_blocks, position = [], [], 0
    for _ in range(group1_blocks):
        data_blocks.append(
            codewords[position:position + group1_data_codewords])
        position += group1_data_codewords
    for _ in range(group2_blocks):
        data_blocks.append(
            codewords[position:position + group2_data_codewords])
        position += group2_data_codewords
    for block in data_blocks:
        ec_blocks.append(_ec_codewords(block, ec_per_block))

    interleaved: list[int] = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                interleaved.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            interleaved.append(block[i])
    return interleaved


# --------------------------------------------------------------------------
# Matrix construction
# --------------------------------------------------------------------------
def _new_matrix(version: int):
    size = version * 4 + 17
    module_grid = [[None] * size for _ in range(size)]

    def draw_finder_pattern(top, left):
        for row_offset in range(-1, 8):
            for column_offset in range(-1, 8):
                row, column = top + row_offset, left + column_offset
                if not (0 <= row < size and 0 <= column < size):
                    continue
                inside = (0 <= row_offset < 7 and 0 <= column_offset < 7)
                is_dark = inside and (row_offset in (0, 6)
                                      or column_offset in (0, 6)
                                      or (2 <= row_offset <= 4
                                          and 2 <= column_offset <= 4))
                module_grid[row][column] = 1 if is_dark else 0

    draw_finder_pattern(0, 0)
    draw_finder_pattern(0, size - 7)
    draw_finder_pattern(size - 7, 0)

    for i in range(8, size - 8):          # timing patterns
        bit = 1 if i % 2 == 0 else 0
        module_grid[6][i] = bit
        module_grid[i][6] = bit

    centres = _ALIGNMENT_PATTERN_CENTRES[version]
    for centre_row in centres:
        for centre_column in centres:
            if ((centre_row < 8 and centre_column < 8)
                    or (centre_row < 8 and centre_column > size - 9)
                    or (centre_row > size - 9 and centre_column < 8)):
                continue
            for row_offset in range(-2, 3):
                for column_offset in range(-2, 3):
                    grid_row = module_grid[centre_row + row_offset]
                    grid_row[centre_column + column_offset] = (
                        1 if (max(abs(row_offset), abs(column_offset)) != 1)
                        else 0)

    module_grid[size - 8][8] = 1                  # dark module

    for i in range(9):                    # reserve format areas
        if module_grid[8][i] is None:
            module_grid[8][i] = 0
        if module_grid[i][8] is None:
            module_grid[i][8] = 0
    for i in range(8):
        if module_grid[8][size - 1 - i] is None:
            module_grid[8][size - 1 - i] = 0
        if module_grid[size - 1 - i][8] is None:
            module_grid[size - 1 - i][8] = 0

    if version >= 7:
        # Reserve the two 6x3 version-information blocks, or data
        # placement would run straight through them.
        for i in range(18):
            r, c = i // 3, i % 3
            module_grid[size - 11 + c][r] = 0
            module_grid[r][size - 11 + c] = 0

    return module_grid, size


def _function_pattern_cells(version: int, size: int):
    """Which cells are function patterns (and so not data)."""
    module_grid, _ = _new_matrix(version)
    return [[module_grid[r][c] is not None for c in range(size)]
            for r in range(size)]


def _place_data_bits(module_grid, reserved, data: list[int], size: int):
    bits = []
    for codeword in data:
        for i in range(7, -1, -1):
            bits.append((codeword >> i) & 1)
    bit_index = 0
    upward = True
    column = size - 1
    while column > 0:
        if column == 6:
            column -= 1                       # skip the vertical timing column
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (column, column - 1):
                if not reserved[row][c]:
                    module_grid[row][c] = (bits[bit_index]
                                           if bit_index < len(bits) else 0)
                    bit_index += 1
        upward = not upward
        column -= 2
    return module_grid


_MASK_PATTERNS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _format_bits(mask: int) -> int:
    """15-bit format info: 5 data bits, BCH(15,5) remainder, then the
    spec XOR.
    """
    value = (_LEVEL_M_INDICATOR_BITS << 3) | mask
    remainder = value << 10
    bch_generator = 0b10100110111
    for i in range(14, 9, -1):
        if remainder & (1 << i):
            remainder ^= bch_generator << (i - 10)
    return ((value << 10) | remainder) ^ _FORMAT_INFO_MASK


def _apply_format(module_grid, size: int, mask: int):
    bits = _format_bits(mask)
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            module_grid[8][i] = bit
        elif i == 6:
            module_grid[8][7] = bit
        elif i == 7:
            module_grid[8][8] = bit
        elif i == 8:
            module_grid[7][8] = bit
        else:
            module_grid[14 - i][8] = bit
        if i < 8:
            module_grid[size - 1 - i][8] = bit
        else:
            module_grid[8][size - 15 + i] = bit
    module_grid[size - 8][8] = 1


def _version_bits(version: int) -> int:
    remainder = version << 12
    bch_generator = 0b1111100100101
    for i in range(17, 11, -1):
        if remainder & (1 << i):
            remainder ^= bch_generator << (i - 12)
    return (version << 12) | remainder


def _apply_version(module_grid, size: int, version: int):
    if version < 7:
        return
    bits = _version_bits(version)
    for i in range(18):
        bit = (bits >> i) & 1
        r, c = i // 3, i % 3
        module_grid[size - 11 + c][r] = bit
        module_grid[r][size - 11 + c] = bit


def _mask_penalty(module_grid, size: int) -> int:
    score = 0
    # rule 1: runs of five or more
    for line in list(module_grid) + [list(col) for col in zip(*module_grid)]:
        run_length, previous_cell = 1, line[0]
        for cell in line[1:]:
            if cell == previous_cell:
                run_length += 1
            else:
                if run_length >= 5:
                    score += 3 + (run_length - 5)
                run_length, previous_cell = 1, cell
        if run_length >= 5:
            score += 3 + (run_length - 5)
    # rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            if (module_grid[r][c] == module_grid[r][c + 1]
                    == module_grid[r + 1][c] == module_grid[r + 1][c + 1]):
                score += 3
    # rule 3: finder-like patterns
    finder_like_run = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    finder_like_run_reversed = list(reversed(finder_like_run))
    for line in list(module_grid) + [list(col) for col in zip(*module_grid)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == finder_like_run or window == finder_like_run_reversed:
                score += 40
    # rule 4: dark/light balance
    dark = sum(sum(row) for row in module_grid)
    dark_percent = dark * 100 // (size * size)
    score += 10 * (abs(dark_percent - 50) // 5)
    return score


def matrix(text: str) -> list[list[int]]:
    """Encode text and return the finished module grid as 0/1 rows."""
    payload = text.encode("utf-8")
    version = _pick_version(len(payload))
    data = _interleave(_encode_data(payload, version), version)

    best_grid, best_score = None, None
    for mask in range(8):
        module_grid, size = _new_matrix(version)
        reserved = _function_pattern_cells(version, size)
        _place_data_bits(module_grid, reserved, data, size)
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _MASK_PATTERNS[mask](r, c):
                    module_grid[r][c] ^= 1
        _apply_format(module_grid, size, mask)
        _apply_version(module_grid, size, version)
        grid = [[cell or 0 for cell in row] for row in module_grid]
        score = _mask_penalty(grid, size)
        if best_score is None or score < best_score:
            best_grid, best_score = grid, score
    return best_grid


def svg(text: str, pixels_per_module: int = 6,
        quiet_zone_modules: int = 4) -> str:
    """Render as a self-contained SVG.
    currentColor so it follows the theme.
    """
    grid = matrix(text)
    size = len(grid)
    image_size = (size + quiet_zone_modules * 2) * pixels_per_module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_size}" '
        f'height="{image_size}" '
        f'viewBox="0 0 {image_size} {image_size}" role="img" '
        f'aria-label="Pairing QR code">',
        f'<rect width="{image_size}" height="{image_size}" fill="#fff"/>',
        '<g fill="#000">',
    ]
    for row_index, row in enumerate(grid):
        column = 0
        while column < size:
            if row[column]:
                start = column
                while column < size and row[column]:
                    column += 1
                parts.append(
                    f'<rect x="'
                    f'{(start + quiet_zone_modules) * pixels_per_module}" '
                    f'y="'
                    f'{(row_index + quiet_zone_modules) * pixels_per_module}" '
                    f'width="{(column - start) * pixels_per_module}" '
                    f'height="{pixels_per_module}"/>')
            else:
                column += 1
    parts.append("</g></svg>")
    return "".join(parts)
