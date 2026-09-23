# Versionslog

Versionsnummeret er runens `version:` i [`runes/tilmeld.yaml`](runes/tilmeld.yaml) — det
samme nummer, Yggdrasil-panelet viser, og det der står under **master → System** i appen.

Sådan opdaterer du: **tryk Restart.** Fra rune 21 henter appen selv nyeste udgivelse
fra GitHub, hver gang den starter. Har selve *rune-definitionen* ændret sig (variabler,
porte, watchers), skal **Runes → Browse GitHub → Reload** køres først.

Databasen i `/data` overlever begge dele.

---

## Version 27

**Ordensregler, synlige links på forsiden — og telefonnumre uden SMS.**

### Ordensregler

Et nyt felt under **Opsætning → Bruger-sidens udseende**, hvor admin skriver gruppens
ordensregler i Markdown. De får deres egen side med et link fra forsiden. Tomt felt =
ingen side og intet link.

### Links på forsiden

Medlemsliste og ordensregler stod kun i topbaren. På en telefon er menuen det første, man
scroller forbi, så de findes nu også som knapper på forsiden — kun dem, der er noget at
vise.

### Rettelse: telefonnumre krævede SMS eller WhatsApp

Nummerfeltet på notifikationslisten blev kun vist, hvis SMS eller WhatsApp var sat op.
Det er rigtigt for en modtagerliste — men en **medlemsliste** har brug for nummeret
uanset. En klub med kun mail kunne derfor ikke få telefonnumre i sin telefonbog.

Nu åbner begge felter, så snart medlemslisten er slået til, og etiketten siger hvorfor
(»Mobilnummer (medlemslisten)«). Er medlemslisten slået fra, er alt som før: der
indsamles ikke oplysninger, der ikke skal bruges til noget.

### En vej tilbage

**Admin-login havde ingen.** Klikkede man »Admin« ved et uheld, stod man med en
password-boks og intet andet — ingen menu, intet link. Nu er der et
**← Tilbage til <gruppen>** øverst.

Samme link er kommet på event-siden, medlemslisten og ordensreglerne. Det står i
indholdet, ikke kun i topbaren: er man scrollet ned i en deltagerliste på en telefon,
er menuen for længst væk.

### Hvor redigeres medlemslisten?

Medlemssiden siger det nu selv, når admin ser den: listen redigeres under
**Notifikationsliste → Modtagere** — det er de samme mennesker, beskederne sendes til.
Linket står direkte på siden.

Mail og telefonnumre på medlemslisten har hele tiden været klikbare (`mailto:` og `tel:`),
så et tryk åbner mail- eller telefonprogrammet.

## Version 26

**Medlemsliste medlemmerne selv kan se — og navne på gæsterne.**

### Medlemsliste

Notifikationslisten har altid haft navn, mail og mobilnummer, men kun admin kunne se den.
Nu kan admin give medlemmerne adgang: et punkt **Medlemmer** på bruger-siden med en
klub-telefonbog, hvor mail og numre kan trykkes på.

Det er **samme liste** som notifikationerne sender til — ét sted at vedligeholde. Slås til
under **Notifikationsliste → Indstillinger**, og er som udgangspunkt **slået fra**.

Den enkelte kan holdes udenfor, og det gælder kun visningen — man får stadig sine beskeder:

- admin trykker **Skjul** ved en modtager
- en bruger med konto sætter selv fluebenet under **Min profil**

Admin kan åbne siden, før listen slås til, og se præcis hvad medlemmerne vil få at se.

### Navne på gæster

Tillader et event gæster, kommer der nu et navnefelt pr. gæst: vælger man 3 pladser,
dukker »Navn på gæst 1« og »Navn på gæst 2« op. Navnene er **valgfri** — den, der tager to
med uden at vide hvem endnu, bliver ikke spærret.

Navnene vises i deltagerlisten (»+2 gæster: Bo, Carl«) og har fået sin egen kolonne i
CSV-eksporten. Skruer man ned for antallet af pladser, forsvinder den overskydende gæst
med, så der ikke står et navn, ingen har plads til.

## Version 25

**Velkomsttekst og dokumenter på gruppens forside.**

### Tekst over »Kommende events«

Under **gruppe-admin → Opsætning → Bruger-sidens udseende** er der et nyt felt til en
tekst, der står øverst på forsiden — velkomst, praktisk info, regler, nyheder. Den
understøtter Markdown (overskrifter, fed, punktlister, links), og alt andet end de
tilladte tags fjernes, så teksten ikke kan indeholde scripts. Tomt felt = ingen boks.
Kun dem, der er logget ind, ser den.

### Dokumenter

Et nyt kort, **Dokumenter på forsiden**, samme sted. Vedtægter, jagtplan, kort — de vises
nederst på forsiden under events og kan hentes af dem, der har adgang til gruppen.

Samme regler som filer på et event: højst 25 MB pr. forsendelse, kun almindelige
dokument- og billedtyper, altid leveret som download, og danske filnavne overlever
(»Vedtægter 2026.pdf« hedder stadig det, når den hentes). Styres af det samme
master-flueben **Filer**.

Reglerne for at gemme en upload ligger nu ét sted i koden og bruges af både event-filer
og dokumenter, så de ikke kan komme til at opføre sig forskelligt.

## Version 24

**Rettelse: del-knappen kunne ikke ramme en gruppe.**

Version 23 lovede, at »Del på Facebook« åbnede en dialog, hvor man valgte sin gruppe.
Det gør den ikke. Facebooks del-dialog slår op på **din egen væg** — der findes ingen
gruppe-destination, hverken i `sharer.php` eller i Metas Share Dialog. Det er ikke noget,
der er blevet lukket; det har aldrig været der.

Kortet viser nu den vej, der virker — og som er den eneste tilbage, siden Groups API
lukkede:

1. **Kopiér teksten** (navn, tidspunkt, frist, beskrivelse og link).
2. **Åbn Facebook-gruppen** og klik i skrivefeltet.
3. Sæt ind og slå op. Facebook bygger selv link-kortet ud fra Open Graph-taggene fra
   version 23 — så navn, dato og billede kommer med. Bagefter kan du slette selve linket
   fra teksten, hvis du vil.

Gruppens adresse sættes under **gruppe-admin → Opsætning → Facebook-gruppe**, så knappen
åbner den direkte. Feltet tager kun adresser på `facebook.com`: det bliver til en knap i
admin-UI'et, og et felt, der kan pege hvor som helst, er en åben dør til et falsk login.

Uden en adresse er der ingen knap — kun en henvisning til, hvor den sættes.

## Version 23

**Del et event på Facebook — og et ordentligt link-kort, når nogen deler linket.**

### Hvorfor der ikke er en Facebook-bruger at koble på

Meta lukkede **Groups API** den 22. april 2024 og fjernede både `publish_to_groups`,
`groups_access_member_info` og muligheden for, at en gruppe-admin installerer en app i
gruppen. Der findes ikke længere en tilladelse at søge om: ingen app kan slå op i en
Facebook-gruppe på dine vegne. Buffer, Hootsuite og Zoho droppede deres gruppe-funktioner
af samme grund.

Derfor er det sidste klik dit — men alt andet er gjort klar.

### Del eventet

Under **Vis liste** (og via »Del« på event-oversigten) er der nu et *Del eventet*-kort:

- **Del på Facebook** åbner Facebooks del-dialog med eventets link klar. Du vælger selv
  gruppen.
  > **RETTET i version 24 — det passer ikke.** Facebooks del-dialog kan kun slå op på
  > din egen væg; der er ingen gruppe-vælger, hverken i `sharer.php` eller i Metas
  > Share Dialog (dokumentationen beskriver kun tidslinjen). Knappen er erstattet —
  > se version 24.
- **Kopiér teksten** giver en færdig tekst — navn, tidspunkt, frist, beskrivelse og link
  — som du indsætter i opslaget. Facebooks dialog kan ikke få tekst med udefra.
  Markdown i beskrivelsen renses til ren tekst, så der ikke står `**fed**` i opslaget.
- Uden en **offentlig URL** under master → Opsætning er der intet at dele, og kortet
  siger det i stedet for at vise en knap, der ikke virker.

### Link-kortet

Et delt link viser nu titel, tidspunkt, frist og et uddrag i stedet for at være et nøgent
link. Facebooks robot kan ikke logge ind, så et event bag adgangskode viser nu en **åben
forside**: navn, hvornår, frist, beskrivelse — og knappen »Log ind for at tilmelde dig«.

**Deltagerlisten og tilmeldings-formularen er stadig lukket.** Kun det, der alligevel
står i opslaget, er synligt.

Klikker man linket og logger ind, lander man tilbage på **eventet** — ikke på forsiden.
Event-navnet slås op i gruppen først, så parameteren ikke kan bruges til at sende folk
ud af appen.

Ikonet findes nu også i 512 px og bruges som billede på link-kortet, når gruppen ikke
selv har uploadet et: Facebook viser ikke et kort med et billede under 200 px.

## Version 22

**Rettelse: »Opdater Tilmeld« kunne ikke hente koden første gang.**

En server, der blev installeret dengang koden lå i et Docker-image (rune 20 og
ældre), har ingen `app/`-mappe i datamappen. Trykkede man **Opdater Tilmeld** efter
at have hentet rune 21, sagde knappen bare:

```
[kode] app/kilde.py mangler - geninstaller serveren.
```

— og bagefter stoppede serveren med vilje, fordi der ingen kode var. Man stod altså
med en slukket server og en besked, der pegede på en anden knap.

Nu gør **begge** knapper det samme, når `app/` er tom: de henter koden fra GitHub,
præcis som en første installation. Databasen og `uploads/` røres ikke.

Startup-beskeden nævner desuden hvilke knapper der hjælper, i stedet for bare
»geninstaller serveren«.

`tests/tjek_rune.py` er ny og fanger netop denne slags fejl, før de når panelet: at
begge knapper kan hente koden fra ingenting, at de to hente-blokke er ord for ord ens,
at `DATA_DIR` er sat, at `done_regex` matcher waitress' startlinje, og at backup ikke
slæber koden og det virtuelle miljø med.

---

## Version 21

**Runen bærer ikke længere koden — den henter den fra GitHub.**

Indtil nu lå app-koden i et Docker-image på GHCR, som GitHub Actions byggede ved hvert
push. Nu kører runen et almindeligt `python:3.12-slim`-image og henter koden fra
repoets `vN`-tag, præcis som doda. **En udgivelse er et git-tag**, og **en genstart er
opdateringen.**

Det betyder i praksis:

| Før | Nu |
|---|---|
| Push til `main` → Actions bygger et image | `git tag vN && git push --tags` |
| Update/Reinstall i panelet | **Restart** — appen henter selv nyeste udgivelse |
| `IMAGE_TAG=v12` låser installationen | `KODE_VERSION=12` låser den, og feltet må gerne stå tomt |
| Koden kun synlig som et image-lag | Koden ligger i `/data/app` og kan læses i panelets Files-fane |

`Dockerfile` og GitHub Actions-workflowet er fjernet — intet brugte dem længere. De
allerede udgivne images (til og med `:v20`) bliver liggende på GHCR, så en gammel
installation kører videre; der kommer bare ikke nye.

### Hvordan opdateringen opfører sig

`kilde.py` kører før serveren starter og følger tre regler:

1. **En fejl må aldrig kunne forhindre serveren i at starte.** Kan GitHub ikke nås,
   kører den kode, der ligger. En netværksfejl må ikke kunne slukke for tilmeldingerne.
2. **Der byttes aldrig halvt.** Der pakkes ud i en frisk mappe ved siden af, den
   tjekkes, og først derefter skiftes navnene — og en afbrudt udskiftning sættes
   tilbage ved næste start.
3. **`KODE_VERSION` er en lås, ikke et ønske.** Står der et tal, hentes præcis det tag,
   også selvom der findes et nyere.

Der spørges om **tags**, ikke om en gren: `main` er arbejdsbordet, `vN` er det eneste,
der betyder »udgivet«.

### Afhængighederne

Flask, waitress, bleach, Markdown, webauthn og cryptography kan ikke ligge i repoet —
`cryptography` er kompileret. De installeres derfor i et virtuelt miljø i `/data/venv`
ved første start: ca. 10 sekunder og ca. 45 MB, én gang. Miljøet bygges om af sig selv,
hvis `requirements.txt` eller Python-versionen ændrer sig — uden det ville et skift fra
`python:3.12` til `3.13` give en importfejl, fordi hjulene er bygget til én bestemt ABI.

**Backup** tager nu kun brugerdata med (database + `uploads/`) i stedet for hele
datamappen. `app/` hentes fra GitHub og `venv/` bygges på ti sekunder; de hører ikke
hjemme i en sikkerhedskopi.

### Rettelse

**Aktivitetsloggen manglede én udsendelse.** Deltagerlisten (CSV), der sendes til
admin-mailen to timer efter fristen, blev sendt uden at skrive en linje i loggen — den
eneste besked i appen, man ikke kunne se var afsted. Nu står den der som alle andre,
med årsag hvis den ikke kunne leveres.

---

## Version 20

**Madbestilling: en besked med antal kuverter, når tilmeldingen lukker.**

Den, der skal bestille mad til et event, får nu selv besked — med tallet. Beskeden
sendes én gang, når tilmeldingsfristen er nået, på mail og/eller SMS.

### Hvem får den

Gruppen sætter en fast madbestiller under **Opsætning → Kontakt → Madbestiller**
(mail, mobilnummer eller begge). Hvert event kan overskrive den under *Redigér event →
Notifikationer → Madbestilling* — de to felter vælges hver for sig, så gruppens faste
mailmodtager kan blive stående, selvom ét event skal ringe til en anden telefon.

Fluebenet **»Besked til madbestilleren når fristen er nået«** slår den til pr. event.
En kopi af et event arver både fluebenet og modtageren.

På deltagerlisten står der nu, hvem beskeden går til og hvor mange kuverter — med en
knap **»Send nu«**, hvis man skal melde tallet ind før fristen eller sende det igen.

### »Spiser ikke med«

Der er kommet en ny slags tilmeldings-punkt ved siden af »deltager ikke«: et
**»spiser ikke med«-felt**. Deltageren kommer til eventet, men trækkes fra
madbestillingen. Det oprettes under **Opsætning → Tilmeldings-punkter** og kan som
alle andre punkter skjules på de events, hvor det ikke giver mening.

Krydset gælder **hele tilmeldingen** — også dens gæster. Krydser man af for sig selv
og to gæster, er det tre kuverter færre. De to fra-meldinger udelukker hinanden: et
afbud spiser ikke med alligevel.

Deltagerlisten viser tallet: *Spiser med: 4 (2 har meldt fra til spisning)*.

### Teksten kan rettes

Skabelonen **»Madbestilling«** ligger sammen med de øvrige under *Opsætning →
Mail-skabeloner* (hvis master har givet gruppen lov). Ud over de sædvanlige
pladsholdere har den fem tal:

| Pladsholder | Betyder |
|---|---|
| `{meals}` | Hvor mange der skal have mad |
| `{count}` | Deltagere + gæster i alt |
| `{no_meals}` | Har meldt fra til spisning |
| `{signups}` | Antal tilmeldinger (personer) |
| `{waitlist}` | Antal på venteliste |

Standardteksten er:

> Tilmeldingen til {event} d. {date}{start} er lukket.
> Der skal bestilles mad til {meals}.
> I alt {count} deltagere inkl. gæster, heraf {no_meals} uden mad.

### Detaljer

- **Afbud og venteliste tæller ikke med.** Kun dem, der reelt deltager, bliver til
  kuverter — samme regel som deltagertallet på listen bruger.
- **Beskeden sendes én gang.** Kunne den ikke leveres, fejler den én gang og står i
  aktivitetsloggen; den prøver ikke igen hvert tiende minut og sender maden af sted
  tre dage senere. »Send nu« er vejen, hvis den skal ud alligevel.
- **Et skjult felt tømmer ikke længere sit indhold.** Er mail ikke sat op globalt,
  vises madbestillerens mailfelt ikke i event-formularen — og før ville et gem så
  have slettet den gemte adresse. Nu bevares den.

---

## Version 19

**SMS som fjerde notifikationskanal — via Gigahost.**

Tilmeld kan nu sende SMS ved siden af mail, WhatsApp og push. Kanalen følger nøjagtig
samme regler som de tre andre: master sætter den op globalt og slår den til pr. gruppe,
og event-fluebenene bestemmer, hvad der bliver sendt.

### Opsætning

Under **master → Opsætning → SMS (Gigahost)** sættes tre ting: brugernavn (det samme som
til Gigahost Kontrolcenter), en **API-adgangskode** (oprettes i Kontrolcenteret — det er
ikke den, du logger ind med) og et **afsendernummer**, der er verificeret på kontoen.
Alle tre skal være udfyldt, før kanalen regnes for sat op.

Knappen **»Tjek saldo og afsendernumre«** slår kontoen op hos Gigahost og viser, hvor
mange klip der er tilbage, hvilke afsendernumre der er godkendt, og om det nummer, du
har skrevet ind, står på listen. Det er dér, fejlen næsten altid sidder, når alt andet
ser rigtigt ud.

### Hvor SMS bliver brugt

- **Til gruppe-admin** — ny tilmelding, ændring, »fristen er nået«. SMS har sit eget
  modtagerfelt ved siden af WhatsApps, fordi en WhatsApp-modtager også kan være en
  gruppechat, og et gruppe-id kan man ikke sende en SMS til.
- **Til deltagere** — kvittering, påmindelse før frist, påmindelse før eventet.
- **Til notifikationslisten** — både den automatiske varsling om nye events og
  »Send nu«.

Deltagerens **mobilnummer er ét felt**, som både WhatsApp og SMS bruger. Feltet hedder
nu »Mobilnummer« i stedet for »WhatsApp-nummer« — også i deltagerlisten og CSV-filen —
og parentesen fortæller, hvad nummeret bliver brugt til i netop den gruppe. Er begge
kanaler slået til, får modtageren begge dele.

### Detaljer

- **Gatewayen slås op i DNS.** Gigahost annoncerer sine aktive gateways som SRV-poster;
  svarer den ene ikke, bruges den næste. Slår opslaget fejl, falder appen tilbage på de
  kendte værtsnavne — opslaget er en forbedring, ikke en forudsætning.
- **Beskeder holdes inden for 3 SMS-dele.** Et enkelt tegn uden for latin-1 (fx en
  tankestreg) ville ellers halvere pladsen fra 459 til 201 tegn og koste flere klip, så
  de typografiske tegn, skabelonerne bruger, oversættes automatisk (— bliver til -,
  … til ...). Er teksten stadig for lang, forkortes den.
- **Fejl i aktivitetsloggen får nu den rigtige kategori.** Før blev alt, der ikke kunne
  leveres fra notifikationslisten, logget som »mail«, uanset kanal. Nu kan man filtrere
  på **SMS** og se præcis, hvad der ikke kom af sted.
- SMS er skrevet direkte oven på stdlib i `gigasms.py` — ingen ny afhængighed, præcis
  som Web Push. `python gigasms.py` slår gatewayene op og kan med `GIGAHOST_USER` +
  `GIGAHOST_PASSWORD` i miljøet hente saldo og afsendernumre.

### Migration af gamle databaser

Tilmeld havde SMS én gang før, og databaser fra dengang har stadig kolonnerne
`sms_enabled` og `admin_phone`. De blev i sin tid flyttet over i WhatsApp-felterne — men
flytningen kørte ved hver opstart, og med en ny kanal af samme navn ville den have slået
WhatsApp til igen på enhver gruppe, der brugte SMS. Flytningen sker nu **én gang**, og de
gamle kolonner fjernes bagefter. Det gamle admin-nummer følger med over i den nye kanals
modtagerfelt, så det ikke går tabt; selve kanalen starter slukket.

---

## Version 18

**Filer på et event — og en rettelse af notifikationslisten.**

### Filer

Gruppe-admin kan lægge filer på et event: program, kort, menu, vedtægter. De vises
under beskrivelsen på eventets side og kan hentes af dem, der har adgang til gruppen.

- Vælg flere filer på én gang. Højst **25 MB pr. forsendelse**.
- Tilladte typer er en hvidliste (pdf, Office/OpenDocument, billeder, txt, csv, zip,
  ics). Filer leveres **altid som download**, aldrig vist i siden, så en vedhæftet fil
  ikke kan køre noget på appens eget domæne.
- Danske filnavne overlever: »Køreplan æøå.pdf« hedder stadig det, når den hentes.
  På disken får filen et tilfældigt navn, så to filer med samme navn ikke kan
  overskrive hinanden.
- Slettes eventet, ryddes filerne fra disken — ikke kun rækkerne i databasen.
- **Master slår det til pr. gruppe** (som mail, WhatsApp og push). Filerne fylder på
  serverens disk, så det er master, der bestemmer hvem der må.

En kopi af et event får **ikke** filerne med — de skal lægges på igen. Det er med
vilje: to events, der pegede på den samme fil, ville miste den begge to, når det ene
blev slettet.

### Rettelse: notifikationslisten bad om modtagere, den ikke kunne tage imod.

Er hverken SMTP eller WhatsApp-gatewayen sat op — men push er slået til — kunne
notifikationslisten åbnes, og »Tilføj modtager« viste kun et navnefelt. Der var
ingen steder at skrive mailadressen eller nummeret, og formularen kunne derfor
aldrig gemmes: en modtager på listen ER jo en adresse eller et nummer.

Afsnittet forklarer nu i stedet, hvad der mangler (SMTP sættes op under
master → Opsætning), og at push virker uafhængigt af det: de enheder, der har
slået notifikationer til på bruger-siden, melder sig selv til og skal ikke tastes ind.

Bekræftelsen under »Send nu« tæller også enhederne med — med kun push var
»0 modtagere« sandt og alligevel misvisende, for beskeden nåede jo frem.

## Version 17

**Push-notifikationer på telefonen — og én knap til lyst/mørkt tema.**

### Tilmeld kan lægges på hjemmeskærmen

Hver gruppe har nu sit eget web-manifest, så `/<gruppe>` kan installeres som en app:
den hedder gruppens navn, har sit eget ikon og åbner direkte på gruppens side.

### Notifikationer som en tredje kanal

Push følger **præcis samme regler som mail og WhatsApp**: master slår den til pr.
gruppe, og event-fluebenene bestemmer, hvad der sendes. Er der intet flueben, kommer
der ingen notifikation.

Tre steder kan man slå dem til, og hvert sted gælder **den enhed man står ved**:

- **Min profil** (deltagere med brugerkonto) — kvittering, påmindelse før fristen og
  påmindelse dagen før eventet.
- **Gruppe-opsætning** (gruppe-admin) — ny tilmelding, ændring og »fristen er nået«.
- **Bruger-siden** (alle, også grupper med delt adgangskode) — varsling om nye events.
  Det er vejen for grupper uden individuelle konti: et abonnement hører til en enhed,
  ikke til en adresse, så der skal ingen identitet til.

Der er en **»Send en prøve«**-knap, så man kan se at det virker uden at vente på et event.

**På iPhone og iPad skal siden først lægges på hjemmeskærmen.** Apple tillader ikke
notifikationer fra en almindelig fane. Kan der ikke sendes til enheden, skriver appen
hvorfor — https, browseren, eller netop hjemmeskærmen — i stedet for at have en knap,
der bare ikke gør noget.

Teknisk: VAPID + RFC 8291 er skrevet direkte oven på `cryptography`, som allerede var
med til passkeys — **ingen ny afhængighed**. Nyttelasten krypteres med enhedens egne
nøgler, så Apple og Google videresender en byteklump, de ikke kan læse: de får aldrig
at vide, hvad eventet hedder. Krypteringen er efterprøvet mod RFC 8291's officielle
testvektor (`python push.py`). Abonnementer, som push-tjenesten melder døde (404/410),
slettes automatisk.

**Push kræver en offentlig URL** under master → Opsætning. Notifikationen indeholder
absolutte adresser, og uden en offentlig URL er der ikke noget at gøre dem absolutte med.

### Tema-skifteren er blevet til ét ikon

De tre knapper (Auto / Lys / Mørk) er erstattet af **én rund ikon-knap**, der vipper
mellem lyst og mørkt. Den viser det tema, et klik giver dig.

Siden **starter altid på systemets tema** og følger det live, indtil du selv trykker.
Dit valg gælder fanen — også gennem de mange sideskift appen laver — og en ny fane
starter forfra på systemets tema.

## Version 16

**Notifikationsliste: giv besked om nye events — også til folk der ikke er tilmeldt.**

Gruppe-admin har fået siden **Notifikationsliste** (`/<gruppe>/admin/notifikationer`).
Her står de modtagere, der skal høre om et nyt event, før de tilmelder sig noget.

- **Mail og/eller mobilnummer**, afhængigt af hvad master har slået til for gruppen.
  Er kun mail aktiveret, vises nummer-feltet slet ikke — og omvendt.
- **Kører gruppen med individuelle bruger-konti, henter listen brugerne selv.** Deres
  mail og mobilnummer kommer direkte fra profilen, så de ikke skal vedligeholdes to
  steder; retter en bruger sin mail, følger listen med. Admin kan stadig tilføje folk
  i hånden — fx dem der endnu ikke har en konto. Dubletter (samme mail eller samme
  nummer) sendes der kun til én gang.
- **Automatisk varsling:** vælg hvor mange dage før et event, listen skal have besked
  (standard 14). Oprettes et event tættere på end det, sendes varslingen med det samme.
  Hvert event har et flueben under *Notifikationer*, så enkelte events kan holdes ude.
- **Send nu:** en knap der sender varslingen om et valgt event, og et felt til en helt
  fri besked til hele listen. `{name}` bliver modtagerens navn, `{group}` gruppens.
- Modtagere kan sættes **på pause** i stedet for at blive slettet.
- Teksten er en **mail-skabelon** (»Nyt event«) som alle de andre, og kan rettes under
  Opsætning, hvis master har givet gruppen lov.

Listen sender kun gennem de kanaler, master har aktiveret for gruppen. Er hverken mail
eller WhatsApp sat op, er siden helt skjult — den ville alligevel ikke kunne sende noget.

Databasen udvides automatisk ved opstart (ny tabel + nye kolonner); intet går tabt.

## Version 13, 14 og 15

Rune-definitionen, ikke appen: cache-bust på `forms.js`, log-watchers og »Wipe« i
panelet, og en advarsel om at `IMAGE_TAG` aldrig må stå tomt.

## Version 12

**Hver udgivelse får sit eget image-tag — og du kan rulle tilbage.**

- Hver udgivelse bygges nu også som `ghcr.io/andreasdinesen/tilmeld:v<version>` ved
  siden af `:latest`. Versionsnummeret læses direkte af rune-filen i build'et, så de
  to aldrig kan komme ud af trit.
- Runen har fået feltet **»App-version«** (`IMAGE_TAG`) i panelets indstillinger.
  `latest` følger nyeste udgivelse — og skriver du fx `v12`, låses installationen til
  netop den version. **Det er vejen tilbage, hvis en udgivelse driller:** sæt feltet,
  tryk »Opdater Tilmeld«, og du kører den gamle version igen uden at vente på en
  rettelse. Databasen i `/data` er upåvirket.
- Feltet accepterer kun `latest` eller `v<tal>` — modsat de øvrige runer, hvor hele
  runtime-imaget er et felt. Her er imaget vores eget, og der er ingen grund til at
  kunne pege appen på et vilkårligt image.

**Versions-taggene begynder ved v12.** Tidligere udgivelser findes kun som `:latest`,
så man kan ikke rulle længere tilbage end hertil.

Selve appen er uændret; det er rune-definitionen og build'et, der har ændret sig.

## Version 11

**Egen »Opdater Tilmeld«-knap i panelet.**

- Runen har fået en `update:`-blok, så serveren får sin egen **»Opdater Tilmeld«**-knap
  ved siden af Start/Stop. Før skulle man geninstallere serveren under
  *Settings → Update/Reinstall* for at få en ny version — det virkede, men lyder
  farligere end det er, og knappen siger nu direkte, hvad den gør.
- Panelet stopper appen, henter imaget forfra, kører rune-scriptet og starter appen
  igen på det nye image. **Databasen og uploads i `/data` røres ikke** — skemaændringer
  kører automatisk, når appen starter.
- Opdaterings-loggen viser hvilken version, der blev hentet.

Selve appen er uændret i denne version; det er kun rune-definitionen, der har fået
den nye knap. Første gang skal den stadig hentes med **Runes → Reload** og derefter
én *Update/Reinstall* — derefter er knappen der.

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
