"""Versioner og selv-opdatering til master-admin.

- Viser versioner af app, Python, Flask/Werkzeug, SQLite og DB-skema.
- Tjekker en GitHub-adresse for en nyere app-version (filen VERSION på repoet).
- Kan køre 'git pull' + pip-opdatering direkte (kræver at appen ligger i et git-repo).
"""
import os
import platform
import sqlite3
import subprocess
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")


def app_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "ukendt"


def _pkg_version(name: str) -> str:
    try:
        import importlib.metadata as md
        return md.version(name)
    except Exception:
        return "ukendt"


def component_versions() -> list:
    """Liste af (navn, version) for installerede systemer."""
    return [
        ("Tilmeld (app)", app_version()),
        ("Python", platform.python_version()),
        ("Flask", _pkg_version("flask")),
        ("Werkzeug", _pkg_version("werkzeug")),
        ("SQLite-motor", sqlite3.sqlite_version),
        ("Database (sqlite3-modul)", sqlite3.version),
    ]


def is_git_repo() -> bool:
    return os.path.isdir(os.path.join(BASE_DIR, ".git"))


def check_latest(repo: str, branch: str = "main") -> dict:
    """Hent VERSION-filen fra GitHub og sammenlign med lokal version."""
    repo = (repo or "").strip().strip("/")
    if not repo:
        return {"ok": False, "error": "Ingen GitHub-adresse angivet."}
    url = f"https://raw.githubusercontent.com/{repo}/{branch or 'main'}/VERSION"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            latest = r.read().decode().strip()
    except Exception as e:
        return {"ok": False, "error": f"Kunne ikke hente version: {e}", "url": url}
    current = app_version()
    return {
        "ok": True, "current": current, "latest": latest,
        "update_available": _newer(latest, current), "url": url,
    }


def _newer(latest: str, current: str) -> bool:
    def parse(v):
        return [int(x) for x in v.split(".") if x.isdigit()]
    try:
        return parse(latest) > parse(current)
    except Exception:
        return latest != current


def _run(cmd: list) -> str:
    try:
        out = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=300)
        return (out.stdout + out.stderr).strip() or "(ingen output)"
    except Exception as e:
        return f"FEJL: {e}"


def update_app(branch: str = "main") -> str:
    """git pull + opdater afhængigheder. Genstart kræves efter."""
    if not is_git_repo():
        return ("Appen er ikke et git-repo, så automatisk opdatering er ikke mulig. "
                "Hent den nyeste version manuelt fra GitHub.")
    log = "$ git pull\n" + _run(["git", "pull", "origin", branch or "main"])
    log += "\n\n$ pip install -r requirements.txt\n" + update_dependencies()
    log += "\n\nGENSTART appen for at den nye version træder i kraft."
    return log


def update_dependencies() -> str:
    req = os.path.join(BASE_DIR, "requirements.txt")
    return _run([sys.executable, "-m", "pip", "install", "--upgrade", "-r", req])
