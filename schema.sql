-- Tilmeld - SQLite skema. Oprettes automatisk ved opstart.

CREATE TABLE IF NOT EXISTS settings (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    master_password_hash TEXT NOT NULL,
    secret_key          TEXT NOT NULL,
    smtp_host           TEXT DEFAULT '',
    smtp_port           INTEGER DEFAULT 587,
    smtp_user           TEXT DEFAULT '',
    smtp_password       TEXT DEFAULT '',
    smtp_from           TEXT DEFAULT '',
    smtp_use_tls        INTEGER DEFAULT 1,
    whatsapp_api_url    TEXT DEFAULT '',             -- URL til WhatsApp-bro/gateway
    whatsapp_api_key    TEXT DEFAULT '',             -- API-nøgle (sendes som Bearer-token)
    base_url            TEXT DEFAULT '',             -- offentlig URL (til links i mails)
    default_deadline_days INTEGER DEFAULT 4,         -- standard: frist X dage før event-start
    github_repo         TEXT DEFAULT '',             -- "ejer/repo" til opdaterings-tjek
    update_branch       TEXT DEFAULT 'main'
);

CREATE TABLE IF NOT EXISTS groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    user_password       TEXT DEFAULT '',          -- plaintext: skal kunne "vises" i admin (delt adgangskode)
    admin_password_hash TEXT NOT NULL,
    mail_enabled        INTEGER DEFAULT 0,         -- slået til af master admin
    whatsapp_enabled    INTEGER DEFAULT 0,
    admin_email         TEXT DEFAULT '',           -- modtager af admin-notifikationer (mail)
    whatsapp_recipient  TEXT DEFAULT '',           -- WhatsApp bruger-nr eller gruppe-id
    image_path          TEXT DEFAULT '',           -- logo/billede vist på bruger-siden
    login_text          TEXT DEFAULT '',           -- tekst vist på bruger-login-skærmen
    templates_enabled   INTEGER DEFAULT 0,         -- master tillader admin at redigere mail-skabeloner
    user_accounts_enabled INTEGER DEFAULT 0,       -- individuelle bruger-konti (login m. brugernavn)
    created_at          TEXT NOT NULL
);

-- Individuelle brugere (globalt unikke brugernavne). Kan være med i flere grupper.
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash       TEXT NOT NULL,
    email               TEXT DEFAULT '',
    whatsapp            TEXT DEFAULT '',
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_groups (
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

-- Tilpassede mail-skabeloner pr. gruppe (tom = brug standard fra koden)
CREATE TABLE IF NOT EXISTS mail_templates (
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    tkey                TEXT NOT NULL,             -- new_signup | change | receipt | reminder
    subject             TEXT DEFAULT '',
    body                TEXT DEFAULT '',
    PRIMARY KEY (group_id, tkey)
);

CREATE TABLE IF NOT EXISTS group_fields (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,
    field_type          TEXT NOT NULL CHECK (field_type IN ('text','dropdown','checkbox')),
    options             TEXT DEFAULT '',           -- JSON-liste til dropdown
    required            INTEGER DEFAULT 0,
    is_decline          INTEGER DEFAULT 0,         -- "deltager ikke": kun navn kræves hvis afkrydset
    multiline           INTEGER DEFAULT 0,         -- notefelt: flerlinjet tekst (alle kan se den)
    sort_order          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    slug                TEXT NOT NULL,
    name                TEXT NOT NULL,
    event_date          TEXT NOT NULL,             -- YYYY-MM-DD
    start_time          TEXT DEFAULT '',           -- HH:MM
    end_time            TEXT DEFAULT '',           -- HH:MM
    description         TEXT DEFAULT '',
    expected_count      INTEGER DEFAULT 0,
    signup_deadline     TEXT DEFAULT '',           -- YYYY-MM-DDTHH:MM
    notify_new_signup   INTEGER DEFAULT 0,
    notify_change       INTEGER DEFAULT 0,
    notify_receipt      INTEGER DEFAULT 0,
    notify_reminder     INTEGER DEFAULT 0,
    reminder_sent       INTEGER DEFAULT 0,
    csv_after_deadline  INTEGER DEFAULT 0,         -- send CSV til admin 2t efter frist
    csv_sent            INTEGER DEFAULT 0,
    capacity_limit      INTEGER DEFAULT 0,         -- hård grænse: ingen tilmelding ud over forventet antal
    notify_deadline     INTEGER DEFAULT 0,         -- besked til admin (m. link) når fristen er nået
    deadline_sent       INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    UNIQUE (group_id, slug)
);

CREATE TABLE IF NOT EXISTS registrations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    email               TEXT DEFAULT '',
    phone               TEXT DEFAULT '',
    user_id             INTEGER DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,  -- ejer (individuel bruger)
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_values (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_id     INTEGER NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
    field_id            INTEGER NOT NULL REFERENCES group_fields(id) ON DELETE CASCADE,
    value               TEXT DEFAULT ''
);

-- Aktivitetslog til master-admin (oprettelser + sendte mail/WhatsApp-beskeder)
CREATE TABLE IF NOT EXISTS activity_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,
    category            TEXT NOT NULL,             -- group | event | signup | mail | whatsapp
    group_slug          TEXT DEFAULT '',
    message             TEXT NOT NULL
);

-- Punkter der er skjult på et bestemt event (default: alle vises)
CREATE TABLE IF NOT EXISTS event_hidden_fields (
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    field_id            INTEGER NOT NULL REFERENCES group_fields(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, field_id)
);
