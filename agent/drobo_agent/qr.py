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
    """
    Multiply two GF(256) elements by adding their logs. Zero has no
    log, so it is handled first.
    """
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


def _ec_codewords(
    data_codewords: list[int], ec_codeword_count: int
) -> list[int]:
    """
    The Reed-Solomon error-correction codewords for one block: the
    remainder of dividing the data, as a polynomial, by the generator
    polynomial. They are what lets a scanner rebuild a damaged code.
    """
    generator = _generator_polynomial(ec_codeword_count)
    remainder = list(data_codewords) + [0] * ec_codeword_count
    for i in range(len(data_codewords)):
        lead_coefficient = remainder[i]
        if not lead_coefficient:
            continue
        for j in range(1, len(generator)):
            remainder[i + j] ^= _gf_multiply(generator[j],
                                             lead_coefficient)
    return remainder[len(data_codewords):]


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
    """
    How many data codewords a version holds at level M: every block of
    both groups, less the error-correction codewords, which the table
    already leaves out.
    """
    (ec_codewords_per_block, group1_blocks, group1_data_codewords,
     group2_blocks, group2_data_codewords) = _LEVEL_M_BLOCK_LAYOUT[version]
    return (group1_blocks * group1_data_codewords
            + group2_blocks * group2_data_codewords)


# What version 10 holds: its data codewords, less byte mode's 4-bit
# indicator and 16-bit count. ISO/IEC 18004 tabulates 213 for 10-M.
MAX_PAYLOAD_BYTES = (_data_codeword_capacity(10) * 8 - 4 - 16) // 8


def _pick_version(payload_length: int) -> int:
    """
    The smallest version the payload fits, because a smaller code has
    bigger modules at the same image size and scans more easily off a
    screen. ValueError past version 10.
    """
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
    """
    The payload as the version's full set of data codewords: byte-mode
    indicator, length, the bytes, a terminator, then the pad codewords
    0xEC and 0x11 alternating to fill the capacity. Those two values
    are the ones the specification prescribes, so every reader expects
    them.
    """
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
    """
    Split the data into the version's blocks, add each block's
    error-correction codewords, and interleave them -- first codeword
    of every block, then the second, and so on, data before EC. The
    specification interleaves so that one smudged patch of the code
    damages a little of every block rather than all of one.
    """
    (ec_codewords_per_block, group1_blocks, group1_data_codewords,
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
        ec_blocks.append(_ec_codewords(block, ec_codewords_per_block))

    interleaved: list[int] = []
    for i in range(max(len(block) for block in data_blocks)):
        # group 2 blocks are one codeword longer than group 1's
        interleaved.extend(block[i] for block in data_blocks
                           if i < len(block))
    for i in range(ec_codewords_per_block):
        interleaved.extend(block[i] for block in ec_blocks)
    return interleaved


# --------------------------------------------------------------------------
# Matrix construction
# --------------------------------------------------------------------------
def _new_matrix(version: int):
    """
    A grid with every function pattern drawn and every other cell None,
    and its modules per side.

    The function patterns are the parts a scanner uses to find and read
    the code -- finders, timing, alignment, the dark module -- plus the
    format and version areas, reserved here (as 0) and filled in later,
    so that data placement knows to skip them.
    """
    modules_per_side = version * 4 + 17
    module_grid = [[None] * modules_per_side
                   for _ in range(modules_per_side)]
    _draw_finder_patterns(module_grid, modules_per_side)
    _draw_timing_patterns(module_grid, modules_per_side)
    _draw_alignment_patterns(module_grid, modules_per_side, version)
    module_grid[modules_per_side - 8][8] = 1                  # dark module
    _reserve_format_areas(module_grid, modules_per_side)
    if version >= 7:
        _reserve_version_areas(module_grid, modules_per_side)
    return module_grid, modules_per_side


def _draw_finder_patterns(module_grid, modules_per_side: int) -> None:
    """The three big corner squares, each with its light separator."""
    _draw_finder_pattern(module_grid, modules_per_side, 0, 0)
    _draw_finder_pattern(module_grid, modules_per_side,
                         0, modules_per_side - 7)
    _draw_finder_pattern(module_grid, modules_per_side,
                         modules_per_side - 7, 0)


def _draw_finder_pattern(
    module_grid, modules_per_side: int, top: int, left: int
) -> None:
    """
    One 7x7 finder (dark ring, light ring, dark 3x3 centre) and the
    one-module light border round it, clipped to the grid.
    """
    for row_offset in range(-1, 8):
        for column_offset in range(-1, 8):
            row, column = top + row_offset, left + column_offset
            if not (0 <= row < modules_per_side
                    and 0 <= column < modules_per_side):
                continue
            inside = (0 <= row_offset < 7 and 0 <= column_offset < 7)
            is_dark = inside and (row_offset in (0, 6)
                                  or column_offset in (0, 6)
                                  or (2 <= row_offset <= 4
                                      and 2 <= column_offset <= 4))
            module_grid[row][column] = 1 if is_dark else 0


def _draw_timing_patterns(module_grid, modules_per_side: int) -> None:
    """
    Row 6 and column 6 alternate dark and light between the finders,
    which is how a scanner measures the module size.
    """
    for i in range(8, modules_per_side - 8):
        bit = 1 if i % 2 == 0 else 0
        module_grid[6][i] = bit
        module_grid[i][6] = bit


def _draw_alignment_patterns(
    module_grid, modules_per_side: int, version: int
) -> None:
    """
    A 5x5 alignment pattern at every pair of the version's centres,
    except the three that would land on a finder.
    """
    centres = _ALIGNMENT_PATTERN_CENTRES[version]
    for centre_row in centres:
        for centre_column in centres:
            if ((centre_row < 8 and centre_column < 8)
                    or (centre_row < 8
                        and centre_column > modules_per_side - 9)
                    or (centre_row > modules_per_side - 9
                        and centre_column < 8)):
                continue
            _draw_alignment_pattern(module_grid, centre_row, centre_column)


def _draw_alignment_pattern(
    module_grid, centre_row: int, centre_column: int
) -> None:
    """One alignment pattern: dark centre, light ring, dark ring."""
    for row_offset in range(-2, 3):
        for column_offset in range(-2, 3):
            grid_row = module_grid[centre_row + row_offset]
            grid_row[centre_column + column_offset] = (
                1 if (max(abs(row_offset), abs(column_offset)) != 1)
                else 0)


def _reserve_format_areas(module_grid, modules_per_side: int) -> None:
    """
    Claim (as 0) the format-information cells beside the finders that
    nothing has drawn yet; _apply_format writes them after masking.
    """
    for i in range(9):
        if module_grid[8][i] is None:
            module_grid[8][i] = 0
        if module_grid[i][8] is None:
            module_grid[i][8] = 0
    for i in range(8):
        if module_grid[8][modules_per_side - 1 - i] is None:
            module_grid[8][modules_per_side - 1 - i] = 0
        if module_grid[modules_per_side - 1 - i][8] is None:
            module_grid[modules_per_side - 1 - i][8] = 0


def _reserve_version_areas(module_grid, modules_per_side: int) -> None:
    """
    Reserve the two 6x3 version-information blocks (version 7 and up),
    or data placement would run straight through them.
    """
    for i in range(18):
        long_offset, short_offset = i // 3, i % 3
        module_grid[modules_per_side - 11 + short_offset][long_offset] = 0
        module_grid[long_offset][modules_per_side - 11 + short_offset] = 0


def _function_pattern_cells(version: int, modules_per_side: int):
    """Which cells are function patterns (and so not data)."""
    module_grid, _ = _new_matrix(version)
    return [[module_grid[row][column] is not None
             for column in range(modules_per_side)]
            for row in range(modules_per_side)]


def _codeword_bits(data_codewords: list[int]) -> list[int]:
    """Every codeword's bits, most significant first."""
    bits = []
    for codeword in data_codewords:
        for i in range(7, -1, -1):
            bits.append((codeword >> i) & 1)
    return bits


def _placement_right_columns(modules_per_side: int):
    """
    The right-hand column of each two-column strip data is placed in,
    right to left. Column 6 is the vertical timing pattern, so the
    strip that would contain it shifts one column left.
    """
    column = modules_per_side - 1
    while column > 0:
        if column == 6:
            column -= 1                       # skip the vertical timing column
        yield column
        column -= 2


def _placement_order(modules_per_side: int):
    """
    Every (row, column) in the order the specification places data:
    two columns at a time, right to left, going up the first strip,
    down the next, and so on, right cell before left in each row.
    """
    upward = True
    for right_column in _placement_right_columns(modules_per_side):
        rows = (range(modules_per_side - 1, -1, -1) if upward
                else range(modules_per_side))
        for row in rows:
            yield row, right_column
            yield row, right_column - 1
        upward = not upward


def _place_data_bits(module_grid, function_cells,
                     data_codewords: list[int], modules_per_side: int):
    """
    Write the data bits into every cell that is not a function pattern,
    in the specification's zig-zag order (see _placement_order); cells
    left over once the bits run out are 0.
    """
    bits = _codeword_bits(data_codewords)
    bit_index = 0
    for row, column in _placement_order(modules_per_side):
        if function_cells[row][column]:
            continue
        module_grid[row][column] = (bits[bit_index]
                                    if bit_index < len(bits) else 0)
        bit_index += 1
    return module_grid


_MASK_PATTERNS = [
    lambda row, column: (row + column) % 2 == 0,
    lambda row, column: row % 2 == 0,
    lambda row, column: column % 3 == 0,
    lambda row, column: (row + column) % 3 == 0,
    lambda row, column: (row // 2 + column // 3) % 2 == 0,
    lambda row, column: (row * column) % 2 + (row * column) % 3 == 0,
    lambda row, column: ((row * column) % 2 + (row * column) % 3) % 2 == 0,
    lambda row, column: ((row + column) % 2 + (row * column) % 3) % 2 == 0,
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


def _apply_format(module_grid, modules_per_side: int, mask: int):
    """
    Write the format information (level and mask) twice -- round the
    top-left finder, and split between the other two -- so a scanner
    can read it even with one copy damaged.
    """
    format_bits = _format_bits(mask)
    for i in range(15):
        bit = (format_bits >> i) & 1
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
            module_grid[modules_per_side - 1 - i][8] = bit
        else:
            module_grid[8][modules_per_side - 15 + i] = bit
    module_grid[modules_per_side - 8][8] = 1


def _version_bits(version: int) -> int:
    """
    18-bit version information: the 6-bit version and its BCH(18,6)
    remainder. Only versions 7 and up carry it; below that the size
    alone tells a scanner the version.
    """
    remainder = version << 12
    bch_generator = 0b1111100100101
    for i in range(17, 11, -1):
        if remainder & (1 << i):
            remainder ^= bch_generator << (i - 12)
    return (version << 12) | remainder


def _apply_version(module_grid, modules_per_side: int, version: int):
    """
    Write the version information into both 6x3 blocks that
    _reserve_version_areas set aside. Nothing to do below version 7.
    """
    if version < 7:
        return
    version_bits = _version_bits(version)
    for i in range(18):
        bit = (version_bits >> i) & 1
        long_offset, short_offset = i // 3, i % 3
        module_grid[modules_per_side - 11 + short_offset][long_offset] = bit
        module_grid[long_offset][modules_per_side - 11 + short_offset] = bit


def _run_lengths(line):
    """The length of each run of equal cells along one row or column."""
    run_length, previous_cell = 1, line[0]
    for cell in line[1:]:
        if cell == previous_cell:
            run_length += 1
            continue
        yield run_length
        run_length, previous_cell = 1, cell
    yield run_length


def _mask_penalty(module_grid, modules_per_side: int) -> int:
    """
    The specification's score for how hard a masked grid is to scan:
    long runs, 2x2 blocks, stretches that look like a finder, and a
    dark/light imbalance all add to it. matrix() keeps the mask with
    the lowest score, because the masks exist only to break up those
    patterns.
    """
    lines = list(module_grid) + [list(col) for col in zip(*module_grid)]
    return (_long_run_penalty(lines)
            + _block_penalty(module_grid, modules_per_side)
            + _finder_like_penalty(lines, modules_per_side)
            + _balance_penalty(module_grid, modules_per_side))


def _long_run_penalty(lines) -> int:
    """Rule 1: 3, plus 1 per extra cell, for every run of 5 or more."""
    return sum(3 + (run_length - 5)
               for line in lines for run_length in _run_lengths(line)
               if run_length >= 5)


def _block_penalty(module_grid, modules_per_side: int) -> int:
    """Rule 2: 3 for each 2x2 block of one colour."""
    return 3 * sum(
        1 for row in range(modules_per_side - 1)
        for column in range(modules_per_side - 1)
        if (module_grid[row][column] == module_grid[row][column + 1]
            == module_grid[row + 1][column]
            == module_grid[row + 1][column + 1]))


def _finder_like_penalty(lines, modules_per_side: int) -> int:
    """
    Rule 3: 40 for each 1:1:3:1:1 stretch with four light cells beside
    it, either way round -- it looks like a finder to a scanner.
    """
    finder_like_run = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    finder_like_run_reversed = list(reversed(finder_like_run))
    return 40 * sum(
        1 for line in lines for i in range(modules_per_side - 10)
        if line[i:i + 11] in (finder_like_run, finder_like_run_reversed))


def _balance_penalty(module_grid, modules_per_side: int) -> int:
    """Rule 4: 10 for every 5% the dark share is away from half."""
    dark_module_count = sum(sum(row) for row in module_grid)
    dark_percent = (dark_module_count * 100
                    // (modules_per_side * modules_per_side))
    return 10 * (abs(dark_percent - 50) // 5)


def _apply_mask(module_grid, function_cells, mask: int,
                modules_per_side: int) -> None:
    """Flip every data cell the mask pattern selects."""
    mask_pattern = _MASK_PATTERNS[mask]
    for row in range(modules_per_side):
        for column in range(modules_per_side):
            if function_cells[row][column] or not mask_pattern(row, column):
                continue
            module_grid[row][column] ^= 1


def _masked_grid(version: int, data_codewords: list[int], mask: int):
    """
    The finished 0/1 grid for one mask: function patterns, data,
    the mask, then the format and version information.
    """
    module_grid, modules_per_side = _new_matrix(version)
    function_cells = _function_pattern_cells(version, modules_per_side)
    _place_data_bits(module_grid, function_cells, data_codewords,
                     modules_per_side)
    _apply_mask(module_grid, function_cells, mask, modules_per_side)
    _apply_format(module_grid, modules_per_side, mask)
    _apply_version(module_grid, modules_per_side, version)
    return [[cell or 0 for cell in row] for row in module_grid]


def matrix(text: str) -> list[list[int]]:
    """Encode text and return the finished module grid as 0/1 rows.

    All eight masks are tried and the one with the lowest penalty is
    kept (the first, on a tie), as the specification requires.
    """
    payload = text.encode("utf-8")
    version = _pick_version(len(payload))
    data_codewords = _interleave(_encode_data(payload, version), version)

    best_grid, best_score = None, None
    for mask in range(8):
        grid = _masked_grid(version, data_codewords, mask)
        score = _mask_penalty(grid, len(grid))
        if best_score is None or score < best_score:
            best_grid, best_score = grid, score
    return best_grid


def _dark_runs(row):
    """(first column, length) of each run of dark modules in a row."""
    modules_per_side = len(row)
    column = 0
    while column < modules_per_side:
        if not row[column]:
            column += 1
            continue
        run_start_column = column
        while column < modules_per_side and row[column]:
            column += 1
        yield run_start_column, column - run_start_column


def svg(text: str, pixels_per_module: int = 6,
        quiet_zone_modules: int = 4) -> str:
    """Render as a self-contained SVG.
    currentColor so it follows the theme.
    """
    grid = matrix(text)
    modules_per_side = len(grid)
    image_size_pixels = ((modules_per_side + quiet_zone_modules * 2)
                         * pixels_per_module)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{image_size_pixels}" '
        f'height="{image_size_pixels}" '
        f'viewBox="0 0 {image_size_pixels} {image_size_pixels}" role="img" '
        f'aria-label="Pairing QR code">',
        f'<rect width="{image_size_pixels}" height="{image_size_pixels}" '
        f'fill="#fff"/>',
        '<g fill="#000">',
    ]
    # One rect per horizontal run of dark modules rather than one per
    # module: the same picture in a fraction of the elements.
    for row_index, row in enumerate(grid):
        y_pixels = (row_index + quiet_zone_modules) * pixels_per_module
        for run_start_column, run_length in _dark_runs(row):
            x_pixels = ((run_start_column + quiet_zone_modules)
                        * pixels_per_module)
            parts.append(
                f'<rect x="{x_pixels}" '
                f'y="{y_pixels}" '
                f'width="{run_length * pixels_per_module}" '
                f'height="{pixels_per_module}"/>')
    parts.append("</g></svg>")
    return "".join(parts)
