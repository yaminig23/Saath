import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, call } from "../api";
import type { Notify, ToolResult } from "../api";
import type { Navigate, OpenTask } from "../nav";
import { DemoBanner } from "./Connect";
import { timeZone } from "../timezone";

/* Labeled demo bookings: real tasks + real approvals around a fictional provider. */

interface DemoOption {
  option_id: string;
  name: string;
  detail: string;
  price: string;
  maps_note?: string;
}

interface DemoContact {
  id: string;
  name: string;
  relation: string;
}

interface DemoInvitee {
  id: string;
  name: string;
  rsvp: string;
}

interface DemoBookingRow {
  id: number;
  booking_id: string;
  task_id: number;
  kind: string;
  option: DemoOption & { venue?: DemoOption; title?: string; date?: string; invitees?: DemoInvitee[] };
  status: string;
  created_at: string;
  disclaimer: string;
}

interface BookingsProps {
  notify: Notify;
  navigate: Navigate;
  onOpenTask: OpenTask;
}

interface Handoff {
  provider: string;
  url: string;
  label: string;
  note: string;
}

type Tab = "cab" | "appointment" | "service" | "party" | "flights";

interface FlightSlice {
  from: string;
  to: string;
  departs: string;
  arrives: string;
  stops: number;
  flights: string[];
}

interface FlightOffer {
  id: string;
  total: string;
  expires: string;
  slices: FlightSlice[];
}

interface FlightSearch {
  request_id: string;
  test_mode: boolean;
  test_note?: string;
  offers: FlightOffer[];
  passengers: { id: string }[];
}

interface TravelerForm {
  title: string;
  gender: string;
  given: string;
  family: string;
  born: string;
  email: string;
  phone: string;
}

function nextWeek(): string {
  const d = new Date();
  d.setDate(d.getDate() + 7);
  return d.toISOString().slice(0, 10);
}

const SERVICES = ["dentist", "salon", "doctor", "spa"];
const SVC_KINDS = ["carwash", "lawyer"];
const KIND_LABEL: Record<string, string> = { cab: "Cab", appointment: "Appointment", service: "Service", party: "Party" };
const TOOL: Record<string, string> = { cab: "demo_book_cab", appointment: "demo_book_appointment", service: "demo_book_service" };

interface CalDraft {
  bookingId: number;
  title: string;
  date: string;
  start: string;
  end: string;
}

function plusMinutes(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm;
  const total = h * 60 + m + minutes;
  return `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function draftFor(booking: DemoBookingRow): CalDraft | null {
  if (booking.kind === "appointment") {
    const parts = (booking.option.option_id || "").split("|");
    if (parts.length !== 5) return null;
    const start = parts[4];
    return {
      bookingId: booking.id,
      title: `${booking.option.name} visit`,
      date: parts[2],
      start,
      end: plusMinutes(start, 30),
    };
  }
  if (booking.kind === "party" && booking.option.date) {
    return {
      bookingId: booking.id,
      title: booking.option.title || "Party",
      date: booking.option.date,
      start: "19:00",
      end: "22:00",
    };
  }
  return null;
}

interface LivePlace {
  name: string;
  address: string;
  rating: number | null;
  open_now: boolean | null;
}

interface LivePlaces {
  query: string;
  live: boolean;
  places: LivePlace[];
}

interface LiveRoute {
  origin: string;
  destination: string;
  mode: string;
  live: boolean;
  distance: string;
  duration: string;
  steps: string[];
  detail?: string;
}

function demoProblem(err: unknown, fallback: string): string {
  /* These demo endpoints always exist on a current server, so a 404 here
     means the running server predates them — say so instead of "Not Found". */
  if (err instanceof ApiError && err.status === 404) {
    return "Bookings need the newest server — stop it, run 'fastapi dev' again, and refresh this tab.";
  }
  return err instanceof Error ? err.message : fallback;
}

export default function Bookings({ notify, navigate, onOpenTask }: BookingsProps) {
  const [tab, setTab] = useState<Tab>("cab");
  const [fromPlace, setFromPlace] = useState("Home");
  const [toPlace, setToPlace] = useState("Airport");
  const [service, setService] = useState("dentist");
  const [date, setDate] = useState(nextWeek());
  const [svc, setSvc] = useState("carwash");
  const [area, setArea] = useState("");
  const [partyTitle, setPartyTitle] = useState("");
  const [partyDate, setPartyDate] = useState(nextWeek());
  const [selectedVenue, setSelectedVenue] = useState("");
  const [contacts, setContacts] = useState<DemoContact[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [options, setOptions] = useState<DemoOption[]>([]);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [bookings, setBookings] = useState<DemoBookingRow[]>([]);
  const [bookingBusy, setBookingBusy] = useState<string | null>(null);
  const [awaiting, setAwaiting] = useState<number | null>(null);
  const [calDraft, setCalDraft] = useState<CalDraft | null>(null);
  const [calBusy, setCalBusy] = useState(false);
  const [routeMode, setRouteMode] = useState("driving");
  const [liveRoute, setLiveRoute] = useState<LiveRoute | null>(null);
  const [livePlaces, setLivePlaces] = useState<LivePlaces | null>(null);
  const [liveBusy, setLiveBusy] = useState(false);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [foodQuery, setFoodQuery] = useState("biryani");
  const [foodArea, setFoodArea] = useState("");
  const [flOrigin, setFlOrigin] = useState("BOM");
  const [flDest, setFlDest] = useState("DEL");
  const [flDate, setFlDate] = useState(nextWeek());
  const [flAdults, setFlAdults] = useState("1");
  const [flSearch, setFlSearch] = useState<FlightSearch | null>(null);
  const [flPicked, setFlPicked] = useState<FlightOffer | null>(null);
  const [flTravelers, setFlTravelers] = useState<TravelerForm[]>([]);

  function patchTraveler(index: number, patch: Partial<TravelerForm>) {
    setFlTravelers((prev) => prev.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  }

  function pickOffer(offer: FlightOffer) {
    setFlPicked(offer);
    setFlTravelers((flSearch?.passengers ?? []).map(() => ({ title: "mr", gender: "m", given: "", family: "", born: "", email: "", phone: "" })));
  }

  async function loadBookings() {
    setBookings(await call<DemoBookingRow[]>("GET", "/demo/bookings"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadBookings().catch((err: unknown) => notify(demoProblem(err, "Could not load bookings."), true));
    call<{ contacts: DemoContact[] }>("GET", "/demo/contacts")
      .then((data) => setContacts(data.contacts))
      .catch((err: unknown) => notify(demoProblem(err, "Could not load contacts."), true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function switchTab(next: Tab) {
    setTab(next);
    setSearched(false);
    setOptions([]);
    setSelectedVenue("");
    setLiveRoute(null);
    setLivePlaces(null);
    setHandoff(null);
    setFlSearch(null);
    setFlPicked(null);
  }

  async function realHandoff(query: string) {
    setHandoffBusy(true);
    try {
      setHandoff(await call<Handoff>("GET", "/demo/handoff?" + query));
    } catch (err) {
      notify(err instanceof Error ? err.message : "Handoff failed.", true);
    } finally {
      setHandoffBusy(false);
    }
  }

  async function checkRoute() {
    setLiveBusy(true);
    try {
      const data = await call<LiveRoute>(
        "GET",
        `/maps/route?from_place=${encodeURIComponent(fromPlace.trim())}&to_place=${encodeURIComponent(toPlace.trim())}&mode=${routeMode}`
      );
      setLiveRoute(data);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Live route failed.", true);
    } finally {
      setLiveBusy(false);
    }
  }

  async function checkPlaces() {
    setLiveBusy(true);
    try {
      const query =
        tab === "appointment" ? service : tab === "service" ? (svc === "carwash" ? "car wash" : "lawyer") : "event venue";
      const data = await call<LivePlaces>(
        "GET",
        `/maps/places?q=${encodeURIComponent(query)}&near=${encodeURIComponent(area.trim())}`
      );
      setLivePlaces(data);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Live places failed.", true);
    } finally {
      setLiveBusy(false);
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      let query = "";
      if (tab === "cab") {
        query = `kind=cab&from_place=${encodeURIComponent(fromPlace.trim())}&to_place=${encodeURIComponent(toPlace.trim())}`;
      } else if (tab === "appointment") {
        query = `kind=appointment&service=${encodeURIComponent(service)}&date=${encodeURIComponent(date.trim())}`;
      } else if (tab === "service") {
        query = `kind=service&service=${encodeURIComponent(svc)}&area=${encodeURIComponent(area.trim())}`;
      } else {
        query = `kind=venue&area=${encodeURIComponent(area.trim())}`;
      }
      const data = await call<{ options: DemoOption[] }>("GET", "/demo/options?" + query);
      setOptions(data.options);
      setSearched(true);
    } catch (err) {
      notify(demoProblem(err, "Search failed."), true);
    } finally {
      setBusy(false);
    }
  }

  async function searchFlights(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const data = await call<FlightSearch>(
        "GET",
        `/travel/flights/search?origin=${encodeURIComponent(flOrigin.trim())}&destination=${encodeURIComponent(flDest.trim())}&date=${encodeURIComponent(flDate.trim())}&adults=${encodeURIComponent(flAdults.trim())}`
      );
      setFlSearch(data);
      setFlPicked(null);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Flight search failed.", true);
    } finally {
      setBusy(false);
    }
  }

  async function bookFlight() {
    if (!flPicked || !flSearch) return;
    setBookingBusy(flPicked.id);
    try {
      const taskId = await startTask(`Flight booking: ${flPicked.total}`);
      const out = await call<ToolResult>("POST", "/tasks/" + taskId + "/tools", {
        tool: "duffel_book_flight",
        args: {
          offer_id: flPicked.id,
          travelers: flTravelers.map((t) => ({
            title: t.title,
            gender: t.gender,
            given_name: t.given.trim(),
            family_name: t.family.trim(),
            born_on: t.born.trim(),
            email: t.email.trim(),
            phone_number: t.phone.trim(),
          })),
          passenger_ids: flSearch.passengers.map((p) => p.id),
        },
      });
      if (out.status === "awaiting_approval") {
        setAwaiting(taskId);
        notify("Sent to Approvals — Saath never books without your yes.");
      } else {
        notify("Booking completed.");
      }
      await loadBookings();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Booking failed.", true);
    } finally {
      setBookingBusy(null);
    }
  }

  async function startTask(objective: string): Promise<number> {
    const task = await call<{ id: number }>("POST", "/tasks", { objective });
    await call("POST", "/tasks/" + task.id + "/transition", { to_status: "IN_PROGRESS" });
    return task.id;
  }

  async function select(option: DemoOption) {
    setBookingBusy(option.option_id);
    try {
      const taskId = await startTask(`Demo ${tab} booking: ${option.name}`);
      const out = await call<ToolResult>("POST", "/tasks/" + taskId + "/tools", {
        tool: TOOL[tab],
        args: { option_id: option.option_id },
      });
      if (out.status === "awaiting_approval") {
        setAwaiting(taskId);
        notify("Sent to Approvals — Saath never books without your yes.");
      } else {
        notify("Booking completed.");
      }
      await loadBookings();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Booking failed.", true);
    } finally {
      setBookingBusy(null);
    }
  }

  async function hostParty(event: FormEvent) {
    event.preventDefault();
    if (!selectedVenue) {
      notify("Pick a venue first.", true);
      return;
    }
    if (picked.length === 0) {
      notify("Invite at least one contact.", true);
      return;
    }
    setBookingBusy("party");
    try {
      const taskId = await startTask(`Demo party: ${partyTitle.trim()}`);
      const out = await call<ToolResult>("POST", "/tasks/" + taskId + "/tools", {
        tool: "demo_host_party",
        args: { venue_option_id: selectedVenue, title: partyTitle.trim(), date: partyDate.trim(), contact_ids: picked },
      });
      if (out.status === "awaiting_approval") {
        setAwaiting(taskId);
        notify("Sent to Approvals — contacting people always needs your yes.");
      }
      await loadBookings();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Party booking failed.", true);
    } finally {
      setBookingBusy(null);
    }
  }

  async function sampleRsvps(booking: DemoBookingRow) {
    try {
      await call("POST", "/demo/party/sample-rsvps", { booking_task_id: booking.task_id });
      await loadBookings();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Sample replies failed.", true);
    }
  }

  async function sendToCalendar(draft: CalDraft) {
    setCalBusy(true);
    try {
      const status = await call<{ connected: boolean }>("GET", "/calendar/status");
      if (!status.connected) {
        notify("Connect Google Calendar first: My Life → Google Calendar.", true);
        navigate("mylife", "calendar");
        return;
      }
      const taskId = await startTask(`Add to Google Calendar: ${draft.title}`);
      const out = await call<ToolResult>("POST", "/tasks/" + taskId + "/tools", {
        tool: "calendar_add_event",
        args: {
          title: draft.title.trim(),
          date: draft.date.trim(),
          start: draft.start.trim(),
          end: draft.end.trim(),
          time_zone: timeZone(),
          description: `From Saath demo booking (reserves nothing — the calendar event is real).`,
        },
      });
      if (out.status === "awaiting_approval") {
        setAwaiting(taskId);
        setCalDraft(null);
        notify("Sent to Approvals — your calendar only changes with your yes.");
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : "Calendar request failed.", true);
    } finally {
      setCalBusy(false);
    }
  }

  async function cancel(booking: DemoBookingRow) {
    try {
      await call("POST", "/demo/cancel", { booking_task_id: booking.task_id });
      notify(booking.booking_id + " cancelled (demo).");
      await loadBookings();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Cancellation failed.", true);
    }
  }

  function toggleContact(id: string) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));
  }

  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const tabBtn = (active: boolean) =>
    "min-h-12 flex-1 rounded-xl border px-2 text-[15px] " +
    (active ? "border-teal-800 bg-teal-800 font-semibold text-white" : "border-stone-300 bg-white");
  const tabs: { key: Tab; label: string }[] = [
    { key: "cab", label: "Cabs" },
    { key: "appointment", label: "Visits" },
    { key: "service", label: "Services" },
    { key: "party", label: "Party" },
    { key: "flights", label: "Flights" },
  ];
  const showMapsNote = options.some((o) => o.maps_note);

  function rsvpChip(rsvp: string) {
    if (rsvp === "yes") return <span className="rounded-full bg-teal-100 px-2.5 py-0.5 text-xs font-semibold text-teal-900">Yes · sample</span>;
    if (rsvp === "no") return <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">No · sample</span>;
    return <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-900">Invited</span>;
  }

  return (
    <div className="flex flex-col gap-3">
      <DemoBanner text="Demo bookings — a fictional provider behind real Saath machinery. Confirmations look like DEMO-0007 and reserve nothing. Listings are samples in the style of Google Maps, not live data." />

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <div className="flex gap-2">
          {tabs.map((t) => (
            <button key={t.key} type="button" onClick={() => switchTab(t.key)} aria-pressed={tab === t.key} className={tabBtn(tab === t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <form onSubmit={tab === "party" ? hostParty : tab === "flights" ? searchFlights : search} className="mt-2 flex flex-col gap-2">
          {tab === "cab" && (
            <div className="flex flex-wrap gap-2">
              <label className="grow text-sm font-medium">
                From
                <input className={input + " mt-1 w-full"} value={fromPlace} onChange={(e) => setFromPlace(e.target.value)} required />
              </label>
              <label className="grow text-sm font-medium">
                To
                <input className={input + " mt-1 w-full"} value={toPlace} onChange={(e) => setToPlace(e.target.value)} required />
              </label>
            </div>
          )}
          {tab === "appointment" && (
            <div className="flex flex-wrap gap-2">
              <label className="grow text-sm font-medium">
                Service
                <select className={input + " mt-1 w-full"} value={service} onChange={(e) => setService(e.target.value)}>
                  {SERVICES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label className="grow text-sm font-medium">
                Date
                <input className={input + " mt-1 w-full"} value={date} onChange={(e) => setDate(e.target.value)} required />
              </label>
              <label className="grow text-sm font-medium">
                Area (optional, for live search)
                <input className={input + " mt-1 w-full"} placeholder="e.g. Andheri" value={area} onChange={(e) => setArea(e.target.value)} />
              </label>
            </div>
          )}
          {tab === "service" && (
            <div className="flex flex-wrap gap-2">
              <label className="grow text-sm font-medium">
                Service
                <select className={input + " mt-1 w-full"} value={svc} onChange={(e) => setSvc(e.target.value)}>
                  {SVC_KINDS.map((s) => (
                    <option key={s} value={s}>{s === "carwash" ? "Car wash" : "Lawyer"}</option>
                  ))}
                </select>
              </label>
              <label className="grow text-sm font-medium">
                Area (optional)
                <input className={input + " mt-1 w-full"} placeholder="e.g. Market" value={area} onChange={(e) => setArea(e.target.value)} />
              </label>
            </div>
          )}
          {tab === "party" && (
            <>
              <div className="flex flex-wrap gap-2">
                <label className="grow text-sm font-medium">
                  Party title
                  <input className={input + " mt-1 w-full"} placeholder="e.g. Diya's birthday" value={partyTitle} onChange={(e) => setPartyTitle(e.target.value)} required />
                </label>
                <label className="text-sm font-medium">
                  Date
                  <input className={input + " mt-1 w-full"} value={partyDate} onChange={(e) => setPartyDate(e.target.value)} required />
                </label>
              </div>
              <label className="block text-sm font-medium">
                Area (optional)
                <input className={input + " mt-1 w-full"} placeholder="e.g. Gardens" value={area} onChange={(e) => setArea(e.target.value)} />
              </label>
            </>
          )}
          {tab === "flights" && (
            <div className="flex flex-wrap gap-2">
              <label className="grow text-sm font-medium">
                From (airport code)
                <input className={input + " mt-1 w-full uppercase"} value={flOrigin} onChange={(e) => setFlOrigin(e.target.value)} required />
              </label>
              <label className="grow text-sm font-medium">
                To (airport code)
                <input className={input + " mt-1 w-full uppercase"} value={flDest} onChange={(e) => setFlDest(e.target.value)} required />
              </label>
              <label className="grow text-sm font-medium">
                Date
                <input className={input + " mt-1 w-full"} value={flDate} onChange={(e) => setFlDate(e.target.value)} required />
              </label>
              <label className="text-sm font-medium">
                Adults
                <input className={input + " mt-1 w-full"} value={flAdults} onChange={(e) => setFlAdults(e.target.value)} required />
              </label>
            </div>
          )}
          {tab === "party" ? (
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={search} disabled={busy} className="min-h-12 grow rounded-xl border border-stone-300 bg-white px-4 font-medium disabled:opacity-50">
                {busy ? "Searching…" : "Find venues"}
              </button>
              <button type="submit" disabled={bookingBusy !== null || !selectedVenue || picked.length === 0} className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50">
                {bookingBusy === "party" ? "Booking…" : "Host party"}
              </button>
            </div>
          ) : (
            <button type="submit" disabled={busy} className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50">
              {busy ? "Searching…" : tab === "flights" ? "Search real flights" : "Search"}
            </button>
          )}
        </form>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {tab === "cab" ? (
            <>
              <select
                className={input}
                value={routeMode}
                onChange={(e) => setRouteMode(e.target.value)}
                aria-label="Travel mode"
              >
                <option value="driving">Driving</option>
                <option value="walking">Walking</option>
                <option value="transit">Transit</option>
                <option value="bicycling">Cycling</option>
              </select>
              <button
                type="button"
                onClick={checkRoute}
                disabled={liveBusy}
                className="min-h-12 grow rounded-xl border border-teal-800 bg-teal-50 px-4 font-medium text-teal-900 disabled:opacity-50"
              >
                {liveBusy ? "Checking…" : "Get real route & time"}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={checkPlaces}
              disabled={liveBusy}
              className="min-h-12 grow rounded-xl border border-teal-800 bg-teal-50 px-4 font-medium text-teal-900 disabled:opacity-50"
            >
              {liveBusy ? "Checking…" : "Find real places"}
            </button>
          )}
        </div>
        <p className="mt-1 text-xs text-stone-500">Live Google data, not demo samples — each check bills a fraction of a cent.</p>
        {(tab === "cab" || (tab === "appointment" && (service === "dentist" || service === "doctor"))) && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() =>
                realHandoff(
                  tab === "cab"
                    ? `kind=cab&from_place=${encodeURIComponent(fromPlace.trim())}&to_place=${encodeURIComponent(toPlace.trim())}`
                    : `kind=appointment&service=${encodeURIComponent(service)}&area=${encodeURIComponent(area.trim())}`
                ).catch(() => undefined)
              }
              disabled={handoffBusy}
              className="min-h-12 w-full rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
            >
              {handoffBusy ? "Opening…" : tab === "cab" ? "Book this ride for real on Uber" : "Book for real on Practo"}
            </button>
            <p className="mt-1 text-xs text-stone-500">Real handoff — the booking completes on the provider&apos;s site, not in Saath.</p>
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Eat out for real</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          <label className="grow text-sm font-medium">
            Craving
            <input className={input + " mt-1 w-full"} value={foodQuery} onChange={(e) => setFoodQuery(e.target.value)} />
          </label>
          <label className="grow text-sm font-medium">
            Area (optional)
            <input className={input + " mt-1 w-full"} placeholder="e.g. Andheri" value={foodArea} onChange={(e) => setFoodArea(e.target.value)} />
          </label>
        </div>
        <button
          type="button"
          onClick={() =>
            realHandoff(`kind=food&q=${encodeURIComponent(foodQuery.trim())}&area=${encodeURIComponent(foodArea.trim())}`).catch(() => undefined)
          }
          disabled={handoffBusy}
          className="mt-2 min-h-12 w-full rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
        >
          {handoffBusy ? "Opening…" : "Find it for real on Zomato"}
        </button>
      </section>

      {handoff && (
        <section className="rounded-2xl border border-teal-800 bg-teal-50 p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-teal-800 px-2.5 py-0.5 text-xs font-semibold text-white">REAL HANDOFF</span>
            <h4 className="text-base font-semibold">{handoff.provider}</h4>
          </div>
          <p className="mt-1 text-[15px]">{handoff.label}</p>
          <a
            href={handoff.url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-block min-h-12 rounded-xl bg-teal-800 px-5 py-3 font-medium text-white"
          >
            Open {handoff.provider}
          </a>
          <p className="mt-1 text-xs text-stone-600">{handoff.note}</p>
        </section>
      )}

      {tab === "flights" && flSearch && (
        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-teal-800 px-2.5 py-0.5 text-xs font-semibold text-white">REAL FLIGHTS</span>
            <h4 className="text-base font-semibold">{flSearch.offers.length} offers</h4>
          </div>
          {flSearch.test_note && <p className="text-sm text-amber-900">Test mode — {flSearch.test_note}</p>}
          {flSearch.offers.length === 0 && <p className="text-sm text-stone-500">No offers for that route and date.</p>}
          {flSearch.offers.map((offer) => (
            <article key={offer.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <p className="text-[15px] font-semibold">{offer.total}</p>
              {offer.slices.map((sl, i) => (
                <p key={i} className="text-sm text-stone-600">
                  {sl.from} → {sl.to} · {sl.departs.slice(11, 16)}–{sl.arrives.slice(11, 16)} ·{" "}
                  {sl.stops === 0 ? "nonstop" : `${sl.stops} stop${sl.stops === 1 ? "" : "s"}`} · {sl.flights.join(", ")}
                </p>
              ))}
              <button
                type="button"
                onClick={() => pickOffer(offer)}
                aria-pressed={flPicked?.id === offer.id}
                className={
                  "mt-2 min-h-10 rounded-xl border px-3 text-sm " +
                  (flPicked?.id === offer.id ? "border-teal-800 bg-teal-50 font-semibold text-teal-900" : "border-stone-300 bg-white")
                }
              >
                {flPicked?.id === offer.id ? "Selected" : "Select this flight"}
              </button>
            </article>
          ))}
          {flPicked && (
            <div className="rounded-2xl border border-teal-800 bg-teal-50 p-4 shadow-sm">
              <h4 className="text-base font-semibold">Who is flying? ({flPicked.total})</h4>
              {flTravelers.map((t, i) => (
                <div key={i} className="mt-2 flex flex-wrap gap-2 rounded-xl border border-teal-800/30 bg-white p-3">
                  <p className="w-full text-sm font-semibold">Traveler {i + 1}</p>
                  <label className="grow text-sm font-medium">
                    Title
                    <select className={input + " mt-1 w-full"} value={t.title} onChange={(e) => patchTraveler(i, { title: e.target.value })}>
                      <option value="mr">Mr</option>
                      <option value="mrs">Mrs</option>
                      <option value="ms">Ms</option>
                      <option value="miss">Miss</option>
                    </select>
                  </label>
                  <label className="grow text-sm font-medium">
                    First name
                    <input className={input + " mt-1 w-full"} value={t.given} onChange={(e) => patchTraveler(i, { given: e.target.value })} required />
                  </label>
                  <label className="grow text-sm font-medium">
                    Last name
                    <input className={input + " mt-1 w-full"} value={t.family} onChange={(e) => patchTraveler(i, { family: e.target.value })} required />
                  </label>
                  <label className="grow text-sm font-medium">
                    Birth date
                    <input className={input + " mt-1 w-full"} placeholder="YYYY-MM-DD" value={t.born} onChange={(e) => patchTraveler(i, { born: e.target.value })} required />
                  </label>
                  <label className="grow text-sm font-medium">
                    Email
                    <input className={input + " mt-1 w-full"} value={t.email} onChange={(e) => patchTraveler(i, { email: e.target.value })} required />
                  </label>
                  <label className="grow text-sm font-medium">
                    Phone
                    <input className={input + " mt-1 w-full"} placeholder="+919876543210" value={t.phone} onChange={(e) => patchTraveler(i, { phone: e.target.value })} required />
                  </label>
                  <label className="text-sm font-medium">
                    Gender (airline form)
                    <select className={input + " mt-1 w-full"} value={t.gender} onChange={(e) => patchTraveler(i, { gender: e.target.value })}>
                      <option value="m">M</option>
                      <option value="f">F</option>
                    </select>
                  </label>
                </div>
              ))}
              <button
                type="button"
                onClick={() => bookFlight().catch(() => undefined)}
                disabled={bookingBusy !== null}
                className="mt-2 min-h-12 w-full rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
              >
                {bookingBusy ? "Booking…" : "Book this flight (goes to Approvals)"}
              </button>
              <p className="mt-1 text-xs text-stone-600">Test mode issues a real test order with a booking reference — no ticket, no charge.</p>
            </div>
          )}
        </section>
      )}

      {liveRoute && (
        <section className="rounded-2xl border border-teal-800 bg-teal-50 p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-teal-800 px-2.5 py-0.5 text-xs font-semibold text-white">LIVE</span>
            <h4 className="text-base font-semibold">
              {liveRoute.origin} → {liveRoute.destination}
            </h4>
          </div>
          {liveRoute.distance ? (
            <>
              <p className="mt-1 text-[15px] font-medium">
                {liveRoute.distance} · {liveRoute.duration} ({liveRoute.mode})
              </p>
              <ol className="mt-1 flex list-decimal flex-col gap-0.5 pl-5 text-sm text-stone-700">
                {liveRoute.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            </>
          ) : (
            <p className="mt-1 text-[15px]">{liveRoute.detail || "No route found."}</p>
          )}
        </section>
      )}

      {livePlaces && (
        <section>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-teal-800 px-2.5 py-0.5 text-xs font-semibold text-white">LIVE</span>
            <h4 className="text-base font-semibold">Real places: {livePlaces.query}</h4>
          </div>
          {livePlaces.places.length === 0 ? (
            <p className="mt-1 text-sm text-stone-500">Google found nothing for that search.</p>
          ) : (
            <div className="mt-2 flex flex-col gap-2">
              {livePlaces.places.map((p, i) => (
                <article key={i} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                  <p className="text-[15px] font-semibold">{p.name}</p>
                  <p className="text-sm text-stone-500">{p.address}</p>
                  <p className="text-sm text-stone-600">
                    {p.rating !== null && p.rating !== undefined ? `★ ${p.rating}` : "No rating"}
                    {p.open_now === true ? " · Open now" : p.open_now === false ? " · Closed" : ""}
                  </p>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {tab === "party" && (
        <section>
          <h4 className="text-base font-semibold">Invite contacts <span className="font-normal text-stone-500">(fictional — no real details)</span></h4>
          <div className="mt-2 flex flex-wrap gap-2">
            {contacts.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => toggleContact(c.id)}
                aria-pressed={picked.includes(c.id)}
                className={
                  "min-h-12 rounded-full border px-4 text-[15px] " +
                  (picked.includes(c.id) ? "border-teal-800 bg-teal-800 font-semibold text-white" : "border-stone-300 bg-white")
                }
              >
                {c.name} · {c.relation}
              </button>
            ))}
          </div>
        </section>
      )}

      {searched && (
        <section>
          <h4 className="text-base font-semibold">{tab === "party" ? "Venues" : "Options"}</h4>
          {showMapsNote && <p className="mt-0.5 text-xs text-stone-500">Sample listings in the style of Google Maps — not live data.</p>}
          <div className="mt-2 flex flex-col gap-2">
            {options.map((o) => (
              <article
                key={o.option_id}
                className={
                  "flex items-center gap-3 rounded-2xl border p-4 shadow-sm " +
                  (tab === "party" && selectedVenue === o.option_id
                    ? "border-teal-800 bg-teal-50"
                    : "border-stone-200 bg-white")
                }
              >
                <span className="grow">
                  <span className="block text-[15px] font-semibold">{o.name}</span>
                  <span className="block text-sm text-stone-500">{o.detail}</span>
                  <span className="block text-[15px] font-medium">{o.price}</span>
                </span>
                {tab === "party" ? (
                  <button
                    type="button"
                    onClick={() => setSelectedVenue(o.option_id)}
                    aria-pressed={selectedVenue === o.option_id}
                    className="min-h-12 shrink-0 rounded-xl border border-stone-300 bg-white px-4 font-medium"
                  >
                    {selectedVenue === o.option_id ? "Picked ✓" : "Pick"}
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={bookingBusy !== null}
                    onClick={() => select(o)}
                    className="min-h-12 shrink-0 rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
                  >
                    {bookingBusy === o.option_id ? "Booking…" : "Select"}
                  </button>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      {awaiting !== null && (
        <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
          <p className="text-[15px] font-medium">Task {awaiting} is waiting for your approval.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" onClick={() => navigate("mylife", "approvals")} className="min-h-12 rounded-xl bg-teal-800 px-4 font-medium text-white">
              Review in Approvals
            </button>
            <button type="button" onClick={() => onOpenTask(awaiting)} className="min-h-12 rounded-xl border border-stone-300 bg-white px-4">
              Open task
            </button>
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center gap-3">
          <h4 className="text-base font-semibold">My bookings</h4>
          <button type="button" className={btn + " ml-auto"} onClick={() => loadBookings().catch((err: unknown) => notify(err instanceof Error ? err.message : "Refresh failed.", true))}>
            Refresh
          </button>
        </div>
        {bookings.length === 0 ? (
          <p className="mt-2 text-sm text-stone-500">No demo bookings yet. Search above, pick an option, approve it.</p>
        ) : (
          <div className="mt-2 flex flex-col gap-2">
            {bookings.map((b) => (
              <article key={b.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-bold">{b.booking_id}</span>
                  <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">
                    {KIND_LABEL[b.kind] || b.kind} · demo
                  </span>
                  <span className={"inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold " + (b.status === "confirmed" ? "bg-teal-100 text-teal-900" : "bg-stone-200 text-stone-600")}>
                    {b.status === "confirmed" ? "Confirmed (demo)" : "Cancelled"}
                  </span>
                </div>
                {b.kind === "party" && b.option.venue ? (
                  <div className="mt-1">
                    <p className="text-[15px] font-medium">{b.option.title} · {b.option.date}</p>
                    <p className="text-sm text-stone-500">{b.option.venue.name} — {b.option.venue.detail}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {(b.option.invitees || []).map((i) => (
                        <span key={i.id} className="inline-flex items-center gap-1.5 rounded-full bg-stone-100 py-0.5 pl-3 pr-1.5 text-sm">
                          {i.name} {rsvpChip(i.rsvp)}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="mt-1 text-[15px] font-medium">{b.option.name}</p>
                    <p className="text-sm text-stone-500">{b.option.detail} · {b.option.price}</p>
                  </>
                )}
                <p className="mt-1 text-xs text-stone-500">{b.disclaimer}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" onClick={() => onOpenTask(b.task_id)} className={btn}>
                    Open task
                  </button>
                  {b.status === "confirmed" && b.kind === "party" && (b.option.invitees || []).some((i) => i.rsvp === "invited") && (
                    <button type="button" onClick={() => sampleRsvps(b)} className={btn}>
                      Show sample replies
                    </button>
                  )}
                  {b.status === "confirmed" && (
                    <button type="button" onClick={() => cancel(b)} className={btn}>
                      Cancel booking
                    </button>
                  )}
                  {b.status === "confirmed" && draftFor(b) && (
                    <button type="button" onClick={() => setCalDraft(draftFor(b))} className={btn}>
                      Add to Google Calendar
                    </button>
                  )}
                </div>
                {calDraft && calDraft.bookingId === b.id && (
                  <form
                    className="mt-2 flex flex-col gap-2 rounded-xl bg-amber-50 p-3"
                    onSubmit={(e) => {
                      e.preventDefault();
                      sendToCalendar(calDraft);
                    }}
                  >
                    <p className="text-sm font-medium">Real Google event — check the details first:</p>
                    <label className="block text-sm font-medium">
                      Title
                      <input
                        className={input + " mt-1 w-full"}
                        value={calDraft.title}
                        onChange={(e) => setCalDraft({ ...calDraft, title: e.target.value })}
                        required
                      />
                    </label>
                    <div className="flex flex-wrap gap-2">
                      <label className="grow text-sm font-medium">
                        Date
                        <input
                          className={input + " mt-1 w-full"}
                          value={calDraft.date}
                          onChange={(e) => setCalDraft({ ...calDraft, date: e.target.value })}
                          required
                        />
                      </label>
                      <label className="text-sm font-medium">
                        Start
                        <input
                          className={input + " mt-1 w-full"}
                          value={calDraft.start}
                          onChange={(e) => setCalDraft({ ...calDraft, start: e.target.value })}
                          required
                        />
                      </label>
                      <label className="text-sm font-medium">
                        End
                        <input
                          className={input + " mt-1 w-full"}
                          value={calDraft.end}
                          onChange={(e) => setCalDraft({ ...calDraft, end: e.target.value })}
                          required
                        />
                      </label>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="submit"
                        disabled={calBusy}
                        className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
                      >
                        {calBusy ? "Sending…" : "Send to Approvals"}
                      </button>
                      <button type="button" className={btn} onClick={() => setCalDraft(null)}>
                        Cancel
                      </button>
                    </div>
                  </form>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
