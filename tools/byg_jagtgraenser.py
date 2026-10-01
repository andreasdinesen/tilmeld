"""Bygger jagtgraenser.json — de veje, åer og øer, jagttidsbekendtgørelsens
lokale områder er afgrænset af.

Kør: python3 tools/byg_jagtgraenser.py

Henter fra OpenStreetMap (Overpass) og gemmer FORENKLEDE linjer i repoets rod.
Appen spørger aldrig Overpass selv — tjenesten er for ustabil til at stå i vejen
for et »Gem«. Filen skal kun bygges igen, hvis en ny bekendtgørelse bruger andre
veje end dem i VEJE/AAER/OER herunder.

Data © OpenStreetMap-bidragydere, ODbL 1.0.
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

SPEJLE = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "Tilmeld-byg (+https://github.com/andreasdinesen/tilmeld)"
DK = "54.5,7.9,57.8,12.8"  # syd,vest,nord,øst

# Nøgle → regex på ref. Motorvejene har både E-nummer og dansk nummer.
VEJE = {
    "E20": r"(^|;) ?E ?20( ?;|$)", "E45": r"(^|;) ?E ?45( ?;|$)", "E39": r"(^|;) ?E ?39( ?;|$)",
    "11": r"(^|;) ?11( ?;|$)", "12": r"(^|;) ?12( ?;|$)", "13": r"(^|;) ?13( ?;|$)",
    "15": r"(^|;) ?15( ?;|$)", "16": r"(^|;) ?16( ?;|$)", "18": r"(^|;) ?18( ?;|$)",
    "21": r"(^|;) ?21( ?;|$)", "26": r"(^|;) ?26( ?;|$)", "28": r"(^|;) ?28( ?;|$)",
    "176": r"(^|;) ?176( ?;|$)", "180": r"(^|;) ?180( ?;|$)", "467": r"(^|;) ?467( ?;|$)",
    "533": r"(^|;) ?533( ?;|$)", "535": r"(^|;) ?535( ?;|$)", "585": r"(^|;) ?585( ?;|$)",
}
AAER = ["Gudenå", "Skjern Å", "Vejle Å", "Skals Å"]
OER = ["Als", "Kegnæs", "Tåsinge", "Møn", "Bogø", "Farø", "Nyord", "Mandø", "Rømø",
       "Sejerø", "Fejø", "Femø", "Lyø", "Strynø", "Endelave", "Nørrejyske Ø"]


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".osm-cache")


def overpass(q: str) -> dict:
    """Med cache: de offentlige Overpass-servere er tit overbelastede, og en
    afbrudt kørsel skal kunne fortsætte uden at hente alt forfra."""
    import hashlib
    os.makedirs(CACHE, exist_ok=True)
    fil = os.path.join(CACHE, hashlib.sha1(q.encode()).hexdigest() + ".json")
    if os.path.exists(fil):
        with open(fil, encoding="utf-8") as f:
            return json.load(f)
    svar = _overpass(q)
    with open(fil, "w", encoding="utf-8") as f:
        json.dump(svar, f)
    return svar


def _overpass(q: str) -> dict:
    data = urllib.parse.urlencode({"data": q}).encode()
    for runde in range(12):
        for url in SPEJLE:
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=240) as r:
                    tekst = r.read().decode("utf-8")
                if tekst.lstrip().startswith("{"):
                    return json.loads(tekst)
                print(f"  {url}: ikke JSON", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"  {url}: {e}", file=sys.stderr)
            time.sleep(5)
        time.sleep(min(30 * (runde + 1), 180))
    raise SystemExit("Overpass svarede ikke — prøv igen senere.")


def _afstand(p, a, b):
    """Punkt→linjestykke i meter (lokal flad tilnærmelse — rigeligt til 100 m)."""
    k = math.cos(math.radians(p[0]))
    px, py = p[1] * k, p[0]
    ax, ay, bx, by = a[1] * k, a[0], b[1] * k, b[0]
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy) * 111_320


def forenkl(pts, tol):
    """Douglas-Peucker. Endepunkterne bevares, så ringe forbliver lukkede."""
    if len(pts) < 3:
        return pts
    i, dmax = 0, 0
    for j in range(1, len(pts) - 1):
        d = _afstand(pts[j], pts[0], pts[-1])
        if d > dmax:
            i, dmax = j, d
    if dmax <= tol:
        return [pts[0], pts[-1]]
    return forenkl(pts[:i + 1], tol)[:-1] + forenkl(pts[i:], tol)


def linjer(elementer, tol):
    ud = []
    for e in elementer:
        if e["type"] == "way" and "geometry" in e:
            ud.append([[round(p["lat"], 5), round(p["lon"], 5)] for p in e["geometry"]])
        elif e["type"] == "relation":
            for m in e.get("members", []):
                if m.get("role") in ("outer", "") and "geometry" in m:
                    ud.append([[round(p["lat"], 5), round(p["lon"], 5)] for p in m["geometry"]])
    return [forenkl(l, tol) for l in ud if len(l) > 1]


def main():
    ud = {"kilde": "Data © OpenStreetMap-bidragydere, ODbL 1.0",
          "bygget": time.strftime("%Y-%m-%d"), "veje": {}, "aaer": {}, "oer": {}}
    for noegle, rx in VEJE.items():
        print(f"vej {noegle} …", flush=True)
        q = (f'[out:json][timeout:180];way["highway"~"^(motorway|trunk|primary|secondary|'
             f'tertiary)$"]["ref"~"{rx}"]({DK});out geom;')
        ud["veje"][noegle] = linjer(overpass(q)["elements"], 60)
        print(f"  {len(ud['veje'][noegle])} stykker")
        time.sleep(3)
    for navn in AAER:
        print(f"å {navn} …", flush=True)
        q = (f'[out:json][timeout:180];(way["waterway"="river"]["name"="{navn}"]({DK});'
             f'relation["waterway"="river"]["name"="{navn}"]({DK}););out geom;')
        ud["aaer"][navn] = linjer(overpass(q)["elements"], 60)
        print(f"  {len(ud['aaer'][navn])} stykker")
        time.sleep(3)
    for navn in OER:
        print(f"ø {navn} …", flush=True)
        q = (f'[out:json][timeout:180];(way["place"~"^(island|islet|peninsula)$"]["name"="{navn}"]'
             f'({DK});relation["place"~"^(island|islet|peninsula)$"]["name"="{navn}"]({DK}););'
             f'out geom;')
        tol = 300 if navn == "Nørrejyske Ø" else 40
        ud["oer"][navn] = linjer(overpass(q)["elements"], tol)
        print(f"  {len(ud['oer'][navn])} stykker")
        time.sleep(3)
    sti = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jagtgraenser.json")
    with open(sti, "w", encoding="utf-8") as f:
        json.dump(ud, f, ensure_ascii=False, separators=(",", ":"))
    print(f"skrevet {sti} ({os.path.getsize(sti) // 1024} KB)")


if __name__ == "__main__":
    main()
