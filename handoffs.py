"""Real booking handoffs (Tier 1 real bookings).

Saath prepares the booking details, then opens the provider's own
site or app with everything filled in — the genuine reservation
happens there, inside the provider, not inside Saath. No credentials,
no billing, no partner approval needed for this tier.

Pure URL builders: no network calls, no secrets, fully deterministic.
Covered by tests/test_handoffs.py.
"""

from urllib.parse import urlencode

import kernel

MAX_TEXT_LEN = 200

UBER_URL = "https://m.uber.com/go/home"
ZOMATO_URL = "https://www.zomato.com/search"
PRACTO_URL = "https://www.practo.com/search/doctors"

# Demo appointment services that map onto Practo specialities.
PRACTO_SPECIALITIES = {"dentist": "Dentist", "doctor": "General Physician"}


class HandoffError(kernel.KernelError):
    """A handoff request broke a handoff rule."""


def _clean(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise HandoffError(f"{label} must not be empty")
    if len(clean) > MAX_TEXT_LEN:
        raise HandoffError(f"{label} must be at most 200 characters")
    return clean


def _optional(value: str, label: str) -> str:
    clean = (value or "").strip()
    if len(clean) > MAX_TEXT_LEN:
        raise HandoffError(f"{label} must be at most 200 characters")
    return clean


def uber_ride(pickup: str, dropoff: str) -> dict:
    """Uber handoff with the route filled in."""
    origin = _clean(pickup, "pickup")
    destination = _clean(dropoff, "dropoff")
    url = UBER_URL + "?" + urlencode({"pickup": origin, "dropoff": destination})
    return {
        "provider": "Uber",
        "url": url,
        "label": f"Book this ride for real on Uber ({origin} to {destination})",
        "note": "Saath opens Uber with your route filled in — the real booking happens in Uber, not inside Saath.",
    }


def zomato_search(query: str, area: str = "") -> dict:
    """Zomato restaurant search handoff."""
    what = _clean(query, "query")
    where = _optional(area, "area")
    full = f"{what} {where}".strip()
    url = ZOMATO_URL + "?" + urlencode({"q": full})
    return {
        "provider": "Zomato",
        "url": url,
        "label": f"Find {full} for real on Zomato",
        "note": "Saath opens Zomato's real listings — the booking happens on Zomato, not inside Saath.",
    }


def practo_doctors(service: str, area: str = "") -> dict:
    """Practo clinic search handoff (dentist/doctor only)."""
    clean_service = (service or "").strip().lower()
    speciality = PRACTO_SPECIALITIES.get(clean_service)
    if speciality is None:
        raise HandoffError("Practo handoff covers dentist and doctor visits (salon and spa have no handoff yet)")
    where = _optional(area, "area")
    params = {"speciality": speciality}
    if where:
        params["locality"] = where
    url = PRACTO_URL + "?" + urlencode(params)
    return {
        "provider": "Practo",
        "url": url,
        "label": f"Book a real {clean_service} visit on Practo" + (f" near {where}" if where else ""),
        "note": "Saath opens Practo's real clinic listings — the booking happens on Practo, not inside Saath.",
    }


def handoff_for(kind: str, *, from_place: str = "", to_place: str = "", service: str = "", area: str = "", q: str = "") -> dict:
    """One handoff for a booking kind.

    kind "cab" -> Uber, "appointment" -> Practo (dentist/doctor),
    "food" -> Zomato. Anything else raises HandoffError saying so
    honestly instead of inventing a provider.
    """
    clean_kind = (kind or "").strip().lower()
    if clean_kind == "cab":
        return uber_ride(from_place, to_place)
    if clean_kind == "appointment":
        return practo_doctors(service, area)
    if clean_kind == "food":
        return zomato_search(q, area)
    raise HandoffError("real handoffs cover cabs, clinic visits, and food (nothing else yet)")
