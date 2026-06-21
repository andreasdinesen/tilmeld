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
   og slår mail/SMS til/fra. Her konfigureres også SMTP/SMS globalt.
2. **Gruppe-admin** (`/gruppe/admin`) opretter events, definerer tilmeldings-punkter
   (tekst/dropdown/checkbox, påkrævet eller ej), sætter/sletter gruppe-password og
   henter deltagerlister (vis eller CSV).
3. **Brugere** (`/gruppe`) logger ind med gruppe-password, ser kommende events
   (afsluttede skjules, låste vises i anden farve efter frist), tilmelder sig og
   redigerer deres tilmelding.

`/master` og `/gruppe/admin` er reserverede og kan ikke oprettes som gruppe-/event-navne.

## Notifikationer

Uden SMTP/SMS-konfiguration logges notifikationer i serverens konsol — fint til test.
Sæt rigtige værdier under master → Opsætning for at sende rigtige beskeder.
SMS bruger GatewayAPI (dansk) som standard.

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

`rune.yaml` pakker appen som en rune til
[yggdrasil](https://github.com/kristianwind/yggdrasil) (peger på GHCR-imaget).
Installér den i yggdrasil ved at importere YAML-filen, eller pege på denne repo.
Sæt `MASTER_PASSWORD` ved oprettelsen. Port 8080 eksponeres.

## Opdatering fra master-admin

Under **master → Opsætning** kan du sætte GitHub-repo (`andreasdinesen/tilmeld`)
og branch. Master → **System** viser app-versionen, kan tjekke GitHub for en nyere
version (læser `VERSION`-filen) og køre `git pull` + opdatering, når appen er
installeret via `git clone`.
