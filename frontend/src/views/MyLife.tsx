import type { Notify } from "../api";
import type { Navigate, OpenTask } from "../nav";
import { BellIcon, CalendarIcon, CheckIcon, DocIcon, TicketIcon } from "../icons";
import Approvals from "./Approvals";
import Bookings from "./Bookings";
import Briefing from "./Briefing";
import CalendarLink from "./CalendarLink";
import Gmail from "./Gmail";
import Reminders from "./Reminders";
import Sub from "./Sub";
import Tasks from "./Tasks";

interface MyLifeProps {
  view: string | null;
  navigate: Navigate;
  notify: Notify;
  openTask: OpenTask;
  focusId: number | null;
  onFocused: () => void;
}

export default function MyLife({ view, navigate, notify, openTask, focusId, onFocused }: MyLifeProps) {
  if (view === "tasks") {
    return (
      <Sub title="Tasks" onBack={() => navigate("mylife", null)}>
        <Tasks notify={notify} focusId={focusId} onFocused={onFocused} onChanged={() => undefined} />
      </Sub>
    );
  }
  if (view === "reminders") {
    return (
      <Sub title="Reminders" onBack={() => navigate("mylife", null)}>
        <Reminders notify={notify} onOpenTask={openTask} />
      </Sub>
    );
  }
  if (view === "briefing") {
    return (
      <Sub title="Briefing" onBack={() => navigate("mylife", null)}>
        <Briefing notify={notify} />
      </Sub>
    );
  }
  if (view === "approvals") {
    return (
      <Sub title="Approvals" onBack={() => navigate("mylife", null)}>
        <Approvals notify={notify} onOpenTask={openTask} />
      </Sub>
    );
  }
  if (view === "bookings") {
    return (
      <Sub title="Bookings" onBack={() => navigate("mylife", null)}>
        <Bookings notify={notify} navigate={navigate} onOpenTask={openTask} />
      </Sub>
    );
  }
  if (view === "calendar") {
    return (
      <Sub title="Google Calendar" onBack={() => navigate("mylife", null)}>
        <CalendarLink notify={notify} />
      </Sub>
    );
  }
  if (view === "gmail") {
    return (
      <Sub title="Gmail" onBack={() => navigate("mylife", null)}>
        <Gmail notify={notify} onSent={() => navigate("mylife", "approvals")} />
      </Sub>
    );
  }

  const cards = [
    { key: "tasks", icon: <CalendarIcon />, title: "Tasks", blurb: "Everything Saath is doing for you" },
    { key: "reminders", icon: <BellIcon />, title: "Reminders", blurb: "Never miss what matters" },
    { key: "briefing", icon: <DocIcon />, title: "Briefing", blurb: "Your day, summarised" },
    { key: "approvals", icon: <CheckIcon />, title: "Approvals", blurb: "Actions waiting for your yes" },
    { key: "bookings", icon: <TicketIcon />, title: "Bookings", blurb: "Demo cabs and appointments" },
    { key: "calendar", icon: <CalendarIcon />, title: "Google Calendar", blurb: "Real events, synced with your yes" },
    { key: "gmail", icon: <DocIcon />, title: "Gmail", blurb: "Real email, sent with your yes" },
  ];

  return (
    <div className="mx-auto max-w-2xl">
      <h2 className="text-2xl font-bold">My Life</h2>
      <p className="mt-1 text-[15px] text-stone-500">Your day, organised in one calm place.</p>
      <div className="mt-4 grid grid-cols-2 gap-3">
        {cards.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => navigate("mylife", c.key)}
            className="flex min-h-28 flex-col items-start gap-1 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm"
          >
            <span className="text-teal-800">{c.icon}</span>
            <span className="text-base font-semibold">{c.title}</span>
            <span className="text-sm text-stone-500">{c.blurb}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
