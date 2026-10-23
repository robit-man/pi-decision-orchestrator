import { ComponentChildren, ComponentProps } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { AlertCircle, ArrowLeft, ArrowRight, Check, ChevronDown, Eye, EyeOff, LoaderCircle, X } from 'lucide-preact';
import { message, refresh, toast } from './api';

export function Button({ children, variant = 'secondary', busy, ...props }: ComponentProps<'button'> & { variant?: 'primary' | 'secondary' | 'danger' | 'quiet'; busy?: boolean }) {
  return <button {...props} type={props.type || 'button'} class={`button ${variant} ${props.class || ''}`} disabled={props.disabled || busy} aria-busy={busy || undefined}>{busy && <LoaderCircle size={16} class="spinner" />}{children}</button>;
}
export function Field({ label, hint, error, children, wide = false }: { label: string; hint?: string; error?: string; children: ComponentChildren; wide?: boolean }) {
  return <label class={`field ${wide ? 'wide' : ''}`}><span class="field-label">{label}</span>{children}{hint && <span class="hint">{hint}</span>}{error && <span class="field-error">{error}</span>}</label>;
}
export function Select(props: ComponentProps<'select'>) { return <span class="select-wrap"><select {...props} /><ChevronDown size={15} aria-hidden="true" /></span>; }
export function CheckBox({ label, checked, onChange, hint, disabled }: { label: string; checked: boolean; onChange: (value: boolean) => void; hint?: string; disabled?: boolean }) { return <label class="check-field"><input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.currentTarget.checked)} /><span>{label}{hint && <small>{hint}</small>}</span></label>; }
export function Secret({ value, onChange, label = 'Password', autoComplete = 'new-password', required = false }: { value: string; onChange: (s: string) => void; label?: string; autoComplete?: string; required?: boolean }) {
  const [show, set] = useState(false); const props = { value, autoComplete, required, onInput: (e: Event & { currentTarget: HTMLInputElement }) => onChange(e.currentTarget.value) }; return <Field label={label}><span class="secret">{show ? <input type="text" {...props} /> : <input type="password" {...props} />}<button type="button" class="icon-button" aria-label={`${show ? 'Hide' : 'Show'} ${label.toLowerCase()}`} onClick={() => set(!show)}>{show ? <EyeOff size={17} /> : <Eye size={17} />}</button></span></Field>;
}
export function Status({ value }: { value: string }) { return <span class={`status status-${value.replace(/[^a-z_]/g, '')}`}><span class="status-dot" />{value.replaceAll('_', ' ')}</span>; }
export function Empty({ title, children, action }: { title: string; children?: ComponentChildren; action?: ComponentChildren }) { return <div class="empty"><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>; }
export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) { return <div role="alert" class="error-box"><AlertCircle size={18} /><div><strong>Couldn’t complete this request</strong><p>{error}</p>{retry && <Button onClick={retry}>Try again</Button>}</div></div>; }
export function Loading({ label = 'Loading…' }: { label?: string }) { return <div class="loading" role="status"><LoaderCircle size={18} class="spinner" />{label}</div>; }
export function Panel({ title, children, actions, className = '' }: { title?: string; children: ComponentChildren; actions?: ComponentChildren; className?: string }) { return <section class={`panel ${className}`}>{title && <div class="panel-heading"><h2>{title}</h2>{actions}</div>}{children}</section>; }
export function PageHead({ title, description, children }: { title: string; description: string; children?: ComponentChildren }) { return <div class="page-head"><div><h1>{title}</h1><p>{description}</p></div>{children && <div class="toolbar">{children}</div>}</div>; }
export function Tabs({ items, active, onChange }: { items: string[]; active: string; onChange: (v: string) => void }) { return <div class="tabs" role="tablist">{items.map(item => <button role="tab" aria-selected={item === active} key={item} onClick={() => onChange(item)}>{item}</button>)}</div>; }
export function JsonView({ value, title = 'Structured details', open = false }: { value: unknown; title?: string; open?: boolean }) { return <details open={open} class="json-details"><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>; }
export function Pagination({ total, offset, limit, setOffset }: { total: number; offset: number; limit: number; setOffset: (v: number) => void }) { return <div class="pagination"><span>{total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : '0 results'}</span><div class="toolbar"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><ArrowLeft size={16} />Previous</Button><Button disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next<ArrowRight size={16} /></Button></div></div>; }
export function Modal({ title, children, close }: { title: string; children: ComponentChildren; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement; dialog.current?.showModal(); return () => previous?.focus(); }, []);
  return <dialog ref={dialog} class="modal" onCancel={close} aria-label={title}><div class="modal-title"><h2>{title}</h2><button class="icon-button" onClick={close} aria-label="Close dialog"><X size={20} /></button></div>{children}</dialog>;
}
export function Confirm({ title, description, confirmLabel = 'Confirm', danger = false, confirm, close }: { title: string; description: string; confirmLabel?: string; danger?: boolean; confirm: () => Promise<unknown>; close: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <Modal title={title} close={close}><p>{description}</p>{error && <ErrorBox error={error} />}<div class="modal-actions"><Button onClick={close} disabled={busy}>Cancel</Button><Button variant={danger ? 'danger' : 'primary'} busy={busy} onClick={async () => { setBusy(true); try { await confirm(); refresh(); close(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }}>{confirmLabel}</Button></div></Modal>;
}
export function useAction() {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function run<T>(fn: () => Promise<T>, success?: string): Promise<T | undefined> { setBusy(true); setError(''); try { const value = await fn(); if (success) toast(success); refresh(); return value; } catch (e) { setError(message(e)); return undefined; } finally { setBusy(false); } }
  return { busy, error, run, clear: () => setError('') };
}
export function Toasts() {
  const [item, set] = useState<{ message: string; error: boolean } | null>(null);
  useEffect(() => { let timer: number; const handler = (e: Event) => { set((e as CustomEvent).detail); clearTimeout(timer); timer = window.setTimeout(() => set(null), 6500); }; window.addEventListener('toast', handler); return () => { clearTimeout(timer); window.removeEventListener('toast', handler); }; }, []);
  return <div class="toast-region" aria-live="polite" aria-atomic="true">{item && <div class={`toast ${item.error ? 'toast-error' : ''}`}>{item.error ? <AlertCircle size={18} /> : <Check size={18} />}<span>{item.message}</span><button class="icon-button" aria-label="Dismiss notification" onClick={() => set(null)}><X size={16} /></button></div>}</div>;
}
