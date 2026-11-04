import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { Notify, TaskDetail, TaskRow, ToolResult } from "../api";

// Mirrors the kernel's legal moves. AWAITING_USER is tool-driven on purpose.
const NEXT_STATUS: Record<string, string[]> = {
  PENDING: ["IN_PROGRESS", "BLOCKED"],
  IN_PROGRESS: ["COMPLETED", "BLOCKED", "FAILED"],
  AWAITING_USER: ["IN_PROGRESS", "BLOCKED", "FAILED"],
  BLOCKED: ["PENDING", "IN_PROGRESS", "FAILED"],
  FAILED: ["PENDING"],
  COMPLETED: [],
};

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Pending",
  IN_PROGRESS: "In progress",
  AWAITING_USER: "Awaiting you",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  COMPLETED: "Completed",
};

const PILL: Record<string, string> = {
  PENDING: "bg-stone-200 text-stone-700",
  IN_PROGRESS: "bg-sky-100 text-sky-900",
  AWAITING_USER: "bg-amber-100 text-amber-900",
  BLOCKED: "bg-orange-100 text-orange-900",
  FAILED: "bg-red-100 text-red-900",
  COMPLETED: "bg-teal-100 text-teal-900",
};

export function StatusPill({ status }: { status: string }) {
  return <span className={"inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold " + (PILL[status] || "bg-stone-200")}>{STATUS_LABEL[status] || status}</span>;
}

interface TasksProps {
  notify: Notify;
  focusId: number | null;
  onFocused: () => void;
  onChanged: () => void;
}

export default function Tasks({ notify, focusId, onFocused, onChanged }: TasksProps) {
  const [rows, setRows] = useState<TaskRow[]>([]);
  const [activeOnly, setActiveOnly] = useState(false);
  const [objective, setObjective] = useState("");
  const [priority, setPriority] = useState("normal");
  const [deadline, setDeadline] = useState("");
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [note, setNote] = useState("");
  const [result, setResult] = useState("");
  const [evidence, setEvidence] = useState("");
  const [tool, setTool] = useState("task_stats");
  const [toolOut, setToolOut] = useState("");

  async function load() {
    setRows(await call<TaskRow[]>("GET", "/tasks" + (activeOnly ? "?active_only=true" : "")));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load tasks.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOnly]);

  useEffect(() => {
    if (focusId !== null) {
      showDetail(focusId).catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not open task.", true));
      onFocused();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);

  async function showDetail(id: number) {
    setDetail(await call<TaskDetail>("GET", "/tasks/" + id));
    setNote("");
    setResult("");
    setEvidence("");
    setToolOut("");
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    await call("POST", "/tasks", { objective: objective.trim(), priority, deadline: deadline.trim() });
    setObjective("");
    setDeadline("");
    setDetail(null);
    await load();
    notify("Task created.");
    onChanged();
  }

  async function move(id: number, to: string) {
    await call("POST", "/tasks/" + id + "/transition", { to_status: to, note, result, evidence });
    await load();
    await showDetail(id);
    notify("Task moved to " + (STATUS_LABEL[to] || to) + ".");
    onChanged();
  }

  async function runTool(id: number) {
    const out = await call<ToolResult>("POST", "/tasks/" + id + "/tools", { tool, args: {} });
    setToolOut(JSON.stringify(out, null, 2));
    if (out.status === "awaiting_approval") notify("Approval requested — check Home or My Life → Approvals.");
    else notify("Tool ran.");
    await load();
    await showDetail(id);
    onChanged();
  }

  const done = rows.filter((t) => t.status === "COMPLETED").length;
  const pct = rows.length === 0 ? 0 : Math.round((done / rows.length) * 100);
  const wrap = async (fn: () => Promise<void>) => {
    try { await fn(); } catch (err) { notify(err instanceof Error ? err.message : "Request failed.", true); }
  };
  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const primary = "min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white";

  return (
    <div className="flex flex-col gap-3">
      {rows.length > 0 && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <div className="text-sm text-stone-600">{done} of {rows.length} completed</div>
          <div className="mt-1 h-2.5 overflow-hidden rounded-full bg-stone-200">
            <div className="h-full bg-teal-700" style={{ width: pct + "%" }} />
          </div>
        </section>
      )}

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">New task</h3>
        <form onSubmit={(e) => wrap(() => create(e))} className="mt-2 flex flex-col gap-2">
          <label className="block text-sm font-medium">
            What needs doing?
            <input className={input + " mt-1 w-full"} placeholder="e.g. Plan Saturday's picnic" value={objective} onChange={(e) => setObjective(e.target.value)} required />
          </label>
          <div className="flex flex-wrap gap-2">
            <label className="grow text-sm font-medium">
              Priority
              <select className={input + " mt-1 w-full"} value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </label>
            <label className="grow text-sm font-medium">
              Deadline, UTC (optional)
              <input className={input + " mt-1 w-full"} placeholder="2026-11-05T09:00:00+00:00" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
            </label>
          </div>
          <button type="submit" className={primary + " mt-1"}>Create task</button>
        </form>
      </section>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setActiveOnly((v) => !v)}
          aria-pressed={activeOnly}
          className={btn + (activeOnly ? " border-teal-800 bg-teal-50 font-semibold text-teal-900" : "")}
        >
          {activeOnly ? "Showing active only" : "Show active only"}
        </button>
        <button type="button" className={btn} onClick={() => wrap(load)}>Refresh</button>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-stone-500">No tasks yet. Create one above, or ask Saath and let it work.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => wrap(() => showDetail(t.id))}
              className="w-full rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
            >
              <span className="block text-[15px] font-medium">{t.objective}</span>
              <span className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-stone-500">
                <StatusPill status={t.status} />
                <span>#{t.id}</span>
                <span>{t.priority}</span>
                {t.deadline && <span>due {t.deadline}</span>}
              </span>
            </button>
          ))}
        </div>
      )}

      {detail && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold">Task {detail.task.id}</h3>
            <StatusPill status={detail.task.status} />
            <button type="button" className={btn + " ml-auto"} onClick={() => setDetail(null)}>Close</button>
          </div>
          <dl className="mt-2 flex flex-col gap-1.5 text-[15px]">
            <div><dt className="text-sm text-stone-500">Objective</dt><dd className="whitespace-pre-wrap break-words">{detail.task.objective}</dd></div>
            <div className="flex flex-wrap gap-x-6 gap-y-1.5">
              <div><dt className="text-sm text-stone-500">Priority</dt><dd>{detail.task.priority}</dd></div>
              <div><dt className="text-sm text-stone-500">Deadline</dt><dd>{detail.task.deadline || "-"}</dd></div>
              <div><dt className="text-sm text-stone-500">Agent</dt><dd>{detail.task.agent || "-"}</dd></div>
              <div><dt className="text-sm text-stone-500">Depends on</dt><dd>{detail.task.dependencies.join(", ") || "-"}</dd></div>
            </div>
            <div><dt className="text-sm text-stone-500">Result</dt><dd className="whitespace-pre-wrap break-words">{detail.task.result || "-"}</dd></div>
            <div><dt className="text-sm text-stone-500">Evidence</dt><dd className="whitespace-pre-wrap break-words">{detail.task.evidence || "-"}</dd></div>
          </dl>

          <h4 className="mt-3 text-sm font-semibold">History</h4>
          <ul className="mt-1 flex flex-col gap-1.5">
            {detail.events.map((e) => (
              <li key={e.id} className="flex gap-2 text-sm text-stone-600">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-teal-700" aria-hidden="true" />
                <span>
                  {e.event_type}{(e.from_status || e.to_status) ? ` ${e.from_status} → ${e.to_status}` : ""} — {e.note} <span className="text-stone-400">({e.created_at})</span>
                </span>
              </li>
            ))}
          </ul>

          {(NEXT_STATUS[detail.task.status] || []).length > 0 && (
            <div className="mt-3 flex flex-col gap-2">
              <label className="block text-sm font-medium">
                Note (optional)
                <input className={input + " mt-1 w-full"} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="flex flex-wrap gap-2">
                <label className="grow text-sm font-medium">
                  Result (for completions)
                  <input className={input + " mt-1 w-full"} value={result} onChange={(e) => setResult(e.target.value)} />
                </label>
                <label className="grow text-sm font-medium">
                  Evidence
                  <input className={input + " mt-1 w-full"} value={evidence} onChange={(e) => setEvidence(e.target.value)} />
                </label>
              </div>
              <div className="flex flex-wrap gap-2">
                {(NEXT_STATUS[detail.task.status] || []).map((target) => (
                  <button key={target} type="button" className={btn} onClick={() => wrap(() => move(detail.task.id, target))}>
                    {STATUS_LABEL[target]}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="grow text-sm font-medium">
              Tool
              <select className={input + " mt-1 w-full"} value={tool} onChange={(e) => setTool(e.target.value)}>
                <option value="task_stats">Count my tasks (runs now)</option>
                <option value="export_calendar">Export reminders file (needs approval)</option>
              </select>
            </label>
            <button type="button" className={btn} onClick={() => wrap(() => runTool(detail.task.id))}>Run tool</button>
          </div>
          {toolOut && <pre className="mt-2 max-h-56 overflow-auto rounded-xl bg-stone-900 p-3 text-[13px] text-stone-100">{toolOut}</pre>}
        </section>
      )}
    </div>
  );
}
