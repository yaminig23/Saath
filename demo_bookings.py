"""Demo bookings (clearly-labeled demonstration provider).

Shows the complete booking idea — search, compare, approve, confirm,
cancel — through REAL Saath machinery (tasks, approvals, audit). The
only fictional part is the provider at the end: every option and
confirmation is marked demo, codes look like DEMO-0007, and nothing
reserves anything anywhere. Deterministic: the same search always
returns the same options, and confirmations recompute prices from the
catalog instead of trusting client arguments.

Listings carry address/area/distance/hours shaped like map results so
the demo shows where live data would plug in; each says plainly that
it is a sample, not live data. Party invites go to fictional contacts
as "Saath demo messages" — no real contact details exist anywhere.
"""

import hashlib
import json
from datetime import datetime

import kernel
import tools
from models import DemoBooking
from util import utcnow

DEMO_AGENT = "demo-bookings"
DISCLAIMER = "Demo booking — no real reservation was made."
MAPS_NOTE = "Sample listing in the style of Google Maps — not live data."

CAB_FLEET = [
    {"id": "swift", "name": "City Swift", "kind": "Hatchback", "base_fare": 120, "per_km": 14, "eta_min": 6},
    {"id": "comfort", "name": "Comfort Plus", "kind": "Sedan", "base_fare": 180, "per_km": 18, "eta_min": 9},
    {"id": "xl", "name": "Family XL", "kind": "SUV", "base_fare": 240, "per_km": 22, "eta_min": 12},
]

CLINICS = [
    {"id": "central", "name": "Central Clinic", "area": "City Centre"},
    {"id": "lakeside", "name": "Lakeside Practice", "area": "Lakeside"},
    {"id": "hilltop", "name": "Hilltop Health", "area": "Hilltop"},
]

APPOINTMENT_SERVICES = ("dentist", "salon", "doctor", "spa")
APPOINTMENT_SLOTS = ("09:30", "11:00", "14:30")

SERVICE_PROVIDERS = {
    "carwash": [
        {"id": "sparkle", "name": "Sparkle Car Wash", "address": "14 Market Road", "area": "Market", "hours": "8:00–20:00", "price": "₹399", "detail": "Exterior wash + vacuum, ~40 min"},
        {"id": "shine", "name": "Shine & Go", "address": "2 Lake View", "area": "Lakeside", "hours": "9:00–21:00", "price": "₹599", "detail": "Full wash + wax, ~60 min"},
        {"id": "pitstop", "name": "Pitstop Express", "address": "77 Station Road", "area": "Station", "hours": "7:00–23:00", "price": "₹299", "detail": "Express exterior wash, ~20 min"},
    ],
    "lawyer": [
        {"id": "mehta", "name": "Mehta & Rao Associates", "address": "9 Court Lane", "area": "City Centre", "hours": "Mon–Fri 10:00–18:00", "price": "First consultation ₹500", "detail": "General practice, Hindi + English"},
        {"id": "iyer", "name": "Iyer Legal Help", "address": "31 River Street", "area": "Riverside", "hours": "Mon–Sat 11:00–19:00", "price": "First consultation ₹400", "detail": "Family and housing matters"},
        {"id": "khan", "name": "Khan & Partners", "address": "5 Garden Avenue", "area": "Gardens", "hours": "Mon–Fri 9:30–17:30", "price": "First consultation ₹600", "detail": "Documents, contracts, notary"},
    ],
}

VENUES = [
    {"id": "terrace", "name": "Sunset Terrace Hall", "address": "1 Hilltop View", "area": "Hilltop", "hours": "Evenings till 22:00", "price": "₹5,000 an evening", "detail": "Seats 40, kitchen included"},
    {"id": "garden", "name": "Palm Garden Lawns", "address": "18 Palm Road", "area": "Gardens", "hours": "8:00–21:00", "price": "₹8,000 an evening", "detail": "Seats 120, open lawn + cover"},
    {"id": "community", "name": "Community Hall", "address": "3 Station Road", "area": "Station", "hours": "9:00–20:00", "price": "₹2,000 an evening", "detail": "Seats 60, bring your own food"},
]

DEMO_CONTACTS = [
    {"id": "aarav", "name": "Aarav", "relation": "son"},
    {"id": "diya", "name": "Diya", "relation": "daughter"},
    {"id": "rao", "name": "Mrs. Rao", "relation": "neighbour"},
    {"id": "karim", "name": "Karim", "relation": "friend"},
    {"id": "elena", "name": "Elena", "relation": "friend"},
    {"id": "joseph", "name": "Joseph", "relation": "cousin"},
    {"id": "fatima", "name": "Fatima", "relation": "neighbour"},
    {"id": "noah", "name": "Noah", "relation": "grandson"},
]

CONTACTS_NOTE = "Fictional contacts for the demo. No real contact details exist; invites go as Saath demo messages."


class DemoError(kernel.KernelError):
    """A demo request broke a demo rule."""


def _no_pipes(value: str, label: str) -> str:
    clean = (value or "").strip()
    if "|" in clean:
        raise DemoError(f"{label} must not contain '|'")
    return clean


def _pick(catalog: list, seed: str) -> list:
    """Deterministically price/order a catalog for a search seed."""
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    options = []
    for index, entry in enumerate(catalog):
        jitter = int(digest[index * 2 : index * 2 + 2], 16)
        options.append({**entry, "jitter": jitter % 40})
    return options


def _distance(seed: str) -> str:
    """Deterministic sample distance like a map result would show."""
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    kilometres = 0.5 + (int(digest[:4], 16) % 55) / 10
    return f"{kilometres:.1f} km away"


def search_cabs(from_place: str, to_place: str) -> dict:
    """Three demo cabs for a route. Same route, same options, always."""
    origin = _no_pipes(from_place, "from_place")
    destination = _no_pipes(to_place, "to_place")
    if not origin or not destination:
        raise DemoError("from_place and to_place must not be empty")
    if len(origin) > 120 or len(destination) > 120:
        raise DemoError("places must be at most 120 characters")
    options = []
    for car in _pick(CAB_FLEET, f"cab|{origin}|{destination}"):
        price = car["base_fare"] + car["jitter"]
        options.append(
            {
                "option_id": f"cab|{car['id']}|{origin}|{destination}",
                "name": car["name"],
                "detail": f"{car['kind']} · arrives in ~{car['eta_min'] + car['jitter'] % 5} min",
                "price": f"₹{price}",
            }
        )
    return {"demo": True, "disclaimer": DISCLAIMER, "options": options}


def search_appointments(service: str, date: str) -> dict:
    """Three demo appointment slots. Same query, same options, always."""
    clean_service = (service or "").strip().lower()
    if clean_service not in APPOINTMENT_SERVICES:
        raise DemoError(f"service must be one of {', '.join(APPOINTMENT_SERVICES)}")
    clean_date = (date or "").strip()
    try:
        datetime.strptime(clean_date, "%Y-%m-%d")
    except ValueError:
        raise DemoError("date must be YYYY-MM-DD")
    options = []
    for index, clinic in enumerate(_pick(CLINICS, f"appt|{clean_service}|{clean_date}")):
        slot = APPOINTMENT_SLOTS[(index + clinic["jitter"]) % len(APPOINTMENT_SLOTS)]
        options.append(
            {
                "option_id": f"appt|{clean_service}|{clean_date}|{clinic['id']}|{slot}",
                "name": clinic["name"],
                "detail": f"{clean_service.title()} · {clean_date} at {slot} · {clinic['area']}",
                "price": "Free demo visit",
            }
        )
    return {"demo": True, "disclaimer": DISCLAIMER, "options": options}


def search_services(service: str, area: str = "") -> dict:
    """Demo everyday services (car wash, lawyer) with map-style listings."""
    clean_service = (service or "").strip().lower()
    if clean_service not in SERVICE_PROVIDERS:
        raise DemoError(f"service must be one of {', '.join(SERVICE_PROVIDERS)}")
    clean_area = _no_pipes(area, "area")
    if len(clean_area) > 120:
        raise DemoError("area must be at most 120 characters")
    area_key = clean_area or "any"
    options = []
    for provider in _pick(SERVICE_PROVIDERS[clean_service], f"svc|{clean_service}|{area_key}"):
        seed = f"svc|{clean_service}|{provider['id']}|{area_key}"
        options.append(
            {
                "option_id": f"svc|{clean_service}|{provider['id']}|{area_key}",
                "name": provider["name"],
                "detail": f"{provider['detail']} · {provider['address']} · {_distance(seed)} · {provider['hours']}",
                "price": provider["price"],
                "maps_note": MAPS_NOTE,
            }
        )
    return {"demo": True, "disclaimer": DISCLAIMER, "options": options}


def search_venues(area: str = "") -> dict:
    """Demo party venues with map-style listings."""
    clean_area = _no_pipes(area, "area")
    if len(clean_area) > 120:
        raise DemoError("area must be at most 120 characters")
    area_key = clean_area or "any"
    options = []
    for venue in _pick(VENUES, f"venue|{area_key}"):
        seed = f"venue|{venue['id']}|{area_key}"
        options.append(
            {
                "option_id": f"venue|{venue['id']}|{area_key}",
                "name": venue["name"],
                "detail": f"{venue['detail']} · {venue['address']} · {_distance(seed)} · {venue['hours']}",
                "price": venue["price"],
                "maps_note": MAPS_NOTE,
            }
        )
    return {"demo": True, "disclaimer": DISCLAIMER, "options": options}


def list_contacts() -> dict:
    """Fictional contacts for demo party invites."""
    return {"demo": True, "note": CONTACTS_NOTE, "contacts": DEMO_CONTACTS}


def _resolve_option(option_id: str, kind: str) -> dict:
    """Recompute an option from its ID. Forged options fail closed."""
    parts = (option_id or "").split("|")
    try:
        if kind == "cab" and len(parts) == 4 and parts[0] == "cab":
            _fleet, origin, destination = parts[1], parts[2], parts[3]
            found = [option for option in search_cabs(origin, destination)["options"] if option["option_id"] == option_id]
        elif kind == "appointment" and len(parts) == 5 and parts[0] == "appt":
            found = [
                option
                for option in search_appointments(parts[1], parts[2])["options"]
                if option["option_id"] == option_id
            ]
        elif kind == "service" and len(parts) == 4 and parts[0] == "svc":
            found = [
                option
                for option in search_services(parts[1], parts[3])["options"]
                if option["option_id"] == option_id
            ]
        elif kind == "venue" and len(parts) == 3 and parts[0] == "venue":
            found = [
                option for option in search_venues(parts[2])["options"] if option["option_id"] == option_id
            ]
        else:
            found = []
    except DemoError:
        found = []
    if not found:
        raise DemoError("unknown demo option")
    return found[0]


def _demo_book(db, *, user_id: int, task, args: dict, kind: str) -> dict:
    option = _resolve_option((args or {}).get("option_id", ""), kind)
    booking_id = f"DEMO-{task.id:04d}"
    row = DemoBooking(
        user_id=user_id,
        task_id=task.id,
        kind=kind,
        option=json.dumps(option, sort_keys=True),
        booking_id=booking_id,
        status="confirmed",
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    return {"booking_id": booking_id, "option": option, "demo": True, "disclaimer": DISCLAIMER}


def _book_cab(db, *, user_id, task, args) -> dict:
    return _demo_book(db, user_id=user_id, task=task, args=args, kind="cab")


def _book_appointment(db, *, user_id, task, args) -> dict:
    return _demo_book(db, user_id=user_id, task=task, args=args, kind="appointment")


def _book_service(db, *, user_id, task, args) -> dict:
    return _demo_book(db, user_id=user_id, task=task, args=args, kind="service")


def _host_party(db, *, user_id, task, args) -> dict:
    """Book a demo venue and invite fictional contacts (approval-gated, like any outreach)."""
    args = args or {}
    venue = _resolve_option(args.get("venue_option_id", ""), "venue")
    title = str(args.get("title", "")).strip()
    if not title:
        raise DemoError("party title must not be empty")
    if len(title) > 120:
        raise DemoError("party title must be at most 120 characters")
    date = str(args.get("date", "")).strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise DemoError("date must be YYYY-MM-DD")
    wanted = args.get("contact_ids", [])
    if not isinstance(wanted, list) or not wanted:
        raise DemoError("invite at least one contact")
    if len(wanted) > len(DEMO_CONTACTS):
        raise DemoError("too many contacts")
    known = {contact["id"]: contact for contact in DEMO_CONTACTS}
    invitees = []
    for contact_id in wanted:
        if contact_id not in known:
            raise DemoError(f"unknown contact {contact_id!r}")
        if any(invitee["id"] == contact_id for invitee in invitees):
            continue
        invitees.append({"id": contact_id, "name": known[contact_id]["name"], "rsvp": "invited"})
    booking_id = f"DEMO-{task.id:04d}"
    option = {"venue": venue, "title": title, "date": date, "invitees": invitees}
    row = DemoBooking(
        user_id=user_id,
        task_id=task.id,
        kind="party",
        option=json.dumps(option, sort_keys=True),
        booking_id=booking_id,
        status="confirmed",
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    return {"booking_id": booking_id, "option": option, "demo": True, "disclaimer": DISCLAIMER}


tools.register(
    "demo_book_cab",
    consequential=True,
    handler=_book_cab,
    description="Book a demo cab (demo — makes no reservation).",
)
tools.register(
    "demo_book_appointment",
    consequential=True,
    handler=_book_appointment,
    description="Book a demo appointment (demo — makes no reservation).",
)
tools.register(
    "demo_book_service",
    consequential=True,
    handler=_book_service,
    description="Book a demo service (demo — makes no reservation).",
)
tools.register(
    "demo_host_party",
    consequential=True,
    handler=_host_party,
    description="Host a demo party and invite demo contacts (demo — contacts nobody).",
)


def list_bookings(db, *, user_id: int) -> list:
    """The user's demo bookings, newest first, with parsed options."""
    rows = db.query(DemoBooking).filter_by(user_id=user_id).order_by(DemoBooking.id.desc()).all()
    bookings = []
    for row in rows:
        try:
            option = json.loads(row.option)
        except ValueError:
            option = {}
        bookings.append(
            {
                "id": row.id,
                "booking_id": row.booking_id,
                "task_id": row.task_id,
                "kind": row.kind,
                "option": option,
                "status": row.status,
                "created_at": row.created_at,
                "demo": True,
                "disclaimer": DISCLAIMER,
            }
        )
    return bookings


def sample_rsvps(db, *, user_id: int, booking_task_id: int) -> dict:
    """Fill in explicitly-requested sample RSVPs (deterministic, labeled)."""
    row = db.query(DemoBooking).filter_by(user_id=user_id, task_id=booking_task_id).first()
    if row is None or row.kind != "party":
        raise kernel.TaskNotFoundError("demo party not found")
    if row.status != "confirmed":
        raise DemoError("demo party is not active")
    try:
        option = json.loads(row.option)
    except ValueError:
        raise DemoError("demo party record is unreadable")
    for invitee in option.get("invitees", []):
        digest = hashlib.md5(f"{row.booking_id}|{invitee['id']}".encode("utf-8")).hexdigest()
        invitee["rsvp"] = "yes" if int(digest[:2], 16) % 2 == 0 else "no"
    row.option = json.dumps(option, sort_keys=True)
    db.commit()
    return {
        "booking_id": row.booking_id,
        "invitees": option.get("invitees", []),
        "sample": True,
        "disclaimer": "Sample replies — nobody real was contacted.",
    }


def cancel_booking(db, *, user_id: int, booking_task_id: int) -> dict:
    """Cancel a demo booking: a real cancellation task, honest record."""
    row = db.query(DemoBooking).filter_by(user_id=user_id, task_id=booking_task_id).first()
    if row is None:
        raise kernel.TaskNotFoundError("demo booking not found")
    if row.status != "confirmed":
        raise DemoError("demo booking is already cancelled")
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Cancel {row.booking_id} (demo)",
        department="general",
        agent=DEMO_AGENT,
        priority="normal",
    )
    kernel.transition_task(db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="demo cancellation started")
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="demo cancellation accepted",
        result=f"Demo cancellation accepted for {row.booking_id} — nothing was ever reserved.",
        evidence=f"demo booking={row.booking_id}",
    )
    row.status = "cancelled"
    row.cancelled_by_task_id = task.id
    db.commit()
    return {"booking_id": row.booking_id, "cancel_task_id": task.id, "demo": True, "disclaimer": DISCLAIMER}


class DemoBookingsProvider:
    """The demo catalog as a provider: status "demo", never "connected"."""

    name = "bookings"
    title = "Bookings (demo)"
    capabilities = ["demo cabs", "demo appointments", "demo services", "demo parties"]

    def status(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "capabilities": self.capabilities,
            "status": "demo",
            "detail": "demo catalog only — confirmations are DEMO-XXXX and reserve nothing",
        }

    def check(self) -> dict:
        return {"ok": True, "detail": "demo catalog: cabs, appointments, services, venues; try My Life → Bookings"}
