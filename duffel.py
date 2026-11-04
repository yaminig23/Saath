"""Duffel flight adapter (Tier 2 real bookings).

Real flight search and booking through Duffel's API. Test-mode keys
(`duffel_test_...`, free from duffel.com) run the genuine
search → offer → order loop against Duffel Airways sandbox
inventory: the booking_reference is a real order record, but
schedules and prices are invented test data — every response says
so. Live keys spend real money and are refused unless DUFFEL_ENV
is explicitly "live".

Covered by tests/test_duffel.py (mocked HTTP, no network).
"""

import re
from datetime import datetime

import httpx

import kernel
import tools
from config import settings

BASE_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"
HTTP_TIMEOUT = 30.0
MAX_OFFERS = 5

_IATA_RE = re.compile(r"^[A-Za-z]{3}$")
MAX_NAME_LEN = 80
MAX_CONTACT_LEN = 120


class DuffelError(kernel.KernelError):
    """A Duffel request broke a rule or Duffel refused it."""


def _headers() -> dict:
    key = settings.duffel_api_key.strip()
    if not key:
        raise DuffelError("needs DUFFEL_API_KEY in .env (test token from duffel.com → Developers → Access tokens)")
    return {"Authorization": f"Bearer {key}", "Duffel-Version": DUFFEL_VERSION}


def _test_mode() -> bool:
    return settings.duffel_api_key.strip().startswith("duffel_test_")


def _detail(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        text = str(first.get("message") or first.get("title") or "").strip()
        if text:
            return text[:300]
    return ""


def _refuse(response, action: str):
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    detail = _detail(payload) or f"HTTP {response.status_code}"
    if response.status_code == 401:
        raise DuffelError("Duffel rejected the key (check DUFFEL_API_KEY)")
    if response.status_code == 422:
        raise DuffelError(f"{action} rejected: {detail}")
    if response.status_code == 429:
        raise DuffelError("Duffel rate limit hit — wait a minute and retry")
    raise DuffelError(f"{action} refused: {detail}")


def _post(path: str, body: dict, action: str) -> dict:
    try:
        response = httpx.post(BASE_URL + path, json=body, headers=_headers(), timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise DuffelError(f"{action} failed: {exc}")
    if response.status_code not in (200, 201):
        _refuse(response, action)
    try:
        payload = response.json()
    except ValueError:
        raise DuffelError(f"{action} returned an unreadable reply")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise DuffelError(f"{action} returned an unreadable reply")
    return payload["data"]


def _get(path: str, action: str) -> dict:
    try:
        response = httpx.get(BASE_URL + path, headers=_headers(), timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise DuffelError(f"{action} failed: {exc}")
    if response.status_code != 200:
        _refuse(response, action)
    try:
        payload = response.json()
    except ValueError:
        raise DuffelError(f"{action} returned an unreadable reply")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise DuffelError(f"{action} returned an unreadable reply")
    return payload["data"]


def _clean_iata(code: str, label: str) -> str:
    clean = (code or "").strip().upper()
    if not _IATA_RE.match(clean):
        raise DuffelError(f"{label} must be a 3-letter airport code (e.g. BOM)")
    return clean


def _clean_date(day: str) -> str:
    clean = (day or "").strip()
    try:
        datetime.strptime(clean, "%Y-%m-%d")
    except ValueError:
        raise DuffelError("date must be YYYY-MM-DD")
    return clean


def _summarise_offer(offer: dict) -> dict:
    slices = []
    for sl in offer.get("slices") or []:
        if not isinstance(sl, dict):
            continue
        origin = (sl.get("origin") or {}).get("iata_code", "")
        destination = (sl.get("destination") or {}).get("iata_code", "")
        segments = sl.get("segments") or []
        legs = []
        for seg in segments if isinstance(segments, list) else []:
            if not isinstance(seg, dict):
                continue
            carrier = seg.get("operating_carrier") or {}
            legs.append(
                    f"{carrier.get('name') or carrier.get('iata_code', '')} "
                    f"{carrier.get('iata_code', '')}{seg.get('operating_carrier_flight_number', '')}".strip()
            )
        slices.append(
            {
                "from": origin,
                "to": destination,
                "departs": str(sl.get("departure_datetime") or (segments[0].get("departing_at", "") if segments else "")),
                "arrives": str(sl.get("arrival_datetime") or (segments[-1].get("arriving_at", "") if segments else "")),
                "stops": max(0, len(segments) - 1) if isinstance(segments, list) else 0,
                "flights": [leg for leg in legs if leg],
            }
        )
    return {
        "id": str(offer.get("id", "")),
        "total": f"{offer.get('total_amount', '')} {offer.get('total_currency', '')}".strip(),
        "expires": str(offer.get("expires_at", "")),
        "slices": slices,
    }


def search_flights(*, origin: str, destination: str, date: str, adults: int = 1) -> dict:
    """Real flight offers (test keys: genuine loop, invented schedules/prices)."""
    clean_origin = _clean_iata(origin, "origin")
    clean_destination = _clean_iata(destination, "destination")
    clean_date = _clean_date(date)
    try:
        count = int(adults)
    except (TypeError, ValueError):
        raise DuffelError("adults must be 1-9")
    if not 1 <= count <= 9:
        raise DuffelError("adults must be 1-9")
    data = _post(
        "/air/offer_requests",
        {"data": {
            "slices": [{"origin": clean_origin, "destination": clean_destination, "departure_date": clean_date}],
            "passengers": [{"type": "adult"} for _ in range(count)],
        }},
        "flight search",
    )
    offers = []
    for offer in (data.get("offers") or [])[:MAX_OFFERS]:
        if isinstance(offer, dict) and offer.get("id"):
            offers.append(_summarise_offer(offer))
    passengers = []
    for entry in data.get("passengers") or []:
        if isinstance(entry, dict) and entry.get("id"):
            passengers.append({"id": str(entry["id"])})
    result = {
        "request_id": str(data.get("id", "")),
        "test_mode": _test_mode(),
        "offers": offers,
        "passengers": passengers,
    }
    if result["test_mode"]:
        result["test_note"] = "Test airline (Duffel Airways): the loop is real but schedules and prices are invented."
    return result


def _clean_traveler(traveler) -> dict:
    if not isinstance(traveler, dict):
        raise DuffelError("traveler must be an object with title, names, birth date, email, and phone")
    given = (traveler.get("given_name") or "").strip()
    family = (traveler.get("family_name") or "").strip()
    if not given or not family:
        raise DuffelError("traveler needs a given_name and family_name")
    if len(given) > MAX_NAME_LEN or len(family) > MAX_NAME_LEN:
        raise DuffelError("traveler names must be at most 80 characters")
    title = (traveler.get("title") or "").strip().lower()
    if title not in ("mr", "mrs", "ms", "miss"):
        raise DuffelError("traveler title must be mr, mrs, ms, or miss")
    gender = (traveler.get("gender") or "").strip().lower()
    if gender not in ("m", "f"):
        raise DuffelError("traveler gender must be m or f (airline requirement)")
    born = (traveler.get("born_on") or "").strip()
    try:
        datetime.strptime(born, "%Y-%m-%d")
    except ValueError:
        raise DuffelError("traveler born_on must be YYYY-MM-DD")
    email = (traveler.get("email") or "").strip()
    if "@" not in email or len(email) > MAX_CONTACT_LEN:
        raise DuffelError("traveler needs a valid email")
    phone = (traveler.get("phone_number") or "").strip().replace(" ", "")
    if not re.match(r"^\+\d{7,15}$", phone):
        raise DuffelError("traveler phone_number must be like +919876543210")
    return {
        "title": title, "gender": gender, "given_name": given, "family_name": family,
        "born_on": born, "email": email, "phone_number": phone,
    }


def book_flight(*, offer_id: str, travelers: list, passenger_ids: list) -> dict:
    """Book one offer: re-checks the live price, then creates the order.

    `travelers` (details per passenger) must line up with the
    `passenger_ids` the search returned — Duffel books every seat from
    the offer request together. Test keys produce a real test order
    (booking_reference included) with no ticket and no charge. Live
    keys are refused unless DUFFEL_ENV is explicitly "live" — real
    money never moves by accident.
    """
    clean_offer = (offer_id or "").strip()
    if not clean_offer:
        raise DuffelError("offer_id must not be empty")
    if not _test_mode() and settings.duffel_env.strip().lower() != "live":
        raise DuffelError("refusing: live Duffel key needs DUFFEL_ENV=live before real money can move")
    if not isinstance(travelers, list) or not 1 <= len(travelers) <= 9:
        raise DuffelError("travelers must be 1-9 traveler objects")
    if not isinstance(passenger_ids, list) or len(passenger_ids) != len(travelers):
        raise DuffelError("passenger_ids must line up with travelers (one id per traveler, from the search)")
    clean_ids = []
    for raw in passenger_ids:
        clean = (raw or "").strip() if isinstance(raw, str) else ""
        if not clean:
            raise DuffelError("passenger_ids must be the non-empty ids from the search")
        clean_ids.append(clean)
    passengers = []
    for raw_traveler, pid in zip(travelers, clean_ids):
        passengers.append({"id": pid, **_clean_traveler(raw_traveler)})
    offer = _get(f"/air/offers/{clean_offer}", "offer re-check")
    total = str(offer.get("total_amount", "") or "")
    currency = str(offer.get("total_currency", "") or "")
    if not total or not currency:
        raise DuffelError("the offer expired or lost its price — search again")
    order = _post(
        "/air/orders",
        {"data": {
            "selected_offers": [clean_offer],
            "passengers": passengers,
            "payments": [{"type": "balance", "currency": currency, "amount": total}],
        }},
        "flight booking",
    )
    refs = order.get("associatedRecords") or order.get("associated_records") or []
    booking_reference = str(order.get("booking_reference", "") or "")
    if not booking_reference and isinstance(refs, list) and refs and isinstance(refs[0], dict):
        booking_reference = str(refs[0].get("reference", "") or "")
    result = {
        "order_id": str(order.get("id", "")),
        "booking_reference": booking_reference,
        "total": f"{order.get('total_amount', total)} {order.get('total_currency', currency)}".strip(),
        "test_mode": _test_mode(),
    }
    if result["test_mode"]:
        result["test_note"] = "Test booking: a real test order, but no ticket was issued and nothing was charged."
    return result


def _tool_search(db, *, user_id, task, args) -> dict:
    args = args or {}
    return search_flights(
        origin=args.get("origin", ""), destination=args.get("destination", ""),
        date=args.get("date", ""), adults=args.get("adults", 1),
    )


def _tool_book(db, *, user_id, task, args) -> dict:
    args = args or {}
    return book_flight(
        offer_id=args.get("offer_id", ""), travelers=args.get("travelers", []), passenger_ids=args.get("passenger_ids", [])
    )


tools.register(
    "duffel_search_flights",
    consequential=False,
    handler=_tool_search,
    description="Search real flight offers (test keys use invented test schedules).",
)
tools.register(
    "duffel_book_flight",
    consequential=True,
    handler=_tool_book,
    description="Book a flight offer for real (test keys: test order, no charge). Needs my approval.",
)
