"""E-mail (SMTP), WhatsApp (HTTP-bro/gateway) og påmindelses-scheduler.

Uden konfiguration logges beskeder blot til konsollen, så lokal test virker
uden rigtige udbydere.
"""
import json
import smtplib
import threading
import time
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import db

# Sættes af app.py: funktion (conn, group, event) -> csv-tekst. Undgår cirkulær import.
csv_builder = None

# Standard-mail-skabeloner. Admin kan overskrive dem pr. gruppe (hvis master tillader).
# Pladsholdere: {event} {name} {date} {group} {deadline}
DEFAULT_TEMPLATES = {
    "new_signup": ("Ny tilmelding: {event}",
                   "{name} har tilmeldt sig {event}."),
    "change": ("Ændret tilmelding: {event}",
               "{name} har ændret sin tilmelding til {event}."),
    "receipt": ("Kvittering: {event}",
                "Tak for din tilmelding til {event} d. {date}."),
    "reminder": ("Påmindelse: tilmeldingsfrist for {event}",
                 "Tilmeldingsfristen for '{event}' er {deadline}. "
                 "Husk at tilmelde dig eller opdatere din tilmelding."),
    "deadline": ("Tilmeldingsfrist nået: {event}",
                 "Tilmeldingsfristen for {event} er nået.\n"
                 "Se deltagerlisten og hent CSV her: {link}"),
}


def _safe_format(text, ctx):
    class _Default(dict):
        def __missing__(self, key):
            return ""
    try:
        return text.format_map(_Default(ctx))
    except Exception:
        return text


def template_for(conn, group, tkey):
    """Returnér (subject, body) for en skabelon — admin-tilpasset eller standard."""
    subject, body = DEFAULT_TEMPLATES.get(tkey, ("", ""))
    try:
        row = conn.execute(
            "SELECT subject, body FROM mail_templates WHERE group_id = ? AND tkey = ?",
            (group["id"], tkey)).fetchone()
        if row and (row["subject"] or row["body"]):
            subject, body = row["subject"] or subject, row["body"] or body
    except Exception:
        pass
    return subject, body


def render_message(conn, group, tkey, ctx):
    subject, body = template_for(conn, group, tkey)
    return _safe_format(subject, ctx), _safe_format(body, ctx)


def _log(channel: str, to: str, subject: str, body: str) -> None:
    print(f"[NOTIFIKATION/{channel}] -> {to or '(ingen modtager)'}: {subject}\n{body}\n",
          flush=True)


def _smtp_send(settings, msg) -> None:
    """Åbn forbindelse og send. Port 465 = implicit SSL (SMTPS); ellers STARTTLS
    (587) hvis slået til. Virker med Gmail, Office 365 m.fl."""
    port = int(settings["smtp_port"] or 587)
    if port == 465:
        with smtplib.SMTP_SSL(settings["smtp_host"], port, timeout=15) as s:
            if settings["smtp_user"]:
                s.login(settings["smtp_user"], settings["smtp_password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings["smtp_host"], port, timeout=15) as s:
            if settings["smtp_use_tls"]:
                s.starttls()
            if settings["smtp_user"]:
                s.login(settings["smtp_user"], settings["smtp_password"])
            s.send_message(msg)


def send_email(settings, to: str, subject: str, body: str) -> str:
    """Returnér "" hvis mailen blev afsendt, ellers en kort fejl-/årsagstekst."""
    if not to:
        return "ingen modtager"
    if not settings["smtp_host"]:
        _log("MAIL", to, subject, body)
        return "SMTP ikke konfigureret"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings["smtp_from"] or settings["smtp_user"]
        msg["To"] = to
        _smtp_send(settings, msg)
        return ""
    except Exception as e:  # robust: en notifikation må aldrig vælte en tilmelding
        print(f"[MAIL-FEJL] {e}", flush=True)
        _log("MAIL", to, subject, body)
        return str(e)[:300]


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
        _smtp_send(settings, msg)
    except Exception as e:
        print(f"[MAIL-FEJL] {e}", flush=True)
        _log("MAIL+CSV", to, subject, f"{body}\n[vedhæftet: {filename}]")


def send_whatsapp(settings, to: str, body: str) -> str:
    """Send en WhatsApp-besked via en HTTP-bro/gateway. Returnér "" hvis sendt,
    ellers en kort fejl-/årsagstekst.

    Kontrakt (konfigurér din bro derefter): POST til whatsapp_api_url med
    Authorization: Bearer <whatsapp_api_key> og JSON-body {"to": <modtager>,
    "message": <tekst>}. Modtager kan være et telefonnummer eller et gruppe-id.
    """
    if not to:
        return "ingen modtager"
    if not settings["whatsapp_api_url"]:
        _log("WHATSAPP", to, "(whatsapp)", body)
        return "WhatsApp-gateway ikke konfigureret"
    try:
        data = json.dumps({"to": to, "message": body}).encode()
        headers = {"Content-Type": "application/json"}
        if settings["whatsapp_api_key"]:
            headers["Authorization"] = "Bearer " + settings["whatsapp_api_key"]
        req = urllib.request.Request(
            settings["whatsapp_api_url"], data=data, headers=headers)
        urllib.request.urlopen(req, timeout=15).read()
        return ""
    except Exception as e:
        print(f"[WHATSAPP-FEJL] {e}", flush=True)
        _log("WHATSAPP", to, "(whatsapp)", body)
        return str(e)[:300]


def _note(err: str) -> str:
    return f"  ⚠ ikke leveret ({err})" if err else ""


def notify_admin(conn, group, subject: str, body: str) -> None:
    """Send til gruppe-admin via de kanaler master har slået til."""
    settings = db.get_settings(conn)
    if group["mail_enabled"] and group["admin_email"]:
        err = send_email(settings, group["admin_email"], subject, body)
        db.add_log(conn, "mail",
                   f"Mail til {group['admin_email']}: {subject}{_note(err)}", group["slug"])
    if group["whatsapp_enabled"] and group["whatsapp_recipient"]:
        err = send_whatsapp(settings, group["whatsapp_recipient"], f"{subject}: {body}")
        db.add_log(conn, "whatsapp",
                   f"WhatsApp til {group['whatsapp_recipient']}: {subject}{_note(err)}",
                   group["slug"])


def notify_participant(conn, group, email: str, whatsapp: str, subject: str, body: str) -> None:
    settings = db.get_settings(conn)
    if group["mail_enabled"] and email:
        err = send_email(settings, email, subject, body)
        db.add_log(conn, "mail", f"Mail til {email}: {subject}{_note(err)}", group["slug"])
    if group["whatsapp_enabled"] and whatsapp:
        err = send_whatsapp(settings, whatsapp, f"{subject}: {body}")
        db.add_log(conn, "whatsapp", f"WhatsApp til {whatsapp}: {subject}{_note(err)}",
                   group["slug"])


# ---- Scheduler: påmindelse 24t før frist + CSV 2t efter frist ------------------

def process_scheduled(now=None):
    """Én gennemløb. Adskilt fra loopet så den kan testes direkte."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    conn = db.get_db()
    try:
        # Opbevaring: ryd aktivitetslog ældre end 30 dage
        cutoff = (now - timedelta(days=30)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM activity_log WHERE created_at < ?", (cutoff,))
        conn.commit()

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
                ctx = {"event": ev["name"], "date": ev["event_date"],
                       "group": group["name"], "deadline": ev["signup_deadline"]}
                subject, body = render_message(conn, group, "reminder", ctx)
                notify_admin(conn, group, subject, body)
                for r in regs:
                    notify_participant(conn, group, r["email"], r["phone"], subject, body)
                conn.execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (ev["id"],))
                conn.commit()

        # Besked til admin når fristen er nået (med link til deltagerlisten)
        dl_rows = conn.execute(
            "SELECT * FROM events WHERE notify_deadline = 1 AND deadline_sent = 0 "
            "AND signup_deadline != ''").fetchall()
        for ev in dl_rows:
            try:
                deadline = datetime.fromisoformat(ev["signup_deadline"])
            except ValueError:
                continue
            if now >= deadline:
                group = conn.execute(
                    "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
                base = (db.get_settings(conn)["base_url"] or "").rstrip("/")
                link = f"{base}/{group['slug']}/admin/events/{ev['id']}/list"
                ctx = {"event": ev["name"], "date": ev["event_date"],
                       "group": group["name"], "deadline": ev["signup_deadline"],
                       "link": link}
                subject, body = render_message(conn, group, "deadline", ctx)
                notify_admin(conn, group, subject, body)
                conn.execute("UPDATE events SET deadline_sent = 1 WHERE id = ?", (ev["id"],))
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
