import { useEffect, useState } from "react";
import { call } from "../api";
import type { Notify, WorkerStatus } from "../api";

export default function Worker({ notify }: { notify: Notify }) {
  const [status, setStatus] = useState<WorkerStatus | null>(null);

  async function load() {
    setStatus(await call<WorkerStatus>("GET", "/worker/status"));
  }

  useEffect(() => {
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load worker status.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function tick() {
    await call("POST", "/worker/tick");
    await load();
    notify("Worker ran one pass over every user.");
  }

  const wrap = async (fn: () => Promise<void>) => {
    try { await fn(); } catch (err) { notify(err instanceof Error ? err.message : "Request failed.", true); }
  };
  const btn = "rounded-md border border-slate-300 bg-slate-100 px-3 py-1.5 text-sm";

  return (
    <div>
      <h2 className="text-xl font-semibold">Worker</h2>
      <p className="mt-1 text-sm text-slate-500">The background worker wakes sleepers, fires due reminders, and notices. Tap “Tick now” to run one pass on demand.</p>
      <div className="mt-3 flex gap-2">
        <button type="button" className={btn} onClick={() => wrap(load)}>Refresh</button>
        <button type="button" className={btn} onClick={() => wrap(tick)}>Tick now</button>
      </div>
      {status && (
        <pre className="mt-3 max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-4 text-[13px] text-slate-100">
          {JSON.stringify(status, null, 2)}
        </pre>
      )}
    </div>
  );
}
