"""SMS via Gigahosts Message Delivery Service (tel.gigahost.dk).

Skrevet direkte oven på stdlib — ingen ny afhængighed, præcis som `push.py`.
API'et er RESTful med XML-svar og HTTP basic auth; dokumentationen ligger på
http://tel.gigahost.dk/docs/.

Tre ting API'et kræver, som er værd at kende:

1. **Gatewayen slås op i DNS.** Gigahost kører flere gateways og annoncerer de
   aktive som SRV-poster på `_sms._tcp.tel.gigahost.dk`. Python har ingen
   SRV-opslag i stdlib, så der ligger en lille DNS-klient nederst i filen.
   Slår den fejl (ingen resolver, UDP spærret), bruges `FALLBACK_GATEWAYS` —
   opslaget er en forbedring, ikke en forudsætning.
2. **Afsenderen skal være verificeret** på Gigahost-kontoen. Et ikke-godkendt
   nummer giver 403, ikke en fejl i selve beskeden.
3. **En besked må fylde højst 3 SMS-dele.** Latin-1 giver 459 tegn, men ét
   eneste tegn udenfor latin-1 (fx en tankestreg) tvinger hele beskeden over i
   UCS2 og halverer pladsen til 201. Derfor oversætter `prepare()` de typografiske
   tegn, appen selv skriver, inden længden måles.

Kør `python gigasms.py` for at slå gatewayene op (og med GIGAHOST_USER +
GIGAHOST_PASSWORD i miljøet: hente saldoen og de godkendte afsendernumre).
"""
import base64
import os
import random
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SERVICE_ADDRESS = "_sms._tcp.tel.gigahost.dk"
# Bruges hvis SRV-opslaget ikke kan lade sig gøre. Listen er dokumentationens
# eksempel; den rigtige liste kommer fra DNS.
FALLBACK_GATEWAYS = ["gw1.tel.gigahost.dk", "gw2.tel.gigahost.dk"]

MAX_RECIPIENTS = 1000          # gatewayens grænse pr. forsendelse
LATIN1_MAX = 459               # 3 SMS-dele i latin-1
UCS2_MAX = 201                 # 3 SMS-dele i unicode
TIMEOUT = 20

_gateway_cache = {"hosts": [], "at": 0.0}
_CACHE_SECS = 3600


class SmsError(Exception):
    """Beskeden kom ikke af sted. Teksten er på dansk og må vises til admin."""


# ---- Tekst: hold beskeden indenfor 3 SMS-dele --------------------------------

# Typografiske tegn appen selv bruger i skabeloner og logtekster. De findes ikke
# i latin-1, og ét af dem ville tvinge HELE beskeden ned på 201 tegn — og koste
# det samme i SMS-dele undervejs. Oversættelsen er ren gevinst.
_TYPOGRAFI = {
    "—": "-", "–": "-", "…": "...", " ": " ",
    "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'",
    "‑": "-", "−": "-", "•": "*", "→": "->",
}


def prepare(text: str) -> str:
    """Gør teksten klar til en SMS: oversæt typografi og klip til 3 dele."""
    text = "".join(_TYPOGRAFI.get(ch, ch) for ch in (text or ""))
    try:
        text.encode("latin-1")
        limit = LATIN1_MAX
    except UnicodeEncodeError:
        limit = UCS2_MAX
    if len(text) > limit:
        text = text[:limit - 3].rstrip() + "..."
    return text


def parts(text: str) -> int:
    """Hvor mange SMS-dele fylder teksten — altså hvad den kommer til at koste.

    En enkeltstående SMS har plads til 160 tegn (70 i unicode). Skal beskeden
    deles, går der plads fra hver del til det, der syr dem sammen igen, og
    grænsen falder til 153 (67). Det er dét regnestykke, `LATIN1_MAX` = 3 × 153
    og `UCS2_MAX` = 3 × 67 kommer af.
    """
    try:
        text.encode("latin-1")
        alene, delt = 160, 153
    except UnicodeEncodeError:
        alene, delt = 70, 67
    n = len(text or "")
    if not n:
        return 0
    return 1 if n <= alene else -(-n // delt)


# ---- HTTP mod gatewayen ------------------------------------------------------

def configured(settings) -> bool:
    """Er SMS sat op globalt? Alle tre dele skal være der for at kunne sende."""
    return bool((settings["sms_username"] or "").strip()
                and (settings["sms_password"] or "")
                and (settings["sms_sender"] or "").strip())


def _request(username, password, method, path, params=None) -> bytes:
    """Kald gatewayen. Prøv næste gateway hvis en ikke svarer.

    Brugernavn og kodeord sendes som en Authorization-header, ikke i URL'en:
    en URL med credentials havner i undtagelsestekster og dermed i loggen.
    """
    body = urllib.parse.urlencode(params or {}).encode() if params else None
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Accept": "application/xml", "Authorization": "Basic " + token}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    hosts = gateways()
    sidste = None
    for host in hosts:
        req = urllib.request.Request(f"https://{host}/{path.lstrip('/')}",
                                     data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # En HTTP-fejl ER et svar — gatewayen lever. At prøve den næste ville
            # bare give samme svar en gang til.
            raise SmsError(_http_fejl(e.code)) from e
        except Exception as e:      # netværk/TLS/timeout: prøv den næste gateway
            sidste = e
    raise SmsError(f"ingen af SMS-gatewayene svarede ({sidste})")


def _http_fejl(code: int) -> str:
    if code == 400:
        return "gatewayen afviste beskeden (tjek nummerformat — husk landekode)"
    if code == 401:
        return "forkert brugernavn eller API-adgangskode"
    if code == 403:
        return "afsendernummeret er ikke godkendt på Gigahost-kontoen"
    if code == 402:
        return "ikke flere SMS-klip på kontoen"
    return f"uventet svar fra gatewayen (HTTP {code})"


def _xml(data: bytes):
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise SmsError(f"kunne ikke læse svaret fra gatewayen ({e})") from e


def _tal(root, tag, default=0) -> int:
    el = root.find(tag)
    try:
        return int((el.text or "").strip())
    except (AttributeError, ValueError):
        return default


# ---- De kald appen bruger ----------------------------------------------------

def send(username, password, sender, recipients, message, tag="") -> dict:
    """Send én besked til én eller flere modtagere.

    Returnér {"id", "cost", "accepted", "erroneous", "erroneous_numbers"}.
    Rejser SmsError hvis intet kom af sted.
    """
    if isinstance(recipients, str):
        recipients = [recipients]
    numre = [normalize(n) for n in recipients if (n or "").strip()]
    if not numre:
        raise SmsError("ingen modtager")
    if len(numre) > MAX_RECIPIENTS:
        raise SmsError(f"højst {MAX_RECIPIENTS} modtagere pr. forsendelse")

    params = {"sender": normalize(sender), "recipients": ",".join(numre),
              "message": prepare(message)}
    if tag:
        params["tag"] = tag
    root = _xml(_request(username, password, "POST", "messages", params))

    fejlede = [(e.text or "").strip()
               for e in root.findall("erroneousRecipients/erroneousRecipient")]
    msg = root.find("Message")
    accepteret = _tal(root, "acceptedRecipientCount")
    if not accepteret:
        raise SmsError("gatewayen accepterede ingen af modtagerne"
                       + (f" ({', '.join(fejlede)})" if fejlede else ""))
    return {"id": msg.get("id", "") if msg is not None else "",
            "cost": _tal(root, "cost"),
            "accepted": accepteret,
            "erroneous": _tal(root, "erroneousRecipientCount"),
            "erroneous_numbers": fejlede}


def credits(username, password) -> dict:
    """Saldoen på kontoen: {"username", "credits", "unit"}."""
    root = _xml(_request(username, password, "GET", "credits"))
    el = root.find("credits")
    return {"username": (root.findtext("username") or "").strip(),
            "credits": _tal(root, "credits", -1),
            "unit": (el.get("unit") if el is not None else "") or "messages"}


def numbers(username, password) -> list:
    """De afsendernumre, kontoen må sende fra.

    Svarets nøjagtige form er ikke dokumenteret, så parseren er bevidst tolerant:
    den samler alt, der ligner et nummer — både som `number`-attribut og som tekst.
    """
    root = _xml(_request(username, password, "GET", "numbers"))
    fundet = []
    for el in root.iter():
        for kandidat in (el.get("number"), (el.text or "").strip()):
            k = (kandidat or "").strip()
            if k and sum(c.isdigit() for c in k) >= 6 and k not in fundet:
                fundet.append(k)
    return fundet


def normalize(number: str) -> str:
    """Nummerets form på vej til gatewayen: kun cifre og et evt. ledende +.

    Gigahost vil have landekode (»+« må gerne udelades), så mellemrum og de
    bindestreger, folk skriver numre med, skal væk. Et nummer UDEN landekode
    lades i fred — det er gatewayens opgave at afvise, ikke vores at gætte.
    """
    s = (number or "").strip()
    plus = s.startswith("+")
    cifre = "".join(c for c in s if c.isdigit())
    return ("+" if plus else "") + cifre


# ---- SRV-opslag (lille DNS-klient, kun stdlib) -------------------------------

def gateways(force: bool = False) -> list:
    """De aktive gateways, bedste først. Caches en time.

    Fejler opslaget, bruges `FALLBACK_GATEWAYS`: en kendt gateway er bedre end
    ingen besked. Derfor må denne funktion aldrig rejse.
    """
    nu = time.time()
    if not force and _gateway_cache["hosts"] and nu - _gateway_cache["at"] < _CACHE_SECS:
        return _gateway_cache["hosts"]
    try:
        hosts = _srv_lookup(SERVICE_ADDRESS)
    except Exception:
        hosts = []
    if not hosts:
        hosts = list(FALLBACK_GATEWAYS)
    _gateway_cache.update(hosts=hosts, at=nu)
    return hosts


def _resolvers() -> list:
    """Navneservere fra /etc/resolv.conf (findes både i containeren og på macOS)."""
    ud = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as f:
            for linje in f:
                dele = linje.split()
                if len(dele) >= 2 and dele[0] == "nameserver":
                    ud.append(dele[1])
    except OSError:
        pass
    return ud[:3]


def _srv_lookup(name: str) -> list:
    """Slå SRV-poster op og returnér værtsnavne sorteret efter prioritet/vægt."""
    poster = []
    for server in _resolvers():
        try:
            poster = _srv_query(server, name)
        except Exception:
            continue
        if poster:
            break
    if not poster:
        return []

    # RFC 2782: lavest prioritet først; indenfor samme prioritet trækkes der lod
    # efter vægt, så belastningen fordeles mellem ligeværdige gateways.
    ud = []
    for prio in sorted({p[0] for p in poster}):
        pulje = [p for p in poster if p[0] == prio]
        while pulje:
            total = sum(max(1, p[1]) for p in pulje)
            lod = random.randint(1, total)
            lobende = 0
            for p in pulje:
                lobende += max(1, p[1])
                if lobende >= lod:
                    ud.append(p[2])
                    pulje.remove(p)
                    break
    return ud


def _srv_query(server: str, name: str) -> list:
    """Ét DNS-opslag over UDP. Returnér [(prioritet, vægt, værtsnavn), ...]."""
    tid = random.randint(0, 0xFFFF)
    sporgsmal = b"".join(bytes([len(d)]) + d.encode("ascii")
                         for d in name.split(".") if d) + b"\x00"
    pakke = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0) + sporgsmal \
        + struct.pack(">HH", 33, 1)          # QTYPE=SRV, QCLASS=IN

    fam = socket.AF_INET6 if ":" in server else socket.AF_INET
    with socket.socket(fam, socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        s.sendto(pakke, (server, 53))
        svar, _ = s.recvfrom(4096)

    if len(svar) < 12 or struct.unpack(">H", svar[:2])[0] != tid:
        return []
    qd, an = struct.unpack(">HH", svar[4:8])
    pos = 12
    for _ in range(qd):
        pos = _skip_name(svar, pos) + 4

    poster = []
    for _ in range(an):
        pos = _skip_name(svar, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", svar[pos:pos + 10])
        pos += 10
        if rtype == 33 and rdlen >= 7:
            prio, vaegt, _port = struct.unpack(">HHH", svar[pos:pos + 6])
            vaert, _ = _read_name(svar, pos + 6)
            if vaert:
                poster.append((prio, vaegt, vaert))
        pos += rdlen
    return poster


def _skip_name(data: bytes, pos: int) -> int:
    """Spring over et navn uden at følge pointere — returnér positionen efter."""
    while pos < len(data):
        n = data[pos]
        if n == 0:
            return pos + 1
        if n & 0xC0 == 0xC0:      # komprimeret: to bytes, og så er navnet slut
            return pos + 2
        pos += 1 + n
    return pos


def _read_name(data: bytes, pos: int, dybde: int = 0) -> tuple:
    """Læs et navn, følg evt. komprimerings-pointere. Returnér (navn, position)."""
    dele = []
    while pos < len(data) and dybde < 10:
        n = data[pos]
        if n == 0:
            return ".".join(dele), pos + 1
        if n & 0xC0 == 0xC0:
            mal = struct.unpack(">H", data[pos:pos + 2])[0] & 0x3FFF
            resten, _ = _read_name(data, mal, dybde + 1)
            dele.append(resten)
            return ".".join(d for d in dele if d), pos + 2
        dele.append(data[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    return ".".join(dele), pos


# ---- Selvtest ----------------------------------------------------------------

if __name__ == "__main__":
    print("SRV-opslag:", SERVICE_ADDRESS)
    for h in gateways(force=True):
        print("  gateway:", h)
    print("prepare(): tankestreg bliver til bindestreg:",
          prepare("Nyt event — husk tilmelding …"))
    bruger = os.environ.get("GIGAHOST_USER")
    kode = os.environ.get("GIGAHOST_PASSWORD")
    if bruger and kode:
        try:
            print("saldo:", credits(bruger, kode))
            print("afsendernumre:", numbers(bruger, kode))
        except SmsError as e:
            print("fejl:", e)
    else:
        print("(sæt GIGAHOST_USER + GIGAHOST_PASSWORD for at teste saldo/numre)")
    # TLS-verifikation skal være slået til — ellers er basic auth værdiløs.
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED
