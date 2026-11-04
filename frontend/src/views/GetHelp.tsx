import { useRef } from "react";
import type { Navigate } from "../nav";

/* Real help: example prompts that actually send, tips, shortcuts, emergencies. */

export default function GetHelp({ navigate }: { navigate: Navigate }) {
  const examples = [
    "I need to go to a pharmacy, but I don't know where to go",
    "Remind me to drink water tomorrow at 9am",
    "Give me my briefing",
    "What do you remember about me?",
  ];
  const promptCount = useRef(0);

  function tryIt(prompt: string) {
    promptCount.current += 1;
    navigate("talk", null, { prompt, promptId: promptCount.current });
  }

  return (
    <div className="flex flex-col gap-4 text-[15px] leading-relaxed">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Talk to Saath</h3>
        <p className="mt-1 text-stone-600">Just say what you need in your own words. Try one:</p>
        <div className="mt-2 flex flex-col gap-2">
          {examples.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => tryIt(prompt)}
              className="min-h-12 rounded-xl border border-stone-300 bg-amber-50 px-4 py-2 text-left"
            >
              “{prompt}”
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Using your voice</h3>
        <ul className="mt-1 list-disc pl-5 text-stone-600">
          <li>Tap the microphone and speak clearly; tap again to stop.</li>
          <li>Saath reads replies aloud when voice replies are on.</li>
          <li>Voice uses your browser's built-in speech — nothing extra to install.</li>
        </ul>
        <button
          type="button"
          onClick={() => navigate("more", "settings")}
          className="mt-2 min-h-12 rounded-xl border border-stone-300 px-4 py-2"
        >
          Voice and text settings
        </button>
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">People who can help</h3>
        <p className="mt-1 text-stone-600">
          Name real people Saath may message in a pinch. Every message pops up for your approval first.
        </p>
        <button
          type="button"
          onClick={() => navigate("more", "trusted")}
          className="mt-2 min-h-12 rounded-xl bg-teal-800 px-4 font-medium text-white"
        >
          My trusted people
        </button>
      </section>

      <section className="rounded-2xl border border-red-200 bg-red-50 p-4 shadow-sm">
        <h3 className="text-base font-semibold text-red-900">Emergency</h3>
        <p className="mt-1 text-stone-600">
          Saath cannot call anyone for you. In an emergency, call your local emergency number directly
          and follow their instructions.
        </p>
        <a
          href="tel:112"
          className="mt-2 inline-block min-h-12 rounded-xl bg-red-700 px-5 py-3 font-semibold text-white"
        >
          Call 112 now (India)
        </a>
        <p className="mt-1 text-xs text-stone-500">Your phone places the call — Saath only opens the dialer.</p>
      </section>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h3 className="text-base font-semibold">Shortcuts</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" onClick={() => navigate("learn")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2">
            Learn something
          </button>
          <button type="button" onClick={() => navigate("mylife")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2">
            Manage my life
          </button>
          <button type="button" onClick={() => navigate("more", "independent")} className="min-h-12 rounded-xl border border-stone-300 px-4 py-2">
            Stay independent
          </button>
        </div>
      </section>
    </div>
  );
}
