# Versionslog

Versionsnummeret er runens `version:` i [`runes/tilmeld.yaml`](runes/tilmeld.yaml) — det
samme nummer, Yggdrasil-panelet viser, og det der står under **master → System** i appen.

Sådan opdaterer du:

1. **Runes → Browse GitHub → Reload** henter den nye rune-definition (det nye nummer
   dukker op i listen).
2. **Serveren → Settings → Update/Reinstall** henter den nye app.

Databasen i `/data` overlever begge trin.

---

## Version 10

**Tilmeldings-punkter kan rettes — og siden bliver, hvor du er.**

- **Redigér et tilmeldings-punkt** i stedet for at slette og oprette det forfra. Under
  gruppe-admin → Opsætning har hvert punkt nu en »Redigér«, der folder en udfyldt
  formular ud: navn, type, dropdown-valg, påkrævet og »deltager ikke«.
  Punktets id bevares, så eksisterende svar i tilmeldingerne følger med.
- Formularen viser, hvor mange tilmeldinger punktet allerede er udfyldt i, og efter
  gemning advares der konkret, hvis ændringen rører ved gamle svar — fx hvis et
  dropdown-valg, nogen har brugt, ikke længere findes. Ændringen blokeres aldrig; en
  tastefejl i navnet skal kunne rettes uden videre.
- **Siden hopper ikke længere til toppen**, når du flytter et punkt op/ned, sletter
  eller gemmer. Opsætnings-siden svarer nu med et redirect tilbage til det afsnit, du
  arbejdede i, så du kan flytte flere punkter i træk. Det gør også F5 uskadelig — før
  gensendte browseren den sidste handling.
- ▲/▼/Slet bliver på én linje, også når punktets navn er langt.
- Dropdown-valg gemmes kun på dropdowns. Skiftede man type, kunne den gamle liste
  ellers blive stående og dukke op igen som en spøgelses-liste under punktet.
- **Tilmeldingsfristen følger nu med, når du flytter et events dato.** Fristen beholder
  sin afstand til eventets start: har et event frist 7 dage før, har det stadig frist
  7 dage før på den nye dato. Det gør især en **kopi** brugbar — kopien arver
  originalens frist, som hører til den gamle dato, så et kopieret event var lukket for
  tilmelding fra det øjeblik, det blev oprettet. Sætter du selv en frist, bevares din
  afstand; rydder du feltet, falder den tilbage til master-standarden.
- Gemmer du alligevel et event, hvor fristen er passeret (eller ligger efter eventets
  start), siger appen det nu tydeligt i stedet for at lade det gå ubemærket hen.
- **Linket til eventet står nu øverst i kalender-postens beskrivelse** i stedet for
  nederst — i en kalender-app er beskrivelsen et lille notefelt, og et link efter en
  lang tekst skal man scrolle efter. Linket ligger både i iCal-feltet `URL` (som Apple
  Kalender viser som en klikbar række) og i beskrivelsen, fordi bl.a. Google ignorerer
  `URL`-feltet.
  Som hidtil kræver det, at »Offentlig URL« er udfyldt under master → Opsætning.
  Adressen udledes **bevidst ikke** af forespørgslen: appen kan nås på flere adresser,
  og et link havner permanent i folks kalender — et forkert link er værre end intet.
- **Ændringer slår nu igennem i folks kalender.** Hvert event får `SEQUENCE` og
  `LAST-MODIFIED`, som fortæller kalenderen, at det er en *nyere udgave* af et event,
  den allerede kender — så en `.ics`-fil, der hentes igen efter en ændring, opdaterer
  eventet i stedet for at blive en dublet. Revisionen tælles kun op, når noget
  kalender-relevant ændres (navn, dato, tidspunkter, beskrivelse) — ikke når du
  retter et notifikations-flueben eller forventet antal.
- **Ved »Tilføj til kalender« står der nu, hvad knappen faktisk gør**: den lægger kun
  dét ene event ind som en kopi, der ikke opdateres. Ved siden af er der et link til
  gruppens kalender-abonnement, som giver alle events automatisk — og følger med, når
  noget ændrer sig. Abonnements-boksen folder sig ud, når man kommer fra linket, og
  forklarer nu også, at kalender-apps selv bestemmer, hvor tit de henter nyt (Google op
  til et døgn, Apple ofte kun én gang om ugen som standard).
- Kalender-feedet sendte `Content-Type: text/calendar; charset=utf-8; charset=utf-8`
  — charset stod der to gange. Rettet.

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
