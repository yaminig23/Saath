import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, call } from "../api";
import type { ApprovalRow, Briefing, NotificationRow, Notify, TaskRow } from "../api";
import Saath from "../companion/Saath";
import type { Navigate, OpenTask } from "../nav";
import { BellIcon, BookIcon, CalendarIcon, HeartIcon, HelpIcon, MicIcon, SendIcon, SunIcon, UsersIcon } from "../icons";
import { ContactRow, DashboardWidget, GlassCard, GlowButton, GhostButton, GradientIcon, StatusDot, SuggestionRow } from "../components/ui";
import { useSettings } from "../settings";
import { createListener, speechSupported } from "../voice";

interface HomeProps {
  username: string;
  notify: Notify;
  navigate: Navigate;
  openTask: OpenTask;
  handsFree?: boolean;
}

interface TrustedPerson {
  id: number;
  name: string;
  email: string;
  relation: string;
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

const BRIEFING_KEY = "saath_briefing";
const BRIEFING_TTL_MS = 10 * 60 * 1000;

function fmtDeadline(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return (
      d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " +
      d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    );
  } catch {
    return iso;
  }
}

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
  const [trusted, setTrusted] = useState<TrustedPerson[]>([]);
  const [briefing, setBriefing] = useState<string | null>(null);
  const [briefingBusy, setBriefingBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [mood, setMood] = useState<string | null>(null);
  const [moodBusy, setMoodBusy] = useState(false);
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
      const [dueRes, apprRes, notifRes, tasksRes, trustedRes] = await Promise.allSettled([
        call<TaskRow[]>("GET", "/reminders/due"),
        call<ApprovalRow[]>("GET", "/approvals?status=pending"),
        call<NotificationRow[]>("GET", "/notifications"),
        call<TaskRow[]>("GET", "/tasks"),
        call<TrustedPerson[]>("GET", "/trusted-contacts"),
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
      if (trustedRes.status === "fulfilled") setTrusted(trustedRes.value);
      else fail(trustedRes.reason);
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
      notify("Hands-free is on — just say Hi Saath. Turn Voice off above for the button.");
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

  async function checkIn(feeling: string) {
    setMoodBusy(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      await call("POST", "/memories", {
        layer: "event",
        key: `Daily check-in ${today}`,
        value: `Feeling ${feeling.toLowerCase()} today.`,
        category: "general",
      });
      setMood(feeling);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Check-in failed.", true);
    } finally {
      setMoodBusy(false);
    }
  }

  const unread = notifications.filter((n) => !n.read_at).length;
  const done = tasks.filter((t) => t.status === "COMPLETED").length;
  const open = tasks.filter((t) => t.status !== "COMPLETED");
  const needs: { icon: React.ReactNode; tile: 1 | 2 | 3 | 4 | 5; title: string; blurb: string; go: () => void }[] = [
    { icon: <HelpIcon />, tile: 1, title: "Get Help", blurb: "Cabs, visits, everyday tasks", go: () => navigate("more", "help") },
    { icon: <BookIcon />, tile: 2, title: "Learn", blurb: "Video lessons on anything", go: () => navigate("learn") },
    { icon: <CalendarIcon />, tile: 3, title: "My Life", blurb: "Tasks, reminders, bookings", go: () => navigate("mylife") },
    { icon: <UsersIcon />, tile: 4, title: "Meet People", blurb: "Groups and volunteers", go: () => navigate("connect") },
    { icon: <SunIcon />, tile: 5, title: "Independence", blurb: "Routines and confidence", go: () => navigate("more", "independent") },
  ];
  const suggestions: { text: string; go: () => void }[] = [];
  if (pending.length > 0) {
    suggestions.push({
      text: `Review ${pending.length} waiting for your yes`,
      go: () => navigate("mylife", "approvals"),
    });
  }
  if (due.length > 0) {
    suggestions.push({ text: `Review ${due.length} due reminder${due.length === 1 ? "" : "s"}`, go: () => navigate("mylife", "tasks") });
  }
  suggestions.push({ text: "Find a pharmacy nearby", go: () => navigate("more", "help") });
  if (open.length > 0) {
    const first = open[0];
    suggestions.push({ text: `Continue: ${first.objective}`, go: () => openTask(first.id) });
  }
  suggestions.push({ text: "Continue a learning video", go: () => navigate("learn") });
  suggestions.push({ text: "Ask Saath to plan your day", go: () => navigate("talk") });

  return (
    <div className="mx-auto max-w-6xl lg:grid lg:grid-cols-[minmax(0,1fr)_21rem] lg:gap-6 lg:items-start">
      <div className="mx-auto w-full max-w-2xl">
        <header className="flex items-center gap-3">
          <div>
            <p className="sa-muted text-sm">
              {greeting()}, {username}
            </p>
            <h2 className="text-2xl font-bold">What shall we do today?</h2>
          </div>
          <button
            type="button"
            onClick={() => navigate("more", "notifications")}
            className="ghost-btn relative ml-auto flex h-12 w-12 shrink-0 items-center justify-center !rounded-full"
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
          <span className="mascot-halo">
            <Saath state={listening ? "listening" : pending.length > 0 ? "approval" : "idle"} size={200} />
          </span>
          <p className="mt-2 max-w-md text-base font-medium">I remind, plan, teach with video, and book - just tell me what you need.</p>
          <p className="sa-muted mt-1 text-sm" role="status">
            {listening ? "Listening..." : handsFree ? "Hands-free is on - say Hi Saath." : pending.length > 0 ? "Something needs your yes below." : "Talk or type below."}
          </p>
        </div>

        <form onSubmit={submit} className="mt-4 flex items-center gap-2">
          <input
            className="glass-input h-14 grow px-5 text-base"
            placeholder="Ask Saath anything…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            aria-label="Ask Saath anything"
          />
          {speechSupported() && (
            <GlowButton
              onClick={toggleMic}
              className="flex h-14 w-14 shrink-0 items-center justify-center"
              aria-label={listening ? "Stop listening" : "Speak to Saath"}
              aria-pressed={listening}
            >
              <MicIcon />
            </GlowButton>
          )}
          <GlowButton type="submit" className="flex h-14 w-14 shrink-0 items-center justify-center" aria-label="Send">
            <SendIcon />
          </GlowButton>
        </form>

        <div className="mt-5 grid grid-cols-2 gap-3 [&>button:last-child]:col-span-2">
          {needs.map((n) => (
            <button
              key={n.title}
              type="button"
              onClick={n.go}
              className="glass-card glass-lift flex min-h-28 flex-col items-start gap-2 p-4 text-left"
            >
              <span className="flex w-full items-center gap-2">
                <GradientIcon tile={n.tile}>{n.icon}</GradientIcon>
                <span aria-hidden="true" className="sa-faint ml-auto text-xl">
                  →
                </span>
              </span>
              <span>
                <span className="block text-base font-semibold">{n.title}</span>
                <span className="sa-muted block text-sm">{n.blurb}</span>
              </span>
            </button>
          ))}
        </div>

        <GlassCard className="mt-6 p-4">
          <div className="flex items-center gap-2">
            <h4 className="text-base font-semibold">Daily briefing</h4>
            <GhostButton
              onClick={refreshBriefing}
              disabled={briefingBusy}
              className="ml-auto min-h-10 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {briefingBusy ? "Writing…" : briefing ? "Refresh" : "Load briefing"}
            </GhostButton>
          </div>
          {briefing ? (
            <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-[15px]">{briefing}</p>
          ) : (
            <p className="sa-muted mt-2 text-sm">Your organiser will summarise the day from your real tasks.</p>
          )}
        </GlassCard>
      </div>

      <aside className="mx-auto mt-6 flex w-full max-w-2xl flex-col gap-3 lg:mx-0 lg:mt-0 lg:sticky lg:top-4" aria-label="Your day at a glance">
        <GlassCard className="sunrise p-4">
          <div className="relative z-10">
            <div className="flex items-baseline gap-2">
              <h3 className="text-base font-semibold">Your day</h3>
              <GhostButton onClick={() => navigate("mylife", "tasks")} className="ml-auto min-h-10 px-3 py-1.5 text-sm">
                View all
              </GhostButton>
            </div>
            {!loaded ? (
              <p className="sa-muted mt-2 text-sm">Loading…</p>
            ) : (
              <div className="mt-2 flex flex-col gap-3 text-[15px]">
                <p className="flex items-baseline gap-2">
                  <span className="text-4xl font-bold">{open.length + due.length}</span>
                  <span className="sa-muted text-sm">open tasks and reminders today</span>
                </p>
                <div>
                  <p className="sa-muted text-sm font-medium">Next reminder</p>
                  {due.length === 0 ? (
                    <p className="sa-muted mt-1 text-sm">Nothing due. Enjoy the quiet.</p>
                  ) : (
                    <button type="button" onClick={() => openTask(due[0].id)} className="mt-1 flex w-full items-center gap-2 text-left">
                      <span className="grow truncate">{due[0].objective}</span>
                      {due[0].deadline && <span className="sa-muted shrink-0 text-sm">{fmtDeadline(due[0].deadline)}</span>}
                    </button>
                  )}
                </div>
                <div className="flex flex-col gap-1.5">
                  <StatusDot tone={pending.length > 0 ? "amber" : "green"} label={pending.length > 0 ? `${pending.length} waiting for your yes` : "No approvals waiting"} />
                  <StatusDot tone="green" label={`${done} of ${tasks.length} tasks completed`} />
                </div>
              </div>
            )}
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <span className="sa-accent" aria-hidden="true">
              <HeartIcon />
            </span>
            <div>
              <h3 className="text-base font-semibold">Daily check-in</h3>
              <p className="sa-muted text-sm">How are you feeling today?</p>
            </div>
          </div>
          {mood ? (
            <p className="mt-2 text-[15px]">
              Noted — feeling {mood.toLowerCase()} today. I am here if you need anything.
            </p>
          ) : (
            <div className="mt-2 flex gap-2">
              {["Good", "Okay", "Low"].map((feeling) => (
                <GhostButton key={feeling} onClick={() => checkIn(feeling)} disabled={moodBusy} className="min-h-11 grow px-3 text-[15px] disabled:opacity-50">
                  {feeling}
                </GhostButton>
              ))}
            </div>
          )}
        </GlassCard>

        <DashboardWidget title="Saath suggestions" className="suggest-surface">
          <ul className="flex flex-col gap-2">
            {suggestions.slice(0, 3).map((s) => (
              <li key={s.text}>
                <SuggestionRow onClick={s.go}>{s.text}</SuggestionRow>
              </li>
            ))}
          </ul>
        </DashboardWidget>

        <DashboardWidget
          title="Trusted connections"
          action={
            <GhostButton onClick={() => navigate("more", "trusted")} className="min-h-10 px-3 py-1.5 text-sm">
              {trusted.length > 0 ? "Manage" : "Add contact"}
            </GhostButton>
          }
        >
          {!loaded ? (
            <p className="sa-muted text-sm">Loading…</p>
          ) : trusted.length === 0 ? (
            <p className="sa-muted text-sm">No trusted people yet. Name someone Saath may contact with your permission.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {trusted.slice(0, 3).map((t) => (
                <ContactRow key={t.id} name={t.name} detail={t.relation} onMessage={() => navigate("more", "trusted")} />
              ))}
            </ul>
          )}
        </DashboardWidget>
      </aside>
    </div>
  );
}
