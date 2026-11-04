import { useEffect, useRef } from "react";
import Saath from "../companion/Saath";
import { speak, speechOutputSupported, stopSpeaking } from "../voice";

interface OnboardingProps {
  onEnableVoice: () => void;
  onTypeInstead: () => void;
}

const INTRO =
  "Hi! I'm Saath, your everyday companion. Here's what you need to know. " +
  "One: tap the Enable voice button once. Then just say Hi Saath anytime, hands free — or type below. Both work. " +
  "Two: I can remind you, plan your day, teach with videos, and help you book things. Just ask in plain words. " +
  "Three: I always ask before booking, paying, or messaging anyone. Nothing happens without your yes.";

/* First-run welcome: Saath itself explains voice + typing + approvals aloud,
   so visually impaired users discover everything without reading. */
export default function Onboarding({ onEnableVoice, onTypeInstead }: OnboardingProps) {
  const spokenRef = useRef(false);

  useEffect(() => {
    if (!spokenRef.current && speechOutputSupported()) {
      spokenRef.current = true;
      speak(INTRO);
    }
    return () => stopSpeaking();
  }, []);

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label="Welcome to Saath">
      <div className="w-full max-w-md rounded-2xl border border-stone-200 bg-white p-6 text-center shadow-xl">
        <div className="flex justify-center">
          <Saath state="excited" size={140} />
        </div>
        <h2 className="mt-2 text-2xl font-bold">Meet Saath</h2>
        <p className="mt-1 text-[15px] text-stone-600">Your companion for everyday life.</p>
        <ol className="mt-4 flex flex-col gap-2 text-left text-[15px]">
          <li className="rounded-xl bg-stone-100 p-3">
            <strong>Talk or type.</strong> Enable voice once, then say <strong>Hi Saath</strong> anytime — or type below.
          </li>
          <li className="rounded-xl bg-stone-100 p-3">
            <strong>Ask for anything.</strong> Reminders, plans, video lessons, bookings — in plain words.
          </li>
          <li className="rounded-xl bg-stone-100 p-3">
            <strong>You stay in charge.</strong> Saath always asks before booking, paying, or messaging.
          </li>
        </ol>
        <div className="mt-4 flex flex-col gap-2">
          <button
            type="button"
            onClick={onEnableVoice}
            className="min-h-14 w-full rounded-xl bg-teal-800 px-4 text-lg font-semibold text-white"
          >
            Enable voice
          </button>
          <button
            type="button"
            onClick={onTypeInstead}
            className="min-h-12 w-full rounded-xl border border-stone-300 bg-white px-4 text-[15px]"
          >
            I&apos;ll type instead
          </button>
        </div>
      </div>
    </div>
  );
}
