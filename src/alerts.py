"""One-line alerts to Eran: unmatched deal, >50 MB file, decoded failure.

Sink: SMTP email when ALERT_SMTP_HOST is configured, always appended to
state/alerts.log. Never guesses, never blocks the batch — an alert is the
terminal action for a row the pipeline refuses to decide on its own.
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .config import state_path

log = logging.getLogger(__name__)


def alert(line: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = f"{stamp} {line}"
    log.warning("ALERT: %s", line)
    with open(state_path("alerts_log"), "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    _email(line)


def _email(line: str) -> None:
    host = os.environ.get("ALERT_SMTP_HOST", "").strip()
    to = os.environ.get("ALERT_EMAIL_TO", "").strip()
    if not host or not to:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[polycam2hub] {line[:120]}"
        msg["From"] = os.environ.get("ALERT_SMTP_USER", "polycam2hub@hellochapter.com")
        msg["To"] = to
        msg.set_content(line)
        with smtplib.SMTP(host, int(os.environ.get("ALERT_SMTP_PORT", "587"))) as s:
            s.starttls()
            user = os.environ.get("ALERT_SMTP_USER", "")
            if user:
                s.login(user, os.environ.get("ALERT_SMTP_PASSWORD", ""))
            s.send_message(msg)
    except Exception:  # alerting must never take the pipeline down
        log.exception("alert email failed (logged to alerts.log regardless)")
