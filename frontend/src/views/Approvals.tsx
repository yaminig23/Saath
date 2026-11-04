import { useEffect, useState } from "react";
import { call } from "../api";
import type { ApprovalRow, Notify } from "../api";
import Saath from "../companion/Saath";

interface ApprovalsProps {
  notify: Notify;
  onOpenTask: (id: number) => void;
}

export default function Approvals({ notify, onOpenTask }: ApprovalsProps) {
  const [rows, setRows] = useState<ApprovalRow[]>([]);

  async function load() {
    setRows(await call<ApprovalRow[]>("GET", "/approvals"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load approvals.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function decide(id: number, approve: boolean) {
    await call("POST", "/approvals/" + id + "/decide", { approve });
    await load();
    notify(approve ? "Approval granted." : "Approval denied.");
  }

  const wrap = async (fn: () => Promise<void>) => {
    try { await fn(); } catch (err) { notify(err instanceof Error ? err.message : "Request failed.", true); }
  };
  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px]";

  const pending = rows.filter((a) => a.status === "pending");
  const decided = rows.filter((a) => a.status !== "pending");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h3 className="text-base font-semibold">Approvals</h3>
        <button type="button" className={btn + " ml-auto min-h-10 text-sm"} onClick={() => wrap(load)}>Refresh</button>
      </div>
      <p className="-mt-1 text-sm text-stone-500">Consequential actions pause here. Nothing runs until you say yes.</p>

      {pending.length === 0 ? (
        <p className="text-sm text-stone-500">Nothing waiting for approval.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3 rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
            <Saath state="approval" size={72} />
            <p className="text-[15px] font-medium">Saath needs your yes before continuing.</p>
          </div>
          {pending.map((a) => (
            <article key={a.id} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <div className="text-[15px] font-semibold">#{a.id} · {a.tool}</div>
              <div className="mt-1 text-sm text-stone-500">Task {a.task_id}{a.note ? ` · ${a.note}` : ""}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="min-h-12 grow rounded-xl bg-teal-800 px-4 py-2 font-medium text-white" onClick={() => wrap(() => decide(a.id, true))}>
                  Approve
                </button>
                <button type="button" className={btn + " grow"} onClick={() => wrap(() => decide(a.id, false))}>
                  Deny
                </button>
                <button type="button" className={btn + " grow"} onClick={() => onOpenTask(a.task_id)}>
                  Open task
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {decided.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-stone-500">Earlier decisions</h4>
          <ul className="mt-1 flex flex-col gap-2">
            {decided.map((a) => (
              <li key={a.id} className="rounded-xl border border-stone-200 bg-white p-3 text-sm">
                <span className="font-medium">#{a.id} · {a.tool}</span> <span className="text-stone-500">({a.status})</span>
                <span className="block whitespace-pre-wrap break-words text-stone-600">{a.result || a.note || "—"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
