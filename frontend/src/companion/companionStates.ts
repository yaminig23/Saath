/* Companion state: Jizo reacts to real app state, never at random. */

export type CompanionState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "happy"
  | "empathetic"
  | "teaching"
  | "waiting"
  | "approval"
  | "connecting"
  | "curious"
  | "excited"
  | "celebrating"
  | "sleepy";

export interface AppStatus {
  online: boolean;
  listening: boolean;
  speaking: boolean;
  thinking: boolean;
  teaching: boolean;
  taskRunning: boolean;
  approvalPending: boolean;
  transient?: CompanionState | null;
}

export function resolveCompanionState(s: AppStatus): CompanionState {
  if (!s.online) return "connecting";
  if (s.listening) return "listening";
  if (s.speaking) return "speaking";
  if (s.thinking) return "thinking";
  if (s.teaching) return "teaching";
  if (s.approvalPending) return "approval";
  if (s.taskRunning) return "waiting";
  if (
    s.transient === "happy" ||
    s.transient === "empathetic" ||
    s.transient === "curious" ||
    s.transient === "excited" ||
    s.transient === "celebrating" ||
    s.transient === "sleepy"
  )
    return s.transient;
  return "idle";
}

export const STATE_LABEL: Record<CompanionState, string> = {
  idle: "Jizo is here",
  listening: "Jizo is listening",
  thinking: "Jizo is thinking",
  speaking: "Jizo is speaking",
  happy: "Jizo is celebrating",
  empathetic: "Jizo understands",
  teaching: "Jizo is teaching",
  waiting: "Jizo is working on a task",
  approval: "Jizo needs your approval",
  connecting: "Jizo is reconnecting",
  curious: "Jizo is curious",
  excited: "Jizo is excited",
  celebrating: "Jizo is celebrating with you",
  sleepy: "Jizo is resting",
};
