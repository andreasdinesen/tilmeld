"""Tilmeld henter sin egen kode fra GitHub.

Indtil rune 20 lå app-koden i et Docker-image på GHCR: en ny udgave krævede et
image-byg i GitHub Actions, og runen pegede bare på et tag. Nu bærer runen ikke
koden — den henter den fra repoets `vN`-tag — og `startup`-kommandoen kører
dette modul FØR serveren starter. **En genstart er altså opdateringen.** Runen
skal kun udgives på ny, når selve rune-definitionen ændrer sig.

## De tre regler

1. **En fejl må aldrig kunne forhindre serveren i at starte.** Alt herinde ender
   med exit 0. Kan GitHub ikke nås, kører den kode, der ligger. Det er den
   vigtigste egenskab: en netværksfejl må ikke kunne slukke for tilmeldingerne.
2. **Der byttes ALDRIG halvt.** Der pakkes ud i en frisk mappe ved siden af, den
   tjekkes, og først derefter skiftes navnene. Mellem de to omdøbninger ligger
   `app/` under `.tilmeld-gammel` — og startup-kommandoen sætter den tilbage,
   hvis vi dør præcis dér.
3. **KODE_VERSION er en lås, ikke et ønske.** Står der et tal, hentes præcis det
   tag — også selvom der findes et nyere. Det er vejen tilbage, når en udgivelse
   er dårlig: sæt tallet i panelet, genstart.

## Hvorfor tags og ikke en gren

`refs/heads/main` ville være ét kald mindre, men main er arbejdsbordet. Taggen
`vN` er den eneste ref, der betyder »udgivet«. Derfor spørges GitHub om
TAG-listen, og det højeste `v<tal>` vinder — ikke det, API'et tilfældigvis
nævner først.

## Versionsnummeret

Der er stadig ÉT nummer: `version:` i `runes/tilmeld.yaml`. Hele repoet pakkes
ud, så rune-filen følger med koden, og den udpakkede fils `version:` ER den
kørende udgave. Git-taggen `vN` skal derfor matche rune-versionen ved udgivelse.

Kør `python3 kilde.py` lokalt for at se, hvad den ville hente (den rører intet,
hvis `app/` ikke findes i den mappe, den køres fra).
"""
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.request

EJER = "andreasdinesen"
REPO = "tilmeld"
APP = "app"                       # den udpakkede kode ligger her (i /data)
NY = ".tilmeld-ny"
GAMMEL = ".tilmeld-gammel"
MAERKE = "app.py"                 # filen der beviser, at arkivet er tilmeld

# Kan pegs et andet sted hen under prøverne, så selve hentningen bliver afprøvet
# og ikke bare læst.
API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
CODELOAD = os.environ.get("GITHUB_CODELOAD", "https://codeload.github.com").rstrip("/")
HOVEDER = {"User-Agent": "tilmeld-installer", "Accept": "*/*"}
MAKS = 64 * 1024 * 1024           # arkivet er ~1 MB; loftet er mod et løbsk svar


def log(besked):
    print(f"[kode] {besked}", flush=True)


def _hent(url, timeout=60):
    req = urllib.request.Request(url, headers=HOVEDER)
    with urllib.request.urlopen(req, timeout=timeout) as svar:
        raa = svar.read(MAKS + 1)
    if len(raa) > MAKS:
        raise ValueError("svaret fra GitHub var urimeligt stort")
    return raa


def nyeste_tag():
    """Højeste `v<tal>` blandt repoets tags, eller None."""
    numre = []
    side = f"{API}/repos/{EJER}/{REPO}/tags?per_page=100"
    for t in json.loads(_hent(side).decode("utf-8")):
        m = re.fullmatch(r"v(\d+)", str(t.get("name", "")))
        if m:
            numre.append(int(m.group(1)))
    return max(numre) if numre else None


def udrullet_version():
    """Versionen i den kode, der ligger nu — læst af den udpakkede rune-fil."""
    sti = os.path.join(APP, "runes", "tilmeld.yaml")
    try:
        with open(sti, encoding="utf-8") as f:
            for linje in f:
                m = re.match(r"\s*version:\s*['\"]?(\d+)", linje)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return None


def _sikre_poster(tf):
    """Kun almindelige filer og mapper, og kun under NY.

    Reservedel til Python < 3.12, hvor `extractall(filter=...)` ikke findes.
    Samme tre spærrer som »data«-filteret: ingen absolutte stier, ingen »..«
    ud af mappen, og ingen symlinks/enheder — et arkiv må ikke kunne pege på
    /etc/passwd og få os til at skrive dér.
    """
    rod = os.path.abspath(NY)
    for post in tf.getmembers():
        if not (post.isfile() or post.isdir()):
            continue
        maal = os.path.abspath(os.path.join(rod, post.name))
        if maal == rod or maal.startswith(rod + os.sep):
            yield post


def hent_og_pak_ud(nummer):
    """Hent taggen og læg den i NY. Returnér stien til den udpakkede kode."""
    url = f"{CODELOAD}/{EJER}/{REPO}/tar.gz/refs/tags/v{nummer}"
    log(f"henter v{nummer} fra GitHub ...")
    raa = _hent(url, timeout=120)
    shutil.rmtree(NY, ignore_errors=True)
    os.makedirs(NY, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raa), mode="r:gz") as tf:
        # filter="data" afviser absolutte stier, »..« og enheds-/link-poster.
        # Uden den ville et arkiv kunne skrive uden for NY. Argumentet kom i
        # Python 3.12; på ældre sorteres posterne fra i hånden, så runen også
        # tåler et ældre python:3.x-image.
        try:
            tf.extractall(NY, filter="data")
        except TypeError:
            tf.extractall(NY, members=_sikre_poster(tf))

    # Mappenavnet i et GitHub-arkiv er <repo>-<ref uden v>, og arkivet begynder
    # med en pax_global_header-post. Ingen af delene gættes: find den mappe, der
    # FINDES og indeholder mærke-filen.
    for rod, _, filer in os.walk(NY):
        if MAERKE in filer and os.path.isdir(os.path.join(rod, "templates")):
            return rod
    raise ValueError(f"arkivet indeholder hverken {MAERKE} eller templates/")


def skift_ind(ny_sti):
    """Byt `app/` ud med den nye kode. To omdøbninger, aldrig en halv kopi."""
    shutil.rmtree(GAMMEL, ignore_errors=True)
    if os.path.isdir(APP):
        os.rename(APP, GAMMEL)
    try:
        os.rename(ny_sti, APP)
    except OSError:
        # Kunne ikke skiftes ind — sæt den gamle tilbage med det samme, i stedet
        # for at efterlade en server helt uden kode.
        if os.path.isdir(GAMMEL) and not os.path.isdir(APP):
            os.rename(GAMMEL, APP)
        raise
    shutil.rmtree(GAMMEL, ignore_errors=True)
    shutil.rmtree(NY, ignore_errors=True)


def oensket_version():
    """KODE_VERSION som tal, eller None for »nyeste«."""
    raa = (os.environ.get("KODE_VERSION") or "").strip()
    # Panelet sender skabelonen uændret, hvis variablen aldrig blev sat.
    if not raa or "KODE_VERSION" in raa or raa.lower() in ("seneste", "latest"):
        return None
    return int(raa) if raa.isdigit() else None


def main():
    har = udrullet_version()
    laas = oensket_version()
    try:
        vil = laas if laas is not None else nyeste_tag()
    except Exception as e:
        log(f"kunne ikke spørge GitHub om udgivelser ({e}) — kører videre på det, der ligger")
        return 0
    if vil is None:
        log("fandt ingen v<tal>-tag på GitHub — kører videre på det, der ligger")
        return 0
    if har == vil:
        log(f"v{har} er allerede udrullet" + (" (låst)" if laas is not None else ""))
        return 0
    if har is not None and laas is None and vil < har:
        log(f"nyeste tag (v{vil}) er ældre end det udrullede (v{har}) — rører intet."
            " Sæt KODE_VERSION for bevidst at rulle tilbage")
        return 0
    try:
        skift_ind(hent_og_pak_ud(vil))
    except Exception as e:
        shutil.rmtree(NY, ignore_errors=True)
        log(f"opdateringen til v{vil} mislykkedes ({e}) — kører videre på"
            f" {'v%d' % har if har else 'den kode, der ligger'}")
        return 0
    log(f"v{vil} er udrullet" + (f" (kom fra v{har})" if har else ""))
    return 0


if __name__ == "__main__":
    # Regel 1: aldrig en exit-kode, der kan forhindre serveren i at starte.
    try:
        sys.exit(main())
    except Exception as e:      # også tastefejl herinde skal ende med 0
        log(f"uventet fejl ({e}) — kører videre på det, der ligger")
        sys.exit(0)
