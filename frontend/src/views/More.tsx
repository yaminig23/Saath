import type { Notify } from "../api";
import type { Navigate, OpenTask } from "../nav";
import { BellIcon, BookIcon, CodeIcon, GearIcon, HelpIcon, LogoutIcon, SunIcon } from "../icons";
import Developer from "./Developer";
import GetHelp from "./GetHelp";
import Memories from "./Memories";
import Notifications from "./Notifications";
import SettingsScreen from "./Settings";
import StayIndependent from "./StayIndependent";
import Sub from "./Sub";

interface MoreProps {
  view: string | null;
  username: string;
  navigate: Navigate;
  notify: Notify;
  openTask: OpenTask;
  onLogout: () => void;
}

export default function More({ view, username, navigate, notify, openTask, onLogout }: MoreProps) {
  if (view === "help") {
    return (
      <Sub title="Get Help" onBack={() => navigate("more", null)}>
        <GetHelp navigate={navigate} />
      </Sub>
    );
  }
  if (view === "independent") {
    return (
      <Sub title="Stay Independent" onBack={() => navigate("more", null)}>
        <StayIndependent navigate={navigate} />
      </Sub>
    );
  }
  if (view === "memories") {
    return (
      <Sub title="What Saath Remembers" onBack={() => navigate("more", null)}>
        <Memories notify={notify} onChanged={() => undefined} />
      </Sub>
    );
  }
  if (view === "notifications") {
    return (
      <Sub title="Notifications" onBack={() => navigate("more", null)}>
        <Notifications notify={notify} />
      </Sub>
    );
  }
  if (view === "settings") {
    return (
      <Sub title="Settings" onBack={() => navigate("more", null)}>
        <SettingsScreen notify={notify} onLogout={onLogout} />
      </Sub>
    );
  }
  if (view === "developer") {
    return (
      <Sub title="Developer Tools" onBack={() => navigate("more", null)}>
        <Developer notify={notify} onOpenTask={openTask} />
      </Sub>
    );
  }

  const items = [
    { key: "help", icon: <HelpIcon />, title: "Get Help", blurb: "How to talk to Saath, voice tips, emergencies" },
    { key: "independent", icon: <SunIcon />, title: "Stay Independent", blurb: "Comfort settings and daily confidence" },
    { key: "memories", icon: <BookIcon />, title: "What Saath Remembers", blurb: "View, correct, delete, and consent" },
    { key: "notifications", icon: <BellIcon />, title: "Notifications", blurb: "What Saath has told you" },
    { key: "settings", icon: <GearIcon />, title: "Settings", blurb: "Text, voice, accessibility, account" },
    { key: "developer", icon: <CodeIcon />, title: "Developer Tools", blurb: "Worker, failures, demo, API info" },
  ];

  return (
    <div className="mx-auto max-w-2xl">
      <h2 className="text-2xl font-bold">More</h2>
      <p className="mt-1 text-[15px] text-stone-500">Signed in as {username}</p>
      <div className="mt-4 flex flex-col gap-2">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => navigate("more", item.key)}
            className="flex min-h-16 items-center gap-3 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
          >
            <span className="shrink-0 text-teal-800">{item.icon}</span>
            <span>
              <span className="block text-base font-semibold">{item.title}</span>
              <span className="block text-sm text-stone-500">{item.blurb}</span>
            </span>
          </button>
        ))}
        <button
          type="button"
          onClick={onLogout}
          className="flex min-h-16 items-center gap-3 rounded-2xl border border-red-200 bg-white p-4 text-left shadow-sm"
        >
          <span className="shrink-0 text-red-700">
            <LogoutIcon />
          </span>
          <span className="block text-base font-semibold text-red-800">Log out</span>
        </button>
      </div>
    </div>
  );
}
