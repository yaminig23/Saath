import { useState } from "react";
import type { FormEvent } from "react";
import type { Notify } from "../api";
import { VOLUNTEERS } from "./connectDemo";
import type { DemoRequest, DemoVolunteer } from "./connectDemo";
import { DemoBanner } from "./Connect";
import Sub from "./Sub";

/* Nearby help: a demo volunteer-request flow. Every profile is fictional;
no verification, rating, or live provider is ever claimed. */

const KEY = "saath_nearby";

interface NearbyStore {
  blocked: string[];
  reported: string[];
  requests: DemoRequest[];
}

const EMPTY: NearbyStore = { blocked: [], reported: [], requests: [] };

function loadStore(): NearbyStore {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    return { ...EMPTY, ...(JSON.parse(raw) as Partial<NearbyStore>) };
  } catch {
    return EMPTY;
  }
}

const SKILLS = ["All", "Technology", "Language practice", "Reading help", "Errands", "Companionship", "Forms & letters"];

export default function Nearby({ notify }: { notify: Notify }) {
  const [store, setStore] = useState<NearbyStore>(loadStore);
  const [skill, setSkill] = useState("All");
  const [screen, setScreen] = useState<"list" | "requests" | "need" | "review" | "done">("list");
  const [volunteer, setVolunteer] = useState<DemoVolunteer | null>(null);
  const [need, setNeed] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [lastRequest, setLastRequest] = useState<DemoRequest | null>(null);

  function commit(next: NearbyStore) {
    setStore(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* private mode */
    }
  }

  function toggleList(list: "blocked" | "reported", id: string) {
    const has = store[list].includes(id);
    commit({ ...store, [list]: has ? store[list].filter((x) => x !== id) : [...store[list], id] });
  }

  function startRequest(v: DemoVolunteer) {
    setVolunteer(v);
    setNeed("");
    setAgreed(false);
    setScreen("need");
  }

  function submitNeed(event: FormEvent) {
    event.preventDefault();
    if (!need.trim()) return;
    setScreen("review");
  }

  function confirm() {
    if (!volunteer) return;
    if (!agreed) {
      notify("Please tick the consent box first — nothing is shared without it.", true);
      return;
    }
    const request: DemoRequest = {
      id: `r${Date.now()}`,
      volunteerId: volunteer.id,
      volunteerName: volunteer.name,
      need: need.trim(),
      status: "sent",
      created: Date.now(),
    };
    commit({ ...store, requests: [request, ...store.requests] });
    setLastRequest(request);
    setScreen("done");
    notify("Request sent (demo).");
  }

  function cancelRequest(id: string) {
    commit({ ...store, requests: store.requests.map((r) => (r.id === id ? { ...r, status: "cancelled" } : r)) });
    notify("Request withdrawn.");
  }

  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const tag = "inline-block rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600";
  const shown = skill === "All" ? VOLUNTEERS : VOLUNTEERS.filter((v) => v.skills.includes(skill));

  if (screen === "requests") {
    return (
      <Sub title="My requests" onBack={() => setScreen("list")}>
        <DemoBanner text="Demo requests — nothing leaves this device." />
        {store.requests.length === 0 ? (
          <p className="mt-2 text-sm text-stone-500">No requests yet.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {store.requests.map((r) => (
              <li key={r.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <span className="block text-[15px] font-semibold">
                  {r.volunteerName} <span className={tag}>{r.status === "sent" ? "Sent (demo)" : "Withdrawn"}</span>
                </span>
                <span className="mt-1 block whitespace-pre-wrap break-words text-[15px]">{r.need}</span>
                <span className="mt-1 block text-sm text-stone-500">{new Date(r.created).toLocaleString()}</span>
                {r.status === "sent" && (
                  <button type="button" onClick={() => cancelRequest(r.id)} className={btn + " mt-2"}>
                    Withdraw request
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Sub>
    );
  }

  if ((screen === "need" || screen === "review" || screen === "done") && volunteer) {
    if (screen === "need") {
      return (
        <Sub title={`Ask ${volunteer.name}`} onBack={() => setScreen("list")}>
          <DemoBanner text="Demo request — nothing leaves this device." />
          <form onSubmit={submitNeed} className="mt-2 flex flex-col gap-2">
            <label className="block text-sm font-medium">
              What do you need help with?
              <textarea
                className="mt-1 min-h-28 w-full rounded-xl border border-stone-300 bg-white p-3 text-[15px]"
                placeholder="e.g. Help me video-call my grandchildren on Sunday"
                value={need}
                onChange={(e) => setNeed(e.target.value)}
                required
              />
            </label>
            <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white">
              Continue
            </button>
          </form>
        </Sub>
      );
    }
    if (screen === "review") {
      return (
        <Sub title="Check and confirm" onBack={() => setScreen("need")}>
          <div className="flex flex-col gap-3">
            <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <p className="text-[15px]"><span className="font-semibold">Volunteer:</span> {volunteer.name} (demo profile)</p>
              <p className="mt-1 whitespace-pre-wrap break-words text-[15px]"><span className="font-semibold">Your need:</span> {need}</p>
            </section>
            <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
              <p className="text-[15px]">
                Only your first name and this need would be shared. Contact details stay hidden until you agree,
                and you can withdraw any time.
              </p>
              <label className="mt-2 flex items-center gap-3 text-[15px] font-medium">
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="h-6 w-6 shrink-0" />
                I agree to share my first name and need with {volunteer.name}
              </label>
            </section>
            <button type="button" onClick={confirm} className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white">
              Send request
            </button>
          </div>
        </Sub>
      );
    }
    return (
      <Sub title="Request sent" onBack={() => setScreen("list")}>
        <div className="flex flex-col gap-3">
          <section className="rounded-2xl border border-teal-200 bg-teal-50 p-4 shadow-sm">
            <p className="text-[15px] font-medium">Your demo request is recorded on this device.</p>
            <p className="mt-1 text-sm text-stone-600">
              In the live service, {lastRequest?.volunteerName || "the volunteer"} would be notified and you could
              track replies under My requests. No real person was contacted.
            </p>
          </section>
          <button type="button" onClick={() => setScreen("requests")} className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white">
            View my requests
          </button>
          <button type="button" onClick={() => setScreen("list")} className="min-h-12 rounded-xl border border-stone-300 bg-white px-5">
            Back to volunteers
          </button>
        </div>
      </Sub>
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h2 className="text-2xl font-bold">Nearby help</h2>
      <p className="mt-1 text-[15px] text-stone-500">Human support for things AI shouldn't do alone.</p>
      <div className="mt-3">
        <DemoBanner text="Demo volunteers — fictional sample profiles, stored only on this device. No verification or ratings are claimed." />
      </div>
      <section className="mt-3 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">How it works</h3>
        <ol className="mt-1 list-decimal pl-5 text-[15px] text-stone-600">
          <li>Describe what you need.</li>
          <li>Choose a volunteer and check the details.</li>
          <li>Agree to share your first name — nothing else leaves without asking.</li>
        </ol>
      </section>
      <button
        type="button"
        onClick={() => setScreen("requests")}
        className="mt-3 min-h-12 w-full rounded-xl border border-stone-300 bg-white px-4 font-medium"
      >
        My requests ({store.requests.filter((r) => r.status === "sent").length} active)
      </button>
      <div className="mt-3 flex flex-wrap gap-2">
        {SKILLS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSkill(s)}
            aria-pressed={skill === s}
            className={
              "min-h-10 rounded-full border px-3.5 text-sm " +
              (skill === s ? "border-teal-800 bg-teal-800 font-semibold text-white" : "border-stone-300 bg-white")
            }
          >
            {s}
          </button>
        ))}
      </div>
      <div className="mt-3 flex flex-col gap-2">
        {shown.map((v) => {
          const blocked = store.blocked.includes(v.id);
          const reported = store.reported.includes(v.id);
          if (blocked) {
            return (
              <div key={v.id} className="flex items-center gap-2 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <span className="grow text-[15px] text-stone-500">You blocked {v.name}.</span>
                <button type="button" onClick={() => toggleList("blocked", v.id)} className={btn}>
                  Unblock
                </button>
              </div>
            );
          }
          return (
            <article key={v.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-teal-800 text-lg font-bold text-white" aria-hidden="true">
                  {v.name.charAt(0)}
                </span>
                <span>
                  <span className="block text-base font-semibold">{v.name} <span className={tag}>Demo profile</span></span>
                  <span className="block text-sm text-stone-500">{v.availability}</span>
                </span>
              </div>
              <p className="mt-2 text-[15px] text-stone-600">{v.about}</p>
              <span className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {v.skills.map((s) => (
                  <span key={s} className="rounded-full bg-amber-100 px-2.5 py-0.5 font-semibold text-amber-900">{s}</span>
                ))}
                {v.languages.map((l) => (
                  <span key={l} className="rounded-full bg-stone-200 px-2.5 py-0.5 font-semibold text-stone-600">{l}</span>
                ))}
              </span>
              <span className="mt-2 flex flex-wrap gap-2">
                <button type="button" onClick={() => startRequest(v)} className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white">
                  Request help
                </button>
                <button
                  type="button"
                  onClick={() => {
                    toggleList("blocked", v.id);
                    notify("Blocked " + v.name + ".");
                  }}
                  className={btn}
                >
                  Block
                </button>
                {reported ? (
                  <button type="button" onClick={() => toggleList("reported", v.id)} className={btn}>
                    Reported — undo
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      toggleList("reported", v.id);
                      notify("Reported — thank you. A live service would review this.");
                    }}
                    className={btn}
                  >
                    Report
                  </button>
                )}
              </span>
            </article>
          );
        })}
      </div>
      <section className="mt-3 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Safety first</h3>
        <p className="mt-1 text-sm text-stone-600">
          Volunteers here are samples. In the live service, Saath would show only genuinely vetted helpers, share
          contact details only with your explicit agreement, and never present anyone as a medical, financial, or
          legal professional without proof.
        </p>
      </section>
    </div>
  );
}
