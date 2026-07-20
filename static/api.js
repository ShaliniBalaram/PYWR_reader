/* The one place that talks to the Flask API. Throws Error(message) on a
   non-2xx or {ok:false} response, so callers can show err.message. */

export async function api(path, body, method) {
  const opts = { method: method || (body ? "POST" : "GET") };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `${res.status} ${res.statusText}`);
  }
  return data;
}
