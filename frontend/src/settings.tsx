/* Genuine accessibility + voice settings, stored on this device only. */
/* eslint-disable react-refresh/only-export-components -- context module by design: provider + hook + constants live together */

import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

export interface SaathSettings {
  textSize: "normal" | "large" | "xl";
  highContrast: boolean;
  reduceMotion: boolean;
  voiceReplies: boolean;
  speechRate: number;
  lang: string;
  voiceOn: boolean;
  darkMode: boolean;
}

const DEFAULTS: SaathSettings = {
  textSize: "normal",
  highContrast: false,
  reduceMotion: false,
  voiceReplies: true,
  speechRate: 1,
  lang: "en-US",
  voiceOn: false,
  darkMode: true,
};

export const SPEECH_LANGS = ["en-US", "en-GB", "hi-IN", "es-ES", "fr-FR", "de-DE"];

const KEY = "saath_settings";

function load(): SaathSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<SaathSettings>;
    return { ...DEFAULTS, ...parsed };
  } catch {
    return DEFAULTS;
  }
}

interface SettingsContextValue {
  settings: SaathSettings;
  update: (patch: Partial<SaathSettings>) => void;
}

const SettingsContext = createContext<SettingsContextValue>({ settings: DEFAULTS, update: () => undefined });

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<SaathSettings>(load);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {
      /* private mode: settings simply don't persist */
    }
    const root = document.documentElement;
    root.style.fontSize = settings.textSize === "xl" ? "20px" : settings.textSize === "large" ? "18px" : "";
    root.classList.toggle("a11y-contrast", settings.highContrast);
    root.classList.toggle("a11y-motion", settings.reduceMotion);
    root.classList.toggle("dark", settings.darkMode);
  }, [settings]);

  return (
    <SettingsContext.Provider value={{ settings, update: (patch) => setSettings((s) => ({ ...s, ...patch })) }}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings(): SettingsContextValue {
  return useContext(SettingsContext);
}
