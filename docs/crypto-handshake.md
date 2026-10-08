# The tier-2 crypto handshake

**Correction, 2026-07-26 — this no longer gates temperature or system info.**
This document was written from a single capture that showed a 4-message
binary exchange happen right before what looked like "the first privileged
command," and it concluded from that ordering that the exchange gated system
info (and therefore temperature). That conclusion was wrong. The real reason
`eCmdGetSysInfo` looked unreliable was a parsing bug in this project's own
code, not a crypto gate: its reply nests an **entity-escaped XML document**
inside `<ResultDetails>` instead of real XML children, so code looking for a
literal `<Temperature>` element found nothing and logged the command as
unanswered. See `sdk/drobo_nasd/sysinfo.py` and
`docs/command-surface.md`, which measured `eCmdGetSysInfo` answering 15/15
with nothing but the plain login, and found several more commands
(`eCmdGetPerformance`, `eCmdGetDroboAppsStatus`, `eCmdGetDemoModeInfo`,
`eCmdGetUserGroupList`, `eCmdGetDimming`) answering the same way, no crypto
exchange attempted.

**So what does this handshake actually gate?** Genuinely unknown — possibly
nothing this project has tried. Fan and PSU status (`eCmdGetFanInfo`,
`eCmdGetPowerInfo`) still never reply, with or without it, but they time out
silently rather than being refused, which fits "this chassis has no such
sensor" at least as well as "needs the crypto." Nobody has since repeated a
side-by-side test of the same command with and without this exchange, so
treat everything below as an accurate record of **what was seen on the
wire**, not as a settled claim about which commands (if any) require it.

---

What stands between a plain login and *whatever it actually gates* — see
`protocol-map.md` for how the exchange itself was found on the wire. This
document is about the handshake itself: its shape, its likely algorithm, and
where the key material would have to live.

**Status: characterized, not solved, and its purpose is now uncertain.** The
message flow and sizes are `OBSERVED`. The algorithm family is a
well-supported `FIRMWARE` finding. The actual key — the one thing that would
let us complete the exchange ourselves — was not recovered from either side.
See [Key material: not recovered](#key-material-not-recovered).

| Mark | Meaning |
|---|---|
| `FIRMWARE` | Read directly out of `nasd` (Drobo 5N firmware 4.2.1) or the installer. |
| `OBSERVED` | Seen on the wire in a packet capture against a live 5N. |
| `GUESS` | Inferred from the above. Treat as a lead, not a fact. |

---

## Message flow `OBSERVED`

Captured 2026-07-25 on TCP 5001, immediately after login and three successful
`eCmdGetConfig` calls, immediately before what this project took, at the
time, to be the first privileged command:

```
client  0x04  1 byte:    0x10
device  0x84  130 bytes: high entropy               (challenge)
client  0x84  178 bytes: base64 ASCII text           (response)
device  0x83  4 bytes:   00 00 00 00                 (accepted)
```

All four frames use the standard `DRINETTM` envelope documented in
`protocol-map.md` and implemented in `sdk/drobo_nasd/config_cmd.py`
(`SIGNATURE + type + version + flags + big-endian u32 length + payload`).
Nothing here is new framing — only the payload contents are being reasoned
about in this document.

In this one capture, everything sent after the `0x83` accept succeeded. But
the inference drawn from that at the time — that commands sent *without*
this exchange would time out or come back empty — has since been
contradicted for several commands: `docs/command-surface.md`'s 2026-07-26
sweep sent `eCmdGetSysInfo`, `eCmdGetPerformance`, `eCmdGetDroboAppsStatus`,
`eCmdGetDemoModeInfo`, `eCmdGetUserGroupList` and `eCmdGetDimming` with a
plain login and no crypto exchange at all, and all of them answered with
real data. So this exchange's relationship to "privilege" is no longer a
safe conclusion — it may gate something Dashboard does for its own reasons
(attestation, telemetry) rather than anything the device itself requires.
What's still solid: the login (`0x07`) already happened before this
exchange, using nothing but the device's own serial number, so whatever this
gates, it isn't identity.

---

## Size analysis `GUESS`, arithmetic shown

The task worth doing here is arithmetic, not assumption.

**Challenge: 130 bytes.**
A 1024-bit RSA modulus produces raw encrypt/decrypt/sign/verify output
exactly 128 bytes long — that's fixed by the modulus size, not by the padding
scheme. 130 = 128 + 2. A 2-byte prefix ahead of one raw RSA block is a clean
fit, and it rhymes with the outer `DRINETTM` envelope's own habit of a fixed
binary prefix ahead of a payload. `GUESS` on what the 2 bytes mean —
plausibly a sub-type or a short sequence/nonce-id, not itself cryptographic.

**Response: 178 bytes of base64 text.**
Valid base64 (no internal padding) always has a length that's a multiple of
4. 178 is not (178 = 4×44 + 2). Peeling off the same 2-byte prefix pattern as
the challenge leaves **176 characters of base64**, and 176 = 4×44 exactly —
a clean, fully-used base64 body with no partial group. Decoded: 44 groups ×
3 bytes = **132 raw bytes**, with *zero* padding characters needed, because
132 itself is a clean multiple of 3. That last part matters: it means
whoever built this payload didn't need to reason about padding at all, which
is the kind of accident that happens when the plaintext size was chosen
first and the encoding followed mechanically.

132 bytes is again suspiciously close to one 128-byte RSA block, this time
with a 4-byte trailer instead of a 2-byte prefix (128 + 4 = 132) — or the
prefix/trailer split could be arranged differently; the arithmetic only
pins down the total, not the internal boundary. Either way: **one RSA-1024
operation's worth of output, plus a handful of bookkeeping bytes, encoded as
base64** is the shape that requires no hand-waving to make the numbers work.

The alternative — treating all 178 bytes as pure base64 with standard
padding — doesn't factor as cleanly (178 isn't `%4`, and getting to 132
decoded bytes from a padded string requires a 180-character string, which is
2 bytes longer than what was observed). The prefix/trailer reading is the
one that needs no fudge factor, which is why it's presented as the leading
guess rather than the only one considered.

---

## Algorithm: RSA, most likely 1024-bit `FIRMWARE` + `GUESS`

Evidence, each independently checked against the unpacked firmware at
`vendor/extracted/rootfs/sbin/nasd` (1,534,216-byte stripped ARM ELF):

- **`UCrypto::ProcessData() - outData buffer must be multiples of 128`** —
  file offset `0x15587c`. `FIRMWARE`. States outright that this crypto class
  operates in 128-byte blocks — exactly the raw output size of a 1024-bit
  RSA operation, and not a natural block size for common symmetric ciphers
  (AES = 16, 3DES = 8).
- **`UCrypto::DecodeAndDecrypt() - bad data size`** — immediately adjacent,
  offset `0x155899`. `FIRMWARE`. Same class, a decrypt path with its own
  size validation.
- **`<RSAKeyValue>` / `Modulus` / `Exponent` / `</RSAKeyValue>` / `InverseQ`
  / `DSAKeyValue` / `X509Data` / `X509Certificate`** — two clusters, file
  offsets `0x15f0e8` and `0x1607b0`–`0x1607d6`. `FIRMWARE`. This is exactly
  the vocabulary of .NET's `RSA.ToXmlString()` / `FromXmlString()` format —
  `<RSAKeyValue>` wrapping `<Modulus>`, `<Exponent>`, and, only for a
  *private* key, `<D>`, `<P>`, `<Q>`, `<DP>`, `<DQ>`, `<InverseQ>`.
  `InverseQ` appearing alone (without the others, per the string table) is
  consistent with a private-key-capable XML (de)serializer being linked in
  as *general-purpose library code*, not with an actual private key sitting
  in the strings — see the caveat below.

**Important caveat, also `FIRMWARE`:** these strings sit inside a much
larger, generic networking/crypto library statically linked into `nasd` —
the same string table carries SMTP, POP, XMPP SASL, NTLM/Digest auth,
PuTTY's SSH2 key format (`---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----`), and
RSS (`<item>`, `guid`, `comments`). None of that is Drobo-specific — it's
off-the-shelf toolkit surface (shape strongly resembles /n software
IPWorks or similar). So the `<RSAKeyValue>` strings prove the *library*
can read and write RSA keys in this format; they are not, themselves,
a key. A real search for actual key bytes (DER `30 82 ...` sequences, or a
`-----BEGIN`/`-----END` PEM pair with real base64 between them) came back
empty — see below.

**Bit length is a `GUESS`.** 128-byte blocks means the modulus is 1024
bits; nothing pins that down harder than "the block-size string says so."
1024-bit RSA was still common in embedded/legacy code circa this firmware's
era (pre-2013 stacks routinely used it), so it's a plausible `GUESS`, not
just an assumption from block size alone.

**Padding scheme is unknown.** Whether this is raw RSA, PKCS#1 v1.5, or
OAEP was not determined. The size arithmetic above (128 bytes in, 128
bytes out) works for any of them, since padding overhead lives *inside*
the block for RSA, not outside it — the ciphertext is always exactly the
modulus size regardless of padding mode.

---

## What kind of exchange this is `GUESS`

There is no username or password anywhere in this exchange — the login step
that precedes it (`0x07`) is just the device serial, which the device hands
out to anyone on port 5000 (see `protocol-map.md`). That makes this look
less like *user* authentication and more like **software attestation**: the
device is checking "is this really Dashboard talking, or something that
merely knows how to log in" before handing over hardware telemetry.

The natural shape for that, given an RSA challenge–response with no
credential in sight: the device holds a **fixed public key** (baked in at
manufacture or firmware build time) and uses it to build the 130-byte
challenge; only a client holding the matching **private key** can produce a
valid 178-byte response. That would explain, cleanly, why an exhaustive
search of the firmware (below) turned up the XML *vocabulary* for RSA keys
but no actual key bytes — the device doesn't need to hold a secret to
*check* an RSA response if it only ever needs the public half, or if the
check is closer to "did decrypting with our stored key produce something
sane" than a full signature verification. This is the strongest available
lead for where to keep looking, not a confirmed mechanism.

---

## Key material: not recovered

### Device side — searched exhaustively, nothing found `FIRMWARE`

- Every file in the unpacked rootfs (1,117 files, `vendor/extracted/rootfs-listing.txt`)
  was checked for `.pem`, `.key`, `.crt`, `.der`, `.p12`, `.pfx` extensions.
  Only hit: `/etc/ssl/certs/ca-certificates.crt`, the standard Mozilla CA
  bundle used for outbound HTTPS (email-alert delivery, update checks) — not
  a Drobo-specific key.
- `/etc/nasd/` contains only Samba/AFP share templates — no key material.
- `/usr/share/`, `/mnt/` (the obvious places to look) are effectively empty
  in this firmware image — mount points populated only at runtime.
- Every file containing `-----BEGIN` was enumerated
  (`dropbearmulti`, `wget`, `openssl`, `libcrypto.so`, `libldap*.so`,
  `libhx509-samba4.so`, `nasd` itself, plus the CA bundle and OpenSSL's own
  `CA.pl`/`CA.sh` helper scripts). Every one of them is either a stock
  package binary that always contains the PEM template string, or (for
  `nasd`) the same generic `"-----BEGIN %s-----"`-shaped format string and a
  bare `CERTIFICATE` begin/end pair — again, template strings, not an
  embedded key.
- The three ESA-side firmware images (`fw-VXI2.bin`, `fw-RTPI.bin`,
  `fw-LXKI.bin` — the VxWorks/BeyondRAID side and the Linux kernel) were
  checked for the same markers (`RSAKeyValue`, `BEGIN`, `Modulus`,
  `DecodeAndDecrypt`, `UCrypto`). None hit. The crypto capability lives
  entirely in `nasd`, on the Linux side.

Conclusion: if a key exists in this firmware image, it is not present as
readable ASCII/PEM/XML anywhere in the 1,117-file rootfs, and not as any of
the string markers checked inside the three binary firmware images. Finding
it (if it's there as a raw DER byte sequence in `.rodata`, or generated at
runtime from something else entirely) would need ARM disassembly, and no
disassembler was available for this analysis.

### Client side — installer format is genuinely encrypted, not just unfamiliar `FIRMWARE`, `BLOCKED`

`vendor/Drobo-Dashboard-3.5.0.exe` is an InstallShield 2010 "Setup Player
V16" bootstrapper. It was **not run** — only its byte layout was analyzed.
This corroborates and slightly extends the prior analysis already on file at
`docs/original-dashboard-installer-notes.md`, which should be treated as the fuller writeup:

- No archive magic (`MSCF`/CAB, `PK\x03\x04`/ZIP, `7z`, `Rar!`) appears
  anywhere in the file. The formal PE image (4 sections) ends at file offset
  `0x117200`; everything from there to EOF (15,476,496 bytes) is an
  appended, unindexed sequence of named entries — filename, a small fixed
  header, then a payload — including one entry literally named
  `Drobo Dashboard.msi` (8,905,639 bytes, confirmed self-consistent against
  the next entry's start offset).
- That payload does **not** decompress as zlib, raw deflate, LZMA/XZ, or
  bzip2 (checked directly with Python's stdlib `zlib`/`lzma`/`bz2` against
  every candidate start offset in the entry's header region — zero hits).
- `docs/original-dashboard-installer-notes.md` already ruled out known InstallShield CAB
  obfuscation schemes (byte-rotate/XOR variants) and, decisively, a *reused*
  keystream across files (two files that should share near-identical
  plaintext produce a fully high-entropy XOR — 7.91 bits/byte — meaning each
  file is keyed independently). Their conclusion: **this is real per-file
  encryption**, not merely an unusual compression format, and unpacking it
  needs either running the installer's own `/extract_all` flag (execution,
  which this analysis did not do) or disassembling the bootstrapper's decrypt
  routine (needs a disassembler — not available for this analysis either).

Since this analysis did not run the installer and had no disassembler,
**the client side is blocked by tooling, not by a wrong approach.** If
Dashboard's own .NET assemblies do embed an `RSAKeyValue` (which is a strong hint given the whole codebase's .NET
Framework 2.0 dependency, called out in `original-dashboard-installer-notes.md`), the private
key referenced in "Where this leaves us" below is sitting inside that
encrypted MSI, unreached.

No key material of any kind was recovered from either side.

---

## Where this leaves us

**Solved:** the shape of the exchange (4 messages, RSA-family, ~1024-bit
block size, base64 framing on the response), and the arithmetic that makes
130 and 178 make sense as one RSA block plus a few bookkeeping bytes.

**Not solved:** the actual key, on either end. The device side was searched
exhaustively at the string level and the search came back empty — the next
step there is ARM disassembly of `nasd` around the `UCrypto`/`DNASAuthenticate`
call sites, which needs a disassembler (Ghidra is free and handles ARM well;
none was available). The client side is a known, fully-mapped, but
**encrypted** container — the next step there is either running the
installer's documented `/extract_all` flag (a decision for the maintainer,
since it means executing vendor code) or reversing the bootstrapper's
decrypt routine, which again needs a disassembler.

Both paths converge on the same missing tool. **The single most promising
next step is getting a disassembler (Ghidra, free) into the toolchain** and
pointing it at `nasd`'s `UCrypto` class first — it's the smaller binary
(1.5 MB vs. the installer's 16.6 MB payload) and every string reference
needed to find the right function is already located by file offset above.
