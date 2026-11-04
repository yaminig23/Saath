import { useEffect, useState } from "react";
import { call } from "../api";
import type { Notify } from "../api";
import { timeZone } from "../timezone";

/* Google Calendar: the first real integration. Connect once in your
   browser, then Saath can read your day and add events — every write
   still pauses for your approval. */

interface CalendarStatus {
  client_configured: boolean;
  connected: boolean;
  email: string;
  broken: string;
  detail: string;
}

interface DayEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  link: string;
}

function todayLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function CalendarLink({ notify }: { notify: Notify }) {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [events, setEvents] = useState<DayEvent[]>([]);
  const [eventsDay, setEventsDay] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setStatus(await call<CalendarStatus>("GET", "/calendar/status"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) =>
      notify(err instanceof Error ? err.message : "Could not load calendar status.", true)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function connect() {
    setBusy(true);
    try {
      const redirect = window.location.origin + "/calendar/callback";
      const data = await call<{ url: string }>("POST", "/calendar/auth-url", { redirect_uri: redirect });
      window.open(data.url, "_blank", "noopener");
      notify("Approve Saath in the new tab, then press Refresh below.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not start connecting.", true);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      const out = await call<{ disconnected: boolean; detail: string }>("POST", "/calendar/disconnect");
      notify(out.detail);
      await load();
      setEvents([]);
      setEventsDay("");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not disconnect.", true);
    } finally {
      setBusy(false);
    }
  }

  async function readToday() {
    setBusy(true);
    try {
      const day = todayLocal();
      const data = await call<{ date: string; events: DayEvent[] }>(
        "GET",
        `/calendar/day?date=${day}&time_zone=${encodeURIComponent(timeZone())}`
      );
      setEvents(data.events);
      setEventsDay(data.date);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not read your calendar.", true);
    } finally {
      setBusy(false);
    }
  }

  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px] disabled:opacity-50";

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h4 className="text-base font-semibold">Google Calendar link</h4>
        {!status ? (
          <p className="mt-1 text-sm text-stone-500">Loading…</p>
        ) : !status.client_configured ? (
          <p className="mt-1 text-[15px]">
            Calendar is not set up on this server yet — it needs a Google Cloud OAuth client first.
          </p>
        ) : status.connected ? (
          <p className="mt-1 text-[15px]">
            Connected{status.email ? ` as ${status.email}` : ""}. Saath reads your day on request, and every new
            event pauses for your approval first.
          </p>
        ) : status.broken ? (
          <p className="mt-1 text-[15px]" role="alert">
            {status.broken}
          </p>
        ) : (
          <p className="mt-1 text-[15px]">
            Not connected. Connecting opens Google in a new tab — Saath only asks to read and write events and
            see your email address, nothing else.
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {status?.connected ? (
            <>
              <button type="button" className={btn} disabled={busy} onClick={readToday}>
                {busy ? "Reading…" : "Show my events today"}
              </button>
              <button type="button" className={btn} disabled={busy} onClick={disconnect}>
                Disconnect
              </button>
            </>
          ) : (
            status?.client_configured && (
              <>
                <button
                  type="button"
                  className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50"
                  disabled={busy}
                  onClick={connect}
                >
                  {busy ? "Starting…" : status?.broken ? "Reconnect Google Calendar" : "Connect Google Calendar"}
                </button>
                <button
                  type="button"
                  className={btn}
                  onClick={() => load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Refresh failed.", true))}
                >
                  Refresh
                </button>
              </>
            )
          )}
        </div>
      </section>

      {eventsDay && (
        <section>
          <h4 className="text-base font-semibold">Real events · {eventsDay}</h4>
          {events.length === 0 ? (
            <p className="mt-1 text-sm text-stone-500">Nothing on your Google Calendar that day.</p>
          ) : (
            <div className="mt-2 flex flex-col gap-2">
              {events.map((e) => (
                <article key={e.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                  <p className="text-[15px] font-semibold">{e.title}</p>
                  <p className="text-sm text-stone-500">
                    {e.start}
                    {e.end ? ` – ${e.end}` : ""}
                  </p>
                  {e.link && (
                    <a href={e.link} target="_blank" rel="noreferrer" className="text-sm font-medium text-teal-900">
                      Open in Google Calendar
                    </a>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
