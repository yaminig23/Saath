import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { ConsentRow, MemoryRow, Notify } from "../api";

/* What Saath remembers, in plain language: sharing switches, grouped memories, no database jargon. */

const CATEGORIES: { key: string; blurb: string }[] = [
  { key: "food", blurb: "Meals and tastes" },
  { key: "health", blurb: "Health topics" },
  { key: "family", blurb: "Family" },
  { key: "work", blurb: "Work" },
  { key: "study", blurb: "Study" },
  { key: "routine", blurb: "Routines" },
  { key: "general", blurb: "Anything else" },
];

const KIND_LABEL: Record<string, string> = {
  skill: "Skill",
  preference: "Preference",
  fact: "Fact",
  event: "Event",
  plan: "Plan",
};

const TIER_LABEL: Record<string, string> = {
  casual: "Everyday",
  trusted: "Private",
  sensitive: "Sensitive",
};

const TIER_PILL: Record<string, string> = {
  casual: "bg-stone-200 text-stone-700",
  trusted: "bg-sky-100 text-sky-900",
  sensitive: "bg-red-100 text-red-900",
};

interface MemoriesProps {
  notify: Notify;
  onChanged: () => void;
}

export default function Memories({ notify, onChanged }: MemoriesProps) {
  const [rows, setRows] = useState<MemoryRow[]>([]);
  const [kind, setKind] = useState("preference");
  const [label, setLabel] = useState("");
  const [details, setDetails] = useState("");
  const [topic, setTopic] = useState("general");
  const [consents, setConsents] = useState<ConsentRow[]>([]);
  const [editing, setEditing] = useState<{ id: number; value: string } | null>(null);

  async function load() {
    setConsents(await call<ConsentRow[]>("GET", "/consents"));
    setRows(await call<MemoryRow[]>("GET", "/memories?limit=50"));
  }

  useEffect(() => {
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load memories.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    await call("POST", "/memories", { layer: kind, key: label.trim(), value: details.trim(), category: topic });
    setLabel("");
    setDetails("");
    await load();
    notify("Saath will remember that.");
  }

  async function saveEdit() {
    if (!editing) return;
    await call("PUT", "/memories/" + editing.id, { value: editing.value });
    setEditing(null);
    await load();
    notify("Memory updated.");
  }

  async function remove(id: number) {
    await call("DELETE", "/memories/" + id);
    await load();
    notify("Memory deleted.");
  }

  async function setConsent(category: string, granted: boolean) {
    await call("POST", "/consents", { category, granted });
    await load();
    notify(granted ? `Saath may now use your ${category} memories.` : `Saath will no longer use your ${category} memories.`);
    onChanged();
  }

  const wrap = async (fn: () => Promise<void>) => {
    try { await fn(); } catch (err) { notify(err instanceof Error ? err.message : "Request failed.", true); }
  };
  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const consentFor = (cat: string) => consents.find((c) => c.category === cat)?.granted;

  const groups = CATEGORIES.map((c) => ({ ...c, items: rows.filter((r) => r.category === c.key) }));
  const others = rows.filter((r) => !CATEGORIES.some((c) => c.key === r.category));

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Sharing permissions</h3>
        <p className="mt-1 text-sm text-stone-500">Choose which topics Saath's helpers may use. Health and family stay locked until you allow them.</p>
        <div className="mt-2 flex flex-col gap-2">
          {CATEGORIES.map((c) => {
            const granted = consentFor(c.key);
            const allowed = granted === true;
            return (
              <div key={c.key} className="flex min-h-14 items-center gap-3 rounded-xl border border-stone-200 px-3 py-2">
                <span className="grow">
                  <span className="block text-[15px] font-medium capitalize">{c.key}</span>
                  <span className="block text-sm text-stone-500">
                    {c.blurb} · {granted === undefined ? "Not set" : allowed ? "Allowed" : "Not allowed"}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => wrap(() => setConsent(c.key, !allowed))}
                  aria-pressed={allowed}
                  className={
                    "min-h-10 shrink-0 rounded-xl border px-4 text-sm font-medium " +
                    (allowed ? "border-teal-800 bg-teal-800 text-white" : "border-stone-300 bg-white")
                  }
                >
                  {allowed ? "Allowed" : "Allow"}
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <div className="flex items-center gap-3">
          <h3 className="text-base font-semibold">Remembered</h3>
          <button type="button" className={btn + " ml-auto"} onClick={() => wrap(load)}>Refresh</button>
        </div>
        {rows.length === 0 ? (
          <p className="mt-2 text-sm text-stone-500">Nothing remembered yet. Teach Saath something below.</p>
        ) : (
          <div className="mt-2 flex flex-col gap-3">
            {groups.filter((g) => g.items.length > 0).map((g) => (
              <div key={g.key}>
                <h4 className="text-sm font-semibold capitalize text-stone-500">{g.key}</h4>
                <div className="mt-1 flex flex-col gap-2">
                  {g.items.map((m) => (
                    <article key={m.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                      {editing?.id === m.id ? (
                        <span className="flex flex-col gap-2">
                          <input className={input + " w-full"} value={editing.value} onChange={(e) => setEditing({ id: m.id, value: e.target.value })} aria-label="Memory details" />
                          <span className="flex gap-2">
                            <button type="button" className={btn} onClick={() => wrap(saveEdit)}>Save</button>
                            <button type="button" className={btn} onClick={() => setEditing(null)}>Cancel</button>
                          </span>
                        </span>
                      ) : (
                        <span>
                          <span className="block text-[15px] font-medium whitespace-pre-wrap break-words">{m.value}</span>
                          <span className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-stone-500">
                            <span>{m.key}</span>
                            <span>{KIND_LABEL[m.layer] || m.layer}</span>
                            <span className={"inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold " + (TIER_PILL[m.tier] || "bg-stone-200")}>
                              {TIER_LABEL[m.tier] || m.tier}
                            </span>
                          </span>
                          <span className="mt-2 flex gap-2">
                            <button type="button" className={btn} onClick={() => setEditing({ id: m.id, value: m.value })}>Correct</button>
                            <button type="button" className={btn} onClick={() => wrap(() => remove(m.id))}>Forget</button>
                          </span>
                        </span>
                      )}
                    </article>
                  ))}
                </div>
              </div>
            ))}
            {others.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-stone-500">Other</h4>
                <div className="mt-1 flex flex-col gap-2">
                  {others.map((m) => (
                    <article key={m.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                      <span className="block text-[15px] font-medium whitespace-pre-wrap break-words">{m.value}</span>
                      <span className="mt-2 flex gap-2">
                        <button type="button" className={btn} onClick={() => setEditing({ id: m.id, value: m.value })}>Correct</button>
                        <button type="button" className={btn} onClick={() => wrap(() => remove(m.id))}>Forget</button>
                      </span>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Teach Saath something</h3>
        <form onSubmit={(e) => wrap(() => create(e))} className="mt-2 flex flex-col gap-2">
          <div className="flex flex-wrap gap-2">
            <label className="grow text-sm font-medium">
              Topic
              <select className={input + " mt-1 w-full"} value={topic} onChange={(e) => setTopic(e.target.value)}>
                {CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.key}</option>)}
              </select>
            </label>
            <label className="grow text-sm font-medium">
              Kind
              <select className={input + " mt-1 w-full"} value={kind} onChange={(e) => setKind(e.target.value)}>
                {Object.entries(KIND_LABEL).map(([value, labelText]) => <option key={value} value={value}>{labelText}</option>)}
              </select>
            </label>
          </div>
          <label className="block text-sm font-medium">
            Short label
            <input className={input + " mt-1 w-full"} placeholder="e.g. wake-up time" value={label} onChange={(e) => setLabel(e.target.value)} required />
          </label>
          <label className="block text-sm font-medium">
            Details
            <input className={input + " mt-1 w-full"} placeholder="e.g. 6:30 in the morning" value={details} onChange={(e) => setDetails(e.target.value)} required />
          </label>
          <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white">Remember this</button>
        </form>
      </section>
    </div>
  );
}
