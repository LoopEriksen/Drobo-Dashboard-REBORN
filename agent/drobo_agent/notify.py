"""
Where alerts go.

Deliberately small. The agent doesn't care how a notification is
delivered, so adding APNs later means adding one class here and nothing
else changes.

ntfy is the recommended starting point: free, has an iOS and Android
app, and needs no account -- you pick an unguessable topic name and
subscribe to it. Because anyone who knows the topic name can read it,
treat the topic like a password and don't put anything sensitive in the
message text.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from .models import SEVERITY_CRITICAL, SEVERITY_WARNING


class Notifier:
    """
    One way of telling the user something. A single method, because the
    agent does not care how delivery happens: Monitor calls send() and
    every backend -- console, ntfy, email, a future APNs -- is just
    another subclass.
    """

    name = "base"

    def send(self, title: str, message: str, severity: str) -> None:
        """Deliver one alert. Each backend overrides this."""
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """
    Print the alert to the agent's own output. Always available, needs
    no configuration, and is what build() falls back to, so an alert is
    never silently dropped just because nothing else was set up.
    """

    name = "console"

    def send(self, title: str, message: str, severity: str) -> None:
        """One line per alert, marked "!!" critical or " !" warning."""
        marker = {SEVERITY_CRITICAL: "!!",
                  SEVERITY_WARNING: " !"}.get(severity, "  ")
        print(f"[alert]{marker} {title}: {message}", flush=True)


class NtfyNotifier(Notifier):
    """POST to an ntfy topic.
    Works with ntfy.sh or a self-hosted server.
    """

    name = "ntfy"

    # ntfy priorities: 1 min .. 5 max
    _PRIORITY_BY_SEVERITY = {SEVERITY_CRITICAL: "5", SEVERITY_WARNING: "4"}
    _TAGS_BY_SEVERITY = {SEVERITY_CRITICAL: "rotating_light",
                         SEVERITY_WARNING: "warning"}

    #: Bounded so an ntfy server that has gone away cannot hold up the
    #: poll cycle the alert is sent from.
    TIMEOUT_SECONDS = 10

    def __init__(self, server: str, topic: str):
        """
        An empty server means the public ntfy.sh, the one the phone app
        subscribes to out of the box, so a topic alone is a working
        configuration.
        """
        self.server = (server or "https://ntfy.sh").rstrip("/")
        self.topic = topic

    def send(self, title: str, message: str, severity: str) -> None:
        """
        POST the message to the topic, with severity mapped to ntfy's
        priority and tags. A failure is printed, not raised.
        """
        if not self.topic:
            return
        request = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=message.encode("utf-8"),
            method="POST",
            headers={
                "Title": title,
                "Priority": self._PRIORITY_BY_SEVERITY.get(severity, "3"),
                "Tags": self._TAGS_BY_SEVERITY.get(severity, "floppy_disk"),
            },
        )
        try:
            with urllib.request.urlopen(
                    request, timeout=self.TIMEOUT_SECONDS) as response:
                response.read()
        except (urllib.error.URLError, OSError) as exc:
            # Never let a failed notification take the agent down -- the
            # whole point is that it keeps watching the array.
            print(f"[notify] ntfy send failed: {exc}", flush=True)


class EmailNotifier(Notifier):
    """
    Send an alert by email, over SMTP.

    The original Drobo Dashboard could email you when a drive failed,
    and this is the replacement for it. Email earns its place next to
    the ntfy push for a boring reason: it is the channel that still
    works in five years when the app on your phone has stopped being
    installable, which on this project is not a hypothetical -- it is
    exactly what happened to DroboPix.

    WHY THE AGENT SENDS IT, NOT THE DROBO. The device has its own mail
    settings (the firmware carries a DRINasEmailAlertConfig section and
    an AlertMailer), but making the Drobo send them would mean writing
    configuration to it, and eCmdSetConfig is in DO_NOT_SEND and staying
    there. Sending from the agent also means alerts still arrive when
    the Drobo is the thing that is broken -- a Drobo that cannot be
    reached obviously cannot email you about it, which is the single
    alert you would most want to receive.

    CREDENTIALS. The password comes from config.json, which is
    gitignored, and it never appears in a log line, an error message, or
    the API. Delivery failures are reported with the server and the
    error type but never the exception's full text, because a rejected
    SMTP login habitually echoes the username back and sometimes more.
    """

    name = "email"

    #: SMTP is slow and this runs on the alerting path. Bounded so a
    #: mail server that has gone away cannot hold up a poll cycle.
    TIMEOUT_SECONDS = 15.0

    def __init__(self, host: str, port: int, username: str, password: str,
                 sender: str, recipients: list[str], use_tls: bool = True):
        self.host = host
        self.port = port
        self.username = username
        self._password = password        # never logged, never returned
        self.sender = sender or username
        self.recipients = recipients
        self.use_tls = use_tls

    def send(self, title: str, message: str, severity: str) -> None:
        """
        Mail the alert to every recipient. Raises RuntimeError on any
        failure, naming the server and the error type only (see the
        class docstring for why never the error's text).
        """
        # Imported here rather than at module scope so that a config
        # with no email backend never even loads smtplib -- which is
        # also what makes the "contacts nothing when off" test provable
        # by boobytrapping it.
        import smtplib
        from email.message import EmailMessage

        mail = EmailMessage()
        mail["Subject"] = title
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.recipients)
        mail.set_content(
            f"{message}\n\n"
            f"-- \n"
            f"Sent by Drobo Dashboard REBORN, the agent watching your Drobo.\n"
            f"Severity: {severity}\n")

        try:
            with smtplib.SMTP(
                    self.host, self.port,
                    timeout=self.TIMEOUT_SECONDS) as smtp:
                self._log_in_and_send(smtp, mail)
        except Exception as exc:
            # Deliberately NOT str(exc): a rejected SMTP login commonly
            # echoes the credentials back in the server's response, and
            # this string goes to stdout and into the agent's log.
            raise RuntimeError(
                f"could not send mail via {self.host}:{self.port} "
                f"({type(exc).__name__})") from None

    def _log_in_and_send(self, smtp, mail) -> None:
        """
        On an open SMTP connection: STARTTLS when use_tls is set, log in
        when there is a username, then send. use_tls means STARTTLS on
        the plain connection; there is no implicit-TLS (port 465) mode.
        """
        if self.use_tls:
            smtp.starttls()
        if self.username:
            smtp.login(self.username, self._password)
        smtp.send_message(mail)


class NotifierGroup(Notifier):
    """
    Every configured backend, sent to as one. Each member's failure is
    caught and printed on its own, so a broken mail server cannot stop
    the ntfy push (or the console line) for the same alert.
    """

    name = "group"

    def __init__(self, members: list[Notifier]):
        self.members = members

    def send(self, title: str, message: str, severity: str) -> None:
        """Send to each member; one failing never stops the rest."""
        for member in self.members:
            try:
                member.send(title, message, severity)
            # A broken backend must not stop the others.
            except Exception as exc:  # noqa: BLE001
                print(f"[notify] backend {member.name} failed: {exc}",
                      flush=True)


def build(notify_config: dict) -> Notifier:
    """
    The notifier the agent sends every alert through, from the "notify"
    block of config.json.

    A backend that is switched on but not configured well enough to send
    is reported and skipped, and an unknown name is reported and
    ignored. If that leaves nothing, the console is used, so an alert
    always goes somewhere the user can find it.
    """
    members: list[Notifier] = []
    for backend in notify_config.get("backends", ["console"]):
        if backend == "console":
            member = ConsoleNotifier()
        elif backend == "ntfy":
            member = _build_ntfy(notify_config)
        elif backend == "email":
            member = _build_email(notify_config)
        else:
            print(f"[notify] unknown backend {backend!r} -- ignoring",
                  flush=True)
            continue
        if member is not None:
            members.append(member)
    if not members:
        members.append(ConsoleNotifier())
    return NotifierGroup(members)


def _build_ntfy(notify_config: dict) -> Notifier | None:
    """The ntfy backend, or None (reported) when it has no topic."""
    ntfy_config = notify_config.get("ntfy", {})
    if not ntfy_config.get("topic"):
        print("[notify] ntfy is enabled but "
              "no topic is set -- skipping", flush=True)
        return None
    return NtfyNotifier(ntfy_config.get("server", ""),
                        ntfy_config["topic"])


def _build_email(notify_config: dict) -> Notifier | None:
    """
    The email backend, or None (reported) when a required setting is
    missing. "to" may be a list or one comma-separated string.
    """
    email_config = notify_config.get("email", {})
    # Every one of these is required, and a partial configuration is
    # reported rather than half-used: an alert that silently fails to
    # send is worse than one that was never configured, because you
    # believe you are covered.
    missing_keys = [key for key in ("host", "to")
                    if not email_config.get(key)]
    if missing_keys:
        print(f"[notify] email is enabled but {', '.join(missing_keys)} "
              f"{'is' if len(missing_keys) == 1 else 'are'} "
              f"not set -- skipping",
              flush=True)
        return None
    recipients = email_config["to"]
    if isinstance(recipients, str):
        recipients = [
            address.strip() for address in recipients.split(",")
            if address.strip()]
    return EmailNotifier(
        host=email_config["host"],
        port=int(email_config.get("port", 587)),
        username=email_config.get("username", ""),
        password=email_config.get("password", ""),
        sender=email_config.get("from", ""),
        recipients=recipients,
        use_tls=bool(email_config.get("use_tls", True)),
    )
