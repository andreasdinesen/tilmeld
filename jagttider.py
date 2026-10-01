"""Jagttider for et sted i Danmark.

Kilden er jagttidsbekendtgørelsen — BEK nr. 470 af 17/05/2024, gældende fra
1. juli 2024. Bilag 1 er de generelle jagttider; bilag 2-4 er de lokale
fravigelser (»Andet«, kronvildt og dåvildt). ALT, der står i bekendtgørelsen,
bor i dette modul — kommer der en ny (revisionen, der var planlagt til
1. juli 2026), er det her og kun her, der skal rettes. Opdatér KILDE med.

Stedet afgøres i tre lag:
  1. Region og kommune — slås op automatisk fra adressen (OpenStreetMap).
     Bilag 2's regler for regioner og hele kommuner følger heraf.
  2. Øen — bilag 2 har egne tider for en række småøer. Foreslås ud fra
     postnummer og OSM's »island«, men admin bekræfter.
  3. Kron- og dåvildt-områderne i bilag 3 og 4 er afgrænset af VEJE, ikke af
     kommunegrænser. Kommunen giver derfor kun en liste af kandidater; ligger
     kommunen helt inde i ét område, vælges det, ellers må admin vælge.

DAWA (Danmarks Adressers Web API) lukkede 1. juli 2026 og svarer 410 Gone.
Opslaget går derfor til Nominatim (OpenStreetMap). Brugsreglerne dér: et
kendetegn i User-Agent og højst et opslag i sekundet — vi slår kun op, når
admin gemmer en adresse.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

KILDE = {
    "navn": "BEK nr. 470 af 17/05/2024 (jagttidsbekendtgørelsen)",
    "gyldig_fra": "1. juli 2024",
    "url": "https://www.retsinformation.dk/eli/lta/2024/470",
}

MAANEDER = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
            "august", "september", "oktober", "november", "december"]

# --------------------------------------------------------------------------- #
# Kommuner og regioner
# --------------------------------------------------------------------------- #
REGIONER = {
    "Hovedstaden": ["Albertslund", "Allerød", "Ballerup", "Bornholm", "Brøndby",
                    "Dragør", "Egedal", "Fredensborg", "Frederiksberg", "Frederikssund",
                    "Furesø", "Gentofte", "Gladsaxe", "Glostrup", "Gribskov", "Halsnæs",
                    "Helsingør", "Herlev", "Hillerød", "Hvidovre", "Høje-Taastrup",
                    "Hørsholm", "Ishøj", "København", "Lyngby-Taarbæk", "Rudersdal",
                    "Rødovre", "Tårnby", "Vallensbæk"],
    "Sjælland": ["Faxe", "Greve", "Guldborgsund", "Holbæk", "Kalundborg", "Køge", "Lejre",
                 "Lolland", "Næstved", "Odsherred", "Ringsted", "Roskilde", "Slagelse",
                 "Solrød", "Sorø", "Stevns", "Vordingborg"],
    "Syddanmark": ["Aabenraa", "Assens", "Billund", "Esbjerg", "Faaborg-Midtfyn", "Fanø",
                   "Fredericia", "Haderslev", "Kerteminde", "Kolding", "Langeland",
                   "Middelfart", "Nordfyn", "Nyborg", "Odense", "Svendborg", "Sønderborg",
                   "Tønder", "Varde", "Vejen", "Vejle", "Ærø"],
    "Midtjylland": ["Favrskov", "Hedensted", "Herning", "Holstebro", "Horsens",
                    "Ikast-Brande", "Lemvig", "Norddjurs", "Odder", "Randers",
                    "Ringkøbing-Skjern", "Samsø", "Silkeborg", "Skanderborg", "Skive",
                    "Struer", "Syddjurs", "Viborg", "Aarhus"],
    "Nordjylland": ["Brønderslev", "Frederikshavn", "Hjørring", "Jammerbugt", "Læsø",
                    "Mariagerfjord", "Morsø", "Rebild", "Thisted", "Vesthimmerland",
                    "Aalborg"],
}
KOMMUNE_REGION = {k: r for r, ks in REGIONER.items() for k in ks}
KOMMUNER = sorted(KOMMUNE_REGION, key=lambda k: k.replace("Aa", "Å"))

# Syddanmark vest for Lillebælt = den jyske del af regionen.
SYD_VEST_FOR_LILLEBAELT = {"Aabenraa", "Billund", "Esbjerg", "Fanø", "Fredericia",
                           "Haderslev", "Kolding", "Sønderborg", "Tønder", "Varde",
                           "Vejen", "Vejle"}


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+(regions)?kommune$", "", s)
    return s


def find_kommune(navn: str) -> str:
    """OSM skriver »Vesthimmerlands Kommune«, »Københavns Kommune« og
    »Bornholms Regionskommune« — genitiv-s'et skal væk, før vi kan slå op."""
    n = _norm(navn)
    for k in KOMMUNER:
        kn = k.lower()
        if n in (kn, kn + "s") or (kn == "københavn" and n == "københavns"):
            return k
    return ""


# --------------------------------------------------------------------------- #
# Bilag 1 — generelle jagttider
#   (nøgle, navn, perioder, note)
#   perioder: liste af (start, slut) som "DD.MM"; [] = ingen jagttid.
# --------------------------------------------------------------------------- #
P = lambda *ps: [tuple(p.split("-")) for p in ps]  # noqa: E731

GENERELLE = [
    ("Hovdyr", [
        ("kronhjort", "Kronhjort større end spidshjort", P("16.10-31.12"), ""),
        ("kronspidshjort", "Kronspidshjort", P("01.09-31.01"), ""),
        ("kronhind", "Kronhind", P("01.10-31.01"), ""),
        ("kronkalv", "Kronkalv", P("01.09-29.02"), ""),
        ("daahjort", "Dåhjort", P("01.09-31.01"), ""),
        ("daa", "Då og -kalv", P("01.10-31.01"), ""),
        ("sikahjort", "Sikahjort", P("01.09-31.01"), ""),
        ("sikahind", "Sikahind og -kalv", P("01.10-31.01"), ""),
        ("raabuk", "Råbuk", P("16.05-15.07", "01.10-31.01"), ""),
        ("raa", "Rå og -lam", P("01.10-31.01"), ""),
        ("muflonvaedder", "Muflonvædder", P("01.09-31.01"), ""),
        ("muflonfaar", "Muflonfår og -lam", P("01.10-31.01"), ""),
        ("vildsvinorne", "Vildsvin, orne", P("01.09-31.01"), ""),
        ("vildsvinso", "Vildsvin, so og grise", P("01.10-31.01"), ""),
    ]),
    ("Rovdyr", [
        ("raev", "Ræv", P("01.09-31.01"), ""),
    ]),
    ("Hare og kanin", [
        ("hare", "Hare", P("01.11-31.01"), ""),
        ("kanin", "Vildkanin", P("01.11-31.01"), ""),
    ]),
    ("Andefugle", [
        ("graaand", "Gråand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("atlingand", "Atlingand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("krikand", "Krikand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("spidsand", "Spidsand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("pibeand", "Pibeand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("skeand", "Skeand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("knarand", "Knarand", P("01.09-31.12"), "På fiskeriterritoriet desuden 01.01-31.01."),
        ("graagaas", "Grågås", P("01.09-31.01"),
         "Desuden 01.08-31.08 på omdriftsarealer — dog ikke nærmere end 300 m fra "
         "kyst og søer over 3 ha."),
        ("blisgaas", "Blisgås", P("01.09-31.01"), ""),
        ("kortnaeb", "Kortnæbbet gås", P("01.09-31.01"), ""),
        ("canadagaas", "Canadagås", P("01.09-31.01"),
         "Desuden 01.08-31.08 på omdriftsarealer — dog ikke nærmere end 300 m fra "
         "kyst og søer over 3 ha."),
        ("saedgaas", "Sædgås", [], ""),
        ("troldand", "Troldand", P("01.10-31.01"), ""),
        ("bjergand", "Bjergand", P("01.10-31.01"), ""),
        ("hvinand", "Hvinand", P("01.10-31.01"), ""),
        ("ederfugl", "Ederfugl, han", P("01.10-31.01"),
         "Ingen jagttid i fuglebeskyttelsesområder, hvor ederfugl er på "
         "udpegningsgrundlaget (F2, F15, F31, F36, F47, F57, F64, F71-73, F94, F96, "
         "F98, F102, F110, F127 og F128)."),
        ("sortand", "Sortand", P("01.10-31.01"), ""),
    ]),
    ("Hønsefugle", [
        ("agerhoene", "Agerhøne", P("16.09-31.10"), ""),
        ("fasan", "Fasan", P("01.10-31.01"), ""),
    ]),
    ("Øvrige fugle", [
        ("blishoene", "Blishøne", P("01.10-31.01"), ""),
        ("bekkasin", "Dobbeltbekkasin", P("01.09-31.12"), ""),
        ("skovsneppe", "Skovsneppe", P("01.10-31.01"), ""),
        ("soelvmaage", "Sølvmåge", P("01.09-31.01"), ""),
        ("ringdue", "Ringdue", P("11.11-31.01"), ""),
        ("husskade", "Husskade", P("01.09-31.01"), ""),
        ("krage", "Krage (grå- og sortkrage)", P("01.09-31.01"), ""),
    ]),
    ("Invasive arter", [
        ("nilgaas", "Nilgås", P("01.09-31.01"), ""),
        ("bisamrotte", "Bisamrotte", P("01.09-31.01"), ""),
        ("sumpbaever", "Sumpbæver", P("01.09-31.01"), ""),
        ("vaskebjoern", "Vaskebjørn", P("01.09-31.01"), ""),
        ("maarhund", "Mårhund", P("01.09-31.01"), ""),
        ("mink", "Mink", P("01.09-31.01"), ""),
    ]),
]

KRON = ("kronhjort", "kronspidshjort", "kronhind", "kronkalv")
RAA = ("raabuk", "raa")


def R(navn, perioder, erstatter, note="", tekst=""):
    """Én lokal regel. `erstatter` er de generelle nøgler, rækken træder i stedet
    for. `tekst` bruges, når perioden ikke er datoer (»1. og 2. lørdag i nov.«)
    — så kan siden ikke sige, om der er åbent i dag, og siger det heller ikke."""
    if isinstance(erstatter, str):
        erstatter = (erstatter,)
    return {"navn": navn, "perioder": P(*perioder) if perioder else [],
            "erstatter": tuple(erstatter), "note": note, "tekst": tekst}


INGEN = ()

# --------------------------------------------------------------------------- #
# Bilag 2 — lokale jagttider, »Andet«
# --------------------------------------------------------------------------- #
# Regler for hele regioner. Gælder automatisk.
REGION_REGLER = [
    ("Syddanmark", "Region Syddanmark (undtagen Mandø)",
     [R("Agerhøne", ["16.09-15.10"], "agerhoene")]),
    ("Midtjylland", "Region Midtjylland (undtagen Endelave)",
     [R("Hare", ["01.11-15.01"], "hare"), R("Agerhøne", ["16.09-15.10"], "agerhoene")]),
    ("Nordjylland", "Region Nordjylland",
     [R("Hare", ["01.11-15.01"], "hare"), R("Agerhøne", ["16.09-15.10"], "agerhoene")]),
]

# Regler for navngivne kommuner. Gælder automatisk. Rækkefølgen tæller: en senere
# regel for samme art vinder (Bornholm har både sølvmåge og ræv/agerhøne).
KOMMUNE_REGLER = [
    ({"Dragør", "Tårnby", "København", "Hvidovre", "Vallensbæk", "Brøndby", "Ishøj",
      "Bornholm", "Greve", "Solrød", "Køge", "Ringsted", "Sorø", "Slagelse", "Næstved",
      "Faxe", "Stevns", "Vordingborg", "Guldborgsund", "Lolland"},
     "Sølvmåge i Hovedstaden og på Sjælland",
     [R("Sølvmåge", ["01.11-31.01"], "soelvmaage")]),
    ({"Vordingborg", "Guldborgsund", "Lolland"}, "Vordingborg, Guldborgsund og Lolland",
     [R("Sædgås", ["01.09-30.11"], "saedgaas")]),
    ({"Bornholm"}, "Bornholms Kommune",
     [R("Ræv", [], "raev"), R("Agerhøne", ["01.10-31.10"], "agerhoene")]),
    (SYD_VEST_FOR_LILLEBAELT, "Syddanmark vest for Lillebælt (undtagen Als og Kegnæs)",
     [R("Hare", ["01.11-15.01"], "hare")]),
    ({"Ærø"}, "Øen Ærø",
     [R("Råbuk", ["16.06-15.07", "01.11-30.11"], "raabuk"),
      R("Rå og -lam", ["01.11-30.11"], "raa")]),
]

# Småøer. Vælges i opsætningen (foreslås ud fra postnummer). Lægges oven på
# region og kommune og vinder over dem — »undtagen Mandø« osv.
OER = {
    "sejeroe": {"navn": "Sejerø", "kommune": "Kalundborg", "postnr": {"4592"}, "regler": [
        R("Råbuk", ["16.05-15.06", "01.12-31.01"], "raabuk"),
        R("Rå og -lam", ["01.12-31.01"], "raa"),
        R("Hare", ["01.11-15.12"], "hare"),
        R("Agerhøne", ["01.10-15.10"], "agerhoene"),
        R("Fasanhøne", ["16.11-30.11"], "fasan"),
        R("Fasanhane", ["01.11-31.01"], "fasan")]},
    "fejoe": {"navn": "Fejø", "kommune": "Lolland", "postnr": {"4944"}, "regler": [
        R("Hare", ["16.11-15.01"], "hare"),
        R("Fasanhøne", [], "fasan"),
        R("Fasanhane", ["16.10-30.11"], "fasan")]},
    "femoe": {"navn": "Femø", "kommune": "Lolland", "postnr": {"4945"}, "regler": [
        R("Hare", ["01.11-15.12"], "hare"),
        R("Agerhøne", [], "agerhoene"),
        R("Fasanhøne", ["01.11-02.11"], "fasan"),
        R("Fasanhane", ["16.10-31.12"], "fasan")]},
    "lyoe": {"navn": "Lyø", "kommune": "Faaborg-Midtfyn", "postnr": set(), "regler": [
        R("Råvildt", ["01.10-15.10"], RAA)]},
    "strynoe": {"navn": "Strynø", "kommune": "Langeland", "postnr": {"5943"}, "regler": [
        R("Hare", [], "hare"),
        R("Fasanhøne", None, "fasan", tekst="1. og 2. lørdag i november"),
        R("Fasanhane", None, "fasan",
          tekst="1. og 2. lørdag i oktober og november samt alle lørdage i december")]},
    "als": {"navn": "Als", "kommune": "Sønderborg", "postnr": {"6430", "6440", "6470"},
            "regler": [
        R("Råbuk", ["16.05-15.07", "01.11-31.12"], "raabuk"),
        R("Rå og -lam", ["01.11-31.12"], "raa"),
        R("Hare", ["01.11-15.12"], "hare"),
        R("Fasan", ["01.11-31.12"], "fasan"),
        R("Skovsneppe", ["01.11-31.12"], "skovsneppe")]},
    "kegnaes": {"navn": "Kegnæs", "kommune": "Sønderborg", "postnr": set(), "regler": [
        R("Råvildt", ["01.12-07.12"], RAA),
        R("Hare", ["01.11-15.12"], "hare"),
        R("Fasan", ["01.11-31.12"], "fasan"),
        R("Skovsneppe", ["01.11-31.12"], "skovsneppe")]},
    "mandoe": {"navn": "Mandø", "kommune": "Esbjerg", "postnr": set(), "regler": [
        R("Råvildt", [], RAA),
        R("Agerhøne", [], "agerhoene")]},
    "endelave": {"navn": "Endelave", "kommune": "Horsens", "postnr": {"8789"}, "regler": [
        R("Råvildt", ["01.10-08.10"], RAA),
        R("Hare", [], "hare"),
        R("Agerhøne", [], "agerhoene")]},
}

# --------------------------------------------------------------------------- #
# Bilag 3 og 4 — kron- og dåvildt
#   hele:   kommuner, der ligger helt i området (vælges automatisk)
#   delvis: kommuner, hvor en vej deler — admin må vælge
# --------------------------------------------------------------------------- #
_HOV_UDEN_BORNHOLM = set(REGIONER["Hovedstaden"]) - {"Bornholm"}

KRON_OMRAADER = {
    "k_hovedstaden": {
        "navn": "Hovedstaden og Nordsjælland (bilag 5)",
        "beskrivelse": "Region Hovedstaden uden Bornholm, og den del af Region Sjælland "
                       "mellem Roskilde og Hovedstaden, der ligger nord for motorvej 21.",
        "hele": _HOV_UDEN_BORNHOLM, "delvis": {"Roskilde"},
        "regler": [
            R("Kronhjort med mindst 6 sprosser (min. 2 cm) på den ene stang",
              ["16.10-30.11"], "kronhjort"),
            R("Øvrige kronhjorte (ikke spidshjort)", [], "kronhjort")]},
    "k_sjaelland": {
        "navn": "Sjælland (bilag 6)",
        "beskrivelse": "Region Sjælland bortset fra Møn, Lolland, Falster og området "
                       "mellem Roskilde og Hovedstaden nord for motorvej 21.",
        "hele": {"Faxe", "Greve", "Holbæk", "Kalundborg", "Køge", "Lejre", "Næstved",
                 "Odsherred", "Ringsted", "Slagelse", "Solrød", "Sorø", "Stevns"},
        "delvis": {"Roskilde", "Vordingborg"},
        "regler": [
            R("Kronhjort større end spidshjort", ["01.10-15.11"], "kronhjort"),
            R("Kronspidshjort og kronkalv", ["01.10-31.01"], ("kronspidshjort", "kronkalv"))]},
    "k_sydvestjylland": {
        "navn": "Sydvestjylland nord for E20 (bilag 7)",
        "beskrivelse": "Nord for E20, syd for rute 28 (Bredsten-Vejle), syd/vest for rute "
                       "176 (Bredsten-Give), motorvej 18 (Give-Herning), rute 15 (Herning-"
                       "Videbæk), rute 467 (Videbæk-Skjern) og Skjern Å.",
        "hele": {"Billund", "Varde"},
        "delvis": {"Esbjerg", "Vejen", "Kolding", "Fredericia", "Vejle", "Ikast-Brande",
                   "Herning", "Ringkøbing-Skjern"},
        "regler": [
            R("Kronhjort større end spidshjort", ["01.09-15.09", "16.10-15.12"],
              "kronhjort")]},
    "k_soenderjylland": {
        "navn": "Syd for E20 samt Als, Rømø og Mandø (bilag 8)",
        "beskrivelse": "Jylland syd for motorvej E20 og øerne Als, Rømø og Mandø.",
        "hele": {"Tønder", "Aabenraa", "Sønderborg", "Haderslev"},
        "delvis": {"Esbjerg", "Vejen", "Kolding", "Fredericia"},
        "regler": [
            R("Kronhjort større end spidshjort", ["01.09-15.09", "16.10-31.12"],
              "kronhjort"),
            R("Kronkalv", ["01.09-31.01"], "kronkalv")]},
    "k_fanoe": {
        "navn": "Fanø",
        "beskrivelse": "Øen Fanø.",
        "hele": {"Fanø"}, "delvis": set(),
        "regler": [
            R("Kronvildt", None, KRON,
              tekst="2. hele weekend i december og 2. hele weekend i januar")]},
    "k_midtjylland": {
        "navn": "Midt- og Østjylland (bilag 9)",
        "beskrivelse": "Syd for rute 26 (Aarhus-E45), vest for E45, syd for rute 16 "
                       "(Randers-Viborg), øst for rute 26/13, syd og øst for rute 12, "
                       "nord og øst for motorvej 18 og rute 176, nord for rute 28 og "
                       "Vejle Å.",
        "hele": {"Skanderborg", "Odder", "Horsens", "Hedensted", "Silkeborg"},
        "delvis": {"Aarhus", "Vejle", "Ikast-Brande", "Herning", "Viborg", "Favrskov",
                   "Randers"},
        "regler": [
            R("Kronkalv", ["01.09-29.02"], "kronkalv",
              "Fra 1. september til og med januar fra 1 time før solopgang til solnedgang."),
            R("Kronspidshjort", ["01.09-31.01"], "kronspidshjort"),
            R("Kronhind", ["01.10-31.01"], "kronhind",
              "Fra 16. oktober til og med januar fra 1 time før solopgang til solnedgang."),
            R("Kronhjort med mindst 5 sprosser (min. 2 cm) på den ene stang",
              ["16.09-30.09", "16.10-31.12"], "kronhjort"),
            R("Øvrige kronhjorte", [], "kronhjort")]},
    "k_djursland": {
        "navn": "Djursland (bilag 10)",
        "beskrivelse": "Syddjurs og Norddjurs samt de dele af Randers, Favrskov og Aarhus "
                       "syd for Gudenåen og rute 180, øst for E45 og nord for rute 26.",
        "hele": {"Syddjurs", "Norddjurs"}, "delvis": {"Randers", "Favrskov", "Aarhus"},
        "regler": [
            R("Kronkalv", ["01.09-29.02"], "kronkalv",
              "I september fra 1 time før solopgang til 1 time efter solnedgang; i december "
              "og januar fra ½ time før til ½ time efter."),
            R("Kronspidshjort", ["01.09-31.01"], "kronspidshjort",
              "I september fra 1 time før solopgang til 1 time efter solnedgang; i december "
              "og januar fra ½ time før til ½ time efter."),
            R("Kronhind", ["01.10-31.01"], "kronhind",
              "I december og januar fra ½ time før solopgang til ½ time efter solnedgang."),
            R("Kronhjort større end spidshjort", ["01.10-30.11"], "kronhjort")]},
    "k_vestjylland": {
        "navn": "Vestjylland og Skive (bilag 11)",
        "beskrivelse": "Lemvig, Struer, Holstebro og Skive samt dele af Viborg, Herning og "
                       "Ringkøbing-Skjern (nord for rute 15/467 og Skjern Å).",
        "hele": {"Lemvig", "Struer", "Holstebro", "Skive"},
        "delvis": {"Viborg", "Herning", "Ringkøbing-Skjern"},
        "regler": [
            R("Kronkalv", ["01.09-29.02"], "kronkalv",
              "Fra 16. december til og med januar fra ½ time før solopgang til solnedgang."),
            R("Kronspidshjort", ["01.09-15.09", "16.10-31.01"], "kronspidshjort"),
            R("Kronhjort større end spidshjort", ["01.09-15.09", "16.10-15.12"],
              "kronhjort"),
            R("Kronhind", ["01.10-31.01"], "kronhind",
              "Fra 16. december til og med januar fra ½ time før solopgang til solnedgang.")]},
    "k_vendsyssel_oest": {
        "navn": "Nord for Limfjorden, øst for E39 (bilag 12)",
        "beskrivelse": "Nord for Limfjorden og øst for motorvej E39 (Aalborg-Hirtshals).",
        "hele": {"Frederikshavn"}, "delvis": {"Hjørring", "Brønderslev", "Aalborg", "Læsø"},
        "regler": [
            R("Kronspidshjort og kronhjort med mindst 5 sprosser (min. 2 cm)",
              ["01.11-31.12"], ("kronspidshjort", "kronhjort")),
            R("Øvrige kronhjorte", [], "kronhjort"),
            R("Kronhind", ["01.11-31.01"], "kronhind"),
            R("Kronkalv", ["01.09-31.01"], "kronkalv")]},
    "k_thy_mors": {
        "navn": "Nord for Limfjorden, vest for E39, inkl. Mors (bilag 13)",
        "beskrivelse": "Nord for Limfjorden og vest for motorvej E39 (Aalborg-Hirtshals), "
                       "inklusive Mors.",
        "hele": {"Morsø", "Thisted", "Jammerbugt"},
        "delvis": {"Hjørring", "Brønderslev", "Aalborg"},
        "regler": [
            R("Kronhjort større end spidshjort med mindst 5 sprosser (min. 2 cm)",
              ["16.10-31.12"], "kronhjort"),
            R("Øvrige kronhjorte", [], "kronhjort"),
            R("Kronspidshjort", ["16.10-31.01"], "kronspidshjort"),
            R("Kronkalv", ["01.09-31.01"], "kronkalv")]},
    "k_himmerland": {
        "navn": "Lille Vildmose-området (bilag 14)",
        "beskrivelse": "De dele af Aalborg, Rebild og Vesthimmerland syd og øst for "
                       "Limfjorden og vest for E45 (Aalborg-Haverslev), nord for rute 535, "
                       "Løgstørvej, Hornumbrovej m.fl. (ikke Livø).",
        "hele": set(), "delvis": {"Aalborg", "Rebild", "Vesthimmerland"},
        "regler": [
            R("Kronhjort større end spidshjort", ["01.12-15.12"], "kronhjort"),
            R("Kronspidshjort", ["01.11-15.12"], "kronspidshjort"),
            R("Kronhind og kronkalv", ["01.11-31.12"], ("kronhind", "kronkalv"))]},
    "k_randers": {
        "navn": "Vest for Randers Fjord, syd for Limfjorden (bilag 15)",
        "beskrivelse": "Vest for Randers Fjord, nord for Gudenåen og rute 180/16, øst for "
                       "rute 533 og Skals Å, syd og øst for Limfjorden — undtagen "
                       "området i bilag 14.",
        "hele": {"Mariagerfjord"},
        "delvis": {"Randers", "Viborg", "Rebild", "Vesthimmerland", "Aalborg", "Favrskov"},
        "regler": [
            R("Kronhjort større end spidshjort", ["16.09-30.09", "16.10-31.12"],
              "kronhjort")]},
}

DAA_OMRAADER = {
    "d_bornholm": {
        "navn": "Bornholm", "beskrivelse": "Bornholms Kommune.",
        "hele": {"Bornholm"}, "delvis": set(),
        "regler": [R("Då og dåhjort", [], ("daahjort", "daa")),
                   R("Dåkalv", ["01.01-31.01"], "daa")]},
    "d_sydvestjylland": {
        "navn": "Sydvestjylland nord for E20 (bilag 16)",
        "beskrivelse": "Fredericia, Varde og Billund samt de dele af Esbjerg, Vejen og "
                       "Kolding nord for E20 og syd for Vejle Å, rute 28, rute 176 og "
                       "motorvej 18 (ikke Fanø).",
        "hele": {"Varde", "Billund"},
        "delvis": {"Fredericia", "Esbjerg", "Vejen", "Kolding", "Vejle"},
        "regler": [R("Dåspidshjort", ["01.12-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.12-15.12"], "daahjort")]},
    "d_toender": {
        "navn": "Tønder og Esbjerg syd for E20 (bilag 17)",
        "beskrivelse": "Tønder Kommune og den del af Esbjerg Kommune syd for E20.",
        "hele": {"Tønder"}, "delvis": {"Esbjerg"},
        "regler": [R("Då og dåhjort", [], ("daahjort", "daa"))]},
    "d_soenderjylland": {
        "navn": "Aabenraa, Sønderborg, Haderslev m.fl. (bilag 18)",
        "beskrivelse": "Aabenraa, Sønderborg og Haderslev samt de dele af Kolding, Vejen og "
                       "Fredericia syd for E20.",
        "hele": {"Aabenraa", "Sønderborg", "Haderslev"},
        "delvis": {"Kolding", "Vejen", "Fredericia"},
        "regler": [R("Dåspidshjort", ["01.11-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.12-31.12"], "daahjort")]},
    "d_fyn": {
        "navn": "Fyn (uden Langeland, Tåsinge og Ærø)",
        "beskrivelse": "Fyn med undtagelse af Langeland, Tåsinge og Ærø.",
        "hele": {"Odense", "Assens", "Faaborg-Midtfyn", "Kerteminde", "Middelfart",
                 "Nordfyn", "Nyborg"},
        "delvis": {"Svendborg"},
        "regler": [R("Dåspidshjort", ["01.10-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.10-31.10", "16.12-31.12"],
                     "daahjort")]},
    "d_taasinge": {
        "navn": "Tåsinge", "beskrivelse": "Øen Tåsinge.",
        "hele": set(), "delvis": {"Svendborg"},
        "regler": [R("Dåhjort", ["01.01-31.01"], "daahjort")]},
    "d_langeland": {
        "navn": "Langeland", "beskrivelse": "Øen Langeland.",
        "hele": {"Langeland"}, "delvis": set(),
        "regler": [R("Dåhjort", ["01.12-31.12"], "daahjort")]},
    "d_aeroe": {
        "navn": "Ærø", "beskrivelse": "Øen Ærø.",
        "hele": {"Ærø"}, "delvis": set(),
        "regler": [R("Dåspidshjort", ["01.01-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", [], "daahjort"),
                   R("Dåkalv", ["01.12-31.01"], "daa"),
                   R("Då", ["01.01-31.01"], "daa")]},
    "d_midtvest": {
        "navn": "Ikast-Brande, Herning, Holstebro, Struer og Lemvig (bilag 19)",
        "beskrivelse": "Ikast-Brande, Herning, Holstebro, Struer og Lemvig Kommuner.",
        "hele": {"Ikast-Brande", "Herning", "Holstebro", "Struer", "Lemvig"},
        "delvis": set(),
        "regler": [R("Dåspidshjort", ["01.12-31.12"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.01-15.01"], "daahjort")]},
    "d_rks": {
        "navn": "Ringkøbing-Skjern (bilag 20)",
        "beskrivelse": "Ringkøbing-Skjern Kommune.",
        "hele": {"Ringkøbing-Skjern"}, "delvis": set(),
        "regler": [R("Dåspidshjort og dåhjort større end spidshjort", ["01.12-31.12"],
                     "daahjort")]},
    "d_vendsyssel_nord": {
        "navn": "Nordligste Vendsyssel, nord for rute 585 (bilag 21)",
        "beskrivelse": "Øst for E39 mellem Hirtshals og rute 585, og nord for rute 585.",
        "hele": set(), "delvis": {"Hjørring", "Frederikshavn"},
        "regler": [R("Då og dåkalv", ["16.10-31.01"], "daa"),
                   R("Dåspidshjort", ["16.11-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.12-15.01"], "daahjort")]},
    "d_oest_e45": {
        "navn": "Nord for Limfjorden, øst for E45 (bilag 22)",
        "beskrivelse": "Nord for Limfjorden og øst for motorvej E45 (Aalborg-Frederikshavn).",
        "hele": set(), "delvis": {"Aalborg", "Frederikshavn", "Brønderslev"},
        "regler": [R("Dåhjort større end spidshjort", [], "daahjort"),
                   R("Dåspidshjort", ["01.12-31.12"], "daahjort"),
                   R("Då og dåkalv", ["01.12-31.01"], "daa")]},
    "d_thy_mors": {
        "navn": "Nord for Limfjorden, vest for E39, inkl. Mors (bilag 23)",
        "beskrivelse": "Nord for Limfjorden og vest for motorvej E39, inklusive Mors.",
        "hele": {"Morsø", "Thisted", "Jammerbugt"},
        "delvis": {"Hjørring", "Brønderslev", "Aalborg"},
        "regler": [R("Då og dåkalv", ["16.10-31.01"], "daa"),
                   R("Dåspidshjort", ["16.11-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["16.12-31.12"], "daahjort")]},
    "d_himmerland": {
        "navn": "Lille Vildmose-området (bilag 24)",
        "beskrivelse": "De dele af Aalborg, Rebild og Vesthimmerland syd og øst for "
                       "Limfjorden og vest for E45 (samme område som kronvildtets bilag 14).",
        "hele": set(), "delvis": {"Aalborg", "Rebild", "Vesthimmerland"},
        "regler": [R("Då og dåkalv", ["01.11-31.12"], "daa"),
                   R("Dåspidshjort", ["01.12-15.12"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["16.12-22.12"], "daahjort")]},
    "d_randers": {
        "navn": "Vest for Randers Fjord, syd for Limfjorden (bilag 25)",
        "beskrivelse": "Vest for Randers Fjord, nord for Gudenåen og rute 180/16, øst for "
                       "rute 533 og Skals Å, syd og øst for Limfjorden — undtagen bilag 24.",
        "hele": {"Mariagerfjord"},
        "delvis": {"Randers", "Viborg", "Rebild", "Vesthimmerland", "Aalborg", "Favrskov"},
        "regler": [R("Dåspidshjort", ["01.10-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["16.09-15.10", "01.12-31.12"],
                     "daahjort")]},
    "d_vendsyssel_midt": {
        "navn": "Mellem E45 og E39, syd for rute 585 (bilag 26)",
        "beskrivelse": "Nord for E45 (Aalborg-Frederikshavn), øst for E39 (Aalborg-"
                       "Hirtshals) og syd for vej 585.",
        "hele": set(), "delvis": {"Brønderslev", "Hjørring", "Aalborg", "Frederikshavn"},
        "regler": [R("Då og dåkalv", ["16.10-31.01"], "daa"),
                   R("Dåspidshjort", ["01.12-15.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["16.12-31.12"], "daahjort")]},
    "d_midtjylland": {
        "navn": "Midt- og Østjylland (bilag 27)",
        "beskrivelse": "Syd for rute 26 (Aarhus-E45), vest for E45, syd for rute 16, øst for "
                       "rute 26/13, syd og øst for rute 12 og Ikast-Brande, øst for motorvej "
                       "18 og rute 176, nord for rute 28 og Vejle Å.",
        "hele": {"Skanderborg", "Odder", "Horsens", "Hedensted", "Silkeborg"},
        "delvis": {"Aarhus", "Vejle", "Viborg", "Favrskov", "Randers"},
        "regler": [R("Dåspidshjort", ["16.10-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["16.10-31.10", "01.12-15.01"],
                     "daahjort")]},
    "d_lolland_falster": {
        "navn": "Lolland, Falster og Møn (bilag 28)",
        "beskrivelse": "Lolland, Guldborgsund samt den del af Vordingborg, der omfatter "
                       "Farø, Bogø, Møn og Nyord.",
        "hele": {"Lolland", "Guldborgsund"}, "delvis": {"Vordingborg"},
        "regler": [R("Dåspidshjort", ["01.10-31.01"], "daahjort"),
                   R("Dåhjort større end spidshjort", ["01.12-31.12"], "daahjort")]},
    "d_laesoe": {
        "navn": "Læsø", "beskrivelse": "Øen Læsø.",
        "hele": {"Læsø"}, "delvis": set(),
        "regler": [R("Då og dåkalv", ["01.12-31.01"], "daa"),
                   R("Dåspidshjort", ["01.12-31.12"], "daahjort"),
                   R("Dåhjort større end spidshjort", [], "daahjort")]},
}

# »-« betyder: valgt, og der gælder ingen lokale tider her. »« betyder: ikke valgt.
INGEN_LOKALE = "-"


# --------------------------------------------------------------------------- #
# Kandidater og forslag
# --------------------------------------------------------------------------- #
def kandidater(omraader: dict, kommune: str) -> list:
    """Områder, kommunen ligger helt eller delvist i — dem, admin kan vælge imellem."""
    ud = []
    for oid, o in omraader.items():
        if kommune in o["hele"] or kommune in o["delvis"]:
            ud.append({"id": oid, "navn": o["navn"], "beskrivelse": o["beskrivelse"],
                       "hele": kommune in o["hele"]})
    return ud


def oe_kandidater(kommune: str) -> list:
    return [{"id": oid, "navn": o["navn"]} for oid, o in OER.items()
            if o["kommune"] == kommune]


def foreslaa(kommune: str, postnr: str = "", oe_navn: str = "") -> dict:
    """Forslaget, der gemmes lige efter et adresseopslag. Kun det sikre vælges;
    resten står tomt (= »vælg«), så siden kan bede admin om at tage stilling."""
    def omr(omraader):
        k = kandidater(omraader, kommune)
        if not k:
            return INGEN_LOKALE
        hele = [x for x in k if x["hele"]]
        if len(hele) == 1 and len(k) == 1:
            return hele[0]["id"]
        return ""

    oe = ""
    oer = oe_kandidater(kommune)
    if not oer:
        oe = INGEN_LOKALE
    else:
        for x in oer:
            o = OER[x["id"]]
            if (oe_navn and oe_navn.lower() == o["navn"].lower()) or postnr in o["postnr"]:
                oe = x["id"]
                break
        else:
            # Småøerne har egne postnumre — fastlandet er det sikre bud. Undtagen
            # Sønderborg, hvor Als og fastlandet (Sundeved) deler 6400.
            oe = "" if kommune == "Sønderborg" else INGEN_LOKALE
    return {"oe": oe, "kron": omr(KRON_OMRAADER), "daa": omr(DAA_OMRAADER)}


# --------------------------------------------------------------------------- #
# Selve tabellen
# --------------------------------------------------------------------------- #
def _anvend(raekker: list, regler: list, kilde: str) -> list:
    """Læg ét lags regler oven på rækkerne. Regler, der deler en art, klumpes —
    »Kronspidshjort og kronhjort ≥5 sprosser« og »Øvrige kronhjorte« skal ind
    på samme plads og fjerne de samme generelle rækker, ellers fjerner den anden
    regel den første."""
    klumper = []
    for r in regler:
        keys = set(r["erstatter"])
        ramt = [k for k in klumper if k["keys"] & keys]
        ny = {"keys": keys, "regler": [r]}
        for k in ramt:
            ny["keys"] |= k["keys"]
            ny["regler"] = k["regler"] + ny["regler"]
            klumper.remove(k)
        klumper.append(ny)
    for k in klumper:
        pos = [i for i, x in enumerate(raekker) if x["keys"] & k["keys"]]
        gruppe = raekker[pos[0]]["gruppe"] if pos else "Øvrige"
        indsaet = pos[0] if pos else len(raekker)
        for i in reversed(pos):
            del raekker[i]
        nye = [{"keys": set(r["erstatter"]), "navn": r["navn"], "perioder": r["perioder"],
                "tekst": r["tekst"], "note": r["note"], "gruppe": gruppe,
                "lokal": kilde} for r in k["regler"]]
        raekker[indsaet:indsaet] = nye
    return raekker


def lag(sted: dict) -> list:
    """De lokale lag, der gælder for stedet, i den rækkefølge de lægges på:
    (kilde-navn, regler)."""
    kommune, region = sted.get("kommune", ""), KOMMUNE_REGION.get(sted.get("kommune", ""))
    oe = sted.get("oe") or ""
    ud = []
    for reg, navn, regler in REGION_REGLER:
        if reg == region:
            ud.append((navn, regler))
    for kommuner, navn, regler in KOMMUNE_REGLER:
        if kommune in kommuner:
            # »Syddanmark vest for Lillebælt undtagen Als og Kegnæs«
            if kommuner is SYD_VEST_FOR_LILLEBAELT and oe in ("als", "kegnaes"):
                continue
            ud.append((navn, regler))
    if oe in OER:
        ud.append((f"Øen {OER[oe]['navn']}", OER[oe]["regler"]))
    for felt, omraader in (("kron", KRON_OMRAADER), ("daa", DAA_OMRAADER)):
        oid = sted.get(felt) or ""
        if oid in omraader:
            ud.append((omraader[oid]["navn"], omraader[oid]["regler"]))
    return ud


def tabel(sted: dict, idag: date | None = None) -> list:
    """Hele oversigten for stedet: [(gruppe, [række, ...]), ...]. Hver række har
    navn, periode-tekst, note, `lokal` (områdets navn eller "") og status."""
    idag = idag or date.today()
    raekker = []
    for gruppe, arter in GENERELLE:
        for key, navn, perioder, note in arter:
            raekker.append({"keys": {key}, "navn": navn, "perioder": perioder, "tekst": "",
                            "note": note, "gruppe": gruppe, "lokal": ""})
    for kilde, regler in lag(sted):
        raekker = _anvend(raekker, regler, kilde)
    grupper = {}
    for r in raekker:
        r.update(status(r, idag))
        r["periode"] = r["tekst"] or periode_tekst(r["perioder"])
        grupper.setdefault(r["gruppe"], []).append(r)
    orden = [g for g, _ in GENERELLE]
    return [(g, grupper[g]) for g in orden if g in grupper]


# --------------------------------------------------------------------------- #
# Datoer
# --------------------------------------------------------------------------- #
def _md(s: str) -> tuple:
    d, m = s.split(".")
    return int(m), int(d)


def _dato(md: tuple, aar: int) -> date:
    m, d = md
    if m == 2 and d == 29:  # »29.02« = februars sidste dag, også i ikke-skudår
        return date(aar, 3, 1) - timedelta(days=1)
    return date(aar, m, d)


def _aaben(perioder, d: date) -> bool:
    md = (d.month, d.day)
    for s, e in perioder:
        s, e = _md(s), _md(e)
        if s <= e:
            if s <= md <= e:
                return True
        elif md >= s or md <= e:  # hen over nytår
            return True
    return False


def _naeste_start(perioder, d: date) -> date | None:
    bedst = None
    for s, _ in perioder:
        for aar in (d.year, d.year + 1):
            k = _dato(_md(s), aar)
            if k > d and (bedst is None or k < bedst):
                bedst = k
    return bedst


def _slut(perioder, d: date) -> date | None:
    """Sidste dag i den periode, d ligger i."""
    md = (d.month, d.day)
    for s, e in perioder:
        s, e = _md(s), _md(e)
        if s <= e and s <= md <= e:
            return _dato(e, d.year)
        if s > e and md >= s:
            return _dato(e, d.year + 1)
        if s > e and md <= e:
            return _dato(e, d.year)
    return None


def status(r: dict, d: date) -> dict:
    if r["tekst"]:
        return {"aaben": None, "status": "se perioden"}
    if not r["perioder"]:
        return {"aaben": False, "status": "fredet"}
    if _aaben(r["perioder"], d):
        slut = _slut(r["perioder"], d)
        return {"aaben": True, "status": f"åben til {kort_dato(slut)}" if slut else "åben"}
    nxt = _naeste_start(r["perioder"], d)
    return {"aaben": False, "status": f"åbner {kort_dato(nxt)}" if nxt else "lukket"}


def kort_dato(d: date) -> str:
    return f"{d.day}. {MAANEDER[d.month - 1][:3]}."


def lang_dato(d: date) -> str:
    return f"{d.day}. {MAANEDER[d.month - 1]} {d.year}"


def periode_tekst(perioder) -> str:
    if not perioder:
        return "Ingen jagttid"
    dele = []
    for s, e in perioder:
        (sm, sd), (em, ed) = _md(s), _md(e)
        slut = "februars udgang" if (em, ed) == (2, 29) else f"{ed}. {MAANEDER[em - 1]}"
        dele.append(f"{sd}. {MAANEDER[sm - 1]} – {slut}")
    return " og ".join(dele)


# --------------------------------------------------------------------------- #
# Sol op og ned (NOAA's forenklede algoritme — et par minutters nøjagtighed)
# --------------------------------------------------------------------------- #
def sol(lat: float, lon: float, d: date) -> tuple | None:
    """(solopgang, solnedgang) som lokale datetime, eller None ved midnatssol."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    # Regnet i UTC og vist i lokal tid. Processen kører i Europe/Copenhagen (TZ i app.py).
    midnat = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    def lokal(minutter):
        return (midnat + timedelta(minutes=minutter)).astimezone().replace(tzinfo=None)
    return lokal(720 - 4 * (lon + ha) - eqtime), lokal(720 - 4 * (lon - ha) - eqtime)


# --------------------------------------------------------------------------- #
# Adresseopslag
# --------------------------------------------------------------------------- #
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def slaa_op(adresse: str, user_agent: str) -> dict:
    """Adresse → {sted, kommune, postnr, oe, lat, lon}. Rejser ValueError med en
    besked, der kan vises direkte, hvis det ikke lykkes."""
    q = urllib.parse.urlencode({"q": adresse, "format": "jsonv2", "addressdetails": 1,
                                "countrycodes": "dk", "limit": 1, "accept-language": "da"})
    req = urllib.request.Request(f"{NOMINATIM}?{q}", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — netværk, DNS, JSON: alt er »prøv igen«
        raise ValueError(f"Adresseopslaget svarede ikke ({e.__class__.__name__}). "
                         "Prøv igen om lidt, eller vælg kommunen i hånden.")
    if not data:
        raise ValueError("Adressen blev ikke fundet. Prøv med vej, husnummer og by — "
                         "eller vælg kommunen i hånden.")
    hit = data[0]
    a = hit.get("address", {})
    kommune = find_kommune(a.get("municipality", "")) or find_kommune(a.get("city", ""))
    if not kommune:
        raise ValueError("Adressen blev fundet, men ikke dens kommune. "
                         "Vælg kommunen i hånden.")
    return {"sted": hit.get("display_name", adresse), "kommune": kommune,
            "postnr": a.get("postcode", ""), "oe": a.get("island", ""),
            "lat": float(hit["lat"]), "lon": float(hit["lon"])}
