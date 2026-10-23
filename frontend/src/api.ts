import { useEffect, useRef, useState } from 'preact/hooks';

export type Data = Record<string, any>;
let csrf = '';
export function setSession(user: Data) { csrf = user.csrf || ''; }
export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}
export async function api<T = Data>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api/v1' + path, { method, credentials: 'same-origin', signal,
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(method !== 'GET' ? { 'X-CSRF-Token': csrf } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'));
    const fields = value.error?.fields?.map((f: Data) => `${f.field}: ${f.message}`).join('; ');
    throw new ApiError(value.error?.code || 'request_failed', fields || value.error?.message || value.detail || 'The request failed. Try again.', response.status);
  }
  return value as T;
}
let refreshTimer: number | undefined;
export function refresh() { clearTimeout(refreshTimer); refreshTimer = window.setTimeout(() => window.dispatchEvent(new Event('data-refresh')), 80); }
export function toast(message: string, error = false) { window.dispatchEvent(new CustomEvent('toast', { detail: { message, error } })); }
export function useResource<T = Data>(path: string | null) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [revision, bump] = useState(0);
  const ref = useRef(path); ref.current = path;
  useEffect(() => { const update = () => bump(v => v + 1); window.addEventListener('data-refresh', update); return () => window.removeEventListener('data-refresh', update); }, []);
  useEffect(() => {
    if (!path) { setLoading(false); return; }
    const controller = new AbortController(); setError(''); setLoading(true);
    api<T>(path, undefined, 'GET', controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, reload: () => bump(v => v + 1) };
}
export function useDebounced(value: string, delay = 250) {
  const [result, set] = useState(value); useEffect(() => { const id = setTimeout(() => set(value), delay); return () => clearTimeout(id); }, [value, delay]); return result;
}
export function uid() { return crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join(''); }
export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([typeof data === 'string' ? data : JSON.stringify(data, null, 2)], { type: name.endsWith('.csv') ? 'text/csv' : 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function bytes(value?: number) { if (value == null) return 'Unavailable'; return value >= 1024 ** 3 ? (value / 1024 ** 3).toFixed(2) + ' GB' : (value / 1024 ** 2).toFixed(1) + ' MB'; }
export function duration(value?: number | null) { return value == null ? '—' : value >= 1000 ? (value / 1000).toFixed(2) + ' s' : Math.round(value) + ' ms'; }
export function when(value?: number) { return value ? new Date(value * 1000).toLocaleString() : 'Never'; }
export function message(error: unknown) { return error instanceof Error ? error.message : 'The operation failed.'; }
