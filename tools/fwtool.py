#!/usr/bin/env python3
"""
fwtool.py - unpack Drobo firmware (.tdf) files.

Drobo firmware is a container Data Robotics called TDIH. This tool reads
it, lists what's inside, extracts each section, and rebuilds the Linux
root filesystem so its files can be read directly.

Everything here is read-only. Nothing is executed, and nothing is
written back to a Drobo.

    py fwtool.py info    release.Drobo5N.4-2-1.tdf
    py fwtool.py extract release.Drobo5N.4-2-1.tdf -o out/
    py fwtool.py rootfs  out/fw-LXFS.bin -o out/rootfs/
    py fwtool.py strings out/rootfs/sbin/nasd

Standard library only, Python 3.9+.

--------------------------------------------------------------------------
Container layout (worked out from Drobo 5N firmware 4.2.1, all
big-endian)

    +0x000  u32   offset of the payload area (0x22c on 5N 4.2.1)
    +0x004  u32   format version (5)
    +0x008  char4 "TDIH" magic
    +0x00c  u32   build number
    +0x010  char  model tag, NUL padded ("5N")
    +0x02c  u32   total payload size
    +0x030  u32   checksum
    +0x034  char  description ("Data Robotics Firmware")
    +0x168  u32   number of section-table entries
    +0x16c  ...   section table, 24 bytes per entry:
                    +0x00 char4  tag
                    +0x04 u32    offset, RELATIVE to the payload area
                    +0x08 u32    size
                    +0x0c u32    reserved
                    +0x10 u32    build number
                    +0x14 u32    checksum

Section tags seen so far:
    VXI2  ARM ELF - the VxWorks-side storage application (BeyondRAID)
    RTPI  ARM ELF - second runtime image
    LXKI  u-boot uImage - the Linux kernel
    LXFS  JFFS2 filesystem - the Linux root filesystem, where nasd lives
--------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import struct
import sys
import zlib

MAGIC_OFFSET = 8
MAGIC = b"TDIH"
SECTION_COUNT_OFFSET = 0x168
SECTION_TABLE_OFFSET = 0x16C
SECTION_ENTRY_SIZE = 24

PAYLOAD_SIGNATURES = [
    (b"hsqs", "squashfs (LE)"), (b"sqsh", "squashfs (BE)"),
    (b"\x45\x3d\xcd\x28", "cramfs"), (b"UBI#", "UBI"),
    (b"\x85\x19", "jffs2"), (b"\x1f\x8b\x08", "gzip"),
    (b"\xfd7zXZ\x00", "xz"), (b"\x7fELF", "ELF"),
    (b"\x27\x05\x19\x56", "u-boot uImage"), (b"-rom1fs-", "romfs"),
]


class Section:
    def __init__(self, tag, offset, size, build, checksum):
        self.tag = tag
        self.offset = offset          # absolute
        self.size = size
        self.build = build
        self.checksum = checksum
        self.kind = "unknown"

    def __str__(self):
        return (f"{self.tag:6s} @0x{self.offset:08x}  {self.size:>12,} bytes  "
                f"build=0x{self.build:x}  csum=0x{self.checksum:08x}  "
                f"{self.kind}")


def read_container(path: str):
    with open(path, "rb") as container_file:
        container_bytes = container_file.read()
    if container_bytes[MAGIC_OFFSET:MAGIC_OFFSET + 4] != MAGIC:
        raise SystemExit(
            f"{path}: not a TDIH firmware container "
            f"(magic was {container_bytes[MAGIC_OFFSET:MAGIC_OFFSET+4]!r})")

    payload_area_offset, version = struct.unpack_from(">II",
                                                      container_bytes, 0)
    build = struct.unpack_from(">I", container_bytes, 0x0C)[0]
    model = container_bytes[0x10:0x2C].split(b"\0")[0].decode("latin1",
                                                              "replace")
    payload_size = struct.unpack_from(">I", container_bytes, 0x2C)[0]
    checksum = struct.unpack_from(">I", container_bytes, 0x30)[0]
    description = (container_bytes[0x34:0x60].split(b"\0")[0]
                   .decode("latin1", "replace"))
    count = struct.unpack_from(">I", container_bytes, SECTION_COUNT_OFFSET)[0]

    sections = []
    for i in range(count):
        entry_offset = SECTION_TABLE_OFFSET + i * SECTION_ENTRY_SIZE
        tag = container_bytes[entry_offset:entry_offset + 4].decode("latin1")
        (relative_offset, size, _reserved, section_build,
         section_checksum) = struct.unpack_from(">IIIII", container_bytes,
                                                entry_offset + 4)
        section = Section(tag, payload_area_offset + relative_offset, size,
                          section_build, section_checksum)
        section_head = container_bytes[section.offset:section.offset + 16]
        for signature, format_name in PAYLOAD_SIGNATURES:
            if section_head.startswith(signature):
                section.kind = format_name
                break
        sections.append(section)

    metadata = {
        "path": path, "size": len(container_bytes),
        "data_offset": payload_area_offset,
        "format_version": version, "build": build, "model": model,
        "payload_size": payload_size, "checksum": checksum,
        "description": description, "sections": sections,
    }
    return container_bytes, metadata


def show_container_info(args) -> int:
    container_bytes, metadata = read_container(args.file)
    print(f"{os.path.basename(metadata['path'])}   {metadata['size']:,} bytes")
    print(f"  description     {metadata['description']}")
    print(f"  model           {metadata['model']}")
    print(f"  format version  {metadata['format_version']}")
    print(f"  build           0x{metadata['build']:x}")
    print(f"  payload         {metadata['payload_size']:,} bytes at "
          f"0x{metadata['data_offset']:x}")
    print(f"  checksum        0x{metadata['checksum']:08x}")
    print(f"  sections        {len(metadata['sections'])}")
    covered = 0
    for section in metadata["sections"]:
        print("   ", section)
        covered += section.size
    print(
        f"\n  {covered:,} of {metadata['payload_size']:,} payload bytes "
        f"accounted for"
        f" ({'complete' if covered == metadata['payload_size'] else 'INCOMPLETE'})"  # noqa: E501
    )

    for section in metadata["sections"]:
        if section.kind == "u-boot uImage":
            uimage_offset = section.offset
            size = struct.unpack_from(">I", container_bytes,
                                      uimage_offset + 12)[0]
            load_address, entry_point = struct.unpack_from(
                ">II", container_bytes, uimage_offset + 16)
            name = (container_bytes[uimage_offset + 32:uimage_offset + 64]
                    .rstrip(b"\0").decode("latin1", "replace"))
            print(f"\n  kernel: {name}")
            print(f"    {size:,} bytes, load=0x{load_address:08x}, "
                  f"entry=0x{entry_point:08x}")
    return 0


def extract_sections(args) -> int:
    container_bytes, metadata = read_container(args.file)
    os.makedirs(args.out, exist_ok=True)
    for section in metadata["sections"]:
        data = container_bytes[section.offset:section.offset + section.size]
        if section.kind == "u-boot uImage" and not args.raw:
            data = data[64:]  # strip the 64-byte uImage header
        output_path = os.path.join(args.out, f"fw-{section.tag}.bin")
        with open(output_path, "wb") as section_file:
            section_file.write(data)
        print(f"  {section.tag:6s} -> {output_path}  "
              f"({len(data):,} bytes, {section.kind})")
    return 0


# ---------------------------------------------------------------------------
# JFFS2
# ---------------------------------------------------------------------------

JFFS2_MAGIC, JFFS2_NODETYPE_DIRENT, JFFS2_NODETYPE_INODE = (
    0x1985, 0xE001, 0xE002)
(JFFS2_COMPR_NONE, JFFS2_COMPR_ZERO, JFFS2_COMPR_RTIME, JFFS2_COMPR_COPY,
 JFFS2_COMPR_ZLIB) = 0, 1, 2, 4, 6
DIRENT_TYPE_NAMES = {1: "fifo", 2: "chr", 4: "dir", 6: "blk", 8: "file",
                     10: "link", 12: "sock"}


def _rtime_decompress(compressed: bytes, output_length: int) -> bytes:
    positions = [0] * 256
    output = bytearray(output_length)
    output_pos = input_pos = 0
    while output_pos < output_length and input_pos + 1 < len(compressed):
        value = compressed[input_pos]
        input_pos += 1
        output[output_pos] = value
        output_pos += 1
        repeat = compressed[input_pos]
        input_pos += 1
        copy_from = positions[value]
        positions[value] = output_pos
        if repeat:
            if copy_from + repeat >= output_pos:
                while repeat and output_pos < output_length:
                    output[output_pos] = output[copy_from]
                    output_pos += 1
                    copy_from += 1
                    repeat -= 1
            else:
                copy_length = min(repeat, output_length - output_pos)
                output[output_pos:output_pos + copy_length] = \
                    output[copy_from:copy_from + copy_length]
                output_pos += copy_length
    return bytes(output)


def _decompress(compression: int, data: bytes, compressed_size: int,
                decompressed_size: int):
    if compression in (JFFS2_COMPR_NONE, JFFS2_COMPR_COPY):
        return data[:decompressed_size]
    if compression == JFFS2_COMPR_ZERO:
        return b"\0" * decompressed_size
    if compression == JFFS2_COMPR_ZLIB:
        try:
            return zlib.decompress(data[:compressed_size])
        except zlib.error:
            try:
                return zlib.decompressobj().decompress(data[:compressed_size])
            except Exception:
                return None
    if compression == JFFS2_COMPR_RTIME:
        return _rtime_decompress(data[:compressed_size], decompressed_size)
    return None


def rebuild_rootfs(args) -> int:
    with open(args.file, "rb") as image_file:
        image = image_file.read()
    if image[:2] != b"\x85\x19":
        print("warning: this does not start with the JFFS2 magic; "
              "trying anyway", file=sys.stderr)

    dirents = collections.defaultdict(list)
    fragments = collections.defaultdict(list)
    unsupported_counts = collections.Counter()

    pos, size = 0, len(image)
    while pos < size - 12:
        magic, node_type, total_length = struct.unpack_from("<HHI", image, pos)
        if (magic != JFFS2_MAGIC or total_length < 12
                or pos + total_length > size):
            pos += 4
            continue
        if node_type == JFFS2_NODETYPE_DIRENT and pos + 40 <= size:
            parent_inode, version, inode, _ = struct.unpack_from(
                "<IIII", image, pos + 12)
            name_size, dirent_type = struct.unpack_from("<BB", image, pos + 28)
            name = image[pos + 40:pos + 40 + name_size]
            if name_size and name:
                dirents[inode].append(
                    (version, name.decode("utf-8", "replace"), parent_inode,
                     dirent_type))
        elif node_type == JFFS2_NODETYPE_INODE and pos + 68 <= size:
            inode, version = struct.unpack_from("<II", image, pos + 12)
            file_size = struct.unpack_from("<I", image, pos + 28)[0]
            fragment_offset, compressed_size, decompressed_size = \
                struct.unpack_from("<III", image, pos + 44)
            compression = image[pos + 56]
            decompressed = _decompress(
                compression, image[pos + 68:pos + 68 + compressed_size],
                compressed_size, decompressed_size)
            if decompressed is None:
                unsupported_counts[compression] += 1
            else:
                fragments[inode].append(
                    (version, fragment_offset, decompressed, file_size))
        pos += (total_length + 3) & ~3

    # JFFS2 is log-structured: the highest version of each node wins.
    latest_dirents = {}
    for inode, dirent_versions in dirents.items():
        dirent_versions.sort()
        _version, name, parent_inode, dirent_type = dirent_versions[-1]
        if inode:
            latest_dirents[inode] = (name, parent_inode, dirent_type)
    children = collections.defaultdict(list)
    for inode, (name, parent_inode, dirent_type) in latest_dirents.items():
        children[parent_inode].append((name, inode, dirent_type))

    paths = {}

    def walk_directory(inode, prefix, depth=0):
        if depth > 24:
            return
        for name, child, dirent_type in sorted(children.get(inode, [])):
            child_path = prefix + "/" + name
            paths[child] = (child_path, dirent_type)
            if dirent_type == 4:
                walk_directory(child, child_path, depth + 1)

    walk_directory(1, "")

    written = 0
    for inode, (path, dirent_type) in paths.items():
        if dirent_type not in (8, 10) or inode not in fragments:
            continue
        parts = sorted(fragments[inode], key=lambda t: t[0])
        file_size = max(part[3] for part in parts)
        file_bytes = bytearray(file_size)
        for _version, fragment_offset, data, _ in parts:
            end = min(fragment_offset + len(data), file_size)
            if end > fragment_offset:
                file_bytes[fragment_offset:end] = data[:end - fragment_offset]
        output_path = os.path.join(args.out,
                                   path.lstrip("/").replace("/", os.sep))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            with open(output_path, "wb") as output_file:
                output_file.write(bytes(file_bytes))
            written += 1
        except OSError as exc:
            print(f"  ! {path}: {exc}", file=sys.stderr)

    listing = os.path.join(args.out, "..", "rootfs-listing.txt")
    with open(listing, "w", encoding="utf-8") as listing_file:
        for path, dirent_type in sorted(paths.values()):
            listing_file.write(
                f"{DIRENT_TYPE_NAMES.get(dirent_type, '?'):5s} {path}\n")

    print(f"  {len(paths):,} paths, {written:,} files written to {args.out}")
    print(f"  listing: {os.path.normpath(listing)}")
    if unsupported_counts:
        # JFFS2's own compressor numbers (JFFS2_COMPR_* in
        # linux/jffs2.h, as named above): LZO is 7, and 8 has no
        # mainline meaning, so it prints as a bare number like any
        # other unnamed type.
        compression_names = {3: "RUBINMIPS", 5: "DYNRUBIN", 7: "LZO"}
        print("  unsupported compression: "
              + ", ".join(f"{compression_names.get(k, k)} x{v}"
                          for k, v in unsupported_counts.items()))
    return 0


def print_strings(args) -> int:
    with open(args.file, "rb") as input_file:
        data = input_file.read()
    found = [s.decode("latin1")
             for s in re.findall(rb"[\x20-\x7e]{%d,}" % args.min, data)]
    unique_strings = list(dict.fromkeys(found))
    if args.grep:
        pattern = re.compile(args.grep, re.I)
        unique_strings = [s for s in unique_strings if pattern.search(s)]
    for s in unique_strings:
        print(s)
    print(f"\n# {len(unique_strings):,} strings", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    subparser = subparsers.add_parser(
        "info", help="describe a .tdf firmware container")
    subparser.add_argument("file")
    subparser.set_defaults(func=show_container_info)

    subparser = subparsers.add_parser(
        "extract", help="write each section to its own file")
    subparser.add_argument("file")
    subparser.add_argument("-o", "--out", default="fw-out")
    subparser.add_argument("--raw", action="store_true",
                           help="keep uImage headers")
    subparser.set_defaults(func=extract_sections)

    subparser = subparsers.add_parser(
        "rootfs", help="rebuild files from a JFFS2 image")
    subparser.add_argument("file")
    subparser.add_argument("-o", "--out", default="rootfs")
    subparser.set_defaults(func=rebuild_rootfs)

    subparser = subparsers.add_parser(
        "strings", help="printable strings, optionally filtered")
    subparser.add_argument("file")
    subparser.add_argument("--min", type=int, default=4)
    subparser.add_argument("--grep", default=None, help="regex filter")
    subparser.set_defaults(func=print_strings)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
