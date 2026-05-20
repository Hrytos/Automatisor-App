from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib import error, request
from urllib.parse import urlparse


PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
VALIDATION_THRESHOLD = 0.6
ADDRESS_VALIDATION_THRESHOLD = 0.6
VISIBLE_CANDIDATE_ADDRESS_THRESHOLD = 0.45
SCORE_WEIGHTS = {
    "company_name": 0.3,
    "address": 0.5,
    "domain": 0.2,
}
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.businessStatus",
        "places.websiteUri",
        "places.googleMapsUri",
    ]
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "of",
    "the",
}


@dataclass(frozen=True)
class ValidationInput:
    company_name: str
    address: str
    domain: str


def normalize_domain(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).hostname or ""
    else:
        value = value.split("/", 1)[0]
    value = value.split("@")[-1].split(":")[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    return re.sub(r"\.+", ".", value)


def comparable_domain(raw: Any) -> str:
    domain = normalize_domain(raw)
    labels = [part for part in domain.split(".") if part]
    if len(labels) <= 2:
        return domain
    return ".".join(labels[-2:])


def tokenize(value: Any) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return {part for part in cleaned.split() if part and part not in STOP_WORDS}


def ordered_tokens(value: Any) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return [part for part in cleaned.split() if part and part not in STOP_WORDS]


def split_compound_name(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    with_camel_spaces = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return re.sub(r"[^A-Za-z0-9]+", " ", with_camel_spaces).strip()


def compact_name(raw: Any) -> str:
    tokens = ordered_tokens(split_compound_name(raw))
    return "".join(tokens)


def acronym_variants(raw: Any) -> set[str]:
    tokens = ordered_tokens(split_compound_name(raw))
    if not tokens:
        return set()
    variants = {"".join(token[0] for token in tokens if token)}
    if len(tokens) > 1 and len(tokens[0]) <= 3:
        variants.add(tokens[0] + "".join(token[0] for token in tokens[1:] if token))
    if len(tokens) == 1 and 2 <= len(tokens[0]) <= 6:
        variants.add(tokens[0])
    return {variant for variant in variants if variant}


def company_name_variants(raw: Any) -> list[str]:
    spaced = split_compound_name(raw)
    compact = compact_name(raw)
    variants = []
    for value in [str(raw or "").strip(), spaced, compact]:
        normalized = re.sub(r"\s+", " ", value).strip()
        if normalized and normalized.lower() not in {item.lower() for item in variants}:
            variants.append(normalized)
    return variants


def name_similarity(a: Any, b: Any) -> float:
    def name_tokens(value: Any) -> set[str]:
        tokens = tokenize(split_compound_name(value))
        expanded = set(tokens)
        for token in tokens:
            if token.isalpha() and 2 <= len(token) <= 4:
                expanded.update(token)
        return expanded

    left_compact = compact_name(a)
    right_compact = compact_name(b)
    left_acronyms = acronym_variants(a)
    right_acronyms = acronym_variants(b)
    if left_compact and right_compact:
        if left_compact == right_compact:
            return 1.0
        if left_compact in right_acronyms or right_compact in left_acronyms:
            return 1.0
        compact_ratio = SequenceMatcher(None, left_compact, right_compact).ratio()
    else:
        compact_ratio = 0.0

    left = name_tokens(a)
    right = name_tokens(b)
    if not left or not right:
        return compact_ratio
    overlap = len(left & right)
    token_ratio = overlap / max(len(left), len(right))
    return max(token_ratio, compact_ratio)


def zip_code(value: Any) -> str:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", str(value or ""))
    return match.group(1) if match else ""


def street_number(value: Any) -> str:
    match = re.search(r"\b(\d{1,6})\b", str(value or ""))
    return match.group(1) if match else ""


def address_similarity(a: Any, b: Any) -> float:
    left = tokenize(a)
    right = tokenize(b)
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    base = overlap / max(len(left), len(right))
    left_zip = zip_code(a)
    right_zip = zip_code(b)
    if left_zip and right_zip and left_zip == right_zip:
        base += 0.15
    left_street_number = street_number(a)
    right_street_number = street_number(b)
    if left_street_number and right_street_number and left_street_number != right_street_number:
        return min(base, 0.35)
    return min(base, 1.0)


def is_address_like_name(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return bool(re.match(r"^\d+\s+[a-z0-9 .'-]+$", normalized))


def domains_match(expected: str, candidate_website: str) -> bool:
    expected_domain = comparable_domain(expected)
    website_domain = comparable_domain(candidate_website)
    return bool(expected_domain and website_domain and expected_domain == website_domain)


def place_name(place: dict[str, Any]) -> str:
    display_name = place.get("displayName") or {}
    if isinstance(display_name, dict):
        return str(display_name.get("text") or "")
    return str(display_name or "")


def compact_place(place: dict[str, Any]) -> dict[str, Any]:
    location = place.get("location") if isinstance(place.get("location"), dict) else {}
    return {
        "place_id": place.get("id") or "",
        "name": place_name(place),
        "address": place.get("formattedAddress") or "",
        "location": {
            "lat": location.get("latitude"),
            "lng": location.get("longitude"),
        }
        if location.get("latitude") is not None and location.get("longitude") is not None
        else None,
        "website": place.get("websiteUri") or "",
        "business_status": place.get("businessStatus") or "",
        "google_maps_uri": place.get("googleMapsUri") or "",
    }


def score_place(site: ValidationInput, place: dict[str, Any]) -> dict[str, Any]:
    compact = compact_place(place)
    name_score = name_similarity(site.company_name, compact["name"])
    address_score = address_similarity(site.address, compact["address"])
    domain_match = domains_match(site.domain, compact["website"])
    domain_score = 1.0 if domain_match else 0.0
    score = (
        (name_score * SCORE_WEIGHTS["company_name"])
        + (address_score * SCORE_WEIGHTS["address"])
        + (domain_score * SCORE_WEIGHTS["domain"])
    )
    score = max(0.0, min(score, 1.0))

    reasons = []
    if name_score >= 0.75:
        reasons.append("company name matches")
    elif name_score >= 0.45:
        reasons.append("company name partially matches")
    else:
        reasons.append("company name does not match closely")

    if address_score >= 0.75:
        reasons.append("address matches")
    elif address_score >= 0.45:
        reasons.append("address partially matches")
    else:
        reasons.append("address does not match closely")

    if compact["website"]:
        reasons.append("domain matches" if domain_match else "domain differs")
    else:
        reasons.append("Google has no website for this place")

    return {
        **compact,
        "score": round(score, 3),
        "name_score": round(name_score, 3),
        "address_score": round(address_score, 3),
        "domain_score": round(domain_score, 3),
        "domain_match": domain_match,
        "score_weights_used": SCORE_WEIGHTS,
        "reasons": reasons,
    }


def status_from_best(best: dict[str, Any] | None) -> tuple[str, str, bool]:
    if not best:
        return "needs_correction", "No Google Places candidate matched the query.", False
    has_identity_signal = best["name_score"] >= 0.45 or best["domain_score"] > 0
    has_address_signal = best["address_score"] >= ADDRESS_VALIDATION_THRESHOLD
    if best["score"] >= VALIDATION_THRESHOLD and has_address_signal and has_identity_signal:
        return "validated", "Company, domain, and address passed validation.", True
    return (
        "needs_correction",
        "Please check whether the company/domain is exactly at this address.",
        False,
    )


def is_visible_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("address_score", 0) < VISIBLE_CANDIDATE_ADDRESS_THRESHOLD:
        return False
    if not candidate.get("website") and is_address_like_name(candidate.get("name")):
        return False
    return True


def unavailable_result(site: ValidationInput, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "can_request_assessment": True,
        "score": 0,
        "threshold": VALIDATION_THRESHOLD,
        "reason": reason,
        "input": site.__dict__,
        "best_match": None,
        "candidates": [],
    }


def search_google_places_for_query(query: str, api_key: str, timeout: int = 20) -> list[dict[str, Any]]:
    payload = {
        "textQuery": query,
        "maxResultCount": 5,
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        PLACES_TEXT_SEARCH_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    places = data.get("places") or []
    return places if isinstance(places, list) else []


def search_google_places(site: ValidationInput, api_key: str, timeout: int = 20) -> list[dict[str, Any]]:
    places_by_id: dict[str, dict[str, Any]] = {}
    for variant in company_name_variants(site.company_name):
        query = f"{variant} {site.address}"
        for place in search_google_places_for_query(query, api_key, timeout):
            place_id = place.get("id") or json.dumps(place, sort_keys=True)
            places_by_id[place_id] = place
    return list(places_by_id.values())


def validate_company_site(
    company_name: str,
    address: str,
    domain: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    site = ValidationInput(
        company_name=str(company_name or "").strip(),
        address=str(address or "").strip(),
        domain=normalize_domain(domain),
    )
    if not site.company_name or not site.address or not site.domain:
        return {
            "status": "unavailable",
            "can_request_assessment": False,
            "score": 0,
            "threshold": VALIDATION_THRESHOLD,
            "reason": "company_name, address, and domain are required.",
            "input": site.__dict__,
            "best_match": None,
            "candidates": [],
        }

    resolved_api_key = os.getenv("GOOGLE_MAPS_API_KEY", "") if api_key is None else api_key
    if not resolved_api_key:
        return unavailable_result(site, "Missing GOOGLE_MAPS_API_KEY.")

    try:
        places = search_google_places(site, resolved_api_key)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return unavailable_result(site, f"Google Places HTTP {exc.code}: {detail}")
    except Exception as exc:
        return unavailable_result(site, f"Google Places request failed: {exc}")

    candidates = sorted(
        (score_place(site, place) for place in places),
        key=lambda item: item["score"],
        reverse=True,
    )
    best = candidates[0] if candidates else None
    status, reason, can_request_assessment = status_from_best(best)
    visible_candidates = [candidate for candidate in candidates if is_visible_candidate(candidate)]
    return {
        "status": status,
        "can_request_assessment": can_request_assessment,
        "score": best["score"] if best else 0,
        "threshold": VALIDATION_THRESHOLD,
        "reason": reason,
        "input": site.__dict__,
        "best_match": best,
        "candidates": visible_candidates,
    }
