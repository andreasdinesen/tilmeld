"""Teksten i en SMS: hvad den må fylde, og hvad den skal skrives med.

Udskilt fra `gigasms.py`, da inMobile kom til som anden udbyder. Reglerne er
**vores**, ikke udbyderens: begge gateways vil gerne sende en besked på fem
dele, men vi vil ikke betale for den. Derfor bor de her, ét sted, så de to
udbydere ikke kan komme til at klippe teksten forskelligt.

En SMS afregnes pr. del. En enkeltstående besked rummer 160 tegn (70 i unicode);
skal den deles, går der plads fra hver del til det, der syr dem sammen igen, og
grænsen falder til 153 (67). Det er dét regnestykke, `LATIN1_MAX` = 3 × 153 og
`UCS2_MAX` = 3 × 67 kommer af.
"""

LATIN1_MAX = 459               # 3 SMS-dele i latin-1
UCS2_MAX = 201                 # 3 SMS-dele i unicode

# Typografiske tegn appen selv bruger i skabeloner og logtekster. De findes ikke
# i latin-1, og ét af dem ville tvinge HELE beskeden ned på 201 tegn — og koste
# det samme i SMS-dele undervejs. Oversættelsen er ren gevinst.
_TYPOGRAFI = {
    "—": "-", "–": "-", "…": "...", " ": " ",
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
    """Hvor mange SMS-dele fylder teksten — altså hvad den kommer til at koste."""
    try:
        text.encode("latin-1")
        alene, delt = 160, 153
    except UnicodeEncodeError:
        alene, delt = 70, 67
    n = len(text or "")
    if not n:
        return 0
    return 1 if n <= alene else -(-n // delt)
