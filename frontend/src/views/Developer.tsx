import { useEffect, useState } from "react";
import { call } from "../api";
import type { Notify } from "../api";
import type { OpenTask } from "../nav";
import Demo from "./Demo";
import Failures from "./Failures";
import Sub from "./Sub";
import Worker from "./Worker";

/* Internal tools, kept out of the consumer navigation. */

const ENDPOINTS: { group: string; routes: string[] }[] = [
  { group: "App", routes: ["GET /health", "GET /docs (interactive API page)"] },
  { group: "Accounts", routes: ["POST /auth/register", "POST /auth/login", "POST /auth/logout"] },
  { group: "Talk", routes: ["POST /chat (thread_id joins a thread)"] },
  { group: "Threads", routes: ["POST /threads", "GET /threads", "GET /threads/{id}"] },
  { group: "Outcomes", routes: ["POST /outcomes/plan", "POST /outcomes/{id}/revise", "POST /outcomes/{id}/followup"] },
  { group: "Providers", routes: ["GET /providers", "POST /providers/{name}/check"] },
  { group: "Demo", routes: ["GET /demo/options", "GET /demo/bookings", "POST /demo/cancel"] },
  {
    group: "Calendar",
    routes: [
      "GET /calendar/status",
      "POST /calendar/auth-url",
      "GET /calendar/callback",
      "POST /calendar/disconnect",
      "GET /calendar/day",
    ],
  },
  {
    group: "Gmail",
    routes: [
      "GET /gmail/status",
      "POST /gmail/auth-url",
      "GET /gmail/callback",
      "POST /gmail/disconnect",
    ],
  },
  { group: "Maps", routes: ["GET /maps/places", "GET /maps/route"] },
  {
    group: "Tasks",
    routes: ["GET /tasks", "POST /tasks", "GET /tasks/{id}", "POST /tasks/{id}/transition", "POST /tasks/{id}/tools"],
  },
  { group: "Daily life", routes: ["POST /reminders", "GET /reminders/due", "POST /briefing"] },
  {
    group: "Memory",
    routes: ["GET /memories", "POST /memories", "PUT /memories/{id}", "DELETE /memories/{id}", "GET /consents", "POST /consents"],
  },
  { group: "Approvals", routes: ["GET /approvals", "POST /approvals/{id}/decide", "GET /failures"] },
  { group: "Worker", routes: ["GET /worker/status", "POST /worker/tick", "GET /notifications", "POST /notifications/{id}/read"] },
];

function ApiInfo() {
  return (
    <div className="flex flex-col gap-3 text-[15px]">
      <a href="/docs" target="_blank" rel="noreferrer" className="min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-3 font-medium text-teal-900">
        Open the interactive API page
      </a>
      {ENDPOINTS.map((g) => (
        <section key={g.group} className="rounded-xl border border-stone-200 bg-white p-4">
          <h4 className="text-base font-semibold">{g.group}</h4>
          <ul className="mt-1 flex flex-col gap-1">
            {g.routes.map((r) => (
              <li key={r}>
                <code className="text-sm">{r}</code>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

interface ProviderRow {
  name: string;
  title: string;
  capabilities: string[];
  status: string;
  detail: string;
}

const PROVIDER_PILL: Record<string, string> = {
  connected: "bg-teal-100 text-teal-900",
  available: "bg-amber-100 text-amber-900",
  missing: "bg-stone-200 text-stone-600",
  demo: "bg-sky-100 text-sky-900",
};

function Providers({ notify }: { notify: Notify }) {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [results, setResults] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setRows(await call<ProviderRow[]>("GET", "/providers"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load providers.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function check(name: string) {
    setBusy(name);
    try {
      const out = await call<{ ok: boolean; detail: string }>("POST", "/providers/" + name + "/check");
      setResults((prev) => ({ ...prev, [name]: (out.ok ? "OK: " : "Not OK: ") + out.detail }));
      await load();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Check failed.", true);
    } finally {
      setBusy(null);
    }
  }

  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-stone-500">
        Connected means verified by a live check. Available means usable but unverified. Missing lists exactly what is needed. Demo means a fictional provider for demonstration.
      </p>
      {rows.map((p) => (
        <article key={p.name} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-semibold">{p.title}</span>
            <span className={"inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold " + (PROVIDER_PILL[p.status] || "bg-stone-200")}>
              {p.status}
            </span>
            <button
              type="button"
              className={btn + " ml-auto"}
              disabled={busy === p.name}
              onClick={() => check(p.name)}
            >
              {busy === p.name ? "Checking…" : "Check"}
            </button>
          </div>
          <p className="mt-1 text-sm text-stone-500">{p.capabilities.join(" · ")}</p>
          <p className="mt-1 text-sm">{p.detail}</p>
          {results[p.name] && <p className="mt-1 whitespace-pre-wrap break-words text-sm font-medium">{results[p.name]}</p>}
        </article>
      ))}
    </div>
  );
}

export default function Developer({ notify, onOpenTask }: { notify: Notify; onOpenTask: OpenTask }) {
  const [dev, setDev] = useState<string | null>(null);

  if (dev === "worker") {
    return (
      <Sub title="Worker" onBack={() => setDev(null)}>
        <Worker notify={notify} />
      </Sub>
    );
  }
  if (dev === "failures") {
    return (
      <Sub title="Failures" onBack={() => setDev(null)}>
        <Failures notify={notify} onOpenTask={onOpenTask} />
      </Sub>
    );
  }
  if (dev === "demo") {
    return (
      <Sub title="Demo Guide" onBack={() => setDev(null)}>
        <Demo />
      </Sub>
    );
  }
  if (dev === "providers") {
    return (
      <Sub title="Providers" onBack={() => setDev(null)}>
        <Providers notify={notify} />
      </Sub>
    );
  }
  if (dev === "api") {
    return (
      <Sub title="API Info" onBack={() => setDev(null)}>
        <ApiInfo />
      </Sub>
    );
  }

  const items = [
    { key: "worker", title: "Worker", blurb: "Background passes and quiet hours" },
    { key: "failures", title: "Failure reports", blurb: "Honest error records" },
    { key: "demo", title: "Demo guide", blurb: "The six-step walkthrough" },
    { key: "providers", title: "Providers", blurb: "Integration statuses and live checks" },
    { key: "api", title: "API info", blurb: "Every endpoint, grouped" },
  ];

  return (
    <div className="flex flex-col gap-2">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => setDev(item.key)}
          className="min-h-16 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
        >
          <span className="block text-base font-semibold">{item.title}</span>
          <span className="block text-sm text-stone-500">{item.blurb}</span>
        </button>
      ))}
    </div>
  );
}
