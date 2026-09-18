"""Kontrol af runen. Kør: python3 tests/tjek_rune.py

Runen skrives i hånden, så der er intet build-script til at fange en tastefejl.
Denne kontrol fanger de fejl, der ellers først viser sig i panelet — hvor en
install-log ikke streamer, og man derfor fejlsøger i blinde.
"""
import pathlib
import re
import sys

import yaml

ROD = pathlib.Path(__file__).resolve().parent.parent
RUNE = ROD / "runes" / "tilmeld.yaml"


def bootstrap_blok(script):
    """Den indlejrede Python, der henter koden fra GitHub — eller None."""
    m = re.search(r"( *python3 - <<'TILMELD_HENT'\n.*?\n *TILMELD_HENT\n)", script, re.S)
    return m.group(1) if m else None


def main():
    raa = RUNE.read_text(encoding="utf-8")
    d = yaml.safe_load(raa)["gameskill"]
    fejl = []

    # Versionen skal være et tal — taggen vN udledes af den.
    if not isinstance(d.get("version"), int):
        fejl.append(f"version skal være et tal, ikke {d.get('version')!r}")

    install = d["install"]["script"]
    update = d["update"]["script"]
    startup = d["startup"]["command"]

    # 1. Begge knapper skal kunne hente koden fra ingenting. Det var netop det,
    #    der manglede i rune 21: en server fra image-tiden havde ingen app/, og
    #    »Opdater Tilmeld« kunne kun sige »geninstaller«.
    bi, bu = bootstrap_blok(install), bootstrap_blok(update)
    if not bi:
        fejl.append("install: mangler bootstrap-blokken")
    if not bu:
        fejl.append("update: mangler bootstrap-blokken — knappen kan ikke hente kode")
    if bi and bu and bi != bu:
        fejl.append("bootstrap-blokken i install: og update: er IKKE ens — de driver fra hinanden")

    # 2. DATA_DIR skal sættes, ellers lander databasen i app/, som opdateringen
    #    river ned. Dyrekøbt: se claude-noter/faldgruber.md punkt 5b.
    if "export DATA_DIR=/data" not in startup:
        fejl.append("startup: mangler 'export DATA_DIR=/data' — databasen ville havne i app/")

    # 3. done_regex skal matche det, waitress rent faktisk skriver.
    if d["startup"]["done_regex"] not in "INFO:waitress:Serving on http://0.0.0.0:8080":
        fejl.append("startup.done_regex matcher ikke waitress' startlinje")

    # 4. Backup må ikke slæbe venv og kode med.
    inc = d["backup"]["include"]
    if "." in inc or any(x in inc for x in ("venv", "app")):
        fejl.append(f"backup.include tager kode/venv med: {inc}")

    # 5. Variabel-mønstrene skal acceptere deres egen standardværdi.
    for v in d["variables"]:
        if v.get("pattern") and not re.fullmatch(v["pattern"], str(v.get("default", ""))):
            if v["key"] != "KODE_VERSION" or v.get("default"):
                fejl.append(f"{v['key']}: default {v.get('default')!r} matcher ikke pattern")

    for f in fejl:
        print(f"[fejl] {f}")
    if fejl:
        return 1
    print(f"Rune {d['version']}: alle kontroller ok "
          f"(bootstrap i begge knapper, DATA_DIR sat, backup uden kode/venv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
