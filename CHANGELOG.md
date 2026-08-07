# Versionslog

Versionsnummeret er runens `version:` i [`runes/tilmeld.yaml`](runes/tilmeld.yaml) — det
samme nummer, Yggdrasil-panelet viser, og det der står under **master → System** i appen.

Sådan opdaterer du:

1. **Runes → Browse GitHub → Reload** henter den nye rune-definition (det nye nummer
   dukker op i listen).
2. **Serveren → Settings → Update/Reinstall** henter den nye app.

Databasen i `/data` overlever begge trin.

---

## Version 9

**Systemsiden viser nu ét versionsnummer.**

- Master → System viser runens versionsnummer — det samme, panelet viser — som et link
  til denne versionslog.
- »App-version & opdatering« er fjernet fra System-siden. Opdatering hører hjemme i
  Yggdrasil-panelet, og de to veje kunne komme i karambolage med hinanden: appen kører
  fra et Docker-image, så et `git pull` inde i containeren ville alligevel blive kastet
  væk ved næste geninstallation.
- Den separate `VERSION`-fil er væk. Der er ét versionsnummer i stedet for to, der
  kunne komme ud af trit.

## Version 8

**Nyt design, tema-skift, startside og passkeys.**

- **Nyt design** i samme udtryk som de øvrige runer (Bogreolen, Kokkeri, Beanledger,
  Muldbog): rolig papirfarvet palet, kort med bløde kanter, pilleformede mærkater.
- **Lyst og mørkt tema** med Auto/Lys/Mørk i topbaren. Valget gemmes i browseren og
  sættes før første optegning, så mørkt tema ikke blinker hvidt ved sideskift.
- **Startside-gruppe**: master kan vælge, hvilken gruppe forsiden `/` skal sende videre
  til, så et rent domæne lander direkte på gruppen. Peger valget på en gruppe, der
  senere slettes, falder forsiden automatisk tilbage til oversigten.
- **Passkeys** (WebAuthn) til master-, gruppe-admin- og bruger-login: log ind med
  fingeraftryk, ansigt eller skærmlås. En passkey er altid et **tillæg** —
  adgangskoden virker uændret, fordi passkeys kræver https, og panelet tilgås over
  almindelig http på `IP:port`. Nøgler oprettes under Opsætning (master og
  gruppe-admin) eller Min profil (bruger).
- Deltagerlisten kan scrolles vandret i stedet for at klemme kolonnerne sammen til ét
  ord pr. linje.
- Adresse-oversigten (`/gruppe`, `/gruppe/admin`, `/master`) er flyttet fra forsiden op
  på master-forsiden, hvor den er relevant.
- Ny afhængighed: `webauthn` (py_webauthn). Kører du appen fra et `git clone` frem for
  runen, skal `pip install -r requirements.txt` køres — ellers starter appen uden
  passkeys, men med adgangskode-login i behold.

## Version 7 og tidligere

Ikke dokumenteret her. Version 7 svarede til app-version 1.1.0 med individuelle
brugerkonti, venteliste, gæstepladser, fremmøde, iCal-feed, WhatsApp-notifikationer og
fil-baseret nulstilling af master-password.
