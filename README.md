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
   og slår mail/WhatsApp/SMS/push til/fra. Her konfigureres også SMTP, WhatsApp-gateway
   og SMS globalt.
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

Lyst og mørkt tema. Én ikon-knap i topbaren vipper mellem dem og viser det tema, et klik
giver dig. Siden starter altid på **systemets** tema og følger det live, indtil du selv
trykker; dit valg gælder fanen og forsvinder, når du åbner appen igen. Temaet sættes før
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

Uden SMTP/WhatsApp/SMS-konfiguration logges notifikationer i serverens konsol — fint til test.
Sæt rigtige værdier under master → Opsætning for at sende rigtige beskeder.

**WhatsApp** sendes via en HTTP-bro/gateway du selv hoster (fx wppconnect/Baileys).
Tilmeld kalder den med `POST <gateway-url>` og JSON-body `{"to": "<nummer eller gruppe-id>",
"message": "..."}` samt header `Authorization: Bearer <api-nøgle>`. Konfigurér din bro
til at acceptere det format (eller sæt en lille adapter foran).

## SMS (Gigahost)

SMS er den fjerde kanal og følger samme regler som de øvrige: master sætter den op
globalt og slår den til pr. gruppe, og event-fluebenene bestemmer, hvad der sendes.

Under **master → Opsætning → SMS (Gigahost)** udfyldes tre felter:

| Felt | Hvad |
|---|---|
| Brugernavn | Det samme som til [Gigahost Kontrolcenter](https://controlcenter.gigahost.dk/). |
| API-adgangskode | Oprettes i Kontrolcenteret — **ikke** den, du logger ind med. |
| Afsendernummer | Et nummer, der er **verificeret** på kontoen. |

Alle tre skal være der, før kanalen regnes for sat op. Knappen **»Tjek saldo og
afsendernumre«** slår kontoen op og viser saldoen, de godkendte afsendernumre og om dit
afsendernummer står på listen — det er dér, fejlen næsten altid sidder.

Der skal være **SMS-klip** på kontoen; gatewayen afviser en besked, hvis saldoen ikke
rækker. Kontoen topper man op i Kontrolcenteret.

Deltagerens **mobilnummer er ét felt**, som både WhatsApp og SMS bruger — ét nummer, op
til to kanaler. Er begge slået til for gruppen, får modtageren begge dele. SMS har
derimod sit **eget admin-modtagerfelt**, fordi WhatsApp-modtageren også kan være en
gruppechat, og et gruppe-id kan man ikke sende en SMS til.

En besked må fylde højst **3 SMS-dele** (459 tegn i latin-1). Appen oversætter selv de
typografiske tegn, skabelonerne bruger (`—` → `-`, `…` → `...`), så ét enkelt tegn ikke
tvinger hele beskeden over i unicode og halverer pladsen til 201 tegn. Er teksten stadig
for lang, forkortes den.

Gatewayen slås op i DNS (`_sms._tcp.tel.gigahost.dk`), så Gigahost kan flytte den uden
at appen skal rettes; svarer den ene ikke, bruges den næste. Hele API-klienten ligger i
`gigasms.py` og bruger kun stdlib — ingen ny afhængighed. Slå gatewayene op med:

```bash
./.venv/bin/python gigasms.py
```

Med `GIGAHOST_USER` og `GIGAHOST_PASSWORD` i miljøet henter den også saldo og
afsendernumre.

## Notifikationsliste

Gruppe-admin kan under **Notifikationsliste** samle de modtagere, der skal høre om et nyt
event — også dem, der ikke er tilmeldt noget endnu. Modtagerne kan have en mailadresse,
et mobilnummer eller begge dele; kun de kanaler, master har slået til for gruppen, vises.
Mobilnummeret bruges af både WhatsApp og SMS.

Kører gruppen med individuelle bruger-konti, hentes brugernes mail og mobilnummer
**direkte fra deres profil** — de skal ikke skrives ind på listen. Admin kan tilføje
ekstra modtagere i hånden, og dubletter sendes der kun til én gang.

Listen bruges to steder:

- **48 timer før tilmeldingsfristen**, hvis eventet har fluebenet »Påmindelse 48t før
  frist«. Går til hele listen — også dem, der ikke har svaret endnu — med et link til eventet.
- **Automatisk**, når der er et valgt antal dage (standard 14) til et events start. Et
  event oprettet tættere på end det varsles med det samme. Hvert event kan holdes ude
  med fluebenet under *Notifikationer* i event-formularen.
- **Manuelt**, med »Send nu«: enten varslingen om et valgt event eller en helt fri besked.
  `{name}` bliver modtagerens navn og `{group}` gruppens.

Teksten er en mail-skabelon (»Nyt event«) og kan rettes under **Opsætning**, hvis master
har givet gruppen lov til at redigere skabeloner.

## Madbestilling

Den, der skal bestille mad til et event, kan få besked med **antallet af kuverter**,
når tilmeldingsfristen er nået. Beskeden sendes én gang, på mail og/eller SMS.

Gruppen sætter en fast madbestiller under **Opsætning → Kontakt → Madbestiller**; hvert
event kan overskrive mail og telefon hver for sig under *Redigér event →
Notifikationer → Madbestilling*, hvor fluebenet også slår beskeden til. En kopi af et
event arver begge dele.

Deltagerlisten viser, hvem beskeden går til og hvor mange kuverter, med en knap
**»Send nu«** — til når tallet skal meldes ind før fristen, eller sendes igen.

### »Spiser ikke med«

Et tilmeldings-punkt kan markeres som et **»spiser ikke med«-felt**: deltageren kommer
til eventet, men trækkes fra madbestillingen. Det oprettes under **Opsætning →
Tilmeldings-punkter** ved siden af »deltager ikke«, og kan skjules på de events, hvor
det ikke giver mening.

Krydset gælder **hele tilmeldingen** — også dens gæster. De to fra-meldinger udelukker
hinanden: et afbud spiser ikke med alligevel. Afbud og venteliste tæller aldrig med.

### Teksten

Skabelonen »Madbestilling« rettes under **Opsætning → Mail-skabeloner** (hvis master har
givet gruppen lov). Ud over de sædvanlige pladsholdere har den fem tal:

| Pladsholder | Betyder |
|---|---|
| `{meals}` | Hvor mange der skal have mad |
| `{count}` | Deltagere + gæster i alt |
| `{no_meals}` | Har meldt fra til spisning |
| `{signups}` | Antal tilmeldinger (personer) |
| `{waitlist}` | Antal på venteliste |

## Del et event (Facebook m.fl.)

Under **Vis liste** på et event kan admin dele det: en færdig tekst at kopiere og en knap,
der åbner klubbens Facebook-gruppe.

**Der er ingen Facebook-bruger at koble på, og ingen del-knap der rammer en gruppe.** Meta
lukkede Groups API 22. april 2024 og fjernede `publish_to_groups`, så ingen app kan slå op
i en gruppe på dine vegne. Og Facebooks egen del-dialog kan kun slå op på din egen væg —
der har aldrig været en gruppe-destination i den.

Vejen der virker: kopiér teksten, åbn gruppen, sæt ind i skrivefeltet. Facebook bygger selv
link-kortet ud fra Open Graph-taggene. Gruppens adresse sættes under **gruppe-admin →
Opsætning → Facebook-gruppe** (kun `facebook.com`-adresser).

Et delt link viser titel, tidspunkt, frist og et uddrag (Open Graph). Da Facebooks robot
ikke kan logge ind, har et event bag adgangskode en **åben forside** med netop de
oplysninger og en »Log ind«-knap — deltagerlisten og tilmeldingen forbliver lukket.
Kræver en **offentlig URL** under master → Opsætning.

## Gæster og deltagertal

Tillader et event gæster, spørger tilmeldingen om **antal gæster** (0 = du kommer alene),
og der kommer et navnefelt pr. gæst. Navnene er valgfri.

Deltagertallet viser både helheden og delene: **6 / 12 deltagere** med
»3 tilmeldt · 3 gæster · 1 afbud« nedenunder. Databasen regner i pladser
(gæster + 1), fordi kapaciteten handler om mennesker — oversættelsen sker ét sted på
serveren.

## Vildtarter og udbytte

Admin opretter gruppens **vildtarter** under Opsætning (en knap indsætter en dansk
standardliste). Resultatet på hver jagt får et talfelt pr. art, og siden **Udbytte** lægger
sæsonen sammen art for art. Jagtsæsonen regnes 1. april – 31. marts.

## Jagtledere og resultat

Hvert event kan få én eller to **jagtledere** valgt fra medlemslisten (i event-formularen).
Medlemmerne trykker på navnet og får mobil og mail frem, klar til at ringe. Kun folk, der
er synlige på medlemslisten, kan vælges.

Efter jagten kan admin udfylde et **Resultat** nederst på deltagerlisten: antal skudt
vildt, vinder af bengættet og en note. Det vises på eventets side.

Begge dele gemmes uden for event-formularens øvrige felter, så en kopi af et event ikke
arver hverken jagtledere eller resultat.

## Gem som PDF

Medlemslisten, ordensreglerne og udbyttet har en **Gem som PDF**-knap. Den bruger browserens
egen udskrift (ingen PDF-pakke på serveren); print-arket er sat op til sort på hvidt uden
menuer og knapper.

## Medlemsliste

Notifikationslisten (navn, mail, mobilnummer) kan gøres synlig for medlemmerne: så får
bruger-siden et punkt **Medlemmer** med en klub-telefonbog. Samme liste som
notifikationerne sender til — ét sted at vedligeholde. Slås til under **admin →
Notifikationsliste → Indstillinger** og er som udgangspunkt slået fra.

Den enkelte kan holdes udenfor visningen — admin med **Skjul**, en bruger med konto selv
under **Min profil**. Begge dele påvirker kun listen; notifikationerne kommer stadig frem.

Listen kan også bruges ved **tilmelding**: slås »Vælg navn fra listen« til, bliver
navnefeltet en rullemenu, og et navn, der allerede er tilmeldt, kan ikke vælges igen.
Admin taster stadig frit.

Listen redigeres under **admin → Notifikationsliste → Modtagere**. Mail og numre er
klikbare på medlemssiden (`mailto:`/`tel:`). Slås medlemslisten til, kan der skrives både
mail og mobilnummer ind, også selvom gruppen ikke har SMS eller WhatsApp — oplysningerne
har et formål i sig selv der.

## Ordensregler

Admin skriver gruppens ordensregler under **Opsætning → Bruger-sidens udseende**. De får
deres egen side med link fra forsiden og fra topbaren. Tomt felt = ingen side.

## Forsiden: velkomsttekst og dokumenter

Under **gruppe-admin → Opsætning → Bruger-sidens udseende** kan admin skrive en tekst, der
står over »Kommende events« på forsiden (Markdown), og lægge dokumenter op, der vises
nederst på forsiden — vedtægter, jagtplan, kort. Begge dele ses kun af dem, der er logget
ind. Dokumenterne følger samme regler og samme master-flueben (**Filer**) som filer på et
event, og ligger i `data/uploads/<gruppe>/dokumenter/`.

## Filer på et event

Gruppe-admin kan vedhæfte filer til et event — program, kort, menu — under **Redigér
event**. De vises på eventets side og kan hentes af dem, der har adgang til gruppen.

Højst 25 MB pr. forsendelse, og kun kendte filtyper (pdf, Office/OpenDocument, billeder,
txt, csv, zip, ics). Filer leveres altid som download, aldrig vist i siden. Filerne ligger
i `data/uploads/<gruppe>/events/<id>/` og følger med i runens backup; slettes eventet,
ryddes de fra disken.

**Master slår filer til pr. gruppe** under Opsætning — de fylder på serverens disk.
En kopi af et event får ikke filerne med.

## Push-notifikationer og app på hjemmeskærmen

Hver gruppe har sit eget web-manifest, så `/<gruppe>` kan lægges på hjemmeskærmen som en
app med gruppens navn og eget ikon.

Push er en **kanal ved siden af mail, WhatsApp og SMS og følger de samme regler**:
master slår den til pr. gruppe under **Opsætning**, og event-fluebenene bestemmer, hvad
der sendes. Notifikationer slås til pr. **enhed** tre steder:

| Sted | Scope | Hvad man får |
|---|---|---|
| **Min profil** | deltager med konto | kvittering, påmindelse før frist, påmindelse før eventet |
| **Gruppe-opsætning** | gruppe-admin | ny tilmelding, ændring, »fristen er nået« |
| **Bruger-siden** | alle, også delt adgangskode | varsling om nye events |

Den sidste er vejen for grupper uden individuelle konti: et abonnement hører til en
enhed, ikke til en adresse, så der kræves ingen identitet.

**Push kræver en offentlig URL** under master → Opsætning — notifikationen indeholder
absolutte adresser. **På iPhone og iPad skal siden lægges på hjemmeskærmen først;** Apple
tillader ikke notifikationer fra en almindelig fane. Kan der ikke sendes til enheden,
skriver appen hvorfor.

VAPID og kryptering (RFC 8291) er skrevet direkte oven på `cryptography`, som allerede var
med til passkeys — ingen ny afhængighed, og ingen tjeneste udenom. Nyttelasten krypteres
med enhedens egne nøgler, så Apple og Google aldrig kan læse, hvad eventet hedder.
Krypteringen kan efterprøves mod RFC 8291's officielle testvektor:

```bash
./.venv/bin/python push.py
```

Ikonet (`static/icon-192.png`) genereres af `make_icons.py` og committes; Pillow er derfor
et build-værktøj, ikke en afhængighed.

## Data

SQLite-filen ligger i `data/tilmeld.db`. Slet mappen for at nulstille alt.

## Yggdrasil-rune

`runes/tilmeld.yaml` pakker appen som en rune til
[yggdrasil](https://github.com/kristianwind/yggdrasil).

Installér via yggdrasils **"Browse runes on GitHub"**:
- Repository: `andreasdinesen/tilmeld`
- Folder: `runes`

Sæt `MASTER_PASSWORD` ved oprettelsen. Port 8080 eksponeres.

Runen **bærer ikke koden**. Den kører et almindeligt `python:3.12-slim`-image og
henter app-koden fra repoets `vN`-tag — samme model som doda. Der bygges altså ikke
længere et Docker-image; en **udgivelse er et git-tag**.

Afhængighederne (Flask, waitress, bleach, Markdown, webauthn, cryptography) kan ikke
ligge i repoet — `cryptography` er kompileret — så de installeres i et virtuelt miljø i
`/data/venv` ved første start. Det tager ca. 10 sekunder og fylder ca. 45 MB. Miljøet
bygges om af sig selv, hvis `requirements.txt` eller Python-versionen ændrer sig.

**Overvågning:** runen giver to log-watchers, der sender en notifikation i panelet —
én for `[MAIL-FEJL]`/`[WHATSAPP-FEJL]`/`[SMS-FEJL]`/`[SCHEDULER-FEJL]`/`[LOG-FEJL]` og én
for uhåndterede serverfejl (HTTP 500). En eksisterende server får dem ved næste Reinstall.

**Wipe** starter forfra med en tom database: `tilmeld.db` (og dens journal-filer)
slettes, og ved næste start gælder `MASTER_PASSWORD` igen. `uploads/`, `app/` og `venv/`
røres ikke. Panelet tilbyder en backup først.

**Backup** tager kun brugerdata med (database + `uploads/`). `app/` hentes fra GitHub
og `venv/` bygges på ti sekunder — de hører ikke hjemme i en sikkerhedskopi.

## Version og opdatering

Der er **ét versionsnummer**: runens `version:` i `runes/tilmeld.yaml`. Hele repoet
pakkes ud, så rune-filen følger med koden, og den udpakkede fils `version:` **er** den
kørende udgave — det tal, der står under **master → System** som et link til
[versionsloggen](CHANGELOG.md). Git-taggen `vN` skal derfor matche rune-versionen.

**En genstart er opdateringen.** Ved hver start spørger `kilde.py` GitHub om det højeste
`v<tal>`-tag og henter det, hvis det er nyere end det udrullede. Kan GitHub ikke nås,
kører serveren bare videre på den kode, der ligger — en netværksfejl må aldrig kunne
slukke for tilmeldingerne.

| Handling i panelet | Hvad der sker |
|---|---|
| **Restart** | Henter nyeste udgivelse og starter. Den normale vej. |
| **Opdater Tilmeld** | Samme opdatering, uden at vente på en genstart. |
| **Runes → Browse GitHub → Reload** | Kun nødvendigt når selve *rune-definitionen* har ændret sig (variabler, porte, watchers). |

`KODE_VERSION` er en **lås, ikke et ønske**. Tom = hent nyeste ved hver genstart. Et tal
(fx `20`) henter præcis den udgave og bliver på den, også selvom der findes en nyere.
Det er vejen tilbage, når en udgivelse driller: skriv tallet, gem, genstart.

Til forskel fra den gamle `IMAGE_TAG` **må feltet gerne stå tomt** — en tom værdi læses
som »nyeste« i stedet for at blive sat ind i en image-adresse.

### Sådan udgives en ny version

```bash
# 1. bump version: i runes/tilmeld.yaml + skriv et afsnit i CHANGELOG.md
git commit -am "feat: ... (rune N)"
git tag vN
git push && git push --tags
```

Taggen **skal** være pushet — det er den, runen henter fra. Uden den finder en ny
installation ingen kode.

GitHub-repoet sættes under **master → Opsætning** og bruges til at slå versionsloggen op.
