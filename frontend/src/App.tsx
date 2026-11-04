import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { call, getToken, onUnauthorized } from "./api";
import type { ApprovalRow, Notify } from "./api";
import Saath from "./companion/Saath";
import type { Navigate, Section, TalkParams } from "./nav";
import { BookIcon, CalendarIcon, ChatIcon, GridIcon, HomeIcon, MicIcon, MoonIcon, SunIcon, UsersIcon } from "./icons";
import { NavigationItem } from "./components/ui";
import { SettingsProvider, useSettings } from "./settings";
import { speak, speechOutputSupported, speechSupported } from "./voice";
import { useVoiceOnEngine } from "./voiceOn";
import Auth from "./views/Auth";
import Chat from "./views/Chat";
import Connect from "./views/Connect";
import Home from "./views/Home";
import More from "./views/More";
import MyLife from "./views/MyLife";
import Onboarding from "./views/Onboarding";
import Tutor from "./views/Tutor";

interface Toast {
  id: number;
  text: string;
  isError: boolean;
}

function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="fixed bottom-24 left-1/2 z-20 flex w-full max-w-md -translate-x-1/2 flex-col gap-2 px-4 md:bottom-6" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={"rounded-lg px-4 py-2 text-sm shadow " + (t.isError ? "bg-red-800 text-white" : "bg-slate-900 text-white")}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

const NAV_ITEMS: { section: Section; label: string; icon: ReactNode }[] = [
  { section: "home", label: "Home", icon: <HomeIcon /> },
  { section: "talk", label: "Talk", icon: <ChatIcon /> },
  { section: "learn", label: "Learn", icon: <BookIcon /> },
  { section: "mylife", label: "My Life", icon: <CalendarIcon /> },
  { section: "connect", label: "Connect", icon: <UsersIcon /> },
  { section: "more", label: "More", icon: <GridIcon /> },
];

function Shell() {
  const { settings, update } = useSettings();
  const [username, setUsername] = useState<string | null>(() => localStorage.getItem("saath_user"));
  const [token, setToken] = useState<string | null>(() => getToken());
  const [section, setSection] = useState<Section>("home");
  const [view, setView] = useState<string | null>(null);
  const [talk, setTalk] = useState<TalkParams>({});
  const [taskFocus, setTaskFocus] = useState<number | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [voiceBlocked, setVoiceBlocked] = useState(false);
  const [showIntro, setShowIntro] = useState(() => { try { return localStorage.getItem("saath_onboarded") !== "1"; } catch { return true; } });
  const [approval, setApproval] = useState<ApprovalRow | null>(null);
  const seenApprovals = useRef<Set<number>>(new Set());
  const spokenApprovals = useRef<Set<number>>(new Set());

  const notify: Notify = useCallback((message, isError = false) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, text: message, isError }].slice(-4));
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      localStorage.removeItem("saath_token");
      localStorage.removeItem("saath_user");
      setToken(null);
      setUsername(null);
      notify("Session expired. Please log in again.", true);
    });
  }, [notify]);

  const navigate: Navigate = useCallback((s, v = null, params = {}) => {
    setSection(s);
    setView(v ?? null);
    if (s === "talk" && (params.prompt !== undefined || params.voiceTurn !== undefined)) setTalk(params);
  }, []);

  const voice = useVoiceOnEngine({
    enabled: settings.voiceOn,
    lang: settings.lang,
    notify,
    onWake: () => navigate("talk", null, { voiceTurn: Date.now() }),
    onFatal: (message: string) => { update({ voiceOn: false }); setVoiceBlocked(/blocked|not supported|could not start|keeps stopping/i.test(message)); },
  });
  const handsFree = voice.phase !== "off";

  function toggleVoice() {
    if (handsFree) {
      voice.stop();
      update({ voiceOn: false });
    } else {
      setVoiceBlocked(false); update({ voiceOn: true });
      voice.start();
    }
  }

  const voiceLabel = voiceBlocked ? "Mic blocked - tap to retry" : voice.phase === "wake" ? "Say Hi Saath" : voice.phase === "handoff" ? "Listening…" : settings.voiceOn ? "Resume voice" : "Voice off";

  function openTask(id: number) {
    setSection("mylife");
    setView("tasks");
    setTaskFocus(id);
  }

  async function logout() {
    try {
      await call("POST", "/auth/logout");
    } catch {
      /* token is cleared below either way */
    }
    localStorage.removeItem("saath_token");
    localStorage.removeItem("saath_user");
    setToken(null);
    setUsername(null);
    setSection("home");
    setView(null);
  }

  useEffect(() => {
    if (token === null) return;
    let cancelled = false;
    async function poll() {
      try {
        const rows = await call<ApprovalRow[]>("GET", "/approvals?status=pending");
        if (cancelled) return;
        const fresh = rows.find((r) => !seenApprovals.current.has(r.id)) ?? null;
        if (fresh && !spokenApprovals.current.has(fresh.id)) {
          spokenApprovals.current.add(fresh.id);
          if ((settings.voiceReplies || voice.phase !== "off") && speechOutputSupported()) {
            speak("Saath needs your yes: " + fresh.tool + " on task " + fresh.task_id + ". Approve or deny on your screen.", { lang: settings.lang, rate: settings.speechRate });
          }
        }
        setApproval((prev) => (prev && fresh && prev.id === fresh.id ? prev : fresh));
      } catch {
        /* next poll retries */
      }
    }
    poll().catch(() => undefined);
    const timer = window.setInterval(() => { poll().catch(() => undefined); }, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function decideApproval(id: number, approve: boolean) {
    try {
      await call("POST", "/approvals/" + id + "/decide", { approve });
      seenApprovals.current.add(id);
      setApproval(null);
      notify(approve ? "Approval granted." : "Approval denied.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Decision failed.", true);
    }
  }

  function dismissApproval() {
    if (approval) seenApprovals.current.add(approval.id);
    setApproval(null);
  }
  function toggleTheme() { update({ darkMode: !settings.darkMode }); }

  if (token === null) {
    return (
      <div className="sa-shell min-h-screen">
        <header className="border-b backdrop-blur-xl md:hidden" style={{ background: "var(--sa-sidebar)", borderColor: "var(--sa-line)" }}>
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
            <Saath state="idle" size={44} />
            <div>
              <h1 className="text-lg font-bold">Saath</h1>
              <p className="sa-muted truncate text-xs">Your companion for everyday life</p>
            </div>
          </div>
        </header>
        <Auth
          onDone={(user) => {
            setUsername(user);
            setToken(getToken());
          }}
        />
        {approval && (
        <div className="fixed inset-0 z-30 flex items-end justify-center bg-black/50 p-4 sm:items-center" role="alertdialog" aria-modal="true" aria-label="Approval needed">
          <div className="glass-card w-full max-w-md p-5">
            <div className="flex items-center gap-3">
              <Saath state="approval" size={64} />
              <h2 className="text-xl font-bold">Your yes needed</h2>
            </div>
            <p className="mt-2 text-[15px]"><strong>{approval.tool}</strong> on task {approval.task_id}{approval.note ? " - " + approval.note : ""}</p>
            <div className="mt-3 flex flex-col gap-2">
              <button type="button" onClick={() => decideApproval(approval.id, true).catch(() => undefined)} className="glow-btn min-h-14 w-full px-4 py-3 text-lg font-semibold">
                Approve
              </button>
              <button type="button" onClick={() => decideApproval(approval.id, false).catch(() => undefined)} className="ghost-btn min-h-12 w-full px-4 py-2 text-[15px]">
                Deny
              </button>
              <div className="flex gap-2">
                <button type="button" onClick={() => { dismissApproval(); openTask(approval.task_id); }} className="ghost-btn min-h-12 grow px-4 py-2 text-sm">
                  Open task
                </button>
                <button type="button" onClick={dismissApproval} className="ghost-btn min-h-12 grow px-4 py-2 text-sm">
                  Later
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {showIntro && (
        <Onboarding
          onEnableVoice={() => { try { localStorage.setItem("saath_onboarded", "1"); } catch { /* private mode */ } setShowIntro(false); toggleVoice(); }}
          onTypeInstead={() => { try { localStorage.setItem("saath_onboarded", "1"); } catch { /* private mode */ } setShowIntro(false); }}
        />
      )}
      <Toasts toasts={toasts} />
      </div>
    );
  }

  return (
    <div className="sa-shell min-h-screen pb-24 md:pb-8">
      <header className="border-b backdrop-blur-xl md:hidden" style={{ background: "var(--sa-sidebar)", borderColor: "var(--sa-line)" }}>
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <Saath state="idle" size={44} />
          <div className="min-w-0">
            <h1 className="text-lg font-bold">Saath</h1>
            <p className="sa-muted truncate text-xs">Signed in as {username || "you"}</p>
          </div>
          <button type="button" onClick={toggleTheme} title={settings.darkMode ? "Light mode" : "Dark mode"} aria-label={settings.darkMode ? "Light mode" : "Dark mode"} className="ghost-btn ml-auto flex min-h-12 min-w-12 items-center justify-center">
            {settings.darkMode ? <SunIcon /> : <MoonIcon />}
          </button>
          {speechSupported() && (
            <button
              type="button"
              onClick={toggleVoice}
              aria-pressed={handsFree}
              title="Hands-free voice: say Hi Saath to talk without tapping"
              className={
                "flex min-h-12 items-center gap-2 whitespace-nowrap px-3 text-sm font-medium " +
                (handsFree ? "glow-btn" : voiceBlocked ? "border-red-700 bg-red-50 text-red-900" : "ghost-btn")
              }
            >
              <MicIcon />
              {voiceLabel}
            </button>
          )}
        </div>
          {handsFree && voice.heard && (
            <p className="sa-muted max-w-55 truncate text-xs" role="status">
              Heard: &ldquo;{voice.heard}&rdquo;
            </p>
          )}
        <span className="sr-only" role="status">
          {voice.phase === "wake"
            ? "Hands-free voice is on. Say Hi Saath."
            : voice.phase === "handoff"
              ? "Heard you. Opening Talk."
              : "Hands-free voice is off."}
        </span>
      </header>
      
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col border-r backdrop-blur-xl md:flex" style={{ background: "var(--sa-sidebar)", borderColor: "var(--sa-line)" }} aria-label="Saath panel">
        <div className="flex items-center gap-3 px-5 pb-4 pt-5">
          <Saath state="idle" size={48} />
          <div>
            <p className="text-xl font-bold">Saath</p>
            <p className="sa-muted truncate text-xs">Signed in as {username || "you"}</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1 px-3" aria-label="Sections">
          {NAV_ITEMS.map((item) => (
            <NavigationItem
              key={item.section}
              active={section === item.section}
              onClick={() => navigate(item.section)}
              label={item.label}
              icon={item.icon}
            />
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-2 p-4">
          {speechSupported() && (
            <button
              type="button"
              onClick={toggleVoice}
              aria-pressed={handsFree}
              className={
                "flex min-h-12 items-center justify-center gap-2 rounded-xl border px-4 text-[15px] font-medium " +
                (handsFree ? "glow-btn" : voiceBlocked ? "border-red-700 bg-red-50 text-red-900" : "ghost-btn")
              }
            >
              <MicIcon />
              {voiceLabel}
            </button>
          )}
          <button type="button" onClick={toggleTheme} className="ghost-btn flex min-h-12 items-center justify-center gap-2 px-4 text-[15px]">
            {settings.darkMode ? <SunIcon /> : <MoonIcon />}
            {settings.darkMode ? "Light mode" : "Dark mode"}
          </button>
        </div>
      </aside>
      <main className="mx-auto max-w-7xl px-4 py-4 md:ml-64 md:px-6">
        {section === "home" && <Home username={username || "friend"} notify={notify} navigate={navigate} openTask={openTask} handsFree={handsFree} />}
        {section === "talk" && <Chat notify={notify} initial={talk.prompt} promptId={talk.promptId} voiceTurn={talk.voiceTurn} handsFree={handsFree} />}
        {section === "learn" && <Tutor notify={notify} navigate={navigate} onOpenTask={openTask} />}
        {section === "mylife" && (
          <MyLife view={view} navigate={navigate} notify={notify} openTask={openTask} focusId={taskFocus} onFocused={() => setTaskFocus(null)} />
        )}
        {section === "connect" && <Connect notify={notify} navigate={navigate} />}
        {section === "more" && (
          <More view={view} username={username || "you"} navigate={navigate} notify={notify} openTask={openTask} onLogout={logout} onReplayWelcome={() => setShowIntro(true)} />
        )}
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-10 border-t backdrop-blur-xl md:hidden" style={{ background: "var(--sa-sidebar)", borderColor: "var(--sa-line)" }} aria-label="Sections">
        <div className="flex">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.section}
              type="button"
              onClick={() => navigate(item.section)}
              aria-current={section === item.section ? "page" : undefined}
              className={
                "flex min-h-16 flex-1 flex-col items-center justify-center gap-0.5 text-[13px] " +
                (section === item.section ? "sa-accent" : "") +
                (section === item.section ? "font-semibold" : "sa-muted")
              }
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      </nav>
      {approval && (
        <div className="fixed inset-0 z-30 flex items-end justify-center bg-black/50 p-4 sm:items-center" role="alertdialog" aria-modal="true" aria-label="Approval needed">
          <div className="glass-card w-full max-w-md p-5">
            <div className="flex items-center gap-3">
              <Saath state="approval" size={64} />
              <h2 className="text-xl font-bold">Your yes needed</h2>
            </div>
            <p className="mt-2 text-[15px]"><strong>{approval.tool}</strong> on task {approval.task_id}{approval.note ? " - " + approval.note : ""}</p>
            <div className="mt-3 flex flex-col gap-2">
              <button type="button" onClick={() => decideApproval(approval.id, true).catch(() => undefined)} className="glow-btn min-h-14 w-full px-4 py-3 text-lg font-semibold">
                Approve
              </button>
              <button type="button" onClick={() => decideApproval(approval.id, false).catch(() => undefined)} className="ghost-btn min-h-12 w-full px-4 py-2 text-[15px]">
                Deny
              </button>
              <div className="flex gap-2">
                <button type="button" onClick={() => { dismissApproval(); openTask(approval.task_id); }} className="ghost-btn min-h-12 grow px-4 py-2 text-sm">
                  Open task
                </button>
                <button type="button" onClick={dismissApproval} className="ghost-btn min-h-12 grow px-4 py-2 text-sm">
                  Later
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {showIntro && (
        <Onboarding
          onEnableVoice={() => { try { localStorage.setItem("saath_onboarded", "1"); } catch { /* private mode */ } setShowIntro(false); toggleVoice(); }}
          onTypeInstead={() => { try { localStorage.setItem("saath_onboarded", "1"); } catch { /* private mode */ } setShowIntro(false); }}
        />
      )}
      <Toasts toasts={toasts} />
    </div>
  );
}

export default function App() {
  return (
    <SettingsProvider>
      <Shell />
    </SettingsProvider>
  );
}
