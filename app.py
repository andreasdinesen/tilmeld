"""Tilmeld - event-tilmeldingssystem (bruger / gruppe-admin / master-admin)."""
import csv
import io
import json
import os
from datetime import datetime, timedelta
from functools import wraps

import bleach
import markdown as markdown_lib
from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, session, url_for)
from markupsafe import Markup
from werkzeug.utils import secure_filename

import auth
import db
import notifications
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


def count_attending(conn, group_id, event_id, exclude_reg_id=None):
    """Antal tilmeldte der reelt deltager (ekskl. 'deltager ikke'-afkrydsede)."""
    decline_ids = [f["id"] for f in all_group_fields(conn, group_id) if f["is_decline"]]
    regs = conn.execute(
        "SELECT id FROM registrations WHERE event_id = ?", (event_id,)).fetchall()
    n = 0
    for r in regs:
        if exclude_reg_id and r["id"] == exclude_reg_id:
            continue
        if decline_ids:
            ph = ",".join("?" * len(decline_ids))
            declined = conn.execute(
                f"SELECT 1 FROM registration_values WHERE registration_id = ? "
                f"AND field_id IN ({ph}) AND value = 'Ja' LIMIT 1",
                [r["id"]] + decline_ids).fetchone()
            if declined:
                continue
        n += 1
    return n


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


def user_has_access(group):
    """Adgang til bruger-siderne. Ikke-logget-ind kræver gruppe-password (eller åben gruppe);
    en gruppe-admin (logget ind) slipper også ind uden bruger-password."""
    if session.get(f"admin_{group['slug']}"):
        return True
    if not group["user_password"]:
        return True
    return bool(session.get(f"user_{group['slug']}"))


def admin_has_access(group):
    return bool(session.get(f"admin_{group['slug']}"))


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
    return render_template("index.html")


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
    return render_template("master/login.html")


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
    conn.close()
    return render_template("master/home.html", groups=data)


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
                "mail_enabled, whatsapp_enabled, admin_email, whatsapp_recipient, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (slug, name, request.form.get("user_password", ""),
                 auth.hash_password(admin_pw),
                 1 if request.form.get("mail_enabled") else 0,
                 1 if request.form.get("whatsapp_enabled") else 0,
                 request.form.get("admin_email", "").strip(),
                 request.form.get("whatsapp_recipient", "").strip(),
                 db.now_iso()),
            )
            conn.commit()
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
        "UPDATE groups SET mail_enabled = ?, whatsapp_enabled = ? WHERE id = ?",
        (1 if request.form.get("mail_enabled") else 0,
         1 if request.form.get("whatsapp_enabled") else 0, g["id"]),
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
    conn.close()
    flash(f"Gruppe '{g['name']}' slettet.", "ok")
    return redirect(url_for("master_home"))


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
            "default_deadline_days=?, github_repo=?, update_branch=? WHERE id = 1",
            (request.form.get("smtp_host", "").strip(),
             int(request.form.get("smtp_port") or 587),
             request.form.get("smtp_user", "").strip(),
             request.form.get("smtp_password", ""),
             request.form.get("smtp_from", "").strip(),
             1 if request.form.get("smtp_use_tls") else 0,
             request.form.get("whatsapp_api_url", "").strip(),
             request.form.get("whatsapp_api_key", "").strip(),
             int(request.form.get("default_deadline_days") or 4),
             request.form.get("github_repo", "").strip(),
             request.form.get("update_branch", "main").strip() or "main"),
        )
        conn.commit()
        flash("Indstillinger gemt.", "ok")
    s = db.get_settings(conn)
    conn.close()
    return render_template("master/settings.html", s=s)


@app.route("/master/system", methods=["GET", "POST"])
@master_required
def master_system():
    conn = db.get_db()
    s = db.get_settings(conn)
    conn.close()
    update_log = None
    check = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "check":
            check = system_info.check_latest(s["github_repo"], s["update_branch"])
        elif action == "update_app":
            update_log = system_info.update_app(s["update_branch"])
        elif action == "update_deps":
            update_log = system_info.update_dependencies()
    return render_template(
        "master/system.html", s=s,
        components=system_info.component_versions(),
        is_git=system_info.is_git_repo(),
        check=check, update_log=update_log)


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
    return render_template("admin/login.html", group=group)


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
    rows = []
    for ev in sorted(events, key=event_sort_key):
        rows.append({
            "ev": ev,
            "state": event_state(ev),
            "count": count_attending(conn, group["id"], ev["id"]),
            "total": count_registrations(conn, ev["id"]),
        })
    conn.close()
    return render_template("admin/home.html", group=group, events=rows)


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
        conn.commit()
        group = get_group(slug)
    fields = all_group_fields(conn, group["id"])
    mail_on, wa_on = group_channels(conn, group)
    conn.close()
    parsed = [{"f": f, "options": json.loads(f["options"] or "[]")} for f in fields]
    return render_template("admin/settings.html", group=group, fields=parsed,
                           mail_on=mail_on, wa_on=wa_on)


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
    )
    if ev:
        conn.execute(
            "UPDATE events SET name=?, slug=?, event_date=?, start_time=?, end_time=?, "
            "description=?, expected_count=?, signup_deadline=?, notify_new_signup=?, "
            "notify_change=?, notify_receipt=?, notify_reminder=?, csv_after_deadline=?, "
            "capacity_limit=? WHERE id = ?",
            vals + (ev["id"],))
        event_id = ev["id"]
        flash("Event opdateret.", "ok")
    else:
        cur = conn.execute(
            "INSERT INTO events (name, slug, event_date, start_time, end_time, description, "
            "expected_count, signup_deadline, notify_new_signup, notify_change, notify_receipt, "
            "notify_reminder, csv_after_deadline, capacity_limit, group_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            vals + (group["id"], db.now_iso()))
        event_id = cur.lastrowid
        flash("Event oprettet.", "ok")

    # Gem hvilke punkter der er skjult på dette event (ukrydsede = skjult)
    conn.execute("DELETE FROM event_hidden_fields WHERE event_id = ?", (event_id,))
    for f in all_group_fields(conn, group["id"]):
        if not request.form.get(f"show_field_{f['id']}"):
            conn.execute(
                "INSERT OR IGNORE INTO event_hidden_fields (event_id, field_id) VALUES (?,?)",
                (event_id, f["id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home", slug=group["slug"]))


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


@app.route("/<slug>/admin/events/<int:event_id>/list")
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
    fields = visible_fields(conn, group["id"], ev["id"])
    regs = _registrations_with_values(conn, ev["id"], fields)
    attending = count_attending(conn, group["id"], ev["id"])
    decline_ids = [f["id"] for f in fields if f["is_decline"]]
    conn.close()
    return render_template("admin/event_list.html", group=group, ev=ev,
                           fields=fields, regs=regs, count=attending,
                           total=len(regs), decline_ids=decline_ids)


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
    writer.writerow(["Navn", "E-mail", "WhatsApp"] + [f["label"] for f in fields] + ["Tilmeldt"])
    for r in regs:
        row = [r["name"], r["email"], r["phone"]]
        row += [r["values"].get(f["id"], "") for f in fields]
        row.append(r["created_at"])
        writer.writerow(row)
    return buf.getvalue()


def _registrations_with_values(conn, event_id, fields):
    regs = conn.execute(
        "SELECT * FROM registrations WHERE event_id = ? ORDER BY created_at", (event_id,)
    ).fetchall()
    out = []
    for r in regs:
        vals = conn.execute(
            "SELECT field_id, value FROM registration_values WHERE registration_id = ?",
            (r["id"],)).fetchall()
        out.append({
            "id": r["id"], "name": r["name"], "email": r["email"], "phone": r["phone"],
            "created_at": r["created_at"],
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
    if not group["user_password"]:
        return redirect(url_for("user_home", slug=slug))
    if request.method == "POST":
        if request.form.get("password", "") == group["user_password"]:
            session[f"user_{slug}"] = True
            return redirect(url_for("user_home", slug=slug))
        flash("Forkert password.", "error")
    return render_template("user/login.html", group=group)


@app.route("/<slug>/logout")
def user_logout(slug):
    session.pop(f"user_{slug}", None)
    return redirect(url_for("user_login", slug=slug))


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
    conn.close()
    return render_template("user/home.html", group=group, events=rows)


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
    fields = visible_fields(conn, group["id"], ev["id"])
    regs = _registrations_with_values(conn, ev["id"], fields)
    attending = count_attending(conn, group["id"], ev["id"])
    full = bool(ev["capacity_limit"] and ev["expected_count"]
                and attending >= ev["expected_count"])
    mail_on, wa_on = group_channels(conn, group)
    decline_ids = [f["id"] for f in fields if f["is_decline"]]
    conn.close()
    parsed_fields = [{"f": f, "options": json.loads(f["options"] or "[]")} for f in fields]
    return render_template("user/event.html", group=group, ev=ev, state=event_state(ev),
                           fields=parsed_fields, regs=regs, count=attending, full=full,
                           mail_on=mail_on, wa_on=wa_on, decline_ids=decline_ids)


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
                           state=event_state(ev), mail_on=mail_on, wa_on=wa_on)


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
    if not name:
        conn.close()
        flash("Navn er påkrævet.", "error")
        return redirect(url_for("user_event", slug=slug, event_slug=event_slug))
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    fields = visible_fields(conn, group["id"], ev["id"])

    # Er et "deltager ikke"-felt afkrydset? Så kræves kun navn.
    declining = any(
        f["is_decline"] and request.form.get(f"field_{f['id']}") for f in fields)

    # Kapacitetsgrænse: afvis hvis der ikke er plads (decline-tilmeldinger tæller ikke med)
    if ev["capacity_limit"] and ev["expected_count"] and not declining:
        taken = count_attending(conn, group["id"], ev["id"], exclude_reg_id=reg_id)
        if taken >= ev["expected_count"]:
            conn.close()
            if reg_id:
                flash("Listen er fyldt op — du kan ikke fjerne fluebenet ved "
                      "'deltager ikke', da der ikke er plads til flere.", "error")
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
            "UPDATE registrations SET name=?, email=?, phone=?, updated_at=? WHERE id=?",
            (name, email, phone, db.now_iso(), reg_id))
        conn.execute("DELETE FROM registration_values WHERE registration_id = ?", (reg_id,))
        rid = reg_id
        is_new = False
    else:
        cur = conn.execute(
            "INSERT INTO registrations (event_id, name, email, phone, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (ev["id"], name, email, phone, db.now_iso(), db.now_iso()))
        rid = cur.lastrowid
        is_new = True
    for fid, val in field_values.items():
        conn.execute(
            "INSERT INTO registration_values (registration_id, field_id, value) VALUES (?,?,?)",
            (rid, fid, val))
    conn.commit()

    # Notifikationer
    if is_new and ev["notify_new_signup"]:
        notifications.notify_admin(conn, group, f"Ny tilmelding: {ev['name']}",
                                   f"{name} har tilmeldt sig {ev['name']}.")
    if not is_new and ev["notify_change"]:
        notifications.notify_admin(conn, group, f"Ændret tilmelding: {ev['name']}",
                                   f"{name} har ændret sin tilmelding til {ev['name']}.")
    if is_new and ev["notify_receipt"]:
        notifications.notify_participant(
            conn, group, email, phone, f"Kvittering: {ev['name']}",
            f"Tak for din tilmelding til {ev['name']} d. {ev['event_date']}.")
    conn.close()
    flash("Tilmelding gemt." if is_new else "Tilmelding opdateret.", "ok")
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
    if ev and event_state(ev) == "open":
        conn.execute("DELETE FROM registrations WHERE id = ? AND event_id = ?",
                     (reg_id, ev["id"]))
        conn.commit()
        flash("Tilmelding fjernet.", "ok")
    else:
        flash("Kan ikke ændre en lukket tilmelding.", "error")
    conn.close()
    return redirect(url_for("user_event", slug=slug, event_slug=event_slug))


# Registrér CSV-byggeren og start påmindelses-/CSV-scheduleren.
notifications.csv_builder = build_csv
notifications.start_scheduler()


if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "1") not in ("0", "false", "False", "")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=debug)
