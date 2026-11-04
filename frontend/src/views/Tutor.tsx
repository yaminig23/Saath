import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { Notify } from "../api";
import Saath from "../companion/Saath";
import type { Navigate, OpenTask } from "../nav";
import { readPhoto } from "../photo";
import { useSettings } from "../settings";
import { speak, speechOutputSupported, stopSpeaking } from "../voice";
import VideoLessons from "./VideoLessons";

/* Visual Tutor: real Muse-planned lessons, real AI illustrations, real photo questions. */

interface TutorStep {
  title: string;
  text: string;
  image_prompt: string;
}

interface Lesson {
  task_id: number;
  topic: string;
  steps: TutorStep[];
  follow_up: string;
  model: string;
  vision_used: boolean;
  image_note: string;
}

const GUIDE: { title: string; body: string[] }[] = [
  {
    title: "Talking to Saath",
    body: [
      "The Talk tab is the front door. Type or speak a request in plain words — “remind me to call Mum tomorrow at 6pm”, or a combined one like “remind me to drink water at 9am and give me my briefing”.",
      "Muse reads your message and plans the work: what you asked, which specialist should do it, and in what order. The reply shows which model understood you and the task number it created.",
      "You can also ask questions — “what do you remember about my routine?” — and Saath answers only from memories you have permitted it to read.",
      "The microphone talks to Saath with your voice, and voice replies read the answers back. Both use your browser's built-in speech services; nothing is uploaded anywhere extra.",
    ],
  },
  {
    title: "Tasks and approvals",
    body: [
      "Every request becomes a real task with a number, a status, and an audit trail you can open under My Life → Tasks. Nothing is simulated: a reminder exists only when its row exists.",
      "Tasks move through Pending → In progress → Completed. If something cannot proceed, it says Blocked or Failed honestly — never a fake completion.",
      "Harmless tools run immediately, but anything consequential pauses at “Awaiting you” and waits under My Life → Approvals. Tap Approve to let it run, or Deny to stop it. Each approval works once and then expires.",
      "Try it: open any task, run the Export reminders file tool, then watch it wait in Approvals until you decide.",
    ],
  },
  {
    title: "Memory and privacy",
    body: [
      "More → What Saath Remembers holds what Saath remembers: skills, preferences, facts, events, and plans. Each memory has a topic and a trust tier.",
      "Health and family memories are sensitive: Saath's helpers cannot use them until you allow that topic. Change any switch at any time.",
      "The background worker obeys the same rules — without permission it skips your sensitive memories and says so, rather than peeking.",
      "You can correct or forget any memory. Forgetting is immediate and permanent.",
    ],
  },
  {
    title: "A day with Saath",
    body: [
      "Morning: your Home screen shows the briefing, what's due, and anything waiting for approval.",
      "Through the day: set reminders from My Life, or just ask in Talk. Due reminders appear on Home.",
      "Overnight: the background worker keeps watch but stays silent during quiet hours, delivering anything held back in the morning.",
      "Notifications collect everything the worker sends. Mark each read when you've seen it.",
    ],
  },
];



interface TutorProps {
  notify: Notify;
  navigate: Navigate;
  onOpenTask: OpenTask;
}

type Mode = "teach" | "photo" | "video" | "guide";

export default function Tutor({ notify, navigate, onOpenTask }: TutorProps) {
  const { settings } = useSettings();
  const [mode, setMode] = useState<Mode>("teach");
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");
  const [teachPhoto, setTeachPhoto] = useState("");
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [pictures, setPictures] = useState<Record<number, string>>({});
  const [pictureBusy, setPictureBusy] = useState(false);
  const [lookPhoto, setLookPhoto] = useState("");
  const [question, setQuestion] = useState("");
  const [lookBusy, setLookBusy] = useState(false);
  const [lookResult, setLookResult] = useState<{ task_id: number; description: string; model: string } | null>(null);
  const fileTeach = useRef<HTMLInputElement>(null);
  const fileLook = useRef<HTMLInputElement>(null);
  const canSpeak = speechOutputSupported();

  useEffect(() => () => stopSpeaking(), []);

  async function attach(file: File | undefined, where: "teach" | "look") {
    if (!file) return;
    try {
      const dataUrl = await readPhoto(file);
      if (where === "teach") setTeachPhoto(dataUrl);
      else setLookPhoto(dataUrl);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not read that photo.", true);
    }
  }

  async function plan(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    stopSpeaking();
    setReading(false);
    try {
      const data = await call<Lesson>("POST", "/tutor/plan", {
        topic: topic.trim(),
        context: context.trim(),
        image_data_url: teachPhoto,
      });
      setLesson(data);
      setStepIdx(0);
      setPictures({});
    } catch (err) {
      notify(err instanceof Error ? err.message : "Lesson failed.", true);
    } finally {
      setBusy(false);
    }
  }

  async function askPhoto(event: FormEvent) {
    event.preventDefault();
    setLookBusy(true);
    try {
      const data = await call<{ task_id: number; description: string; model: string }>("POST", "/tutor/look", {
        image_data_url: lookPhoto,
        question: question.trim(),
      });
      setLookResult(data);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Photo question failed.", true);
    } finally {
      setLookBusy(false);
    }
  }

  async function illustrate(step: TutorStep, index: number) {
    if (!lesson || pictureBusy) return;
    setPictureBusy(true);
    try {
      const data = await call<{ image_data_url: string }>("POST", "/tutor/illustrate", {
        task_id: lesson.task_id,
        prompt: step.image_prompt,
      });
      setPictures((prev) => ({ ...prev, [index]: data.image_data_url }));
    } catch (err) {
      notify(err instanceof Error ? err.message : "Illustration failed.", true);
    } finally {
      setPictureBusy(false);
    }
  }

  function readAloud(text: string) {
    if (!canSpeak) {
      notify("Voice replies are not supported in this browser.", true);
      return;
    }
    speak(text, {
      rate: settings.speechRate,
      lang: settings.lang,
      onStart: () => setReading(true),
      onEnd: () => setReading(false),
    });
  }

  function askAbout(step: TutorStep, index: number) {
    if (!lesson) return;
    navigate("talk", null, {
      prompt: `Explain more about step ${index + 1} of "${lesson.topic}": ${step.title} — ${step.text}`,
      promptId: Date.now(),
    });
  }

  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px]";
  const primary = "min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50";
  const tab = (active: boolean) =>
    "min-h-12 flex-1 rounded-xl border px-3 text-[15px] " +
    (active ? "border-teal-800 bg-teal-800 font-semibold text-white" : "border-stone-300 bg-white");
  const saathState = reading ? "speaking" : busy || lookBusy ? "thinking" : lesson || lookResult ? "teaching" : "idle";

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-center gap-3">
        <Saath state={saathState} size={72} />
        <div>
          <h2 className="text-2xl font-bold">Learn</h2>
          <p className="text-sm text-stone-500">Saath teaches in small steps, with pictures and patience.</p>
          <p className="mt-1 text-sm text-stone-600">Confused anywhere? Open Talk and ask Saath - every lesson answers follow-ups.</p>
        </div>
      </div>

      <div className="mt-3 flex gap-2" role="tablist" aria-label="Learn modes">
        <button type="button" role="tab" aria-selected={mode === "teach"} className={tab(mode === "teach")} onClick={() => setMode("teach")}>
          Teach me
        </button>
        <button type="button" role="tab" aria-selected={mode === "photo"} className={tab(mode === "photo")} onClick={() => setMode("photo")}>
          Explain my photo
        </button>
        <button type="button" role="tab" aria-selected={mode === "video"} className={tab(mode === "video")} onClick={() => setMode("video")}>
          Videos
        </button>
        <button type="button" role="tab" aria-selected={mode === "guide"} className={tab(mode === "guide")} onClick={() => setMode("guide")}>
          Using Saath
        </button>
      </div>

      {mode === "teach" && (
        <div className="mt-3 flex flex-col gap-3">
          <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <form onSubmit={plan} className="flex flex-col gap-2">
              <label className="block text-sm font-medium">
                What do you want to learn?
                <input className={input + " mt-1 w-full"} placeholder="e.g. boiling rice, using an ATM" value={topic} onChange={(e) => setTopic(e.target.value)} required />
              </label>
              <label className="block text-sm font-medium">
                Anything I should know? (optional)
                <input className={input + " mt-1 w-full"} placeholder="e.g. I have a gas stove and a small pot" value={context} onChange={(e) => setContext(e.target.value)} />
              </label>
              <div>
                <input
                  ref={fileTeach}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  aria-label="Attach a photo"
                  onChange={(e) => attach(e.target.files?.[0], "teach").catch(() => undefined).finally(() => { if (fileTeach.current) fileTeach.current.value = ""; })}
                />
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={btn} onClick={() => fileTeach.current?.click()}>
                    {teachPhoto ? "Change photo" : "Attach a photo (optional)"}
                  </button>
                  {teachPhoto && (
                    <button type="button" className={btn} onClick={() => setTeachPhoto("")}>
                      Remove photo
                    </button>
                  )}
                </div>
                {teachPhoto && <img src={teachPhoto} alt="Your attached photo" className="mt-2 max-h-48 rounded-xl border border-stone-200" />}
              </div>
              <button type="submit" className={primary} disabled={busy}>
                {busy ? "Planning your lesson…" : "Teach me"}
              </button>
            </form>
          </section>

          {lesson && (
            <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold">{lesson.topic}</h3>
                <button type="button" className="ml-auto min-h-10 rounded-xl border border-stone-300 px-3 text-sm" onClick={() => onOpenTask(lesson.task_id)}>
                  Task {lesson.task_id}
                </button>
              </div>
              <p className="mt-1 text-sm text-stone-500">
                Planned by {lesson.model}
                {lesson.model === "stub-1" ? " (offline mode — connect the live model for full lessons)" : ""}
                {lesson.vision_used ? " · your photo was used" : ""}
              </p>
              {lesson.image_note && <p className="mt-1 text-sm text-amber-800">{lesson.image_note}</p>}
              {lesson.steps.length > 0 && (
                <article className="mt-3 rounded-xl bg-amber-50 p-4">
                  <p className="text-sm text-stone-500">Step {stepIdx + 1} of {lesson.steps.length}</p>
                  <h4 className="mt-0.5 text-lg font-semibold">{lesson.steps[stepIdx].title}</h4>
                  <p className="mt-1 text-[15px] leading-relaxed text-stone-800">{lesson.steps[stepIdx].text}</p>
                  {pictures[stepIdx] && (
                    <figure className="mt-2">
                      <img src={pictures[stepIdx]} alt={`AI illustration for step ${stepIdx + 1}`} className="max-h-64 rounded-xl border border-stone-200" />
                      <figcaption className="mt-1 text-xs text-stone-500">AI illustration, made for this step</figcaption>
                    </figure>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" className={btn} disabled={stepIdx === 0} onClick={() => setStepIdx((i) => Math.max(0, i - 1))}>
                      Previous
                    </button>
                    <button
                      type="button"
                      className={btn}
                      disabled={stepIdx >= lesson.steps.length - 1}
                      onClick={() => setStepIdx((i) => Math.min(lesson.steps.length - 1, i + 1))}
                    >
                      Next
                    </button>
                    <button type="button" className={btn} onClick={() => readAloud(lesson.steps[stepIdx].text)}>
                      {reading ? "Reading…" : "Read aloud"}
                    </button>
                    {lesson.steps[stepIdx].image_prompt ? (
                      <button type="button" className={btn} disabled={pictureBusy} onClick={() => illustrate(lesson.steps[stepIdx], stepIdx)}>
                        {pictureBusy ? "Drawing…" : pictures[stepIdx] ? "Draw again" : "Make a picture"}
                      </button>
                    ) : (
                      <span className="self-center text-sm text-stone-500">No picture needed for this step.</span>
                    )}
                    <button type="button" className={btn} onClick={() => askAbout(lesson.steps[stepIdx], stepIdx)}>
                      Ask about this
                    </button>
                  </div>
                  <p className="mt-2 text-xs text-stone-500">Pictures use your AI quota — one per tap.</p>
                </article>
              )}
              {lesson.follow_up && <p className="mt-2 text-sm text-stone-600">Next: {lesson.follow_up}</p>}
            </section>
          )}
        </div>
      )}

      {mode === "photo" && (
        <div className="mt-3 flex flex-col gap-3">
          <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <form onSubmit={askPhoto} className="flex flex-col gap-2">
              <input
                ref={fileLook}
                type="file"
                accept="image/*"
                className="hidden"
                aria-label="Attach a photo to ask about"
                onChange={(e) => attach(e.target.files?.[0], "look").catch(() => undefined).finally(() => { if (fileLook.current) fileLook.current.value = ""; })}
              />
              <div className="flex flex-wrap gap-2">
                <button type="button" className={btn} onClick={() => fileLook.current?.click()}>
                  {lookPhoto ? "Change photo" : "Attach a photo"}
                </button>
                {lookPhoto && (
                  <button type="button" className={btn} onClick={() => setLookPhoto("")}>
                    Remove photo
                  </button>
                )}
              </div>
              {lookPhoto && <img src={lookPhoto} alt="Your attached photo" className="max-h-48 rounded-xl border border-stone-200" />}
              <label className="block text-sm font-medium">
                Your question
                <input className={input + " mt-1 w-full"} placeholder="e.g. What do these buttons do?" value={question} onChange={(e) => setQuestion(e.target.value)} />
              </label>
              <button type="submit" className={primary} disabled={lookBusy || !lookPhoto}>
                {lookBusy ? "Looking…" : "Ask about this photo"}
              </button>
            </form>
          </section>

          {lookResult && (
            <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold">What Saath sees</h3>
                <button type="button" className="ml-auto min-h-10 rounded-xl border border-stone-300 px-3 text-sm" onClick={() => onOpenTask(lookResult.task_id)}>
                  Task {lookResult.task_id}
                </button>
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-[15px] leading-relaxed">{lookResult.description}</p>
              <p className="mt-1 text-sm text-stone-500">Seen by {lookResult.model}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className={btn} onClick={() => readAloud(lookResult.description)}>
                  {reading ? "Reading…" : "Read aloud"}
                </button>
                <button
                  type="button"
                  className={btn}
                  onClick={() => navigate("talk", null, { prompt: `Tell me more about my photo: ${lookResult.description.slice(0, 300)}`, promptId: Date.now() })}
                >
                  Discuss in Talk
                </button>
              </div>
            </section>
          )}
        </div>
      )}

      {mode === "video" && (
        <div className="mt-3">
          <VideoLessons notify={notify} onOpenTask={onOpenTask} />
        </div>
      )}

      {mode === "guide" && (
        <div className="mt-3 flex flex-col gap-3">
          {GUIDE.map((lessonGuide, i) => (
            <details key={lessonGuide.title} className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm" open={i === 0}>
              <summary className="cursor-pointer text-base font-semibold">{lessonGuide.title}</summary>
              <div className="mt-2 flex flex-col gap-2 text-[15px] leading-relaxed text-stone-700">
                {lessonGuide.body.map((paragraph, j) => (
                  <p key={j}>{paragraph}</p>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
