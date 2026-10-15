// Pure policy of the router (design.md "Warming (rent-or-buy)", 3.22-partner.md): resume or seed a fresh partner,
// the per-token rates from `pa_ledger.py prices --json`, and when to ping an idle agent to keep its cache warm.

export const TTL_MS = 3_600_000 // the 1h cache TTL
export const MARGIN_MS = 300_000
export const CTX_MAX = 300_000 // rotate (and stop warming) at ~300k context
export const PING_AFTER_MS = 55 * 60_000
export const CHILD_WARM_MS = 24 * 3_600_000
export const PING_WRITE_TOKENS = 150
export const PING_OUTPUT_TOKENS = 30

export type PartnerFacts = {
  lastTurnAt: number
  ctx: number
  topicFlag?: boolean
  modelSwitchAt?: number
}

/** `'resume'` the recorded partner or seed a `'fresh'` one; never rotate with a child out. */
export function decideRoute(
  partner: PartnerFacts | null | undefined,
  now: number,
  opts: { childOut?: boolean } = {},
): 'resume' | 'fresh' {
  if (!partner) return 'fresh'
  if (opts.childOut) return 'resume'
  if (!(now - partner.lastTurnAt < TTL_MS - MARGIN_MS)) return 'fresh'
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

/** One ping: the context read, a small write at write_1h, a short output. */
export function pingUsd(ctx: number, rates: Rates): number {
  return ctx * rates.read + PING_WRITE_TOKENS * rates.write_1h + PING_OUTPUT_TOKENS * rates.output
}

/** A fresh start: the seed plus the measured prefix written at write_1h. */
export function freshStartUsd(seedTokens: number, prefixTokens: number, rates: Rates): number {
  return (seedTokens + prefixTokens) * rates.write_1h
}

export type AgentFacts = { status: string; lastRequestAt: number; ctx: number }

export type PingOptions = {
  childOut?: boolean
  waitStart?: number // when the wait began; defaults to lastRequestAt
  spentUsd?: number // pings already spent in this wait
  seedTokens?: number
  prefixTokens?: number
  pingAfterMs?: number // the trial knob PA_MOD_PING_AFTER_MS; defaults to PING_AFTER_MS
}

/**
 * The loop expert's status for warming (3.23 T13b.c4): `$.agent.list()` forgets a finished agent (design.md "Warming"),
 * and one dropped from it is still resumed by a send. So an expert absent from the list, in a wait (`kind`: question or
 * child) after a recorded turn end and not stopped, counts as `completed`; else its listed status (undefined: unknown).
 */
export function expertStatus(listed: string | undefined, kind: string, lastCompleteAt: number, stopped: boolean): string | undefined {
  return listed ?? (kind !== '' && lastCompleteAt > 0 && !stopped ? 'completed' : undefined)
}

/**
 * The partner's status for warming (3.23, as expertStatus): one dropped from `$.agent.list()` after a recorded turn
 * end counts as `completed`, since a send still resumes it; else its listed status (undefined: unknown).
 */
export function partnerStatus(listed: string | undefined, lastTurnAt: number): string | undefined {
  return listed ?? (lastTurnAt > 0 ? 'completed' : undefined)
}

/** Whether to ping now, when the ping is due (`at`), and the rule that decided (`why`). */
export function pingDecision(
  agent: AgentFacts,
  now: number,
  rates: Rates,
  opts: PingOptions = {},
): { ping: boolean; at: number; why: string } {
  const at = agent.lastRequestAt + (opts.pingAfterMs ?? PING_AFTER_MS)
  if (agent.status !== 'idle' && agent.status !== 'completed') return { ping: false, at, why: `status ${agent.status}` }
  if (now < at) return { ping: false, at, why: 'not due' }
  // A child out wakes the agent with its hand-back, and it is never rotated meanwhile, so it stays warm at any size
  // (a cold wake rewrites the whole context: ~$2.40 at 300k on Opus 5.5 against ~$0.06 a ping).
  if (opts.childOut) {
    const waited = now - (opts.waitStart ?? agent.lastRequestAt)
    return waited < CHILD_WARM_MS ? { ping: true, at, why: 'child out' } : { ping: false, at, why: 'child out 24h' }
  }
  // Waiting on the developer near 300k: the next message rotates to a fresh partner, so warming buys nothing.
  if (agent.ctx >= CTX_MAX) return { ping: false, at, why: 'skip300k' }
  const spent = (opts.spentUsd ?? 0) + pingUsd(agent.ctx, rates)
  const fresh = freshStartUsd(opts.seedTokens ?? 0, opts.prefixTokens ?? 0, rates)
  return spent <= fresh ? { ping: true, at, why: 'rent' } : { ping: false, at, why: 'buy' }
}
