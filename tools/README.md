# Tools

Helper scripts. Most were used to work out how a Drobo talks. The
double-click `.bat` files in the main folder run three of them:
`find-app-exe.ps1` and `start-with-mock.ps1` (first table below) and
`capture-session.ps1`, which in turn uses `extract_commands.py` and
`nasd_dissect.py`. Do not move or rename any of those five. **You do not need
to run any of them yourself to use the software**: the `.bat` files in the
main folder cover that.

How to read the tables:

- **Talks to a real Drobo?** says whether the script reaches a Drobo on your
  network. Run those only when a task asks you to. No script here changes the
  Drobo's settings or touches the data on its disks; where a recording needs a
  change, you make it yourself in the original Dashboard.
- **Still needed?** marks the finished one-off scripts. They are kept as a
  record of how something was found out; nothing asks for them to be run
  again.

Run the Python tools from the repository's top folder, for example
`py tools/drobo_probe.py mdns`.

## Used by the double-click launchers (do not move or rename)

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`find-app-exe.ps1`](find-app-exe.ps1) | Finds the newest copy of the Windows app on this PC, built or installed | No | Yes |
| [`start-with-mock.ps1`](start-with-mock.ps1) | Starts a simulated Drobo, opens the app against it, and stops the simulator when the app closes. Run it through `START WITH SIMULATED DROBO (testing).bat` | No Drobo needed, but the app still looks for Drobos on your network when it starts | Yes |

## Recording what the original Drobo Dashboard sends

These need the original Drobo Dashboard 3.5.0 on a PC on the same network as
the Drobo. The `.ps1` scripts usually need an administrator window. A recording can
contain the Drobo password in plain text: **never commit one**.

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`capture-session.ps1`](capture-session.ps1) | Records one session of you using the original Dashboard, then prints every command it sent. Run it through `CAPTURE DROBO COMMANDS (run me).bat`, which runs it by name: do not move or rename it. Saves the recording in your TEMP folder, outside the repository | Yes: checks the Drobo answers and searches the network for it, then records | Yes, when a capture is asked for |
| [`capture-identify.ps1`](capture-identify.ps1) | Recorded the Dashboard's Identify button. That button is called "Blink Lights", and `capture-session.ps1` now covers it | No: it only records | No (replaced) |
| [`capture-create-share.ps1`](capture-create-share.ps1) | Recorded the Dashboard creating and deleting a share. Done 2026-08-25 | Yes: searches the network for it, then records | No (finished) |
| [`capture-nasd.ps1`](capture-nasd.ps1) | The first recording script, from before the protocol was worked out. Saves into `docs/captures/` | Yes: connects to the Drobo's status port once to check the address, then records | No (finished) |
| [`nasd_tap.py`](nasd_tap.py) | Sits between the Dashboard and the Drobo and logs everything both say. No administrator window needed. Saves into `docs/captures/` | Yes: passes the Dashboard's traffic on to the Drobo | No (the protocol it was built to learn is solved) |

Git ignores the recordings saved in `docs/captures/`. Do not force one in.

## Reading a recording (on this PC)

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`extract_commands.py`](extract_commands.py) | Lists every command a recording shows the Dashboard sending, with its parameters. Hides anything that looks like a password in what it prints | No | Yes (`capture-session.ps1` runs it, so do not move or rename it) |
| [`nasd_dissect.py`](nasd_dissect.py) | Works out how the messages in a recording are framed. It does not hide passwords | No | Yes (`extract_commands.py` uses it, so do not move or rename it) |

## Probing and sweeping a real Drobo

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`drobo_probe.py`](drobo_probe.py) | Finds Drobos on the network and lists which ports they answer on | Yes | Yes |
| [`access_codes.py`](access_codes.py) | Reads each share's permission numbers, so you can see which number changes when you change a permission in the original Dashboard | Yes | Yes (an open task for testers) |
| [`nasd_sweep.py`](nasd_sweep.py) | Sends a fixed list of safe questions and records which ones the Drobo answers. Ran 2026-07-26 | Yes | Yes (the two sweeps below reuse its code) |
| [`sweep_remainder.py`](sweep_remainder.py) | Second sweep: seven commands the first one did not try. Ran 2026-07-27 | Yes | No (finished) |
| [`sweep_last_six.py`](sweep_last_six.py) | Third sweep: the last six commands, one at a time. One (Identify) is meant to flash the front lights; another makes the Drobo try to check for firmware updates. Ran 2026-07-27 | Yes | No (finished) |
| [`nasd_probe.py`](nasd_probe.py) | The earliest experiment at talking to a Drobo, from before the protocol was worked out | Yes | No (replaced by `sdk/`) |

## Reading Drobo's own files (on this PC)

These read copies of Drobo's firmware or of the original Dashboard program that
you keep on your own PC. They are never part of the repository (git ignores a
`vendor/` folder for them).

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`fwtool.py`](fwtool.py) | Unpacks a Drobo firmware file (`.tdf`) so the files inside can be read | No | Kept for research |
| [`fw_params.py`](fw_params.py) | Searches the firmware for the names of command parameters | No | Kept for research |
| [`dashboard_strings.py`](dashboard_strings.py) | Pulls protocol words out of the original Dashboard's program files | No | Kept for research |

## Offline tests

No Drobo needed. They run from any folder, for example
`py tools/test_nasd_sweep.py` from the repository's top folder.

| File | What it does | Talks to a real Drobo? | Still needed? |
|---|---|---|---|
| [`test_extract_commands.py`](test_extract_commands.py) | Checks `extract_commands.py` against a recording it builds itself | No | Yes |
| [`test_fw_params.py`](test_fw_params.py) | Checks `fw_params.py`. Skips one check unless the firmware is on this PC | No | Yes |
| [`test_nasd_dissect.py`](test_nasd_dissect.py) | Checks `nasd_dissect.py`. Prints `ok:` lines rather than `PASS` | No | Yes |
| [`test_nasd_sweep.py`](test_nasd_sweep.py) | Checks `nasd_sweep.py`'s safety list and password hiding against a pretend Drobo | No | Yes |

## Why this folder has no subfolders

Several tools load each other by sitting side by side, and find the rest of
the repository by looking one folder up. Moving them into subfolders would
break them without an error message, and could make the recording tools save
where git does not ignore their files.
