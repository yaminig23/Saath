import { useEffect, useState } from "react";
import { call } from "../api";
import type { Notify } from "../api";
import { SPEECH_LANGS, useSettings } from "../settings";

/* Real settings: every control changes behaviour on this device. */

function Toggle({ label, hint, on, onClick }: { label: string; hint?: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className="flex min-h-14 w-full items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-left"
    >
      <span className="grow">
        <span className="block text-base font-medium">{label}</span>
        {hint && <span className="block text-sm text-stone-500">{hint}</span>}
      </span>
      <span className={"flex h-8 w-14 shrink-0 items-center rounded-full px-1 " + (on ? "justify-end bg-teal-800" : "justify-start bg-stone-300")}>
        <span className="h-6 w-6 rounded-full bg-white" />
      </span>
    </button>
  );
}

function Choices<T extends string | number>({
  label,
  options,
  value,
  onPick,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onPick: (v: T) => void;
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white px-4 py-3">
      <div className="text-base font-medium">{label}</div>
      <div className="mt-2 flex gap-2">
        {options.map((o) => (
          <button
            key={String(o.value)}
            type="button"
            onClick={() => onPick(o.value)}
            aria-pressed={value === o.value}
            className={
              "min-h-12 grow rounded-xl border px-3 " +
              (value === o.value ? "border-teal-800 bg-teal-50 font-semibold text-teal-900" : "border-stone-300 bg-white")
            }
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

interface StoredMemory {
  id: number;
  layer: string;
  category: string;
  key: string;
  value: string;
}

export default function SettingsScreen({ notify, onLogout, onReplayWelcome }: { notify: Notify; onLogout: () => void; onReplayWelcome?: () => void }) {
  const { settings, update } = useSettings();
  const [backend, setBackend] = useState("Checking…");
  const [chatLang, setChatLang] = useState("en");
  const [chatLangId, setChatLangId] = useState<number | null>(null);

  async function load() {
    try {
      const h = await call<{ status: string; app: string; env: string }>("GET", "/health");
      setBackend(`Connected Â· ${h.app} Â· ${h.env}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unreachable";
      setBackend(message);
      notify("Backend unreachable: " + message, true);
    }
    try {
      const rows = await call<StoredMemory[]>("GET", "/memories");
      const found = rows.find((r) => r.layer === "user" && r.key === "language");
      if (found) {
        setChatLangId(found.id);
        setChatLang(found.value === "hindi" ? "hi" : "en");
      }
    } catch {
      /* language stays English until saved */
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notify]);

  async function pickChatLang(next: string) {
    setChatLang(next);
    const value = next === "hi" ? "hindi" : "english";
    try {
      if (chatLangId === null) {
        const created = await call<StoredMemory>("POST", "/memories", {
          layer: "user",
          category: "preferences",
          key: "language",
          value,
        });
        setChatLangId(created.id);
      } else {
        await call("PUT", "/memories/" + chatLangId, { value });
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not save language.", true);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Choices
        label="Text size"
        value={settings.textSize}
        onPick={(v) => update({ textSize: v })}
        options={[
          { value: "normal", label: "Normal" },
          { value: "large", label: "Large" },
          { value: "xl", label: "Largest" },
        ]}
      />
      <Toggle label="High contrast" hint="Plain black on white" on={settings.highContrast} onClick={() => update({ highContrast: !settings.highContrast })} />
            <Toggle label="Dark mode" hint="Warm charcoal theme" on={settings.darkMode} onClick={() => update({ darkMode: !settings.darkMode })} />
      <Toggle label="Reduce motion" hint="Saath holds still" on={settings.reduceMotion} onClick={() => update({ reduceMotion: !settings.reduceMotion })} />
      <Toggle label="Voice replies" hint="Saath reads answers aloud" on={settings.voiceReplies} onClick={() => update({ voiceReplies: !settings.voiceReplies })} />
      <Choices
        label="Speech speed"
        value={settings.speechRate}
        onPick={(v) => update({ speechRate: v })}
        options={[
          { value: 0.75, label: "Slow" },
          { value: 1, label: "Normal" },
          { value: 1.25, label: "Fast" },
        ]}
      />
      <label className="block rounded-xl border border-stone-200 bg-white px-4 py-3 text-base font-medium">
        Voice language
        <select
          className="mt-2 min-h-12 w-full rounded-xl border border-stone-300 bg-white px-3"
          value={settings.lang}
          onChange={(e) => update({ lang: e.target.value })}
        >
          {SPEECH_LANGS.map((lang) => (
            <option key={lang} value={lang}>
              {lang}
            </option>
          ))}
        </select>
      </label>
      <Choices
        label="Saath replies in"
        value={chatLang}
        onPick={(v) => pickChatLang(v).catch(() => undefined)}
        options={[
          { value: "en", label: "English" },
          { value: "hi", label: "Hindi" },
        ]}
      />
      <div className="rounded-xl border border-stone-200 bg-white px-4 py-3 text-[15px]">
        <span className="font-medium">Backend: </span>
        <span className="text-stone-600">{backend}</span>
      </div>
      <div className="rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-500">
        Saath keeps your sign-in and the AI key on the server. These display and voice settings stay on this device only.
      </div>
      {onReplayWelcome && (
        <button
          type="button"
          onClick={onReplayWelcome}
          className="min-h-14 rounded-xl border border-stone-300 bg-white px-4 py-3 text-base font-medium"
        >
          Replay welcome tour
        </button>
      )}
      <button
        type="button"
        onClick={onLogout}
        className="min-h-14 rounded-xl border border-red-200 bg-white px-4 py-3 text-base font-semibold text-red-800"
      >
        Log out
      </button>
    </div>
  );
}
