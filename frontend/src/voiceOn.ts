import { useEffect, useState } from "react";
import type { Notify } from "./api";
import { createWakeListener, speak, speechSupported } from "./voice";
import type { WakeCallbacks } from "./voice";

/* Hands-free engine: listens for "Hi Saath" from any screen, then hands
   a voice turn to Talk. The mic runs only while this is switched on, pauses
   when the tab is hidden, and never starts by itself (browsers require a tap).

   The core is a framework-free class with injected dependencies so the
   lifecycle (the part that broke before: start must begin listening
   immediately, never gated on async UI state) is covered by
   `npm run test:voice`. The hook below is a thin React wrapper. */

export type VoicePhase = "off" | "wake" | "handoff";

type Listener = ReturnType<typeof createWakeListener>;

export interface VoiceEngineDeps {
  isSupported: () => boolean;
  createListener: (callbacks: WakeCallbacks, lang: string) => Listener;
  speak: (text: string, opts: { lang: string; onEnd: () => void }) => void;
  notify: Notify;
  onWake: () => void;
  onFatal: (message: string) => void;
  onPhase: (phase: VoicePhase) => void;
  onHeard?: (text: string) => void;
  handoffMs?: number;
  restartBaseMs?: number;
  ackTimeoutMs?: number;
  maxRestarts?: number;
}

export class VoiceEngine {
  private phase: VoicePhase = "off";
  private want = false;
  private lang: string;
  private listener: Listener = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private restarts = 0;
  private ends: number[] = [];
  private deps: VoiceEngineDeps;

  constructor(deps: VoiceEngineDeps, lang: string) {
    this.deps = deps;
    this.lang = lang;
  }

  updateDeps(patch: Partial<VoiceEngineDeps>) {
    this.deps = { ...this.deps, ...patch };
  }

  setLang(lang: string) {
    this.lang = lang;
    if (this.phase === "wake") this.beginWake();
  }

  getPhase(): VoicePhase {
    return this.phase;
  }

  start() {
    if (this.phase !== "off") return;
    if (!this.deps.isSupported()) {
      const message = "Voice input is not supported in this browser. It works best in Chrome or Edge.";
      this.deps.notify(message, true);
      this.deps.onFatal(message);
      return;
    }
    this.want = true;
    this.restarts = 0;
    this.ends = [];
    this.beginWake();
    if (this.phase !== "off") {
      this.deps.notify("Hands-free is on — say Hi Saath whenever you need me.");
    }
  }

  stop() {
    this.want = false;
    this.clearTimer();
    this.stopListener();
    this.setPhase("off");
  }

  pause() {
    this.stopListener();
  }

  resume() {
    if (this.phase === "wake") this.beginWake();
  }

  private setPhase(next: VoicePhase) {
    this.phase = next;
    this.deps.onPhase(next);
  }

  private clearTimer() {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private stopListener() {
    this.listener?.stop();
    this.listener = null;
  }

  private giveUp(message: string) {
    this.clearTimer();
    this.stopListener();
    this.setPhase("off");
    this.deps.notify(message, true);
    this.deps.onFatal(message);
  }

  private beginWake() {
    if (!this.want) return;
    this.clearTimer();
    this.stopListener();
    const listener = this.deps.createListener(
      {
        onWake: () => this.handleWake(),
        onError: (message, fatal) => this.handleError(message, fatal),
        onEnd: () => this.handleEnd(),
        onInterim: (text) => {
          if (this.phase === "wake") this.deps.onHeard?.(text);
        },
      },
      this.lang
    );
    if (!listener) {
      this.giveUp("Voice input is not supported in this browser.");
      return;
    }
    try {
      this.listener = listener;
      listener.start();
      this.restarts = 0;
      this.setPhase("wake");
    } catch {
      this.listener = null;
      this.giveUp("Could not start the microphone. Voice is now off.");
    }
  }

  private handleWake() {
    this.stopListener();
    this.setPhase("handoff");
    let fired = false;
    const fire = () => {
      if (fired) return;
      fired = true;
      this.deps.onWake();
      this.timer = setTimeout(() => {
        if (this.phase === "handoff") this.beginWake();
      }, this.deps.handoffMs ?? 12000);
    };
    this.deps.speak("Yes?", { lang: this.lang, onEnd: fire });
    setTimeout(fire, this.deps.ackTimeoutMs ?? 5000);
  }

  private handleError(message: string, fatal: boolean) {
    if (this.phase !== "wake") return;
    const max = this.deps.maxRestarts ?? 8;
    if (fatal || this.restarts >= max) {
      this.giveUp(message + " Voice is now off — tap Voice to try again.");
      return;
    }
    this.restarts += 1;
    const base = this.deps.restartBaseMs ?? 1000;
    const delay = Math.min(base * this.restarts, 5000);
    this.timer = setTimeout(() => {
      if (this.phase === "wake") this.beginWake();
    }, delay);
  }

  private handleEnd() {
    if (this.phase !== "wake") return;
    const now = Date.now();
    this.ends = [...this.ends.filter((t) => now - t < 10000), now];
    if (this.ends.length > 6) {
      this.giveUp("The microphone keeps stopping. Voice is now off — tap Voice to try again.");
      return;
    }
    this.timer = setTimeout(() => {
      if (this.phase === "wake") this.beginWake();
    }, 300);
  }
}

interface EngineOpts {
  enabled: boolean;
  lang: string;
  notify: Notify;
  onWake: () => void;
  onFatal: (message: string) => void;
}

export function useVoiceOnEngine({ enabled, lang, notify, onWake, onFatal }: EngineOpts) {
  const [phase, setPhase] = useState<VoicePhase>("off");
  const [heard, setHeard] = useState("");
  const [engine] = useState(
    () =>
      new VoiceEngine(
        {
          isSupported: speechSupported,
          createListener: (callbacks, listenerLang) => createWakeListener(callbacks, listenerLang),
          speak: (text, opts) => speak(text, opts),
          notify,
          onWake,
          onFatal,
          onPhase: (next) => setPhase(next),
          onHeard: (text) => setHeard(text),
        },
        lang
      )
  );

  useEffect(() => {
    engine.updateDeps({ notify, onWake, onFatal, onHeard: (text) => setHeard(text) });
  });

  useEffect(() => {
    if (!enabled) engine.stop();
  }, [enabled, engine]);

  useEffect(() => {
    engine.setLang(lang);
  }, [lang, engine]);

  useEffect(() => {
    function onHidden() {
      if (document.hidden) engine.pause();
      else engine.resume();
    }
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      document.removeEventListener("visibilitychange", onHidden);
      engine.stop();
    };
  }, [engine]);

  useEffect(() => {
    if (!heard) return;
    const timer = window.setTimeout(() => setHeard(""), 4000);
    return () => window.clearTimeout(timer);
  }, [heard]);

  return {
    phase,
    heard,
    start: () => engine.start(),
    stop: () => engine.stop(),
  };
}
