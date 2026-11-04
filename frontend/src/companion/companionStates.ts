/* Companion state: Saath reacts to real app state, never at random. */

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
  idle: "Saath is here",
  listening: "Saath is listening",
  thinking: "Saath is thinking",
  speaking: "Saath is speaking",
  happy: "Saath is celebrating",
  empathetic: "Saath understands",
  teaching: "Saath is teaching",
  waiting: "Saath is working on a task",
  approval: "Saath needs your approval",
  connecting: "Saath is reconnecting",
  curious: "Saath is curious",
  excited: "Saath is excited",
  celebrating: "Saath is celebrating with you",
  sleepy: "Saath is resting",
};
