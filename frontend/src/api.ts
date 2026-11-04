/* Typed client for the Saath API (same origin, token in localStorage). */

export interface TaskRow {
  id: number;
  objective: string;
  department: string;
  agent: string;
  priority: string;
  dependencies: number[];
  deadline: string;
  status: string;
  result: string;
  evidence: string;
  created_at: string;
  updated_at: string;
}

export interface TaskEventRow {
  id: number;
  task_id: number;
  event_type: string;
  from_status: string;
  to_status: string;
  note: string;
  created_at: string;
}

export interface TaskDetail {
  task: TaskRow;
  events: TaskEventRow[];
}

export interface ChatReply {
  task_id: number;
  reply: string;
  model: string;
  input_chars: number;
  output_chars: number;
  children: number[];
}

export interface Briefing {
  task_id: number;
  summary: string;
  date: string;
  due_count: number;
  today_count: number;
  open_count: number;
}

export interface MemoryRow {
  id: number;
  layer: string;
  category: string;
  tier: string;
  key: string;
  value: string;
  department: string;
  agent: string;
  source_task_id: number | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
  blocked_by_consent?: boolean;
}

export interface ConsentRow {
  category: string;
  granted: boolean;
  updated_at: string;
}

export interface ApprovalRow {
  id: number;
  task_id: number;
  tool: string;
  args: unknown;
  status: string;
  note: string;
  result: string;
  expires_at: string;
  decided_at: string;
  created_at: string;
}

export interface FailureRow {
  id: number;
  task_id: number;
  agent: string;
  tool: string;
  error: string;
  context: unknown;
  created_at: string;
}

export interface NotificationRow {
  id: number;
  task_id: number;
  kind: string;
  text: string;
  created_at: string;
  read_at: string;
}

export interface WorkerReport {
  users: number;
  checked: number;
  notified: number;
  skipped_quiet: number;
  errors: { user_id?: number; error: string }[];
}

export interface WorkerStatus {
  running: boolean;
  interval_seconds: number;
  ticks_total: number;
  last_tick_at: string;
  last_report: Partial<WorkerReport>;
  last_error: string;
}

export interface ToolResult {
  status: string;
  approval_id?: number;
  result?: unknown;
}

export type Notify = (message: string, isError?: boolean) => void;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(fn: () => void): void {
  unauthorizedHandler = fn;
}

export function getToken(): string | null {
  return localStorage.getItem("saath_token");
}

function errorMessage(data: unknown, status: number): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (Array.isArray(detail)) {
      return detail.map((e) => (e as { msg?: string }).msg || JSON.stringify(e)).join("; ");
    }
    if (typeof detail === "string") return detail;
  }
  return "Request failed (" + status + ")";
}

export async function call<T>(method: string, path: string, body?: unknown, skipAuthRedirect = false): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && !skipAuthRedirect) {
    if (unauthorizedHandler) unauthorizedHandler();
    throw new ApiError("Session expired. Please log in again.", 401);
  }
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status);
  return data as T;
}
