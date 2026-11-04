// Hands-free voice checks against the real compiled voice.js + voiceOn.js.
// Run from frontend/: npm run test:voice
// Covers the reported bug: tapping Voice must start the mic immediately,
// never gated on async UI state (the old hook-only design failed this).
import voice from "./out/voice.js";
import engineModule from "./out/voiceOn.js";

const { matchesWakeWord, matchesStopPhrase } = voice;
const { VoiceEngine } = engineModule;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let passed = 0;

function check(name, actual, expected) {
  if (actual !== expected) {
    console.log(`FAIL ${name}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
    process.exitCode = 1;
  } else {
    passed += 1;
  }
}

const wordCases = [
  [matchesWakeWord, "Hi Saath", true],
  [matchesWakeWord, "hey saath", true],
  [matchesWakeWord, "Hello Saath!", true],
  [matchesWakeWord, "OK saath", true],
  [matchesWakeWord, "hi sath", true],
  [matchesWakeWord, "hey sat", true],
  [matchesWakeWord, "hi gizo", true],
  [matchesWakeWord, "hey jizo", true],
  [matchesWakeWord, "hi saath what is the weather", true],
  [matchesWakeWord, "high saath", true],
  [matchesWakeWord, "hey sath", true],
  [matchesWakeWord, "hi said", true],
  [matchesWakeWord, "hi sadh", true],
  [matchesWakeWord, "high sath", true],
  [matchesWakeWord, "saath", true],
  [matchesWakeWord, "saath remind me later", true],
  [matchesWakeWord, "i said hi", false],
  [matchesWakeWord, "the saath show", false],
  [matchesWakeWord, "hi there", false],
  [matchesWakeWord, "", false],
  [matchesWakeWord, "tell me about saath", false],
  [matchesStopPhrase, "thank you", true],
  [matchesStopPhrase, "Thanks!", true],
  [matchesStopPhrase, "thank you very much", true],
  [matchesStopPhrase, "bye", true],
  [matchesStopPhrase, "goodbye", true],
  [matchesStopPhrase, "stop", true],
  [matchesStopPhrase, "stop listening", true],
  [matchesStopPhrase, "that's all", true],
  [matchesStopPhrase, "remind me to stop by the shops", false],
  [matchesStopPhrase, "don't stop the music", false],
  [matchesStopPhrase, "what is the weather", false],
];

for (const [fn, input, expected] of wordCases) {
  check(`${fn.name}(${JSON.stringify(input)})`, fn(input), expected);
}

/* Fake mic: the test drives wake/error/end by hand, like a browser would. */
function makeHarness({ supported = true } = {}) {
  const created = [];
  const spoken = [];
  const notices = [];
  const phases = [];
  let woke = 0;
  let fatal = 0;
  const engine = new VoiceEngine(
    {
      isSupported: () => supported,
      createListener: (callbacks, lang) => {
        const mic = {
          lang,
          started: false,
          stopped: false,
          callbacks,
          start() {
            this.started = true;
          },
          stop() {
            this.stopped = true;
          },
        };
        created.push(mic);
        return mic;
      },
      speak: (text, opts) => {
        spoken.push(text);
        setImmediate(() => opts.onEnd());
      },
      notify: (message, isError) => notices.push([message, isError === true]),
      onWake: () => {
        woke += 1;
      },
      onFatal: () => {
        fatal += 1;
      },
      onPhase: (phase) => phases.push(phase),
      handoffMs: 30,
      restartBaseMs: 10,
      ackTimeoutMs: 40,
    },
    "en-US"
  );
  return { engine, created, spoken, notices, phases, woke: () => woke, fatal: () => fatal };
}

async function main() {
  // The reported bug: start() must begin listening synchronously.
  {
    const h = makeHarness();
    h.engine.start();
    check("start creates mic immediately", h.created.length, 1);
    check("mic started", h.created[0].started, true);
    check("phase after start", h.engine.getPhase(), "wake");
    check("start announces", h.notices.some(([m]) => m.includes("Hands-free is on")), true);
    h.engine.stop();
  }

  // Wake: ack spoken, Talk handed a turn, mic resumes listening.
  {
    const h = makeHarness();
    h.engine.start();
    h.created[0].callbacks.onWake();
    check("phase on wake", h.engine.getPhase(), "handoff");
    await sleep(20);
    check("ack spoken", h.spoken.join(","), "Yes?");
    check("talk handed a turn", h.woke(), 1);
    await sleep(60);
    check("mic resumes after handoff", h.created.length, 2);
    check("phase after handoff", h.engine.getPhase(), "wake");
    h.engine.stop();
  }

  // Fatal mic error: off, honest message, fatal reported once.
  {
    const h = makeHarness();
    h.engine.start();
    h.created[0].callbacks.onError("Microphone error: not-allowed", true);
    check("phase after fatal", h.engine.getPhase(), "off");
    check("fatal reported", h.fatal(), 1);
    check("fatal notice is error", h.notices.some(([, e]) => e), true);
    await sleep(60);
    check("no restart after fatal", h.created.length, 1);
  }

  // Transient error: mic restarts by itself.
  {
    const h = makeHarness();
    h.engine.start();
    h.created[0].callbacks.onError("Microphone error: network", false);
    await sleep(60);
    check("mic restarts after transient error", h.created.length, 2);
    check("phase after restart", h.engine.getPhase(), "wake");
    h.engine.stop();
  }

  // Stop: everything cancelled, stale mic events ignored.
  {
    const h = makeHarness();
    h.engine.start();
    h.engine.stop();
    check("phase after stop", h.engine.getPhase(), "off");
    check("mic stopped", h.created[0].stopped, true);
    h.created[0].callbacks.onError("Microphone error: network", false);
    await sleep(60);
    check("no restart after stop", h.created.length, 1);
  }

  // Unsupported browser: honest message, mic never touched.
  {
    const h = makeHarness({ supported: false });
    h.engine.start();
    check("mic never created when unsupported", h.created.length, 0);
    check("fatal reported when unsupported", h.fatal(), 1);
    check("phase stays off", h.engine.getPhase(), "off");
  }

  if (process.exitCode) {
    console.log("VOICE CHECK FAILED");
  } else {
    console.log(`VOICE CHECK PASS (${passed} assertions)`);
  }
}

await main();
