// The router's conversation route (design.md, "The conversation"; phase 3.22 T5b): pure helpers. Every main-loop turn is
// answered locally (a marker's reply, the attachment notice, or the stub); every prompt is accepted and sorted by
// origin; the developer's messages, framed other prompts and children's hand-backs queue in the state's outbox, which
// the route tick sends; the partner's replies come back as marker prompts. register.tsx spells the `$` calls.
// 3.24 T13B: every reply draws as an orange Claude card (card.ts claudeCard); a reply with no hand-back goes as a feed
// record while the feed lives (feed.ts replyRecord: its row is hidden), the marker prompt only when it does not.
// T8d: the partner ends each turn with SubagentHandback; its hand-back's main turn shows the report, and the marker is
// the fallback when no hand-back for that answer arrives within HANDBACK_WAIT_MS of its turn.complete.
// T8a: the conversation is filed in the journal (journal.ts), a fresh partner is seeded from it (seed.ts) when the
// policy says so (policy.ts), and an answer that asks raises the waiting toast.
import { type Entry, entryPath, type Meta, parseEntry, parseEntryPath, renderEntry, stamp } from './journal'
import { CTX_MAX, decideRoute, ratesFrom } from './policy'
import type { LoopState } from '../types'
import type {
  Child, JournalOut, OutMsg, Partner, PriceCache, ReplyMark, RouterState, ToastOut, TurnKind, TurnRegistry, YourTurn,
} from './state'

/** The stub answer of every main-loop turn that carries no reply; its row draws nothing. */
export const STUB = '‹sent›'
export const ATTACH_NOTICE = '(Claude sees text only; your attachment stays here.)' // was 'The partner' (3.22 T8f)
/** A send to a recorded partner not seen running within this long seeds a fresh one (after `--resume`: P14). */
export const RESUME_CHECK_MS = 30_000
/** Registered turns kept at most (an agent's turn.start registers too and is cleared at its turn.complete). */
const MAX_TURNS = 64
/** A partner answer's hand-back may follow its turn.complete this long before the marker fallback is queued (T8d). */
export const HANDBACK_WAIT_MS = 5_000
/** A shown reply's hand-back repeated within this long (a second delivery of it) gets the stub. */
const DUP_MS = 60_000
/** Reply marks kept at most, newest by `at`. */
const MAX_REPLIES = 32

export type Route = 'developer' | 'own' | 'note' | 'handback' | 'feed' | 'other'

type Origin = { kind: string; name?: string }

const HANDBACK = new Set(['task-notification', 'peer', 'peer-send-message'])

/** The feed monitor's notification (3.23 T13d): an event (`Monitor event: "pa feed"`) or its end notice (`Monitor "pa
 * feed" …`). The monitor's description is the router's own marker; turn.start, which has no origin, matches the text. */
export const isFeedText = (text: string): boolean =>
  /^\s*<task-notification>/.test(text) && /<summary>Monitor (?:event: )?"pa feed"/.test(text)

/** The child agent a hand-back names: `<task-id>…</task-id>` or `<agent-message from="…">`; null when none. */
export function handbackChild(text: string): string | null {
  const id = /<task-id>([^<]+)<\/task-id>/.exec(text)?.[1] ?? /<agent-message from="([^"]+)">/.exec(text)?.[1]
  return id === undefined || id.trim() === '' ? null : id.trim()
}

/**
 * 3.24 T13r: the agent's own text inside a `<task-notification>` block. Claude Code escapes `&`, `<` and `>` there
 * (`<event>`, `<result>`; quotes stay as written; an `<agent-message>` body is not escaped); undone once, `&amp;` last,
 * so a literal `&amp;` an agent wrote comes back as written.
 */
export const xmlText = (s: string): string => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')

/**
 * The report a hand-back carries: an `<agent-message>`'s body after its "The report follows:" line, each line
 * de-indented by the harness's two spaces, up to the closing tag at column zero (an inner lookalike is indented); a
 * `<task-notification>`'s `<result>` content, unescaped (xmlText). End whitespace trimmed; null when neither shape matches.
 */
export function handbackReport(text: string): string | null {
  const am = /<agent-message from="[^"]*">/.exec(text)
  if (am !== null) {
    const head = text.indexOf('The report follows:', am.index)
    if (head < 0) {
      return null
    }
    const nl = text.indexOf('\n', head)
    let body = nl < 0 ? '' : text.slice(nl + 1)
    const close = /(?:^|\n)<\/agent-message>/.exec(body)
    body = close === null ? body : body.slice(0, close.index)
    return body.split('\n').map(l => (l.startsWith('  ') ? l.slice(2) : l)).join('\n').replace(/\s+$/, '')
  }
  const tn = /<task-notification>[\s\S]*?<result>([\s\S]*)<\/result>/.exec(text)
  return tn === null ? null : xmlText(tn[1] ?? '').replace(/\s+$/, '')
}

/** A note (3.23 T13): an agent's SendMessage to the main session, `<agent-message from=…>` without "The report follows:"
 * (a hand-back carries it). Never filed, forwarded or read as a contract. */
export function isNote(text: string): boolean {
  const am = /<agent-message from="[^"]+">/.exec(text)
  return am !== null && !text.includes('The report follows:', am.index)
}

/** A note's body: the lines after its `<agent-message from=…>` line, up to the closing tag at column zero; '' when none. */
export function noteBody(text: string): string {
  const am = /<agent-message from="[^"]*">[^\n]*\n?/.exec(text)
  if (am === null) {
    return ''
  }
  const body = text.slice(am.index + am[0].length)
  const close = /(?:^|\n)<\/agent-message>/.exec(body)
  return (close === null ? body : body.slice(0, close.index)).trim()
}

/** A task-notification result that only points elsewhere: the report went as a hand-back, or none was delivered. */
const POINTER = /^(This agent's report was delivered to you as a message|The subagent ended without delivering a report)/

/** 3.24 T17g: whether a hand-back report only points elsewhere (POINTER). */
export const isPointer = (report: string): boolean => POINTER.test(report.trim())

/**
 * 3.24 T17g: an agent's final report given as text after its SubagentHandback was spent (round 4: T1's expert handed
 * back a waiting line, its done report's SubagentHandback was refused, so the report came only as its last text and the
 * notification only pointed). From `$.session.messages({ agentId })`: the last message, when it is the assistant's with
 * text and no tool call; else null.
 */
export function lateReport(found: unknown): string | null {
  if (!Array.isArray(found) || found.length === 0) {
    return null
  }
  const m = found[found.length - 1] as { role?: unknown; text?: unknown; toolUses?: unknown } | null
  const uses = Array.isArray(m?.toolUses) ? m.toolUses.length : 0
  return m?.role === 'assistant' && typeof m.text === 'string' && m.text.trim() !== '' && uses === 0 ? m.text.trim() : null
}

/**
 * 3.24 T17h: a SubagentHandback carrying no report (`.` or blank), refused unless its agent answers a warming ping
 * (`pinged`: the partner's or the loop expert's `.`). The hand-back is an agent's one report: round 5's thinker, waiting
 * on its retrievers, spent it on `.`, so its review never reached Claude and its second hand-back was refused.
 */
export function noReportHandback(input: unknown, pinged: boolean): boolean {
  const m = (input as { message?: unknown } | null)?.message
  const text = typeof m === 'string' ? m.trim() : ''
  return !pinged && (text === '' || text === '.')
}

/** 3.24 T17h: what the agent reads when noReportHandback refuses its call. */
export const NO_REPORT_REASON = 'Nothing was sent: a hand-back is your one final report, and "." is not a report. To wait '
  + 'for agents you started, end your turn with no tool call; their reports wake you. Hand back once, with your full answer.'

/** A partner hand-back's report, null when it carries none (no shape, or a notification that only points). */
export function partnerReport(text: string): string | null {
  const r = handbackReport(text)
  return r === null || POINTER.test(r.trim()) ? null : r
}

/** A reply's key: the agent and an FNV-1a hash (and length) of its text, whitespace collapsed (answer = report). */
export function replyKey(agentId: string, text: string): string {
  const t = text.replace(/\s+/g, ' ').trim()
  let h = 0x811c9dc5
  for (let i = 0; i < t.length; i += 1) {
    h = Math.imul(h ^ t.charCodeAt(i), 0x01000193) >>> 0
  }
  return `${agentId}:${h.toString(16)}:${String(t.length)}`
}

/** Where a prompt goes: the developer's (to the partner), the router's own marker, a hand-back, the feed, or anything else. */
export function classify(origin: Origin, text: string, sdkAsUser: boolean): Route {
  // The router's own marker, whatever origin it arrives under (it is submitted `asUser` since T11's recheck).
  if (/^\s*‹pa reply \d+›\s*$/.test(text)) {
    return 'own'
  }
  const kind = origin.kind
  // 3.23 T13d: the feed before any hand-back: never forwarded, filed, carded or sent to the partner.
  if (kind === 'task-notification' && isFeedText(text)) {
    return 'feed'
  }
  if (kind === 'composer' || kind === 'bridge' || (kind === 'sdk' && sdkAsUser)) {
    return 'developer'
  }
  if (kind === 'plugin' && origin.name === 'pa') {
    return 'own'
  }
  if (HANDBACK.has(kind) && handbackChild(text) !== null) {
    return isNote(text) ? 'note' : 'handback'
  }
  return 'other'
}

export function markerText(n: number): string {
  return `‹pa reply ${String(n)}›`
}

// 3.23 T13d.c11: the `reply_fallback` option (`fallbackOf`, `noticeText`, `noticeReply`) retired: its `notice` mode
// appended a `system` row the engine never draws (T13 probe), so the reply was invisible live. The marker is the only path.

export function parseMarker(text: string): number | null {
  const m = /‹pa reply (\d+)›/.exec(text)
  return m === null ? null : Number(m[1])
}

/** The kind of a turn starting with `text`: a marker, an accepted text that carried attachments, or the stub. */
export function turnKindOf(text: string, attach: readonly string[]): TurnKind {
  const n = parseMarker(text)
  if (n !== null) {
    return { kind: 'marker', n }
  }
  if (attach.some(a => a === text || (a !== '' && text.includes(a)))) {
    return { kind: 'attach' }
  }
  return { kind: 'stub' }
}

export const emptyTurns = (): TurnRegistry => ({ kinds: {}, attach: [], replies: [] })

/** The registry with the turn registered (oldest dropped past MAX_TURNS) and a matched attachment text or partner
 * hand-back consumed: a hand-back matches by its reply key, else by its prompt's text. */
export function registerTurn(reg: TurnRegistry | undefined, turnId: string, text: string): TurnRegistry {
  const r = reg ?? emptyTurns()
  let replies = r.replies ?? []
  const child = handbackChild(text)
  const report = child === null ? null : handbackReport(text)
  const key = child === null || report === null ? '' : replyKey(child, report)
  const ri = replies.findIndex(p => p.key === key || p.prompt === text || (p.prompt !== '' && text.includes(p.prompt)))
  const hit = replies[ri]
  let kind = turnKindOf(text, r.attach)
  if (hit !== undefined) {
    kind = { kind: 'reply', text: hit.text }
    replies = [...replies.slice(0, ri), ...replies.slice(ri + 1)]
  }
  let attach = r.attach
  if (kind.kind === 'attach') {
    const i = attach.findIndex(a => a === text || (a !== '' && text.includes(a)))
    attach = [...attach.slice(0, i), ...attach.slice(i + 1)]
  }
  const ids = Object.keys(r.kinds).filter(id => id !== turnId)
  const kinds: Record<string, TurnKind> = {}
  for (const id of ids.slice(Math.max(0, ids.length - (MAX_TURNS - 1)))) {
    kinds[id] = r.kinds[id] as TurnKind
  }
  kinds[turnId] = kind
  return { kinds, attach, replies }
}

export function clearTurn(reg: TurnRegistry | undefined, turnId: string): TurnRegistry {
  const r = reg ?? emptyTurns()
  const kinds = { ...r.kinds }
  delete kinds[turnId]
  return { ...r, kinds }
}

export function addAttach(reg: TurnRegistry | undefined, text: string): TurnRegistry {
  const r = reg ?? emptyTurns()
  return { ...r, attach: [...r.attach, text].slice(-8) }
}

/** A partner hand-back whose turn shows `text` (its prompt registers as a `reply` turn). */
export function addReply(reg: TurnRegistry | undefined, key: string, prompt: string, text: string): TurnRegistry {
  const r = reg ?? emptyTurns()
  return { ...r, replies: [...(r.replies ?? []), { key, prompt, text }].slice(-8) }
}

/** A local turn's answer text; a made-up answer must carry text (an empty one did not end the turn). */
export function localAnswer(kind: TurnKind, reply: string | null): string {
  if (kind.kind === 'marker') {
    return reply === null || reply.trim() === '' ? STUB : reply
  }
  if (kind.kind === 'reply' || kind.kind === 'card') {
    return kind.text.trim() === '' ? STUB : kind.text
  }
  return kind.kind === 'attach' ? ATTACH_NOTICE : STUB
}

/** Statuses of an agent that is mid-turn or held: never rotated away, never pinged. */
export const isBusy = (status: string | undefined): boolean => status === 'running' || status === 'pending' || status === 'waiting'

/**
 * Resume the recorded partner or seed a fresh one: `policy.decideRoute` (TTL from its last turn or its spawn, ctx,
 * topic, model switch; never with a child out). A busy partner is resumed (it rotates at the next quiet point), and
 * `rotateAt` > 0 (`PA_MOD_ROTATE_AT`, trials) counts a ctx at or past it as over the limit.
 */
export function partnerRoute(p: Partner | null, now: number, opts: { childOut: boolean; busy: boolean; rotateAt: number }): 'resume' | 'fresh' {
  if (p === null) {
    return 'fresh'
  }
  if (opts.busy) {
    return 'resume'
  }
  const ctx = opts.rotateAt > 0 && p.ctx >= opts.rotateAt ? Math.max(p.ctx, CTX_MAX) : p.ctx
  const facts = { lastTurnAt: Math.max(p.lastTurnAt, p.spawnedAt), ctx, topicFlag: p.topic, modelSwitchAt: p.modelSwitchAt }
  return decideRoute(facts, now, { childOut: opts.childOut })
}

const ENDED = ['completed', 'failed', 'killed', 'done']

/** A child is out: the list shows one of the partner's in `pending|running|waiting`, or the router has one unfinished. */
export function childOut(partnerId: string, children: Record<string, Child>,
  list: readonly { id: string; status: string; parentId?: string }[]): boolean {
  if (partnerId === '') {
    return false
  }
  if (list.some(a => a.parentId === partnerId && isBusy(a.status))) {
    return true
  }
  return Object.values(children).some(c => c.parent === partnerId && !ENDED.includes(c.status))
}

export function newPartner(agentId: string, model: string, now: number): Partner {
  return { agentId, spawnedAt: now, lastTurnAt: 0, model, ctx: 0, topic: false, modelSwitchAt: 0, pingInFlight: false, firstCtx: 0 }
}

/** The partner's agent type (3.22 T8g; `claude` at T8f, `partner` before): never the built-in `claude` catch-all. */
export const PARTNER_TYPE = 'pa-claude'

/** The newest live `pa-claude` agent other than `old`: the one a spawn started when its result named no agentId. */
export function newestPartner(list: readonly { id: string; type: string; status: string }[], old: string): string | undefined {
  return list.filter(a => a.type === PARTNER_TYPE && a.id !== old && isLive(a.status)).at(-1)?.id
}

/** The watcher's last-step facts of an agent (`.run/mod/<sid>/agents/<agentId>.json`, 3.22 T8f): model, effort and ctx (3.23 T5d2b), '' and 0 when absent. */
export function stepFactsOf(text: string | null): { model: string; effort: string; ctx: number } {
  try {
    const d = JSON.parse(text ?? '') as Record<string, unknown>
    const s = (v: unknown): string => (typeof v === 'string' ? v : '')
    return { model: s(d['model']), effort: s(d['effort']), ctx: typeof d['ctx'] === 'number' && Number.isFinite(d['ctx']) ? d['ctx'] : 0 }
  } catch {
    return { model: '', effort: '', ctx: 0 }
  }
}

export const isLive = (status: string | undefined): boolean => status === 'running' || status === 'pending'

/** 3.24 T17g: the agents still at work for the your-turn toast, `except` left out: running, pending or `waiting` (held
 * on an Agent call or background work it owns). Round 4: the planner, waiting on its first retriever spawn, counted as
 * done, and "your turn" toasted while it planned. */
export const workingIds = (list: readonly { id: string; status?: string }[], except = ''): string[] =>
  list.filter(a => a.id !== except && isBusy(a.status)).map(a => a.id)

type Usage = { input_tokens: number; cache_read_input_tokens: number; cache_creation_input_tokens: number; output_tokens?: number }

/** The context a turn's request carried: input + cache read + cache write. */
export function usageCtx(u: Usage | undefined): number | null {
  return u === undefined ? null : u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
}

/** The next marker number: one past every number recorded or queued. */
export function nextMarker(s: RouterState): number {
  let n = 0
  for (const k of Object.keys(s.markers)) {
    n = Math.max(n, Number(k) || 0)
  }
  for (const k of s.pendingMarkers) {
    n = Math.max(n, k)
  }
  return n + 1
}

/** The reply a marker's file holds: a journal entry's body, or a whole `replies/<n>.md` file (0193534's state). */
export const markerReply = (text: string): string => parseEntry(text)?.body ?? text

/** 3.24 T17g: the model and effort a marker's journal entry (Claude's answer) records, '' each when absent. */
export function markerFacts(text: string): { model: string; effort: string } {
  const meta = parseEntry(text)?.meta
  return { model: meta?.model ?? '', effort: meta?.effort ?? '' }
}

/** One journal entry of the router (writer `r`), named in local time: its path and text. */
export function journalOut(at: number, seq: number, who: string, kind: string, meta: Meta, body: string): JournalOut {
  const [date, time] = stamp(at)
  return { rel: entryPath(date, time, seq % 1000, 'r', who, kind), text: renderEntry({ ts: new Date(at).toISOString(), ...meta }, body) }
}

export const fileEntry = (s: RouterState, out: JournalOut): RouterState => ({ ...s, journal: [...s.journal, out] })

/** The journal's usage of a turn. */
export function entryUsage(u: Usage | undefined): Meta['usage'] {
  return u === undefined
    ? undefined
    : { input: u.input_tokens, cacheRead: u.cache_read_input_tokens, cacheWrite: u.cache_creation_input_tokens, output: u.output_tokens ?? 0 }
}

const DECISION = /\b(your (call|decision|choice)|decide|confirm|approve|go ahead|shall i|should i|do you want|would you like|which (one|option|do you))\b/i

/** An answer that waits on the developer: a `?` in its last 300 chars (trimmed), or a last line asking a decision. */
export function asksQuestion(answer: string): boolean {
  const t = answer.trim()
  if (t.slice(-300).includes('?')) {
    return true
  }
  const last = t.split(/\r?\n/).map(l => l.trim()).filter(l => l !== '').at(-1) ?? ''
  return DECISION.test(last)
}

export function toastOut(cause: ToastOut['cause'], text: string, at: number): ToastOut {
  return { cause, body: text.trim().slice(-240), at }
}

/** 3.23 T13d.c10: vanilla Claude Code's idle-notice wait; the your-turn toast fires this long after Claude's reply. */
export const YOUR_TURN_MS = 60_000

/** Claude's reply arms its your-turn toast (once per turnId); a reply that raised a stop's toast disarms it. `known`:
 * the agents live when it landed. */
export function armYourTurn(s: RouterState, turnId: string, answer: string, now: number, known: string[], stopped: boolean): RouterState {
  if (stopped) {
    return { ...s, yourTurn: null }
  }
  if (s.yourTurn?.turnId === turnId) {
    return s
  }
  const body = answer.split(/\r?\n/).map(l => l.trim()).find(l => l !== '') ?? ''
  return { ...s, yourTurn: { turnId, at: now, body: body.slice(0, 240), known, fired: false } }
}

/** 3.24 T17j: the loop's next step on a hand-back it has not acted on yet, '' none: a contract landed but not routed
 * (`loop:handback` while running, `loop:routing`), or the close chain running (`loop:closing`). A hold means the loop
 * waits on the developer: ''. */
export function loopPending(l: LoopState): string {
  if (l.holds.length > 0) {
    return ''
  }
  if (l.state === 'closing') {
    return 'loop:closing'
  }
  const t = l.task
  if (t === null) {
    return ''
  }
  if (l.state === 'routing') {
    const r = (t as typeof t & { routed?: unknown }).routed
    return typeof r === 'string' && r !== '' ? '' : 'loop:routing'
  }
  return l.state === 'running' && t.contract === null && (t.pendingContract ?? null) !== null ? 'loop:handback' : ''
}

/** The route tick's step for an armed your-turn toast: `held` (a dialog open or queued, a stop's toast owed) or an
 * agent started since the reply drops it; any agent live (running or pending, Claude too) waits; 3.24 T17j: a loop
 * step `pending` (loopPending) waits, its toast (review, close question, park) then drops this one; YOUR_TURN_MS after
 * the reply it fires. */
export function yourTurnStep(y: YourTurn, now: number, live: readonly string[], partnerId: string, held: string, pending = ''):
  { do: 'fire' } | { do: 'wait'; why?: string } | { do: 'drop'; why: string; agents: string[] } {
  if (held !== '') {
    return { do: 'drop', why: held, agents: [] }
  }
  const started = live.filter(id => id !== partnerId && !y.known.includes(id))
  if (started.length > 0) {
    return { do: 'drop', why: 'agent', agents: started }
  }
  if (pending !== '') {
    return { do: 'wait', why: pending }
  }
  return live.length > 0 || now - y.at < YOUR_TURN_MS ? { do: 'wait' } : { do: 'fire' }
}

/** 3.24 T17t: a stop's toast (any but the your-turn one) is sent only after YOUR_TURN_MS of developer quiet since it
 * was raised; the your-turn toast is queued already due. */
export function dueToast(t: ToastOut, now: number): boolean {
  return t.turn === true || now - t.at >= YOUR_TURN_MS
}

/** 3.24 T17t: the developer typed or answered a picker: the stops' toasts still waiting are dropped. */
export function heardDeveloper(s: RouterState): RouterState {
  return s.toasts.some(t => t.turn !== true) ? { ...s, toasts: s.toasts.filter(t => t.turn === true) } : s
}

/** `pa_ledger.py toast` argv: titled with the project folder's name. */
export function toastArgv(python: string, ledger: string, sid: string, root: string, t: ToastOut): string[] {
  const name = root.replace(/[\\/]+$/, '').split(/[\\/]/).at(-1) ?? root
  const what = t.turn === true ? 'your turn' : t.cause === 'input' ? 'needs your input' : t.cause === 'review' ? 'review waiting' : 'Claude asks'
  return [python, ledger, 'toast', '--session', sid, '--cause', t.cause, '--title', `PA3 · ${name} · ${what}`, '--body', t.body, '--project', root]
}

/** A sent message less the router's leading `‹pa›` blocks (the talk line, the running-now note), as the journal keeps it. */
export function bareMessage(sent: string): string {
  let t = sent.trimStart()
  while (t.startsWith('‹pa›')) {
    const i = t.indexOf('\n\n')
    if (i < 0) {
      return t
    }
    t = t.slice(i + 2).trimStart()
  }
  return t
}

/**
 * The seed's entries newest first, without the newest dev msg that is `sending` itself (the message follows the seed);
 * `sending` compared without its `‹pa›` prefix blocks (3.23 T8).
 */
export function seedEntries(entries: Entry[], sending: string): Entry[] {
  const key = (e: Entry): string => `${e.meta.ts ?? ''}\u0000${e.path}`
  const out = [...entries].sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0))
  const bare = bareMessage(sending).trim()
  const i = out.findIndex(e => parseEntryPath(e.path)?.who === 'dev' && e.body.trim() === bare)
  return i < 0 ? out : [...out.slice(0, i), ...out.slice(i + 1)]
}

/**
 * A model's prices from `pa_ledger.py prices --json` output: with `--model`, its `price` row under its `model_key`
 * (Python's own matcher); without, the whole table, looked up by `policy.modelKey`. Null when it gives no rates.
 */
export function priceCacheOf(stdout: string, model: string): PriceCache | null {
  let doc: unknown
  try {
    doc = JSON.parse(stdout.trim().split('\n').at(-1) ?? '')
  } catch {
    return null
  }
  if (typeof doc !== 'object' || doc === null) {
    return null
  }
  const o = doc as Record<string, unknown>
  const price = o['price']
  const key = o['model_key']
  const prices = o['prices']
  let pc: PriceCache | null = null
  if (typeof price === 'object' && price !== null && typeof key === 'string') {
    pc = { table: { [key]: price }, lookup: key }
  } else if (typeof prices === 'object' && prices !== null) {
    pc = { table: prices as Record<string, unknown>, lookup: model }
  }
  return pc !== null && ratesFrom(pc.table, pc.lookup, 0) !== null ? pc : null
}

/** The seed reads dev msgs and partner answers only (`claude`, or `partner` before 3.22 T8f); live or rolled paths (3.23 T7). */
export const isSeedName = (name: string): boolean => /-dev-msg(\.md)?$|-(claude|partner)-answer(\.md)?$/.test(name)

export const framedOther = (kind: string, text: string): string => `A ${kind} prompt arrived: ${text}`

/** Where an `other` message goes: its `child` (a notice's caller) when that is a listed agent other than the partner;
 * '' means the partner's route. */
export function otherTarget(child: string, partnerId: string, list: readonly { id: string }[]): string {
  return child !== '' && child !== partnerId && list.some(a => a.id === child) ? child : ''
}

export const forwardText = (child: string, text: string): string => `Hand-back from your background agent ${child}:\n${text}`

export function enqueue(s: RouterState, kind: OutMsg['kind'], text: string, child: string, at: number): RouterState {
  return { ...s, outbox: [...s.outbox, { kind, text, child, at }] }
}

/** A hand-back taken: the child marked forwarded and its forward queued. */
export function takeHandback(s: RouterState, child: string, text: string, at: number): RouterState {
  return enqueue({ ...s, forwarded: { ...s.forwarded, [child]: at } }, 'forward', forwardText(child, text), child, at)
}

/** The reply marks with `key` set, the newest MAX_REPLIES kept. */
function withMark(s: RouterState, key: string, m: ReplyMark): Record<string, ReplyMark> {
  const all = Object.entries({ ...s.replies, [key]: m }).sort((a, b) => b[1].at - a[1].at || (a[0] < b[0] ? -1 : 1))
  return Object.fromEntries(all.slice(0, MAX_REPLIES))
}

/**
 * The partner's reply recorded: usage, model and the ping hold updated; a new wait starts for warming. Its hand-back
 * already showed it (`key` marked `handback`): marked `done`, no marker. Else its file is marker `n`, marked `wait`:
 * dueMarkers queues it when no hand-back came within HANDBACK_WAIT_MS.
 */
export function applyReply(s: RouterState, n: number, path: string, key: string, now: number, ctx: number | null, model = ''): RouterState {
  const p = s.partner
  const shown = s.replies[key]?.via === 'handback'
  return {
    ...s,
    markers: shown ? s.markers : { ...s.markers, [String(n)]: path },
    replies: withMark(s, key, shown ? { via: 'done', n: 0, at: now } : { via: 'wait', n, at: now }),
    partner: p === null ? null : {
      ...p,
      lastTurnAt: now,
      ctx: ctx ?? p.ctx,
      pingInFlight: false,
      model: model !== '' ? model : p.model,
      firstCtx: p.firstCtx === 0 && ctx !== null ? ctx : p.firstCtx,
    },
    awaiting: null,
    warm: { ...s.warm, waitStart: now, waitUsd: 0, waitPings: 0 },
  }
}

/** A partner hand-back's report taken: `show` unless its answer's marker was queued (`marker`) or it was shown
 * already (`handback`, or `done` within DUP_MS); an answer waiting on it (`wait`) is then carried by it, no marker. */
export function takeReply(s: RouterState, key: string, now: number): { state: RouterState; show: boolean } {
  const m = s.replies[key]
  if (m === undefined || (m.via === 'done' && now - m.at >= DUP_MS)) {
    return { state: { ...s, replies: withMark(s, key, { via: 'handback', n: 0, at: now }) }, show: true }
  }
  if (m.via === 'wait') {
    return { state: { ...s, replies: withMark(s, key, { via: 'done', n: m.n, at: now }) }, show: true }
  }
  return { state: m.via === 'marker' ? { ...s, replies: withMark(s, key, { ...m, via: 'done', at: now }) } : s, show: false }
}

/** Answers that waited HANDBACK_WAIT_MS with no hand-back: marked `marker`, their numbers queued in `at` order. */
export function dueMarkers(s: RouterState, now: number): RouterState {
  const due = Object.entries(s.replies).filter(([, m]) => m.via === 'wait' && now - m.at >= HANDBACK_WAIT_MS)
    .sort((a, b) => a[1].at - b[1].at || a[1].n - b[1].n)
  if (due.length === 0) {
    return s
  }
  const replies = { ...s.replies }
  for (const [k, m] of due) {
    replies[k] = { ...m, via: 'marker' }
  }
  return { ...s, replies, pendingMarkers: [...s.pendingMarkers, ...due.map(([, m]) => m.n)] }
}

/** Any other partner turn ended (a `.` ping, an empty answer): the hold lifts, the send is confirmed. */
export function partnerTurnEnded(s: RouterState, now: number, ctx: number | null): RouterState {
  const p = s.partner
  return { ...s, partner: p === null ? null : { ...p, lastTurnAt: now, ctx: ctx ?? p.ctx, pingInFlight: false }, awaiting: null }
}

/** The parent a forward goes to: the router's record, else `$.agent.list()`'s `parentId`; null when unknown. */
export function parentOf(children: Record<string, Child>, child: string, list: readonly { id: string; parentId?: string }[]): string | null {
  const own = children[child]?.parent
  if (own !== undefined && own !== '') {
    return own
  }
  const p = list.find(a => a.id === child)?.parentId
  return p === undefined || p === '' ? null : p
}

/** The outbox waits while a ping turn runs or a send to the partner is not yet confirmed. */
export const outboxHeld = (s: RouterState): boolean => s.partner?.pingInFlight === true || s.awaiting !== null

// Render rules: rows the transcript draws as nothing, so it reads as a chat (3.21 P3b).
/** A user row the transcript draws as nothing: the router's marker, or a hand-back. The text decides too, because a
 * stored row's origin need not be the one prompt.submit saw (3.22 T11: a marker row drew under "Prompt from the pa
 * plugin" although the origin rule was in place). */
export function hideUser(origin: Origin | undefined, text = ''): boolean {
  if (origin !== undefined && ((origin.kind === 'plugin' && origin.name === 'pa') || HANDBACK.has(origin.kind))) {
    return true
  }
  return /‹pa reply \d+›/.test(text) || /^\s*<task-notification>/.test(text) || /^\s*<agent-message from="/.test(text)
}

export const hideAssistant = (text: string): boolean => text.trim() === STUB

export const hideSpinner = (message: string | null): boolean => message !== null && /background agent/i.test(message)
