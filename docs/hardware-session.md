# The hardware session

> This is the maintainer's own checklist, written for the maintainer's unit.
> The jobs meant for testers, with read-only steps, are in
> [`testers-tasks.md`](testers-tasks.md).

Everything still open that only a real Drobo 5N can settle, as one sitting,
in order. Written 2026-09-21; revised 2026-09-26, when it turned out the
original Dashboard's Blink Lights press had been recorded back on 2026-07-25,
so a last part, F, sends that value from this app. It takes about forty-five
minutes, most of it in two short recordings of the original Drobo Dashboard
3.5.0.

These items used to be scattered across the roadmap and the comments in the
capture script, each saying "needs a run on the hardware". This page is the
run.

## What it settles

| Part | What it settles |
|---|---|
| A | **Creating a share** from this app, for the first time since the fix that decides an unacknowledged write by reading it back. Also the first time a real 5N is read under the whole-frame time limit of 2026-09-19. That limit covers the status channel and the settings reads and writes only; temperature, the event log, Identify, DroboApps and DroboSync still read without it. If the app shows your Drobo normally, the status half is done |
| A, C | **One share permission code.** `tools/access_codes.py` reads every share's permission numbers before and after the original Dashboard sets one, so the word Dashboard shows is pinned to the number the Drobo stores. Code 1 is read/write; 0 and 2 are still unknown |
| B | **Turning Blink Lights off** in the original Dashboard. The on press is already recorded; the off press never has been, and this app's Stop sends 0 as a guess. Also the **event log's reply format**, and **creating a Drobo user**, which has never been seen on the wire and is now a wanted feature |
| D | **Deleting a Drobo user** |
| E | **Deleting a share** from this app, which has never been sent to a device at all |
| F | **Blinking the lights with 900**, the value the original Dashboard was recorded sending on 2026-07-25. This project has only ever sent nothing or 15, and on this unit neither lit anything. If the lights blink at 900, the unit is seconds, and this app's default can change from 15 to 900 |

Nothing here is guessed at the Drobo. Every write in parts B and D is the
original Dashboard doing its normal job. The writes in A and E are this app's
share create and delete, built from a recording of Dashboard doing the same.
The one in F is an Identify at 900, the value Dashboard itself was recorded
sending.

## Before you start

- Do this on an up-to-date `main`, after the work from 2026-09-19 to 09-21 has
  landed, so the agent is the current code. The app is whichever built copy is
  newest, so check it: **SETTINGS** > **THE DROBO** > **VERSIONS** should show
  this app as 1.1.0. If it does not, run
  `dotnet build windows\DroboDashboardReborn -c Release` first.
- This PC on the same network as the Drobo, and the Drobo powered on with its
  lights steady.
- Drobo Dashboard 3.5.0 installed and **closed**.
- The agent set up for your real Drobo, not the simulator. If any message in
  the app ends in `(simulated)` or `[simulated Drobo]`, stop: you are talking
  to the pretend one.
- Paper and a pen. Some of what matters is what you see on screen, which the
  recording cannot show.
- Optional, and it saves time later: open Dashboard 3.5.0 once now, find where
  it manages the Drobo's **users** (nobody has recorded where that is), then
  close it again. The first recording is seven minutes, and hunting for a
  screen eats into them.

Two names are used throughout. **ZZDELETEME** is a throwaway share this app
creates in part A and deletes in part E. **zzdeleteme** is a throwaway Drobo
user Dashboard creates in part B and deletes in part D.

To run a `py tools\...` command below: open the `Drobo Dashboard REBORN`
folder in File Explorer, click the address bar, type `cmd`, press Enter, and
type the command into the window that opens. Leave that window open; parts A,
C and F all use it.

## A. Create the throwaway share, and take the "before" reading

1. Double-click **`START DROBO DASHBOARD.bat`** in the repository folder. A
   console window opens (that is the agent; it has to stay open while the app
   is in use) and then the app.
2. Check the **STATUS** tab shows your Drobo.
3. Go to the **SHARES** tab and press **New share…** (top right of the share
   list).
4. In the **New share** window, type `ZZDELETEME` under **SHARE NAME** and
   press **Create**.
5. Note the colour and the exact words of the result, and copy them down:
   - **Green** (on the Shares tab, above the list): done. That includes the
     case where it says the Drobo *did not answer to say it accepted this* but
     reading it back shows the change is there -- that is the case this run is
     for, and it counts as done.
   - **Amber** (on the Shares tab): unsettled -- nothing can say whether it was
     created. **Do not press Create again.** Look first: the share list under
     the message is re-read straight after the write. If ZZDELETEME is in it,
     carry on as if it were green (and note that it was amber). If it is not
     there after a minute, stop here and send what it said.
   - **Red** (inside the New share window, which stays open): failed; nothing
     was created. Copy the message, press Cancel, and skip every step below
     that mentions ZZDELETEME (B5 and E). The rest is still worth doing.
6. In the command window: `py tools\access_codes.py before`
   - If it says `No Drobo address. Pass --host <address>.` or
     `Could not reach the Drobo at ...` (the router may have moved it), find the address
     on the app's **SETTINGS** tab, **THE DROBO**, **NETWORK CONFIGURATION**
     (press Refresh; it is the **IP address** line), and run
     `py tools\access_codes.py before --host <that address>`.
   - It prints your shares and each user's permission number. Ignore its own
     "Now, in Drobo Dashboard" instructions; part B below replaces them.
7. Close the app, then close the agent's console window. Their own traffic
   would otherwise land in the recording.

## B. First recording: Blink Lights, the event log, and a new user

1. Double-click **`CAPTURE DROBO COMMANDS (run me).bat`** in the repository
   folder. Windows asks for administrator rights; approve it (the recorder,
   `pktmon`, cannot run without them).
2. It checks the Drobo answers, then prints the steps. Read them, then press
   **Enter** to start. It records for up to **seven minutes**, and **Enter**
   stops it early once you are done.
3. Work through these in Drobo Dashboard 3.5.0, in order:
   1. **Open Dashboard and let it connect.** Wait until it shows your Drobo.
   2. **Tools page -> Blink Lights.** It is **not** called Identify: it sits
      next to **Rename**, with a small sun/rays icon. Press it once and watch
      the front of the Drobo; press it again to turn it off. Write down what
      the lights did after each press, including "nothing". The first press
      is already on record from 2026-07-25; the second, the off press, never
      has been.
   3. **Open the page that shows the Drobo's event log.** Let it load
      completely and scroll it once. Write down roughly what it showed.
   4. **Create a user called `zzdeleteme`.** Give it a password you use
      nowhere else -- it will be in the recording in plain text. Apply or save,
      and let Dashboard finish.
   5. **Give `zzdeleteme` a permission on ZZDELETEME that is not read/write**
      -- read-only, or no access -- and apply it. Write down the **exact
      words** Dashboard shows for the level you chose. ZZDELETEME only.
4. Press **Enter** in the capture window to stop. It decodes the recording and
   prints every command Dashboard sent. **Do not do its step 6 yet** -- that is
   part D.
5. Copy everything the capture window printed into a text file before closing
   it, and note the folder it says the capture was kept in.

## C. Close Dashboard, and take the "after" reading

1. Close Drobo Dashboard 3.5.0.
2. In the command window: `py tools\access_codes.py after` (add the same
   `--host <address>` as before if you needed it). It should say that
   `zzdeleteme` was **ADDED** to ZZDELETEME with a code: that number and the
   words you wrote down in B5 are the measurement.

This has to happen before part D. Delete the user first and it is gone before
the "after" reading, so the comparison never sees it.

## D. Second recording: delete the user

1. Double-click **`CAPTURE DROBO COMMANDS (run me).bat`** again, approve the
   administrator prompt, and press **Enter** to start.
2. It shows the same step list. This time do only its steps **1** and **6**:
   open Dashboard, let it connect, and **delete the user `zzdeleteme`** --
   that user and nothing else.
3. Press **Enter** to stop. Copy what it printed into a second text file.
4. Close Dashboard.

## E. Delete the throwaway share from this app

1. Double-click **`START DROBO DASHBOARD.bat`** again.
2. **SHARES** tab: on the **ZZDELETEME** row, press **Delete…**.
3. The **Delete share** window warns that everything in the share goes with
   it (ZZDELETEME is empty). Under **TYPE THE SHARE NAME TO CONFIRM**, type
   `ZZDELETEME` exactly -- capitals count -- then click **Delete share**
   (Enter does not press it, on purpose).
4. Note the colour and the words, exactly as in part A: green on the Shares
   tab is done, amber is unsettled (do not press Delete again; check whether
   ZZDELETEME is still in the list, and say which), red inside the window is a
   failure.

## F. Blink the lights at 900, from this app

This part asks only one thing: does this project's own Identify make the lights
blink when it sends what the original Dashboard sends? When Blink Lights was
recorded on 2026-07-25, Dashboard sent an interval of **900**. This app has
only ever sent nothing or **15**, its own guess, and on this unit neither lit
anything. Leave the app open from part E.

1. If the lights are still blinking from part B, wait until they stop, so
   that anything you see now is this app's doing.
2. Find the Drobo's address: **SETTINGS** tab, **THE DROBO**, **NETWORK
   CONFIGURATION** (press Refresh; it is the **IP address** line).
3. In the command window, type these three lines, one at a time. Watch the
   front of the Drobo as you press Enter on the second:

   ```
   cd sdk
   py -m drobo_nasd.identify <that address> --interval 900
   cd ..
   ```

   The second line sends exactly what the agent sends for
   `POST /api/identify?interval=900`.
4. Write down what the lights did in the first minute, including "nothing",
   and what the command printed.
   - If it says the Drobo *did not accept Identify just now*, wait half a
     minute and run the second line once more. The device has been seen to
     turn down an Identify sent soon after another one.
   - If the lights blink: go to the app's **TOOLS** tab and press **Stop**
     under **IDENTIFY**, then write down whether they stop. Stop sends 0,
     which is this project's guess at the off switch. If they keep blinking,
     leave them; the original Dashboard's own warning says they blink for 15
     minutes.
5. Change nothing in the app because of what you saw. If 900 blinks, the
   default changes in the code after you report back.

## What to send back

Keep these out of public issues, because they name your shares:

- **A:** the colour and exact words of the create, and the `before` output.
- **B:** the first capture window's output, plus your notes: which steps you
  managed and which you skipped, what the lights did, what the event log page
  showed, and the exact permission words from B5.
- **C:** the `after` output.
- **D:** the second capture window's output.
- **E:** the colour and exact words of the delete.
- **F:** what the lights did at 900, what the command printed, and, if you
  pressed Stop, whether they stopped.

Send the decoded output, not the capture files. Those stay on this PC.

## Safety

- **ZZDELETEME and zzdeleteme only.** Never touch **ZZTESTZZ2** if you see it
  -- it is left on the maintainer's unit on purpose, pending the maintainer's
  decision (it will not exist on yours) -- and never a share with real files
  in it.
- **Do not format, repair, or update firmware**, and do not change anything
  about the disk pack. If a Dashboard screen offers one of those, do not click
  it; if you are not sure, stop.
- **Never retry an amber result.** It may already have happened; look first.
- **Captures never go into the repository.** They are saved in your TEMP
  folder (`drobo-session-<date>-<time>`), and on this unencrypted protocol they
  can hold the Drobo password -- and the throwaway user's -- in plain text. Keep
  them on this PC or in a private folder outside the repository, never in a
  folder git tracks. See [`captures/README.md`](captures/README.md), which
  also suggests using a throwaway admin password while recording.
- If anything in the app says `(simulated)`, it is not talking to your Drobo.
  Stop and check the agent's settings before going on.
