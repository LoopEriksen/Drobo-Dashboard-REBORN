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
`docs/protocol-map.md`. When a finding is reported, send the decoded output,
never the capture file.

Why: **the Drobo protocol is unencrypted — confirmed on the wire** (no
transport crypto on either port; the command-channel login is the device
serial in clear; a captured rename write carried the password fields in
plain text). A capture carries the device serial, disk serials, addresses
and network names, and may contain your Drobo admin password, share
passwords and usernames in plain readable text. Once something is pushed
to GitHub it is effectively public forever, even if you delete it later.

## Handling captures on your own PC

- Before capturing, change the Drobo's admin password to a throwaway value
  (`test1234`), and change it back afterwards. A password typed during the
  recording is then worthless.
- To see what a capture gives away, open it in Wireshark: **Edit ▸ Find
  Packet**, set it to search *Packet bytes* for *String*, and search for
  your password, your username, and your Wi-Fi/network names.
