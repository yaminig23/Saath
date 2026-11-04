import type { Navigate } from "../nav";
import { useSettings } from "../settings";

/* Everyday confidence: comfort controls and shortcuts that all work. */

export default function StayIndependent({ navigate }: { navigate: Navigate }) {
  const { settings, update } = useSettings();

  const sizes = [
    { value: "normal", label: "Normal" },
    { value: "large", label: "Large" },
    { value: "xl", label: "Largest" },
  ] as const;

  return (
    <div className="flex flex-col gap-4 text-[15px] leading-relaxed">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Make Saath comfortable</h3>
        <div className="mt-1 text-sm text-stone-500">Text size</div>
        <div className="mt-1 flex gap-2">
          {sizes.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => update({ textSize: s.value })}
              aria-pressed={settings.textSize === s.value}
              className={
                "min-h-12 grow rounded-xl border px-3 " +
                (settings.textSize === s.value
                  ? "border-teal-800 bg-teal-50 font-semibold text-teal-900"
                  : "border-stone-300 bg-white")
              }
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => update({ voiceReplies: !settings.voiceReplies })}
          aria-pressed={settings.voiceReplies}
          className="mt-2 min-h-12 w-full rounded-xl border border-stone-300 px-4 py-2 text-left"
        >
          Voice replies: {settings.voiceReplies ? "on" : "off"}
        </button>
        <button
          type="button"
          onClick={() => navigate("more", "settings")}
          className="mt-2 min-h-12 w-full rounded-xl border border-stone-300 px-4 py-2 text-left"
        >
          All accessibility settings
        </button>
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Daily confidence</h3>
        <div className="mt-2 flex flex-col gap-2">
          <button type="button" onClick={() => navigate("mylife", "reminders")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2 text-left">
            Set a reminder in plain words
          </button>
          <button type="button" onClick={() => navigate("mylife", "briefing")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2 text-left">
            Hear today's plan
          </button>
          <button type="button" onClick={() => navigate("more", "memories")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2 text-left">
            Review what Saath remembers
          </button>
        </div>
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">You stay in charge</h3>
        <p className="mt-1 text-stone-600">
          Saath never takes a consequential action without asking you first. Anything waiting for a decision
          appears on your home screen and under My Life → Approvals.
        </p>
      </section>
    </div>
  );
}
