"""Tilmeld - event-tilmeldingssystem (bruger / gruppe-admin / master-admin)."""
import csv
import io
import json
import os
import secrets
import time
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

# Tidszone: alle datoer/frister er "vægur-tid". Uden dette kører containeren i UTC,
# så en frist kl. 12:00 ville reelt være 14:00 dansk tid. Sættes før datetime bruges.
os.environ.setdefault("TZ", "Europe/Copenhagen")
try:
    time.tzset()
except AttributeError:  # findes ikke på Windows
    pass

import bleach
import markdown as markdown_lib
from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, session, url_for)
from markupsafe import Markup
from werkzeug.utils import secure_filename

import auth
import db
import notifications
import passkeys
import system_info

# Tilladte HTML-tags i renderet Markdown (alt andet fjernes, så en beskrivelse
# ikke kan injicere fx <script> hos brugerne).
_MD_TAGS = ["p", "br", "hr", "strong", "em", "b", "i", "u", "del", "a",
            "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
            "blockquote", "code", "pre", "span",
            "table", "thead", "tbody", "tr", "th", "td"]
_MD_ATTRS = {"a": ["href", "title"]}

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__)
# SameSite=Lax er browsernes standard i forvejen, men sæt den eksplicit: sammen med
# kravet om Content-Type: application/json på webauthn-endpointsene er den CSRF-spærren.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db.init_db()
_conn = db.get_db()
app.secret_key = db.get_settings(_conn)["secret_key"]
_conn.close()


# --------------------------------------------------------------------------- #
# Hjælpefunktioner
# --------------------------------------------------------------------------- #
def get_group(slug):
    conn = db.get_db()
    g = conn.execute("SELECT * FROM groups WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return g


def event_state(ev):
    """'open' (kan tilmelde), 'locked' (efter frist, vises i anden farve),
    'finished' (afholdt, skjules for brugere)."""
    now = datetime.now()
    end = ev["end_time"] or "23:59"
    try:
        end_dt = datetime.strptime(f"{ev['event_date']} {end}", "%Y-%m-%d %H:%M")
        if now > end_dt:
            return "finished"
    except ValueError:
        pass
    if ev["signup_deadline"]:
        try:
            if now > datetime.fromisoformat(ev["signup_deadline"]):
                return "locked"
        except ValueError:
            pass
    return "open"


def event_sort_key(ev):
    try:
        return datetime.strptime(ev["event_date"], "%Y-%m-%d")
    except ValueError:
        return datetime.max


def count_registrations(conn, event_id):
    return conn.execute(
        "SELECT COUNT(*) AS c FROM registrations WHERE event_id = ?", (event_id,)
    ).fetchone()["c"]


def all_group_fields(conn, group_id):
    return conn.execute(
        "SELECT * FROM group_fields WHERE group_id = ? ORDER BY sort_order, id",
        (group_id,)).fetchall()


def group_channels(conn, group):
    """Kan gruppen reelt sende mail/WhatsApp? Kræver global opsætning OG at master har
    aktiveret kanalen for gruppen. Bruges til at skjule felter/valg når intet er sat op."""
    s = db.get_settings(conn)
    mail = bool(s["smtp_host"]) and bool(group["mail_enabled"])
    whatsapp = bool(s["whatsapp_api_url"]) and bool(group["whatsapp_enabled"])
    return mail, whatsapp


def is_declined(conn, group_id, reg_id):
    """Har tilmeldingen sat et 'deltager ikke'-felt?"""
    decline_ids = [f["id"] for f in all_group_fields(conn, group_id) if f["is_decline"]]
    if not decline_ids:
        return False
    ph = ",".join("?" * len(decline_ids))
    return bool(conn.execute(
        f"SELECT 1 FROM registration_values WHERE registration_id = ? "
        f"AND field_id IN ({ph}) AND value = 'Ja' LIMIT 1",
        [reg_id] + decline_ids).fetchone())


def count_attending(conn, group_id, event_id, exclude_reg_id=None):
    """Antal optagne pladser: summen af 'seats' for dem der reelt deltager
    (ekskl. afbud og ekskl. venteliste)."""
    decline_ids = [f["id"] for f in all_group_fields(conn, group_id) if f["is_decline"]]
    regs = conn.execute(
        "SELECT id, seats, waitlist FROM registrations WHERE event_id = ?",
        (event_id,)).fetchall()
    n = 0
    for r in regs:
        if exclude_reg_id and r["id"] == exclude_reg_id:
            continue
        if r["waitlist"]:
            continue
        if decline_ids:
            ph = ",".join("?" * len(decline_ids))
            declined = conn.execute(
                f"SELECT 1 FROM registration_values WHERE registration_id = ? "
                f"AND field_id IN ({ph}) AND value = 'Ja' LIMIT 1",
                [r["id"]] + decline_ids).fetchone()
            if declined:
                continue
        n += max(1, r["seats"] or 1)
    return n


def waitlist_position(conn, event_id, reg_id):
    """Nummer på ventelisten (1 = først)."""
    rows = conn.execute(
        "SELECT id FROM registrations WHERE event_id = ? AND waitlist = 1 "
        "ORDER BY created_at, id", (event_id,)).fetchall()
    for i, r in enumerate(rows, start=1):
        if r["id"] == reg_id:
            return i
    return len(rows)


def promote_waitlist(conn, group, ev):
    """Ryk folk op fra ventelisten hvis der er blevet plads (FIFO). Returnér de oprykkede."""
    if not (ev["capacity_limit"] and ev["expected_count"] and ev["waitlist_enabled"]):
        return []
    promoted = []
    while True:
        nxt = conn.execute(
            "SELECT * FROM registrations WHERE event_id = ? AND waitlist = 1 "
            "ORDER BY created_at, id LIMIT 1", (ev["id"],)).fetchone()
        if not nxt:
            break
        taken = count_attending(conn, group["id"], ev["id"])
        if taken + max(1, nxt["seats"] or 1) > ev["expected_count"]:
            break  # ikke plads til den næste — bevar rækkefølgen
        conn.execute("UPDATE registrations SET waitlist = 0 WHERE id = ?", (nxt["id"],))
        conn.commit()
        promoted.append(nxt)
    return promoted


def notify_promoted(conn, group, ev, promoted):
    """Send besked til dem der er rykket op fra ventelisten."""
    for r in promoted:
        ctx = {"event": ev["name"], "name": r["name"], "date": ev["event_date"],
               "group": group["name"], "deadline": ev["signup_deadline"]}
        subj, body = notifications.render_message(conn, group, "waitlist_promoted", ctx)
        notifications.notify_participant(conn, group, r["email"], r["phone"], subj, body)
        db.add_log(conn, "signup", f"{r['name']} rykket op fra ventelisten til {ev['name']}",
                   group["slug"])


def hidden_field_ids(conn, event_id):
    rows = conn.execute(
        "SELECT field_id FROM event_hidden_fields WHERE event_id = ?", (event_id,)).fetchall()
    return {r["field_id"] for r in rows}


def visible_fields(conn, group_id, event_id):
    """Gruppens punkter minus dem der er skjult på dette event."""
    hidden = hidden_field_ids(conn, event_id)
    return [f for f in all_group_fields(conn, group_id) if f["id"] not in hidden]


def master_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not session.get("master"):
            return redirect(url_for("master_login"))
        return f(*a, **k)
    return wrapper


def current_user_id(group):
    """Id på den individuelle bruger der er logget ind på gruppen (ellers None)."""
    return session.get(f"uid_{group['slug']}")


def get_user(conn, user_id):
    if not user_id:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def user_has_access(group):
    """Adgang til bruger-siderne. Admin slipper altid ind. Med individuelle konti
    kræves login som bruger; ellers gruppe-password (eller åben gruppe)."""
    if session.get(f"admin_{group['slug']}"):
        return True
    if group["user_accounts_enabled"]:
        return current_user_id(group) is not None
    if not group["user_password"]:
        return True
    return bool(session.get(f"user_{group['slug']}"))


def can_edit_registration(group, reg):
    """Må den nuværende besøgende redigere/fjerne denne tilmelding?"""
    if session.get(f"admin_{group['slug']}"):
        return True  # admin kan altid (fjerne eller oprette på vegne af)
    if group["user_accounts_enabled"]:
        uid = current_user_id(group)
        return uid is not None and reg["user_id"] == uid  # kun sin egen
    return user_has_access(group)  # delt password: betroet gruppe


def admin_has_access(group):
    return bool(session.get(f"admin_{group['slug']}"))


@app.context_processor
def inject_app_version():
    # Bruges som cache-bust på style.css/passkey.js: Cloudflare edge-cacher statiske
    # filer i timevis og ignorerer Cache-Control, så en ny udgivelse skal have en ny URL.
    # Runens version bumpes ved hver udgivelse og er derfor det rigtige tal.
    return {"app_version": system_info.rune_version()}


@app.template_filter("dt")
def fmt_dt(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return value


@app.template_filter("d")
def fmt_d(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return value


@app.template_filter("md")
def render_markdown(text):
    """Render Markdown til sikker HTML (allowlist-renset)."""
    if not text:
        return ""
    html = markdown_lib.markdown(
        text, extensions=["nl2br", "sane_lists", "fenced_code", "tables"])
    clean = bleach.clean(html, tags=_MD_TAGS, attributes=_MD_ATTRS,
                         protocols=["http", "https", "mailto"], strip=True)
    return Markup(clean)


# --------------------------------------------------------------------------- #
# Forside
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    # Master kan pege forsiden på én gruppe, så et rent domæne (fx kalender.hjorten.eu)
    # lander direkte på den gruppe i stedet for en teknisk oversigt.
    conn = db.get_db()
    slug = (db.get_settings(conn)["default_group"] or "").strip()
    exists = bool(slug) and bool(
        conn.execute("SELECT 1 FROM groups WHERE slug = ?", (slug,)).fetchone())
    conn.close()
    if exists:
        return redirect(url_for("user_home", slug=slug))
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    # Servér vores kalender-ikon på standard-stien, så browsere (og evt. en proxy
    # der ellers viser et andet ikon) får det rigtige favicon.
    return send_file(os.path.join(app.static_folder, "favicon.svg"),
                     mimetype="image/svg+xml")


# --------------------------------------------------------------------------- #
# Master-admin
# --------------------------------------------------------------------------- #
@app.route("/master/login", methods=["GET", "POST"])
def master_login():
    if request.method == "POST":
        conn = db.get_db()
        s = db.get_settings(conn)
        conn.close()
        if auth.verify_password(request.form.get("password", ""), s["master_password_hash"]):
            session["master"] = True
            return redirect(url_for("master_home"))
        flash("Forkert master-password.", "error")
    conn = db.get_db()
    has_keys = bool(passkeys.list_credentials(conn, "master"))
    conn.close()
    return render_template("master/login.html",
                           passkey_on=has_keys and passkeys.is_secure_origin(request))


@app.route("/master/logout")
def master_logout():
    session.pop("master", None)
    return redirect(url_for("master_login"))


@app.route("/master")
@master_required
def master_home():
    conn = db.get_db()
    groups = conn.execute("SELECT * FROM groups ORDER BY name").fetchall()
    data = []
    for g in groups:
        ev_count = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE group_id = ?", (g["id"],)
        ).fetchone()["c"]
        data.append({"g": g, "events": ev_count})
    start_slug = (db.get_settings(conn)["default_group"] or "").strip()
    start_group = next((g for g in groups if g["slug"] == start_slug), None)
    conn.close()
    return render_template("master/home.html", groups=data, start_group=start_group)


@app.route("/master/groups/new", methods=["GET", "POST"])
@master_required
def master_group_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = auth.slugify(request.form.get("slug", "") or name)
        admin_pw = request.form.get("admin_password", "")
        if not name or not slug:
            flash("Navn og slug er påkrævet.", "error")
        elif not auth.is_valid_slug(slug):
            flash(f"Ugyldig eller reserveret slug: '{slug}'.", "error")
        elif not admin_pw:
            flash("Admin-password er påkrævet.", "error")
        elif get_group(slug):
            flash("En gruppe med den slug findes allerede.", "error")
        else:
            conn = db.get_db()
            conn.execute(
                "INSERT INTO groups (slug, name, user_password, admin_password_hash, "
                "mail_enabled, whatsapp_enabled, admin_email, whatsapp_recipient, "
                "templates_enabled, user_accounts_enabled, calendar_token, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (slug, name, request.form.get("user_password", ""),
                 auth.hash_password(admin_pw),
                 1 if request.form.get("mail_enabled") else 0,
                 1 if request.form.get("whatsapp_enabled") else 0,
                 request.form.get("admin_email", "").strip(),
                 request.form.get("whatsapp_recipient", "").strip(),
                 1 if request.form.get("templates_enabled") else 0,
                 1 if request.form.get("user_accounts_enabled") else 0,
                 secrets.token_urlsafe(16),
                 db.now_iso()),
            )
            conn.commit()
            db.add_log(conn, "group", f"Gruppe '{name}' oprettet (/{slug})", slug)
            conn.close()
            flash(f"Gruppe '{name}' oprettet.", "ok")
            return redirect(url_for("master_home"))
    return render_template("master/group_new.html")


@app.route("/master/groups/<slug>/toggle", methods=["POST"])
@master_required
def master_group_toggle(slug):
    g = get_group(slug)
    if not g:
        abort(404)
    conn = db.get_db()
    conn.execute(
        "UPDATE groups SET mail_enabled = ?, whatsapp_enabled = ?, templates_enabled = ?, "
        "user_accounts_enabled = ? WHERE id = ?",
        (1 if request.form.get("mail_enabled") else 0,
         1 if request.form.get("whatsapp_enabled") else 0,
         1 if request.form.get("templates_enabled") else 0,
         1 if request.form.get("user_accounts_enabled") else 0, g["id"]),
    )
    conn.commit()
    conn.close()
    flash("Notifikationsindstillinger gemt.", "ok")
    return redirect(url_for("master_home"))


@app.route("/master/groups/<slug>/delete", methods=["POST"])
@master_required
def master_group_delete(slug):
    g = get_group(slug)
    if not g:
        abort(404)
    conn = db.get_db()
    conn.execute("DELETE FROM groups WHERE id = ?", (g["id"],))
    conn.commit()
    db.add_log(conn, "group", f"Gruppe '{g['name']}' slettet", slug)
    conn.close()
    flash(f"Gruppe '{g['name']}' slettet.", "ok")
    return redirect(url_for("master_home"))


@app.route("/master/log")
@master_required
def master_log():
    conn = db.get_db()
    category = request.args.get("category", "")
    if category:
        entries = conn.execute(
            "SELECT * FROM activity_log WHERE category = ? ORDER BY id DESC LIMIT 500",
            (category,)).fetchall()
    else:
        entries = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT 500").fetchall()
    conn.close()
    return render_template("master/log.html", entries=entries, category=category)


@app.route("/master/log/clear", methods=["POST"])
@master_required
def master_log_clear():
    conn = db.get_db()
    conn.execute("DELETE FROM activity_log")
    conn.commit()
    conn.close()
    flash("Aktivitetsloggen er ryddet.", "ok")
    return redirect(url_for("master_log"))


@app.route("/master/whatsapp/groups")
@master_required
def master_whatsapp_groups():
    """Hent WhatsApp-gruppe-id'er fra broen (GET <base>/groups med Bearer-nøgle)."""
    conn = db.get_db()
    s = db.get_settings(conn)
    conn.close()
    api = (s["whatsapp_api_url"] or "").rstrip("/")
    if not api:
        return render_template("master/whatsapp_groups.html",
                               error="WhatsApp er ikke sat op endnu.", groups=None, url="")
    base = api[:-5] if api.endswith("/send") else api.rsplit("/", 1)[0]
    groups_url = base + "/groups"
    req = urllib.request.Request(groups_url)
    if s["whatsapp_api_key"]:
        req.add_header("Authorization", "Bearer " + s["whatsapp_api_key"])
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return render_template("master/whatsapp_groups.html",
                               error=f"Kunne ikke hente grupper fra {groups_url}: {e}",
                               groups=None, url=groups_url)
    items = data.get("groups") if isinstance(data, dict) else data
    groups = [{"id": str(g.get("id", "")), "name": str(g.get("name", ""))}
              for g in (items or []) if isinstance(g, dict)]
    return render_template("master/whatsapp_groups.html", groups=groups, error=None,
                           url=groups_url)


@app.route("/master/settings", methods=["GET", "POST"])
@master_required
def master_settings():
    conn = db.get_db()
    if request.method == "POST":
        if request.form.get("new_master_password"):
            conn.execute(
                "UPDATE settings SET master_password_hash = ? WHERE id = 1",
                (auth.hash_password(request.form["new_master_password"]),),
            )
        conn.execute(
            "UPDATE settings SET smtp_host=?, smtp_port=?, smtp_user=?, smtp_password=?, "
            "smtp_from=?, smtp_use_tls=?, whatsapp_api_url=?, whatsapp_api_key=?, "
            "base_url=?, default_deadline_days=?, github_repo=?, update_branch=? WHERE id = 1",
            (request.form.get("smtp_host", "").strip(),
             int(request.form.get("smtp_port") or 587),
             request.form.get("smtp_user", "").strip(),
             request.form.get("smtp_password", ""),
             request.form.get("smtp_from", "").strip(),
             1 if request.form.get("smtp_use_tls") else 0,
             request.form.get("whatsapp_api_url", "").strip(),
             request.form.get("whatsapp_api_key", "").strip(),
             request.form.get("base_url", "").strip(),
             int(request.form.get("default_deadline_days") or 4),
             request.form.get("github_repo", "").strip(),
             request.form.get("update_branch", "main").strip() or "main"),
        )
        # Startside-gruppe gemmes kun hvis slug'en findes — ellers ville forsiden pege
        # på en slettet gruppe og give 404.
        start = request.form.get("default_group", "").strip()
        if start and not conn.execute(
                "SELECT 1 FROM groups WHERE slug = ?", (start,)).fetchone():
            start = ""
        conn.execute("UPDATE settings SET default_group = ? WHERE id = 1", (start,))
        conn.commit()
        flash("Indstillinger gemt.", "ok")
    s = db.get_settings(conn)
    groups = conn.execute("SELECT slug, name FROM groups ORDER BY name").fetchall()
    creds = passkeys.list_credentials(conn, "master")
    conn.close()
    return render_template("master/settings.html", s=s, groups=groups, creds=creds,
                           passkey_blocked=passkeys.blocked_reason(request))


@app.route("/master/system", methods=["GET", "POST"])
@master_required
def master_system():
    conn = db.get_db()
    s = db.get_settings(conn)
    conn.close()
    update_log = None
    if request.method == "POST" and request.form.get("action") == "update_deps":
        update_log = system_info.update_dependencies()
    version = system_info.rune_version()
    return render_template(
        "master/system.html", s=s,
        components=system_info.component_versions(),
        version=version,
        changelog_url=system_info.changelog_url(
            s["github_repo"], s["update_branch"], version),
        update_log=update_log)


# --------------------------------------------------------------------------- #
# Passkeys (WebAuthn) — se passkeys.py. Altid et TILLÆG til adgangskoden.
#
# Alle endpoints er JSON og kræver Content-Type: application/json. Det er samtidig
# CSRF-spærren oven på SameSite=Lax: en formular fra et fremmed site kan ikke sætte
# den content-type uden et preflight, som CORS afviser.
# --------------------------------------------------------------------------- #
def _passkey_target(conn, data, require_login):
    """Slå scope + gruppe/bruger op ud fra JSON-body → (ctx, fejltekst).

    require_login gælder registrering og sletning: man skal allerede være logget ind
    på præcis det scope, man vil lægge en nøgle på. Ved login er svaret nej — det er
    jo dét, nøglen skal bevise.
    """
    if not passkeys.AVAILABLE:
        return None, "Passkeys er ikke tilgængelige på denne installation."
    scope = (data.get("scope") or "").strip()
    slug = (data.get("slug") or "").strip()

    if scope == "master":
        if require_login and not session.get("master"):
            return None, "Ikke logget ind som master."
        return {"scope": "master", "group": None, "group_id": None, "user_id": None,
                "label": "Master-admin"}, ""

    group = get_group(slug) if slug else None
    if not group:
        return None, "Ukendt gruppe."

    if scope == "admin":
        if require_login and not admin_has_access(group):
            return None, "Ikke logget ind som admin."
        return {"scope": "admin", "group": group, "group_id": group["id"], "user_id": None,
                "label": f"{group['name']} · admin"}, ""

    if scope == "user":
        if not group["user_accounts_enabled"]:
            return None, "Gruppen bruger ikke individuelle brugerkonti."
        uid = session.get(f"uid_{group['slug']}")
        if require_login and not uid:
            return None, "Ikke logget ind."
        label = ""
        if uid:
            u = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
            if not u:
                return None, "Ukendt bruger."
            label = u["username"]
        return {"scope": "user", "group": group, "group_id": None, "user_id": uid,
                "label": label}, ""

    return None, "Ukendt scope."


@app.route("/webauthn/register/options", methods=["POST"])
def webauthn_register_options():
    conn = db.get_db()
    ctx, err = _passkey_target(conn, request.get_json(silent=True) or {}, require_login=True)
    if err:
        conn.close()
        return {"error": err}, 403
    opts = passkeys.registration_options(
        conn, request, session, ctx["scope"], ctx["label"],
        group_id=ctx["group_id"], user_id=ctx["user_id"])
    conn.close()
    return Response(opts, mimetype="application/json")


@app.route("/webauthn/register/verify", methods=["POST"])
def webauthn_register_verify():
    data = request.get_json(silent=True) or {}
    conn = db.get_db()
    ctx, err = _passkey_target(conn, data, require_login=True)
    if err:
        conn.close()
        return {"error": err}, 403
    err = passkeys.registration_verify(
        conn, request, session, data.get("credential") or {}, data.get("name", ""),
        ctx["scope"], group_id=ctx["group_id"], user_id=ctx["user_id"])
    if not err:
        db.add_log(conn, "user", f"Passkey tilføjet ({ctx['label']})",
                   ctx["group"]["slug"] if ctx["group"] else "")
    conn.close()
    return ({"error": err}, 400) if err else {"ok": True}


@app.route("/webauthn/delete", methods=["POST"])
def webauthn_delete():
    data = request.get_json(silent=True) or {}
    conn = db.get_db()
    ctx, err = _passkey_target(conn, data, require_login=True)
    if err:
        conn.close()
        return {"error": err}, 403
    ok = passkeys.delete_credential(conn, int(data.get("id") or 0), ctx["scope"],
                                    group_id=ctx["group_id"], user_id=ctx["user_id"])
    conn.close()
    return {"ok": True} if ok else ({"error": "Nøglen blev ikke fundet."}, 404)


@app.route("/webauthn/login/options", methods=["POST"])
def webauthn_login_options():
    conn = db.get_db()
    ctx, err = _passkey_target(conn, request.get_json(silent=True) or {}, require_login=False)
    if err:
        conn.close()
        return {"error": err}, 400
    opts = passkeys.authentication_options(conn, request, session)
    conn.close()
    return Response(opts, mimetype="application/json")


@app.route("/webauthn/login/verify", methods=["POST"])
def webauthn_login_verify():
    data = request.get_json(silent=True) or {}
    conn = db.get_db()
    ctx, err = _passkey_target(conn, data, require_login=False)
    if err:
        conn.close()
        return {"error": err}, 400

    row, err = passkeys.authentication_verify(
        conn, request, session, data.get("credential") or {},
        ctx["scope"], group_id=ctx["group_id"])
    if err:
        conn.close()
        return {"error": err}, 403

    if ctx["scope"] == "master":
        session["master"] = True
        target = url_for("master_home")
    elif ctx["scope"] == "admin":
        session[f"admin_{ctx['group']['slug']}"] = True
        target = url_for("admin_home", slug=ctx["group"]["slug"])
    else:
        # Nøglen hører til en bruger — men brugeren skal også være med i DENNE gruppe.
        member = conn.execute(
            "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ?",
            (row["user_id"], ctx["group"]["id"])).fetchone()
        if not member:
            conn.close()
            return {"error": "Din bruger er ikke med i denne gruppe."}, 403
        session[f"uid_{ctx['group']['slug']}"] = row["user_id"]
        target = url_for("user_home", slug=ctx["group"]["slug"])
    conn.close()
    return {"ok": True, "redirect": target}


# --------------------------------------------------------------------------- #
# Gruppe-admin
# --------------------------------------------------------------------------- #
@app.route("/<slug>/admin/login", methods=["GET", "POST"])
def admin_login(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if request.method == "POST":
        if auth.verify_password(request.form.get("password", ""), group["admin_password_hash"]):
            session[f"admin_{slug}"] = True
            return redirect(url_for("admin_home", slug=slug))
        flash("Forkert admin-password.", "error")
    conn = db.get_db()
    has_keys = bool(passkeys.list_credentials(conn, "admin", group_id=group["id"]))
    conn.close()
    return render_template("admin/login.html", group=group,
                           passkey_on=has_keys and passkeys.is_secure_origin(request))


@app.route("/<slug>/admin/logout")
def admin_logout(slug):
    session.pop(f"admin_{slug}", None)
    return redirect(url_for("admin_login", slug=slug))


@app.route("/<slug>/admin")
def admin_home(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    events = conn.execute(
        "SELECT * FROM events WHERE group_id = ?", (group["id"],)
    ).fetchall()
    rows, past = [], []
    for ev in sorted(events, key=event_sort_key):
        state = event_state(ev)
        item = {
            "ev": ev,
            "state": state,
            "count": count_attending(conn, group["id"], ev["id"]),
            "total": count_registrations(conn, ev["id"]),
        }
        (past if state == "finished" else rows).append(item)
    past.reverse()  # afholdte: nyeste øverst
    conn.close()
    return render_template("admin/home.html", group=group, events=rows, past=past)


@app.route("/<slug>/admin/settings", methods=["GET", "POST"])
def admin_settings(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "password":
            conn.execute("UPDATE groups SET user_password = ? WHERE id = ?",
                         (request.form.get("user_password", ""), group["id"]))
            flash("Gruppe-password opdateret.", "ok")
        elif action == "delete_password":
            conn.execute("UPDATE groups SET user_password = '' WHERE id = ?", (group["id"],))
            flash("Gruppe-password slettet — bruger-siden er nu åben uden login.", "ok")
        elif action == "contact":
            # Opdatér kun de felter der faktisk blev vist/sendt, så den ene kanal
            # ikke nulstiller den anden.
            if "admin_email" in request.form:
                conn.execute("UPDATE groups SET admin_email = ? WHERE id = ?",
                             (request.form.get("admin_email", "").strip(), group["id"]))
            if "whatsapp_recipient" in request.form:
                conn.execute("UPDATE groups SET whatsapp_recipient = ? WHERE id = ?",
                             (request.form.get("whatsapp_recipient", "").strip(), group["id"]))
            flash("Kontaktoplysninger gemt.", "ok")
        elif action == "add_field":
            is_decline = 1 if request.form.get("is_decline") else 0
            opts = [o.strip() for o in request.form.get("options", "").split(",") if o.strip()]
            chosen = request.form.get("field_type", "text")
            # "Notefelt" gemmes som flerlinjet tekst; "deltager ikke" er altid en checkbox
            multiline = 1 if chosen == "note" else 0
            if is_decline:
                ftype = "checkbox"
            elif chosen == "note":
                ftype = "text"
            else:
                ftype = chosen
            # "Deltager ikke" er aldrig påkrævet; ellers respekteres fluebenet
            required = 0 if is_decline else (1 if request.form.get("required") else 0)
            nxt = (conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM group_fields WHERE group_id = ?",
                (group["id"],)).fetchone()["n"])
            conn.execute(
                "INSERT INTO group_fields (group_id, label, field_type, options, required, "
                "is_decline, multiline, sort_order) VALUES (?,?,?,?,?,?,?,?)",
                (group["id"], request.form.get("label", "").strip(), ftype,
                 json.dumps(opts), required, is_decline, multiline, nxt),
            )
            flash("Punkt tilføjet.", "ok")
        elif action == "delete_field":
            conn.execute("DELETE FROM group_fields WHERE id = ? AND group_id = ?",
                         (request.form.get("field_id"), group["id"]))
            flash("Punkt slettet.", "ok")
        elif action == "move_field":
            _move_field(conn, group["id"], request.form.get("field_id"),
                        request.form.get("direction"))
        elif action == "branding":
            login_text = request.form.get("login_text", "").strip()
            image_path = group["image_path"]
            file = request.files.get("image")
            if file and file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in ALLOWED_IMAGE_EXT:
                    flash("Ugyldigt billedformat (brug png/jpg/gif/webp).", "error")
                else:
                    gdir = os.path.join(db.DATA_DIR, "uploads", group["slug"])
                    os.makedirs(gdir, exist_ok=True)
                    fname = secure_filename("logo" + ext)
                    file.save(os.path.join(gdir, fname))
                    image_path = f"{group['slug']}/{fname}"
            elif request.form.get("remove_image"):
                image_path = ""
            conn.execute("UPDATE groups SET login_text = ?, image_path = ? WHERE id = ?",
                         (login_text, image_path, group["id"]))
            flash("Bruger-side opdateret.", "ok")
        elif action == "templates" and group["templates_enabled"]:
            for tkey in notifications.DEFAULT_TEMPLATES:
                conn.execute(
                    "INSERT INTO mail_templates (group_id, tkey, subject, body) "
                    "VALUES (?,?,?,?) ON CONFLICT(group_id, tkey) DO UPDATE SET "
                    "subject = excluded.subject, body = excluded.body",
                    (group["id"], tkey,
                     request.form.get(f"subject_{tkey}", "").strip(),
                     request.form.get(f"body_{tkey}", "").strip()))
            flash("Mail-skabeloner gemt.", "ok")
        conn.commit()
        group = get_group(slug)
    fields = all_group_fields(conn, group["id"])
    mail_on, wa_on = group_channels(conn, group)
    templates = []
    if group["templates_enabled"]:
        labels = {"new_signup": "Ny tilmelding (til admin)",
                  "change": "Ændret tilmelding (til admin)",
                  "receipt": "Kvittering (til deltager)",
                  "reminder": "Påmindelse før frist",
                  "deadline": "Frist nået (til admin, med link)",
                  "waitlist_promoted": "Rykket op fra venteliste (til deltager)",
                  "event_reminder": "Påmindelse før eventet (til deltager)"}
        for tkey in notifications.DEFAULT_TEMPLATES:
            subj, body = notifications.template_for(conn, group, tkey)
            templates.append({"key": tkey, "label": labels.get(tkey, tkey),
                              "subject": subj, "body": body})
    creds = passkeys.list_credentials(conn, "admin", group_id=group["id"])
    conn.close()
    parsed = [{"f": f, "options": json.loads(f["options"] or "[]")} for f in fields]
    return render_template("admin/settings.html", group=group, fields=parsed,
                           mail_on=mail_on, wa_on=wa_on, templates=templates,
                           creds=creds, passkey_blocked=passkeys.blocked_reason(request))


def _move_field(conn, group_id, field_id, direction):
    """Byt rækkefølge med naboen og renummerér sort_order sekventielt."""
    fields = all_group_fields(conn, group_id)
    ids = [f["id"] for f in fields]
    try:
        idx = ids.index(int(field_id))
    except (ValueError, TypeError):
        return
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(ids):
        ids[idx], ids[swap] = ids[swap], ids[idx]
    for pos, fid in enumerate(ids):
        conn.execute("UPDATE group_fields SET sort_order = ? WHERE id = ?", (pos, fid))


def _render_event_form(group, ev):
    conn = db.get_db()
    fields = all_group_fields(conn, group["id"])
    hidden = hidden_field_ids(conn, ev["id"]) if ev else set()
    days = db.get_settings(conn)["default_deadline_days"]
    mail_on, wa_on = group_channels(conn, group)
    conn.close()
    return render_template("admin/event_form.html", group=group, ev=ev,
                           fields=fields, hidden=hidden, default_deadline_days=days,
                           mail_on=mail_on, wa_on=wa_on)


@app.route("/<slug>/admin/events/new", methods=["GET", "POST"])
def admin_event_new(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    if request.method == "POST":
        return _save_event(group, None)
    return _render_event_form(group, None)


@app.route("/<slug>/admin/events/<int:event_id>/edit", methods=["GET", "POST"])
def admin_event_edit(slug, event_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE id = ? AND group_id = ?",
                      (event_id, group["id"])).fetchone()
    conn.close()
    if not ev:
        abort(404)
    if request.method == "POST":
        return _save_event(group, ev)
    return _render_event_form(group, ev)


def _default_deadline(conn, event_date, start_time):
    """Frist = standard antal dage før event-start (master-indstilling)."""
    if not event_date:
        return ""
    try:
        days = db.get_settings(conn)["default_deadline_days"]
        d = datetime.strptime(event_date, "%Y-%m-%d")
        t = start_time or "12:00"
        dt = datetime.strptime(f"{event_date} {t}", "%Y-%m-%d %H:%M") - timedelta(days=days)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _save_event(group, ev):
    name = request.form.get("name", "").strip()
    slug = auth.slugify(request.form.get("slug", "") or name)
    if not name or not slug:
        flash("Navn er påkrævet.", "error")
        return _render_event_form(group, ev)
    if not auth.is_valid_slug(slug):
        flash(f"Ugyldigt eller reserveret event-navn: '{slug}'.", "error")
        return _render_event_form(group, ev)
    conn = db.get_db()
    dupe = conn.execute(
        "SELECT id FROM events WHERE group_id = ? AND slug = ? AND id != ?",
        (group["id"], slug, ev["id"] if ev else -1)).fetchone()
    if dupe:
        conn.close()
        flash("Et event med det navn findes allerede i gruppen.", "error")
        return _render_event_form(group, ev)

    event_date = request.form.get("event_date", "")
    start_time = request.form.get("start_time", "")
    deadline = request.form.get("signup_deadline", "")
    if not deadline:  # fald tilbage til standard: X dage før start
        deadline = _default_deadline(conn, event_date, start_time)

    vals = (
        name, slug, event_date, start_time, request.form.get("end_time", ""),
        request.form.get("description", ""),
        int(request.form.get("expected_count") or 0),
        deadline,
        1 if request.form.get("notify_new_signup") else 0,
        1 if request.form.get("notify_change") else 0,
        1 if request.form.get("notify_receipt") else 0,
        1 if request.form.get("notify_reminder") else 0,
        1 if request.form.get("csv_after_deadline") else 0,
        1 if request.form.get("capacity_limit") else 0,
        1 if request.form.get("notify_deadline") else 0,
        1 if request.form.get("waitlist_enabled") else 0,
        1 if request.form.get("allow_guests") else 0,
        1 if request.form.get("notify_event_reminder") else 0,
    )
    if ev:
        conn.execute(
            "UPDATE events SET name=?, slug=?, event_date=?, start_time=?, end_time=?, "
            "description=?, expected_count=?, signup_deadline=?, notify_new_signup=?, "
            "notify_change=?, notify_receipt=?, notify_reminder=?, csv_after_deadline=?, "
            "capacity_limit=?, notify_deadline=?, waitlist_enabled=?, allow_guests=?, "
            "notify_event_reminder=? WHERE id = ?",
            vals + (ev["id"],))
        event_id = ev["id"]
        flash("Event opdateret.", "ok")
    else:
        cur = conn.execute(EVENT_INSERT_SQL, vals + (group["id"], db.now_iso()))
        event_id = cur.lastrowid
        db.add_log(conn, "event", f"Event '{name}' oprettet i {group['name']}", group["slug"])
        flash("Event oprettet.", "ok")

    # Gem hvilke punkter der er skjult på dette event (ukrydsede = skjult)
    hidden_ids = [f["id"] for f in all_group_fields(conn, group["id"])
                  if not request.form.get(f"show_field_{f['id']}")]
    _set_hidden_fields(conn, event_id, hidden_ids)
    conn.commit()

    # Gentagne events (kun ved oprettelse)
    if not ev:
        extra = _create_repeats(conn, group, vals, hidden_ids,
                                request.form.get("repeat_interval", ""),
                                request.form.get("repeat_count") or 1)
        if extra:
            flash(f"Oprettede {extra} gentagelser mere.", "ok")
    conn.close()
    return redirect(url_for("admin_home", slug=group["slug"]))


EVENT_INSERT_SQL = (
    "INSERT INTO events (name, slug, event_date, start_time, end_time, description, "
    "expected_count, signup_deadline, notify_new_signup, notify_change, notify_receipt, "
    "notify_reminder, csv_after_deadline, capacity_limit, notify_deadline, "
    "waitlist_enabled, allow_guests, notify_event_reminder, group_id, created_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _set_hidden_fields(conn, event_id, field_ids):
    conn.execute("DELETE FROM event_hidden_fields WHERE event_id = ?", (event_id,))
    for fid in field_ids:
        conn.execute(
            "INSERT OR IGNORE INTO event_hidden_fields (event_id, field_id) VALUES (?,?)",
            (event_id, fid))


def _add_months(d, n):
    import calendar
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _create_repeats(conn, group, vals, hidden_ids, interval, count):
    """Opret gentagelser af et nyoprettet event (ugentligt/hver 14. dag/månedligt)."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    if interval not in ("weekly", "biweekly", "monthly") or count < 2:
        return 0
    count = min(count, 26)
    try:
        base_date = datetime.strptime(vals[2], "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0
    base_deadline = None
    if vals[7]:
        try:
            base_deadline = datetime.fromisoformat(vals[7])
        except ValueError:
            base_deadline = None
    made = 0
    for i in range(1, count):
        if interval == "weekly":
            nd = base_date + timedelta(weeks=i)
        elif interval == "biweekly":
            nd = base_date + timedelta(weeks=2 * i)
        else:
            nd = _add_months(base_date, i)
        delta = (nd - base_date).days
        v = list(vals)
        v[1] = f"{vals[1]}-{i + 1}"
        v[2] = nd.strftime("%Y-%m-%d")
        v[7] = ((base_deadline + timedelta(days=delta)).strftime("%Y-%m-%dT%H:%M")
                if base_deadline else "")
        if conn.execute("SELECT 1 FROM events WHERE group_id = ? AND slug = ?",
                        (group["id"], v[1])).fetchone():
            continue  # slug optaget — spring over
        cur = conn.execute(EVENT_INSERT_SQL, tuple(v) + (group["id"], db.now_iso()))
        _set_hidden_fields(conn, cur.lastrowid, hidden_ids)
        made += 1
    conn.commit()
    if made:
        db.add_log(conn, "event", f"{made} gentagelser af '{vals[0]}' oprettet",
                   group["slug"])
    return made


@app.route("/<slug>/admin/events/<int:event_id>/delete", methods=["POST"])
def admin_event_delete(slug, event_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    conn.execute("DELETE FROM events WHERE id = ? AND group_id = ?", (event_id, group["id"]))
    conn.commit()
    conn.close()
    flash("Event slettet.", "ok")
    return redirect(url_for("admin_home", slug=slug))


@app.route("/<slug>/admin/events/<int:event_id>/copy", methods=["POST"])
def admin_event_copy(slug, event_id):
    """Opret en kopi af et event (samme indstillinger) og åbn den til redigering."""
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE id = ? AND group_id = ?",
                      (event_id, group["id"])).fetchone()
    if not ev:
        conn.close()
        abort(404)
    base = f"{ev['slug']}-kopi"
    nslug, i = base, 2
    while conn.execute("SELECT 1 FROM events WHERE group_id = ? AND slug = ?",
                       (group["id"], nslug)).fetchone():
        nslug, i = f"{base}-{i}", i + 1
    vals = (f"{ev['name']} (kopi)", nslug, ev["event_date"], ev["start_time"], ev["end_time"],
            ev["description"], ev["expected_count"], ev["signup_deadline"],
            ev["notify_new_signup"], ev["notify_change"], ev["notify_receipt"],
            ev["notify_reminder"], ev["csv_after_deadline"], ev["capacity_limit"],
            ev["notify_deadline"], ev["waitlist_enabled"], ev["allow_guests"],
            ev["notify_event_reminder"])
    cur = conn.execute(EVENT_INSERT_SQL, vals + (group["id"], db.now_iso()))
    new_id = cur.lastrowid
    _set_hidden_fields(conn, new_id, list(hidden_field_ids(conn, ev["id"])))
    conn.commit()
    db.add_log(conn, "event", f"Event '{ev['name']}' kopieret", group["slug"])
    conn.close()
    flash("Kopi oprettet — ret dato og navn her.", "ok")
    return redirect(url_for("admin_event_edit", slug=slug, event_id=new_id))


@app.route("/<slug>/admin/events/<int:event_id>/list", methods=["GET", "POST"])
def admin_event_list(slug, event_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE id = ? AND group_id = ?",
                      (event_id, group["id"])).fetchone()
    if not ev:
        conn.close()
        abort(404)
    if request.method == "POST" and request.form.get("action") == "attendance":
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM registrations WHERE event_id = ?", (ev["id"],)).fetchall()]
        for rid in ids:
            conn.execute("UPDATE registrations SET attended = ? WHERE id = ?",
                         (1 if request.form.get(f"att_{rid}") else 0, rid))
        conn.commit()
        flash("Fremmøde gemt.", "ok")
    fields = visible_fields(conn, group["id"], ev["id"])
    regs = _registrations_with_values(conn, ev["id"], fields)
    attending = count_attending(conn, group["id"], ev["id"])
    decline_ids = [f["id"] for f in fields if f["is_decline"]]
    attended_count = sum(1 for r in regs if r["attended"])
    conn.close()
    return render_template("admin/event_list.html", group=group, ev=ev,
                           fields=fields, regs=regs, count=attending,
                           total=len(regs), decline_ids=decline_ids,
                           state=event_state(ev), attended_count=attended_count)


@app.route("/<slug>/admin/events/<int:event_id>/export.csv")
def admin_event_export(slug, event_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE id = ? AND group_id = ?",
                      (event_id, group["id"])).fetchone()
    if not ev:
        conn.close()
        abort(404)
    content = build_csv(conn, group, ev)
    conn.close()
    filename = f"{group['slug']}-{ev['slug']}-deltagere.csv"
    return Response(content, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def build_csv(conn, group, ev):
    """Byg CSV-deltagerliste (BOM + semikolon) ud fra synlige punkter."""
    fields = visible_fields(conn, group["id"], ev["id"])
    regs = _registrations_with_values(conn, ev["id"], fields)
    buf = io.StringIO()
    buf.write("﻿")  # BOM så Excel viser æøå korrekt
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Navn", "E-mail", "WhatsApp", "Pladser", "Status", "Mødt op"]
                    + [f["label"] for f in fields] + ["Tilmeldt"])
    for r in regs:
        status = "Venteliste" if r["waitlist"] else "Deltager"
        row = [r["name"], r["email"], r["phone"], r["seats"], status,
               "Ja" if r["attended"] else ""]
        row += [r["values"].get(f["id"], "") for f in fields]
        row.append(r["created_at"])
        writer.writerow(row)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Kalender (iCal/.ics)
# --------------------------------------------------------------------------- #
def _ics_escape(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", r"\;")
            .replace(",", r"\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _ics_fold(line):
    """iCal-linjer må højst være 75 oktetter; fold med mellemrum."""
    out = []
    while len(line.encode("utf-8")) > 73:
        cut = 73
        while len(line[:cut].encode("utf-8")) > 73:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def _to_utc(naive):
    """Lokal (vægur-)tid -> UTC ud fra TZ. None hvis tidszone-data mangler."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.environ.get("TZ") or "Europe/Copenhagen")
        return naive.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
    except Exception:
        return None


def build_ics(group, events, base_url=""):
    """Byg en iCal-fil med de givne events."""
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Tilmeld//DA//",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           f"X-WR-CALNAME:{_ics_escape(group['name'])}"]
    for ev in events:
        try:
            day = datetime.strptime(ev["event_date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        out.append("BEGIN:VEVENT")
        out.append(f"UID:tilmeld-{group['slug']}-{ev['id']}@tilmeld")
        out.append(f"DTSTAMP:{stamp}")
        if ev["start_time"]:
            start = datetime.strptime(f"{ev['event_date']} {ev['start_time']}",
                                      "%Y-%m-%d %H:%M")
            if ev["end_time"]:
                end = datetime.strptime(f"{ev['event_date']} {ev['end_time']}",
                                        "%Y-%m-%d %H:%M")
                if end <= start:
                    end = start + timedelta(hours=2)
            else:
                end = start + timedelta(hours=2)
            su, eu = _to_utc(start), _to_utc(end)
            if su and eu:
                out.append("DTSTART:" + su.strftime("%Y%m%dT%H%M%SZ"))
                out.append("DTEND:" + eu.strftime("%Y%m%dT%H%M%SZ"))
            else:  # fallback: flydende lokal tid
                out.append("DTSTART:" + start.strftime("%Y%m%dT%H%M%S"))
                out.append("DTEND:" + end.strftime("%Y%m%dT%H%M%S"))
        else:  # heldagsevent
            out.append("DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d"))
            out.append("DTEND;VALUE=DATE:" + (day + timedelta(days=1)).strftime("%Y%m%d"))
        out.append(f"SUMMARY:{_ics_escape(ev['name'])}")
        desc = ev["description"] or ""
        if base_url:
            link = f"{base_url.rstrip('/')}/{group['slug']}/{ev['slug']}"
            out.append(f"URL:{link}")
            desc = (desc + "\n\n" + link).strip()
        if desc:
            out.append(f"DESCRIPTION:{_ics_escape(desc)}")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in out) + "\r\n"


def ensure_calendar_token(conn, group):
    """Sørg for at gruppen har en kalender-token (til .ics-abonnement)."""
    if group["calendar_token"]:
        return group["calendar_token"]
    token = secrets.token_urlsafe(16)
    conn.execute("UPDATE groups SET calendar_token = ? WHERE id = ?", (token, group["id"]))
    conn.commit()
    return token


def _registrations_with_values(conn, event_id, fields):
    # Deltagere først, derefter ventelisten (i den rækkefølge de skrev sig på)
    regs = conn.execute(
        "SELECT * FROM registrations WHERE event_id = ? ORDER BY waitlist, created_at, id",
        (event_id,)).fetchall()
    out = []
    for r in regs:
        vals = conn.execute(
            "SELECT field_id, value FROM registration_values WHERE registration_id = ?",
            (r["id"],)).fetchall()
        out.append({
            "id": r["id"], "name": r["name"], "email": r["email"], "phone": r["phone"],
            "user_id": r["user_id"], "created_at": r["created_at"],
            "seats": max(1, r["seats"] or 1), "waitlist": r["waitlist"],
            "attended": r["attended"],
            "values": {v["field_id"]: v["value"] for v in vals},
        })
    return out


# --------------------------------------------------------------------------- #
# Bruger-UI
# --------------------------------------------------------------------------- #
@app.route("/<slug>/image")
def group_image(slug):
    group = get_group(slug)
    if not group or not group["image_path"]:
        abort(404)
    path = os.path.join(db.DATA_DIR, "uploads", group["image_path"])
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


@app.route("/<slug>/login", methods=["GET", "POST"])
def user_login(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    # Individuelle bruger-konti: log ind med brugernavn + password
    if group["user_accounts_enabled"]:
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            conn = db.get_db()
            u = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            member = u and conn.execute(
                "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ?",
                (u["id"], group["id"])).fetchone()
            conn.close()
            if u and member and auth.verify_password(request.form.get("password", ""),
                                                      u["password_hash"]):
                session[f"uid_{slug}"] = u["id"]
                return redirect(url_for("user_home", slug=slug))
            flash("Forkert brugernavn eller adgangskode.", "error")
        conn = db.get_db()
        # Vis kun passkey-knappen hvis nogen i gruppen faktisk har registreret en.
        has_keys = bool(conn.execute(
            "SELECT 1 FROM credentials c JOIN user_groups ug ON ug.user_id = c.user_id "
            "WHERE c.scope = 'user' AND ug.group_id = ?", (group["id"],)).fetchone())
        conn.close()
        return render_template("user/login.html", group=group, accounts=True,
                               passkey_on=has_keys and passkeys.is_secure_origin(request))
    # Delt gruppe-password (eller åben gruppe)
    if not group["user_password"]:
        return redirect(url_for("user_home", slug=slug))
    if request.method == "POST":
        if request.form.get("password", "") == group["user_password"]:
            session[f"user_{slug}"] = True
            return redirect(url_for("user_home", slug=slug))
        flash("Forkert password.", "error")
    return render_template("user/login.html", group=group, accounts=False, passkey_on=False)


@app.route("/<slug>/logout")
def user_logout(slug):
    session.pop(f"user_{slug}", None)
    session.pop(f"uid_{slug}", None)
    return redirect(url_for("user_login", slug=slug))


@app.route("/<slug>/kalender.ics")
def group_calendar_ics(slug):
    """Abonnements-feed til Google/Outlook/Apple Kalender. Adgang via hemmelig token
    (kalender-apps sender ikke cookies) — eller almindelig login i browseren."""
    group = get_group(slug)
    if not group:
        abort(404)
    token = request.args.get("token", "")
    if not (group["calendar_token"] and token == group["calendar_token"]):
        if not user_has_access(group):
            abort(404)
    conn = db.get_db()
    since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    events = conn.execute(
        "SELECT * FROM events WHERE group_id = ? AND event_date >= ? ORDER BY event_date",
        (group["id"], since)).fetchall()
    base = db.get_settings(conn)["base_url"]
    conn.close()
    return Response(build_ics(group, events, base), mimetype="text/calendar; charset=utf-8")


@app.route("/<slug>/<event_slug>/event.ics")
def user_event_ics(slug, event_slug):
    """Enkelt event som .ics ("Tilføj til kalender")."""
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE group_id = ? AND slug = ?",
                      (group["id"], event_slug)).fetchone()
    base = db.get_settings(conn)["base_url"]
    conn.close()
    if not ev:
        abort(404)
    return Response(
        build_ics(group, [ev], base), mimetype="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={ev['slug']}.ics"})


@app.route("/<slug>")
def user_home(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    events = conn.execute("SELECT * FROM events WHERE group_id = ?", (group["id"],)).fetchall()
    rows = []
    for ev in sorted(events, key=event_sort_key):
        state = event_state(ev)
        if state == "finished":
            continue  # afsluttede events skjules for brugere
        rows.append({"ev": ev, "state": state,
                     "count": count_attending(conn, group["id"], ev["id"])})
    cal_url = url_for("group_calendar_ics", slug=group["slug"],
                      token=ensure_calendar_token(conn, group), _external=True)
    conn.close()
    return render_template("user/home.html", group=group, events=rows, cal_url=cal_url,
                           accounts=bool(group["user_accounts_enabled"]),
                           is_admin=bool(session.get(f"admin_{group['slug']}")))


@app.route("/<slug>/<event_slug>")
def user_event(slug, event_slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE group_id = ? AND slug = ?",
                      (group["id"], event_slug)).fetchone()
    if not ev:
        conn.close()
        abort(404)
    state = event_state(ev)
    fields = visible_fields(conn, group["id"], ev["id"])
    regs = _registrations_with_values(conn, ev["id"], fields)
    attending = count_attending(conn, group["id"], ev["id"])
    full = bool(ev["capacity_limit"] and ev["expected_count"]
                and attending >= ev["expected_count"])
    mail_on, wa_on = group_channels(conn, group)
    decline_ids = [f["id"] for f in fields if f["is_decline"]]
    is_admin = bool(session.get(f"admin_{group['slug']}"))
    accounts = bool(group["user_accounts_enabled"])
    my_uid = current_user_id(group)
    # Markér hvilke tilmeldinger den besøgende må redigere + om egen tilmelding findes
    has_own = False
    for r in regs:
        if is_admin or (not accounts) or (my_uid and r["user_id"] == my_uid):
            r["can_edit"] = True
        else:
            r["can_edit"] = False
        if accounts and my_uid and r["user_id"] == my_uid:
            has_own = True
    # Admin kan oprette på vegne af en gruppes brugere
    group_users = []
    if accounts and is_admin:
        group_users = conn.execute(
            "SELECT u.id, u.username, u.name FROM users u JOIN user_groups ug ON ug.user_id = u.id "
            "WHERE ug.group_id = ? ORDER BY u.username", (group["id"],)).fetchall()
    my_name = ""
    if accounts and my_uid and not is_admin:
        mu = get_user(conn, my_uid)
        my_name = (mu["name"] or mu["username"]) if mu else ""
    conn.close()
    # Med konti: brugere ser kun tilmeldings-formularen hvis de ikke allerede er tilmeldt
    show_signup = (state == "open") and (is_admin or not accounts or not has_own)
    parsed_fields = [{"f": f, "options": json.loads(f["options"] or "[]")} for f in fields]
    return render_template("user/event.html", group=group, ev=ev, state=state,
                           fields=parsed_fields, regs=regs, count=attending, full=full,
                           mail_on=mail_on, wa_on=wa_on, decline_ids=decline_ids,
                           accounts=accounts, is_admin=is_admin, show_signup=show_signup,
                           group_users=group_users, my_name=my_name)


@app.route("/<slug>/<event_slug>/signup", methods=["POST"])
def user_signup(slug, event_slug):
    return _handle_registration(slug, event_slug, None)


@app.route("/<slug>/<event_slug>/edit/<int:reg_id>", methods=["GET", "POST"])
def user_edit(slug, event_slug, reg_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE group_id = ? AND slug = ?",
                      (group["id"], event_slug)).fetchone()
    if not ev:
        conn.close()
        abort(404)
    reg = conn.execute("SELECT * FROM registrations WHERE id = ? AND event_id = ?",
                       (reg_id, ev["id"])).fetchone()
    if not reg:
        conn.close()
        abort(404)
    if not can_edit_registration(group, reg):
        conn.close()
        flash("Du kan kun rette din egen tilmelding.", "error")
        return redirect(url_for("user_event", slug=slug, event_slug=event_slug))
    fields = visible_fields(conn, group["id"], ev["id"])
    if request.method == "POST":
        conn.close()
        return _handle_registration(slug, event_slug, reg_id)
    vals = conn.execute(
        "SELECT field_id, value FROM registration_values WHERE registration_id = ?",
        (reg_id,)).fetchall()
    mail_on, wa_on = group_channels(conn, group)
    conn.close()
    parsed_fields = [{"f": f, "options": json.loads(f["options"] or "[]")} for f in fields]
    current = {v["field_id"]: v["value"] for v in vals}
    return render_template("user/signup_form.html", group=group, ev=ev,
                           fields=parsed_fields, reg=reg, current=current,
                           state=event_state(ev), mail_on=mail_on, wa_on=wa_on,
                           accounts=bool(group["user_accounts_enabled"]),
                           is_admin=bool(session.get(f"admin_{group['slug']}")))


def _handle_registration(slug, event_slug, reg_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE group_id = ? AND slug = ?",
                      (group["id"], event_slug)).fetchone()
    if not ev:
        conn.close()
        abort(404)
    if event_state(ev) != "open":
        conn.close()
        flash("Tilmeldingen er lukket for dette event.", "error")
        return redirect(url_for("user_event", slug=slug, event_slug=event_slug))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    # Individuelle konti: knyt tilmeldingen til en bruger; navn + kontakt kommer fra profilen
    accounts = bool(group["user_accounts_enabled"])
    owner_id = None
    if accounts:
        if session.get(f"admin_{group['slug']}"):
            ob = request.form.get("on_behalf_user", "").strip()
            owner_id = int(ob) if ob.isdigit() else None
        else:
            owner_id = current_user_id(group)
        if reg_id:  # bevar eksisterende ejer ved redigering
            ex = conn.execute("SELECT user_id FROM registrations WHERE id = ?",
                              (reg_id,)).fetchone()
            if ex and ex["user_id"] is not None:
                owner_id = ex["user_id"]
        u = get_user(conn, owner_id)
        if u:  # navn + kontakt fra brugerens profil (navn skal ikke tastes)
            name = u["name"] or u["username"]
            email, phone = u["email"], u["whatsapp"]

    if not name:
        conn.close()
        flash("Navn er påkrævet.", "error")
        return redirect(url_for("user_event", slug=slug, event_slug=event_slug))

    fields = visible_fields(conn, group["id"], ev["id"])

    # Er et "deltager ikke"-felt afkrydset? Så kræves kun navn.
    declining = any(
        f["is_decline"] and request.form.get(f"field_{f['id']}") for f in fields)

    # Antal pladser (dig + gæster)
    seats = 1
    if ev["allow_guests"]:
        try:
            seats = max(1, min(50, int(request.form.get("seats") or 1)))
        except ValueError:
            seats = 1
    if declining:
        seats = 1  # afbud optager ingen plads alligevel

    # Er tilmeldingen allerede på venteliste? (bevares ved redigering)
    waitlist_flag = 0
    if reg_id:
        exw = conn.execute("SELECT waitlist FROM registrations WHERE id = ?",
                           (reg_id,)).fetchone()
        waitlist_flag = exw["waitlist"] if exw else 0

    # Kapacitetsgrænse: afbud og venteliste optager ikke pladser
    if (ev["capacity_limit"] and ev["expected_count"] and not declining
            and not waitlist_flag):
        taken = count_attending(conn, group["id"], ev["id"], exclude_reg_id=reg_id)
        if taken + seats > ev["expected_count"]:
            if not reg_id and ev["waitlist_enabled"]:
                waitlist_flag = 1  # ny tilmelding ryger på venteliste
            else:
                conn.close()
                if reg_id:
                    flash("Listen er fyldt op — der er ikke plads til ændringen "
                          "(fx at fjerne 'deltager ikke' eller tilføje gæster).", "error")
                    return redirect(url_for("user_edit", slug=slug, event_slug=event_slug,
                                            reg_id=reg_id))
                flash("Der er desværre ikke plads til flere på dette event.", "error")
                return redirect(url_for("user_event", slug=slug, event_slug=event_slug))

    # Læs og valider punkter
    field_values = {}
    for f in fields:
        if f["field_type"] == "checkbox":
            field_values[f["id"]] = "Ja" if request.form.get(f"field_{f['id']}") else "Nej"
        else:
            field_values[f["id"]] = request.form.get(f"field_{f['id']}", "").strip()
        if not declining and f["required"]:
            if f["field_type"] == "checkbox" and field_values[f["id"]] != "Ja":
                conn.close()
                flash(f"Punktet '{f['label']}' skal markeres.", "error")
                return redirect(url_for("user_event", slug=slug, event_slug=event_slug))
            if f["field_type"] != "checkbox" and not field_values[f["id"]]:
                conn.close()
                flash(f"Punktet '{f['label']}' skal udfyldes.", "error")
                return redirect(url_for("user_event", slug=slug, event_slug=event_slug))

    if reg_id:
        conn.execute(
            "UPDATE registrations SET name=?, email=?, phone=?, user_id=?, seats=?, "
            "waitlist=?, updated_at=? WHERE id=?",
            (name, email, phone, owner_id, seats, waitlist_flag, db.now_iso(), reg_id))
        conn.execute("DELETE FROM registration_values WHERE registration_id = ?", (reg_id,))
        rid = reg_id
        is_new = False
    else:
        cur = conn.execute(
            "INSERT INTO registrations (event_id, name, email, phone, user_id, seats, "
            "waitlist, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ev["id"], name, email, phone, owner_id, seats, waitlist_flag,
             db.now_iso(), db.now_iso()))
        rid = cur.lastrowid
        is_new = True
    for fid, val in field_values.items():
        conn.execute(
            "INSERT INTO registration_values (registration_id, field_id, value) VALUES (?,?,?)",
            (rid, fid, val))
    conn.commit()
    if is_new:
        suffix = " (afbud)" if declining else (" (venteliste)" if waitlist_flag else "")
        db.add_log(conn, "signup", f"{name} tilmeldt {ev['name']}{suffix}", group["slug"])
    else:
        db.add_log(conn, "signup", f"{name} ændrede tilmelding til {ev['name']}", group["slug"])

    # Notifikationer (tekst hentes fra gruppens mail-skabeloner eller standard)
    ctx = {"event": ev["name"], "name": name, "date": ev["event_date"],
           "group": group["name"], "deadline": ev["signup_deadline"]}
    if is_new and ev["notify_new_signup"]:
        subj, body = notifications.render_message(conn, group, "new_signup", ctx)
        notifications.notify_admin(conn, group, subj, body)
    if not is_new and ev["notify_change"]:
        subj, body = notifications.render_message(conn, group, "change", ctx)
        notifications.notify_admin(conn, group, subj, body)
    if is_new and ev["notify_receipt"] and not waitlist_flag:
        subj, body = notifications.render_message(conn, group, "receipt", ctx)
        notifications.notify_participant(conn, group, email, phone, subj, body)

    if waitlist_flag:
        pos = waitlist_position(conn, ev["id"], rid)
        flash(f"Der er fyldt op — du er skrevet på ventelisten som nr. {pos}. "
              "Du får besked hvis der bliver en plads.", "ok")
    else:
        flash("Tilmelding gemt." if is_new else "Tilmelding opdateret.", "ok")

    # Blev der frigjort pladser (fx afbud eller færre gæster)? Ryk ventelisten op.
    notify_promoted(conn, group, ev, promote_waitlist(conn, group, ev))
    conn.close()
    return redirect(url_for("user_event", slug=slug, event_slug=event_slug))


@app.route("/<slug>/<event_slug>/delete/<int:reg_id>", methods=["POST"])
def user_delete(slug, event_slug, reg_id):
    group = get_group(slug)
    if not group:
        abort(404)
    if not user_has_access(group):
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    ev = conn.execute("SELECT * FROM events WHERE group_id = ? AND slug = ?",
                      (group["id"], event_slug)).fetchone()
    reg = conn.execute("SELECT * FROM registrations WHERE id = ? AND event_id = ?",
                       (reg_id, ev["id"])).fetchone() if ev else None
    if reg and not can_edit_registration(group, reg):
        conn.close()
        flash("Du kan kun fjerne din egen tilmelding.", "error")
        return redirect(url_for("user_event", slug=slug, event_slug=event_slug))
    if ev and event_state(ev) == "open":
        conn.execute("DELETE FROM registrations WHERE id = ? AND event_id = ?",
                     (reg_id, ev["id"]))
        conn.commit()
        if reg:
            db.add_log(conn, "signup", f"{reg['name']} fjernet fra {ev['name']}", group["slug"])
        flash("Tilmelding fjernet.", "ok")
        # Der blev en plads ledig — ryk ventelisten op
        notify_promoted(conn, group, ev, promote_waitlist(conn, group, ev))
    else:
        flash("Kan ikke ændre en lukket tilmelding.", "error")
    conn.close()
    return redirect(url_for("user_event", slug=slug, event_slug=event_slug))


@app.route("/<slug>/profil", methods=["GET", "POST"])
def user_profile(slug):
    """Individuel bruger: skift adgangskode + sæt mail/WhatsApp til notifikationer."""
    group = get_group(slug)
    if not group or not group["user_accounts_enabled"]:
        abort(404)
    uid = current_user_id(group)
    if not uid:
        return redirect(url_for("user_login", slug=slug))
    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "contact":
            conn.execute("UPDATE users SET name = ?, email = ?, whatsapp = ? WHERE id = ?",
                         (request.form.get("name", "").strip(),
                          request.form.get("email", "").strip(),
                          request.form.get("whatsapp", "").strip(), uid))
            flash("Oplysninger gemt.", "ok")
        elif action == "password":
            newpw = request.form.get("new_password", "")
            if len(newpw) < 4:
                flash("Adgangskoden skal være mindst 4 tegn.", "error")
            else:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                             (auth.hash_password(newpw), uid))
                flash("Adgangskode ændret.", "ok")
        conn.commit()
    u = get_user(conn, uid)
    # "Mine tilmeldinger": kommende events på tværs af ALLE brugerens grupper
    mine = conn.execute(
        "SELECT r.seats, r.waitlist, e.name AS ev_name, e.slug AS ev_slug, "
        "e.event_date, e.start_time, g.slug AS g_slug, g.name AS g_name "
        "FROM registrations r JOIN events e ON e.id = r.event_id "
        "JOIN groups g ON g.id = e.group_id "
        "JOIN user_groups ug ON ug.group_id = g.id AND ug.user_id = r.user_id "
        "WHERE r.user_id = ? AND e.event_date >= ? ORDER BY e.event_date, e.start_time",
        (uid, datetime.now().strftime("%Y-%m-%d"))).fetchall()
    creds = passkeys.list_credentials(conn, "user", user_id=uid)
    conn.close()
    return render_template("user/profile.html", group=group, u=u, mine=mine, creds=creds,
                           passkey_blocked=passkeys.blocked_reason(request))


@app.route("/<slug>/glemt", methods=["GET", "POST"])
def user_forgot(slug):
    """Glemt adgangskode: send nulstillings-link på mail (kræver SMTP + mail på profilen)."""
    group = get_group(slug)
    if not group or not group["user_accounts_enabled"]:
        abort(404)
    if request.method == "POST":
        ident = request.form.get("ident", "").strip()
        conn = db.get_db()
        u = conn.execute(
            "SELECT u.* FROM users u JOIN user_groups ug ON ug.user_id = u.id "
            "WHERE ug.group_id = ? AND (u.username = ? OR u.email = ?)",
            (group["id"], ident, ident)).fetchone()
        settings = db.get_settings(conn)
        if u and u["email"] and settings["smtp_host"]:
            token = secrets.token_urlsafe(24)
            expires = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE users SET reset_token = ?, reset_expires = ? WHERE id = ?",
                         (token, expires, u["id"]))
            conn.commit()
            base = (settings["base_url"] or "").rstrip("/")
            link = (f"{base}{url_for('user_reset', slug=slug, token=token)}" if base
                    else url_for("user_reset", slug=slug, token=token, _external=True))
            notifications.send_email(
                settings, u["email"], f"Nulstil adgangskode – {group['name']}",
                f"Hej {u['name'] or u['username']}.\n\nKlik her for at vælge en ny "
                f"adgangskode (linket udløber om 1 time):\n{link}\n\n"
                "Har du ikke bedt om det, kan du ignorere denne mail.")
            db.add_log(conn, "user", f"Nulstillings-link sendt til {u['username']}",
                       group["slug"])
        conn.close()
        # Afslør ikke om brugeren findes
        flash("Hvis kontoen findes og har en e-mail, er der sendt et nulstillings-link.", "ok")
        return redirect(url_for("user_login", slug=slug))
    return render_template("user/forgot.html", group=group)


@app.route("/<slug>/nulstil/<token>", methods=["GET", "POST"])
def user_reset(slug, token):
    group = get_group(slug)
    if not group or not group["user_accounts_enabled"]:
        abort(404)
    conn = db.get_db()
    u = conn.execute("SELECT * FROM users WHERE reset_token = ? AND reset_token != ''",
                     (token,)).fetchone()
    valid = False
    if u and u["reset_expires"]:
        try:
            valid = datetime.now() <= datetime.fromisoformat(u["reset_expires"])
        except ValueError:
            valid = False
    if not valid:
        conn.close()
        flash("Linket er udløbet eller ugyldigt. Prøv igen.", "error")
        return redirect(url_for("user_forgot", slug=slug))
    if request.method == "POST":
        newpw = request.form.get("new_password", "")
        if len(newpw) < 4:
            flash("Adgangskoden skal være mindst 4 tegn.", "error")
        else:
            conn.execute(
                "UPDATE users SET password_hash = ?, reset_token = '', reset_expires = '' "
                "WHERE id = ?", (auth.hash_password(newpw), u["id"]))
            conn.commit()
            conn.close()
            flash("Adgangskoden er ændret — log ind med den nye.", "ok")
            return redirect(url_for("user_login", slug=slug))
    conn.close()
    return render_template("user/reset.html", group=group, token=token)


# --------------------------------------------------------------------------- #
# Gruppe-admin: individuelle brugere
# --------------------------------------------------------------------------- #
@app.route("/<slug>/admin/users", methods=["GET", "POST"])
def admin_users(slug):
    group = get_group(slug)
    if not group:
        abort(404)
    if not admin_has_access(group):
        return redirect(url_for("admin_login", slug=slug))
    if not group["user_accounts_enabled"]:
        flash("Individuelle brugere er ikke slået til for gruppen.", "error")
        return redirect(url_for("admin_home", slug=slug))
    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            if not auth.is_valid_username(username):
                flash("Ugyldigt brugernavn (3-40 tegn: bogstaver, tal, . _ -).", "error")
            elif len(pw) < 4:
                flash("Adgangskoden skal være mindst 4 tegn.", "error")
            elif conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                flash("Brugernavnet er allerede optaget (brugernavne er unikke på hele systemet).", "error")
            else:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, name, created_at) VALUES (?,?,?,?)",
                    (username, auth.hash_password(pw), request.form.get("name", "").strip(),
                     db.now_iso()))
                conn.execute("INSERT INTO user_groups (user_id, group_id) VALUES (?,?)",
                             (cur.lastrowid, group["id"]))
                db.add_log(conn, "user", f"Bruger '{username}' oprettet i {group['name']}", group["slug"])
                flash(f"Bruger '{username}' oprettet.", "ok")
        elif action == "resetpw":
            newpw = request.form.get("new_password", "")
            if len(newpw) < 4:
                flash("Adgangskoden skal være mindst 4 tegn.", "error")
            else:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                             (auth.hash_password(newpw), request.form.get("user_id")))
                flash("Adgangskode nulstillet.", "ok")
        elif action == "remove":
            conn.execute("DELETE FROM user_groups WHERE user_id = ? AND group_id = ?",
                         (request.form.get("user_id"), group["id"]))
            flash("Bruger fjernet fra gruppen.", "ok")
        conn.commit()
    users = conn.execute(
        "SELECT u.* FROM users u JOIN user_groups ug ON ug.user_id = u.id "
        "WHERE ug.group_id = ? ORDER BY u.username", (group["id"],)).fetchall()
    conn.close()
    return render_template("admin/users.html", group=group, users=users)


# --------------------------------------------------------------------------- #
# Master-admin: brugere på tværs af grupper
# --------------------------------------------------------------------------- #
@app.route("/master/users", methods=["GET", "POST"])
@master_required
def master_users():
    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("user_id")
        if action == "delete":
            u = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            if u:
                db.add_log(conn, "user", f"Bruger '{u['username']}' slettet")
            flash("Bruger slettet.", "ok")
        elif action == "add_group":
            gid = request.form.get("group_id")
            if uid and gid:
                conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)",
                             (uid, gid))
                flash("Bruger tilføjet til gruppen.", "ok")
        elif action == "remove_group":
            conn.execute("DELETE FROM user_groups WHERE user_id = ? AND group_id = ?",
                         (uid, request.form.get("group_id")))
            flash("Bruger fjernet fra gruppen.", "ok")
        conn.commit()
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    users = []
    for u in rows:
        groups = conn.execute(
            "SELECT g.id, g.name, g.slug FROM groups g JOIN user_groups ug ON ug.group_id = g.id "
            "WHERE ug.user_id = ? ORDER BY g.name", (u["id"],)).fetchall()
        users.append({"u": u, "groups": groups})
    all_groups = conn.execute(
        "SELECT id, name FROM groups WHERE user_accounts_enabled = 1 ORDER BY name").fetchall()
    conn.close()
    return render_template("master/users.html", users=users, all_groups=all_groups)


# Registrér CSV-byggeren og start påmindelses-/CSV-scheduleren.
notifications.csv_builder = build_csv
notifications.start_scheduler()


if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "1") not in ("0", "false", "False", "")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=debug)
