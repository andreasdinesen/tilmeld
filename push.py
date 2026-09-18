"""Web Push (VAPID + RFC 8291) uden ekstra pakker.

Hele modulet hviler på `cryptography`, som allerede er her via `webauthn` —
der kommer altså ingen ny afhængighed ind for at kunne sende notifikationer.

**Erfaringerne fra doda er bygget ind her** (se `~/ClaudeMacBook/RUNE-ERFARINGER.md`
og `doda/app/push.js`); de er dyrt betalte, så lav dem ikke om uden grund:

1. **VAPID-signaturen skal være rå `r‖s` (64 b), ikke DER.** Alle biblioteker
   giver DER som standard — push-tjenesten afviser den med en kryptisk 400.
2. **Nyttelasten er ikke valgfri.** doda sendte først TOMME pushes, som kun
   vækkede service workeren. Det kom aldrig frem på iPhone: iOS skal vække en
   worker og køre JavaScript, før noget kan vises, og det led fejlede seks
   gange. Med en nyttelast kan systemet vise notifikationen SELV (Declarative
   Web Push, `web_push: 8030`) uden at starte workeren.
3. **Alle adresser i nyttelasten skal være ABSOLUTTE.** Nyttelasten læses af
   systemet, ikke af en side — der er ingen base at opløse `icon.png` imod. En
   relativ adresse får en streng parser til at kassere HELE notifikationen:
   Apple kvitterer 201, og der sker ingenting.
4. **`Urgency: high`.** Uden headeren er hastegraden »normal«, og så må
   push-tjenesten udsætte leveringen for at spare strøm.
5. **404/410 betyder »abonnementet findes ikke mere«** — slet rækken, ellers
   vokser en liste af døde endepunkter.

Krypteringen er efterprøvet mod RFC 8291's officielle testvektor — se
`selvtest()` nederst.
"""
import base64
import hmac
import json
import hashlib
import os
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURVE = ec.SECP256R1()


# --------------------------------------------------------------------------- #
# base64url uden padding — formatet alle Web Push-felter bruger
# --------------------------------------------------------------------------- #
def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _raw_public(key) -> bytes:
    """Den offentlige nøgle som UKOMPRIMERET punkt: 0x04 ‖ x ‖ y (65 b).
    Det er den form både `applicationServerKey` og RFC 8291 vil have."""
    return key.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


# --------------------------------------------------------------------------- #
# VAPID: nøglepar og Authorization-header
# --------------------------------------------------------------------------- #
def ensure_keys(conn) -> tuple:
    """(offentlig_b64u, privat_nøgle). Laves ÉN gang og bliver liggende —
    skifter nøglen, dør hvert eneste abonnement på alle enheder."""
    import db
    s = db.get_settings(conn)
    pub = (s["vapid_public"] or "").strip()
    priv = (s["vapid_private"] or "").strip()
    if pub and priv:
        return pub, serialization.load_der_private_key(base64.b64decode(priv), password=None)

    key = ec.generate_private_key(CURVE)
    pub = b64u(_raw_public(key.public_key()))
    der = key.private_bytes(serialization.Encoding.DER,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    conn.execute("UPDATE settings SET vapid_public = ?, vapid_private = ? WHERE id = 1",
                 (pub, base64.b64encode(der).decode()))
    conn.commit()
    return pub, key


def public_key(conn) -> str:
    return ensure_keys(conn)[0]


def _authorization(conn, endpoint: str, contact: str) -> str:
    """Ét ES256-JWT pr. push-tjeneste, gyldigt 12 timer.

    `sub` skal være en adresse, tjenesten kan kontakte afsenderen på — en
    https-URL eller en mailto:. Er der ingen offentlig URL sat op, falder vi
    tilbage på en mailto, så headeren i det mindste er gyldig.
    """
    pub, key = ensure_keys(conn)
    origin = "{0.scheme}://{0.netloc}".format(urlparse(endpoint))
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"},
                           separators=(",", ":")).encode())
    exp = int(datetime.now(timezone.utc).timestamp()) + 12 * 3600
    body = b64u(json.dumps({"aud": origin, "exp": exp, "sub": contact},
                           separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()

    # DER -> rå r‖s. Faldgrube 1: uden det her afvises tokenet.
    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s_ = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s_.to_bytes(32, "big")
    return f"vapid t={head}.{body}.{b64u(raw_sig)},k={pub}"


# --------------------------------------------------------------------------- #
# RFC 8291: krypter nyttelasten til ÉT abonnement
# --------------------------------------------------------------------------- #
def encrypt(plaintext: bytes, p256dh: str, auth: str,
            as_private=None, salt: bytes = None) -> bytes:
    """»aes128gcm«-kroppen til én modtager.

    Nøglerne (`p256dh`/`auth`) findes kun på modtagerens enhed, så push-
    tjenesten videresender en byteklump, den ikke selv kan læse. Derfor lærer
    Apple og Google aldrig, hvad eventet hedder.

    `as_private` og `salt` er KUN til selvtesten — i drift skal begge være
    friske pr. besked.
    """
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_public) != 65 or len(auth_secret) != 16:
        raise ValueError("abonnementets nøgler har forkert længde")

    as_private = as_private or ec.generate_private_key(CURVE)
    as_public = _raw_public(as_private.public_key())
    salt = salt or os.urandom(16)

    shared = as_private.exchange(ec.ECDH(),
                                 ec.EllipticCurvePublicKey.from_encoded_point(CURVE, ua_public))

    # RFC 8291 §3.4: BEGGE offentlige nøgler indgår i info-strengen, så nøglen
    # er bundet til præcis dette par — ikke bare til den delte hemmelighed.
    prk_key = _hmac(auth_secret, shared)
    key_info = b"WebPush: info\x00" + ua_public + as_public + b"\x01"
    ikm = _hmac(prk_key, key_info)

    # RFC 8188: herfra er det almindelig aes128gcm-indpakning.
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    # 0x02 markerer den SIDSTE record. Med 0x01 venter modtageren på mere og
    # kasserer det hele.
    body = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + body


# --------------------------------------------------------------------------- #
# Selve afsendelsen
# --------------------------------------------------------------------------- #
def build_payload(title: str, body: str, url: str, icon: str = "") -> dict:
    """Notifikationen som den skal se ud — lagt fast HER, ikke i service workeren.

    `web_push: 8030` er markøren, der gør den deklarativ: kan browseren læse
    den, viser SYSTEMET notifikationen uden at starte service workeren — og det
    er netop det led, der fejler på iOS. Kan den ikke, får workeren nyttelasten
    i `event.data` og viser den samme tekst. Ét kald dækker begge.

    `navigate` er påkrævet i formatet og skal være en absolut adresse.
    """
    # Skabelonerne slutter tit med et link, fordi en mail har brug for det. I en
    # notifikation er den rå adresse ren støj: et tryk fører allerede derhen.
    # Derfor fjernes en sidste linje, der ER destinationen.
    tekst = (body or "").strip()
    if url and tekst.endswith(url):
        tekst = tekst[:-len(url)].rstrip()
    n = {"title": title[:120], "body": tekst[:400], "navigate": url}
    if icon:
        n["icon"] = icon          # valgfri — et ikon er ikke en notifikation værd
    return {"web_push": 8030, "notification": n}


def send(conn, sub, title: str, body: str, url: str, icon: str = "",
         contact: str = "") -> dict:
    """Send én push. Returnér {ok, gone, status, message}.

    `gone` = abonnementet findes ikke mere og skal slettes af den, der kalder.
    Kaster aldrig: en notifikation må ikke kunne vælte det, der udløste den.
    """
    endpoint = (sub["endpoint"] or "").strip()
    try:
        u = urlparse(endpoint)
    except ValueError:
        return {"ok": False, "gone": True, "status": 0, "message": "ugyldigt endepunkt"}
    if u.scheme != "https":
        return {"ok": False, "gone": True, "status": 0, "message": "endepunkt er ikke https"}
    if not (sub["p256dh"] and sub["auth"]):
        return {"ok": False, "gone": True, "status": 0, "message": "abonnementet mangler nøgler"}

    try:
        payload = json.dumps(build_payload(title, body, url, icon),
                             separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        data = encrypt(payload, sub["p256dh"], sub["auth"])
        headers = {
            "Authorization": _authorization(conn, endpoint, contact or "mailto:tilmeld@invalid"),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            # Kort TTL: teksten er lagt fast ved afsendelsen, og en besked der
            # ligger i kø i timevis kan nå at handle om et event, der er aflyst.
            "TTL": "3600",
            "Urgency": "high",          # faldgrube 4
        }
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "gone": False, "status": r.status, "message": ""}
    except urllib.error.HTTPError as e:
        # Kroppen med: Apple og Google skriver HVORFOR de afviste
        # (»VapidPkHashMismatch«, »BadJwtToken«). Uden den er en 400 bare en 400.
        try:
            msg = e.read().decode("utf-8", "replace").strip()[:200]
        except Exception:
            msg = ""
        print(f"[PUSH-FEJL] {e.code} {msg}", flush=True)
        return {"ok": False, "gone": e.code in (404, 410), "status": e.code, "message": msg}
    except Exception as e:
        print(f"[PUSH-FEJL] {e}", flush=True)
        return {"ok": False, "gone": False, "status": 0, "message": str(e)[:200]}


# --------------------------------------------------------------------------- #
# Selvtest mod RFC 8291's officielle testvektor (§5)
# --------------------------------------------------------------------------- #
def selvtest() -> bool:
    """Kør med: ./.venv/bin/python -c "import push; push.selvtest()"

    Beviser at `encrypt()` rammer spec'en byte for byte — ikke bare at den er
    enig med sig selv. Erfaringsfilen er klar: efterprøv kryptoet FØR resten
    bygges, ellers kan 300 linjer være spildt.
    """
    ua_public = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    auth_secret = "BTBZMqHH6r4Tts7J_aSIgg"
    as_private_raw = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
    salt = "DGv6ra1nlYgDCS1FRnbzlw"
    forventet = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
                 "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVB"
                 "St2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
    plaintext = b"When I grow up, I want to be a watermelon"

    as_key = ec.derive_private_key(int.from_bytes(unb64u(as_private_raw), "big"), CURVE)
    faktisk = b64u(encrypt(plaintext, ua_public, auth_secret,
                           as_private=as_key, salt=unb64u(salt)))
    if faktisk == forventet:
        print("RFC 8291-testvektor: OK — krypteringen rammer spec'en byte for byte")
        return True
    print("RFC 8291-testvektor: FEJLEDE")
    print("  forventet:", forventet)
    print("  faktisk:  ", faktisk)
    return False


if __name__ == "__main__":
    raise SystemExit(0 if selvtest() else 1)
