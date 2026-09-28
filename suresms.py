"""SMS via SureSMS (api.suresms.com, deres HTTP(S)-API).

Tredje udbyder ved siden af `gigasms.py` og `inmobile.py`. Samme overflade —
`configured()`, `send()` og en `SmsError` med en dansk tekst — så
`notifications.send_sms` kun skal vælge mellem dem.

Dokumentationen: https://developer.suresms.com/https/ (siden ligger bag
Cloudflare og kan ikke hentes med curl; den skal læses i en browser).

Fem ting, API'et gør på sin egen måde:

1. **Godkendelsen er to almindelige felter**, login og adgangskode, sendt som
   parametre — ikke en header. Bruger man en API-nøgle i stedet for kontoens
   adgangskode, skal login være det faste ord `apikey`. Nøgler laves på
   https://app.suresms.com/UserApi/Index.
2. **Nummeret skal have plus og landekode** (`+4540123456`) — modsat inMobile,
   der vil have det uden plus. Vores numre står i forvejen på den form.
3. **Én modtager pr. kald.** Flere numre adskilt med `;` virker kun på deres
   v3-endpoint, så her sendes ét kald pr. nummer og resultaterne tælles sammen.
4. **Afsenderen er valgfri** (kontoens standard bruges ellers), højst 11 tegn —
   og den skal være godkendt hos SureSMS. Er den ikke det, svarer de 401 på hver
   eneste besked, selvom login er rigtigt.
5. **Svaret er en HTTP-kode, ikke en nyttelast.** 200 betyder »modtaget til
   forsendelse« — ikke leveret. 400 og 401 har hver sin tekst i kroppen, og de
   er skrevet til mennesker: »I don't know you. Bye bye!« er deres måde at sige
   forkert login på.

Teksten sendes som UTF-8. Skulle æ, ø og å komme skævt frem på telefonen, har
SureSMS et alternativt endpoint (`SendSMS_UTF7.aspx`) — det er dét, der skal
skiftes til, og intet andet.

Kør `python suresms.py` for at se forsendelsen uden at sende den (og med
SURESMS_LOGIN + SURESMS_PASSWORD i miljøet: slå saldoen op).
"""
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import smstekst

BASE = "https://api.suresms.com"
SEND_STI = "/Script/SendSMS.aspx"
SALDO_STI = "/script/GetUserBalance.aspx"
TIMEOUT = 20
MAX_SENDER = 11                # deres grænse for et afsender-navn

# Reglerne for selve teksten er de samme som hos de andre udbydere.
LATIN1_MAX = smstekst.LATIN1_MAX
UCS2_MAX = smstekst.UCS2_MAX
prepare = smstekst.prepare
parts = smstekst.parts


class SmsError(Exception):
    """Beskeden kom ikke af sted. Teksten er på dansk og må vises til admin."""


NAVN = "SureSMS"


def configured(settings) -> bool:
    """Er SureSMS sat op globalt? Login og adgangskode/API-nøgle skal være der.

    Afsenderen er IKKE med: den er valgfri hos SureSMS, som falder tilbage på
    kontoens standard-afsender.
    """
    return not mangler(settings)


def mangler(settings) -> str:
    """Hvad står tomt? "" hvis udbyderen er klar. Teksten vises til master."""
    savn = [navn for navn, n in (("et login", "suresms_login"),
                                 ("en adgangskode eller API-nøgle", "suresms_password"))
            if not (settings[n] or "").strip()]
    if not savn:
        return ""
    return f"{NAVN} mangler " + " og ".join(savn)


def send_settings(settings, to, body) -> dict:
    """Send med de oplysninger, master har gemt. Samme kald hos alle udbydere."""
    return send(settings["suresms_login"], settings["suresms_password"],
                settings["suresms_sender"], to, body, tag="Tilmeld")


def _nummer(v: str) -> str:
    """»+45 40 12 34 56« -> »+4540123456«. SureSMS vil have plusset med."""
    cifre = "".join(ch for ch in str(v or "") if ch.isdigit())
    return f"+{cifre}" if cifre else ""


def _kald(sti: str, params: dict) -> str:
    """Ét kald mod API'et. Returnér svarets krop, eller rejs SmsError.

    POST og ikke GET: login og adgangskode ville ellers stå i hver eneste
    adresse undervejs — i proxy-logs, i vores egne fejlbeskeder, overalt.
    """
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(BASE + sti, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as svar:
            return svar.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        raise SmsError(_fejltekst(e)) from e
    except urllib.error.URLError as e:
        raise SmsError(f"kunne ikke nå SureSMS ({e.reason})") from e


def _fejltekst(e) -> str:
    """SureSMS' fejl som én dansk linje.

    Deres egne tekster er på engelsk og henvendt til et menneske (»I don't know
    you. Bye bye!« = forkert login). De oversættes her, for admin skal kunne se
    HVAD han skal rette — ikke gætte ud fra en talkode.
    """
    try:
        krop = e.read().decode("utf-8", "replace").strip()[:200]
    except Exception:
        krop = ""
    lav = krop.lower()
    if "i don't know you" in lav or "i dont know you" in lav:
        return "SureSMS godkendte ikke login og adgangskode"
    if e.code == 401:
        return ("SureSMS afviste forsendelsen (401) — afsenderen er sandsynligvis "
                "ikke godkendt på kontoen")
    if "wrong parameters" in lav:
        return "SureSMS mangler eller forstod ikke et felt i forsendelsen"
    if "group not found" in lav:
        return "SureSMS opfattede modtageren som et gruppenavn — mangler nummeret landekode?"
    return f"SureSMS svarede {e.code}" + (f": {krop}" if krop else "")


def send(login: str, password: str, sender: str, recipients, message: str,
         tag: str = "") -> dict:
    """Send én besked til én eller flere modtagere.

    Returnér {"id", "cost", "accepted", "erroneous", "erroneous_numbers"} — de
    samme nøgler som de to andre udbydere, så afsendelseslaget kan behandle dem
    ens. `cost` kan ikke læses af svaret her og sættes til antallet af SMS-dele,
    vi selv har regnet os frem til.

    `tag` findes for at have samme kald som de øvrige; SureSMS har intet felt
    til det.
    """
    if isinstance(recipients, str):
        recipients = [recipients]
    numre = [_nummer(n) for n in recipients if _nummer(n)]
    if not numre:
        raise SmsError("ingen modtager")
    tekst = prepare(message)
    if not tekst:
        raise SmsError("tom besked")

    sendt, fejlede, sidste = 0, [], None
    for nummer in numre:
        params = {"login": (login or "").strip(), "password": password or "",
                  "to": nummer, "text": tekst}
        # Tom afsender = kontoens standard. Sender vi et tomt felt med, risikerer
        # vi et 401 på noget, brugeren aldrig har bedt om.
        if (sender or "").strip():
            params["from"] = (sender or "").strip()[:MAX_SENDER]
        try:
            _kald(SEND_STI, params)
            sendt += 1
        except SmsError as e:
            fejlede.append(nummer)
            sidste = e
    if not sendt:
        raise sidste or SmsError("ingen af beskederne kunne sendes")
    return {"id": "", "cost": parts(tekst) * sendt, "accepted": sendt,
            "erroneous": len(fejlede), "erroneous_numbers": fejlede}


def balance(login: str, password: str) -> dict:
    """Kontoens saldo: {"amount": tal-som-tekst, "currency": "DKK", "raw": ...}.

    Svaret er XML, og hvad felterne hedder, står der ikke noget sted — derfor
    læses det eftergivende: første tal er beløbet, første bogstavfelt valutaen.
    Er formen en anden end forventet, returneres den rå tekst, så admin kan se
    NOGET i stedet for en fejl.
    """
    raa = _kald(SALDO_STI, {"login": (login or "").strip(), "password": password or ""})
    ud = {"amount": "", "currency": "", "raw": raa[:200]}
    try:
        rod = ET.fromstring(raa)
    except ET.ParseError:
        return ud
    for node in rod.iter():
        vaerdi = (node.text or "").strip()
        if not vaerdi:
            continue
        if not ud["amount"] and vaerdi.replace(",", ".").replace("-", "").replace(".", "").isdigit():
            ud["amount"] = vaerdi
        elif not ud["currency"] and vaerdi.isalpha() and len(vaerdi) <= 4:
            ud["currency"] = vaerdi
    return ud


def selvtest():
    """Byg en forsendelse uden at sende den, og vis den."""
    params = {"login": "apikey", "password": "<nøgle>", "to": _nummer("+45 40 30 01 00"),
              "text": prepare("Madbestilling: Jagt 3 oktober 2026 — mad til 13."),
              "from": "Svaljagt"}
    print("POST", BASE + SEND_STI)
    print(urllib.parse.urlencode(params))
    assert params["to"] == "+4540300100", "nummeret skal have plus og landekode"
    assert "—" not in params["text"], "tankestregen skal være oversat"
    assert _nummer("40300100") == "+40300100", "cifre uden landekode røres ikke"
    print("\nFormen er i orden.")
    login = (os.environ.get("SURESMS_LOGIN") or "").strip()
    kode = os.environ.get("SURESMS_PASSWORD") or ""
    if not login or not kode:
        print("Sæt SURESMS_LOGIN + SURESMS_PASSWORD for også at slå saldoen op.")
        return
    try:
        print("Saldo:", balance(login, kode))
    except SmsError as e:
        print("Kunne ikke slå saldoen op:", e)


if __name__ == "__main__":
    selvtest()
