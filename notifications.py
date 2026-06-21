"""E-mail (SMTP), SMS (HTTP-gateway) og påmindelses-scheduler.

Uden konfiguration logges beskeder blot til konsollen, så lokal test virker
uden rigtige udbydere.
"""
import json
import smtplib
import threading
import time
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import db

# Sættes af app.py: funktion (conn, group, event) -> csv-tekst. Undgår cirkulær import.
csv_builder = None


def _log(channel: str, to: str, subject: str, body: str) -> None:
    print(f"[NOTIFIKATION/{channel}] -> {to or '(ingen modtager)'}: {subject}\n{body}\n",
          flush=True)


def send_email(settings, to: str, subject: str, body: str) -> None:
    if not to:
        return
    if not settings["smtp_host"]:
        _log("MAIL", to, subject, body)
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings["smtp_from"] or settings["smtp_user"]
        msg["To"] = to
        with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=15) as s:
            if settings["smtp_use_tls"]:
                s.starttls()
            if settings["smtp_user"]:
                s.login(settings["smtp_user"], settings["smtp_password"])
            s.send_message(msg)
    except Exception as e:  # robust: en notifikation må aldrig vælte en tilmelding
        print(f"[MAIL-FEJL] {e}")
        _log("MAIL", to, subject, body)


def send_email_with_attachment(settings, to, subject, body, filename, content):
    if not to:
        return
    if not settings["smtp_host"]:
        _log("MAIL+CSV", to, subject, f"{body}\n[vedhæftet: {filename}]\n{content}")
        return
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = settings["smtp_from"] or settings["smtp_user"]
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        part = MIMEText(content, "csv", "utf-8")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
        with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=15) as s:
            if settings["smtp_use_tls"]:
                s.starttls()
            if settings["smtp_user"]:
                s.login(settings["smtp_user"], settings["smtp_password"])
            s.send_message(msg)
    except Exception as e:
        print(f"[MAIL-FEJL] {e}", flush=True)
        _log("MAIL+CSV", to, subject, f"{body}\n[vedhæftet: {filename}]")


def send_sms(settings, to: str, body: str) -> None:
    if not to:
        return
    if not settings["sms_api_key"]:
        _log("SMS", to, "(sms)", body)
        return
    try:
        # GatewayAPI (dansk) som default-skabelon. Andre udbydere kan tilføjes her.
        data = urllib.parse.urlencode({
            "token": settings["sms_api_key"],
            "sender": settings["sms_sender"] or "Tilmeld",
            "message": body,
            "msisdn": to,
        }).encode()
        req = urllib.request.Request(
            "https://gatewayapi.com/rest/mtsms", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[SMS-FEJL] {e}")
        _log("SMS", to, "(sms)", body)


def notify_admin(conn, group, subject: str, body: str) -> None:
    """Send til gruppe-admin via de kanaler master har slået til."""
    settings = db.get_settings(conn)
    if group["mail_enabled"] and group["admin_email"]:
        send_email(settings, group["admin_email"], subject, body)
    if group["sms_enabled"] and group["admin_phone"]:
        send_sms(settings, group["admin_phone"], f"{subject}: {body}")


def notify_participant(conn, group, email: str, phone: str, subject: str, body: str) -> None:
    settings = db.get_settings(conn)
    if group["mail_enabled"] and email:
        send_email(settings, email, subject, body)
    if group["sms_enabled"] and phone:
        send_sms(settings, phone, f"{subject}: {body}")


# ---- Scheduler: påmindelse 24t før frist + CSV 2t efter frist ------------------

def process_scheduled(now=None):
    """Én gennemløb. Adskilt fra loopet så den kan testes direkte."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    conn = db.get_db()
    try:
        # Påmindelse: indenfor 24t før fristen (og fristen ikke passeret)
        rows = conn.execute(
            "SELECT * FROM events WHERE notify_reminder = 1 AND reminder_sent = 0 "
            "AND signup_deadline != ''").fetchall()
        for ev in rows:
            try:
                deadline = datetime.fromisoformat(ev["signup_deadline"])
            except ValueError:
                continue
            if now <= deadline <= now + timedelta(hours=24):
                group = conn.execute(
                    "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
                regs = conn.execute(
                    "SELECT * FROM registrations WHERE event_id = ?", (ev["id"],)).fetchall()
                subject = f"Påmindelse: tilmeldingsfrist for {ev['name']}"
                body = (f"Tilmeldingsfristen for '{ev['name']}' er {ev['signup_deadline']}. "
                        "Husk at tilmelde dig eller opdatere din tilmelding.")
                notify_admin(conn, group, subject, body)
                for r in regs:
                    notify_participant(conn, group, r["email"], r["phone"], subject, body)
                conn.execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (ev["id"],))
                conn.commit()

        # CSV til admin 2 timer efter frist
        csv_rows = conn.execute(
            "SELECT * FROM events WHERE csv_after_deadline = 1 AND csv_sent = 0 "
            "AND signup_deadline != ''").fetchall()
        for ev in csv_rows:
            try:
                deadline = datetime.fromisoformat(ev["signup_deadline"])
            except ValueError:
                continue
            if now >= deadline + timedelta(hours=2):
                group = conn.execute(
                    "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
                if group["mail_enabled"] and group["admin_email"] and csv_builder:
                    content = csv_builder(conn, group, ev)
                    settings = db.get_settings(conn)
                    send_email_with_attachment(
                        settings, group["admin_email"],
                        f"Deltagerliste: {ev['name']}",
                        f"Tilmeldingsfristen for '{ev['name']}' er udløbet. "
                        "Deltagerlisten er vedhæftet.",
                        f"{group['slug']}-{ev['slug']}-deltagere.csv", content)
                conn.execute("UPDATE events SET csv_sent = 1 WHERE id = ?", (ev["id"],))
                conn.commit()
    finally:
        conn.close()


def _reminder_loop():
    while True:
        try:
            process_scheduled()
        except Exception as e:
            print(f"[SCHEDULER-FEJL] {e}", flush=True)
        time.sleep(600)  # tjek hvert 10. minut


def start_scheduler():
    t = threading.Thread(target=_reminder_loop, daemon=True)
    t.start()
