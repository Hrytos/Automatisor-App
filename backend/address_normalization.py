import re

SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
ZIP_RE = re.compile(r"(\d{5})")

STATE_ABBREVIATIONS = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "district of columbia": "dc",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}

STREET_SUFFIX_ABBREVIATIONS = {
    "street": "st",
    "st": "st",
    "avenue": "ave",
    "ave": "ave",
    "boulevard": "blvd",
    "blvd": "blvd",
    "road": "rd",
    "rd": "rd",
    "drive": "dr",
    "dr": "dr",
    "lane": "ln",
    "ln": "ln",
    "court": "ct",
    "ct": "ct",
    "circle": "cir",
    "cir": "cir",
    "parkway": "pkwy",
    "pkwy": "pkwy",
    "place": "pl",
    "pl": "pl",
    "terrace": "ter",
    "ter": "ter",
    "highway": "hwy",
    "hwy": "hwy",
    "way": "way",
}


def _clean(value: str) -> str:
    raw = str(value or "").strip().lower()
    raw = PUNCT_RE.sub(" ", raw)
    raw = SPACE_RE.sub(" ", raw).strip()
    return raw


def normalize_state(value: str) -> str:
    cleaned = _clean(value)
    return STATE_ABBREVIATIONS.get(cleaned, cleaned)


def canonical_zip(value: str) -> str:
    match = ZIP_RE.search(str(value or ""))
    if not match:
        return ""
    return match.group(1).zfill(5)


def normalize_full_address(value: str) -> str:
    normalized = _clean(value)
    zip_code = canonical_zip(value)
    return f"{normalized} | {zip_code}" if zip_code else normalized


def normalize_street_line(value: str) -> dict[str, str]:
    cleaned = _clean(value)
    if not cleaned:
        return {"house_number": "", "route": ""}
    parts = cleaned.split(" ")
    house_number = parts[0] if parts and parts[0].isdigit() else ""
    route_parts = parts[1:] if house_number else parts
    normalized_route = [
        STREET_SUFFIX_ABBREVIATIONS.get(part, part)
        for part in route_parts
        if part
    ]
    return {
        "house_number": house_number,
        "route": " ".join(normalized_route).strip(),
    }
