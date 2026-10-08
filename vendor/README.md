# vendor/ — Drobo's own binaries

**On GitHub this folder shows only this README.** Everything else in it stays
on your own PC, because git ignores it: copies of Drobo's own software, such
as the original Dashboard installer, a firmware download and the files
unpacked from it.
The analysis tools in `tools/` read them from here.

Put copies of the original Drobo software here (see `.gitignore`).

Expected contents:

```
vendor/
  Drobo-Dashboard-3.5.0.exe       the final Windows installer (studied, never run)
  Drobo-5N-firmware-4.2.1/        the firmware download: release.Drobo5N.4-2-1.tdf
  extracted/                      what tools/fwtool.py unpacks from that .tdf:
                                  fw-*.bin sections, rootfs/ (the device's own
                                  files; tools/test_fw_params.py looks for
                                  rootfs/sbin/nasd) and rootfs-listing.txt,
                                  plus text files of strings pulled from the
                                  firmware and the installer
```

The original Dashboard's own programs (`Drobo Dashboard.exe`, `DDService.exe`)
do not need copying here: `tools/dashboard_strings.py` is given their path,
such as the folder Windows installed them in.

## Why this matters more than it sounds

Drobo published no protocol documentation and the company is gone. The
Dashboard binaries are the only artefact that still contains a description of
how a Drobo is spoken to — the port numbers, the command names, the XML tags,
the error strings are all in there as data, whether or not anyone meant them to
be readable.

Packet capture tells you *what* a conversation looked like on one occasion.
The binaries tell you the *vocabulary* — including commands Dashboard only
sends in situations you can't safely reproduce, like a failing drive or a
degraded array. The two together are far stronger than either alone: strings
give you candidate names and ports, captures confirm which are real and how
they're framed.

## What gets done with it

Static inspection only. In order:

1. **Identify the installer type** (NSIS, InstallShield, MSI, Inno) and unpack
   it *without running it*.
2. **Inventory the payload** — executables, DLLs, .NET assemblies, config
   files, XML schemas, help content, driver packages.
3. **Extract strings** from each binary and filter for the interesting shapes:
   URLs, `mdns`/`_tcp` service names, port numbers, XML element and attribute
   names, format strings, SQL, error messages, registry paths.
4. **Check for managed code.** If any component is .NET, its method and class
   names survive in the assembly metadata and can be read directly — that would
   be the single biggest shortcut available.
5. **Cross-reference against `docs/protocol-map.md`**, marking everything found
   this way as `GUESS` until a packet capture confirms it.

Results so far: `docs/original-dashboard-installer-notes.md` (the installer)
and `docs/protocol-map.md` (the protocol).

## Ground rules

- **Never execute anything from this folder** as part of analysis. Reading a
  file is safe; running an installer is not, and there's no reason to.
- **Never commit these files.** They remain Drobo's copyrighted software. This
  project reimplements the protocol for hardware you own — that's ordinary
  interoperability work — but redistributing their binaries is a different
  thing entirely, and pointless besides.
- **Findings go in `docs/protocol-map.md`**, marked with how they were
  discovered, so it's always clear which facts came from a real capture and
  which came from reading strings out of a binary.
