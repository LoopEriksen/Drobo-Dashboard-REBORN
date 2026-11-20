# Packet captures

Wireshark captures from Phase 1 were saved here on the PC that made them, one
file per Dashboard action, named `NN-short-description.pcapng`
(`docs/archive/phase-1-capture-playbook.md` was the checklist). Two older
tools, `tools/capture-nasd.ps1` and `tools/nasd_tap.py`, still save here by
default. The current one, `tools/capture-session.ps1`, saves to your TEMP
folder instead.

## Captures never go into the repository

Git ignores the capture files saved in this folder (`.pcapng`, `.pcap`,
`.etl`, `.jsonl`, `.bin`, `.xml`, `.raw`, `.json` and `.txt`), and this
README is the only file here that git tracks. Keep it that way: never force
a capture in with `git add -f`, and never copy one into a folder git does
not ignore. What goes into the repository is the finding, written up in
`docs/protocol-map.md`.

Why: **the Drobo protocol is unencrypted — confirmed on the wire** (no
transport crypto on either port; the command-channel login is the device
serial in clear; a captured rename write carried the password fields in
plain text). A capture carries the device serial, disk serials, addresses
and network names, and may contain your Drobo admin password, share
passwords and usernames in plain readable text. Once something is pushed
to GitHub it is effectively public forever, even if you delete it later.

## What you can post from a capture session

When a finding is reported, the same rule applies as in the tester guide
(`windows/installer/TESTING.md`) and `docs/testers-tasks.md`:

- **Safe to paste after reading:** the console output of `nasd_sweep.py` and
  `drobo_probe.py`. Read it first and take out any address, name or serial it
  shows.
- **Paste only with names removed:** the decoded command list that
  `CAPTURE DROBO COMMANDS (run me).bat` prints, and the output of
  `access_codes.py`. They name your shares and user accounts, and can show
  serials and addresses. The decoder replaces anything that looks like a
  credential with `[redacted]`, but names are left as they are.
- **Never post:** any capture file, or the JSON report `nasd_sweep.py` writes.
  They can hold your Drobo's serial, every drive serial, share and user names
  and your Drobo's address, and a capture can hold your password.

## Handling captures on your own PC

- Before capturing, change the Drobo's admin password to a throwaway value
  (`test1234`), and change it back afterwards. A password typed during the
  recording is then worthless.
- To see what a capture gives away, open it in Wireshark: **Edit ▸ Find
  Packet**, set it to search *Packet bytes* for *String*, and search for
  your password, your username, and your Wi-Fi/network names.
