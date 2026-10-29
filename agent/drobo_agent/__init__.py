"""
Drobo Agent - the always-on service behind the Windows and iOS apps.

Layout (run_agent.py, one folder up, starts it):
    api.py       the HTTP/JSON server; its opening comment lists
                 every route
    webui.py     the self-contained web dashboard served at /
    monitor.py   polling loop, history, alert rules, sending
                 notifications
    drivers.py   the real Drobo driver and the simulator: the polling
                 path to the Drobo (api.py's on-demand routes call the
                 SDK directly)
    notify.py    where alerts go: console, ntfy, email
    photos.py    DroboPix-style photo backup, receiving half
    backup.py    copying the Drobo to an external drive, with robocopy
    updates.py   the update check, off unless switched on
    config.py    config loading and first-run token generation
    qr.py        the QR code a phone scans to pair with the agent
    models.py    the shapes the agent adds (alerts); the device shapes
                 come from drobo_nasd.models and are re-exported here
"""

__version__ = "1.2.0"

# The Drobo protocol lives in its own package, `drobo_nasd`, a sibling
# of this one rather than a subpackage. That split is deliberate: the
# library knows about Drobos and nothing about applications, so it is
# useful to anyone with this hardware, not just to this dashboard.
#
# The sdk/ that ships in this checkout always wins over anything
# installed in the interpreter. Another copy of a package named
# `drobo_nasd` may be installed there, and it need not match this one,
# so a `pip install` made elsewhere would otherwise silently redirect
# this agent's imports into that copy. Binding to the sibling sdk/ first
# keeps this checkout running its own code and nothing else. Only fall
# back to an installed copy when there is no sdk/ next to us (an
# unpacked release rather than a clone).
import os as _os
import sys as _sys

_sibling_sdk_dir = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "..", "..", "sdk"))
if _os.path.isdir(_os.path.join(_sibling_sdk_dir, "drobo_nasd")):
    if _sibling_sdk_dir in _sys.path:
        _sys.path.remove(_sibling_sdk_dir)
    # ahead of site-packages, not merely present
    _sys.path.insert(0, _sibling_sdk_dir)
else:  # no checkout beside us; an installed copy is the only option
    import drobo_nasd as _installed_drobo_nasd  # noqa: F401
