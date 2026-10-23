"""Generate the Drobo Dashboard REBORN application icon.

Produces two multi-resolution .ico files next to this script:

    AppIcon.ico       -- the normal app / taskbar / tray icon
    AppIconAlert.ico  -- the same mark with a small red badge added in
                         the corner, swapped in by the tray icon while
                         a critical alert is active (see
                         TrayIconManager.cs)

Design -- a text lockup, deliberately NOT a copy of Drobo's own logo
(this project is unaffiliated; see the repo README). The letterforms
below are a plain geometric 5x7 block font written for this file, not
traced from any existing typeface:

    - "drobo", lowercase, in front, in near-white.
    - "REBORN" behind it, larger and in red, so it reads as a stamp the
      wordmark is sitting on top of.
    - A thin dark halo around the front word, so "drobo" stays readable
      exactly where it crosses the red rather than dissolving into it.
    - A dark rounded plate behind everything, so the mark holds up
      against a light title bar or wallpaper instead of disappearing.

Sized smallest-first, because 16x16 is the taskbar and tray size and
that is the one that decides whether an icon works. Six letters across
sixteen pixels is under three pixels per letter -- unavoidably grey mush
-- so below 48px the lockup is dropped entirely in favour of a single
bold lowercase "d" on a red plate. That preserves the two things which
actually identify an icon at a glance: its colour and its silhouette.

Pure Python standard library only (zlib for PNG compression/CRC32,
struct for binary packing) -- no Pillow, no third-party packages, so
this reproduces identically on any machine with plain Python 3
installed. Re-run this script any time the design changes:

    python windows/DroboDashboardReborn/Assets/generate_icon.py"""
from __future__ import annotations

import os
import struct
import zlib
from functools import partial

ICON_SIZES = [16, 32, 48, 64, 128, 256]
SUBSAMPLES_PER_AXIS = 4  # per output pixel, for anti-aliased edges

# ---- palette
# -----------------------------------------------------------
WORDMARK_HALO = (46, 52, 61, 255)    # dark halo around the front word
REBORN_RED = (211, 58, 46, 255)      # the REBORN stamp
WORDMARK = (238, 242, 247, 255)      # the "drobo" lettering in front
PLATE = (26, 30, 37, 255)            # dark plate the lockup sits on
# tray badge when a critical alert is active
ALERT_RED = (226, 43, 36, 255)
# thin light ring around the badge for contrast
ALERT_RING = (255, 255, 255, 230)
TRANSPARENT = (0, 0, 0, 0)

# ---- a tiny, original 5x7 block font, only the letters REBORN needs
# ----
# ('1' = ink, '0' = empty; original plain geometric letterforms, not
# traced from any existing typeface.)
FONT_5X7 = {
    # Uppercase -- the REBORN stamp behind.
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "N": ["10001", "11001", "10101", "10101", "10011", "10001", "10001"],
    # Lowercase -- the "drobo" wordmark in front. Drawn with a full
    # 7-row box so ascenders (d, b) and x-height letters (r, o) share
    # one baseline.
    "d": ["00001", "00001", "01111", "10001", "10001", "10001", "01111"],
    "r": ["00000", "00000", "10110", "11001", "10000", "10000", "10000"],
    "o": ["00000", "00000", "01110", "10001", "10001", "10001", "01110"],
    "b": ["10000", "10000", "11110", "10001", "10001", "10001", "11110"],
}

BEHIND_WORD = "REBORN"   # large, red, behind
FRONT_WORD = "drobo"     # lowercase, light, in front


def word_predicate(word: str, left: float, top: float, dot_size: float):
    """
    A predicate covering `word` rendered from FONT_5X7 at `dot_size` px
    per dot, with its top-left at (left, top). One gap column between
    letters.
    """
    letter_width = 5 * dot_size

    def covers_point(x: float, y: float) -> bool:
        offset_y = y - top
        if offset_y < 0 or offset_y >= 7 * dot_size:
            return False
        glyph_row = int(offset_y / dot_size)
        if not (0 <= glyph_row < 7):
            return False
        letter_left = 0.0
        offset_x = x - left
        for letter in word:
            if letter_left <= offset_x < letter_left + letter_width:
                column = int((offset_x - letter_left) / dot_size)
                if 0 <= column < 5:
                    return FONT_5X7[letter][glyph_row][column] == "1"
                return False
            letter_left += letter_width + dot_size
        return False

    return covers_point


def word_width(word: str, dot_size: float) -> float:
    """Width of `word` at `dot_size` px per dot, including inter-letter
    gaps."""
    return len(word) * 5 * dot_size + (len(word) - 1) * dot_size


def in_rounded_rectangle(x: float, y: float, left: float, top: float,
                         right: float, bottom: float,
                         radius: float) -> bool:
    if x < left or x > right or y < top or y > bottom:
        return False
    radius = min(radius, (right - left) / 2, (bottom - top) / 2)
    if radius <= 0:
        return True
    if x < left + radius and y < top + radius:
        return ((x - (left + radius)) ** 2 + (y - (top + radius)) ** 2
                <= radius * radius)
    if x > right - radius and y < top + radius:
        return ((x - (right - radius)) ** 2 + (y - (top + radius)) ** 2
                <= radius * radius)
    if x < left + radius and y > bottom - radius:
        return ((x - (left + radius)) ** 2 + (y - (bottom - radius)) ** 2
                <= radius * radius)
    if x > right - radius and y > bottom - radius:
        return ((x - (right - radius)) ** 2 + (y - (bottom - radius)) ** 2
                <= radius * radius)
    return True


def in_circle(x: float, y: float, centre_x: float, centre_y: float,
              radius: float) -> bool:
    return (x - centre_x) ** 2 + (y - centre_y) ** 2 <= radius * radius


def build_shapes(size: float, with_badge: bool):
    """
    Front-to-back list of (predicate, rgba). `sample` returns the FIRST
    match, so earlier entries are drawn in front.

    The mark is a text lockup: the word "drobo" in lowercase, sitting in
    front of a larger "REBORN" in red. Lowercase is deliberate -- it is
    what the maintainer asked for, and at icon sizes a lowercase word
    with ascenders (d, b) has a more distinctive silhouette than an
    all-caps block.

    Below 48px none of that survives. Six letters across sixteen pixels
    is under three pixels per letter, which renders as grey mush -- so
    small sizes get a single bold "d" on a red plate instead. That keeps
    the two things that actually identify an icon in a taskbar: its
    colour and its shape.
    """
    shapes = []
    draw_lockup = size >= 48

    if not draw_lockup:
        # --- small: one letter, high contrast
        # --------------------------------
        margin = 0.08 * size
        in_plate = partial(in_rounded_rectangle, left=margin, top=margin,
                           right=size - margin, bottom=size - margin,
                           radius=0.22 * size)

        # A "d" filling most of the plate. dot_size is chosen so the
        # glyph's 5x7 box fits with a margin; the letter is what carries
        # recognition here.
        dot_size = (size * 0.52) / 7.0
        glyph_width = word_width("d", dot_size)
        glyph_left = (size - glyph_width) / 2.0
        glyph_top = (size - 7 * dot_size) / 2.0
        shapes.append((word_predicate("d", glyph_left, glyph_top, dot_size),
                       WORDMARK))
        shapes.append((in_plate, REBORN_RED))
        return _add_alert_badge(shapes, size, with_badge)

    # --- large: the full lockup
    # ---------------------------------------------
    # REBORN sits BEHIND, set wide across the icon. "drobo" sits in
    # front of it, overlapping -- not stacked underneath, which is what
    # a first pass at this produced and which just read as two separate
    # lines of text.
    #
    # Both are positioned from their vertical CENTRE rather than their
    # top edge, because that is what makes the overlap controllable:
    # give them centres a little apart and they interleave, give them
    # the same centre and "drobo" sits squarely across REBORN's waist.
    def top_from_centre(centre_y: float, dot_size: float) -> float:
        return centre_y - 3.5 * dot_size

    # REBORN: nearly the full width, so it is unmistakably the larger of
    # the two.
    behind_dot_size = (size * 0.90) / word_width(BEHIND_WORD, 1.0)
    behind_width = word_width(BEHIND_WORD, behind_dot_size)
    behind_left = (size - behind_width) / 2.0
    behind_top = top_from_centre(size * 0.50, behind_dot_size)

    # drobo: narrower and set on a centre slightly below REBORN's, so it
    # crosses the lower half of the red letters. Sitting dead-centre
    # hides REBORN's midsection and makes it hard to read; a little low
    # leaves the tops of the red letters clear.
    front_dot_size = (size * 0.66) / word_width(FRONT_WORD, 1.0)
    front_width = word_width(FRONT_WORD, front_dot_size)
    front_left = (size - front_width) / 2.0
    front_top = top_from_centre(size * 0.60, front_dot_size)

    # A soft dark plate behind everything, so the mark reads on any
    # wallpaper or title bar rather than dissolving into it.
    margin = 0.05 * size
    in_plate = partial(in_rounded_rectangle, left=margin, top=margin,
                       right=size - margin, bottom=size - margin,
                       radius=0.20 * size)

    # A thin dark outline around the front word, so "drobo" stays
    # legible exactly where it crosses the red. Cheap halo: the same
    # glyph, grown.
    halo_offset = front_dot_size * 0.55
    shapes.append((
        lambda x, y, covers_front_word=word_predicate(
            FRONT_WORD, front_left, front_top, front_dot_size
        ), offset=halo_offset:
            covers_front_word(x, y)
            or covers_front_word(x - offset, y)
            or covers_front_word(x + offset, y)
            or covers_front_word(x, y - offset)
            or covers_front_word(x, y + offset),
        None,  # placeholder, replaced below
    ))
    shapes[-1] = (shapes[-1][0], WORDMARK_HALO)

    # ...but the letter itself must win over its own halo, so insert it
    # first.
    shapes.insert(0, (word_predicate(FRONT_WORD, front_left, front_top,
                                     front_dot_size), WORDMARK))

    shapes.append((word_predicate(BEHIND_WORD, behind_left, behind_top,
                                  behind_dot_size), REBORN_RED))
    shapes.append((in_plate, PLATE))
    return _add_alert_badge(shapes, size, with_badge)


def _add_alert_badge(shapes, size: float, with_badge: bool):
    """The critical-alert dot, sized so it signals at 16px without
    dominating at 256."""
    if not with_badge:
        return shapes
    radius_fraction = 0.20 if size <= 32 else 0.15 if size <= 64 else 0.115
    badge_radius = radius_fraction * size
    centre_fraction = 0.80 if size <= 32 else 0.835
    badge_centre = centre_fraction * size
    ring_radius = badge_radius + (0.045 if size <= 32 else 0.03) * size
    shapes.insert(0, (partial(in_circle, centre_x=badge_centre,
                              centre_y=badge_centre, radius=badge_radius),
                      ALERT_RED))
    shapes.insert(1, (partial(in_circle, centre_x=badge_centre,
                              centre_y=badge_centre, radius=ring_radius),
                      ALERT_RING))
    return shapes


def sample(shapes, x: float, y: float):
    for predicate, rgba in shapes:
        if predicate(x, y):
            return rgba
    return TRANSPARENT


def render(size: int, with_badge: bool):
    shapes = build_shapes(float(size), with_badge)
    pixel_rows = []
    for pixel_y in range(size):
        pixel_row = []
        for pixel_x in range(size):
            weighted_red = weighted_green = weighted_blue = alpha_total = 0
            for sub_row in range(SUBSAMPLES_PER_AXIS):
                y = pixel_y + (sub_row + 0.5) / SUBSAMPLES_PER_AXIS
                for sub_column in range(SUBSAMPLES_PER_AXIS):
                    x = pixel_x + (sub_column + 0.5) / SUBSAMPLES_PER_AXIS
                    red, green, blue, alpha = sample(shapes, x, y)
                    weighted_red += red * alpha
                    weighted_green += green * alpha
                    weighted_blue += blue * alpha
                    alpha_total += alpha
            samples_per_pixel = SUBSAMPLES_PER_AXIS * SUBSAMPLES_PER_AXIS
            if alpha_total == 0:
                pixel_row.append((0, 0, 0, 0))
            else:
                pixel_row.append((weighted_red // alpha_total,
                                  weighted_green // alpha_total,
                                  weighted_blue // alpha_total,
                                  alpha_total // samples_per_pixel))
        pixel_rows.append(pixel_row)
    return pixel_rows


# ---- minimal PNG encoder (stdlib zlib only)
# -----------------------------

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF))


def encode_png(pixels) -> bytes:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    scanlines = bytearray()
    for pixel_row in pixels:
        scanlines.append(0)  # filter type: None
        for (red, green, blue, alpha) in pixel_row:
            scanlines += bytes((red, green, blue, alpha))
    image_header = struct.pack(">IIBBBBB", width, height,
                               8, 6, 0, 0, 0)  # 8-bit RGBA
    compressed_scanlines = zlib.compress(bytes(scanlines), 9)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", image_header)
            + _png_chunk(b"IDAT", compressed_scanlines)
            + _png_chunk(b"IEND", b""))


# ---- minimal ICO container (PNG-compressed frames, one per size)
# --------

def write_ico(path: str, frames) -> None:
    frame_count = len(frames)
    ico_header = struct.pack("<HHH", 0, 1, frame_count)
    directory_entries = bytearray()
    image_data = bytearray()
    image_offset = 6 + 16 * frame_count
    for size, png in frames:
        # 0 means 256 per the ICO spec
        dimension_byte = size if size < 256 else 0
        directory_entry = struct.pack(
            "<BBBBHHII", dimension_byte, dimension_byte, 0, 0, 1, 32,
            len(png), image_offset)
        directory_entries += directory_entry
        image_data += png
        image_offset += len(png)
    with open(path, "wb") as ico_file:
        ico_file.write(ico_header + bytes(directory_entries)
                       + bytes(image_data))


def build_frames(with_badge: bool):
    frames = []
    for size in ICON_SIZES:
        pixels = render(size, with_badge)
        frames.append((size, encode_png(pixels)))
    return frames


def main():
    output_dir = os.path.dirname(os.path.abspath(__file__))

    normal_frames = build_frames(with_badge=False)
    write_ico(os.path.join(output_dir, "AppIcon.ico"), normal_frames)
    print("Wrote AppIcon.ico (%d frames: %s)" % (
        len(normal_frames),
        ", ".join(str(size) for size, _ in normal_frames)))

    alert_frames = build_frames(with_badge=True)
    write_ico(os.path.join(output_dir, "AppIconAlert.ico"), alert_frames)
    print("Wrote AppIconAlert.ico (%d frames: %s)" % (
        len(alert_frames),
        ", ".join(str(size) for size, _ in alert_frames)))


if __name__ == "__main__":
    main()
