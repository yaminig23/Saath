import type { CompanionState } from "./companionStates";
import { STATE_LABEL } from "./companionStates";

/* Saath: Saath's blob companion. One SVG identity, CSS motion, stills for
   reduced motion. Expressions follow the Saath design brief (leaves, teal
   scarf with golden lotus, brows + blush + hands per state). */

const INK = "#30251F";
const BODY = "#FFF3DF";
const BODY_LINE = "#E7D9BE";
const SHADE = "#F1E4CC";
const BLUSH = "#F5B6A8";
const TEAL = "#248F89";
const TEAL_DARK = "#1B6E68";
const SAGE = "#A8CDB6";
const GOLD = "#F4BE55";
const AMBER = "#d97706";
const MOUTH = "#8a5a44";
const TONGUE = "#e8a0a0";
const HEART = "#E8736B";

interface SaathProps {
  state: CompanionState;
  size?: number;
}

const MOTION: Record<CompanionState, string> = {
  idle: "cloud-bob",
  listening: "cloud-lean",
  thinking: "cloud-bob-slow",
  speaking: "cloud-bob",
  happy: "cloud-wiggle",
  empathetic: "cloud-breathe",
  teaching: "cloud-bob",
  waiting: "cloud-bob-slow",
  approval: "cloud-nod",
  connecting: "cloud-bob-slower",
  curious: "cloud-lean",
  excited: "cloud-wiggle",
  celebrating: "cloud-wiggle",
  sleepy: "cloud-bob-slower",
};

type Eyes = "default" | "big" | "side" | "closed" | "soft" | "droopy";
type Brows = "flat" | "raised" | "raisedOne" | "worried" | "focused" | "sleepy";
type Mouth = "smile" | "smallSmile" | "flat" | "bigSmile" | "flap" | "open";
type Arms = "down" | "wave" | "up" | "point" | "heart";

const EYES: Record<CompanionState, Eyes> = {
  idle: "default",
  listening: "big",
  thinking: "side",
  speaking: "default",
  happy: "closed",
  empathetic: "soft",
  teaching: "default",
  waiting: "default",
  approval: "big",
  connecting: "default",
  curious: "big",
  excited: "big",
  celebrating: "closed",
  sleepy: "droopy",
};

const BROWS: Record<CompanionState, Brows> = {
  idle: "flat",
  listening: "raised",
  thinking: "focused",
  speaking: "flat",
  happy: "raised",
  empathetic: "worried",
  teaching: "raisedOne",
  waiting: "flat",
  approval: "raised",
  connecting: "flat",
  curious: "raisedOne",
  excited: "raised",
  celebrating: "raised",
  sleepy: "sleepy",
};

const MOUTHS: Record<CompanionState, Mouth> = {
  idle: "smile",
  listening: "smallSmile",
  thinking: "flat",
  speaking: "flap",
  happy: "bigSmile",
  empathetic: "smallSmile",
  teaching: "smile",
  waiting: "smile",
  approval: "open",
  connecting: "flat",
  curious: "smallSmile",
  excited: "bigSmile",
  celebrating: "bigSmile",
  sleepy: "smallSmile",
};

const ARMS: Record<CompanionState, Arms> = {
  idle: "down",
  listening: "wave",
  thinking: "down",
  speaking: "down",
  happy: "up",
  empathetic: "heart",
  teaching: "point",
  waiting: "down",
  approval: "wave",
  connecting: "down",
  curious: "down",
  excited: "up",
  celebrating: "up",
  sleepy: "down",
};

function Pupil({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill={INK} />
      <circle cx={cx + r * 0.32} cy={cy - r * 0.32} r={r * 0.38} fill="#ffffff" />
    </g>
  );
}

function EyesView({ kind }: { kind: Eyes }) {
  if (kind === "closed") {
    return (
      <g stroke={INK} strokeWidth={3.5} strokeLinecap="round" fill="none">
        <path d="M76,108 q12,-10 24,0" />
        <path d="M120,108 q12,-10 24,0" />
      </g>
    );
  }
  if (kind === "droopy") {
    return (
      <g stroke={INK} strokeWidth={3} strokeLinecap="round" fill="none">
        <path d="M78,110 q10,5 20,0" />
        <path d="M122,110 q10,5 20,0" />
      </g>
    );
  }
  const r = kind === "big" ? 7 : kind === "soft" ? 4.5 : 5.5;
  const dx = kind === "side" ? 5 : 0;
  const dy = kind === "soft" ? 2 : 1;
  return (
    <g>
      <ellipse cx={88} cy={106} rx={11} ry={13} fill="#ffffff" stroke={BODY_LINE} strokeWidth={1.5} />
      <ellipse cx={132} cy={106} rx={11} ry={13} fill="#ffffff" stroke={BODY_LINE} strokeWidth={1.5} />
      <Pupil cx={88 + dx} cy={106 + dy} r={r} />
      <Pupil cx={132 + dx} cy={106 + dy} r={r} />
    </g>
  );
}

function BrowsView({ kind }: { kind: Brows }) {
  const common = { stroke: INK, strokeWidth: 5, strokeLinecap: "round" } as const;
  switch (kind) {
    case "raised":
      return (
        <g {...common}>
          <path d="M76,80 h24" />
          <path d="M120,80 h24" />
        </g>
      );
    case "raisedOne":
      return (
        <g {...common}>
          <path d="M76,78 h24" />
          <path d="M120,88 h24" />
        </g>
      );
    case "worried":
      return (
        <g {...common}>
          <path d="M76,88 L100,80" />
          <path d="M120,80 L144,88" />
        </g>
      );
    case "focused":
      return (
        <g {...common}>
          <path d="M76,80 L100,87" />
          <path d="M120,87 L144,80" />
        </g>
      );
    case "sleepy":
      return (
        <g {...common} strokeWidth={4} opacity={0.7}>
          <path d="M80,90 h16" />
          <path d="M124,90 h16" />
        </g>
      );
    case "flat":
    default:
      return (
        <g {...common}>
          <path d="M76,84 h24" />
          <path d="M120,84 h24" />
        </g>
      );
  }
}

function MouthView({ kind }: { kind: Mouth }) {
  switch (kind) {
    case "smallSmile":
      return <path d="M101,129 q9,7 18,0" stroke={INK} strokeWidth={3} strokeLinecap="round" fill="none" />;
    case "flat":
      return <path d="M100,130 h20" stroke={INK} strokeWidth={3} strokeLinecap="round" />;
    case "bigSmile":
      return (
        <g>
          <path d="M92,126 Q110,148 128,126 Q124,143 110,143 Q96,143 92,126 Z" fill={MOUTH} />
          <ellipse cx={110} cy={138} rx={5} ry={3.5} fill={TONGUE} />
        </g>
      );
    case "flap":
      return (
        <g>
          <g className="mouth-a">
            <ellipse cx={110} cy={131} rx={9} ry={11} fill={MOUTH} />
            <ellipse cx={110} cy={136} rx={5} ry={4} fill={TONGUE} />
          </g>
          <path className="mouth-b" d="M101,129 q9,7 18,0" stroke={INK} strokeWidth={3} strokeLinecap="round" fill="none" />
        </g>
      );
    case "open":
      return <ellipse cx={110} cy={132} rx={6.5} ry={8.5} fill={MOUTH} />;
    case "smile":
    default:
      return <path d="M96,128 q14,11 28,0" stroke={INK} strokeWidth={3.5} strokeLinecap="round" fill="none" />;
  }
}

function Hand({ x, y }: { x: number; y: number }) {
  return <circle cx={x} cy={y} r={8} fill={BODY} stroke={BODY_LINE} strokeWidth={2} />;
}

function ArmsView({ pose }: { pose: Arms }) {
  const arm = { fill: BODY, stroke: BODY_LINE, strokeWidth: 2 };
  if (pose === "up") {
    return (
      <g>
        <rect x={36} y={76} width={13} height={36} rx={6.5} {...arm} />
        <Hand x={42} y={72} />
        <rect x={171} y={76} width={13} height={36} rx={6.5} {...arm} />
        <Hand x={178} y={72} />
      </g>
    );
  }
  if (pose === "point") {
    return (
      <g>
        <rect x={36} y={116} width={13} height={32} rx={6.5} {...arm} />
        <Hand x={42} y={152} />
        <rect x={172} y={110} width={36} height={13} rx={6.5} {...arm} />
        <Hand x={212} y={116} />
      </g>
    );
  }
  if (pose === "wave") {
    return (
      <g>
        <rect x={36} y={116} width={13} height={32} rx={6.5} {...arm} />
        <Hand x={42} y={152} />
        <rect x={171} y={76} width={13} height={36} rx={6.5} {...arm} />
        <Hand x={178} y={72} />
      </g>
    );
  }
  if (pose === "heart") {
    return (
      <g>
        <rect x={52} y={128} width={13} height={30} rx={6.5} {...arm} />
        <rect x={155} y={128} width={13} height={30} rx={6.5} {...arm} />
        <path
          d="M110,158 c-9,-9 -21,-2 -16,8 l16,12 l16,-12 c5,-10 -7,-17 -16,-8"
          fill={HEART}
        />
      </g>
    );
  }
  return (
    <g>
      <rect x={36} y={116} width={13} height={32} rx={6.5} {...arm} />
      <Hand x={42} y={152} />
      <rect x={171} y={116} width={13} height={32} rx={6.5} {...arm} />
      <Hand x={178} y={152} />
    </g>
  );
}

function Lotus({ x, y, s }: { x: number; y: number; s: number }) {
  const petal = (rotate: number) => (
    <ellipse cx={x} cy={y - 4 * s} rx={2.6 * s} ry={6 * s} fill={GOLD} transform={`rotate(${rotate} ${x} ${y})`} />
  );
  return (
    <g>
      {petal(-38)}
      {petal(-19)}
      {petal(0)}
      {petal(19)}
      {petal(38)}
    </g>
  );
}

export default function Saath({ state, size = 160 }: SaathProps) {
  const tilt = state === "curious" ? "rotate(-4 110 120)" : undefined;
  const glow = state === "happy" || state === "excited" || state === "celebrating";
  return (
    <svg
      width={size}
      height={(size * 200) / 220}
      viewBox="0 0 220 200"
      role="img"
      aria-label={STATE_LABEL[state]}
      style={{ overflow: "visible" }}
    >
      {(state === "listening" || state === "connecting") && (
        <circle
          cx={110}
          cy={120}
          r={76}
          fill="none"
          stroke={TEAL}
          strokeWidth={3}
          className={state === "listening" ? "cloud-ring" : "cloud-ring-slow"}
        />
      )}
      <ellipse cx={110} cy={190} rx={58} ry={7} fill={INK} opacity={0.08} />
      <g className={MOTION[state]} transform={tilt}>
        <ArmsView pose={ARMS[state]} />
        <path
          d="M110,58 C148,58 176,90 176,128 C176,162 144,188 110,188 C76,188 44,162 44,128 C44,90 72,58 110,58 Z"
          fill={BODY}
          stroke={BODY_LINE}
          strokeWidth={2.5}
        />
        <ellipse cx={110} cy={168} rx={50} ry={18} fill={SHADE} opacity={0.65} />
        <ellipse cx={86} cy={92} rx={17} ry={11} fill="#ffffff" opacity={0.55} />
        <path d="M110,58 C100,50 90,48 82,52 C90,58 102,60 110,58 Z" fill={TEAL} />
        <path d="M110,58 C120,50 130,48 138,52 C130,58 118,60 110,58 Z" fill={TEAL} />
        <path d="M86,54 L106,57" stroke={SAGE} strokeWidth={1.5} strokeLinecap="round" />
        <path d="M114,57 L134,54" stroke={SAGE} strokeWidth={1.5} strokeLinecap="round" />
        <path d="M50,146 Q110,164 170,146 L170,162 Q110,180 50,162 Z" fill={TEAL} />
        <path d="M50,158 Q110,176 170,158" stroke={TEAL_DARK} strokeWidth={2} fill="none" opacity={0.6} />
        <rect x={148} y={158} width={17} height={26} rx={8} fill={TEAL} />
        <rect x={148} y={158} width={17} height={26} rx={8} fill="none" stroke={TEAL_DARK} strokeWidth={1.5} opacity={0.6} />
        <Lotus x={88} y={164} s={1} />
        <ellipse cx={68} cy={126} rx={8} ry={5} fill={BLUSH} opacity={glow ? 0.85 : 0.6} />
        <ellipse cx={152} cy={126} rx={8} ry={5} fill={BLUSH} opacity={glow ? 0.85 : 0.6} />
        <BrowsView kind={BROWS[state]} />
        <EyesView kind={EYES[state]} />
        <MouthView kind={MOUTHS[state]} />
        {state === "approval" && (
          <g className="cloud-badge">
            <circle cx={170} cy={48} r={15} fill={AMBER} />
            <text x={170} y={55} textAnchor="middle" fontSize={20} fontWeight="bold" fill="#ffffff">
              !
            </text>
          </g>
        )}
        {state === "waiting" && (
          <g fill={TEAL}>
            <circle className="cloud-dot" cx={92} cy={34} r={4} />
            <circle className="cloud-dot cloud-dot-2" cx={110} cy={34} r={4} />
            <circle className="cloud-dot cloud-dot-3" cx={128} cy={34} r={4} />
          </g>
        )}
      </g>
    </svg>
  );
}
