from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True, slots=True)
class CountrySpec:
    code: str
    name: str


UNKNOWN_COUNTRY = CountrySpec("ZZ", "Unknown")


COUNTRIES: dict[str, CountrySpec] = {
    "ARG": CountrySpec("ARG", "Argentina"),
    "AUT": CountrySpec("AUT", "Austria"),
    "BEL": CountrySpec("BEL", "Belgium"),
    "BRA": CountrySpec("BRA", "Brazil"),
    "CHE": CountrySpec("CHE", "Switzerland"),
    "CHN": CountrySpec("CHN", "China"),
    "DEU": CountrySpec("DEU", "Germany"),
    "DNK": CountrySpec("DNK", "Denmark"),
    "ENG": CountrySpec("ENG", "England"),
    "ESP": CountrySpec("ESP", "Spain"),
    "FIN": CountrySpec("FIN", "Finland"),
    "FRA": CountrySpec("FRA", "France"),
    "GRC": CountrySpec("GRC", "Greece"),
    "IRL": CountrySpec("IRL", "Ireland"),
    "ITA": CountrySpec("ITA", "Italy"),
    "INT": CountrySpec("INT", "International"),
    "JPN": CountrySpec("JPN", "Japan"),
    "MEX": CountrySpec("MEX", "Mexico"),
    "NLD": CountrySpec("NLD", "Netherlands"),
    "NOR": CountrySpec("NOR", "Norway"),
    "POL": CountrySpec("POL", "Poland"),
    "PRT": CountrySpec("PRT", "Portugal"),
    "ROU": CountrySpec("ROU", "Romania"),
    "RUS": CountrySpec("RUS", "Russia"),
    "SCO": CountrySpec("SCO", "Scotland"),
    "SWE": CountrySpec("SWE", "Sweden"),
    "TUR": CountrySpec("TUR", "Turkey"),
    "USA": CountrySpec("USA", "United States"),
    "ZZ": UNKNOWN_COUNTRY,
}


COUNTRY_ALIASES: dict[str, str] = {
    "SWZ": "CHE",
    "SWITZERLAND": "CHE",
    "UNITED STATES OF AMERICA": "USA",
    "UNITED STATES": "USA",
    "US": "USA",
    "ENGLAND": "ENG",
    "SCOTLAND": "SCO",
    "GERMANY": "DEU",
    "SPAIN": "ESP",
    "ITALY": "ITA",
    "FRANCE": "FRA",
    "BELGIUM": "BEL",
    "NETHERLANDS": "NLD",
    "PORTUGAL": "PRT",
    "TURKEY": "TUR",
    "GREECE": "GRC",
    "INTERNATIONAL": "INT",
    "WORLD": "INT",
}


# football-data.co.uk division identifiers used by the season workbooks.
DIVISIONS: dict[str, tuple[CountrySpec, str]] = {
    "E0": (COUNTRIES["ENG"], "Premier League"),
    "E1": (COUNTRIES["ENG"], "Championship"),
    "E2": (COUNTRIES["ENG"], "League One"),
    "E3": (COUNTRIES["ENG"], "League Two"),
    "EC": (COUNTRIES["ENG"], "National League"),
    "SC0": (COUNTRIES["SCO"], "Premiership"),
    "SC1": (COUNTRIES["SCO"], "Championship"),
    "SC2": (COUNTRIES["SCO"], "League One"),
    "SC3": (COUNTRIES["SCO"], "League Two"),
    "D1": (COUNTRIES["DEU"], "Bundesliga"),
    "D2": (COUNTRIES["DEU"], "2. Bundesliga"),
    "SP1": (COUNTRIES["ESP"], "La Liga"),
    "SP2": (COUNTRIES["ESP"], "Segunda Division"),
    "I1": (COUNTRIES["ITA"], "Serie A"),
    "I2": (COUNTRIES["ITA"], "Serie B"),
    "F1": (COUNTRIES["FRA"], "Ligue 1"),
    "F2": (COUNTRIES["FRA"], "Ligue 2"),
    "B1": (COUNTRIES["BEL"], "First Division A"),
    "N1": (COUNTRIES["NLD"], "Eredivisie"),
    "P1": (COUNTRIES["PRT"], "Primeira Liga"),
    "T1": (COUNTRIES["TUR"], "Super Lig"),
    "G1": (COUNTRIES["GRC"], "Super League Greece"),
}


COMPETITION_COUNTRIES: dict[str, CountrySpec] = {
    "premier-league": COUNTRIES["ENG"],
    "championship": COUNTRIES["ENG"],
    "league-one": COUNTRIES["ENG"],
    "league-two": COUNTRIES["ENG"],
    "national-league": COUNTRIES["ENG"],
    "premiership": COUNTRIES["SCO"],
    "bundesliga": COUNTRIES["DEU"],
    "2-bundesliga": COUNTRIES["DEU"],
    "la-liga": COUNTRIES["ESP"],
    "segunda-division": COUNTRIES["ESP"],
    "serie-a": COUNTRIES["ITA"],
    "serie-b": COUNTRIES["ITA"],
    "ligue-1": COUNTRIES["FRA"],
    "ligue-2": COUNTRIES["FRA"],
    "first-division-a": COUNTRIES["BEL"],
    "eredivisie": COUNTRIES["NLD"],
    "primeira-liga": COUNTRIES["PRT"],
    "super-lig": COUNTRIES["TUR"],
    "super-league-greece": COUNTRIES["GRC"],
    "mls": COUNTRIES["USA"],
}


def slugify(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = text.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "unknown"


def resolve_country(value: object) -> CountrySpec:
    raw = str(value or "").strip()
    if not raw:
        return UNKNOWN_COUNTRY
    upper = raw.upper()
    code = COUNTRY_ALIASES.get(upper, upper)
    if code in COUNTRIES:
        return COUNTRIES[code]
    # Keep unfamiliar sports-provider codes deterministic without pretending
    # they are ISO codes. The source value is still available to the importer.
    compact = re.sub(r"[^A-Z0-9]", "", upper)[:8] or "ZZ"
    return CountrySpec(compact, raw)


def infer_competition_country(competition: object) -> CountrySpec:
    return COMPETITION_COUNTRIES.get(slugify(competition), UNKNOWN_COUNTRY)
