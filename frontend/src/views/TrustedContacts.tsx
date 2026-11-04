import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { Notify, ToolResult } from "../api";
import type { Navigate } from "../nav";

/* Trusted people: real contacts for escalation. Naming someone here
   authorises nothing - every message goes through the approval popup. */

interface TrustedPerson {
  id: number;
  name: string;
  email: string;
  relation: string;
}

export default function TrustedContacts({ notify, navigate }: { notify: Notify; navigate: Navigate }) {
  const [rows, setRows] = useState<TrustedPerson[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [relation, setRelation] = useState("");
  const [gmailOk, setGmailOk] = useState<boolean | null>(null);
  const [tellId, setTellId] = useState<number | null>(null);
  const [subject, setSubject] = useState("Saath needs your help");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setRows(await call<TrustedPerson[]>("GET", "/trusted-contacts"));
  }

  async function loadAll() {
    try {
      await load();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not load contacts.", true);
    }
    try {
      const status = await call<{ connected: boolean }>("GET", "/gmail/status");
      setGmailOk(status.connected);
    } catch {
      setGmailOk(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadAll().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await call("POST", "/trusted-contacts", { name: name.trim(), email: email.trim(), relation: relation.trim() });
      setName("");
      setEmail("");
      setRelation("");
      await load();
      notify("Trusted person added. Saath will still ask before every message.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not add contact.", true);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    try {
      await call("DELETE", "/trusted-contacts/" + id);
      await load();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not remove contact.", true);
    }
  }

  async function tell(person: TrustedPerson) {
    if (!message.trim()) {
      notify("Write the message first.", true);
      return;
    }
    setBusy(true);
    try {
      const task = await call<{ id: number }>("POST", "/tasks", { objective: "Tell " + person.name + ": " + subject.trim() });
      await call("POST", "/tasks/" + task.id + "/transition", { to_status: "IN_PROGRESS" });
      const out = await call<ToolResult>("POST", "/tasks/" + task.id + "/tools", {
        tool: "gmail_send",
        args: { to: person.email, subject: subject.trim(), body: message.trim() },
      });
      if (out.status === "awaiting_approval") {
        notify("Check the approval popup - nothing sends without your yes.");
      } else {
        notify("Message sent and verified.");
      }
      setTellId(null);
      setMessage("");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not send.", true);
    } finally {
      setBusy(false);
    }
  }

  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3 text-[15px] text-stone-800";

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[15px] text-stone-600">
        Real people Saath may contact in a pinch. Naming someone here sends nothing - every message pops up for your
        approval first.
      </p>
      {gmailOk === false && (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4">
          <p className="text-[15px] font-semibold">Gmail is not connected</p>
          <p className="mt-1 text-sm text-stone-600">Messages send from your Gmail. Connect it first:</p>
          <button
            type="button"
            onClick={() => navigate("mylife", "gmail")}
            className="mt-2 min-h-12 rounded-xl border border-stone-300 bg-white px-4 text-[15px]"
          >
            Connect Gmail
          </button>
        </div>
      )}
      <form onSubmit={add} className="flex flex-col gap-2 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Add a trusted person</h3>
        <label className="text-sm font-medium">
          Name
          <input className={input + " mt-1 w-full"} value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="text-sm font-medium">
          Email
          <input className={input + " mt-1 w-full"} value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="text-sm font-medium">
          Relation (optional)
          <input
            className={input + " mt-1 w-full"}
            placeholder="daughter, neighbour, friend"
            value={relation}
            onChange={(e) => setRelation(e.target.value)}
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="min-h-12 rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add"}
        </button>
      </form>
      {rows.length === 0 && <p className="text-sm text-stone-500">Nobody named yet. Add one person you trust.</p>}
      {rows.map((person) => (
        <article key={person.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <p className="text-base font-semibold">
            {person.name}
            {person.relation ? <span className="font-normal text-stone-500"> · {person.relation}</span> : null}
          </p>
          <p className="text-sm text-stone-500">{person.email}</p>
          {tellId === person.id ? (
            <div className="mt-2 flex flex-col gap-2">
              <label className="text-sm font-medium">
                Subject
                <input className={input + " mt-1 w-full"} value={subject} onChange={(e) => setSubject(e.target.value)} />
              </label>
              <label className="text-sm font-medium">
                Message
                <textarea
                  className={input + " mt-1 min-h-24 w-full py-2"}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="I need help getting to the pharmacy today…"
                />
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => tell(person).catch(() => undefined)}
                  disabled={busy}
                  className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50"
                >
                  {busy ? "Sending…" : "Ask Saath to send"}
                </button>
                <button
                  type="button"
                  onClick={() => setTellId(null)}
                  className="min-h-12 rounded-xl border border-stone-300 px-4"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setTellId(person.id)}
                className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white"
              >
                Tell {person.name}
              </button>
              <button
                type="button"
                onClick={() => remove(person.id).catch(() => undefined)}
                className="min-h-12 rounded-xl border border-stone-300 px-4 text-sm"
              >
                Remove
              </button>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
