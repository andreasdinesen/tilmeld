"""Passkeys (WebAuthn) til master-, gruppe-admin- og bruger-login.

Tre »scopes«, alle i tabellen `credentials`:

  master  -> giver adgang til /master           (group_id og user_id er NULL)
  admin   -> giver adgang til /<slug>/admin     (group_id sat)
  user    -> logger en brugerkonto ind          (user_id sat)

**Passkeys er altid et TILLÆG, aldrig en erstatning.** Adgangskoden virker uændret —
dels fordi WebAuthn kræver et sikkert kontekst (https eller localhost), og yggdrasil-
panelet serverer appen over almindelig http på IP:port, dels så man aldrig kan låse sig
ude ved at miste sin telefon.

Nøglerne oprettes som *discoverable credentials* (resident keys), så login ikke kræver
brugernavn: browseren viser selv de nøgler, der hører til domænet.

rp_id og origin udledes **pr. request** af X-Forwarded-Host/-Proto, så det virker bag
Cloudflare Tunnel uden konfiguration (samme greb som i de øvrige runer).
"""
import base64
from datetime import datetime

# webauthn-pakken er en ekstra afhængighed. Mangler den (fx efter en selv-opdatering,
# hvor 'git pull' lykkedes men pip-trinnet ikke blev kørt), skal appen stadig starte —
# uden passkeys, men med adgangskode-login i behold. AVAILABLE styrer både endpoints
# og hvad UI'et viser.
try:
    from webauthn import (generate_authentication_options, generate_registration_options,
                          options_to_json, verify_authentication_response,
                          verify_registration_response)
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                          PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement,
                                          UserVerificationRequirement)
    AVAILABLE = True
except ImportError as _e:  # pragma: no cover
    AVAILABLE = False
    print(f"[passkeys] deaktiveret: {_e} — kør 'pip install -r requirements.txt'", flush=True)

RP_NAME = "Tilmeld"

# Nøgle i Flask-sessionen hvor udfordringen ligger mellem options og verify.
CHALLENGE_KEY = "wa_challenge"


# --------------------------------------------------------------------------- #
# base64url uden padding (samme format som WebAuthn-JSON bruger)
# --------------------------------------------------------------------------- #
def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


# --------------------------------------------------------------------------- #
# Domæne pr. request
# --------------------------------------------------------------------------- #
def rp_from_request(request) -> tuple:
    """(rp_id, origin) udledt af requesten.

    Bag en reverse proxy er request.host containerens adresse — den rigtige står i
    X-Forwarded-Host. Headeren kan indeholde en liste ("a, b"); første led gælder.
    """
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").split(",")[0].strip()
    proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "https")
    proto = proto.split(",")[0].strip()
    rp_id = host.split(":")[0]
    return rp_id, f"{proto}://{host}"


def is_secure_origin(request) -> bool:
    """Kan passkeys overhovedet bruges her?

    WebAuthn virker kun i et sikkert kontekst: https, eller localhost over http.
    Mangler pakken, er svaret nej uanset adressen.
    """
    return not blocked_reason(request)


def blocked_reason(request) -> str:
    """"" hvis passkeys kan bruges her, ellers en forklaring til brugeren."""
    if not AVAILABLE:
        return ("Passkeys er slået fra på denne installation: Python-pakken »webauthn« "
                "mangler. Kør »pip install -r requirements.txt« og genstart.")
    rp_id, origin = rp_from_request(request)
    if origin.startswith("https://") or rp_id in ("localhost", "127.0.0.1", "::1"):
        return ""
    return ("Passkeys kræver en sikker forbindelse (https) eller localhost. Denne side er "
            "åbnet over almindelig http, så nøgler kan hverken oprettes eller bruges her. "
            "Åbn appen på dens https-domæne i stedet.")


# --------------------------------------------------------------------------- #
# Opslag i databasen
# --------------------------------------------------------------------------- #
def _scope_where(scope: str, group_id=None, user_id=None) -> tuple:
    if scope == "master":
        return "scope = 'master'", ()
    if scope == "admin":
        return "scope = 'admin' AND group_id = ?", (group_id,)
    if scope == "user":
        return "scope = 'user' AND user_id = ?", (user_id,)
    raise ValueError(f"ukendt scope: {scope}")


def list_credentials(conn, scope: str, group_id=None, user_id=None) -> list:
    where, args = _scope_where(scope, group_id, user_id)
    return conn.execute(
        f"SELECT * FROM credentials WHERE {where} ORDER BY created_at", args).fetchall()


def delete_credential(conn, cred_id: int, scope: str, group_id=None, user_id=None) -> bool:
    """Slet én nøgle — men kun inden for det scope, kalderen faktisk er logget ind på."""
    where, args = _scope_where(scope, group_id, user_id)
    cur = conn.execute(f"DELETE FROM credentials WHERE id = ? AND {where}", (cred_id,) + args)
    conn.commit()
    return cur.rowcount > 0


def find_by_credential_id(conn, credential_id: str):
    return conn.execute(
        "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Registrering
# --------------------------------------------------------------------------- #
def registration_options(conn, request, session, scope: str, label: str,
                         group_id=None, user_id=None) -> str:
    """JSON til navigator.credentials.create(). Lægger udfordringen i sessionen."""
    rp_id, _ = rp_from_request(request)
    existing = list_credentials(conn, scope, group_id, user_id)
    handle = {"master": b"master",
              "admin": f"admin:{group_id}".encode(),
              "user": f"user:{user_id}".encode()}[scope]

    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=handle,
        user_name=label,
        user_display_name=label,
        # Nøglen skal kunne findes uden brugernavn, og kræve fingeraftryk/PIN.
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        # Ellers kan man registrere den samme nøgle to gange på samme konto.
        exclude_credentials=[PublicKeyCredentialDescriptor(id=b64d(c["credential_id"]))
                             for c in existing],
    )
    session[CHALLENGE_KEY] = b64e(opts.challenge)
    return options_to_json(opts)


def registration_verify(conn, request, session, credential: dict, name: str,
                        scope: str, group_id=None, user_id=None) -> str:
    """Gem en ny nøgle. Returnerer "" ved succes, ellers en fejltekst."""
    challenge = session.pop(CHALLENGE_KEY, None)
    if not challenge:
        return "Registreringen udløb — prøv igen."
    rp_id, origin = rp_from_request(request)
    try:
        res = verify_registration_response(
            credential=credential,
            expected_challenge=b64d(challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as e:  # py_webauthn rejser mange forskellige typer
        return f"Nøglen kunne ikke godkendes ({e})."

    cred_id = b64e(res.credential_id)
    if find_by_credential_id(conn, cred_id):
        return "Den nøgle er allerede registreret."
    conn.execute(
        "INSERT INTO credentials (scope, group_id, user_id, credential_id, public_key, "
        "sign_count, name, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (scope, group_id, user_id, cred_id, b64e(res.credential_public_key),
         res.sign_count, (name or "").strip()[:60] or "Passkey",
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return ""


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def authentication_options(conn, request, session) -> str:
    """JSON til navigator.credentials.get().

    allow_credentials er bevidst tom: nøglerne er discoverable, så browseren viser
    selv de relevante. Det betyder også, at siden ikke afslører, hvilke nøgler der
    findes, før man har bevist, at man har en.
    """
    rp_id, _ = rp_from_request(request)
    opts = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session[CHALLENGE_KEY] = b64e(opts.challenge)
    return options_to_json(opts)


def authentication_verify(conn, request, session, credential: dict,
                          scope: str, group_id=None):
    """Godkend et login-svar.

    Returnerer (row, "") ved succes, ellers (None, fejltekst). `row` er rækken fra
    `credentials`, så kalderen kan sætte den rigtige session-nøgle.
    """
    challenge = session.pop(CHALLENGE_KEY, None)
    if not challenge:
        return None, "Login-forsøget udløb — prøv igen."
    raw_id = credential.get("id") or ""
    row = find_by_credential_id(conn, raw_id)
    if not row:
        return None, "Ukendt passkey."

    # Nøglen skal høre til netop det login, der er i gang. Uden dette tjek ville en
    # gruppe-admins passkey kunne bruges på /master-login-siden.
    if row["scope"] != scope:
        return None, "Den passkey hører til et andet login."
    if scope == "admin" and row["group_id"] != group_id:
        return None, "Den passkey hører til en anden gruppe."

    rp_id, origin = rp_from_request(request)
    try:
        res = verify_authentication_response(
            credential=credential,
            expected_challenge=b64d(challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=b64d(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
        )
    except Exception as e:
        return None, f"Login med passkey mislykkedes ({e})."

    conn.execute("UPDATE credentials SET sign_count = ?, last_used = ? WHERE id = ?",
                 (res.new_sign_count, datetime.now().isoformat(timespec="seconds"), row["id"]))
    conn.commit()
    return row, ""
