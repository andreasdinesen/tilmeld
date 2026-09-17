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
    "waitlist_promoted": ("Du har fået en plads: {event}",
                          "Hej {name}. Der er blevet en plads ledig, og du er rykket op fra "
                          "ventelisten til {event} d. {date}. Vi ses!"),
    "event_reminder": ("Påmindelse: {event} er i morgen",
                       "Hej {name}. Husk at du er tilmeldt {event} d. {date}"
                       "{start}. Vi ses!"),
    "event_announce": ("Nyt event i {group}: {event}",
                       "Hej {name}. {event} afholdes d. {date}{start}.\n"
                       "Tilmeldingsfrist: {deadline}.\n{link}"),
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


def channels(conn, group):
    """(mail, whatsapp): kan gruppen reelt sende? Kræver BÅDE global opsætning og at
    master har slået kanalen til for gruppen. `app.group_channels` kalder den her, så
    UI og afsendelse aldrig kan være uenige om, hvad der er aktiveret på systemet."""
    s = db.get_settings(conn)
    return (bool(s["smtp_host"]) and bool(group["mail_enabled"]),
            bool(s["whatsapp_api_url"]) and bool(group["whatsapp_enabled"]))


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


def _reg_declined(conn, group_id, reg_id) -> bool:
    """Har tilmeldingen krydset et 'deltager ikke'-felt af?"""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM group_fields WHERE group_id = ? AND is_decline = 1",
        (group_id,)).fetchall()]
    if not ids:
        return False
    ph = ",".join("?" * len(ids))
    return bool(conn.execute(
        f"SELECT 1 FROM registration_values WHERE registration_id = ? "
        f"AND field_id IN ({ph}) AND value = 'Ja' LIMIT 1", [reg_id] + ids).fetchone())


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


# ---- Notifikationsliste: hvem står på den, og hvordan sendes der til dem ------

def _norm_mail(v):
    return (v or "").strip().lower()


def _norm_phone(v):
    """Sammenlignings-form for et nummer: kun cifre og et evt. ledende +.
    "+45 20 12 34 56" og "+4520123456" er den samme modtager."""
    keep = "".join(ch for ch in (v or "") if ch.isdigit() or ch == "+")
    return keep if not keep.startswith("+") else "+" + keep[1:].replace("+", "")


def list_recipients(conn, group):
    """Modtagerne på gruppens notifikationsliste.

    To kilder: dem admin har skrevet ind i hånden, og — når gruppen kører med
    individuelle bruger-konti — gruppens egne brugere, hvis mail og mobilnummer
    hentes direkte fra deres profil. Sidstnævnte skal ikke vedligeholdes to steder:
    retter brugeren sin mail, følger listen med af sig selv.

    Dubletter (samme mail eller samme nummer) fjernes. De manuelle står først og
    vinder, så et navn admin selv har skrevet ikke bliver overskrevet af et brugernavn.
    """
    rows = conn.execute(
        "SELECT * FROM notify_recipients WHERE group_id = ? ORDER BY id",
        (group["id"],)).fetchall()
    out = [{"id": r["id"], "name": r["name"] or "", "email": r["email"] or "",
            "whatsapp": r["whatsapp"] or "", "active": bool(r["active"]),
            "source": "manual"} for r in rows]

    if group["user_accounts_enabled"] and group["notify_list_users"]:
        users = conn.execute(
            "SELECT u.* FROM users u JOIN user_groups ug ON ug.user_id = u.id "
            "WHERE ug.group_id = ? ORDER BY u.username", (group["id"],)).fetchall()
        for u in users:
            out.append({"id": None, "name": u["name"] or u["username"],
                        "email": u["email"] or "", "whatsapp": u["whatsapp"] or "",
                        "active": True, "source": "user", "username": u["username"]})

    seen_mail, seen_phone, uniq = set(), set(), []
    for r in out:
        m, p = _norm_mail(r["email"]), _norm_phone(r["whatsapp"])
        if (m and m in seen_mail) or (p and p in seen_phone):
            continue
        if m:
            seen_mail.add(m)
        if p:
            seen_phone.add(p)
        uniq.append(r)
    return uniq


def send_to_list(conn, group, subject, body, ctx=None, note=""):
    """Send én besked til hele notifikationslisten. Returnér (mails, whatsapps, fejl).

    `subject` og `body` er RÅ skabelontekst — pladsholderne udfyldes her, én gang pr.
    modtager, så {name} bliver modtagerens eget navn. Kanalerne følger det, master har
    aktiveret for gruppen: er mail slået fra, sendes der ikke mail, uanset hvad der står
    i listen.
    """
    mail_on, wa_on = channels(conn, group)
    settings = db.get_settings(conn)
    mails = whatsapps = 0
    errors = []
    for r in list_recipients(conn, group):
        if not r["active"]:
            continue
        c = dict(ctx or {}, name=r["name"])
        subj, text = _safe_format(subject, c), _safe_format(body, c)
        if mail_on and r["email"]:
            err = send_email(settings, r["email"], subj, text)
            if err:
                errors.append(f"{r['email']}: {err}")
            else:
                mails += 1
        if wa_on and r["whatsapp"]:
            err = send_whatsapp(settings, r["whatsapp"], f"{subj}: {text}")
            if err:
                errors.append(f"{r['whatsapp']}: {err}")
            else:
                whatsapps += 1

    # Én logline for hele udsendelsen — ikke én pr. modtager. Listen kan være lang,
    # og aktivitetsloggen skal stadig kunne læses.
    suffix = f" ({note})" if note else ""
    if mails or whatsapps:
        db.add_log(conn, "mail" if mails else "whatsapp",
                   f"Notifikationsliste{suffix}: {mails} mail, {whatsapps} WhatsApp — "
                   f"»{_safe_format(subject, dict(ctx or {}, name=''))}«", group["slug"])
    for e in errors[:10]:
        db.add_log(conn, "mail", f"Notifikationsliste{suffix} ikke leveret — {e}",
                   group["slug"])
    return mails, whatsapps, errors


def announce_message(conn, group, ev, base_url=""):
    """(subject, body, ctx) til varslingen om et event. Skabelonen er RÅ — {name}
    udfyldes først pr. modtager i `send_to_list`."""
    subject, body = template_for(conn, group, "event_announce")
    base = (base_url or "").rstrip("/")
    ctx = {"event": ev["name"], "date": ev["event_date"], "group": group["name"],
           "deadline": ev["signup_deadline"] or "ingen",
           "start": f" kl. {ev['start_time']}" if ev["start_time"] else "",
           "link": f"{base}/{group['slug']}/{ev['slug']}" if base else ""}
    return subject, body, ctx


def announce_event(conn, group, ev, note=""):
    """Varsl notifikationslisten om ét event."""
    subject, body, ctx = announce_message(
        conn, group, ev, (db.get_settings(conn)["base_url"] or "").strip())
    return send_to_list(conn, group, subject, body, ctx, note=note)


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

        # Varsling til notifikationslisten X dage før eventet ("der er et nyt event").
        # Opdages et event først INDE i vinduet — fx oprettet 3 dage før med 14 dages
        # varsel — sendes varslingen med det samme. Det er meningen: pointen er at nå
        # ud, ikke at ramme en bestemt dag.
        nl_rows = conn.execute(
            "SELECT * FROM events WHERE notify_list = 1 AND notify_list_sent = 0"
        ).fetchall()
        for ev in nl_rows:
            group = conn.execute(
                "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
            if not group or not group["notify_list_enabled"]:
                continue
            try:
                start = datetime.strptime(
                    f"{ev['event_date']} {ev['start_time'] or '00:00'}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            days = max(0, group["notify_list_days"] if group["notify_list_days"] is not None else 14)
            if now < start - timedelta(days=days):
                continue  # endnu ikke tid
            if now <= start:
                announce_event(conn, group, ev, note=f"{days} dage før")
            # Uanset om der blev sendt: markér som afsendt. Et event, der allerede er
            # begyndt, skal ikke varsles — og slet ikke tjekkes igen hvert 10. minut.
            conn.execute("UPDATE events SET notify_list_sent = 1 WHERE id = ?", (ev["id"],))
            conn.commit()

        # Påmindelse 24t før SELVE eventet (til dem der er tilmeldt, ikke afbud/venteliste)
        er_rows = conn.execute(
            "SELECT * FROM events WHERE notify_event_reminder = 1 AND event_reminder_sent = 0"
        ).fetchall()
        for ev in er_rows:
            try:
                start = datetime.strptime(
                    f"{ev['event_date']} {ev['start_time'] or '00:00'}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if now <= start <= now + timedelta(hours=24):
                group = conn.execute(
                    "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
                regs = conn.execute(
                    "SELECT * FROM registrations WHERE event_id = ? AND waitlist = 0",
                    (ev["id"],)).fetchall()
                for r in regs:
                    if _reg_declined(conn, group["id"], r["id"]):
                        continue
                    ctx = {"event": ev["name"], "name": r["name"], "date": ev["event_date"],
                           "group": group["name"], "deadline": ev["signup_deadline"],
                           "start": f" kl. {ev['start_time']}" if ev["start_time"] else ""}
                    subject, body = render_message(conn, group, "event_reminder", ctx)
                    notify_participant(conn, group, r["email"], r["phone"], subject, body)
                conn.execute("UPDATE events SET event_reminder_sent = 1 WHERE id = ?",
                             (ev["id"],))
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
