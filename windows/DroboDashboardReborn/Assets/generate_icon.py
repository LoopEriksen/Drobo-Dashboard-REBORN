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

ICON_SIZES_PIXELS = [16, 32, 48, 64, 128, 256]
SUBSAMPLES_PER_AXIS = 4  # per output pixel, for anti-aliased edges

# ---- palette ---------------------------------------------------------
WORDMARK_HALO = (46, 52, 61, 255)    # dark halo around the front word
REBORN_RED = (211, 58, 46, 255)      # the REBORN stamp
WORDMARK_COLOUR = (238, 242, 247, 255)   # the "drobo" lettering in front
PLATE_COLOUR = (26, 30, 37, 255)     # dark plate the lockup sits on
# tray badge when a critical alert is active
ALERT_RED = (226, 43, 36, 255)
# thin light ring around the badge for contrast
ALERT_RING = (255, 255, 255, 230)
TRANSPARENT = (0, 0, 0, 0)

# -- a tiny, original 5x7 block font, only the letters REBORN needs ----
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


def _glyph_has_ink(letter: str, glyph_row: int, column: int) -> bool:
    """True when the font marks (glyph_row, column) of letter as ink.

    A column outside the 5-wide glyph is the gap between letters, so it
    is never ink.
    """
    if not 0 <= column < 5:
        return False
    return FONT_5X7[letter][glyph_row][column] == "1"


def word_predicate(word: str, left: float, top: float,
                   dot_size_pixels: float):
    """
    A predicate covering `word` rendered from FONT_5X7 at
    `dot_size_pixels` px per dot, with its top-left at (left, top). One
    gap column between letters.

    A predicate rather than a raster, because every shape here is
    sampled at sub-pixel points for anti-aliasing: asking "is this
    point ink?" works at any size and needs no image library.
    """
    letter_width = 5 * dot_size_pixels

    def covers_point(x: float, y: float) -> bool:
        offset_y = y - top
        if offset_y < 0 or offset_y >= 7 * dot_size_pixels:
            return False
        glyph_row = int(offset_y / dot_size_pixels)
        if not (0 <= glyph_row < 7):
            return False
        letter_left = 0.0
        offset_x = x - left
        for letter in word:
            if letter_left <= offset_x < letter_left + letter_width:
                return _glyph_has_ink(
                    letter, glyph_row,
                    int((offset_x - letter_left) / dot_size_pixels))
            letter_left += letter_width + dot_size_pixels
        return False

    return covers_point


def word_width(word: str, dot_size_pixels: float) -> float:
    """Width of `word` at `dot_size_pixels` px per dot,
    including inter-letter gaps.

    It is linear in the dot size, so word_width(word, 1.0) is the width
    in dots: dividing a target width by it gives the dot size that makes
    the word exactly that wide.
    """
    return (len(word) * 5 * dot_size_pixels
            + (len(word) - 1) * dot_size_pixels)


def in_rounded_rectangle(x: float, y: float, left: float, top: float,
                         right: float, bottom: float,
                         radius: float) -> bool:
    """True when (x, y) is inside the rectangle with rounded corners.

    Like the letters, the plate is a predicate rather than a drawing,
    so it is anti-aliased by the same sub-pixel sampling. The radius is
    clamped to half the shorter side, so a large one gives a pill
    rather than nonsense.
    """
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
    """True when (x, y) is inside the circle (the alert badge)."""
    return (x - centre_x) ** 2 + (y - centre_y) ** 2 <= radius * radius


def _small_icon_shapes(size_pixels: float):
    """The shapes for a size below 48px: one bold "d" on a red plate.

    At these sizes the lockup is grey mush (see build_shapes), so the
    letter and the colour are what carry recognition.
    """
    plate_margin_pixels = 0.08 * size_pixels
    in_plate = partial(in_rounded_rectangle, left=plate_margin_pixels,
                       top=plate_margin_pixels,
                       right=size_pixels - plate_margin_pixels,
                       bottom=size_pixels - plate_margin_pixels,
                       radius=0.22 * size_pixels)

    # A "d" filling most of the plate. dot_size_pixels is chosen so the
    # glyph's 5x7 box fits with a margin; the letter is what carries
    # recognition here.
    dot_size_pixels = (size_pixels * 0.52) / 7.0
    glyph_width = word_width("d", dot_size_pixels)
    glyph_left = (size_pixels - glyph_width) / 2.0
    glyph_top = (size_pixels - 7 * dot_size_pixels) / 2.0
    return [
        (word_predicate("d", glyph_left, glyph_top, dot_size_pixels),
         WORDMARK_COLOUR),
        (in_plate, REBORN_RED),
    ]


def _top_from_centre(centre_y: float, dot_size_pixels: float) -> float:
    """The top edge that puts a 7-dot-tall word's centre at centre_y."""
    return centre_y - 3.5 * dot_size_pixels


def _place_word(word: str, size_pixels: float, width_fraction: float,
                centre_fraction: float):
    """(left, top, dot_size_pixels) for word set width_fraction of the
    icon wide, centred across it, with its vertical centre at
    centre_fraction of the way down.

    Placing by the vertical CENTRE rather than the top edge is what
    makes the overlap of the two words controllable: give them centres
    a little apart and they interleave, give them the same centre and
    "drobo" sits squarely across REBORN's waist.
    """
    dot_size_pixels = (size_pixels * width_fraction) / word_width(word, 1.0)
    width = word_width(word, dot_size_pixels)
    left = (size_pixels - width) / 2.0
    top = _top_from_centre(size_pixels * centre_fraction, dot_size_pixels)
    return left, top, dot_size_pixels


def _halo_predicate(covers_front_word, halo_offset_pixels: float):
    """A predicate covering the front word grown by halo_offset_pixels.

    A cheap halo: the same glyph tested at its own place and shifted
    left, right, up and down by the offset.
    """
    def covers_halo(x: float, y: float) -> bool:
        return (covers_front_word(x, y)
                or covers_front_word(x - halo_offset_pixels, y)
                or covers_front_word(x + halo_offset_pixels, y)
                or covers_front_word(x, y - halo_offset_pixels)
                or covers_front_word(x, y + halo_offset_pixels))

    return covers_halo


def _lockup_shapes(size_pixels: float):
    """The shapes for 48px and up: "drobo" in front of a red "REBORN".

    REBORN sits BEHIND, set wide across the icon. "drobo" sits in front
    of it, overlapping -- not stacked underneath, which is what a first
    pass at this produced and which just read as two separate lines of
    text. Front to back: the front word, its halo, REBORN, the plate.
    """
    # REBORN: nearly the full width, so it is unmistakably the larger of
    # the two.
    behind_left, behind_top, behind_dot_size_pixels = _place_word(
        BEHIND_WORD, size_pixels, 0.90, 0.50)

    # drobo: narrower and set on a centre slightly below REBORN's, so it
    # crosses the lower half of the red letters. Sitting dead-centre
    # hides REBORN's midsection and makes it hard to read; a little low
    # leaves the tops of the red letters clear.
    front_left, front_top, front_dot_size_pixels = _place_word(
        FRONT_WORD, size_pixels, 0.66, 0.60)

    # A soft dark plate behind everything, so the mark reads on any
    # wallpaper or title bar rather than dissolving into it.
    plate_margin_pixels = 0.05 * size_pixels
    in_plate = partial(in_rounded_rectangle, left=plate_margin_pixels,
                       top=plate_margin_pixels,
                       right=size_pixels - plate_margin_pixels,
                       bottom=size_pixels - plate_margin_pixels,
                       radius=0.20 * size_pixels)

    # A thin dark outline around the front word, so "drobo" stays
    # legible exactly where it crosses the red. The letter itself comes
    # first, so it wins over its own halo.
    halo_offset_pixels = front_dot_size_pixels * 0.55
    covers_halo = _halo_predicate(
        word_predicate(FRONT_WORD, front_left, front_top,
                       front_dot_size_pixels),
        halo_offset_pixels)
    return [
        (word_predicate(FRONT_WORD, front_left, front_top,
                        front_dot_size_pixels), WORDMARK_COLOUR),
        (covers_halo, WORDMARK_HALO),
        (word_predicate(BEHIND_WORD, behind_left, behind_top,
                        behind_dot_size_pixels), REBORN_RED),
        (in_plate, PLATE_COLOUR),
    ]


def build_shapes(size_pixels: float, with_badge: bool):
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
    if size_pixels < 48:
        shapes = _small_icon_shapes(size_pixels)
    else:
        shapes = _lockup_shapes(size_pixels)
    return _add_alert_badge(shapes, size_pixels, with_badge)


def _add_alert_badge(shapes, size_pixels: float, with_badge: bool):
    """The critical-alert dot, sized so it signals at 16px without
    dominating at 256.

    The dot and then its ring go in front of everything else, so the
    badge is never covered by the mark.
    """
    if not with_badge:
        return shapes
    radius_fraction = (0.20 if size_pixels <= 32
                       else 0.15 if size_pixels <= 64 else 0.115)
    badge_radius_pixels = radius_fraction * size_pixels
    centre_fraction = 0.80 if size_pixels <= 32 else 0.835
    badge_centre_pixels = centre_fraction * size_pixels
    ring_radius_pixels = (badge_radius_pixels
                          + (0.045 if size_pixels <= 32 else 0.03)
                          * size_pixels)
    shapes.insert(0, (partial(in_circle, centre_x=badge_centre_pixels,
                              centre_y=badge_centre_pixels,
                              radius=badge_radius_pixels),
                      ALERT_RED))
    shapes.insert(1, (partial(in_circle, centre_x=badge_centre_pixels,
                              centre_y=badge_centre_pixels,
                              radius=ring_radius_pixels),
                      ALERT_RING))
    return shapes


def sample(shapes, x: float, y: float):
    """The colour at (x, y): the first shape that covers the point.

    First match wins because the list is front to back -- a painter's
    algorithm run in reverse, which stops as soon as it knows the
    answer. Nothing covering it means transparent.
    """
    for predicate, rgba in shapes:
        if predicate(x, y):
            return rgba
    return TRANSPARENT


def _average_pixel(shapes, pixel_x: int, pixel_y: int):
    """One output pixel: the alpha-weighted average of its subsamples.

    Colour is weighted by alpha (a premultiplied average) so the
    transparent samples at an edge do not drag the colour towards black
    and leave a dark fringe; alpha itself is the plain average.
    """
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
        return (0, 0, 0, 0)
    return (weighted_red // alpha_total,
            weighted_green // alpha_total,
            weighted_blue // alpha_total,
            alpha_total // samples_per_pixel)


def render(size_pixels: int, with_badge: bool):
    """The icon at one size, as rows of (red, green, blue, alpha).

    Each pixel is SUBSAMPLES_PER_AXIS x SUBSAMPLES_PER_AXIS samples
    averaged, which anti-aliases the edges of the letters and the plate
    without an image library.
    """
    shapes = build_shapes(float(size_pixels), with_badge)
    return [[_average_pixel(shapes, pixel_x, pixel_y)
             for pixel_x in range(size_pixels)]
            for pixel_y in range(size_pixels)]


# ---- minimal PNG encoder (stdlib zlib only) --------------------------

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """One PNG chunk: length, type, data, then CRC-32 of type+data."""
    return (struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF))


def encode_png(pixels) -> bytes:
    """An 8-bit RGBA PNG of the pixel rows.

    Hand-rolled on zlib and struct so the script needs nothing beyond
    the standard library. Every scanline uses filter type None: the
    icons are tiny, so better compression is not worth the code.
    """
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


# ---- minimal ICO container (PNG-compressed frames, one per size) -----

def write_ico(path: str, frames) -> None:
    """Write (size_pixels, png_bytes) frames to path as one .ico file.

    The frames are stored as PNG, which Windows has accepted inside an
    ICO since Vista and which keeps the 256px frame small. The
    directory's width and height are single bytes, so 256 is written
    as 0, as the format defines.
    """
    frame_count = len(frames)
    ico_header = struct.pack("<HHH", 0, 1, frame_count)
    directory_entries = bytearray()
    image_data = bytearray()
    image_offset = 6 + 16 * frame_count
    for size_pixels, png_bytes in frames:
        # 0 means 256 per the ICO spec
        dimension_byte = size_pixels if size_pixels < 256 else 0
        directory_entry = struct.pack(
            "<BBBBHHII", dimension_byte, dimension_byte, 0, 0, 1, 32,
            len(png_bytes), image_offset)
        directory_entries += directory_entry
        image_data += png_bytes
        image_offset += len(png_bytes)
    with open(path, "wb") as ico_file:
        ico_file.write(ico_header + bytes(directory_entries)
                       + bytes(image_data))


def build_frames(with_badge: bool):
    """(size_pixels, png_bytes) for every size in ICON_SIZES_PIXELS,
    smallest first, ready for write_ico.
    """
    frames = []
    for size_pixels in ICON_SIZES_PIXELS:
        pixels = render(size_pixels, with_badge)
        frames.append((size_pixels, encode_png(pixels)))
    return frames


def main():
    """Write AppIcon.ico and AppIconAlert.ico next to this script.

    Next to the script rather than the working directory, so it writes
    to the same place however it is run.
    """
    output_dir = os.path.dirname(os.path.abspath(__file__))

    normal_frames = build_frames(with_badge=False)
    write_ico(os.path.join(output_dir, "AppIcon.ico"), normal_frames)
    print("Wrote AppIcon.ico (%d frames: %s)" % (
        len(normal_frames),
        ", ".join(str(size_pixels) for size_pixels, _ in normal_frames)))

    alert_frames = build_frames(with_badge=True)
    write_ico(os.path.join(output_dir, "AppIconAlert.ico"), alert_frames)
    print("Wrote AppIconAlert.ico (%d frames: %s)" % (
        len(alert_frames),
        ", ".join(str(size_pixels) for size_pixels, _ in alert_frames)))


if __name__ == "__main__":
    main()
