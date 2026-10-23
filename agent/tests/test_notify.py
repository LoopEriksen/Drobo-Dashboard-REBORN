"""
Offline tests for notify.py, with the email backend as the main event.

Two properties matter more than the rest and are tested by BOOBY-TRAPPING
rather than by mocking politely:

  - a config without email must never touch smtplib at all
  - a password must never reach stdout or an exception message

Both are security properties on a service that runs unattended on a machine
holding somebody's only copy of their data, so neither is left to inspection.

    py test_notify.py
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk"
        )
    ),
)

from drobo_agent import notify  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + label
        + (f"  {failure_detail}" if failure_detail and not condition else "")
    )
    if not condition:
        failures.append(label)


SECRET = "hunter2-do-not-leak"


def email_config(**overrides):
    email_settings = {
        "host": "smtp.example.invalid",
        "port": 587,
        "username": "me@example.invalid",
        "password": SECRET,
        "from": "me@example.invalid",
        "to": "you@example.invalid",
    }
    email_settings.update(overrides)
    return {"backends": ["email"], "email": email_settings}


print("\n== the existing backends still behave ==")
captured_stdout = io.StringIO()
with contextlib.redirect_stdout(captured_stdout):
    notify.build({"backends": ["console"]}).send("T", "hello", "critical")
check("console still prints", "hello" in captured_stdout.getvalue(),
      captured_stdout.getvalue())
check("an empty backend list falls back to console",
      notify.build({}).members[0].name == "console")
check("an unknown backend is ignored, not fatal",
      notify.build({"backends": ["telepathy"]}).members[0].name == "console")


print("\n== a config with no email never loads smtplib ==")


# Not "does not send" -- does not IMPORT. Proven by replacing the module with
# something that explodes on any attribute access.
class _ExplodingModule:
    def __getattr__(self, name):
        raise AssertionError(
            f"smtplib was touched ({name}) with email not configured"
        )


_real_smtplib = sys.modules.get("smtplib")
sys.modules["smtplib"] = _ExplodingModule()
try:
    with contextlib.redirect_stdout(io.StringIO()):
        notify.build({"backends": ["console"]}).send("T", "m", "info")
        notify.build({"backends": ["ntfy"], "ntfy": {"topic": ""}}).send(
            "T", "m", "info"
        )
    check("console and a disabled ntfy touch smtplib not at all", True)
except AssertionError as exc:
    check("console and a disabled ntfy touch smtplib not at all", False, exc)
finally:
    if _real_smtplib is not None:
        sys.modules["smtplib"] = _real_smtplib
    else:
        del sys.modules["smtplib"]

print("\n== a half-configured email backend is refused, not half-used ==")
# An alert that silently fails to send is worse than one never configured,
# because you believe you are covered.
for missing, label in ((["host"], "no host"), (["to"], "no recipient")):
    notify_config = email_config()
    for field_name in missing:
        notify_config["email"][field_name] = ""
    captured_stdout = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout):
        notifier_group = notify.build(notify_config)
    check(
        f"{label} -> email backend not built",
        all(member.name != "email" for member in notifier_group.members),
        [member.name for member in notifier_group.members],
    )
    check(
        f"{label} -> and it says so on stdout",
        "skipping" in captured_stdout.getvalue(),
        captured_stdout.getvalue(),
    )

print("\n== a complete config does build the backend ==")
notifier_group = notify.build(email_config())
email_backends = [member for member in notifier_group.members if member.name
                  == "email"]
check(
    "the email backend is present",
    len(email_backends) == 1,
    [member.name for member in notifier_group.members],
)
check(
    "a comma-separated recipient list is split",
    notify.build(email_config(to="a@x.invalid, b@x.invalid"))
    .members[0]
    .recipients
    == ["a@x.invalid", "b@x.invalid"],
)
check(
    "a single recipient still works",
    email_backends[0].recipients == ["you@example.invalid"],
)
check(
    "'from' defaults to the username when unset",
    notify.build(email_config(**{"from": ""})).members[0].sender
    == "me@example.invalid",
)

print("\n== THE PASSWORD NEVER ESCAPES ==")
# A rejected SMTP login habitually echoes the credentials back in the server's
# response text. That text must never reach a log line or an exception.
email_backend = email_backends[0]
check("it is not a public attribute", not any(
    getattr(email_backend, attribute, None) == SECRET
    for attribute in dir(email_backend) if not attribute.startswith("_")))
check("repr() does not contain it", SECRET not in repr(email_backend))
check(
    "vars() does not expose it under a public name",
    not any(
        value == SECRET
        for name, value in vars(email_backend).items()
        if not name.startswith("_")
    ),
)


# Force a failure and check what comes out of it.
class _CredentialEchoingSMTP:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        raise RuntimeError(f"535 auth failed for user={user} pass={password}")

    def send_message(self, message):
        pass


import smtplib  # noqa: E402
_original_smtp_class = smtplib.SMTP
smtplib.SMTP = _CredentialEchoingSMTP
try:
    raised = None
    try:
        email_backend.send("Drobo: action needed", "a drive failed",
                           "critical")
    except Exception as exc:
        raised = exc
    check("a delivery failure raises", raised is not None)
    check("-- and the message does NOT contain the password",
          raised is not None and SECRET not in str(raised), str(raised))
    check(
        "-- nor the server's echoed response",
        raised is not None and "535" not in str(raised),
        str(raised),
    )
    check(
        "-- but does name the server, so it is diagnosable",
        raised is not None and "smtp.example.invalid" in str(raised),
        str(raised),
    )
    check("-- and the error TYPE, which is the useful half",
          raised is not None and "RuntimeError" in str(raised), str(raised))

    # A failing backend must not stop the others -- NotifierGroup's contract.
    captured_stdout = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout):
        notify.build(
            {**email_config(), "backends": ["console", "email"]}
        ).send("T", "m", "critical")
    printed = captured_stdout.getvalue()
    check(
        "a broken email backend does not stop the console one",
        "m" in printed,
        printed,
    )
    check(
        "-- and the failure notice carries no password",
        SECRET not in printed,
        printed,
    )
finally:
    smtplib.SMTP = _original_smtp_class

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
