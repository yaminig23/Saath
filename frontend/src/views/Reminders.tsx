import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { Notify, TaskRow } from "../api";

function tomorrow9UTC(): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + 1);
  d.setUTCHours(9, 0, 0, 0);
  return d.toISOString().slice(0, 19) + "+00:00";
}

interface RemindersProps {
  notify: Notify;
  onOpenTask: (id: number) => void;
}

export default function Reminders({ notify, onOpenTask }: RemindersProps) {
  const [message, setMessage] = useState("");
  const [deadline, setDeadline] = useState(tomorrow9UTC());
  const [priority, setPriority] = useState("normal");
  const [due, setDue] = useState<TaskRow[]>([]);

  async function load() {
    setDue(await call<TaskRow[]>("GET", "/reminders/due"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load due reminders.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    await call("POST", "/reminders", { message: message.trim(), deadline: deadline.trim(), priority });
    setMessage("");
    await load();
    notify("Reminder set.");
  }

  const wrap = async (fn: () => Promise<void>) => {
    try { await fn(); } catch (err) { notify(err instanceof Error ? err.message : "Request failed.", true); }
  };
  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">New reminder</h3>
        <form onSubmit={(e) => wrap(() => create(e))} className="mt-2 flex flex-col gap-2">
          <label className="block text-sm font-medium">
            Remind me to…
            <input className={input + " mt-1 w-full"} placeholder="e.g. call Mum" value={message} onChange={(e) => setMessage(e.target.value)} required />
          </label>
          <div className="flex flex-wrap gap-2">
            <label className="grow text-sm font-medium">
              Day and time, UTC
              <input className={input + " mt-1 w-full"} value={deadline} onChange={(e) => setDeadline(e.target.value)} required />
            </label>
            <label className="text-sm font-medium">
              Priority
              <select className={input + " mt-1 w-full"} value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </label>
          </div>
          <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white">Set reminder</button>
        </form>
        <p className="mt-2 text-xs text-stone-500">Tip: you can also just tell Saath — “remind me to stretch tomorrow at 9am”.</p>
      </section>

      <section>
        <div className="flex items-center gap-3">
          <h3 className="text-base font-semibold">Due now</h3>
          <button type="button" className={btn + " ml-auto"} onClick={() => wrap(load)}>Refresh</button>
        </div>
        {due.length === 0 ? (
          <p className="mt-2 text-sm text-stone-500">Nothing due. Enjoy the quiet.</p>
        ) : (
          <div className="mt-2 flex flex-col gap-2">
            {due.map((t) => (
              <article key={t.id} className="flex items-center gap-3 rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
                <span className="grow">
                  <span className="block text-[15px] font-medium">{t.objective}</span>
                  <span className="block text-sm text-stone-500">Was due {t.deadline}</span>
                </span>
                <button type="button" className={btn + " shrink-0"} onClick={() => onOpenTask(t.id)}>Open task</button>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
