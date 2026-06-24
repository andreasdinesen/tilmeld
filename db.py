"""SQLite-forbindelse og schema-init. Databasen oprettes automatisk ved opstart."""
import os
import secrets
import sqlite3
from datetime import datetime

import auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "tilmeld.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Opret datamappe, tabeller og standard-indstillinger hvis de mangler."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
    conn = get_db()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _migrate(conn)

    row = conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
    if row is None:
        # Brug 'or' så en *tom* env-variabel (fx SECRET_KEY="") falder tilbage til
        # standard/genereret værdi i stedet for at give en tom nøgle.
        master_pw = os.environ.get("MASTER_PASSWORD") or "admin"
        secret = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
        conn.execute(
            "INSERT INTO settings (id, master_password_hash, secret_key) VALUES (1, ?, ?)",
            (auth.hash_password(master_pw), secret),
        )
        conn.commit()
        if not (os.environ.get("MASTER_PASSWORD") or "").strip():
            print("=" * 64)
            print(" ADVARSEL: intet MASTER_PASSWORD sat. Standard er 'admin'.")
            print(" Log ind på /master og skift det med det samme.")
            print("=" * 64)

    # Reparér en tom secret_key (fx databaser oprettet mens SECRET_KEY="" blev sendt
    # som env) — ellers fejler login med 500, fordi Flask-sessioner kræver en nøgle.
    cur = conn.execute("SELECT secret_key FROM settings WHERE id = 1").fetchone()
    if not (cur and (cur["secret_key"] or "").strip()):
        conn.execute("UPDATE settings SET secret_key = ? WHERE id = 1",
                     (os.environ.get("SECRET_KEY") or secrets.token_hex(32),))
        conn.commit()
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Tilføj kolonner til eksisterende databaser (ADD COLUMN er idempotent-sikret)."""
    def cols(table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    def add(table, col, decl):
        if col not in cols(table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    add("settings", "default_deadline_days", "INTEGER DEFAULT 4")
    add("settings", "github_repo", "TEXT DEFAULT ''")
    add("settings", "update_branch", "TEXT DEFAULT 'main'")
    add("settings", "whatsapp_api_url", "TEXT DEFAULT ''")
    add("settings", "whatsapp_api_key", "TEXT DEFAULT ''")
    add("groups", "image_path", "TEXT DEFAULT ''")
    add("groups", "login_text", "TEXT DEFAULT ''")
    add("groups", "whatsapp_enabled", "INTEGER DEFAULT 0")
    add("groups", "whatsapp_recipient", "TEXT DEFAULT ''")
    add("groups", "templates_enabled", "INTEGER DEFAULT 0")
    add("group_fields", "is_decline", "INTEGER DEFAULT 0")
    add("group_fields", "multiline", "INTEGER DEFAULT 0")
    add("events", "csv_after_deadline", "INTEGER DEFAULT 0")
    add("events", "csv_sent", "INTEGER DEFAULT 0")
    add("events", "capacity_limit", "INTEGER DEFAULT 0")

    # Flyt evt. gamle SMS-data over til WhatsApp-felterne (kun hvis de gamle kolonner
    # findes — dvs. databaser oprettet før WhatsApp-skiftet).
    gcols = cols("groups")
    if "admin_phone" in gcols:
        conn.execute("UPDATE groups SET whatsapp_recipient = admin_phone "
                     "WHERE (whatsapp_recipient IS NULL OR whatsapp_recipient = '') "
                     "AND admin_phone != ''")
    if "sms_enabled" in gcols:
        conn.execute("UPDATE groups SET whatsapp_enabled = sms_enabled "
                     "WHERE whatsapp_enabled = 0 AND sms_enabled = 1")
    conn.commit()


def get_settings(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
