import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { call, getToken } from "../api";
import type { Notify } from "../api";
import type { OpenTask } from "../nav";
import { readPhoto } from "../photo";
import { useSettings } from "../settings";
import { speak, speechOutputSupported, stopSpeaking } from "../voice";

/* Video lessons: real job pipeline with honest progress, playback, scenes, and follow-up. */

interface VideoScene {
  index: number;
  title: string;
  seconds: number;
  start: number;
  end: number;
  has_keyframe: boolean;
  has_clip: boolean;
}

interface VideoStage {
  key: string;
  label: string;
  state: "done" | "current" | "todo";
}

interface VideoJob {
  id: number;
  task_id: number;
  topic: string;
  format: string;
  duration_s: number;
  status: string;
  provider: string;
  orientation: string;
  language: string;
  live_footage: boolean;
  lesson: { task_id: number | null; model: string; vision_used: boolean };
  scenes: VideoScene[];
  stages: VideoStage[];
  has_file: boolean;
  waiting_on: string;
  error: string;
  cost_note: string;
  cost: { provider: string; model: string; scenes: number; seconds: number; estimate_usd: number; note: string };
  has_audio: boolean | "unknown";
  narration_note: string;
  created_at: string;
  updated_at: string;
}

interface Keyframe {
  index: number;
  title: string;
  image_data_url: string;
}

interface ProviderRow {
  name: string;
  status: string;
  detail: string;
}

const FORMATS = [
  { key: "short", label: "Quick short", durations: [10, 20, 30] },
  { key: "tutorial", label: "Step-by-step", durations: [30, 60, 90] },
  { key: "scenario", label: "Real-life scenario", durations: [30, 60, 90] },
];

const TERMINAL = ["COMPLETED", "FAILED", "CANCELLED"];
const SPEEDS = [0.75, 1, 1.25, 1.5];

function srtToVtt(srt: string): string {
  return "WEBVTT\n\n" + srt.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2");
}

async function fetchAuthedBlob(path: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, { headers });
  if (res.status === 401) throw new Error("Session expired. Please log in again.");
  if (!res.ok) throw new Error("Download failed (" + res.status + ")");
  return URL.createObjectURL(await res.blob());
}

function statusPill(status: string): string {
  const base = "inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ";
  if (status === "COMPLETED") return base + "bg-teal-100 text-teal-900";
  if (status === "FAILED") return base + "bg-red-100 text-red-900";
  if (status === "CANCELLED") return base + "bg-stone-200 text-stone-600";
  if (status === "PENDING_APPROVAL") return base + "bg-amber-100 text-amber-900";
  return base + "bg-amber-100 text-amber-900";
}

function stageMark(state: string): string {
  if (state === "done") return "✓";
  if (state === "current") return "…";
  return "·";
}

function providerLabel(provider: string): string {
  if (provider === "local-demo") return "Muse-powered illustrated video";
  if (provider === "runway") return "Runway footage";
  return "Test footage — not real video";
}

function failureHint(error: string): string {
  if (error.includes("ffmpeg")) {
    return "Multi-scene assembly needs ffmpeg on the server. Your lesson plan and pictures below are still real and usable.";
  }
  if (error.includes("RUNWAYML_API_SECRET")) {
    return "Real footage needs a Runway key on the server. The plan, pictures, and captions are still real.";
  }
  return "";
}

function JobDetail({
  job,
  notify,
  onOpenTask,
  onChanged,
  onBack,
  onRemade,
}: {
  job: VideoJob;
  notify: Notify;
  onOpenTask: OpenTask;
  onChanged: () => void;
  onBack: () => void;
  onRemade: (topic: string) => void;
}) {
  const { settings } = useSettings();
  const [frames, setFrames] = useState<Record<number, string>>({});
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [vttUrl, setVttUrl] = useState<string | null>(null);
  const [transcript, setTranscript] = useState("");
  const [videoFailed, setVideoFailed] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [question, setQuestion] = useState("");
  const [sceneSel, setSceneSel] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; model: string; task_id: number } | null>(null);
  const [askBusy, setAskBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [acting, setActing] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const urlsRef = useRef<string[]>([]);
  const loadedFileRef = useRef(false);
  const canSpeak = speechOutputSupported();
  const frameCount = job.scenes.filter((s) => s.has_keyframe).length;

  useEffect(() => {
    const missingFile = job.has_file && !loadedFileRef.current;
    const missingFrames = frameCount > Object.keys(frames).length;
    if (!missingFile && !missingFrames) return;
    let cancelled = false;
    (async () => {
      if (missingFrames) {
        const data = await call<{ keyframes: Keyframe[] }>("GET", "/video/jobs/" + job.id + "/keyframes");
        if (cancelled) return;
        const next: Record<number, string> = {};
        for (const frame of data.keyframes) next[frame.index] = frame.image_data_url;
        setFrames(next);
      }
      if (missingFile) {
        const [video, captions] = await Promise.all([
          fetchAuthedBlob("/video/jobs/" + job.id + "/file"),
          call<{ captions: string; transcript: string }>("GET", "/video/jobs/" + job.id + "/captions"),
        ]);
        if (cancelled) {
          URL.revokeObjectURL(video);
          return;
        }
        const vtt = URL.createObjectURL(new Blob([srtToVtt(captions.captions)], { type: "text/vtt" }));
        urlsRef.current.push(video, vtt);
        loadedFileRef.current = true;
        setVideoUrl(video);
        setVttUrl(vtt);
        setTranscript(captions.transcript);
      }
    })().catch((err: unknown) => {
      if (!cancelled) notify(err instanceof Error ? err.message : "Could not load video media.", true);
    });
    return () => {
      cancelled = true;
    };
  }, [job.id, job.has_file, frameCount, job.status, frames, notify]);

  useEffect(
    () => () => {
      urlsRef.current.forEach((u) => URL.revokeObjectURL(u));
      urlsRef.current = [];
      stopSpeaking();
    },
    []
  );

  async function act(label: string, path: string, method: string, body?: unknown) {
    setActing(true);
    try {
      await call(method, path, body);
      notify(label);
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Request failed.", true);
    } finally {
      setActing(false);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setAskBusy(true);
    try {
      const data = await call<{ answer: string; model: string; task_id: number }>("POST", "/video/jobs/" + job.id + "/ask", {
        question: question.trim(),
        scene_index: sceneSel === "" ? null : Number(sceneSel),
      });
      setAnswer(data);
      setQuestion("");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Question failed.", true);
    } finally {
      setAskBusy(false);
    }
  }

  function readAloud(text: string) {
    if (!canSpeak) {
      notify("Voice replies are not supported in this browser.", true);
      return;
    }
    speak(text, { rate: settings.speechRate, lang: settings.lang, onStart: () => setReading(true), onEnd: () => setReading(false) });
  }

  function seek(seconds: number) {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = seconds;
    el.play().catch(() => undefined);
  }

  function pickSpeed(next: number) {
    setSpeed(next);
    if (videoRef.current) videoRef.current.playbackRate = next;
  }

  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px] disabled:opacity-50";
  const terminal = TERMINAL.includes(job.status);
  const workingIndex = (() => {
    if (terminal || job.scenes.length === 0) return -1;
    if (job.status === "GENERATING_ASSETS") {
      return job.scenes.find((s) => !s.has_keyframe)?.index ?? -1;
    }
    if (job.status === "GENERATING_VIDEO" || job.status === "ASSEMBLING") {
      return job.scenes.find((s) => !s.has_clip)?.index ?? -1;
    }
    return -1;
  })();

  return (
    <div className="flex flex-col gap-3">
      <div>
        <button type="button" className={btn} onClick={onBack}>
          ← All video lessons
        </button>
      </div>

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold">{job.topic}</h3>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className={statusPill(job.status)}>{job.status.replace(/_/g, " ")}</span>
          <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">
            {job.format} · {job.duration_s}s
          </span>
          <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">
            {providerLabel(job.provider)}
          </span>
          {job.orientation === "portrait" && (
            <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">Portrait</span>
          )}
          {job.language === "hi" && (
            <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">Hindi</span>
          )}
          {job.has_audio === true && (
            <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">Narrated</span>
          )}
          {job.has_audio === false && (
            <span className="rounded-full bg-stone-200 px-2.5 py-0.5 text-xs font-semibold text-stone-600">Silent</span>
          )}
          <button type="button" className="ml-auto min-h-10 rounded-xl border border-stone-300 px-3 text-sm" onClick={() => onOpenTask(job.task_id)}>
            Task {job.task_id}
          </button>
        </div>
        {job.narration_note && job.narration_note !== "narrated" && (
          <p className="mt-1 text-sm text-stone-500">{job.narration_note}</p>
        )}
        {job.lesson.model && (
          <p className="mt-1 text-sm text-stone-500">
            Planned by {job.lesson.model}
            {job.lesson.vision_used ? " · your photo was used" : ""}
          </p>
        )}
        <ol className="mt-2 flex flex-col gap-1" aria-label="Generation progress">
          {job.stages.map((stage) => (
            <li key={stage.key} className="flex items-center gap-2 text-[15px]">
              <span aria-hidden="true" className={"w-5 text-center font-bold " + (stage.state === "done" ? "text-teal-800" : "text-stone-400")}>
                {stageMark(stage.state)}
              </span>
              <span className={stage.state === "todo" ? "text-stone-500" : ""}>{stage.label}</span>
            </li>
          ))}
        </ol>
      </section>

      {job.status === "PENDING_APPROVAL" && (
        <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
          <h4 className="text-base font-semibold">Confirm the spend</h4>
          <p className="mt-1 text-[15px]">{job.cost_note}</p>
          <ul className="mt-2 flex flex-col gap-0.5 text-sm text-stone-700" aria-label="Cost breakdown">
            <li>Provider: {job.cost.provider} · model {job.cost.model}</li>
            <li>
              Plan: {job.cost.scenes > 0 ? job.cost.scenes + " scenes" : "scenes planned next"} · {job.cost.seconds}s
            </li>
            <li>Estimate: ${job.cost.estimate_usd.toFixed(2)}</li>
            <li>{job.cost.note}</li>
          </ul>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" disabled={acting} className="min-h-12 grow rounded-xl bg-teal-800 px-4 font-medium text-white disabled:opacity-50" onClick={() => act("Cost confirmed.", "/video/jobs/" + job.id + "/confirm", "POST", { confirm: true })}>
              Confirm spend
            </button>
            <button type="button" disabled={acting} className={btn + " grow"} onClick={() => act("Job cancelled.", "/video/jobs/" + job.id + "/confirm", "POST", { confirm: false })}>
              Don&apos;t spend
            </button>
          </div>
        </section>
      )}

      {job.status === "FAILED" && (
        <section className="rounded-2xl border border-red-200 bg-red-50 p-4 shadow-sm" role="alert">
          <h4 className="text-base font-semibold text-red-900">This video could not be made</h4>
          <p className="mt-1 text-[15px] text-red-900">{job.error}</p>
          {failureHint(job.error) && <p className="mt-1 text-sm text-red-800">{failureHint(job.error)}</p>}
        </section>
      )}

      {job.has_file && !videoFailed && videoUrl && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <video
            ref={videoRef}
            controls
            playsInline
            preload="metadata"
            src={videoUrl}
            aria-label={"Video lesson: " + job.topic}
            className="w-full rounded-xl bg-black"
            onError={() => setVideoFailed(true)}
          >
            {vttUrl && <track kind="captions" src={vttUrl} srcLang={settings.lang} label="Captions" default />}
          </video>
          <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Playback speed">
            <span className="text-sm text-stone-500">Speed:</span>
            {SPEEDS.map((rate) => (
              <button
                key={rate}
                type="button"
                onClick={() => pickSpeed(rate)}
                aria-pressed={speed === rate}
                className={"min-h-10 rounded-xl border px-3 text-sm " + (speed === rate ? "border-teal-800 bg-teal-50 font-semibold text-teal-900" : "border-stone-300 bg-white")}
              >
                {rate}×
              </button>
            ))}
          </div>
        </section>
      )}

      {job.has_file && (videoFailed || !videoUrl) && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm" role="status">
          <p className="text-[15px]">
            {videoFailed && !job.live_footage
              ? "Test footage is not playable video — that is expected. The lesson plan, pictures, captions, and transcript below are real."
              : videoFailed
                ? "This video file would not play. The captions and transcript below are still available."
                : "Loading video…"}
          </p>
        </section>
      )}

      {job.scenes.length > 0 && (
        <section>
          <h4 className="text-base font-semibold">Scenes</h4>
          <div className="mt-2 flex flex-col gap-2">
            {job.scenes.map((scene) => (
              <article
                key={scene.index}
                className={
                  "flex gap-3 rounded-2xl border p-4 shadow-sm " +
                  (scene.index === workingIndex ? "border-teal-700 bg-teal-50 ring-2 ring-teal-700" : "border-stone-200 bg-white")
                }
              >
                {frames[scene.index] ? (
                  <img src={frames[scene.index]} alt={"Illustration for scene " + (scene.index + 1)} className="h-20 w-20 shrink-0 rounded-xl border border-stone-200 object-cover" />
                ) : (
                  <span className="flex h-20 w-20 shrink-0 items-center justify-center rounded-xl bg-stone-100 text-xs text-stone-500">
                    {scene.has_keyframe ? "Loading…" : "No picture yet"}
                  </span>
                )}
                <span className="grow">
                  <span className="block text-[15px] font-semibold">
                    {scene.index + 1}. {scene.title}
                    {scene.index === workingIndex && (
                      <span className="ml-2 rounded-full bg-teal-800 px-2 py-0.5 text-xs font-semibold text-white">
                        Making this now
                      </span>
                    )}
                  </span>
                  <span className="block text-sm text-stone-500">
                    {scene.seconds}s{scene.has_clip ? " · filmed" : ""}
                  </span>
                  {videoUrl && !videoFailed && (
                    <button type="button" className="mt-1 min-h-10 rounded-xl border border-stone-300 px-3 text-sm" onClick={() => seek(scene.start)}>
                      Play from here
                    </button>
                  )}
                </span>
              </article>
            ))}
          </div>
        </section>
      )}

      {transcript && (
        <details className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <summary className="cursor-pointer text-base font-semibold">Transcript</summary>
          <p className="mt-2 whitespace-pre-wrap break-words text-[15px] leading-relaxed text-stone-700">{transcript}</p>
          <div className="mt-2">
            <button type="button" className={btn} onClick={() => readAloud(transcript)}>
              {reading ? "Reading…" : "Read aloud"}
            </button>
          </div>
        </details>
      )}

      {job.scenes.length > 0 && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <h4 className="text-base font-semibold">Ask about this lesson</h4>
          <form onSubmit={ask} className="mt-2 flex flex-col gap-2">
            <div className="flex flex-wrap gap-2">
              <input
                className="min-h-12 grow rounded-xl border border-stone-300 bg-white px-3"
                placeholder="e.g. Explain that again, more simply"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                aria-label="Question about this lesson"
              />
              <select
                className="min-h-12 rounded-xl border border-stone-300 bg-white px-3"
                value={sceneSel}
                onChange={(e) => setSceneSel(e.target.value)}
                aria-label="Limit to one scene"
              >
                <option value="">Whole lesson</option>
                {job.scenes.map((scene) => (
                  <option key={scene.index} value={scene.index}>
                    Scene {scene.index + 1}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50" disabled={askBusy || !question.trim()}>
              {askBusy ? "Thinking…" : "Ask"}
            </button>
          </form>
          {answer && (
            <div className="mt-2 rounded-xl bg-amber-50 p-4">
              <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed">{answer.answer}</p>
              <p className="mt-1 text-sm text-stone-500">Answered by {answer.model}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className={btn} onClick={() => readAloud(answer.answer)}>
                  {reading ? "Reading…" : "Read aloud"}
                </button>
                <button type="button" className={btn} onClick={() => onOpenTask(answer.task_id)}>
                  Open task
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      <div className="flex flex-wrap gap-2">
        {!terminal && (
          <button type="button" disabled={acting} className={btn} onClick={() => act("Job cancelled.", "/video/jobs/" + job.id + "/cancel", "POST")}>
            Cancel job
          </button>
        )}
        {terminal && (
          <button type="button" disabled={acting} className={btn} onClick={() => act("Job deleted.", "/video/jobs/" + job.id, "DELETE").then(onBack)}>
            Delete job
          </button>
        )}
        <button type="button" className={btn} onClick={() => onRemade(job.topic)}>
          Make another version
        </button>
      </div>
    </div>
  );
}

interface VideoLessonsProps {
  notify: Notify;
  onOpenTask: OpenTask;
}

export default function VideoLessons({ notify, onOpenTask }: VideoLessonsProps) {
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [provider, setProvider] = useState<ProviderRow | null>(null);
  const [topic, setTopic] = useState("");
  const [format, setFormat] = useState("short");
  const [duration, setDuration] = useState("10");
  const [context, setContext] = useState("");
  const [photo, setPhoto] = useState("");
  const [footage, setFootage] = useState("local-demo");
  const [orientation, setOrientation] = useState("landscape");
  const [language, setLanguage] = useState("en");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [tickBusy, setTickBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const selectedRef = useRef<number | null>(null);
  const [detail, setDetail] = useState<VideoJob | null>(null);

  useEffect(() => {
    selectedRef.current = selected;
  });

  const loadJobsQuiet = useCallback(async () => {
    const data = await call<VideoJob[]>("GET", "/video/jobs");
    setJobs(data);
    if (selectedRef.current !== null) {
      try {
        const one = await call<VideoJob>("GET", "/video/jobs/" + selectedRef.current);
        setDetail(one);
      } catch {
        /* job deleted elsewhere; the list refresh already handled it */
      }
    }
  }, []);

  const activeCount = jobs.filter((j) => !TERMINAL.includes(j.status)).length;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadJobsQuiet()
      .then(() => call<ProviderRow[]>("GET", "/providers"))
      .then((rows) => setProvider(rows.find((r) => r.name === "video") ?? null))
      .catch((err: unknown) => notify(err instanceof Error ? err.message : "Could not load video jobs.", true));
  }, [loadJobsQuiet, notify]);

  useEffect(() => {
    if (activeCount === 0) return;
    const timer = window.setInterval(() => {
      loadJobsQuiet().catch(() => undefined);
    }, 8000);
    return () => window.clearInterval(timer);
  }, [activeCount, loadJobsQuiet]);

  async function refresh() {
    try {
      await loadJobsQuiet();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Refresh failed.", true);
    }
  }

  async function runWorker() {
    setTickBusy(true);
    try {
      await call("POST", "/worker/tick");
      await loadJobsQuiet();
      notify("Worker ran — statuses updated.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Worker run failed.", true);
    } finally {
      setTickBusy(false);
    }
  }

  async function attach(file: File | undefined) {
    if (!file) return;
    try {
      setPhoto(await readPhoto(file));
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not read that photo.", true);
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    try {
      const data = await call<VideoJob>("POST", "/video/jobs", {
        topic: topic.trim(),
        format,
        duration_s: Number(duration),
        context: context.trim(),
        image_data_url: photo,
        provider: footage,
        orientation: format === "short" ? orientation : "landscape",
        language,
      });
      setTopic("");
      setContext("");
      setPhoto("");
      setDetail(data);
      setSelected(data.id);
      notify("Video job started — it advances about once a minute. Run worker now hurries it.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not start the video job.", true);
    } finally {
      setCreating(false);
    }
  }

  function remake(remadeTopic: string) {
    setTopic(remadeTopic);
    setSelected(null);
    setDetail(null);
  }

  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px] disabled:opacity-50";
  const durations = FORMATS.find((f) => f.key === format)?.durations ?? [10];

  if (selected !== null && detail) {
    return (
      <JobDetail
        key={detail.id}
        job={detail}
        notify={notify}
        onOpenTask={onOpenTask}
        onChanged={refresh}
        onBack={() => {
          setSelected(null);
          setDetail(null);
        }}
        onRemade={remake}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {provider && (
        <section className={"rounded-2xl border p-4 shadow-sm " + (provider.status === "missing" ? "border-amber-300 bg-amber-50" : "border-stone-200 bg-white")}>
          <h4 className="text-base font-semibold">Real footage {provider.status === "missing" ? "is not connected" : "status"}</h4>
          <p className="mt-1 text-[15px]">{provider.detail}</p>
          {provider.status === "missing" && (
            <p className="mt-1 text-sm text-stone-600">
              Until then, Runway motion footage is blocked — but Local generation below renders real animated lessons
              without any key, and planning, illustrations, scenes, and captions still run for real.
            </p>
          )}
        </section>
      )}

      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h4 className="text-base font-semibold">Make a video lesson</h4>
        <form onSubmit={create} className="mt-2 flex flex-col gap-2">
          <label className="block text-sm font-medium">
            What should the video teach?
            <input className={input + " mt-1 w-full"} placeholder="e.g. using an ATM, boiling rice" value={topic} onChange={(e) => setTopic(e.target.value)} required />
          </label>
          <div className="flex flex-wrap gap-2">
            <label className="grow text-sm font-medium">
              Format
              <select
                className={input + " mt-1 w-full"}
                value={format}
                onChange={(e) => {
                  setFormat(e.target.value);
                  setDuration(String(FORMATS.find((f) => f.key === e.target.value)?.durations[0] ?? 10));
                  setOrientation("landscape");
                }}
              >
                {FORMATS.map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm font-medium">
              Length
              <select className={input + " mt-1 w-full"} value={duration} onChange={(e) => setDuration(e.target.value)}>
                {durations.map((d) => (
                  <option key={d} value={d}>
                    {d} seconds
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="block text-sm font-medium">
            Anything Saath should know? (optional)
            <input className={input + " mt-1 w-full"} placeholder="e.g. I learn slowly, keep it simple" value={context} onChange={(e) => setContext(e.target.value)} />
          </label>
          <fieldset>
            <legend className="text-sm font-medium">Footage</legend>
            <div className="mt-1 flex flex-col gap-2">
              <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                <input type="radio" name="footage" value="local-demo" checked={footage === "local-demo"} onChange={() => setFootage("local-demo")} />
                Local generation — Muse-powered illustrated video (no key needed)
              </label>
              <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                <input type="radio" name="footage" value="runway" checked={footage === "runway"} onChange={() => setFootage("runway")} />
                Runway — real footage (needs the key above)
              </label>
              <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                <input type="radio" name="footage" value="fake" checked={footage === "fake"} onChange={() => setFootage("fake")} />
                Labeled test — fake footage, clearly marked everywhere
              </label>
            </div>
            <p className="mt-1 text-sm text-stone-500" role="status">
              {footage === "local-demo" && "Rendering is free on this device. Muse planning and illustrations use API quota."}
              {footage === "runway" && "Paid footage — you confirm the exact spend before anything is generated."}
              {footage === "fake" && "Nothing real is produced. The workflow runs, but the clips are labeled test bytes."}
            </p>
          </fieldset>
          <fieldset>
            <legend className="text-sm font-medium">Lesson language</legend>
            <div className="mt-1 flex flex-col gap-2">
              <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                <input type="radio" name="language" value="en" checked={language === "en"} onChange={() => setLanguage("en")} />
                English
              </label>
              <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                <input type="radio" name="language" value="hi" checked={language === "hi"} onChange={() => setLanguage("hi")} />
                Hindi — narration needs a Hindi voice on this device
              </label>
            </div>
          </fieldset>
          {format === "short" && (
            <fieldset>
              <legend className="text-sm font-medium">Shape (shorts only)</legend>
              <div className="mt-1 flex flex-col gap-2">
                <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                  <input type="radio" name="orientation" value="landscape" checked={orientation === "landscape"} onChange={() => setOrientation("landscape")} />
                  Wide — for TV and computer
                </label>
                <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                  <input type="radio" name="orientation" value="portrait" checked={orientation === "portrait"} onChange={() => setOrientation("portrait")} />
                  Tall — for phones
                </label>
              </div>
            </fieldset>
          )}
          <div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              aria-label="Attach a photo"
              onChange={(e) => attach(e.target.files?.[0]).catch(() => undefined).finally(() => { if (fileRef.current) fileRef.current.value = ""; })}
            />
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btn} onClick={() => fileRef.current?.click()}>
                {photo ? "Change photo" : "Attach a photo (optional)"}
              </button>
              {photo && (
                <button type="button" className={btn} onClick={() => setPhoto("")}>
                  Remove photo
                </button>
              )}
            </div>
            {photo && <img src={photo} alt="Your attached photo" className="mt-2 max-h-48 rounded-xl border border-stone-200" />}
          </div>
          <button type="submit" className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50" disabled={creating}>
            {creating ? "Starting…" : "Make video lesson"}
          </button>
        </form>
      </section>

      <section>
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-base font-semibold">My video lessons</h4>
          <span className="text-sm text-stone-500">Advances ~once a minute</span>
          <span className="ml-auto flex gap-2">
            <button type="button" className={btn + " min-h-10 text-sm"} onClick={refresh}>
              Refresh
            </button>
            <button type="button" className={btn + " min-h-10 text-sm"} disabled={tickBusy} onClick={runWorker}>
              {tickBusy ? "Running…" : "Run worker now"}
            </button>
          </span>
        </div>
        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-stone-500">No video lessons yet. Describe one above.</p>
        ) : (
          <div className="mt-2 flex flex-col gap-2" role="status">
            {jobs.map((job) => (
              <button
                key={job.id}
                type="button"
                onClick={() => {
                  setDetail(job);
                  setSelected(job.id);
                }}
                className="rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
              >
                <span className="block text-[15px] font-semibold">{job.topic}</span>
                <span className="mt-1 flex flex-wrap items-center gap-2">
                  <span className={statusPill(job.status)}>{job.status.replace(/_/g, " ")}</span>
                  <span className="text-xs text-stone-500">
                    {job.format} · {job.duration_s}s · {job.provider === "local-demo" ? "illustrated" : job.live_footage ? "Runway" : "test"}
                  </span>
                </span>
                <span className="mt-1 block text-sm text-stone-500">
                  {job.stages
                    .filter((s) => s.state !== "todo")
                    .map((s) => `${stageMark(s.state)} ${s.label}`)
                    .join("  ") || "Not started yet"}
                </span>
                {job.status === "FAILED" && job.error && (
                  <span className="mt-1 block truncate text-sm text-red-800">{job.error}</span>
                )}
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
