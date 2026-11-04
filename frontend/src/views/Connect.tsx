import { useState } from "react";
import type { FormEvent } from "react";
import type { Notify } from "../api";
import type { Navigate } from "../nav";
import { GROUPS, PARTNERS, SEED_MESSAGES } from "./connectDemo";
import type { DemoGroup, DemoMessage, DemoProfile } from "./connectDemo";
import Nearby from "./Nearby";
import Sub from "./Sub";

/* Practice community: every control works against on-device demo state. */

export function DemoBanner({ text }: { text: string }) {
  return (
    <p className="rounded-xl border border-dashed border-stone-400 bg-stone-100 p-3 text-sm text-stone-600">{text}</p>
  );
}

const KEY = "saath_connect";

interface ConnectStore {
  joined: string[];
  posts: Record<string, DemoMessage[]>;
  blocked: string[];
  reported: string[];
  interested: string[];
  threads: Record<string, DemoMessage[]>;
}

const EMPTY: ConnectStore = { joined: [], posts: {}, blocked: [], reported: [], interested: [], threads: {} };

function loadStore(): ConnectStore {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    return { ...EMPTY, ...(JSON.parse(raw) as Partial<ConnectStore>) };
  } catch {
    return EMPTY;
  }
}

function saveStore(next: ConnectStore) {
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode */
  }
}

const REPLIES = [
  "Thanks for sharing that! What got you interested in this?",
  "Lovely — tell me a little more about yourself.",
  "That sounds great. When do you usually find time for it?",
];

interface ConnectProps {
  notify: Notify;
  navigate: Navigate;
}

export default function Connect({ notify, navigate }: ConnectProps) {
  const [store, setStore] = useState<ConnectStore>(loadStore);
  const [tab, setTab] = useState<"groups" | "partners">("groups");
  const [area, setArea] = useState<"hub" | "communities" | "nearby">("hub");
  const [groupId, setGroupId] = useState<string | null>(null);
  const [partnerId, setPartnerId] = useState<string | null>(null);
  const [postDraft, setPostDraft] = useState("");
  const [msgDraft, setMsgDraft] = useState("");

  function commit(next: ConnectStore) {
    setStore(next);
    saveStore(next);
  }

  function toggleList(list: "joined" | "blocked" | "reported" | "interested", id: string) {
    const has = store[list].includes(id);
    commit({ ...store, [list]: has ? store[list].filter((x) => x !== id) : [...store[list], id] });
    return !has;
  }

  function sendPost(group: DemoGroup, event: FormEvent) {
    event.preventDefault();
    const text = postDraft.trim();
    if (!text) return;
    const msg: DemoMessage = { id: `m${Date.now()}`, author: "You", text, mine: true };
    commit({ ...store, posts: { ...store.posts, [group.id]: [...(store.posts[group.id] || []), msg] } });
    setPostDraft("");
    notify("Posted to " + group.name + ".");
  }

  function sendMessage(partner: DemoProfile, event: FormEvent) {
    event.preventDefault();
    const text = msgDraft.trim();
    if (!text) return;
    const mine: DemoMessage = { id: `m${Date.now()}`, author: "You", text, mine: true };
    const current = store.threads[partner.id] || [];
    commit({ ...store, threads: { ...store.threads, [partner.id]: [...current, mine] } });
    setMsgDraft("");
    const reply: DemoMessage = {
      id: `m${Date.now()}r`,
      author: partner.name,
      text: REPLIES[current.length % REPLIES.length],
      mine: false,
    };
    window.setTimeout(() => {
      setStore((prev) => {
        const next = { ...prev, threads: { ...prev.threads, [partner.id]: [...(prev.threads[partner.id] || []), reply] } };
        saveStore(next);
        return next;
      });
    }, 900);
  }

  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const tabBtn = (active: boolean) =>
    "min-h-12 flex-1 rounded-xl border px-3 text-[15px] " +
    (active ? "border-teal-800 bg-teal-800 font-semibold text-white" : "border-stone-300 bg-white");
  const tag = "inline-block rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600";

  const group = GROUPS.find((g) => g.id === groupId) || null;
  const partner = PARTNERS.find((p) => p.id === partnerId) || null;

  if (group) {
    const joined = store.joined.includes(group.id);
    const reported = store.reported.includes(group.id);
    const interested = store.interested.includes(group.id);
    const messages = [...(SEED_MESSAGES[group.id] || []), ...(store.posts[group.id] || [])];
    return (
      <Sub title={group.name} onBack={() => setGroupId(null)}>
        <div className="flex flex-col gap-3">
          <DemoBanner text="Demo group — sample members and messages, nobody real." />
          <p className="text-[15px] text-stone-600">{group.blurb} · {group.members} members</p>
          <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <h4 className="text-sm font-semibold text-stone-500">Announcement</h4>
            <p className="mt-1 text-[15px]">{group.announcement}</p>
          </section>
          <section className="flex items-center gap-2 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <span className="grow text-[15px]">{group.meetup}</span>
            <button
              type="button"
              onClick={() => {
                const now = toggleList("interested", group.id);
                notify(now ? "You're interested — added to your list." : "Interest removed.");
              }}
              aria-pressed={interested}
              className={btn + (interested ? " border-teal-800 bg-teal-50 font-semibold text-teal-900" : "")}
            >
              {interested ? "Interested ✓" : "I'm interested"}
            </button>
          </section>
          <section>
            <h4 className="text-base font-semibold">Discussion</h4>
            <ul className="mt-2 flex flex-col gap-2">
              {messages.map((m) => (
                <li key={m.id} className={"rounded-2xl border p-3 " + (m.mine ? "border-teal-200 bg-teal-50" : "border-stone-200 bg-white")}>
                  <span className="block text-sm font-semibold">{m.author} {m.mine ? "(you)" : <span className={tag}>Sample</span>}</span>
                  <span className="block text-[15px]">{m.text}</span>
                </li>
              ))}
            </ul>
            {joined ? (
              <form onSubmit={(e) => sendPost(group, e)} className="mt-2 flex gap-2">
                <input
                  className="min-h-12 grow rounded-xl border border-stone-300 bg-white px-3"
                  placeholder="Write something kind…"
                  value={postDraft}
                  onChange={(e) => setPostDraft(e.target.value)}
                  aria-label="Write to the group"
                />
                <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-4 font-medium text-white">Post</button>
              </form>
            ) : (
              <button
                type="button"
                onClick={() => {
                  toggleList("joined", group.id);
                  notify("You joined " + group.name + ".");
                }}
                className="mt-2 min-h-12 w-full rounded-xl bg-teal-800 px-4 font-medium text-white"
              >
                Join to post
              </button>
            )}
          </section>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => navigate("talk", null, { prompt: `Help me introduce myself to the ${group.name} group.`, promptId: Date.now() })}
              className={btn}
            >
              Get introduction tips
            </button>
            {reported ? (
              <button type="button" onClick={() => toggleList("reported", group.id)} className={btn}>
                Reported — undo
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  toggleList("reported", group.id);
                  notify("Reported — thank you. A live service would review this.");
                }}
                className={btn}
              >
                Report group
              </button>
            )}
          </div>
        </div>
      </Sub>
    );
  }

  if (partner) {
    const blocked = store.blocked.includes(partner.id);
    const reported = store.reported.includes(partner.id);
    const thread: DemoMessage[] = store.threads[partner.id] || [
      { id: `${partner.id}-hello`, author: partner.name, text: partner.greeting, mine: false },
    ];
    return (
      <Sub title={partner.name} onBack={() => setPartnerId(null)}>
        <div className="flex flex-col gap-3">
          <DemoBanner text="Demo conversation — this person is fictional and replies are automatic sample text." />
          {blocked ? (
            <div className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <p className="text-[15px]">You blocked {partner.name}. The conversation is hidden.</p>
              <button type="button" onClick={() => toggleList("blocked", partner.id)} className={btn + " mt-2"}>
                Unblock
              </button>
            </div>
          ) : (
            <>
              <ul className="flex flex-col gap-2">
                {thread.map((m) => (
                  <li
                    key={m.id}
                    className={
                      m.mine
                        ? "max-w-[85%] self-end rounded-2xl rounded-br-md bg-teal-800 px-3 py-2 text-white"
                        : "max-w-[85%] self-start rounded-2xl rounded-bl-md border border-stone-200 bg-white px-3 py-2 shadow-sm"
                    }
                  >
                    <span className="block whitespace-pre-wrap break-words text-[15px]">{m.text}</span>
                    {!m.mine && <span className={tag + " mt-1"}>Demo reply</span>}
                  </li>
                ))}
              </ul>
              <form onSubmit={(e) => sendMessage(partner, e)} className="flex gap-2">
                <input
                  className="min-h-12 grow rounded-xl border border-stone-300 bg-white px-3"
                  placeholder="Write a message…"
                  value={msgDraft}
                  onChange={(e) => setMsgDraft(e.target.value)}
                  aria-label="Write a message"
                />
                <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-4 font-medium text-white">Send</button>
              </form>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => {
                    toggleList("blocked", partner.id);
                    notify("Blocked " + partner.name + ".");
                  }}
                  className={btn}
                >
                  Block
                </button>
                {reported ? (
                  <button type="button" onClick={() => toggleList("reported", partner.id)} className={btn}>
                    Reported — undo
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      toggleList("reported", partner.id);
                      notify("Reported — thank you. A live service would review this.");
                    }}
                    className={btn}
                  >
                    Report
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </Sub>
    );
  }

  if (area === "nearby") {
    return (
      <Sub title="Nearby help" onBack={() => setArea("hub")}>
        <Nearby notify={notify} />
      </Sub>
    );
  }

  if (area === "hub") {
    return (
      <div className="mx-auto max-w-2xl">
        <h2 className="text-2xl font-bold">Connect</h2>
        <p className="mt-1 text-[15px] text-stone-500">People, groups, and good conversation.</p>
        <div className="mt-3">
          <DemoBanner text="Practice community — every group, person, and volunteer here is fictional sample data, stored only on this device." />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={() => setArea("communities")}
            className="flex min-h-28 flex-col items-start gap-1 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
          >
            <span className="text-base font-semibold">Communities</span>
            <span className="text-sm text-stone-500">Groups and conversation partners</span>
          </button>
          <button
            type="button"
            onClick={() => setArea("nearby")}
            className="flex min-h-28 flex-col items-start gap-1 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
          >
            <span className="text-base font-semibold">Nearby help</span>
            <span className="text-sm text-stone-500">Volunteers for everyday support</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <Sub title="Communities" onBack={() => setArea("hub")}>
    <div>
      <DemoBanner text="Practice community — every group and person here is fictional sample data, stored only on this device." />
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={() => setTab("groups")} aria-pressed={tab === "groups"} className={tabBtn(tab === "groups")}>
          Groups
        </button>
        <button type="button" onClick={() => setTab("partners")} aria-pressed={tab === "partners"} className={tabBtn(tab === "partners")}>
          Conversation partners
        </button>
      </div>

      {tab === "groups" && (
        <div className="mt-3 flex flex-col gap-2">
          {GROUPS.map((g) => {
            const joined = store.joined.includes(g.id);
            return (
              <article key={g.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <button type="button" onClick={() => setGroupId(g.id)} className="block w-full text-left">
                  <span className="block text-base font-semibold">{g.name}</span>
                  <span className="block text-sm text-stone-500">{g.blurb} · {g.members} members</span>
                </button>
                <span className="mt-2 block">
                  <button
                    type="button"
                    onClick={() => {
                      const now = toggleList("joined", g.id);
                      notify(now ? "You joined " + g.name + "." : "You left " + g.name + ".");
                    }}
                    aria-pressed={joined}
                    className={btn + (joined ? " border-teal-800 bg-teal-50 font-semibold text-teal-900" : "")}
                  >
                    {joined ? "Joined ✓" : "Join"}
                  </button>
                </span>
              </article>
            );
          })}
        </div>
      )}

      {tab === "partners" && (
        <div className="mt-3 flex flex-col gap-2">
          {PARTNERS.map((p) => {
            const blocked = store.blocked.includes(p.id);
            if (blocked) {
              return (
                <div key={p.id} className="flex items-center gap-2 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                  <span className="grow text-[15px] text-stone-500">You blocked {p.name}.</span>
                  <button type="button" onClick={() => toggleList("blocked", p.id)} className={btn}>
                    Unblock
                  </button>
                </div>
              );
            }
            return (
              <article key={p.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <div className="flex items-center gap-3">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-teal-800 text-lg font-bold text-white" aria-hidden="true">
                    {p.name.charAt(0)}
                  </span>
                  <span>
                    <span className="block text-base font-semibold">{p.name} <span className={tag}>Sample profile</span></span>
                    <span className="block text-sm text-stone-500">{p.note}</span>
                  </span>
                </div>
                <span className="mt-2 flex flex-wrap gap-1.5 text-xs">
                  {p.interests.map((i) => (
                    <span key={i} className="rounded-full bg-amber-100 px-2.5 py-0.5 font-semibold text-amber-900">{i}</span>
                  ))}
                  {p.languages.map((l) => (
                    <span key={l} className="rounded-full bg-stone-200 px-2.5 py-0.5 font-semibold text-stone-600">{l}</span>
                  ))}
                </span>
                <span className="mt-2 block">
                  <button type="button" onClick={() => setPartnerId(p.id)} className="min-h-12 w-full rounded-xl bg-teal-800 px-4 font-medium text-white">
                    Start conversation
                  </button>
                </span>
              </article>
            );
          })}
          <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <h4 className="text-base font-semibold">Staying safe</h4>
            <p className="mt-1 text-sm text-stone-600">
              Never share passwords, money, or ID documents with people you meet online. Block or report anyone who
              makes you uncomfortable — in this demo it takes effect immediately, on this device.
            </p>
          </section>
        </div>
      )}
    </div>
    </Sub>
  );
}
