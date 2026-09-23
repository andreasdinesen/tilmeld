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
    sms_username        TEXT DEFAULT '',             -- Gigahost: brugernavn (som i Kontrolcenteret)
    sms_password        TEXT DEFAULT '',             -- Gigahost: API-adgangskode (ikke login-koden)
    sms_sender          TEXT DEFAULT '',             -- afsendernummer, VERIFICERET hos Gigahost
    base_url            TEXT DEFAULT '',             -- offentlig URL (til links i mails)
    default_deadline_days INTEGER DEFAULT 4,         -- standard: frist X dage før event-start
    github_repo         TEXT DEFAULT 'andreasdinesen/tilmeld',  -- "ejer/repo" til opdaterings-tjek
    update_branch       TEXT DEFAULT 'main',
    default_group       TEXT DEFAULT '',             -- slug: forsiden "/" sender videre hertil
    vapid_public        TEXT DEFAULT '',             -- Web Push: afsender-nøglepar. Laves én
    vapid_private       TEXT DEFAULT ''              -- gang; skiftes de, dør ALLE abonnementer.
);

CREATE TABLE IF NOT EXISTS groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    user_password       TEXT DEFAULT '',          -- plaintext: skal kunne "vises" i admin (delt adgangskode)
    admin_password_hash TEXT NOT NULL,
    mail_enabled        INTEGER DEFAULT 0,         -- slået til af master admin
    whatsapp_enabled    INTEGER DEFAULT 0,
    sms_enabled         INTEGER DEFAULT 0,
    admin_email         TEXT DEFAULT '',           -- modtager af admin-notifikationer (mail)
    whatsapp_recipient  TEXT DEFAULT '',           -- WhatsApp bruger-nr eller gruppe-id
    sms_recipient       TEXT DEFAULT '',           -- admins mobilnummer (SMS har ingen gruppechat,
                                                   -- derfor sit eget felt ved siden af WhatsApps)
    catering_email      TEXT DEFAULT '',           -- madbestilleren: gruppens standard. Et event
    catering_phone      TEXT DEFAULT '',           -- kan overskrive begge (events.catering_*).
    image_path          TEXT DEFAULT '',           -- logo/billede vist på bruger-siden
    login_text          TEXT DEFAULT '',           -- tekst vist på bruger-login-skærmen
    templates_enabled   INTEGER DEFAULT 0,         -- master tillader admin at redigere mail-skabeloner
    user_accounts_enabled INTEGER DEFAULT 0,       -- individuelle bruger-konti (login m. brugernavn)
    calendar_token      TEXT DEFAULT '',           -- hemmelig token til .ics-abonnement
    notify_list_enabled INTEGER DEFAULT 0,         -- notifikationsliste: varsling om nye events
    notify_list_days    INTEGER DEFAULT 14,        -- varsling sendes X dage før event-start
    notify_list_users   INTEGER DEFAULT 1,         -- medtag gruppens brugere (når konti er slået til)
    push_enabled        INTEGER DEFAULT 0,         -- push-notifikationer (slået til af master)
    signup_from_members INTEGER DEFAULT 0,         -- tilmelding vælger navn fra
                                                   -- medlemslisten i stedet for fritekst
    members_visible     INTEGER DEFAULT 0,         -- må medlemmerne se hinandens
                                                   -- kontaktoplysninger? Slås til af admin.
    home_text           TEXT DEFAULT '',           -- Markdown over »Kommende events« på forsiden
    rules_text          TEXT DEFAULT '',           -- Markdown: gruppens ordensregler (egen side)
    facebook_url        TEXT DEFAULT '',           -- adressen på klubbens Facebook-gruppe,
                                                   -- så »Del«-kortet kan åbne den direkte
    files_enabled       INTEGER DEFAULT 0,         -- må admin vedhæfte filer på events (master styrer
                                                   -- det: filerne fylder på SERVERENS disk)
    created_at          TEXT NOT NULL
);

-- Individuelle brugere (globalt unikke brugernavne). Kan være med i flere grupper.
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash       TEXT NOT NULL,
    name                TEXT DEFAULT '',           -- fulde navn (bruges automatisk ved tilmelding)
    email               TEXT DEFAULT '',
    whatsapp            TEXT DEFAULT '',           -- mobilnummer: bruges til BÅDE WhatsApp og SMS.
                                                   -- Kolonnenavnet er historisk (SMS kom til igen
                                                   -- efter WhatsApp) — ét nummer, to kanaler.
    hide_from_members   INTEGER DEFAULT 0,         -- brugerens eget valg: stå ikke på
                                                   -- gruppens synlige medlemsliste
    reset_token         TEXT DEFAULT '',           -- "glemt adgangskode"-token
    reset_expires       TEXT DEFAULT '',
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
    is_meal_decline     INTEGER DEFAULT 0,         -- "spiser ikke med": deltager, men skal ikke have
                                                   -- mad. Trækkes fra i madbestillingens antal.
                                                   -- Udelukker is_decline (et afbud spiser slet ikke).
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
    waitlist_enabled    INTEGER DEFAULT 0,         -- venteliste når kapaciteten er fyldt
    allow_guests        INTEGER DEFAULT 0,         -- tilmelding kan omfatte flere pladser (+1)
    notify_event_reminder INTEGER DEFAULT 0,       -- påmindelse 24t før selve eventet
    event_reminder_sent INTEGER DEFAULT 0,
    notify_list         INTEGER DEFAULT 0,         -- varsl notifikationslisten om dette event
    notify_list_sent    INTEGER DEFAULT 0,
    notify_catering     INTEGER DEFAULT 0,         -- besked til madbestilleren når fristen er nået
    catering_sent       INTEGER DEFAULT 0,
    catering_email      TEXT DEFAULT '',           -- tomt = brug gruppens standard
    catering_phone      TEXT DEFAULT '',
    -- Resultatet EFTER jagten. Bevidst uden for event-formularens vals-tuple, så en
    -- kopi af et event aldrig arver sidste jagts udbytte.
    leaders             TEXT DEFAULT '',           -- JSON: op til 2 henvisninger til
                                                   -- medlemslisten, fx ["m:4","u:brian"]
    result_game         TEXT DEFAULT '',           -- antal skudt vildt (tekst: »12« eller
                                                   -- »8 fasaner, 2 harer«)
    result_winner       TEXT DEFAULT '',           -- vinder af bengættet
    result_legs         INTEGER DEFAULT 0,         -- bengættets facit: samlet antal ben
    result_note         TEXT DEFAULT '',           -- Markdown: alt andet værd at nævne
    created_at          TEXT NOT NULL,
    updated_at          TEXT DEFAULT '',           -- iCal LAST-MODIFIED
    revision            INTEGER DEFAULT 0,         -- iCal SEQUENCE: tælles op når noget
                                                   -- kalender-relevant ændres
    UNIQUE (group_id, slug)
);

CREATE TABLE IF NOT EXISTS registrations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    email               TEXT DEFAULT '',
    phone               TEXT DEFAULT '',
    user_id             INTEGER DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,  -- ejer (individuel bruger)
    seats               INTEGER DEFAULT 1,         -- antal pladser (dig + gæster)
    guest_names         TEXT DEFAULT '',           -- JSON-liste med gæsternes navne
                                                   -- (valgfri; højst seats-1 navne)
    waitlist            INTEGER DEFAULT 0,         -- 1 = står på venteliste
    attended            INTEGER DEFAULT 0,         -- fremmøde markeret af admin
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_values (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_id     INTEGER NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
    field_id            INTEGER NOT NULL REFERENCES group_fields(id) ON DELETE CASCADE,
    value               TEXT DEFAULT ''
);

-- Aktivitetslog til master-admin (oprettelser + sendte mail/WhatsApp/SMS-beskeder)
CREATE TABLE IF NOT EXISTS activity_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,
    category            TEXT NOT NULL,             -- group | event | signup | mail | whatsapp | sms | push
    group_slug          TEXT DEFAULT '',
    message             TEXT NOT NULL
);

-- Passkeys (WebAuthn). Én række pr. registreret nøgle.
-- scope = 'master' (group_id/user_id NULL) | 'admin' (group_id sat) | 'user' (user_id sat).
CREATE TABLE IF NOT EXISTS credentials (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scope               TEXT NOT NULL CHECK (scope IN ('master','admin','user')),
    group_id            INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
    credential_id       TEXT NOT NULL UNIQUE,        -- base64url
    public_key          TEXT NOT NULL,               -- base64url (COSE)
    sign_count          INTEGER DEFAULT 0,
    name                TEXT DEFAULT '',             -- brugerens eget navn på nøglen
    created_at          TEXT NOT NULL,
    last_used           TEXT DEFAULT ''
);

-- Notifikationsliste pr. gruppe: modtagere der varsles om nye events.
-- Gruppens egne brugere hentes direkte fra users/user_groups og står IKKE her --
-- denne tabel er kun til dem, der ikke har en konto.
CREATE TABLE IF NOT EXISTS notify_recipients (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name                TEXT DEFAULT '',
    email               TEXT DEFAULT '',
    whatsapp            TEXT DEFAULT '',           -- mobilnummer til WhatsApp-broen OG til SMS
                                                   -- (samme nummer; se users.whatsapp)
    active              INTEGER DEFAULT 1,         -- sat på pause uden at blive slettet
    hidden              INTEGER DEFAULT 0,         -- står ikke på den synlige medlemsliste
                                                   -- (men får stadig notifikationer)
    created_at          TEXT NOT NULL
);

-- Filer vedhæftet et event (program, kort, menu ...).
--
-- Navnet på disken er et TILFÆLDIGT token, ikke brugerens filnavn. To grunde:
-- `secure_filename` æder æ/ø/å (»Køreplan.pdf« bliver til »Kreplan.pdf«), og to
-- filer med samme navn ville overskrive hinanden. Det rigtige navn står i
-- `original_name` og bruges, når filen hentes.
CREATE TABLE IF NOT EXISTS event_files (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    stored_name         TEXT NOT NULL,             -- <token>.<ext> i uploads/<slug>/events/<event_id>/
    original_name       TEXT NOT NULL,             -- vises i UI og bruges som download-navn
    size                INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- Vildtarter pr. gruppe. Admin bestemmer selv listen — et konsortium skyder ikke
-- det samme som et andet — og så kan udbyttet tælles op på tværs af året.
CREATE TABLE IF NOT EXISTS game_species (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    sort_order          INTEGER DEFAULT 0
);

-- Udbyttet pr. event pr. art. Kun rækker med et tal gemmes, så en jagt uden
-- råvildt ikke fylder en nul-række.
CREATE TABLE IF NOT EXISTS event_game (
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    species_id          INTEGER NOT NULL REFERENCES game_species(id) ON DELETE CASCADE,
    antal               INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_id, species_id)
);

-- Dokumenter på gruppens forside (vedtægter, jagtplan ...). Samme regler som
-- event_files: tilfældigt navn på disken, det rigtige i original_name.
CREATE TABLE IF NOT EXISTS group_files (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    stored_name         TEXT NOT NULL,             -- <token>.<ext> i uploads/<slug>/dokumenter/
    original_name       TEXT NOT NULL,
    size                INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- Web Push-abonnementer: én række pr. ENHED, ikke pr. person. Samme menneske har
-- typisk både en telefon og en bærbar, og de skal begge have besked.
--
-- scope afgør HVAD enheden får, og spejler passkeys' tre scopes:
--   'user'  = en deltager med brugerkonto (user_id sat) -> kvittering, påmindelser
--   'admin' = gruppe-admins egen enhed                  -> admin-beskeder
--   'list'  = notifikationslisten (ingen identitet)     -> varsling om nye events
--
-- endpoint ER hemmeligheden bag et abonnement: den, der har adressen, kan sende
-- til enheden. Vis den aldrig i et UI, og log den aldrig.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
    scope               TEXT NOT NULL CHECK (scope IN ('user','admin','list')),
    endpoint            TEXT NOT NULL UNIQUE,
    p256dh              TEXT DEFAULT '',           -- modtagerens offentlige nøgle (base64url)
    auth                TEXT DEFAULT '',           -- modtagerens auth-hemmelighed (base64url)
    label               TEXT DEFAULT '',           -- brugerens eget navn på enheden
    created_at          TEXT NOT NULL,
    last_ok             TEXT DEFAULT '',
    fails               INTEGER NOT NULL DEFAULT 0
);

-- Punkter der er skjult på et bestemt event (default: alle vises)
CREATE TABLE IF NOT EXISTS event_hidden_fields (
    event_id            INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    field_id            INTEGER NOT NULL REFERENCES group_fields(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, field_id)
);
