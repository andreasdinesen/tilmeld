"""Prøv opdateringsmekanismen af — mod en ATTRAP-GitHub, ikke den rigtige.

Kør: python3 tests/tjek_opdatering.py

`kilde.py` er det eneste stykke, hvor en fejl betyder, at serveren enten holder
op med at opdatere eller slet ikke starter. Den læses derfor ikke bare igennem:
her rejses en lille HTTP-server, der svarer som GitHubs tag-liste og codeload,
og hele forløbet køres igennem i en midlertidig mappe.

Det vigtigste, prøven vogter over, er SKIFTET fra rune 53 til 54: dér flyttede
kodens versionsnummer fra `runes/tilmeld.yaml` ud i sin egen `VERSION`-fil. En
udrulning fra før skiftet har ingen VERSION-fil, og hvis opslaget ikke falder
tilbage til rune-filen, henter serveren det samme tag i en uendelighed.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lav_arkiv(version: int, med_version_fil: bool, yaml_version: int) -> bytes:
    """Et tarball, der ligner GitHubs: én topmappe med hele repoet i."""
    buf = io.BytesIO()
    top = f"tilmeld-{version}"
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        def fil(navn, indhold):
            data = indhold.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{navn}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        fil("app.py", f"# tilmeld v{version}\n")
        fil("runes/tilmeld.yaml", f"gameskill:\n  version: {yaml_version}\n")
        fil("templates/base.html", "<html></html>\n")
        if med_version_fil:
            fil("VERSION", f"{version}\n")
    return buf.getvalue()


def start_attrap(tags, arkiver):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if "/tar.gz/" in self.path:
                nr = int(self.path.rstrip("/").split("/")[-1].lstrip("v"))
                krop, typ = arkiver[nr], "application/gzip"
            elif self.path.startswith("/repos/"):
                krop = json.dumps([{"name": t} for t in tags]).encode()
                typ = "application/json"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(krop)))
            self.end_headers()
            self.wfile.write(krop)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def koer(mappe, api):
    """Kør kilde.py i `mappe` mod attrappen. Returnér (udskrift, exit-kode)."""
    miljoe = dict(os.environ, GITHUB_API=api, GITHUB_CODELOAD=api)
    p = subprocess.run([sys.executable, os.path.join(ROD, "kilde.py")],
                       cwd=mappe, capture_output=True, text=True, env=miljoe)
    return (p.stdout + p.stderr).strip(), p.returncode


def udrullet(mappe):
    sti = os.path.join(mappe, "app", "VERSION")
    return open(sti).read().strip() if os.path.exists(sti) else "(ingen VERSION-fil)"


def main():
    arkiver = {54: lav_arkiv(54, True, 53), 55: lav_arkiv(55, True, 53)}
    srv = start_attrap(["v54", "v55"], arkiver)
    api = f"http://127.0.0.1:{srv.server_port}"
    fejl = []

    # 1) En udrulning fra FØR skiftet: ingen VERSION-fil, kun rune-filens 53.
    base = tempfile.mkdtemp()
    app = os.path.join(base, "app", "runes")
    os.makedirs(app)
    open(os.path.join(base, "app", "app.py"), "w").write("# gammel\n")
    open(os.path.join(app, "tilmeld.yaml"), "w").write("gameskill:\n  version: 53\n")

    ud, kode = koer(base, api)
    print("1) fra v53 (uden VERSION-fil):", ud)
    if "v55 er udrullet" not in ud or udrullet(base) != "55":
        fejl.append("skiftet fra en udrulning uden VERSION-fil hentede ikke nyeste tag")

    # 2) Kørt igen: nu ER den nyeste, og der må IKKE hentes noget.
    ud, _ = koer(base, api)
    print("2) kørt igen:                 ", ud)
    if "allerede udrullet" not in ud:
        fejl.append("hentede igen, selvom v55 allerede lå der — VERSION læses ikke")

    # 3) Rune-definitionens tal (53) må ikke forveksles med kodens (55).
    yaml_sti = os.path.join(base, "app", "runes", "tilmeld.yaml")
    if "version: 53" not in open(yaml_sti).read():
        fejl.append("rune-filens version blev ændret af opdateringen")
    print("3) rune-definition i koden:   v53, kode: v" + udrullet(base))

    # 4) KODE_VERSION låser stadig til et bestemt tag.
    os.environ["KODE_VERSION"] = "54"
    ud, _ = koer(base, api)
    os.environ.pop("KODE_VERSION")
    print("4) låst til v54:              ", ud)
    if udrullet(base) != "54":
        fejl.append("KODE_VERSION-låsen rullede ikke tilbage")

    # 5) GitHub nede: der må ikke ske noget, og exit skal være 0.
    ud, kode = koer(base, "http://127.0.0.1:1")
    print("5) GitHub nede:               ", ud, f"(exit {kode})")
    if kode != 0 or udrullet(base) != "54":
        fejl.append("en netværksfejl rørte ved koden eller gav exit != 0")

    srv.shutdown()
    shutil.rmtree(base, ignore_errors=True)
    if fejl:
        print("\nFEJL:")
        for f in fejl:
            print("  -", f)
        return 1
    print("\nOpdateringen virker: skiftet fra rune-filens tal til VERSION er sikkert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
