import { useEffect, useState } from "react";
import { call } from "../api";
import type { NotificationRow, Notify } from "../api";

export default function Notifications({ notify }: { notify: Notify }) {
  const [rows, setRows] = useState<NotificationRow[]>([]);

  async function load() {
    setRows(await call<NotificationRow[]>("GET", "/notifications"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load notifications.", true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function markRead(id: number) {
    await call("POST", "/notifications/" + id + "/read");
    await load();
  }

  const btn = "min-h-10 rounded-xl border border-stone-300 bg-white px-3 py-1.5 text-sm";
  const unread = rows.filter((n) => !n.read_at).length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h3 className="text-base font-semibold">Notifications</h3>
        {unread > 0 && <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-900">{unread} new</span>}
        <button
          type="button"
          className={btn + " ml-auto"}
          onClick={() => load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Refresh failed.", true))}
        >
          Refresh
        </button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-stone-500">No notifications.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((n) => (
            <li
              key={n.id}
              className={
                "rounded-2xl border p-4 shadow-sm " +
                (n.read_at ? "border-stone-200 bg-white" : "border-amber-300 bg-amber-50")
              }
            >
              {!n.read_at && <span className="mb-1 inline-block rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-900">New</span>}
              <div className="whitespace-pre-wrap break-words text-[15px]">{n.text}</div>
              <div className="mt-1 text-sm text-stone-500">
                {n.kind}{n.task_id ? " · task " + n.task_id : ""} · {n.created_at}
              </div>
              {!n.read_at && (
                <div className="mt-2">
                  <button type="button" className={btn} onClick={() => markRead(n.id).catch((err: unknown) => notify(err instanceof Error ? err.message : "Mark-read failed.", true))}>
                    Mark read
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
