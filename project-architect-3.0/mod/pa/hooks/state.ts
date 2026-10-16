// The router's state file `.run/mod/<sid>/state.json` (design.md, "State"): pure parse, serialize and default. It is
// written whole after every transition, keyed by session; `.run/mod/last.json` names the session that wrote last, so a
// resumed or cleared session finds the previous state. Its types live in the contract, types/index.d.ts.
import type { Awaiting, Child, FeedDrawn, JournalOut, OutMsg, Partner, Present, ReplyMark, RouterState, ToastOut, WarmAgent, YourTurn } from '../types'

export type {
  Awaiting, Child, Hold, JournalOut, OutMsg, Partner, Pending, PlanEdit, PlanFile, Present, PriceCache, ReplyMark, ReplyTurn, RouterState,
  ToastOut, TurnKind, TurnRegistry, Warm, WarmAgent, YourTurn,
} from '../types'

/** The schema version; parseState migrates older documents up to it. */
export const STATE_V = 1

/** Processed inbox ids are kept this long; inbox requests older than this are never drained (inbox.ts). */
export const DONE_TTL_MS = 24 * 3_600_000

/** The live feed's folder of session `sid`, project-relative (3.23 T13d: state.json's `feed.dir` when absent). */
export const feedDirOf = (sid: string): string => `.run/mod/${sid}/feed`

export function defaultState(sid: string, gen: number): RouterState {
  return {
    v: STATE_V,
    gen,
    sid,
    partner: null,
    children: {},
    inboxDone: {},
    markers: {},
    pendingMarkers: [],
    replies: {},
    outbox: [],
    forwarded: {},
    awaiting: null,
    sentKind: '',
    sentAt: 0,
    warm: { pings: 0, pingUsd: 0, avoidedUsd: 0, lastPingAt: 0, waitStart: 0, waitUsd: 0, waitPings: 0, waitCount: 0 },
    warmAgents: {},
    hold: null,
    planEdits: [],
    pending: [],
    journal: [],
    toasts: [],
    present: [],
    planFile: null,
    stopToast: null,
    yourTurn: null,
    learning: false,
    feed: { dir: feedDirOf(sid) },
  }
}

type Obj = Record<string, unknown>

const isObj = (x: unknown): x is Obj => typeof x === 'object' && x !== null && !Array.isArray(x)
const num = (x: unknown, d: number): number => (typeof x === 'number' && Number.isFinite(x) ? x : d)
const str = (x: unknown, d: string): string => (typeof x === 'string' ? x : d)

function parseYourTurn(x: unknown): YourTurn | null {
  if (!isObj(x) || typeof x['turnId'] !== 'string' || x['turnId'] === '') {
    return null
  }
  const known = Array.isArray(x['known']) ? x['known'].filter((k): k is string => typeof k === 'string') : []
  return { turnId: x['turnId'], at: num(x['at'], 0), body: str(x['body'], ''), known, fired: x['fired'] === true }
}

function parsePartner(x: unknown): Partner | null {
  if (!isObj(x) || typeof x['agentId'] !== 'string' || x['agentId'] === '') {
    return null
  }
  return {
    agentId: x['agentId'],
    spawnedAt: num(x['spawnedAt'], 0),
    lastTurnAt: num(x['lastTurnAt'], 0),
    model: str(x['model'], ''),
    ctx: num(x['ctx'], 0),
    topic: x['topic'] === true,
    modelSwitchAt: num(x['modelSwitchAt'], 0),
    pingInFlight: x['pingInFlight'] === true,
    firstCtx: num(x['firstCtx'], 0),
  }
}

function parseChildren(x: unknown): Record<string, Child> {
  const out: Record<string, Child> = {}
  if (!isObj(x)) {
    return out
  }
  for (const [id, c] of Object.entries(x)) {
    if (isObj(c)) {
      out[id] = { parent: str(c['parent'], ''), type: str(c['type'], ''), startedAt: num(c['startedAt'], 0), status: str(c['status'], '') }
    }
  }
  return out
}

/** v0 (before the `v` field) kept inboxDone as a list of ids; they migrate stamped with `now`. */
function parseDone(x: unknown, now: number): Record<string, number> {
  const out: Record<string, number> = {}
  if (Array.isArray(x)) {
    for (const id of x) {
      if (typeof id === 'string') {
        out[id] = now
      }
    }
  } else if (isObj(x)) {
    for (const [id, at] of Object.entries(x)) {
      if (typeof at === 'number') {
        out[id] = at
      }
    }
  }
  return out
}

function parseList<T>(x: unknown, one: (o: Obj) => T | null): T[] {
  if (!Array.isArray(x)) {
    return []
  }
  const out: T[] = []
  for (const item of x) {
    const t = isObj(item) ? one(item) : null
    if (t !== null) {
      out.push(t)
    }
  }
  return out
}

/**
 * The state a file holds, migrated to STATE_V with defaults for missing or mistyped fields; null when the text does
 * not parse, is not an object, or carries a newer `v` than this code knows (never overwrite what a newer mod wrote).
 * `sid` fills a document that lacks one.
 */
export function parseState(text: string, sid: string, now: number): RouterState | null {
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObj(doc)) {
    return null
  }
  const v = num(doc['v'], 0)
  if (v > STATE_V) {
    return null
  }
  const base = defaultState(str(doc['sid'], sid), num(doc['gen'], 0))
  const markers: Record<string, string> = {}
  if (isObj(doc['markers'])) {
    for (const [n, p] of Object.entries(doc['markers'])) {
      if (typeof p === 'string') {
        markers[n] = p
      }
    }
  }
  const forwarded: Record<string, number> = {}
  if (isObj(doc['forwarded'])) {
    for (const [id, at] of Object.entries(doc['forwarded'])) {
      if (typeof at === 'number') {
        forwarded[id] = at
      }
    }
  }
  const replies: Record<string, ReplyMark> = {}
  if (isObj(doc['replies'])) {
    for (const [k, m] of Object.entries(doc['replies'])) {
      const via = isObj(m) ? m['via'] : undefined
      if (isObj(m) && (via === 'wait' || via === 'marker' || via === 'handback' || via === 'done')) {
        replies[k] = { via, n: num(m['n'], 0), at: num(m['at'], 0) }
      }
    }
  }
  const aw = doc['awaiting']
  const awaiting: Awaiting | null =
    isObj(aw) && typeof aw['agentId'] === 'string' ? { agentId: aw['agentId'], text: str(aw['text'], ''), at: num(aw['at'], 0) } : null
  const w = isObj(doc['warm']) ? doc['warm'] : {}
  const warmAgents: Record<string, WarmAgent> = {}
  if (isObj(doc['warmAgents'])) {
    for (const [id, a] of Object.entries(doc['warmAgents'])) {
      if (isObj(a)) {
        warmAgents[id] = {
          role: str(a['role'], ''), lastPingAt: num(a['lastPingAt'], 0), pingInFlight: a['pingInFlight'] === true,
          waitKind: str(a['waitKind'], ''), waitStart: num(a['waitStart'], 0), waitUsd: num(a['waitUsd'], 0),
          waitPings: num(a['waitPings'], 0), waitCount: num(a['waitCount'], 0),
          pings: num(a['pings'], 0), pingUsd: num(a['pingUsd'], 0), avoidedUsd: num(a['avoidedUsd'], 0),
        }
      }
    }
  }
  const h = doc['hold']
  const pf = doc['planFile']
  return {
    ...base,
    partner: parsePartner(doc['partner']),
    children: parseChildren(doc['children']),
    inboxDone: parseDone(doc['inboxDone'], now),
    markers,
    pendingMarkers: Array.isArray(doc['pendingMarkers'])
      ? doc['pendingMarkers'].filter((n): n is number => typeof n === 'number' && Number.isInteger(n))
      : [],
    replies,
    outbox: parseList(doc['outbox'], (o): OutMsg | null => {
      const kind = o['kind']
      return (kind === 'dev' || kind === 'other' || kind === 'forward') && typeof o['text'] === 'string'
        ? { kind, text: o['text'], child: str(o['child'], ''), at: num(o['at'], 0) }
        : null
    }),
    forwarded,
    awaiting,
    sentKind: doc['sentKind'] === 'dev' || doc['sentKind'] === 'other' || doc['sentKind'] === 'forward' ? doc['sentKind'] : '',
    sentAt: num(doc['sentAt'], 0),
    warm: {
      pings: num(w['pings'], 0),
      pingUsd: num(w['pingUsd'], 0),
      avoidedUsd: num(w['avoidedUsd'], 0),
      lastPingAt: num(w['lastPingAt'], 0),
      waitStart: num(w['waitStart'], 0),
      waitUsd: num(w['waitUsd'], 0),
      waitPings: num(w['waitPings'], 0),
      waitCount: num(w['waitCount'], 0),
    },
    warmAgents,
    hold: isObj(h) ? { reason: str(h['reason'], ''), at: num(h['at'], 0) } : null,
    planEdits: parseList(doc['planEdits'], o =>
      typeof o['id'] === 'string' ? { id: o['id'], agentId: str(o['agentId'], ''), at: num(o['at'], 0) } : null),
    pending: parseList(doc['pending'], o =>
      typeof o['id'] === 'string' ? { id: o['id'], agentId: str(o['agentId'], ''), kind: str(o['kind'], ''), at: num(o['at'], 0) } : null),
    journal: parseList(doc['journal'], (o): JournalOut | null =>
      typeof o['rel'] === 'string' && typeof o['text'] === 'string' ? { rel: o['rel'], text: o['text'] } : null),
    toasts: parseList(doc['toasts'], (o): ToastOut | null =>
      (o['cause'] === 'question' || o['cause'] === 'input' || o['cause'] === 'review') && typeof o['body'] === 'string'
        ? { cause: o['cause'], body: o['body'], at: num(o['at'], 0), ...(o['turn'] === true ? { turn: true as const } : {}) }
        : null),
    present: parseList(doc['present'], (o): Present | null =>
      typeof o['id'] === 'string' && (o['kind'] === 'plan' || o['kind'] === 'ask')
        ? {
          id: o['id'], kind: o['kind'], caller: str(o['caller'], ''), path: str(o['path'], ''), title: str(o['title'], ''),
          question: str(o['question'], ''),
          options: Array.isArray(o['options']) ? o['options'].filter((x): x is string => typeof x === 'string') : [],
          at: num(o['at'], 0),
          ...(o['planKind'] === 'generation' || o['planKind'] === 'phase' || o['planKind'] === 'adhoc' ? { planKind: o['planKind'] } : {}),
          ...(Array.isArray(o['rules']) ? { rules: o['rules'].filter((x): x is string => typeof x === 'string') } : {}),
          ...(Array.isArray(o['candidates']) ? { candidates: o['candidates'].filter((x): x is string => typeof x === 'string') } : {}),
        }
        : null),
    planFile: isObj(pf) && typeof pf['path'] === 'string' && pf['path'] !== '' ? { sid: str(pf['sid'], ''), path: pf['path'] } : null,
    stopToast: doc['stopToast'] === 'question' || doc['stopToast'] === 'input' || doc['stopToast'] === 'review' ? doc['stopToast'] : null,
    yourTurn: parseYourTurn(doc['yourTurn']),
    learning: doc['learning'] === true,
    // 3.23 T13d: absent (a state from before the feed), the folder of the session that wrote it.
    feed: isObj(doc['feed']) && typeof doc['feed']['dir'] === 'string' && doc['feed']['dir'] !== '' ? { dir: doc['feed']['dir'] } : base.feed,
  }
}

export function serializeState(s: RouterState): string {
  return `${JSON.stringify({ ...s, v: STATE_V }, null, 1)}\n`
}

export function statePath(base: string, sid: string): string {
  return `${base}/${sid}/state.json`
}

export function lastPath(base: string): string {
  return `${base}/last.json`
}

export function lastJson(sid: string, at: number): string {
  return `${JSON.stringify({ sid, at })}\n`
}

/** The session last.json names, or null. */
export function parseLast(text: string | null): string | null {
  if (text === null) {
    return null
  }
  try {
    const doc: unknown = JSON.parse(text)
    const sid = isObj(doc) ? doc['sid'] : undefined
    return typeof sid === 'string' && sid !== '' ? sid : null
  } catch {
    return null
  }
}

/** The state with inbox ids older than `ttl` dropped (their requests are past the drain's horizon too). */
export function pruneDone(s: RouterState, now: number, ttl: number = DONE_TTL_MS): RouterState {
  const kept: Record<string, number> = {}
  for (const [id, at] of Object.entries(s.inboxDone)) {
    if (now - at <= ttl) {
      kept[id] = at
    }
  }
  return { ...s, inboxDone: kept }
}

export function markDone(s: RouterState, id: string, at: number): RouterState {
  return { ...s, inboxDone: { ...s.inboxDone, [id]: at } }
}

/** 3.23 T13d.c6a: the router's drawn registries (its card texts, its feed answers' runs), kept in `.run/mod/drawn.json`
 * as well as in plugin state, so a resumed, restarted or cleared session still draws them. */
export type Drawn = { cards: string[]; feedRuns: FeedDrawn[] }

export function drawnPath(base: string): string {
  return `${base}/drawn.json`
}

/** The file's registries; empty ones when it is absent or unreadable; a malformed entry is dropped. */
export function parseDrawn(text: string | null): Drawn {
  let doc: unknown = null
  try {
    doc = text === null ? null : JSON.parse(text)
  } catch {
    doc = null
  }
  const d = isObj(doc) ? doc : {}
  const cards = Array.isArray(d['cards']) ? d['cards'].filter((c): c is string => typeof c === 'string') : []
  const feedRuns = Array.isArray(d['feedRuns'])
    ? d['feedRuns'].filter((x): x is FeedDrawn => isObj(x) && typeof x['text'] === 'string' && Array.isArray(x['runs']))
    : []
  return { cards, feedRuns }
}

export function serializeDrawn(d: Drawn): string {
  return `${JSON.stringify(d)}\n`
}

/** `kept` (the file's) then `live` entries not in it (same `key`), the newest `cap` kept. */
export function mergeDrawn<T>(kept: readonly T[], live: readonly T[], key: (x: T) => string, cap: number): T[] {
  const have = new Set(kept.map(key))
  return [...kept, ...live.filter(x => !have.has(key(x)))].slice(-cap)
}
