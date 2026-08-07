"""Versioner til master → System.

Appens version ER runens version — det tal, Yggdrasil-panelet viser (`version:` i
`runes/tilmeld.yaml`). Ét tal ét sted: så kan man se i appen, hvad man har kørende,
og sammenligne direkte med panelets rune-liste.

Selve opdateringen sker i panelet (Runes → Reload, derefter serverens
Update/Reinstall), ikke herfra — derfor er der ingen 'git pull' i denne fil.
"""
import os
import platform
import re
import sqlite3
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNE_FILE = os.path.join(BASE_DIR, "runes", "tilmeld.yaml")


def rune_version() -> str:
    """Runens `version:` læst direkte fra YAML'en.

    Læses med en regex frem for en YAML-parser, så appen ikke får PyYAML som
    afhængighed for ét enkelt tal.
    """
    try:
        with open(RUNE_FILE, encoding="utf-8") as f:
            m = re.search(r"^\s*version:\s*(\S+)", f.read(), re.MULTILINE)
        return m.group(1).strip('"\'') if m else "ukendt"
    except OSError:
        return "ukendt"


def changelog_url(repo: str, branch: str = "main", version: str = "") -> str:
    """Link til versionsloggen på GitHub, med anker til den aktuelle version.

    GitHub laver ankeret ud fra overskriften, så "## Version 9" bliver "#version-9".
    Uden et repo i indstillingerne er der intet at linke til.
    """
    repo = (repo or "").strip().strip("/")
    if not repo:
        return ""
    url = f"https://github.com/{repo}/blob/{branch or 'main'}/CHANGELOG.md"
    if version and version != "ukendt":
        url += f"#version-{version}"
    return url


def _pkg_version(name: str) -> str:
    try:
        import importlib.metadata as md
        return md.version(name)
    except Exception:
        return "ukendt"


def component_versions() -> list:
    """Liste af (navn, version) for de systemer appen kører på.

    Appens egen version står ikke her — den vises for sig som runens versionsnummer.
    """
    return [
        ("Python", platform.python_version()),
        ("Flask", _pkg_version("flask")),
        ("Werkzeug", _pkg_version("werkzeug")),
        ("SQLite-motor", sqlite3.sqlite_version),
        ("Database (sqlite3-modul)", sqlite3.version),
    ]


def _run(cmd: list) -> str:
    try:
        out = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=300)
        return (out.stdout + out.stderr).strip() or "(ingen output)"
    except Exception as e:
        return f"FEJL: {e}"


def update_dependencies() -> str:
    req = os.path.join(BASE_DIR, "requirements.txt")
    return _run([sys.executable, "-m", "pip", "install", "--upgrade", "-r", req])
