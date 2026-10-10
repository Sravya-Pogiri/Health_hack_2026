// Thin API client. All times shown in the UI come from the engine clock, never the browser clock.
export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData } = {}): Promise<T> {
  const init: RequestInit = { method: opts.method || (opts.body || opts.form ? "POST" : "GET") };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data as T;
}

export const post = (path: string, body: any = {}) => api(path, { method: "POST", body });
