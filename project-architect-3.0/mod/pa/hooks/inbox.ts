// The router's inbox `.run/mod/<sid>/inbox/` (design.md, "The plugins talk through files"): the watcher writes one file
// per tool request, `<ms>-<seq>-<kind>.json` holding `{id, kind, caller, input, at}`; the router's one-second tick
// drains it. Pure: naming, parsing, which files are owed, and the handler table. A handler returns effects (data) that
// the drain performs inline, since `$` cannot be passed to a helper.

import type { ToastOut } from '../types'

import { presentOf } from './planview'
import { DONE_TTL_MS, type Present } from './state'

export type Request = { id: string; kind: string; caller: string; input: unknown; at: number }

export type RequestName = { id: string; ms: number; seq: number; kind: string }

/** What a handler asks the drain to do: the debug log; T8a: the topic flag on the calling partner, a waiting toast;
 * T8c: a dialog queued for the presentation tick (planview.ts); 3.23 T5b: a loop notice queued as a framed `other`
 * message for `to` (a request's caller), else the partner; T13b: a loop stop's toast cause owed to Claude's next reply
 * (`stop`; null clears it); 3.24 T13E: a card the loop drew (`card`: registered, journaled, drawn at zero tokens). */
export type Effect =
  | { do: 'log'; kind: string; data: Record<string, unknown> }
  | { do: 'topic'; agentId: string }
  | { do: 'toast'; cause: ToastOut['cause']; body: string }
  | { do: 'present'; item: Present }
  | { do: 'notice'; text: string; to: string }
  | { do: 'stop'; cause: ToastOut['cause'] | null }
  | { do: 'card'; text: string }

/**
 * Kinds the loop's tick drains itself (autopilot.tsx, marked done in loop.json); the router's drain skips them. T5d2a:
 * `forward`, written by the router when it queues a forward of a child's hand-back to the loop's expert. T6a1: the
 * router's `install` (an approved generation or phase plan) and `answer` (the answer to the loop's own `ask`).
 */
export const LOOP_KINDS: ReadonlySet<string> = new Set(['release', 'plan_edit', 'steer', 'forward', 'install', 'answer', 'cleanup'])

/** A file name's kind is one the loop drains. */
export const isLoopKind = (name: string): boolean => LOOP_KINDS.has(parseRequestName(name)?.kind ?? '')

export type Handler = (req: Request) => Effect[]

const KIND = /^[a-z][a-z0-9_]*$/

/** The file name of a request; its id is the name without `.json`. */
export function requestName(ms: number, seq: number, kind: string): string {
  if (!KIND.test(kind)) {
    throw new Error(`bad request kind: ${kind}`)
  }
  return `${String(ms)}-${String(seq)}-${kind}.json`
}

export function parseRequestName(name: string): RequestName | null {
  const m = /^(\d+)-(\d+)-([a-z][a-z0-9_]*)\.json$/.exec(name)
  if (m === null) {
    return null
  }
  return { id: name.slice(0, -'.json'.length), ms: Number(m[1]), seq: Number(m[2]), kind: m[3] ?? '' }
}

/** The request a file holds, or null when it does not parse or its id or kind disagrees with its name. */
export function parseRequest(text: string, name: string): Request | null {
  const n = parseRequestName(name)
  if (n === null) {
    return null
  }
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) {
    return null
  }
  const o = doc as Record<string, unknown>
  if (o['id'] !== n.id || o['kind'] !== n.kind) {
    return null
  }
  return {
    id: n.id,
    kind: n.kind,
    caller: typeof o['caller'] === 'string' ? o['caller'] : '',
    input: o['input'] ?? null,
    at: typeof o['at'] === 'number' ? o['at'] : n.ms,
  }
}

/** The request files owed: well named, not done, inside the horizon; oldest first (ms, then seq). */
export function pendingRequests(names: readonly string[], done: Readonly<Record<string, number>>, now: number,
  ttl: number = DONE_TTL_MS): string[] {
  const owed: RequestName[] = []
  for (const name of names) {
    const n = parseRequestName(name)
    if (n !== null && done[n.id] === undefined && now - n.ms <= ttl) {
      owed.push(n)
    }
  }
  owed.sort((a, b) => a.ms - b.ms || a.seq - b.seq || (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0))
  return owed.map(n => `${n.id}.json`)
}

const noop: Handler = req => [{ do: 'log', kind: 'inbox.drain', data: { id: req.id, kind: req.kind } }]

const inputText = (input: unknown, key: string): string => {
  const v = typeof input === 'object' && input !== null ? (input as Record<string, unknown>)[key] : undefined
  return typeof v === 'string' ? v : ''
}

/** A `plan` or `ask` queued for the presentation tick; an input it cannot read is logged and dropped. */
const presentFx = (req: Request): Effect[] => {
  const item = presentOf(req)
  return item === null
    ? [{ do: 'log', kind: 'present.bad', data: { id: req.id, kind: req.kind } }]
    : [{ do: 'present', item }]
}

const CAUSES: readonly ToastOut['cause'][] = ['review', 'question', 'input']

/** A loop notice (`caller: "loop"`, input `{text, toast?, to?}`): the text to `to` or the partner; a named toast is a
 * stop's, owed to Claude's next reply (T13b: one toast per stop, none at the notice). */
const noticeFx = (req: Request): Effect[] => {
  const text = inputText(req.input, 'text')
  if (text.trim() === '') {
    return [{ do: 'log', kind: 'notice.bad', data: { id: req.id } }]
  }
  const cause = CAUSES.find(c => c === inputText(req.input, 'toast'))
  const fx: Effect[] = [{ do: 'notice', text, to: inputText(req.input, 'to') }]
  return cause === undefined ? fx : [...fx, { do: 'stop', cause }]
}

/** kind -> handler; any other kind goes to `noop`. `ask` raises the toast and queues the picker (Claude's ask takes a
 * pending stop's place, T13b); `plan` queues path 0. */
export const HANDLERS: Readonly<Record<string, Handler>> = {
  topic: req => [...noop(req), { do: 'topic', agentId: req.caller }],
  ask: req => [...noop(req), { do: 'toast', cause: 'input', body: inputText(req.input, 'question') },
    ...(req.caller === 'loop' ? [] : [{ do: 'stop', cause: null } as const]), ...presentFx(req)],
  plan: req => [...noop(req), ...presentFx(req)],
  notice: req => [...noop(req), ...noticeFx(req)],
  // 3.23 T13d.c4: a toast raised at once, no Claude turn (the generation end's closed line; input `{cause, body}`).
  toast: req => [...noop(req), { do: 'toast', cause: CAUSES.find(c => c === inputText(req.input, 'cause')) ?? 'input', body: inputText(req.input, 'body') }],
  // 3.24 T13E: a card of the loop's (input `{text}`, a cardText; the review release's "✓ Expert · T<n> · accepted").
  card: req => [...noop(req), ...(inputText(req.input, 'text').trim() === ''
    ? [{ do: 'log', kind: 'card.bad', data: { id: req.id } } as const]
    : [{ do: 'card', text: inputText(req.input, 'text') } as const])],
}

/** A loop notice request: its file name and body, in the format `parseRequest` reads. */
export function noticeRequest(ms: number, seq: number, text: string, toast: ToastOut['cause'] | '' = '', to = ''):
  { name: string; body: string } {
  const name = requestName(ms, seq, 'notice')
  const input: Record<string, string> = { text }
  if (toast !== '') {
    input['toast'] = toast
  }
  if (to !== '') {
    input['to'] = to
  }
  return { name, body: JSON.stringify({ id: name.slice(0, -'.json'.length), kind: 'notice', caller: 'loop', input, at: ms }, null, 1) }
}

/** The router's `forward` request (3.23 T5d2a): a forward of `child`'s hand-back to the loop expert `agentId` is queued. */
export function forwardRequest(ms: number, seq: number, agentId: string, child: string): { name: string; body: string } {
  const name = requestName(ms, seq, 'forward')
  return { name, body: JSON.stringify({ id: name.slice(0, -'.json'.length), kind: 'forward', caller: 'router', input: { agentId, child }, at: ms }, null, 1) }
}

/** Any request file (3.23 T6a1: the router's `install` and `answer`, the loop's `ask` with caller `loop`). */
export function requestFile(ms: number, seq: number, kind: string, caller: string, input: unknown): { name: string; body: string } {
  const name = requestName(ms, seq, kind)
  return { name, body: JSON.stringify({ id: name.slice(0, -'.json'.length), kind, caller, input, at: ms }, null, 1) }
}

export function handle(req: Request): Effect[] {
  return (HANDLERS[req.kind] ?? noop)(req)
}
