/* Voice input/output via the browser's built-in speech services. No keys, no uploads. */

interface Listener {
  start: () => void;
  stop: () => void;
}

type SpeechWindow = Window & {
  SpeechRecognition?: new () => SpeechRecognizer;
  webkitSpeechRecognition?: new () => SpeechRecognizer;
};

interface SpeechRecognizer {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

interface SpeechResultEvent {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
}

export function speechSupported(): boolean {
  const w = window as unknown as SpeechWindow;
  return !!w.SpeechRecognition || !!w.webkitSpeechRecognition;
}

export function speechOutputSupported(): boolean {
  return "speechSynthesis" in window;
}

export interface ListenerCallbacks {
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  onInterim?: (text: string) => void;
  onEnd?: () => void;
}

export function createListener(callbacks: ListenerCallbacks, lang = "en-US", opts: { interim?: boolean } = {}): Listener | null {
  const w = window as unknown as SpeechWindow;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  if (!Ctor) return null;
  let rec: SpeechRecognizer | null = null;
  return {
    start() {
      this.stop();
      rec = new Ctor();
      rec.lang = lang;
      rec.continuous = false;
      rec.interimResults = opts.interim === true;
      rec.onresult = (event: SpeechResultEvent) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) callbacks.onFinal(result[0].transcript.trim());
          else interim += result[0].transcript;
        }
        if (interim && callbacks.onInterim) callbacks.onInterim(interim.trim());
      };
      rec.onerror = (event: { error: string }) => callbacks.onError("Microphone error: " + event.error);
      rec.onend = () => {
        rec = null;
        callbacks.onEnd?.();
      };
      rec.start();
    },
    stop() {
      if (rec) {
        rec.onend = null;
        try { rec.stop(); } catch { /* already stopped */ }
        rec = null;
      }
    },
  };
}

/* Hands-free wake word: "Hi Jizo" (and close variants Saath hears). */

function cleanTranscript(transcript: string): string {
  return transcript
    .toLowerCase()
    .replace(/[^a-z\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const WAKE_GREETINGS = ["hi", "hey", "hello", "hai", "hay", "ok", "okay"];
const WAKE_NAMES = ["jizo", "jizou", "jeezo", "saath", "sath", "sat"];

export function matchesWakeWord(transcript: string): boolean {
  const words = new Set(cleanTranscript(transcript).split(" ").filter(Boolean));
  return WAKE_GREETINGS.some((g) => words.has(g)) && WAKE_NAMES.some((n) => words.has(n));
}

const STOP_EXACT = new Set(["thanks", "thank you", "goodbye", "good bye", "bye", "bye bye", "stop", "stop listening", "that s all", "done", "no thanks"]);
const STOP_PREFIX = ["thank you", "thanks", "goodbye", "bye bye"];

export function matchesStopPhrase(transcript: string): boolean {
  const clean = cleanTranscript(transcript);
  if (!clean) return false;
  if (STOP_EXACT.has(clean)) return true;
  return STOP_PREFIX.some((p) => clean.startsWith(p + " "));
}

export interface WakeCallbacks {
  onWake: () => void;
  onError: (message: string, fatal: boolean) => void;
  onEnd?: () => void;
}

/* Continuous wake-word listener. Ignores audio while Saath itself is speaking. */
export function createWakeListener(callbacks: WakeCallbacks, lang = "en-US"): Listener | null {
  const w = window as unknown as SpeechWindow;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  if (!Ctor) return null;
  let rec: SpeechRecognizer | null = null;
  let spokeAt = 0;
  let watch: number | null = null;
  return {
    start() {
      this.stop();
      rec = new Ctor();
      rec.lang = lang;
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (event: SpeechResultEvent) => {
        if (Date.now() - spokeAt < 800) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (matchesWakeWord(event.results[i][0].transcript)) {
            callbacks.onWake();
            return;
          }
        }
      };
      rec.onerror = (event: { error: string }) => {
        const fatal = event.error === "not-allowed" || event.error === "service-not-allowed";
        if (event.error === "aborted") return;
        callbacks.onError("Microphone error: " + event.error, fatal);
      };
      rec.onend = () => {
        rec = null;
        callbacks.onEnd?.();
      };
      rec.start();
      watch = window.setInterval(() => {
        if ("speechSynthesis" in window && window.speechSynthesis.speaking) spokeAt = Date.now();
      }, 250);
    },
    stop() {
      if (watch !== null) {
        window.clearInterval(watch);
        watch = null;
      }
      if (rec) {
        rec.onend = null;
        try { rec.stop(); } catch { /* already stopped */ }
        rec = null;
      }
    },
  };
}

export interface SpeakOptions {
  onStart?: () => void;
  onEnd?: () => void;
  rate?: number;
  lang?: string;
}

export function speak(text: string, opts: SpeakOptions = {}): void {
  if (!speechOutputSupported()) {
    opts.onEnd?.();
    return;
  }
  window.speechSynthesis.cancel();
  const voice = new SpeechSynthesisUtterance(text.slice(0, 1200));
  voice.lang = opts.lang || "en-US";
  if (opts.rate) voice.rate = opts.rate;
  let ended = false;
  const done = () => {
    if (!ended) {
      ended = true;
      opts.onEnd?.();
    }
  };
  voice.onstart = () => opts.onStart?.();
  voice.onend = done;
  voice.onerror = done;
  window.speechSynthesis.speak(voice);
  // Fallback: some browsers never fire onend, so watch from the side.
  let heard = false;
  const watch = window.setInterval(() => {
    if (window.speechSynthesis.speaking) heard = true;
    else if (heard) {
      window.clearInterval(watch);
      done();
    }
  }, 250);
  window.setTimeout(() => window.clearInterval(watch), 90000);
}

export function stopSpeaking(): void {
  if (speechOutputSupported()) window.speechSynthesis.cancel();
}

export function pauseSpeaking(): void {
  if (speechOutputSupported()) window.speechSynthesis.pause();
}

export function resumeSpeaking(): void {
  if (speechOutputSupported()) window.speechSynthesis.resume();
}
