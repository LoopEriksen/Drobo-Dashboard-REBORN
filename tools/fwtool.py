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
# How much of a section's start is compared with PAYLOAD_SIGNATURES.
SECTION_HEAD_BYTES = 16
# A u-boot uImage starts with a fixed 64-byte header before the
# kernel itself.
UIMAGE_HEADER_BYTES = 64

PAYLOAD_SIGNATURES = [
    (b"hsqs", "squashfs (LE)"), (b"sqsh", "squashfs (BE)"),
    (b"\x45\x3d\xcd\x28", "cramfs"), (b"UBI#", "UBI"),
    (b"\x85\x19", "jffs2"), (b"\x1f\x8b\x08", "gzip"),
    (b"\xfd7zXZ\x00", "xz"), (b"\x7fELF", "ELF"),
    (b"\x27\x05\x19\x56", "u-boot uImage"), (b"-rom1fs-", "romfs"),
]


class Section:
    """One entry of the container's section table.

    The table stores each offset relative to the payload area; it is
    made absolute when the table is read, so everything after that
    can slice the container directly.
    """

    def __init__(self, tag, absolute_offset, size_bytes, build_number,
                 checksum):
        self.tag = tag
        self.absolute_offset = absolute_offset
        self.size_bytes = size_bytes
        self.build_number = build_number
        self.checksum = checksum
        self.payload_format = "unknown"

    def __str__(self):
        return (f"{self.tag:6s} @0x{self.absolute_offset:08x}  "
                f"{self.size_bytes:>12,} bytes  "
                f"build=0x{self.build_number:x}  "
                f"csum=0x{self.checksum:08x}  "
                f"{self.payload_format}")


def _payload_format(section_head: bytes) -> str:
    """The format whose signature `section_head` starts with, or
    "unknown"; the first match in PAYLOAD_SIGNATURES wins."""
    for signature, format_name in PAYLOAD_SIGNATURES:
        if section_head.startswith(signature):
            return format_name
    return "unknown"


def _read_section_table(container_bytes: bytes, payload_area_offset: int,
                        section_count: int) -> list[Section]:
    """Every section-table entry, with its offset made absolute and its
    payload format sniffed from its first bytes."""
    sections = []
    for section_index in range(section_count):
        entry_offset = (SECTION_TABLE_OFFSET
                        + section_index * SECTION_ENTRY_SIZE)
        tag = container_bytes[entry_offset:entry_offset + 4].decode("latin1")
        (relative_offset, size_bytes, _reserved, section_build_number,
         section_checksum) = struct.unpack_from(">IIIII", container_bytes,
                                                entry_offset + 4)
        section = Section(tag, payload_area_offset + relative_offset,
                          size_bytes, section_build_number, section_checksum)
        section_head = container_bytes[
            section.absolute_offset:
            section.absolute_offset + SECTION_HEAD_BYTES]
        section.payload_format = _payload_format(section_head)
        sections.append(section)
    return sections


def read_container(path: str):
    """The container's bytes and its parsed header, sections included.

    A file without the TDIH magic ends the program with SystemExit
    rather than an exception, because this is a command-line tool and
    the message is all the user needs.
    """
    with open(path, "rb") as container_file:
        container_bytes = container_file.read()
    if container_bytes[MAGIC_OFFSET:MAGIC_OFFSET + 4] != MAGIC:
        raise SystemExit(
            f"{path}: not a TDIH firmware container "
            f"(magic was {container_bytes[MAGIC_OFFSET:MAGIC_OFFSET+4]!r})")

    payload_area_offset, format_version = struct.unpack_from(
        ">II", container_bytes, 0)
    build_number = struct.unpack_from(">I", container_bytes, 0x0C)[0]
    model = container_bytes[0x10:0x2C].split(b"\0")[0].decode("latin1",
                                                              "replace")
    payload_size = struct.unpack_from(">I", container_bytes, 0x2C)[0]
    checksum = struct.unpack_from(">I", container_bytes, 0x30)[0]
    description = (container_bytes[0x34:0x60].split(b"\0")[0]
                   .decode("latin1", "replace"))
    section_count = struct.unpack_from(">I", container_bytes,
                                       SECTION_COUNT_OFFSET)[0]

    sections = _read_section_table(container_bytes, payload_area_offset,
                                   section_count)

    metadata = {
        "path": path, "size": len(container_bytes),
        "payload_area_offset": payload_area_offset,
        "format_version": format_version, "build": build_number,
        "model": model,
        "payload_size": payload_size, "checksum": checksum,
        "description": description, "sections": sections,
    }
    return container_bytes, metadata


def _print_uimage_header(container_bytes: bytes, section: Section) -> None:
    """The kernel name, size, load address and entry point from a
    uImage section's header."""
    uimage_offset = section.absolute_offset
    image_data_size_bytes = struct.unpack_from(">I", container_bytes,
                                               uimage_offset + 12)[0]
    load_address, entry_point = struct.unpack_from(
        ">II", container_bytes, uimage_offset + 16)
    image_name = (container_bytes[uimage_offset + 32:uimage_offset + 64]
                  .rstrip(b"\0").decode("latin1", "replace"))
    print(f"\n  kernel: {image_name}")
    print(f"    {image_data_size_bytes:,} bytes, load=0x{load_address:08x}, "
          f"entry=0x{entry_point:08x}")


def show_container_info(args) -> int:
    """The info sub-command: the header, every section, whether the
    sections cover the whole payload, and the kernel's uImage header,
    whose name carries the kernel version."""
    container_bytes, metadata = read_container(args.file)
    print(f"{os.path.basename(metadata['path'])}   {metadata['size']:,} bytes")
    print(f"  description     {metadata['description']}")
    print(f"  model           {metadata['model']}")
    print(f"  format version  {metadata['format_version']}")
    print(f"  build           0x{metadata['build']:x}")
    print(f"  payload         {metadata['payload_size']:,} bytes at "
          f"0x{metadata['payload_area_offset']:x}")
    print(f"  checksum        0x{metadata['checksum']:08x}")
    print(f"  sections        {len(metadata['sections'])}")
    covered = 0
    for section in metadata["sections"]:
        print("   ", section)
        covered += section.size_bytes
    coverage = ("complete" if covered == metadata["payload_size"]
                else "INCOMPLETE")
    print(
        f"\n  {covered:,} of {metadata['payload_size']:,} payload bytes "
        f"accounted for ({coverage})"
    )

    for section in metadata["sections"]:
        if section.payload_format == "u-boot uImage":
            _print_uimage_header(container_bytes, section)
    return 0


def extract_sections(args) -> int:
    """The extract sub-command: each section to fw-<tag>.bin.

    A uImage loses its 64-byte header unless --raw is given, so the
    kernel file holds the kernel itself.
    """
    container_bytes, metadata = read_container(args.file)
    os.makedirs(args.out, exist_ok=True)
    for section in metadata["sections"]:
        section_bytes = container_bytes[
            section.absolute_offset:
            section.absolute_offset + section.size_bytes]
        if section.payload_format == "u-boot uImage" and not args.raw:
            section_bytes = section_bytes[UIMAGE_HEADER_BYTES:]
        output_path = os.path.join(args.out, f"fw-{section.tag}.bin")
        with open(output_path, "wb") as section_file:
            section_file.write(section_bytes)
        print(f"  {section.tag:6s} -> {output_path}  "
              f"({len(section_bytes):,} bytes, {section.payload_format})")
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
DIRENT_TYPE_DIR = 4
DIRENT_TYPE_FILE = 8
DIRENT_TYPE_LINK = 10

# Every node starts with magic, node type and total length.
JFFS2_NODE_HEADER_BYTES = 12
# Where a directory entry's name starts, and an inode's data.
DIRENT_NAME_OFFSET = 40
INODE_DATA_OFFSET = 68
# The inode of the filesystem's root directory.
JFFS2_ROOT_INODE = 1
# Deeper than any real tree; it stops a corrupt image whose entries
# form a loop from recursing forever.
MAX_DIRECTORY_DEPTH = 24


def _copy_overlapping_run(output: bytearray, output_pos: int, copy_from: int,
                          repeat_count: int, output_length: int) -> int:
    """Copy a back-reference that overlaps what it is writing, byte by
    byte, so each byte copied can itself be copied again; returns the
    new output position."""
    while repeat_count and output_pos < output_length:
        output[output_pos] = output[copy_from]
        output_pos += 1
        copy_from += 1
        repeat_count -= 1
    return output_pos


def _decompress_rtime(compressed: bytes, output_length: int) -> bytes:
    """JFFS2's rtime decompressor, ported from linux/fs/jffs2/
    compr_rtime.c.

    Each input pair is a literal byte and a repeat count; the repeat
    copies from where that byte value was last written. A copy that
    overlaps its own output must go byte by byte, exactly as the
    kernel does, or a run of a repeating pattern comes out wrong.
    """
    last_output_position_by_byte = [0] * 256
    output = bytearray(output_length)
    output_pos = input_pos = 0
    while output_pos < output_length and input_pos + 1 < len(compressed):
        byte_value = compressed[input_pos]
        input_pos += 1
        output[output_pos] = byte_value
        output_pos += 1
        repeat_count = compressed[input_pos]
        input_pos += 1
        copy_from = last_output_position_by_byte[byte_value]
        last_output_position_by_byte[byte_value] = output_pos
        if not repeat_count:
            continue
        if copy_from + repeat_count >= output_pos:
            output_pos = _copy_overlapping_run(
                output, output_pos, copy_from, repeat_count, output_length)
            continue
        copy_length = min(repeat_count, output_length - output_pos)
        output[output_pos:output_pos + copy_length] = (
            output[copy_from:copy_from + copy_length])
        output_pos += copy_length
    return bytes(output)


def _decompress_zlib(compressed: bytes):
    """zlib data, or None.

    A stream that zlib.decompress rejects is retried with a
    decompressobj, which returns what it can of a truncated stream
    instead of failing outright.
    """
    try:
        return zlib.decompress(compressed)
    except zlib.error:
        pass
    try:
        return zlib.decompressobj().decompress(compressed)
    except Exception:
        return None


def _decompress_jffs2_node(compression: int, data: bytes,
                           compressed_size: int, decompressed_size: int):
    """One inode node's data, or None for a compressor this tool does
    not implement (the caller counts those)."""
    if compression in (JFFS2_COMPR_NONE, JFFS2_COMPR_COPY):
        return data[:decompressed_size]
    if compression == JFFS2_COMPR_ZERO:
        return b"\0" * decompressed_size
    if compression == JFFS2_COMPR_ZLIB:
        return _decompress_zlib(data[:compressed_size])
    if compression == JFFS2_COMPR_RTIME:
        return _decompress_rtime(data[:compressed_size], decompressed_size)
    return None


def _read_dirent_node(image: bytes, offset: int,
                      dirents_by_inode: dict) -> None:
    """File the directory entry at `offset` under its inode, as
    (version, name, parent inode, type). An entry without a name is
    skipped."""
    parent_inode, version, inode, _ = struct.unpack_from(
        "<IIII", image, offset + 12)
    name_size, dirent_type = struct.unpack_from("<BB", image, offset + 28)
    name = image[offset + DIRENT_NAME_OFFSET:
                 offset + DIRENT_NAME_OFFSET + name_size]
    if name_size and name:
        dirents_by_inode[inode].append(
            (version, name.decode("utf-8", "replace"), parent_inode,
             dirent_type))


def _read_inode_node(image: bytes, offset: int, fragments_by_inode: dict,
                     unsupported_counts: collections.Counter) -> None:
    """File the data fragment of the inode node at `offset` as
    (version, offset in file, data, file size), or count its
    compressor as unsupported."""
    inode, version = struct.unpack_from("<II", image, offset + 12)
    file_size = struct.unpack_from("<I", image, offset + 28)[0]
    fragment_offset, compressed_size, decompressed_size = (
        struct.unpack_from("<III", image, offset + 44))
    compression = image[offset + 56]
    decompressed = _decompress_jffs2_node(
        compression,
        image[offset + INODE_DATA_OFFSET:
              offset + INODE_DATA_OFFSET + compressed_size],
        compressed_size, decompressed_size)
    if decompressed is None:
        unsupported_counts[compression] += 1
        return
    fragments_by_inode[inode].append(
        (version, fragment_offset, decompressed, file_size))


def _scan_jffs2_nodes(image: bytes):
    """Every directory entry and data fragment in a JFFS2 image, and a
    count of nodes whose compressor is not supported.

    Anything that is not a well-formed node is stepped over four bytes
    at a time (nodes are 4-byte aligned), so padding, erased blocks
    and damage do not stop the scan.
    """
    dirents_by_inode = collections.defaultdict(list)
    fragments_by_inode = collections.defaultdict(list)
    unsupported_counts = collections.Counter()

    offset, image_size_bytes = 0, len(image)
    while offset < image_size_bytes - JFFS2_NODE_HEADER_BYTES:
        magic, node_type, node_length_bytes = struct.unpack_from(
            "<HHI", image, offset)
        if (magic != JFFS2_MAGIC
                or node_length_bytes < JFFS2_NODE_HEADER_BYTES
                or offset + node_length_bytes > image_size_bytes):
            offset += 4
            continue
        if (node_type == JFFS2_NODETYPE_DIRENT
                and offset + DIRENT_NAME_OFFSET <= image_size_bytes):
            _read_dirent_node(image, offset, dirents_by_inode)
        elif (node_type == JFFS2_NODETYPE_INODE
              and offset + INODE_DATA_OFFSET <= image_size_bytes):
            _read_inode_node(image, offset, fragments_by_inode,
                             unsupported_counts)
        offset += (node_length_bytes + 3) & ~3  # the next aligned node
    return dirents_by_inode, fragments_by_inode, unsupported_counts


def _latest_dirents(dirents_by_inode: dict) -> dict:
    """inode -> (name, parent inode, type) from each inode's newest
    directory entry, since JFFS2 is log-structured and the highest
    version wins. Inode 0 (a deletion) is dropped."""
    latest_dirents = {}
    for inode, dirent_versions in dirents_by_inode.items():
        dirent_versions.sort()
        _version, name, parent_inode, dirent_type = dirent_versions[-1]
        if inode:
            latest_dirents[inode] = (name, parent_inode, dirent_type)
    return latest_dirents


def _walk_directory(children: dict, inode: int, prefix: str, paths: dict,
                    depth: int = 0) -> None:
    """Add the path of everything under directory `inode` to `paths`,
    in name order, recursing into subdirectories."""
    if depth > MAX_DIRECTORY_DEPTH:
        return
    for name, child, dirent_type in sorted(children.get(inode, [])):
        child_path = prefix + "/" + name
        paths[child] = (child_path, dirent_type)
        if dirent_type == DIRENT_TYPE_DIR:
            _walk_directory(children, child, child_path, paths, depth + 1)


def _build_paths(latest_dirents: dict) -> dict:
    """inode -> (absolute path, type) for everything reachable from the
    root directory."""
    children = collections.defaultdict(list)
    for inode, (name, parent_inode, dirent_type) in latest_dirents.items():
        children[parent_inode].append((name, inode, dirent_type))
    paths = {}
    _walk_directory(children, JFFS2_ROOT_INODE, "", paths)
    return paths


def _assemble_file(inode_fragments: list) -> bytearray:
    """A file's bytes from its fragments, applied oldest version first
    so a newer fragment overwrites an older one. The size is the
    largest any fragment records."""
    inode_fragments = sorted(inode_fragments, key=lambda fragment: fragment[0])
    file_size = max(fragment[3] for fragment in inode_fragments)
    file_bytes = bytearray(file_size)
    for _version, fragment_offset, data, _ in inode_fragments:
        end = min(fragment_offset + len(data), file_size)
        if end > fragment_offset:
            file_bytes[fragment_offset:end] = data[:end - fragment_offset]
    return file_bytes


def _write_rebuilt_file(output_path: str, path: str,
                        file_bytes: bytearray) -> bool:
    """Write one rebuilt file; on failure report it under its image
    path and return False, so one bad name does not stop the rest."""
    try:
        with open(output_path, "wb") as output_file:
            output_file.write(bytes(file_bytes))
        return True
    except OSError as exc:
        print(f"  ! {path}: {exc}", file=sys.stderr)
        return False


def _write_files(paths: dict, fragments_by_inode: dict, out_dir: str) -> int:
    """Write every regular file and symlink that has data under
    `out_dir`; returns how many were written. A file that cannot be
    written is reported and skipped."""
    files_written_count = 0
    for inode, (path, dirent_type) in paths.items():
        if (dirent_type not in (DIRENT_TYPE_FILE, DIRENT_TYPE_LINK)
                or inode not in fragments_by_inode):
            continue
        file_bytes = _assemble_file(fragments_by_inode[inode])
        output_path = os.path.join(out_dir,
                                   path.lstrip("/").replace("/", os.sep))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if _write_rebuilt_file(output_path, path, file_bytes):
            files_written_count += 1
    return files_written_count


def _write_listing(paths: dict, out_dir: str) -> str:
    """Write rootfs-listing.txt (type and path of everything, sorted)
    inside `out_dir`, and return its path.

    Inside, not beside: it used to go to `out_dir`/.., so with `-o .`
    or a top-level folder it landed in the parent folder or the drive's
    root, outside the folder the user named.
    """
    listing_path = os.path.join(out_dir, "rootfs-listing.txt")
    with open(listing_path, "w", encoding="utf-8") as listing_file:
        for path, dirent_type in sorted(paths.values()):
            listing_file.write(
                f"{DIRENT_TYPE_NAMES.get(dirent_type, '?'):5s} {path}\n")
    return listing_path


def _report_unsupported(unsupported_counts: collections.Counter) -> None:
    """Name the compressors whose nodes could not be read, with their
    counts, so a missing file has an explanation."""
    if not unsupported_counts:
        return
    # JFFS2's own compressor numbers (JFFS2_COMPR_* in
    # linux/jffs2.h, as named above): LZO is 7, and 8 has no
    # mainline meaning, so it prints as a bare number like any
    # other unnamed type.
    compression_names = {3: "RUBINMIPS", 5: "DYNRUBIN", 7: "LZO"}
    counts_text = ", ".join(
        f"{compression_names.get(compression_type, compression_type)} "
        f"x{node_count}"
        for compression_type, node_count in unsupported_counts.items())
    print("  unsupported compression: " + counts_text)


def rebuild_rootfs(args) -> int:
    """The rootfs sub-command: rebuild the files of a JFFS2 image.

    JFFS2 is log-structured, so a file is the newest version of its
    directory entry plus its data fragments in version order; the
    image is scanned once, then the tree is rebuilt from the root.
    The output folder is made as soon as the image has been read,
    because it was only ever made per file written: an image with
    nothing to extract crashed writing the listing into a folder that
    did not exist. Not before the read, so a mistyped image path leaves
    no empty folder behind.
    """
    with open(args.file, "rb") as image_file:
        image = image_file.read()
    os.makedirs(args.out, exist_ok=True)
    if image[:2] != b"\x85\x19":
        print("warning: this does not start "
              "with the JFFS2 magic; trying anyway", file=sys.stderr)

    dirents_by_inode, fragments_by_inode, unsupported_counts = (
        _scan_jffs2_nodes(image))
    paths = _build_paths(_latest_dirents(dirents_by_inode))
    files_written_count = _write_files(paths, fragments_by_inode, args.out)
    listing_path = _write_listing(paths, args.out)

    print(f"  {len(paths):,} paths, {files_written_count:,} files written "
          f"to {args.out}")
    print(f"  listing: {os.path.normpath(listing_path)}")
    _report_unsupported(unsupported_counts)
    return 0


def print_strings(args) -> int:
    """The strings sub-command: each distinct printable run of --min
    characters or more, in first-seen order, optionally filtered by
    --grep. The count goes to stderr so stdout stays pipeable."""
    with open(args.file, "rb") as input_file:
        file_bytes = input_file.read()
    all_strings = [text.decode("latin1")
                   for text in re.findall(rb"[\x20-\x7e]{%d,}" % args.min,
                                          file_bytes)]
    unique_strings = list(dict.fromkeys(all_strings))
    if args.grep:
        pattern = re.compile(args.grep, re.I)
        unique_strings = [text for text in unique_strings
                          if pattern.search(text)]
    for text in unique_strings:
        print(text)
    print(f"\n# {len(unique_strings):,} strings", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    """Run one sub-command; each one's function is its argparse
    default, so dispatch is one call."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    info_parser = subparsers.add_parser(
        "info", help="describe a .tdf firmware container")
    info_parser.add_argument("file")
    info_parser.set_defaults(func=show_container_info)

    extract_parser = subparsers.add_parser(
        "extract", help="write each section to its own file")
    extract_parser.add_argument("file")
    extract_parser.add_argument("-o", "--out", default="fw-out")
    extract_parser.add_argument("--raw", action="store_true",
                                help="keep uImage headers")
    extract_parser.set_defaults(func=extract_sections)

    rootfs_parser = subparsers.add_parser(
        "rootfs", help="rebuild files from a JFFS2 image")
    rootfs_parser.add_argument("file")
    rootfs_parser.add_argument("-o", "--out", default="rootfs")
    rootfs_parser.set_defaults(func=rebuild_rootfs)

    strings_parser = subparsers.add_parser(
        "strings", help="printable strings, optionally filtered")
    strings_parser.add_argument("file")
    strings_parser.add_argument("--min", type=int, default=4)
    strings_parser.add_argument("--grep", default=None, help="regex filter")
    strings_parser.set_defaults(func=print_strings)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
