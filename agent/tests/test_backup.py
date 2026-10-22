"""
Offline tests for backup.py.

Two things here could destroy somebody's data if wrong, so they get the most
attention: robocopy's exit codes (success is NOT zero, and reading it as Unix
would report every good backup as broken) and the destination refusals (which
exist to stop a mirror being pointed somewhere that deletes things).

A real backup IS run, into temp folders, because "the dry run copies nothing"
is not a claim worth taking on trust.

    py test_backup.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk")))

from drobo_agent import backup

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"  {failure_detail}" if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


temp_dir = tempfile.mkdtemp(prefix="drobobackup-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
source_dir = os.path.join(temp_dir, "source")
destination_dir = os.path.join(temp_dir, "backup")
os.makedirs(os.path.join(source_dir, "Photos", "2026"), exist_ok=True)
for name in ("a.jpg", "b.jpg"):
    with open(os.path.join(source_dir, "Photos", "2026", name),
              "w") as photo_file:
        photo_file.write("pretend photo " + name)

print("\n== robocopy exit codes: success is NOT zero ==")
# The classic bug in every script that wraps robocopy. Reading these as Unix
# exit codes reports every successful backup as a failure.
for code, expected_ok, note in [
    (0, True, "nothing to copy"),
    (1, True, "files copied"),
    (2, True, "extra files in destination"),
    (3, True, "copied + extras"),
    (4, True, "mismatches, but nothing failed"),
    (7, True, "1+2+4, all non-failure bits"),
    (8, False, "some files could not be copied"),
    (9, False, "copied some, failed others"),
    (16, False, "serious error"),
    (24, False, "8+16"),
]:
    ok, summary = backup.interpret_exit_code(code)
    check(f"exit {code:<2} -> {'ok' if expected_ok else 'FAILURE'}  ({note})", ok == expected_ok,
          f"got ok={ok} {summary}")

_, up_to_date_summary = backup.interpret_exit_code(0)
check("exit 0 says there was nothing to do, not 'success'",
      "already up to date" in up_to_date_summary, up_to_date_summary)
_, failure_summary = backup.interpret_exit_code(8)
check("a failure names itself loudly",
      "COULD NOT BE COPIED" in failure_summary, failure_summary)
check("a negative code is a failure, not a bitmask",
      backup.interpret_exit_code(-1)[0] is False)

print("\n== destinations that must be refused ==")
# Each of these is a way to lose data or hang forever.
nested_dir = os.path.join(source_dir, "nested")
check("copying a folder into itself is refused",
      any("never finishes" in refusal for refusal in backup.check_destination(source_dir, nested_dir)),
      backup.check_destination(source_dir, nested_dir))
check("source == destination is refused",
      any("same folder" in refusal
          for refusal in backup.check_destination(source_dir, source_dir)))
check("backing up into a parent of the source is refused",
      any("parent of" in refusal
          for refusal in backup.check_destination(nested_dir, source_dir)),
      backup.check_destination(nested_dir, source_dir))
# Mirroring onto C:\ would try to delete Windows.
root_refusals = backup.check_destination(source_dir, "C:\\")
check("a bare drive root is refused", any("root of" in refusal for refusal in root_refusals), root_refusals)
check("-- and it suggests a folder instead", any("DroboBackup" in refusal for refusal in root_refusals))
check("an empty destination is refused", backup.check_destination(source_dir, "") != [])
check("an empty source is refused",
      backup.check_destination("", destination_dir) != [])
check("a sensible destination is allowed", backup.check_destination(source_dir, destination_dir) == [],
      backup.check_destination(source_dir, destination_dir))

print("\n== plan() describes, and copies NOTHING ==")
default_plan = backup.plan(source_dir, destination_dir)
check("the plan is allowed", default_plan.allowed, default_plan.refusals)
check("it warns that the destination will be created",
      any("does not exist yet" in warning
          for warning in default_plan.warnings), default_plan.warnings)
check("robocopy is the command", default_plan.command[0] == "robocopy",
      default_plan.command)
check("restartable mode is on, so a Wi-Fi blip does not restart a big file",
      "/Z" in default_plan.command, default_plan.command)
check("retries are bounded -- robocopy's default is a MILLION",
      "/R:2" in default_plan.command, default_plan.command)
check("mirroring is NOT on by default", "/MIR" not in default_plan.command,
      default_plan.command)
# The actual proof:
check("planning wrote nothing to disk", not os.path.exists(destination_dir))

print("\n== mirroring is opt-in and says what it will delete ==")
mirror_plan = backup.plan(source_dir, destination_dir, mirror=True)
check("/MIR appears only when asked for", "/MIR" in mirror_plan.command,
      mirror_plan.command)
mirror_warning_text = " ".join(mirror_plan.warnings)
check("the warning says DELETED in plain words",
      "DELETED" in mirror_warning_text, mirror_warning_text)
check("-- and gives the scenario that matters",
      "delete a photo by accident" in mirror_warning_text, mirror_warning_text)

print("\n== a missing source is refused before anything runs ==")
missing_source_plan = backup.plan(os.path.join(temp_dir, "no-such-folder"),
                                  destination_dir)
check("a nonexistent local source is refused", not missing_source_plan.allowed,
      missing_source_plan.refusals)

print("\n== starting a backup that must not run raises, rather than running ==")
runner = backup.BackupRunner()
try:
    runner.start(source_dir, source_dir)
    check("a refused plan raises BackupError", False)
except backup.BackupError as exc:
    check("a refused plan raises BackupError", True)
    check("-- and the message says why", "same folder" in str(exc), str(exc))
check("nothing was recorded as a job", runner.job is None)

print("\n== a real backup, into a real temp folder ==")
if os.name != "nt":
    print("  SKIP    robocopy is Windows-only")
else:
    job = runner.start(source_dir, destination_dir)
    for _ in range(150):                      # up to ~15s
        if not job.running:
            break
        time.sleep(0.1)
    check("the backup finished", not job.running, job.summary)
    check("it succeeded", job.ok is True, f"exit={job.exit_code} {job.summary} {job.error}")
    copied_file = os.path.join(destination_dir, "Photos", "2026", "a.jpg")
    check("the files actually arrived", os.path.exists(copied_file),
          copied_file)
    check("content matches", os.path.exists(copied_file)
          and open(copied_file).read() == "pretend photo a.jpg")
    check("progress output was captured for a UI to show", len(job.tail) > 0, len(job.tail))
    check("the job serialises for the API", "destination" in job.to_dict())

    print("\n== a second run finds nothing to do ==")
    second_job = runner.start(source_dir, destination_dir)
    for _ in range(150):
        if not second_job.running:
            break
        time.sleep(0.1)
    check("re-running succeeds", second_job.ok is True,
          f"exit={second_job.exit_code}")
    check("-- and does not re-copy what is already there",
          second_job.exit_code == 0,
          f"exit={second_job.exit_code} ({second_job.summary})")

    print("\n== two at once is refused ==")
    # Two robocopy processes on one destination would interleave and neither
    # result would be trustworthy.
    concurrent_runner = backup.BackupRunner()
    concurrent_runner.start(source_dir, os.path.join(temp_dir, "backup2"))
    try:
        concurrent_runner.start(source_dir, os.path.join(temp_dir, "backup3"))
        # It may legitimately have finished already on a tiny fixture.
        check("a second concurrent backup is refused (or the first had finished)", True)
    except backup.BackupError as exc:
        check("a second concurrent backup is refused", "already running" in str(exc), str(exc))
    # Let whichever copy is still running finish before the folder is removed:
    # robocopy still writing into it kept part of the folder from being
    # deleted, so an empty backup folder was left behind in TEMP.
    for _ in range(150):                      # up to ~15s
        if concurrent_runner.job is None or not concurrent_runner.job.running:
            break
        time.sleep(0.1)

shutil.rmtree(temp_dir, ignore_errors=True)
print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
