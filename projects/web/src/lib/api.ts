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

/**
 * Pull the documented error code and message out of a failure body.
 *
 * These projects return their catalogued errors at the top level —
 * `{code, message, detail}` — where `detail` is an extra payload, NOT the
 * FastAPI wrapper. Descending into `detail` first therefore threw away the real
 * code and produced "Request failed (422)". Top level wins; the FastAPI shapes
 * (`detail` as a string, or as a nested error object) are the fallbacks.
 */
function messageFrom(status: number, body: unknown): { code: string; message: string } {
  const fallback = { code: `http_${status}`, message: `Request failed (${status})` };
  if (!body || typeof body !== "object") return fallback;
  const b = body as Record<string, unknown>;

  const pick = (o: Record<string, unknown>): { code: string; message: string } | null => {
    // `error` may itself be the envelope — dresscast returns {error: {code, message}}.
    if (o.error && typeof o.error === "object") {
      const inner = pick(o.error as Record<string, unknown>);
      if (inner) return inner;
    }
    const rawCode = o.code ?? o.error;
    const code = typeof rawCode === "string" || typeof rawCode === "number" ? String(rawCode) : undefined;
    const rawMessage = o.message ?? (typeof o.detail === "string" ? o.detail : undefined) ?? code;
    const message = typeof rawMessage === "string" ? rawMessage : undefined;
    return code || message
      ? { code: code ?? fallback.code, message: message ?? fallback.message }
      : null;
  };

  const top = pick(b);
  if (top) return top;

  if (typeof b.detail === "string") return { code: fallback.code, message: b.detail };
  if (b.detail && typeof b.detail === "object") {
    const nested = pick(b.detail as Record<string, unknown>);
    if (nested) return nested;
  }
  // FastAPI validation errors: detail is an array of {loc, msg, type}.
  if (Array.isArray(b.detail) && b.detail.length) {
    const first = b.detail[0] as { msg?: string; loc?: unknown[] };
    if (first?.msg) {
      const where = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "";
      return { code: "validation_error", message: where ? `${where}: ${first.msg}` : first.msg };
    }
  }
  return fallback;
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
