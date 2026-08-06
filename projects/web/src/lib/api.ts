/**
 * The one place that talks to the gateway.
 *
 * Every project is mounted at /api/<slug>, so a client is just that prefix plus
 * typed helpers. Errors carry the structured detail the APIs return (each project
 * defines an error catalog in its SCOPE.md) rather than collapsing to a status
 * code — screens can then say what actually went wrong.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** True when retrying could plausibly succeed — the UI offers a retry only then. */
  get retryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

function messageFrom(status: number, body: unknown): { code: string; message: string } {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    // FastAPI puts our structured errors under `detail`.
    const d = (b.detail ?? b) as Record<string, unknown> | string;
    if (typeof d === "string") return { code: `http_${status}`, message: d };
    if (d && typeof d === "object") {
      const code = (d.error ?? d.code ?? `http_${status}`) as string;
      const message = (d.message ?? d.detail ?? d.error ?? `Request failed (${status})`) as string;
      return { code: String(code), message: String(message) };
    }
  }
  return { code: `http_${status}`, message: `Request failed (${status})` };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init?.headers,
      },
    });
  } catch (cause) {
    throw new ApiError(0, "network_error", "Could not reach the server. Is it running?", cause);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body = text ? safeJson(text) : undefined;

  if (!response.ok) {
    const { code, message } = messageFrom(response.status, body);
    throw new ApiError(response.status, code, message, body);
  }
  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function query(params?: Record<string, unknown>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, String(v)));
    else search.append(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

/** A typed client scoped to one project's mount point. */
export function client(slug: string) {
  const base = `/api/${slug}`;
  return {
    get: <T>(path: string, params?: Record<string, unknown>) =>
      request<T>(`${base}${path}${query(params)}`),
    post: <T>(path: string, body?: unknown) =>
      request<T>(`${base}${path}`, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    put: <T>(path: string, body?: unknown) =>
      request<T>(`${base}${path}`, {
        method: "PUT",
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    del: <T>(path: string) => request<T>(`${base}${path}`, { method: "DELETE" }),
    upload: <T>(path: string, form: FormData) =>
      request<T>(`${base}${path}`, { method: "POST", body: form }),
  };
}

export type ProjectInfo = {
  slug: string;
  name: string;
  tagline: string;
  routes: number;
};

export type ProjectsResponse = {
  projects: ProjectInfo[];
  unavailable: (Omit<ProjectInfo, "routes"> & { error: string })[];
};

export const gateway = {
  projects: () => request<ProjectsResponse>("/api/projects"),
  health: () => request<{ ok: boolean; mounted: number; unavailable: number }>("/api/health"),
};
