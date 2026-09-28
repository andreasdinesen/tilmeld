"""SMS via inMobile (api.inmobile.com, REST API v4).

Anden udbyder ved siden af `gigasms.py`. Samme overflade — `configured()`,
`send()` og en `SmsError` med en dansk tekst — så `notifications.send_sms` kun
skal vælge mellem dem, ikke gøre noget forskelligt.

Skrevet direkte oven på stdlib: JSON over HTTPS, ingen ny afhængighed.
Dokumentationen er maskinlæsbar på https://api.inmobile.com/swagger/v1/swagger.json
(ReDoc-siden på /docs/ er bare den pæne udgave af den fil).

Fire ting, API'et kræver, som er værd at kende:

1. **Godkendelsen er Basic auth, hvor brugernavnet er ligegyldigt.** API-nøglen
   er adgangskoden; brugernavnet kasseres. Nøglen laves i inMobiles egen
   administration. (Nøglen kan også sendes som `?apikey=` i adressen — det gør vi
   ikke: så står den i enhver proxy-log undervejs.)
2. **Nummeret skrives uden plus.** `4540123456`, ikke `+4540123456`. Vi gemmer
   numre med plus i databasen, så `_nummer()` renser dem — og sender `countryHint`
   med, så et gammelt 8-cifret nummer uden landekode stadig kan valideres.
3. **Afsenderen må være tekst.** 3-11 tegn bogstaver (fx »Svaljagt«) eller op til
   14 cifre. Det er forskellen fra Gigahost, hvor afsenderen skal være et
   verificeret NUMMER. En tekstafsender kan der ikke svares på.
4. **Et svar med 200 betyder ikke, at beskeden er sendt.** Hver modtager får en
   linje i `results` med `isValidMsisdn`; er nummeret ugyldigt, står fejlen dér —
   ikke i HTTP-koden. Derfor tæller `send()` selv efter.

Kør `python inmobile.py` for at prøve nøglen af (sætter INMOBILE_API_KEY i miljøet
og slår kontoens lister op — et harmløst opslag, der ikke sender noget).
"""
import base64
import json
import os
import urllib.error
import urllib.request

import smstekst

BASE = "https://api.inmobile.com"
TIMEOUT = 20
MAX_RECIPIENTS = 250           # API'ets grænse: 1-250 beskeder pr. kald

# Reglerne for selve teksten er de samme som hos den anden udbyder.
LATIN1_MAX = smstekst.LATIN1_MAX
UCS2_MAX = smstekst.UCS2_MAX
prepare = smstekst.prepare
parts = smstekst.parts


class SmsError(Exception):
    """Beskeden kom ikke af sted. Teksten er på dansk og må vises til admin."""


NAVN = "inMobile"


def configured(settings) -> bool:
    """Er inMobile sat op globalt? Nøgle og afsender skal begge være der."""
    return not mangler(settings)


def mangler(settings) -> str:
    """Hvad står tomt? "" hvis udbyderen er klar. Teksten vises til master."""
    savn = [navn for navn, n in (("en API-nøgle", "inmobile_api_key"),
                                 ("en afsender", "inmobile_sender"))
            if not (settings[n] or "").strip()]
    if not savn:
        return ""
    return f"{NAVN} mangler " + " og ".join(savn)


def send_settings(settings, to, body) -> dict:
    """Send med de oplysninger, master har gemt. Samme kald hos alle udbydere."""
    return send(settings["inmobile_api_key"], settings["inmobile_sender"],
                to, body, tag="Tilmeld")


def _nummer(v: str) -> str:
    """»+45 40 12 34 56« -> »4540123456«. Kun cifre; plus og mellemrum ryger."""
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _afsender(v: str) -> str:
    """Afsenderen som API'et vil have den: cifre (op til 14) eller tekst (3-11).

    Længere tekst klipper API'et selv af, men så står der noget andet på
    telefonen, end der står i opsætningen — hellere klippe her, hvor det kan ses.
    """
    v = str(v or "").strip()
    cifre = "".join(ch for ch in v if ch.isdigit())
    if cifre and not any(ch.isalpha() for ch in v):
        return cifre[:14]
    return v[:11]


def _kald(api_key: str, metode: str, sti: str, krop=None):
    """Ét kald mod API'et. Returnér det afkodede JSON-svar eller rejs SmsError."""
    if not (api_key or "").strip():
        raise SmsError("ingen API-nøgle til inMobile")
    data = json.dumps(krop).encode("utf-8") if krop is not None else None
    req = urllib.request.Request(f"{BASE}{sti}", data=data, method=metode)
    # TOMT brugernavn og nøglen som adgangskode. Dokumentationen siger, at
    # brugernavnet kasseres, men inMobiles egen PHP-klient sender `:<nøgle>`
    # (CURLOPT_USERPWD), og der er ingen grund til at afvige fra den udgave, de
    # selv tester imod.
    noegle = base64.b64encode(f":{api_key.strip()}".encode("utf-8")).decode()
    req.add_header("Authorization", f"Basic {noegle}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as svar:
            raa = svar.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as e:
        raise SmsError(_fejltekst(e)) from e
    except urllib.error.URLError as e:
        raise SmsError(f"kunne ikke nå inMobile ({e.reason})") from e
    try:
        return json.loads(raa)
    except ValueError:
        raise SmsError("uforståeligt svar fra inMobile")


def _fejltekst(e) -> str:
    """Lav API'ets fejlsvar om til én linje, admin kan bruge til noget.

    Formatet er {"errorMessage": "...", "details": ["..."]}. 401 får sin egen
    tekst, fordi »Unauthorized« ikke fortæller nogen, hvad de skal gøre.
    """
    try:
        krop = json.loads(e.read().decode("utf-8"))
    except Exception:
        krop = {}
    besked = (krop.get("errorMessage") or "").strip()
    detaljer = "; ".join(str(d) for d in (krop.get("details") or []))[:200]
    if e.code == 401:
        # Ingen 403 i API'et: en nøgle uden rettighed til netop dette opslag får
        # samme svar som en forkert nøgle. Teksten må ikke gætte på hvilken.
        return ("inMobile afviste nøglen (401) — enten er den forkert, eller også "
                "har den ikke lov til dette opslag")
    samlet = " — ".join(x for x in (besked, detaljer) if x)
    return f"inMobile svarede {e.code}" + (f": {samlet}" if samlet else "")


def send(api_key: str, sender: str, recipients, message: str, tag: str = "") -> dict:
    """Send én besked til én eller flere modtagere.

    Returnér {"accepted", "erroneous", "erroneous_numbers", "cost", "id"} — de
    samme nøgler som `gigasms.send`, så afsendelseslaget kan behandle de to
    udbydere ens. `cost` er antallet af SMS-dele, API'et siger det bliver.

    `tag` findes for at have samme kald som Gigahost; inMobile har ikke noget
    felt til det, så den bruges ikke.
    """
    if isinstance(recipients, str):
        recipients = [recipients]
    numre = [_nummer(n) for n in recipients if _nummer(n)]
    if not numre:
        raise SmsError("ingen modtager")
    if len(numre) > MAX_RECIPIENTS:
        raise SmsError(f"højst {MAX_RECIPIENTS} modtagere pr. forsendelse")
    tekst = prepare(message)
    if not tekst:
        raise SmsError("tom besked")

    krop = {"messages": [{
        "to": n,
        "countryHint": "45",
        "text": tekst,
        "from": _afsender(sender),
        # "auto": API'et vælger selv gsm7 og skifter først til ucs2, hvis teksten
        # kræver det. Vi klipper allerede teksten efter samme regel, så det
        # eneste, "auto" ændrer, er at et enkelt særtegn ikke koster dobbelt.
        "encoding": "auto",
    } for n in numre]}
    svar = _kald(api_key, "POST", "/v4/sms/outgoing", krop)

    linjer = svar.get("results") or []
    gyldige = [r for r in linjer if r.get("numberDetails", {}).get("isValidMsisdn")]
    fejlede = [r.get("numberDetails", {}).get("rawMsisdn", "")
               for r in linjer if not r.get("numberDetails", {}).get("isValidMsisdn")]
    if not gyldige:
        raise SmsError("inMobile godkendte ingen af numrene"
                       + (f" ({', '.join(x for x in fejlede if x)})" if fejlede else ""))
    return {"id": (gyldige[0].get("messageId") or ""),
            "cost": sum(int(r.get("smsCount") or 0) for r in gyldige),
            "accepted": len(gyldige),
            "erroneous": len(fejlede),
            "erroneous_numbers": [x for x in fejlede if x]}


# Harmløse opslag, nøglen kan prøves af med. Rækkefølgen er ikke tilfældig:
# skabelonerne hører til SMS-området ligesom afsendelsen, så en nøgle, der må
# sende, må som regel også læse dem. Modtagerlisterne er reserven.
#
# Statusrapporterne ville ellers være det oplagte valg — de er en fælde: API'et
# udleverer hver rapport ÉN gang og sletter den bagefter, så et »tjek nøglen«-klik
# ville smide kvitteringerne for allerede sendte beskeder væk.
_PROEVEOPSLAG = (("SMS-skabelonerne", "/v4/sms/templates?pageLimit=1"),
                 ("modtagerlisterne", "/v4/lists?pageLimit=1"))


def check(api_key: str) -> dict:
    """Prøv nøglen af uden at sende noget. Returnér {"opslag": det der svarede}.

    En nøgle hos inMobile kan begrænses til bestemte operationer, og API'et har
    ingen 403: en manglende rettighed kommer tilbage som 401 — præcis som en
    forkert nøgle. Derfor prøves flere opslag. Svarer ét af dem, er nøglen god;
    svarer ingen, siger vi det, vi VED (den kom ikke igennem her), og ikke at den
    er forkert.

    inMobile har ikke noget saldo-opslag i API'et — antallet af SMS-klip ses kun
    på deres egen side.
    """
    sidste = None
    for navn, sti in _PROEVEOPSLAG:
        try:
            _kald(api_key, "GET", sti)
            return {"opslag": navn}
        except SmsError as e:
            sidste = e
    raise sidste


def selvtest():
    """Byg en forsendelse uden at sende den, og vis den.

    Kaldet mod API'et kræver en nøgle; men formen på det, vi sender, kan tjekkes
    uden. Sæt INMOBILE_API_KEY i miljøet for også at prøve nøglen af.
    """
    krop = {"messages": [{"to": _nummer("+45 40 30 01 00"), "countryHint": "45",
                          "text": prepare("Madbestilling: Jagt 3 oktober 2026 — mad til 13."),
                          "from": _afsender("Svaljagt"), "encoding": "auto"}]}
    print("POST", BASE + "/v4/sms/outgoing")
    print(json.dumps(krop, indent=2, ensure_ascii=False))
    assert krop["messages"][0]["to"] == "4540300100", "nummeret skal renses for plus"
    assert "—" not in krop["messages"][0]["text"], "tankestregen skal være oversat"
    assert _afsender("Svallinggaard Jagt") == "Svallinggaa", "tekstafsender klippes ved 11"
    assert _afsender("+45 40 30 01 00") == "4540300100", "nummer-afsender beholder cifrene"
    print("\nFormen er i orden.")
    noegle = (os.environ.get("INMOBILE_API_KEY") or "").strip()
    if not noegle:
        print("Sæt INMOBILE_API_KEY for også at prøve nøglen af.")
        return
    try:
        print("Nøglen virker:", check(noegle))
    except SmsError as e:
        print("Nøglen virker IKKE:", e)


if __name__ == "__main__":
    selvtest()
