# Phase 1: Protocol capture playbook

> **Archived.** Its advice to commit captures is out of date: captures never
> go into the repository (see `docs/captures/README.md`).

The goal of this phase is to end up with `docs/protocol-map.md` filled in, plus
a folder of packet captures committed to this repo. Nothing else gets built
until that exists.

**Target: Drobo 5N** (confirmed 2026-07-23). It's a network model, so this is
plain Ethernet capture — follow Steps 0 through 4 and ignore Step 5.

Read `docs/archive/reality-check.md` §0 first if you haven't — the short version is that
the still-working Drobo Dashboard is now the only surviving copy of the spec,
so capture it while it still runs.

---

## Step 0 — Preserve what you have (do this today, 15 minutes)

1. **Do not uninstall Drobo Dashboard.** If it's currently working, leave it.
2. Copy the installer if you still have it, plus the whole installed folder
   (usually `C:\Program Files\Drobo\Drobo Dashboard\`), somewhere safe and off
   this machine. The binaries are a reference we may need to inspect later.
3. Note the exact versions, and put them in `protocol-map.md`:
   - Dashboard version — Help ▸ About. (Final Windows release was 3.5.0.)
   - Drobo model and firmware version — shown on Dashboard's status page.
4. If the firmware reads **4.3.0 or 4.3.1**, note it. Those releases were pulled
   by Drobo for reported problems; 4.2.2 is the community's last-known-good.
   Don't act on this yet, just record it.

---

## Step 1 — Find the Drobo on the network

From this repo:

```bash
py tools/drobo_probe.py mdns
```

This asks every device on the network to list the services it advertises. It's
the same mechanism Dashboard uses to find a Drobo, so the Drobo should answer.
Look for anything with "drobo" in the name and note the IP address.

Then:

```bash
py tools/drobo_probe.py scan
```

Sweeps your subnet for hosts listening on ports a NAS typically uses. Once you
know the Drobo's IP, get the full picture:

```bash
py tools/drobo_probe.py report 10.0.0.50 -o docs/captures/findings.json
```

(substitute the real IP). That runs a full 1–10000 port sweep and records what
every open port says when you connect to it.

**If nothing at all shows up** — the 5N is definitely on the network, so
something is in the way. In order of likelihood:

1. **You're not on the same network as the Drobo.** Check the IP the probe
   reports for itself. If it starts `172.20.10.`, you're on an iPhone Personal
   Hotspot, not your home LAN.
2. **Windows Firewall is dropping the replies.** Try again with the private
   network profile temporarily off, and turn it back on afterwards.
3. **The PC and the Drobo are on different subnets or VLANs** (common with a
   guest Wi-Fi network). mDNS does not cross subnets.

---

## Step 2 — Set up Wireshark

1. Install Wireshark (the default components are fine; **do** include Npcap).
2. Start a capture on the network adapter your PC uses.
3. Set the capture filter to just the Drobo, so the file stays readable:
   ```
   host 10.0.0.50
   ```
4. Confirm you see traffic when you open Dashboard.

If the Drobo is on Wi-Fi and the PC is on Ethernet (or vice versa), you'll still
see everything — the traffic is between the PC and the Drobo, and you're
capturing at the PC.

---

## Step 3 — Capture one feature at a time

This is the important discipline. **One action per capture file.** A single
30-minute capture of you clicking everything is nearly useless; twenty small
labelled captures are a specification.

For each item on the checklist below:

1. Start a fresh Wireshark capture.
2. Do exactly that one thing in Drobo Dashboard. Nothing else.
3. Stop the capture and save it as
   `docs/captures/NN-short-description.pcapng`.
4. In Wireshark, right-click a packet in the conversation ▸ **Follow ▸ TCP
   Stream** to see the request and response as readable text. If it looks like
   XML or text, paste the interesting part into `protocol-map.md`.

### Capture checklist

> **This list is the original Phase-1 plan, not the live tracker.** It has not
> been ticked as work landed, so read it as "what a full sweep would cover",
> not as "what is outstanding". The tracker that *is* kept current is
> `tools/capture-session.ps1`, which retires a step the moment it is settled
> and records why in a comment beside it. Per its own retirement notes:
> share **create and delete** were captured 2026-08-25 (items 10 and 11 here);
> spin-down was settled on hardware 2026-08-16; of the permission levels only
> code `1` (read/write) is confirmed — `0` is deliberately hedged, and `2` has
> never been observed.
> Check that script before re-recording anything on this list.

Work down this list. Tick items off as you go.

- [ ] `01-dashboard-launch` — start Dashboard with the Drobo already on
- [ ] `02-discovery` — Dashboard finding the Drobo (turn the Drobo on with
      Dashboard already running, if you can)
- [ ] `03-idle-60s` — Dashboard open, doing nothing, for 60 seconds. This shows
      the polling interval and the heartbeat/status message, which is the single
      most valuable capture on the list
- [ ] `04-status-page` — click through to the main status/capacity view
- [ ] `05-drive-details` — open the per-drive / disk pack detail view
- [ ] `06-tools-page` — open the tools / settings pages (don't change anything)
- [ ] `07-rename` — rename the Drobo, if you're comfortable doing so
- [ ] `08-led-blink` — trigger any "identify / blink lights" function
- [ ] `09-shares-list` — view the list of network shares (NAS models)
- [ ] `10-share-create` — create a test share (NAS models)
- [ ] `11-share-delete` — delete that test share
- [ ] `12-users-list` — view users/permissions (NAS models)
- [ ] `13-droboapps-list` — open the DroboApps page (NAS models)
- [ ] `14-alerts` — any alert/notification settings page
- [ ] `15-diagnostics` — generate a diagnostic file, if the option exists
- [ ] `16-drive-pull` — **only if you're confident and backed up**: pull one
      drive from a healthy, redundant pack and capture the alert traffic. This
      is how you learn the failure-state messages. Re-seat it afterwards.
      Skip this if there's any doubt.

Also capture, separately:

- [ ] `20-smb-mount` — mapping a network drive to a share in Windows Explorer
- [ ] `21-file-copy` — copying a small file to the Drobo

### Two ground rules

- **Don't change settings you don't understand**, and don't touch anything
  related to the disk pack, formatting, or firmware. Reading is safe; writing is
  not, and this hardware is irreplaceable.
- **Assume the captures contain your password.** If the protocol is
  unencrypted — and it very likely is — your Drobo credentials are sitting in
  those files in plain text. Change the Drobo's admin password to a throwaway
  value before capturing, and never commit the files. See the note in
  `docs/captures/README.md`.

---

## Step 4 — Write it down as you go

Fill in `docs/protocol-map.md` while it's fresh. For each Dashboard action,
record: which port, what was sent, what came back, and what it means. Even
"port 5000, sends `<something>`, gets back 400 bytes of binary I don't
understand yet" is worth writing — it's a starting point for next time.

Things to try to pin down, in priority order:

1. **The status message.** What Dashboard polls every few seconds, and how to
   read capacity, drive health, and temperature out of it. This alone unlocks
   the first working version of the agent and the Windows app.
2. **How a session starts** — is there a login, a handshake, a magic first
   packet?
3. **The command format** — how a request is framed (length prefix? XML?
   fixed-size struct?) and how responses are matched to requests.
4. **Error responses** — what a rejected or failed command looks like.
5. Everything else.

---

## Step 5 — Direct-attached (USB / Thunderbolt) models — NOT APPLICABLE

> **Skip this.** The target is a Drobo 5N, which is a network model. Kept only
> in case a USB-attached Drobo joins the project later.

Different technique, same goal.

- Wireshark can capture USB on Windows via **USBPcap**, which is an optional
  component in the Wireshark installer. Re-run the installer and enable it if
  you skipped it.
- Capture on the USBPcap interface, then run the same checklist from Step 3.
- The traffic will be SCSI commands rather than network packets. The prior art
  is the old open-source `drobo-utils` project, which drove first-generation
  Drobos this way — worth finding and reading before you start from scratch,
  since someone has already decoded part of this.
- Realistic expectation: this path is harder than the network path, and the iOS
  app in this scenario can't talk to the Drobo at all. It would talk to the
  agent on the PC, which talks to the Drobo over USB.

---

## When Phase 1 is done

You're done when you can answer: *"To read drive health from this Drobo, connect
to X, send Y, and parse Z out of the response."* At that point the agent in
`docs/archive/reality-check.md` §4 becomes a couple of days of work, and the project
stops being a research problem and starts being a normal software project.
