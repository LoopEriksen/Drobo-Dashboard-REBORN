# Drobo Dashboard 3.5.0 installer — analysis notes

Static analysis of `vendor/Drobo-Dashboard-3.5.0.exe`, the final Windows
release. Nothing was executed.

**Outcome: the container format is fully mapped, but the file payloads are
encrypted and could not be decrypted by static analysis alone.** What that
means in practice, and the cheaper routes, are at the bottom.

---

## What it is

| | |
|---|---|
| Type | InstallShield **Setup Player V16** (InstallShield 2010) |
| PE | x86, 4 sections, sections end at `0x117200` |
| Bootstrapper | `setupPreReq.pdb` — this outer exe is only the prerequisite launcher |
| Appended payload | 15,476,496 bytes from `0x117200` to EOF |
| Real product | `Drobo Dashboard.msi` + `Drobo Dashboard.isc`, embedded |
| Prerequisites referenced | `dotnetfx.exe`, `vcredist_x86.exe`, `instmsi30.exe`, `WindowsInstaller-KB893803-x86.exe` |

The `.NET` prerequisite is worth noting: **Drobo Dashboard is at least partly a
.NET application.** If the MSI is ever unpacked, any managed assemblies inside
will carry full class and method names in their metadata — which would be the
richest possible source for the client side of the protocol.

---

## Container format (recovered)

The payload is a flat sequence of entries, back to back, with no index. Each
entry is:

```
    +0x000  char[260]   filename, NUL-padded to MAX_PATH
    +0x104  u32         kind (6 for every entry seen)
    +0x108  u32         0
    +0x10c  u32         payload size in bytes
    +0x110  u32         0
    +0x114  u32         0
    +0x118  u32         1
    +0x11c  u8[0x18]    zeros
    +0x134  u8[size]    payload  <- encrypted
    then the next entry begins immediately
```

Verified by arithmetic: the `Drobo Dashboard.msi` entry declares 8,905,639
bytes of payload starting at `0x75979C`, which ends at exactly `0xFD7B43` —
the byte where the next entry's name (`Setup.ini`) begins. The chain is
self-consistent across every entry checked.

### Entries located

| Name | Size | Payload at |
|---|---|---|
| `0x0804.ini` | 4,071 | `0x001173DA` |
| `0x0409.ini` | 4,598 | `0x001184F9` |
| `Drobo Dashboard.isc` | 305,664 | `0x007591B9` |
| **`Drobo Dashboard.msi`** | **8,905,639** | **`0x0075979C`** |
| `Setup.ini` | 1,150 | `0x00FD7C7B` |

Others present but not individually mapped: `instmsiw.exe`, `vcredist_x86.exe`,
and several more language `.ini` files.

---

## Why the payloads could not be decrypted

Tested and ruled out:

| Hypothesis | Result |
|---|---|
| Plain deflate / zlib at any offset in the first 64 bytes | No |
| InstallShield CAB obfuscation (`ROR8(b ^ 0xD5, 2) - seed % 0x47`) | No |
| Its inverse | No |
| Flat XOR `0xD5`, bare `ROR2`, bare `ROL2` | No |
| **Reused keystream across files** | **No** — decisive test below |

The keystream test is the one that settles it. Two InstallShield language
`.ini` files should have near-identical plaintext, so under a reused keystream
`cipher_A XOR cipher_B` would collapse to mostly zeros and low entropy.
Measured over 2,048 bytes:

```
zero bytes in XOR:  10/2048  (0.5%)
entropy: A=7.91  B=7.90  A^B=7.91
```

Full entropy in the XOR means per-file keying. This is real encryption, not the
obfuscation InstallShield used in its older CAB format. Recovering it means
disassembling the decrypt routine in the bootstrapper's `.text` — no
disassembler was available for this analysis, and it is a substantial job.

*(The two `.ini` payloads do share their first two bytes, `4a e2`. That is a
per-entry constant ahead of the ciphertext, not evidence of a shared keystream
— the very next byte diverges.)*

---

## Three ways forward

**1. Run the installer's own extract flag.** InstallShield 2010 supports
`Setup.exe /extract_all:<folder>`, which unpacks the MSI and support files
*without installing anything*. This is by far the cheapest route to the
Dashboard binaries, and if any are .NET, the client side of the protocol falls
out almost for free.

It does mean executing the installer, so it is the maintainer's call, not something
to do unattended. Run it in a folder you don't mind, and check what appeared
before doing anything else.

**2. Skip it and capture packets instead** *(recommended)*. The firmware
already gave up the server side — the port, the transport, the XML format and
all 103 commands (see [`protocol-map.md`](protocol-map.md); an earlier "106"
conflated the table with loose strings). What's left is the
byte framing and one real status response, and a Wireshark session on
`tcp port 5000` answers both in about twenty minutes. The installer is a
*second* route to answers we can get more directly.

**3. Reverse the decryption routine.** Real work, needs a disassembler, and
only worth doing if both routes above fail.

---

## Reproducing this analysis

The measurements above came from throwaway scripts. The reusable part — the
container walk — is worth re-deriving from the layout table above if anyone
picks this up again. The firmware unpacker
([`tools/fwtool.py`](../tools/fwtool.py)) is the tool that *did* pay off, and
is committed.
