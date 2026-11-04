import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { ChatReply, Notify } from "../api";
import Jizo from "../companion/Jizo";
import { useSettings } from "../settings";
import { createListener, matchesStopPhrase, pauseSpeaking, resumeSpeaking, speak, speechOutputSupported, speechSupported, stopSpeaking } from "../voice";

interface Msg {
  mine: boolean;
  text: string;
  meta: string;
}

export default function Chat({ notify, initial, promptId, voiceTurn, handsFree }: { notify: Notify; initial?: string; promptId?: number; voiceTurn?: number; handsFree?: boolean }) {
  const [messages, setMessages] = useState<Msg[]>(() => {
    try {
      const raw = localStorage.getItem("saath_chat");
      if (!raw) return [];
      const parsed = JSON.parse(raw) as Msg[];
      return Array.isArray(parsed) ? parsed.slice(-50) : [];
    } catch {
      return [];
    }
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [paused, setPaused] = useState(false);
  const { settings, update } = useSettings();
  const listenerRef = useRef<ReturnType<typeof createListener>>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const canHear = speechSupported();
  const canSpeak = speechOutputSupported();
  const sentRef = useRef<number | null>(null);
  const voiceTurnRef = useRef<number | null>(null);
  const [conversation, setConversation] = useState(false);
  const convRef = useRef(false);
  const busyRef = useRef(false);
  const genRef = useRef(0);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, busy]);

  useEffect(() => () => {
    listenerRef.current?.stop();
    stopSpeaking();
  }, []);

  useEffect(() => {
    if (initial !== undefined && promptId !== undefined && sentRef.current !== promptId) {
      sentRef.current = promptId;
      send(initial).catch((err: unknown) => notify(err instanceof Error ? err.message : "Chat failed.", true));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial, promptId]);

  useEffect(() => {
    if (voiceTurn !== undefined && voiceTurnRef.current !== voiceTurn) {
      voiceTurnRef.current = voiceTurn;
      if (busyRef.current) {
        notify("Let me finish this answer — say Hi Jizo again after.");
        return;
      }
      if (!canHear) {
        notify("Voice input is not supported in this browser.", true);
        return;
      }
      stopSpeaking();
      // A wake-word turn must reset speech state and open the mic atomically.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSpeaking(false);
      setPaused(false);
      convRef.current = true;
      setConversation(true);
      startConversationListen();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceTurn]);

  useEffect(() => {
    try {
      localStorage.setItem("saath_chat", JSON.stringify(messages.slice(-50)));
    } catch {
      /* private mode: history simply isn't kept */
    }
  }, [messages]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    stopSpeaking();
    setSpeaking(false);
    setPaused(false);
    setMessages((prev) => [...prev, { mine: true, text: message, meta: "" }]);
    try {
      const reply = await call<ChatReply>("POST", "/chat", { message });
      setMessages((prev) => [...prev, { mine: false, text: reply.reply, meta: reply.model + " · task " + reply.task_id }]);
      if (settings.voiceReplies && canSpeak) {
        setPaused(false);
        speak(reply.reply, {
          rate: settings.speechRate,
          lang: settings.lang,
          onStart: () => setSpeaking(true),
          onEnd: () => {
            setSpeaking(false);
            setPaused(false);
            if (convRef.current) startConversationListen();
          },
        });
      } else if (convRef.current) {
        startConversationListen();
      }
    } catch (err) {
      endConversation();
      notify(err instanceof Error ? err.message : "Chat failed.", true);
    } finally {
      setBusy(false);
    }
  }

  function endConversation() {
    convRef.current = false;
    setConversation(false);
    genRef.current += 1;
  }

  function startConversationListen() {
    if (!canHear) {
      endConversation();
      return;
    }
    listenerRef.current?.stop();
    const gen = ++genRef.current;
    const listener = createListener(
      {
        onFinal: (text) => {
          if (gen !== genRef.current) return;
          genRef.current += 1;
          setListening(false);
          if (!convRef.current) return;
          if (matchesStopPhrase(text)) {
            endConversation();
            if (settings.voiceReplies && canSpeak) {
              speak("Goodbye for now.", { rate: settings.speechRate, lang: settings.lang });
            } else {
              notify("Hands-free paused — say Hi Jizo anytime.");
            }
            return;
          }
          if (busyRef.current) {
            endConversation();
            setInput(text);
            notify("I'm still thinking — your words are in the box. Press Send.");
            return;
          }
          send(text).catch((err: unknown) => {
            endConversation();
            notify(err instanceof Error ? err.message : "Chat failed.", true);
          });
        },
        onInterim: (text) => {
          if (gen === genRef.current) setInput(text);
        },
        onError: (message) => {
          if (gen !== genRef.current) return;
          endConversation();
          setListening(false);
          notify(message, true);
        },
        onEnd: () => {
          if (gen !== genRef.current || !convRef.current) return;
          endConversation();
          setListening(false);
        },
      },
      settings.lang,
      { interim: true }
    );
    if (!listener) {
      endConversation();
      notify("Voice input is not supported in this browser.", true);
      return;
    }
    listenerRef.current = listener;
    listener.start();
    setListening(true);
  }

  function toggleMic() {
    if (handsFree) {
      notify("Hands-free is on — just say Hi Jizo. Turn Voice off above for the button.");
      return;
    }
    if (listening) {
      endConversation();
      listenerRef.current?.stop();
      setListening(false);
      return;
    }
    endConversation();
    const listener = createListener(
      {
        onFinal: (text) => {
          setListening(false);
          send(text).catch((err: unknown) => notify(err instanceof Error ? err.message : "Chat failed.", true));
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

  function stopAll() {
    endConversation();
    listenerRef.current?.stop();
    setListening(false);
    stopSpeaking();
    setSpeaking(false);
    setPaused(false);
  }

  function repeatLast() {
    const last = [...messages].reverse().find((m) => !m.mine);
    if (!last || !canSpeak) return;
    setPaused(false);
    speak(last.text, {
      rate: settings.speechRate,
      lang: settings.lang,
      onStart: () => setSpeaking(true),
      onEnd: () => {
        setSpeaking(false);
        setPaused(false);
      },
    });
  }

  function togglePause() {
    if (!speaking) return;
    if (paused) {
      resumeSpeaking();
      setPaused(false);
    } else {
      pauseSpeaking();
      setPaused(true);
    }
  }

  function clearHistory() {
    stopAll();
    setMessages([]);
    try {
      localStorage.removeItem("saath_chat");
    } catch {
      /* nothing stored */
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await send(input);
  }

  const smallBtn = "min-h-10 rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm";

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Jizo state={listening ? "listening" : speaking ? "speaking" : busy ? "thinking" : "idle"} size={64} />
        <div>
          <h2 className="text-xl font-bold">Talk</h2>
          <p className="text-sm text-stone-500" role="status">
            {listening
              ? "I'm listening…"
              : speaking
                ? paused
                  ? "Paused."
                  : "Speaking…"
                : busy
                  ? "Let me think about that."
                  : "Talk or speak — Saath answers, plans, and delegates."}
          </p>
        </div>
        {canSpeak && (
          <button
            type="button"
            onClick={() => { update({ voiceReplies: !settings.voiceReplies }); stopSpeaking(); }}
            className={smallBtn + " ml-auto"}
            aria-pressed={settings.voiceReplies}
          >
            {settings.voiceReplies ? "Voice replies: on" : "Voice replies: off"}
          </button>
        )}
        {speaking && (
          <button type="button" onClick={togglePause} className={smallBtn}>
            {paused ? "Resume" : "Pause"}
          </button>
        )}
        {speaking && (
          <button type="button" onClick={stopAll} className={smallBtn}>
            Stop
          </button>
        )}
        {!speaking && messages.some((m) => !m.mine) && (
          <button type="button" onClick={repeatLast} className={smallBtn}>
            Repeat
          </button>
        )}
        {messages.length > 0 && (
          <button type="button" onClick={clearHistory} className={smallBtn}>
            Clear
          </button>
        )}
      </div>
      {conversation && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-2xl border border-teal-800 bg-teal-50 px-4 py-2 text-[15px]" role="status">
          <span className="grow">Hands-free conversation — say “thank you” to stop.</span>
          <button type="button" onClick={stopAll} className={smallBtn}>
            Stop
          </button>
        </div>
      )}
      <div className="chat-scroll flex max-h-[46vh] min-h-[240px] flex-col gap-2 overflow-y-auto rounded-2xl border border-stone-200 bg-white p-3">
        {messages.length === 0 && (
          <p className="text-sm text-stone-500">Say hello, ask a question, or request something: “remind me to drink water tomorrow at 9am and give me my briefing”.</p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.mine
                ? "max-w-[85%] self-end rounded-2xl rounded-br-md bg-teal-800 px-3 py-2 text-white"
                : "max-w-[85%] self-start rounded-2xl rounded-bl-md border border-stone-200 bg-white px-3 py-2 shadow-sm"
            }
          >
            <div className="whitespace-pre-wrap break-words text-[15px]">{m.text}</div>
            {m.meta && <div className="mt-1 text-xs opacity-75">{m.meta}</div>}
          </div>
        ))}
        {busy && <div className="self-start text-sm italic text-stone-500">Thinking…</div>}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input
          className="min-h-12 grow rounded-xl border border-stone-300 bg-white px-3"
          placeholder="Type a message, or use the microphone…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        {canHear && (
          <button
            type="button"
            onClick={toggleMic}
            className={"min-h-12 rounded-xl border px-4 " + (listening ? "border-red-700 text-red-700" : "border-stone-300 bg-white")}
            aria-pressed={listening}
          >
            {listening ? "Stop" : handsFree ? "Say Hi Jizo" : "Speak"}
          </button>
        )}
        <button type="submit" disabled={busy} className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50">
          Send
        </button>
      </form>
    </div>
  );
}
