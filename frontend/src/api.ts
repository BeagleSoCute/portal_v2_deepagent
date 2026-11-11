export type BuiltinTool = { kind: 'builtin'; ref: string; params: Record<string, unknown> };
export type PythonTool = {
  kind: 'python';
  ref: string;
  description: string;
  code: string;
  input_schema: unknown;
  timeout_s: number;
};
export type HttpTool = { kind: 'http'; ref: string; description: string; spec: unknown };
export type McpTool = { kind: 'mcp'; server: string; include_tools: string[]; exclude_tools: string[] };
export type Tool = BuiltinTool | PythonTool | HttpTool | McpTool;

export type Skill = { name: string; description: string; body: string };
export type SubAgent = {
  name: string;
  description: string;
  system_prompt: string | null;
  model: string | null;
  connection: string | null;
  tools: string[];
  mode: 'isolated' | 'fork';
};
export type MiddlewareEntry = { ref: string; params: Record<string, unknown> };

export type Spec = {
  schema_version: 1;
  id?: string | null;
  version: number;
  name: string;
  description: string;
  model: { connection: string | null; ref: string; temperature: number | null; max_tokens: number | null };
  instructions: string;
  skills: Skill[];
  memory: { name: string; body: string }[];
  tools: Tool[];
  mcp_servers: Record<string, unknown>;
  subagents: SubAgent[];
  middleware: MiddlewareEntry[];
  hitl: { interrupt_on: Record<string, boolean> };
  response_format: unknown;
  limits: { max_steps: number; run_timeout_s: number; max_tokens_per_run: number };
  backend_preset: 'state' | 'store';
  updated_at?: string;
};

export type AgentRow = {
  id: string;
  name: string;
  description: string;
  version: number;
  model: string;
  updated_at: string;
  versions: number[];
};

export type Connection = {
  id: string;
  name: string;
  kind: string;
  kind_label: string;
  base_url: string | null;
  default_model: string;
  has_key: boolean;
  api_key_masked: string;
  updated_at: string;
};

export type ConnectionInput = {
  name: string;
  kind: string;
  base_url: string | null;
  default_model: string;
  api_key?: string | null;
};

export type JsonSchema = {
  type?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  enum?: string[];
  default?: unknown;
  minimum?: number;
};

export type Registry = {
  connection_kinds: { ref: string; label: string }[];
  tools: { kind: 'builtin'; ref: string; label: string; doc: string }[];
  python_tools: { enabled: boolean; allowed_imports: string[]; note: string };
  middleware: { ref: string; label: string; note: string; params_schema: JsonSchema }[];
};

export type AuditRun = {
  request_id: string;
  started_at: string;
  rows_count: number;
  tool_calls: number;
  failed_rows: number;
  agent_name: string | null;
  question: string | null;
  status: string | null;
  elapsed_ms: number | null;
};

export type AuditRow = {
  id: string;
  created_at: string;
  resource_type: string;
  resource_id: string | null;
  action: string;
  status: string;
  actor_name: string | null;
  request: string | null;
  result: unknown;
  changed_fields: unknown;
  optional_info: { event?: string } | null;
};

export type AuditStats = { total_rows: number; by_resource_type: Record<string, number> };

export type ToolCall = { name: string; args: Record<string, unknown>; id: string };
export type ActionRequest = { name: string; args: Record<string, unknown>; description?: string };

export type RunEvent =
  | { event: 'start'; data: { agent: string; version: number; model: string; tools: string[] } }
  | { event: 'token'; data: { text: string; node?: string } }
  | { event: 'tool_calls'; data: { node: string; calls: ToolCall[] } }
  | { event: 'step'; data: { node: string } }
  | { event: 'interrupt'; data: { payload: { id: string; value: { action_requests?: ActionRequest[] } }[] } }
  | { event: 'error'; data: { message: string } }
  | { event: 'done'; data: { elapsed_s: number } };

export class ApiError extends Error {
  constructor(
    public status: number,
    public problems: string[],
  ) {
    super(problems.join('\n'));
  }
}

// FastAPI puts the reason in `detail`: a string, a list of validation errors, or {problems}.
async function problemsOf(response: Response): Promise<string[]> {
  try {
    const { detail } = await response.json();
    if (typeof detail === 'string') return [detail];
    if (Array.isArray(detail?.problems)) return detail.problems;
    if (Array.isArray(detail)) return detail.map((d) => `${(d.loc ?? []).join('.')}: ${d.msg}`);
  } catch {
    // Not JSON: fall through to the status line.
  }
  return [`คำขอล้มเหลว (${response.status})`];
}

/** The browser is already on its way to another page, so there is nothing to show. */
export class Redirecting extends Error {}

// No session: go to login. Signed in without the access role: go to the Portal's denied page.
async function leaveIfShutOut(response: Response): Promise<void> {
  if (response.status === 401) {
    window.location.assign('/auth/login');
    throw new Redirecting();
  }
  if (response.status === 403) {
    const body = await response.clone().json().catch(() => null);
    if (body?.detail === 'no_access' && body.denied_url) {
      window.location.assign(body.denied_url);
      throw new Redirecting();
    }
  }
}

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await leaveIfShutOut(response);
  if (!response.ok) throw new ApiError(response.status, await problemsOf(response));
  return response.json();
}

export type Me = { username: string; auth: boolean; portal_url: string };

export const api = {
  me: () => call<Me>('/api/me'),
  registry: () => call<Registry>('/api/registry'),
  agents: () => call<AgentRow[]>('/api/agents'),
  agent: (id: string) => call<Spec>(`/api/agents/${id}`),
  createAgent: (spec: Partial<Spec>) => call<Spec>('/api/agents', 'POST', spec),
  saveAgent: (id: string, spec: Spec) => call<Spec>(`/api/agents/${id}`, 'PUT', spec),
  deleteAgent: (id: string) => call<unknown>(`/api/agents/${id}`, 'DELETE'),
  versions: (id: string) => call<{ versions: number[]; head: number }>(`/api/agents/${id}/versions`),
  activate: (id: string, version: number) => call<Spec>(`/api/agents/${id}/activate/${version}`, 'POST'),
  validate: (spec: unknown) =>
    call<{ ok: boolean; problems: string[] }>('/api/specs/validate', 'POST', spec),

  connections: () => call<Connection[]>('/api/connections'),
  createConnection: (input: ConnectionInput) => call<Connection>('/api/connections', 'POST', input),
  deleteConnection: (id: string) => call<unknown>(`/api/connections/${id}`, 'DELETE'),
  testConnection: (id: string) =>
    call<{ ok: boolean; elapsed_s: number; reply_preview?: string; error?: string }>(
      `/api/connections/${id}/test`,
      'POST',
      {},
    ),
  applyConnection: (id: string) =>
    call<{ updated: { name: string; version: number }[] }>(`/api/connections/${id}/apply-to-all`, 'POST', {}),

  checkPython: (code: string) =>
    call<{ enabled: boolean; ok: boolean; problems: string[]; input_schema: unknown; description: string }>(
      '/api/tools/python/check',
      'POST',
      { code },
    ),
  runPython: (code: string, kwargs: Record<string, unknown>, timeout_s: number) =>
    call<Record<string, unknown>>('/api/tools/python/run', 'POST', { code, kwargs, timeout_s }),

  auditRuns: () => call<{ runs: AuditRun[] }>('/api/audit/runs?limit=50'),
  auditRows: (requestId: string) =>
    call<{ rows: AuditRow[] }>(`/api/audit/rows?request_id=${encodeURIComponent(requestId)}`),
  auditRecent: () => call<{ rows: AuditRow[] }>('/api/audit/rows?limit=200'),
  auditStats: () => call<AuditStats>('/api/audit/stats'),
  appendOnlyCheck: () =>
    call<Record<'update' | 'delete', { blocked: boolean; error: string | null }>>(
      '/api/audit/append-only-check',
    ),
};

// The run endpoints answer a POST with server-sent events, which EventSource cannot do.
export async function streamRun(
  path: '/api/runs/stream' | '/api/runs/resume',
  body: unknown,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  await leaveIfShutOut(response);
  if (!response.ok || !response.body) throw new ApiError(response.status, await problemsOf(response));

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const event = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.+)$/m.exec(block)?.[1];
      if (event && data) onEvent({ event, data: JSON.parse(data) } as RunEvent);
    }
  }
}

export function messageOf(error: unknown): string {
  if (error instanceof Redirecting) return 'กำลังพาไปหน้าเข้าสู่ระบบ…';
  if (error instanceof ApiError) return error.problems.join('\n');
  return error instanceof Error ? error.message : String(error);
}
