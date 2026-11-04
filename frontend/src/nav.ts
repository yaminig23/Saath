/* App navigation contract shared by the shell and all screens. */

export type Section = "home" | "talk" | "learn" | "mylife" | "connect" | "more";

export interface TalkParams {
  prompt?: string;
  promptId?: number;
  voiceTurn?: number;
}

export type Navigate = (section: Section, view?: string | null, params?: TalkParams) => void;

export type OpenTask = (id: number) => void;
