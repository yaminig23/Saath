import { useState } from "react";
import { call } from "../api";
import type { Briefing, Notify } from "../api";

export default function BriefingView({ notify }: { notify: Notify }) {
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    try {
      const briefing = await call<Briefing>("POST", "/briefing");
      setSummary(briefing.summary);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Briefing failed.", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
      <h3 className="text-base font-semibold">Daily briefing</h3>
      <p className="mt-1 text-sm text-stone-500">The Daily Organiser reads your real tasks and writes today's summary.</p>
      <button
        type="button"
        onClick={load}
        disabled={busy}
        className="mt-3 min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50"
      >
        {busy ? "Writing…" : "Get today's briefing"}
      </button>
      {summary && (
        <p className="mt-3 max-h-[420px] overflow-auto whitespace-pre-wrap break-words text-[15px] leading-relaxed text-stone-800">
          {summary}
        </p>
      )}
    </section>
  );
}
