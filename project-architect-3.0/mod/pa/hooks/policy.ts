// Pure policy of the router (design.md "Warming", 3.22-partner.md): resume or seed a fresh partner, the per-token
// rates from `pa_ledger.py prices --json`, when to ping an idle agent to keep its cache warm, and when it is cold.

export const TTL_MS = 3_600_000 // the 1h cache TTL
export const CTX_MAX = 300_000 // rotate at ~300k context
export const PING_AFTER_MS = 55 * 60_000
// 3.28.8: a 5-minute agent (a coder) is pinged 4 min after its last request (was 4.5): the 30-s warm tick and its
// answer put the pings ~4.5 min apart, each under the 5-min cache with room for the request's own seconds (a simulated
// run at 4.5 min had them 5.0 min apart, 4.83 min after the last answer; 26 real coder pings at 4.2-4.6 min all hit).
export const PING_AFTER_5M_MS = 240_000
export const PING_WRITE_TOKENS = 150
export const PING_OUTPUT_TOKENS = 30

/** An agent's cache tier: its agent file's `cacheTtl` (cacheTtlOf). */
export type Ttl = '5m' | '1h'

/**
 * 3.28.8 (developer 2026-10-16): the pings an idle stretch gets (from a real turn's end: the developer's message, a
 * message between agents, a hand-back, a background shell's end), from PA3's own history: a ping pays while the wait is
 * likelier to end soon than the ping costs against going cold (a coder's cold rewrite; an expert's or Claude's fresh
 * seeded start). Coders: 2.5 h flat, 33 ~4.5 min apart (shell waits ended within 2.5 h, but one overnight job). Experts:
 * 12 h flat, 12 at 55 min (long waits kept ending; a night's question), 7 past 250k. Claude: by size, as a fresh Claude is
 * cheap and starts lean: 12 under 100k, 6 under 125k, 1 under 175k, none from there (a fresh Claude takes the next
 * message). Was 3.28.1 T12's size budget for all: 300k / ctx (1-5) at 1 h, 600k / ctx (1-12) at 5 min.
 */
export type WarmRole = 'coder' | 'expert' | 'claude'
const LIFE_MS = { '1h': TTL_MS, '5m': 300_000 } as const

/** The pings an idle stretch of `role` gets at `ctx` (an unknown context, 0, counts as small). */
export function pingCount(ctx: number, role: WarmRole): number {
  if (role === 'coder') {
    return 33
  }
  if (role === 'expert') {
    return ctx > 250_000 ? 7 : 12
  }
  return ctx < 100_000 ? 12 : ctx < 125_000 ? 6 : ctx < 175_000 ? 1 : 0
}

/** When a ping falls due after the last request: 55 min (`override`: the trial knob), 4 min for a 5-minute agent. */
export function pingAfterOf(ttl: Ttl, override?: number): number {
  return ttl === '5m' ? PING_AFTER_5M_MS : override ?? PING_AFTER_MS
}

/**
 * 3.28.1 T12: an idle agent is cold once its last request (a turn's end or a ping) is older than its cache's life, or,
 * its stretch's pings spent (`pingsLeft` false), older than the ping time. Nothing is sent to a cold agent: a fresh one
 * of its role, seeded from the journal, takes the message and its place.
 */
export function isCold(lastRequestAt: number, now: number, ttl: Ttl, pingsLeft: boolean, pingAfterMs?: number): boolean {
  const age = now - lastRequestAt
  return age >= LIFE_MS[ttl] || (!pingsLeft && age >= pingAfterOf(ttl, pingAfterMs))
}

export type PartnerFacts = {
  lastTurnAt: number
  ctx: number
  topicFlag?: boolean
  modelSwitchAt?: number
}

/** `'resume'` the recorded partner or seed a `'fresh'` one: a cold one never resumes; else never rotate with a child out. */
export function decideRoute(
  partner: PartnerFacts | null | undefined,
  now: number,
  opts: { childOut?: boolean; pingsLeft?: boolean } = {},
): 'resume' | 'fresh' {
  if (!partner) return 'fresh'
  if (isCold(partner.lastTurnAt, now, '1h', opts.pingsLeft === true)) return 'fresh'
  if (opts.childOut) return 'resume'
  if (!(partner.ctx < CTX_MAX)) return 'fresh'
  if (partner.topicFlag) return 'fresh'
  if ((partner.modelSwitchAt ?? 0) > partner.lastTurnAt) return 'fresh'
  return 'resume'
}

export type Rates = { input: number; write_5m: number; write_1h: number; read: number; output: number }

const RATE_KEYS = ['input', 'write_5m', 'write_1h', 'read', 'output'] as const

/** Like `pa/prices.py normalize_model`: lowercase, no `[1m]` tags, no spaces, no date suffix. */
export function normalizeModel(model: string): string {
  return model
    .toLowerCase()
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\s+/g, '')
    .replace(/-[0-9]{8}$/, '')
}

/** The price key of a model: `claude-opus-5-5[1m]` → `opus-5-5`; the longest key the normalized id contains. */
export function modelKey(model: string, keys: string[]): string | null {
  const m = normalizeModel(model)
  const bare = m.replace(/^claude-/, '')
  if (keys.includes(bare)) return bare
  const hits = keys.filter((k) => m.includes(k)).sort((a, b) => b.length - a.length || (a < b ? -1 : 1))
  return hits[0] ?? null
}

function perToken(r: Record<string, unknown>): Rates | null {
  const out = {} as Rates
  for (const k of RATE_KEYS) {
    const v = Number(r[k])
    if (!Number.isFinite(v)) return null
    out[k] = v / 1_000_000
  }
  return out
}

/** Per-token rates for `model` at `ctx` from the prices JSON ($/MTok; a `prompt_tier` replaces them when ctx > over). */
export function ratesFrom(pricesJson: string | Record<string, unknown>, model: string, ctx: number): Rates | null {
  let table: Record<string, unknown>
  try {
    table = typeof pricesJson === 'string' ? (JSON.parse(pricesJson) as Record<string, unknown>) : pricesJson
  } catch {
    return null
  }
  if (!table || typeof table !== 'object') return null
  // The real documents: `prices --json` holds the table under `prices`; `--model` holds one row as `price`.
  if (table.prices && typeof table.prices === 'object') table = table.prices as Record<string, unknown>
  else if (table.price && typeof table.price === 'object' && typeof table.model_key === 'string') {
    table = { [table.model_key]: table.price }
  }
  const key = modelKey(model, Object.keys(table))
  if (!key) return null
  const row = table[key] as Record<string, unknown> | undefined
  if (!row || typeof row !== 'object') return null
  // A prompt tier's rates are nested (`rates`) or, as `pa_ledger.py prices --json` writes them, flat beside `over`.
  const tier = row.prompt_tier as ({ over?: unknown; rates?: Record<string, unknown> } & Record<string, unknown>) | undefined
  if (tier && typeof tier === 'object' && ctx > Number(tier.over)) {
    return perToken({ ...row, ...(tier.rates ?? tier) })
  }
  return perToken(row)
}

/** 3.28.1 T11: an agent file's cache tier (`cacheTtl: 5m` or `1h`); `1h` when it names none. */
export function cacheTtlOf(agentMd: string | null): '5m' | '1h' {
  return /^\s*cacheTtl:\s*5m\b/m.test(agentMd ?? '') ? '5m' : '1h'
}

/** Rates at an agent's tier: a 5-minute agent writes at write_5m (pingUsd and a cold rewrite read `write_1h`). */
export function tierRates(rates: Rates, ttl: '5m' | '1h'): Rates {
  return ttl === '5m' ? { ...rates, write_1h: rates.write_5m } : rates
}

/** One ping: the context read, a small write at write_1h, a short output. */
export function pingUsd(ctx: number, rates: Rates): number {
  return ctx * rates.read + PING_WRITE_TOKENS * rates.write_1h + PING_OUTPUT_TOKENS * rates.output
}

export type AgentFacts = { status: string; lastRequestAt: number; ctx: number }

export type PingOptions = {
  ttl?: Ttl // its tier; defaults to 1h
  pings?: number // pings already sent in this idle stretch
  count?: number // the stretch's pings (pingCount at its start); defaults to pingCount at agent.ctx
  pingAfterMs?: number // the trial knob PA_MOD_PING_AFTER_MS (1-hour agents); defaults to PING_AFTER_MS
}

/**
 * The loop expert's status for warming (3.23 T13b.c4): `$.agent.list()` forgets a finished agent (design.md "Warming"),
 * and one dropped from it is still resumed by a send. So an expert absent from the list, in a wait (`kind`: question or
 * child) after a recorded turn end and not stopped, counts as `completed`; else its listed status (undefined: unknown).
 * 3.28.1 T8: with a child out, an expert whose own turn has ended (`turnEnded`: a turn.complete after its last request)
 * counts as `idle` whatever the list says: the list holds it `waiting` (or `running`) while its background child runs.
 * 3.28.1 T15: so does one waiting on a background shell (`kind` shell).
 */
export function expertStatus(listed: string | undefined, kind: string, lastCompleteAt: number, stopped: boolean, turnEnded = false): string | undefined {
  if ((kind === 'child' || kind === 'shell') && turnEnded && !stopped && (listed === 'waiting' || listed === 'running')) return 'idle'
  return listed ?? (kind !== '' && lastCompleteAt > 0 && !stopped ? 'completed' : undefined)
}

/**
 * The partner's status for warming (3.23, as expertStatus): one dropped from `$.agent.list()` after a recorded turn
 * end counts as `completed`, since a send still resumes it; else its listed status (undefined: unknown).
 * 3.28.1 T12: one whose own turn has ended (`turnEnded`: a turn end after the last message sent to it) counts as `idle`
 * when listed `waiting`: the list holds it so while background work it owns runs (TerrainDiffusion a3dff244: its own
 * background shell never ended, and it was never pinged).
 */
export function partnerStatus(listed: string | undefined, lastTurnAt: number, turnEnded = false): string | undefined {
  if (turnEnded && listed === 'waiting') return 'idle'
  return listed ?? (lastTurnAt > 0 ? 'completed' : undefined)
}

/**
 * Whether to ping now, when the ping is due (`at`), and the rule that decided (`why`): an idle agent is pinged just
 * before its cache lapses while its stretch has pings left (pingCount, by role); never a cold one.
 */
export function pingDecision(agent: AgentFacts, now: number, opts: PingOptions = {}): { ping: boolean; at: number; why: string } {
  const ttl = opts.ttl ?? '1h'
  const at = agent.lastRequestAt + pingAfterOf(ttl, opts.pingAfterMs)
  if (agent.status !== 'idle' && agent.status !== 'completed') return { ping: false, at, why: `status ${agent.status}` }
  if ((opts.pings ?? 0) >= (opts.count ?? pingCount(agent.ctx, ttl === '5m' ? 'coder' : 'expert'))) return { ping: false, at, why: 'count' }
  if (now < at) return { ping: false, at, why: 'not due' }
  if (now - agent.lastRequestAt >= LIFE_MS[ttl]) return { ping: false, at, why: 'cold' }
  return { ping: true, at, why: 'size' }
}
