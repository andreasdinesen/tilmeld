"""E-mail (SMTP), WhatsApp (HTTP-bro/gateway), SMS (Gigahost) og påmindelses-scheduler.

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
import gigasms
import push as webpush

# Sættes af app.py: funktion (conn, group, event) -> csv-tekst. Undgår cirkulær import.
csv_builder = None

# Standard-mail-skabeloner. Admin kan overskrive dem pr. gruppe (hvis master tillader).
# Pladsholdere: {event} {name} {date} {group} {deadline} {start} {link}
# Kun i »catering« og »deadline«: {count} {meals} {no_meals} {signups} {waitlist}
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
    # Madbestilling: går til den, der skal bestille maden — ikke til deltagerne.
    # Pladsholderne {meals}/{count}/{no_meals} findes KUN her og i »deadline«;
    # de andre skabeloner har ingen tal at sætte ind.
    "catering": ("Madbestilling: {event} d. {date}",
                 "Tilmeldingen til {event} d. {date}{start} er lukket.\n"
                 "Der skal bestilles mad til {meals}.\n"
                 "I alt {count} deltagere inkl. gæster, heraf {no_meals} uden mad."),
    # Uden »Hej {name}«: varslingen går også til modtagere UDEN navn — en adresse
    # admin har tastet ind, eller en telefon der har abonneret. »Hej .« er værre
    # end ingen hilsen. Admin kan selv sætte {name} ind, hvis listen har navne.
    "event_announce": ("Nyt event i {group}: {event}",
                       "{event} afholdes d. {date}{start}.\n"
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
    """(mail, whatsapp, sms, push): kan gruppen reelt sende? Hver kanal kræver BÅDE
    global opsætning og at master har slået den til for gruppen. `app.group_channels`
    kalder den her, så UI og afsendelse aldrig kan være uenige om, hvad der er aktiveret.

    SMS' »globale opsætning« er brugernavn + API-adgangskode + afsendernummer hos
    Gigahost. Alle tre skal være der: uden et verificeret afsendernummer svarer
    gatewayen 403, og så ville kanalen se aktiv ud og aldrig levere noget.

    Push' »globale opsætning« er den offentlige URL. VAPID-nøglerne laver appen selv,
    men nyttelasten skal indeholde ABSOLUTTE adresser — en relativ adresse får en
    streng parser til at kassere hele notifikationen (doda, 07-09-2026). Uden en
    offentlig URL er der ikke noget at gøre dem absolutte med.
    """
    s = db.get_settings(conn)
    return (bool(s["smtp_host"]) and bool(group["mail_enabled"]),
            bool(s["whatsapp_api_url"]) and bool(group["whatsapp_enabled"]),
            gigasms.configured(s) and bool(group["sms_enabled"]),
            bool((s["base_url"] or "").strip()) and bool(group["push_enabled"]))


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


def send_sms(settings, to: str, body: str) -> str:
    """Send en SMS via Gigahosts Message Delivery Service. Returnér "" hvis sendt,
    ellers en kort fejl-/årsagstekst.

    Selve API'et bor i `gigasms.py`. Her ligger kun det, notifikationslaget skal
    kunne: fejl må aldrig vælte en tilmelding, og en besked, der ikke kan sendes,
    skal stadig kunne ses i konsollen under lokal test.

    En SMS koster penge pr. afsendt del, så `gigasms.prepare()` holder teksten
    indenfor 3 dele og oversætter de typografiske tegn, skabelonerne bruger.
    """
    if not to:
        return "ingen modtager"
    if not gigasms.configured(settings):
        _log("SMS", to, "(sms)", body)
        return "SMS ikke konfigureret"
    try:
        gigasms.send(settings["sms_username"], settings["sms_password"],
                     settings["sms_sender"], to, body, tag="Tilmeld")
        return ""
    except Exception as e:  # robust: en notifikation må aldrig vælte en tilmelding
        print(f"[SMS-FEJL] {e}", flush=True)
        _log("SMS", to, "(sms)", body)
        return str(e)[:300]


def _reg_ticked(conn, group_id, reg_id, kolonne: str) -> bool:
    """Har tilmeldingen krydset et felt af, der er mærket med `kolonne`?

    `kolonne` er `is_decline` (»deltager ikke«) eller `is_meal_decline`
    (»spiser ikke med«). Begge er checkbokse, og et kryds gemmes som 'Ja'.
    """
    ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM group_fields WHERE group_id = ? AND {kolonne} = 1",
        (group_id,)).fetchall()]
    if not ids:
        return False
    ph = ",".join("?" * len(ids))
    return bool(conn.execute(
        f"SELECT 1 FROM registration_values WHERE registration_id = ? "
        f"AND field_id IN ({ph}) AND value = 'Ja' LIMIT 1", [reg_id] + ids).fetchone())


def _reg_declined(conn, group_id, reg_id) -> bool:
    """Har tilmeldingen krydset et 'deltager ikke'-felt af?"""
    return _reg_ticked(conn, group_id, reg_id, "is_decline")


def event_counts(conn, group, ev) -> dict:
    """Tallene for ét event — de samme, som skabelonerne kan sætte ind.

        count     deltagere + gæster (summen af pladser, uden afbud og venteliste)
        signups   antal tilmeldinger bag `count` (personer, ikke pladser)
        meals     hvor mange der skal have mad
        no_meals  hvor mange af `count` der har meldt fra til spisning
        waitlist  antal på venteliste
        declined  antal afbud

    »Spiser ikke med« gælder HELE tilmeldingen — også dens gæster. Krydser man af
    for sig selv og to gæster, er det tre kuverter færre. Det er den eneste
    fortolkning, der kan tastes entydigt i en checkboks; skal gæsterne kunne skille
    sig ud, hører det til som et selvstændigt punkt.
    """
    regs = conn.execute("SELECT * FROM registrations WHERE event_id = ?",
                        (ev["id"],)).fetchall()
    tal = dict(count=0, signups=0, meals=0, no_meals=0, waitlist=0, declined=0)
    for r in regs:
        if r["waitlist"]:
            tal["waitlist"] += 1
            continue
        if _reg_ticked(conn, group["id"], r["id"], "is_decline"):
            tal["declined"] += 1
            continue
        pladser = max(1, r["seats"] or 1)
        tal["count"] += pladser
        tal["signups"] += 1
        if _reg_ticked(conn, group["id"], r["id"], "is_meal_decline"):
            tal["no_meals"] += pladser
        else:
            tal["meals"] += pladser
    return tal


def _note(err: str) -> str:
    return f"  ⚠ ikke leveret ({err})" if err else ""


# ---- Push: den tredje kanal ---------------------------------------------------

def group_url(conn, group, path: str = "") -> str:
    """Absolut adresse ind i gruppen. Push-nyttelasten SKAL bruge absolutte
    adresser — se `push.build_payload`."""
    base = (db.get_settings(conn)["base_url"] or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{group['slug']}" + (f"/{path.lstrip('/')}" if path else "")


def push_targets(conn, group, scope: str, user_id=None):
    """Enhederne der skal have en push. Én række pr. ENHED — samme person har
    typisk både en telefon og en bærbar, og begge skal have besked."""
    if scope == "user":
        if not user_id:
            return []
        return conn.execute(
            "SELECT * FROM push_subscriptions WHERE group_id = ? AND scope = 'user' "
            "AND user_id = ?", (group["id"], user_id)).fetchall()
    return conn.execute(
        "SELECT * FROM push_subscriptions WHERE group_id = ? AND scope = ?",
        (group["id"], scope)).fetchall()


def send_push(conn, group, subs, subject: str, body: str, link: str = "") -> int:
    """Send til en række enheder. Returnér antal der tog imod.

    Døde abonnementer (404/410) slettes med det samme: ellers vokser en liste af
    endepunkter, der koster et HTTPS-kald hver gang der sker noget.
    """
    if not subs:
        return 0
    url = link or group_url(conn, group)
    icon = ""
    base = (db.get_settings(conn)["base_url"] or "").strip().rstrip("/")
    if base:
        icon = f"{base}/static/icon-192.png"
    contact = base or "mailto:tilmeld@invalid"
    sent = 0
    for sub in subs:
        r = webpush.send(conn, sub, subject, body, url, icon, contact)
        if r["ok"]:
            sent += 1
            conn.execute("UPDATE push_subscriptions SET last_ok = ?, fails = 0 WHERE id = ?",
                         (db.now_iso(), sub["id"]))
        elif r["gone"]:
            conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
        else:
            conn.execute("UPDATE push_subscriptions SET fails = fails + 1 WHERE id = ?",
                         (sub["id"],))
    conn.commit()
    return sent


def notify_admin(conn, group, subject: str, body: str, link: str = "") -> None:
    """Send til gruppe-admin via de kanaler master har slået til."""
    settings = db.get_settings(conn)
    mail_on, wa_on, sms_on, push_on = channels(conn, group)
    if group["mail_enabled"] and group["admin_email"]:
        err = send_email(settings, group["admin_email"], subject, body)
        db.add_log(conn, "mail",
                   f"Mail til {group['admin_email']}: {subject}{_note(err)}", group["slug"])
    if group["whatsapp_enabled"] and group["whatsapp_recipient"]:
        err = send_whatsapp(settings, group["whatsapp_recipient"], f"{subject}: {body}")
        db.add_log(conn, "whatsapp",
                   f"WhatsApp til {group['whatsapp_recipient']}: {subject}{_note(err)}",
                   group["slug"])
    # SMS har sit eget modtagerfelt: `whatsapp_recipient` kan være en gruppechat,
    # og et gruppe-id kan man ikke sende en SMS til.
    if group["sms_enabled"] and group["sms_recipient"]:
        err = send_sms(settings, group["sms_recipient"], f"{subject}: {body}")
        db.add_log(conn, "sms",
                   f"SMS til {group['sms_recipient']}: {subject}{_note(err)}",
                   group["slug"])
    if push_on:
        n = send_push(conn, group, push_targets(conn, group, "admin"), subject, body, link)
        if n:
            db.add_log(conn, "push", f"Push til admin ({n} enhed(er)): {subject}",
                       group["slug"])


def notify_participant(conn, group, email: str, whatsapp: str, subject: str, body: str,
                       user_id=None, link: str = "") -> None:
    settings = db.get_settings(conn)
    mail_on, wa_on, sms_on, push_on = channels(conn, group)
    if group["mail_enabled"] and email:
        err = send_email(settings, email, subject, body)
        db.add_log(conn, "mail", f"Mail til {email}: {subject}{_note(err)}", group["slug"])
    # Ét mobilnummer, to kanaler: er både WhatsApp og SMS slået til for gruppen,
    # får deltageren begge dele. Det er master, der vælger kanalerne pr. gruppe.
    if group["whatsapp_enabled"] and whatsapp:
        err = send_whatsapp(settings, whatsapp, f"{subject}: {body}")
        db.add_log(conn, "whatsapp", f"WhatsApp til {whatsapp}: {subject}{_note(err)}",
                   group["slug"])
    if group["sms_enabled"] and whatsapp:
        err = send_sms(settings, whatsapp, f"{subject}: {body}")
        db.add_log(conn, "sms", f"SMS til {whatsapp}: {subject}{_note(err)}",
                   group["slug"])
    # Push følger BRUGERKONTOEN, ikke tilmeldingen: en deltager uden konto har
    # ingen identitet at binde en enhed til. De grupper når push gennem
    # notifikationslisten i stedet.
    if push_on and user_id:
        n = send_push(conn, group, push_targets(conn, group, "user", user_id),
                      subject, body, link)
        if n:
            db.add_log(conn, "push", f"Push til bruger ({n} enhed(er)): {subject}",
                       group["slug"])


# ---- Madbestilling: én besked, én modtager, når tilmeldingen er lukket --------

def catering_contact(group, ev) -> tuple:
    """(mail, mobilnummer) til madbestilleren. Eventet vinder over gruppens standard.

    De to felter vælges HVER FOR SIG: en gruppe kan have en fast mailmodtager og
    et enkelt event en anden telefon, uden at det ene felt slår det andet ihjel.
    """
    return ((ev["catering_email"] or "").strip() or (group["catering_email"] or "").strip(),
            (ev["catering_phone"] or "").strip() or (group["catering_phone"] or "").strip())


def catering_message(conn, group, ev) -> tuple:
    """(emne, tekst, tal) til madbestillingen. Tallene tælles på stedet."""
    tal = event_counts(conn, group, ev)
    ctx = dict(tal, event=ev["name"], date=ev["event_date"], group=group["name"],
               deadline=ev["signup_deadline"] or "ingen",
               start=f" kl. {ev['start_time']}" if ev["start_time"] else "")
    subject, body = render_message(conn, group, "catering", ctx)
    return subject, body, tal


def notify_catering(conn, group, ev, note="") -> tuple:
    """Send madbestillingen. Returnér (antal kanaler der tog imod, fejl-liste).

    Modtageren er ÉN person — den der bestiller maden — ikke deltagerne og ikke
    notifikationslisten. Derfor sin egen funktion: kanalerne er de samme, men
    adressen kommer et andet sted fra.
    """
    settings = db.get_settings(conn)
    email, phone = catering_contact(group, ev)
    if not email and not phone:
        return 0, ["ingen madbestiller sat på eventet eller gruppen"]

    subject, body, tal = catering_message(conn, group, ev)
    suffix = f" ({note})" if note else ""
    sendt, errors = 0, []

    def _kanal(kanal, modtager, err, etiket):
        nonlocal sendt
        if err:
            errors.append(f"{modtager}: {err}")
        else:
            sendt += 1
        db.add_log(conn, kanal,
                   f"Madbestilling{suffix} til {modtager} ({etiket}): "
                   f"{ev['name']} — mad til {tal['meals']}{_note(err)}", group["slug"])

    if group["mail_enabled"] and email:
        _kanal("mail", email, send_email(settings, email, subject, body), "mail")
    if group["sms_enabled"] and phone:
        _kanal("sms", phone, send_sms(settings, phone, f"{subject}: {body}"), "SMS")
    if group["whatsapp_enabled"] and phone:
        _kanal("whatsapp", phone, send_whatsapp(settings, phone, f"{subject}: {body}"),
               "WhatsApp")
    if not sendt and not errors:
        errors.append("hverken mail, SMS eller WhatsApp er slået til for gruppen")
    return sendt, errors


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
    """Send én besked til hele notifikationslisten.
    Returnér (mails, whatsapps, sms'er, push, fejl).

    `subject` og `body` er RÅ skabelontekst — pladsholderne udfyldes her, én gang pr.
    modtager, så {name} bliver modtagerens eget navn. Kanalerne følger det, master har
    aktiveret for gruppen: er mail slået fra, sendes der ikke mail, uanset hvad der står
    i listen.

    Modtagerens mobilnummer bruges af BÅDE WhatsApp og SMS — det er ét nummer, og
    hvilke kanaler det nås på, er masters valg pr. gruppe. Er begge slået til, får
    modtageren begge dele; det er bevidst, ligesom mail ved siden af WhatsApp.
    """
    mail_on, wa_on, sms_on, push_on = channels(conn, group)
    settings = db.get_settings(conn)
    mails = whatsapps = smses = 0
    errors = []
    suffix = f" ({note})" if note else ""

    def _fejl(kanal, modtager, err):
        """Notér en fejl: én til flash-beskeden, én til aktivitetsloggen.

        Loggen får den RIGTIGE kanal som kategori — ellers kan man ikke filtrere
        på SMS og se, at det var SMS'erne, der ikke kom af sted. Og højst 10
        linjer: en lang liste med en død udbyder ville ellers fylde hele loggen.
        """
        errors.append(f"{modtager}: {err}")
        if len(errors) <= 10:
            db.add_log(conn, kanal,
                       f"Notifikationsliste{suffix} ikke leveret — {modtager}: {err}",
                       group["slug"])

    for r in list_recipients(conn, group):
        if not r["active"]:
            continue
        c = dict(ctx or {}, name=r["name"])
        subj, text = _safe_format(subject, c), _safe_format(body, c)
        if mail_on and r["email"]:
            err = send_email(settings, r["email"], subj, text)
            if err:
                _fejl("mail", r["email"], err)
            else:
                mails += 1
        if wa_on and r["whatsapp"]:
            err = send_whatsapp(settings, r["whatsapp"], f"{subj}: {text}")
            if err:
                _fejl("whatsapp", f"{r['whatsapp']} (WhatsApp)", err)
            else:
                whatsapps += 1
        if sms_on and r["whatsapp"]:
            err = send_sms(settings, r["whatsapp"], f"{subj}: {text}")
            if err:
                _fejl("sms", f"{r['whatsapp']} (SMS)", err)
            else:
                smses += 1

    # Push til de enheder, der har abonneret på gruppens varslinger. De har ingen
    # identitet — det ER pointen: en gruppe med delt adgangskode har ingen brugere
    # at hænge et abonnement på, men kan stadig sige »giv mig besked om nye events«.
    pushes = 0
    if push_on:
        subj = _safe_format(subject, dict(ctx or {}, name=""))
        text = _safe_format(body, dict(ctx or {}, name=""))
        pushes = send_push(conn, group, push_targets(conn, group, "list"), subj, text,
                           (ctx or {}).get("link", ""))

    # Én logline for hele udsendelsen — ikke én pr. modtager. Listen kan være lang,
    # og aktivitetsloggen skal stadig kunne læses. Kategorien er den kanal, der nåede
    # flest — loggen kan filtreres på den, og linjen rummer alle fire tal alligevel.
    if mails or whatsapps or smses or pushes:
        kategori = max((mails, "mail"), (whatsapps, "whatsapp"), (smses, "sms"),
                       (pushes, "push"), key=lambda t: t[0])[1]
        db.add_log(conn, kategori,
                   f"Notifikationsliste{suffix}: {mails} mail, {whatsapps} WhatsApp, "
                   f"{smses} SMS, {pushes} push — "
                   f"»{_safe_format(subject, dict(ctx or {}, name=''))}«",
                   group["slug"])
    return mails, whatsapps, smses, pushes, errors


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

        # Madbestilling når fristen er nået: ÉN besked til den, der bestiller maden,
        # med antallet af kuverter. Står for sig selv ved siden af »frist nået«-
        # beskeden til admin, fordi modtageren er en anden og teksten er en anden.
        cat_rows = conn.execute(
            "SELECT * FROM events WHERE notify_catering = 1 AND catering_sent = 0 "
            "AND signup_deadline != ''").fetchall()
        for ev in cat_rows:
            try:
                deadline = datetime.fromisoformat(ev["signup_deadline"])
            except ValueError:
                continue
            if now >= deadline:
                group = conn.execute(
                    "SELECT * FROM groups WHERE id = ?", (ev["group_id"],)).fetchone()
                if group:
                    notify_catering(conn, group, ev, note="frist nået")
                # Markér som sendt uanset udfald. En madbestilling, der ikke kunne
                # leveres, skal fejle ÉN gang og stå i loggen — ikke prøve igen hvert
                # 10. minut og sende maden af sted tre dage senere.
                conn.execute("UPDATE events SET catering_sent = 1 WHERE id = ?", (ev["id"],))
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
