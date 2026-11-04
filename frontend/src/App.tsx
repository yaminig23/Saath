import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { call, getToken, onUnauthorized } from "./api";
import type { Notify } from "./api";
import Jizo from "./companion/Jizo";
import type { Navigate, Section, TalkParams } from "./nav";
import { BookIcon, CalendarIcon, ChatIcon, GridIcon, HomeIcon, MicIcon, UsersIcon } from "./icons";
import { SettingsProvider, useSettings } from "./settings";
import { speechSupported } from "./voice";
import { useVoiceOnEngine } from "./voiceOn";
import Auth from "./views/Auth";
import Chat from "./views/Chat";
import Connect from "./views/Connect";
import Home from "./views/Home";
import More from "./views/More";
import MyLife from "./views/MyLife";
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
    onFatal: () => update({ voiceOn: false }),
  });
  const handsFree = voice.phase !== "off";

  function toggleVoice() {
    if (handsFree) {
      voice.stop();
      update({ voiceOn: false });
    } else {
      update({ voiceOn: true });
      voice.start();
    }
  }

  const voiceLabel = voice.phase === "wake" ? "Say Hi Jizo" : voice.phase === "handoff" ? "Listening…" : settings.voiceOn ? "Resume voice" : "Voice off";

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

  if (token === null) {
    return (
      <div className="min-h-screen">
        <header className="border-b border-stone-200 bg-white">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
            <Jizo state="idle" size={44} />
            <div>
              <h1 className="text-lg font-bold">Saath</h1>
              <p className="text-xs text-stone-500">Your companion for everyday life</p>
            </div>
          </div>
        </header>
        <Auth
          onDone={(user) => {
            setUsername(user);
            setToken(getToken());
          }}
        />
        <Toasts toasts={toasts} />
      </div>
    );
  }

  return (
    <div className="min-h-screen pb-24 md:pb-0">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <Jizo state="idle" size={44} />
          <div>
            <h1 className="text-lg font-bold">Saath</h1>
            <p className="text-xs text-stone-500">Signed in as {username || "you"}</p>
          </div>
          {speechSupported() && (
            <button
              type="button"
              onClick={toggleVoice}
              aria-pressed={handsFree}
              title="Hands-free voice: say Hi Jizo to talk without tapping"
              className={
                "ml-auto flex min-h-12 items-center gap-2 rounded-full border px-4 text-[15px] font-medium " +
                (handsFree ? "border-teal-800 bg-teal-800 text-white" : "border-stone-300 bg-white text-stone-600")
              }
            >
              <MicIcon />
              {voiceLabel}
            </button>
          )}
        </div>
        <span className="sr-only" role="status">
          {voice.phase === "wake"
            ? "Hands-free voice is on. Say Hi Jizo."
            : voice.phase === "handoff"
              ? "Heard you. Opening Talk."
              : "Hands-free voice is off."}
        </span>
      </header>
      <nav className="sticky top-0 z-10 hidden border-b border-stone-200 bg-white md:block" aria-label="Sections">
        <div className="mx-auto flex max-w-5xl gap-2 px-4 py-2">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.section}
              type="button"
              onClick={() => navigate(item.section)}
              aria-current={section === item.section ? "page" : undefined}
              className={
                "flex min-h-14 flex-1 items-center justify-center gap-2 rounded-xl px-4 text-base " +
                (section === item.section ? "bg-teal-800 font-semibold text-white" : "text-stone-600")
              }
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      </nav>
      <main className="mx-auto max-w-5xl px-4 py-4">
        {section === "home" && <Home username={username || "friend"} notify={notify} navigate={navigate} openTask={openTask} handsFree={handsFree} />}
        {section === "talk" && <Chat notify={notify} initial={talk.prompt} promptId={talk.promptId} voiceTurn={talk.voiceTurn} handsFree={handsFree} />}
        {section === "learn" && <Tutor notify={notify} navigate={navigate} onOpenTask={openTask} />}
        {section === "mylife" && (
          <MyLife view={view} navigate={navigate} notify={notify} openTask={openTask} focusId={taskFocus} onFocused={() => setTaskFocus(null)} />
        )}
        {section === "connect" && <Connect notify={notify} navigate={navigate} />}
        {section === "more" && (
          <More view={view} username={username || "you"} navigate={navigate} notify={notify} openTask={openTask} onLogout={logout} />
        )}
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-10 border-t border-stone-200 bg-white md:hidden" aria-label="Sections">
        <div className="flex">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.section}
              type="button"
              onClick={() => navigate(item.section)}
              aria-current={section === item.section ? "page" : undefined}
              className={
                "flex min-h-16 flex-1 flex-col items-center justify-center gap-0.5 text-[13px] " +
                (section === item.section ? "font-semibold text-teal-900" : "text-stone-500")
              }
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      </nav>
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
