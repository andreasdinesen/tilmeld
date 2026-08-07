# Tilmeld

Event-tilmeldingssystem med tre niveauer: bruger, gruppe-admin og master-admin.
Bygget i Python (Flask) + SQLite. Databasen oprettes automatisk ved første opstart.

## Kør lokalt på din Mac

```bash
cd ~/tilmeld
bash run.sh
```

Åbn derefter:

- **Master-admin:** http://localhost:8080/master
- **Bruger-side:** http://localhost:8080/<gruppenavn>
- **Gruppe-admin:** http://localhost:8080/<gruppenavn>/admin

Første gang er master-password `admin` (med mindre du sætter `MASTER_PASSWORD`).
Skift det under **Opsætning** på master-siden med det samme.

### Sæt eget master-password

```bash
MASTER_PASSWORD="dit-hemmelige-kodeord" bash run.sh
```

## Sådan hænger det sammen

1. **Master-admin** (`/master`) opretter grupper, sætter hver gruppes admin-password
   og slår mail/WhatsApp til/fra. Her konfigureres også SMTP og WhatsApp-gateway globalt.
2. **Gruppe-admin** (`/gruppe/admin`) opretter events, definerer tilmeldings-punkter
   (tekst/dropdown/checkbox, påkrævet eller ej), sætter/sletter gruppe-password og
   henter deltagerlister (vis eller CSV).
3. **Brugere** (`/gruppe`) logger ind med gruppe-password, ser kommende events
   (afsluttede skjules, låste vises i anden farve efter frist), tilmelder sig og
   redigerer deres tilmelding.

`/master` og `/gruppe/admin` er reserverede og kan ikke oprettes som gruppe-/event-navne.

## Startside

Under **master → Opsætning → Startside** kan du vælge, hvilken gruppe forsiden `/` skal vise.
Så lander et rent domæne direkte på gruppens bruger-side i stedet for en teknisk oversigt.
Vælges »Ingen«, vises oversigten. `/master` og `/gruppe/admin` virker uændret, og peger
valget på en gruppe der senere slettes, falder forsiden automatisk tilbage til oversigten.

## Tema

Lyst og mørkt tema med Auto/Lys/Mørk i topbaren. Valget gemmes i browseren og sættes før
første paint, så mørkt tema ikke blinker hvidt ved sideskift.

## Passkeys

Alle tre logins — master, gruppe-admin og brugerkonti — kan bruge passkeys (fingeraftryk,
ansigt eller skærmlås) i stedet for at taste adgangskoden. Nøgler oprettes under
**Opsætning** (master og gruppe-admin) eller **Min profil** (bruger).

En passkey er altid et **tillæg**, aldrig en erstatning: adgangskoden virker uændret.
Det er med vilje — WebAuthn kræver en sikker forbindelse (https eller localhost), og
tilgår man appen på `IP:port` over almindelig http, kan passkeys ikke bruges. Der er
adgangskoden den eneste vej ind, og knapperne skjules med en forklaring.

Domænet er en del af nøglen: skifter appens domæne, holder eksisterende passkeys op med
at virke, og der skal oprettes nye. `rp_id` udledes af `X-Forwarded-Host`/`-Proto`, så det
virker bag en reverse proxy (fx Cloudflare Tunnel) uden konfiguration.

## Notifikationer

Uden SMTP/WhatsApp-konfiguration logges notifikationer i serverens konsol — fint til test.
Sæt rigtige værdier under master → Opsætning for at sende rigtige beskeder.

**WhatsApp** sendes via en HTTP-bro/gateway du selv hoster (fx wppconnect/Baileys).
Tilmeld kalder den med `POST <gateway-url>` og JSON-body `{"to": "<nummer eller gruppe-id>",
"message": "..."}` samt header `Authorization: Bearer <api-nøgle>`. Konfigurér din bro
til at acceptere det format (eller sæt en lille adapter foran).

## Data

SQLite-filen ligger i `data/tilmeld.db`. Slet mappen for at nulstille alt.

## Docker

```bash
docker build -t tilmeld .
docker run -p 8080:8080 -v tilmeld-data:/data -e MASTER_PASSWORD=skift-mig tilmeld
```

Data (SQLite + uploads) ligger i volumen `/data`. Imaget bygges og udgives også
automatisk til GitHub Container Registry (`ghcr.io/andreasdinesen/tilmeld`) via
GitHub Actions ved hvert push til `main`.

## Yggdrasil-rune

`runes/tilmeld.yaml` pakker appen som en rune til
[yggdrasil](https://github.com/kristianwind/yggdrasil) (peger på GHCR-imaget).

Installér via yggdrasils **"Browse runes on GitHub"**:
- Repository: `andreasdinesen/tilmeld`
- Folder: `runes`

Sæt `MASTER_PASSWORD` ved oprettelsen. Port 8080 eksponeres.

## Version og opdatering

Der er **ét versionsnummer**: runens `version:` i `runes/tilmeld.yaml`. Det er det tal,
Yggdrasil-panelet viser, og det står under **master → System** som et link til
[versionsloggen](CHANGELOG.md).

Opdatering sker i panelet — ikke inde i appen:

1. **Runes → Browse GitHub → Reload** henter den nye rune-definition.
2. **Serveren → Settings → Update/Reinstall** henter det nye Docker-image.

`/data` (database og uploads) overlever begge trin.

GitHub-repoet sættes under **master → Opsætning** og bruges til at slå versionsloggen op.
