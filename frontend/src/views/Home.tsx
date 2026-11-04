import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, call } from "../api";
import type { ApprovalRow, Briefing, NotificationRow, Notify, TaskRow } from "../api";
import Jizo from "../companion/Jizo";
import type { Navigate, OpenTask } from "../nav";
import { BellIcon, BookIcon, CalendarIcon, HelpIcon, MicIcon, SendIcon, SunIcon } from "../icons";
import { useSettings } from "../settings";
import { createListener, speechSupported } from "../voice";

interface HomeProps {
  username: string;
  notify: Notify;
  navigate: Navigate;
  openTask: OpenTask;
  handsFree?: boolean;
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

const BRIEFING_KEY = "saath_briefing";
const BRIEFING_TTL_MS = 10 * 60 * 1000;

function cachedBriefing(): string | null {
  try {
    const raw = sessionStorage.getItem(BRIEFING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { ts: number; summary: string };
    if (Date.now() - parsed.ts > BRIEFING_TTL_MS) return null;
    return parsed.summary;
  } catch {
    return null;
  }
}

export default function Home({ username, notify, navigate, openTask, handsFree }: HomeProps) {
  const { settings } = useSettings();
  const [input, setInput] = useState("");
  const [listening, setListening] = useState(false);
  const listenerRef = useRef<ReturnType<typeof createListener>>(null);
  const [due, setDue] = useState<TaskRow[]>([]);
  const [pending, setPending] = useState<ApprovalRow[]>([]);
  const [notifications, setNotifications] = useState<NotificationRow[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [briefing, setBriefing] = useState<string | null>(null);
  const [briefingBusy, setBriefingBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const notifiedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => {
      if (reason instanceof ApiError && reason.status === 401) return;
      if (!notifiedRef.current) {
        notifiedRef.current = true;
        notify(reason instanceof Error ? reason.message : "Could not load home data.", true);
      }
    };
    async function load() {
      const [dueRes, apprRes, notifRes, tasksRes] = await Promise.allSettled([
        call<TaskRow[]>("GET", "/reminders/due"),
        call<ApprovalRow[]>("GET", "/approvals?status=pending"),
        call<NotificationRow[]>("GET", "/notifications"),
        call<TaskRow[]>("GET", "/tasks"),
      ]);
      if (cancelled) return;
      if (dueRes.status === "fulfilled") setDue(dueRes.value);
      else fail(dueRes.reason);
      if (apprRes.status === "fulfilled") setPending(apprRes.value);
      else fail(apprRes.reason);
      if (notifRes.status === "fulfilled") setNotifications(notifRes.value);
      else fail(notifRes.reason);
      if (tasksRes.status === "fulfilled") setTasks(tasksRes.value);
      else fail(tasksRes.reason);
      setLoaded(true);
      setBriefing(cachedBriefing());
    }
    load().catch(fail);
    return () => {
      cancelled = true;
      listenerRef.current?.stop();
    };
  }, [notify]);

  function ask(text: string) {
    const prompt = text.trim();
    if (!prompt) return;
    setInput("");
    navigate("talk", null, { prompt, promptId: Date.now() });
  }

  function toggleMic() {
    if (handsFree) {
      notify("Hands-free is on — just say Hi Jizo. Turn Voice off above for the button.");
      return;
    }
    if (listening) {
      listenerRef.current?.stop();
      setListening(false);
      return;
    }
    const listener = createListener(
      {
        onFinal: (text) => {
          setListening(false);
          ask(text);
        },
        onError: (message) => {
          setListening(false);
          notify(message, true);
        },
      },
      settings.lang
    );
    if (!listener) {
      notify("Voice input is not supported in this browser.", true);
      return;
    }
    listenerRef.current = listener;
    listener.start();
    setListening(true);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    ask(input);
  }

  async function refreshBriefing() {
    setBriefingBusy(true);
    try {
      const data = await call<Briefing>("POST", "/briefing");
      setBriefing(data.summary);
      try {
        sessionStorage.setItem(BRIEFING_KEY, JSON.stringify({ ts: Date.now(), summary: data.summary }));
      } catch {
        /* private mode: briefing simply isn't cached */
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : "Briefing failed.", true);
    } finally {
      setBriefingBusy(false);
    }
  }

  const unread = notifications.filter((n) => !n.read_at).length;
  const done = tasks.filter((t) => t.status === "COMPLETED").length;
  const pct = tasks.length === 0 ? 0 : Math.round((done / tasks.length) * 100);
  const needs = [
    { icon: <HelpIcon />, title: "Get Help", blurb: "Answers, guidance, and support", go: () => navigate("more", "help") },
    { icon: <BookIcon />, title: "Learn Something", blurb: "Patient step-by-step teaching", go: () => navigate("learn") },
    { icon: <CalendarIcon />, title: "Manage My Life", blurb: "Tasks, reminders, and plans", go: () => navigate("mylife") },
    { icon: <SunIcon />, title: "Stay Independent", blurb: "Routines and everyday confidence", go: () => navigate("more", "independent") },
  ];

  return (
    <div className="mx-auto max-w-2xl">
      <header className="flex items-center gap-3">
        <div>
          <p className="text-sm text-stone-500">
            {greeting()}, {username}
          </p>
          <h2 className="text-2xl font-bold">What shall we do today?</h2>
        </div>
        <button
          type="button"
          onClick={() => navigate("more", "notifications")}
          className="relative ml-auto flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-stone-300 bg-white"
          aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        >
          <BellIcon />
          {unread > 0 && (
            <span className="absolute -right-1 -top-1 flex h-6 min-w-6 items-center justify-center rounded-full bg-red-700 px-1 text-xs font-bold text-white">
              {unread}
            </span>
          )}
        </button>
      </header>

      <div className="mt-2 flex flex-col items-center text-center">
        <Jizo state={listening ? "listening" : "idle"} size={200} />
        <p className="mt-1 text-[15px] text-stone-600" role="status">
          {listening ? "I'm listening…" : handsFree ? "Hands-free is on — say Hi Jizo." : "I'm right here. Talk or type below."}
        </p>
      </div>

      <form onSubmit={submit} className="mt-4 flex items-center gap-2">
        <input
          className="h-14 grow rounded-2xl border border-stone-300 bg-white px-4 text-base"
          placeholder="Ask Saath anything…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          aria-label="Ask Saath anything"
        />
        {speechSupported() && (
          <button
            type="button"
            onClick={toggleMic}
            className={"flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-white " + (listening ? "bg-red-700" : "bg-teal-800")}
            aria-label={listening ? "Stop listening" : "Speak to Saath"}
            aria-pressed={listening}
          >
            <MicIcon />
          </button>
        )}
        <button
          type="submit"
          className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-teal-800 text-white"
          aria-label="Send"
        >
          <SendIcon />
        </button>
      </form>

      <div className="mt-5 grid grid-cols-2 gap-3">
        {needs.map((n) => (
          <button
            key={n.title}
            type="button"
            onClick={n.go}
            className="flex min-h-28 flex-col items-start gap-1 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
          >
            <span className="text-teal-800">{n.icon}</span>
            <span className="text-base font-semibold">{n.title}</span>
            <span className="text-sm text-stone-500">{n.blurb}</span>
          </button>
        ))}
      </div>

      <h3 className="mt-6 text-lg font-semibold">Today</h3>
      <div className="mt-3 flex flex-col gap-3">
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <h4 className="text-base font-semibold">Daily briefing</h4>
            <button
              type="button"
              onClick={refreshBriefing}
              disabled={briefingBusy}
              className="ml-auto min-h-10 rounded-md border border-stone-300 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {briefingBusy ? "Writing…" : briefing ? "Refresh" : "Load briefing"}
            </button>
          </div>
          {briefing ? (
            <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-[15px] text-stone-700">{briefing}</p>
          ) : (
            <p className="mt-2 text-sm text-stone-500">Your organiser will summarise the day from your real tasks.</p>
          )}
        </section>

        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <h4 className="text-base font-semibold">
            Due reminders {due.length > 0 && <span className="font-normal text-stone-500">({due.length})</span>}
          </h4>
          {!loaded ? (
            <p className="mt-2 text-sm text-stone-500">Loading…</p>
          ) : due.length === 0 ? (
            <p className="mt-2 text-sm text-stone-500">Nothing due. Enjoy the quiet.</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {due.slice(0, 3).map((t) => (
                <li key={t.id} className="flex items-center gap-2 text-[15px]">
                  <span className="grow">{t.objective}</span>
                  <button
                    type="button"
                    onClick={() => openTask(t.id)}
                    className="min-h-10 shrink-0 rounded-md border border-stone-300 px-3 py-1.5 text-sm"
                  >
                    Open
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {pending.length > 0 && (
          <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
            <h4 className="text-base font-semibold">Waiting for your approval ({pending.length})</h4>
            <ul className="mt-2 flex flex-col gap-2">
              {pending.slice(0, 3).map((a) => (
                <li key={a.id} className="flex items-center gap-2 text-[15px]">
                  <span className="grow">
                    {a.tool} on task {a.task_id}
                  </span>
                  <button
                    type="button"
                    onClick={() => navigate("mylife", "approvals")}
                    className="min-h-10 shrink-0 rounded-md bg-teal-800 px-3 py-1.5 text-sm text-white"
                  >
                    Review
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <h4 className="text-base font-semibold">Tasks</h4>
            <button
              type="button"
              onClick={() => navigate("mylife", "tasks")}
              className="ml-auto min-h-10 rounded-md border border-stone-300 px-3 py-1.5 text-sm"
            >
              Open tasks
            </button>
          </div>
          {tasks.length === 0 ? (
            <p className="mt-2 text-sm text-stone-500">No tasks yet.</p>
          ) : (
            <div className="mt-2">
              <div className="text-sm text-stone-600">
                {done} of {tasks.length} completed
              </div>
              <div className="mt-1 h-2.5 overflow-hidden rounded-full bg-stone-200">
                <div className="h-full bg-teal-700" style={{ width: pct + "%" }} />
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
