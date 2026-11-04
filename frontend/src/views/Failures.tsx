import { useEffect, useState } from "react";
import { call } from "../api";
import type { FailureRow, Notify } from "../api";

interface FailuresProps {
  notify: Notify;
  onOpenTask: (id: number) => void;
}

export default function Failures({ notify, onOpenTask }: FailuresProps) {
  const [rows, setRows] = useState<FailureRow[]>([]);

  async function load() {
    setRows(await call<FailureRow[]>("GET", "/failures"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load failures.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const btn = "rounded-md border border-slate-300 bg-slate-100 px-3 py-1 text-sm";

  return (
    <div>
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">Failures</h2>
        <button type="button" className={btn} onClick={() => load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Refresh failed.", true))}>Refresh</button>
      </div>
      <p className="mt-1 text-sm text-slate-500">Blocks, errors, and guardrail reports. Failures never masquerade as completions.</p>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">All clear — no failures recorded.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-3">
          {rows.map((f) => (
            <li key={f.id} className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
              <div className="font-semibold">Task {f.task_id} · {f.agent || "?"} · {f.tool || "?"}</div>
              <div className="mt-1 whitespace-pre-wrap break-words text-red-900">{f.error}</div>
              <div className="mt-1 text-[13px] text-slate-500">{f.created_at}</div>
              <div className="mt-2">
                <button type="button" className={btn} onClick={() => onOpenTask(f.task_id)}>Open task</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
