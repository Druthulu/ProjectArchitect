import { describe, expect, test } from 'claude-code/testing'

import {
  cacheTtlOf, decideRoute, expertStatus, isCold, partnerStatus, PING_AFTER_5M_MS, pingCount, pingDecision, pingUsd, ratesFrom, type Rates, tierRates,
} from './policy'

// Sample `pa_ledger.py prices --json` output ($/MTok).
const PRICES = JSON.stringify({
  'opus-5-5': { input: 4.0, write_5m: 5.0, write_1h: 8.0, read: 0.2, output: 20.0 },
  'sonnet-5-5': { input: 2.0, write_5m: 2.5, write_1h: 4.0, read: 0.1, output: 10.0 },
  'fable-5-1': { input: 8.0, write_5m: 10.0, write_1h: 16.0, read: 0.4, output: 40.0 },
  'haiku-5-5': {
    input: 1.0,
    write_5m: 1.25,
    write_1h: 2.0,
    read: 0.05,
    output: 5.0,
    prompt_tier: { over: 100000, rates: { input: 2.0, write_5m: 2.5, write_1h: 4.0, read: 0.5, output: 7.5 } },
  },
})

const M = 1_000_000
const close = (a: number, b: number) => Math.abs(a - b) < 1e-12

describe('decideRoute', () => {
  const now = 10 * 3_600_000
  const p = { lastTurnAt: now - 60_000, ctx: 50_000 }

  test('resume when every condition holds; fresh with no partner', async () => {
    expect(decideRoute(p, now)).toBe('resume')
    expect(decideRoute(null, now)).toBe('fresh')
    expect(decideRoute(undefined, now, { childOut: true })).toBe('fresh')
  })

  test('3.28.1 T12: cold is fresh: pings spent, at the ping time (55 min); pings left, at the cache life (60 min)', async () => {
    expect(decideRoute({ ...p, lastTurnAt: now - 3_299_999 }, now)).toBe('resume')
    expect(decideRoute({ ...p, lastTurnAt: now - 3_300_000 }, now)).toBe('fresh')
    expect(decideRoute({ ...p, lastTurnAt: now - 3_300_000 }, now, { pingsLeft: true })).toBe('resume')
    expect(decideRoute({ ...p, lastTurnAt: now - 3_600_000 }, now, { pingsLeft: true })).toBe('fresh')
  })

  test('ctx < 300,000', async () => {
    expect(decideRoute({ ...p, ctx: 299_999 }, now)).toBe('resume')
    expect(decideRoute({ ...p, ctx: 300_000 }, now)).toBe('fresh')
  })

  test('the topic flag and a model switch after the last turn', async () => {
    expect(decideRoute({ ...p, topicFlag: true }, now)).toBe('fresh')
    expect(decideRoute({ ...p, modelSwitchAt: p.lastTurnAt }, now)).toBe('resume')
    expect(decideRoute({ ...p, modelSwitchAt: p.lastTurnAt + 1 }, now)).toBe('fresh')
  })

  test('with a child out it resumes unless cold (3.28.1 T12: cold beats a child out)', async () => {
    const big = { lastTurnAt: now - 60_000, ctx: 900_000, topicFlag: true, modelSwitchAt: now }
    expect(decideRoute(big, now)).toBe('fresh')
    expect(decideRoute(big, now, { childOut: true })).toBe('resume')
    expect(decideRoute({ ...big, lastTurnAt: 0 }, now, { childOut: true, pingsLeft: true })).toBe('fresh')
  })
})

describe('ratesFrom', () => {
  test('model names normalize like the Python side', async () => {
    const opus = ratesFrom(PRICES, 'claude-opus-5-5[1m]', 0)!
    expect(close(opus.input, 4 / M) && close(opus.write_5m, 5 / M) && close(opus.write_1h, 8 / M)).toBe(true)
    expect(close(opus.read, 0.2 / M) && close(opus.output, 20 / M)).toBe(true)
    expect(close(ratesFrom(PRICES, 'claude-sonnet-5-5', 0)!.read, 0.1 / M)).toBe(true)
    expect(close(ratesFrom(PRICES, 'claude-fable-5-1[1m]', 0)!.write_1h, 16 / M)).toBe(true)
    expect(close(ratesFrom(PRICES, 'claude-haiku-5-5', 0)!.read, 0.05 / M)).toBe(true)
    expect(close(ratesFrom(PRICES, 'Claude-Haiku-5-5-20261001', 0)!.read, 0.05 / M)).toBe(true)
    expect(ratesFrom(PRICES, 'claude-opus-4-1', 0)).toBe(null)
    expect(ratesFrom('not json', 'claude-opus-5-5', 0)).toBe(null)
  })

  test('the prompt tier applies only when ctx > over', async () => {
    expect(close(ratesFrom(PRICES, 'claude-haiku-5-5', 100_000)!.read, 0.05 / M)).toBe(true)
    const tier = ratesFrom(PRICES, 'claude-haiku-5-5', 100_001)!
    expect(close(tier.read, 0.5 / M) && close(tier.output, 7.5 / M)).toBe(true)
    expect(close(ratesFrom(JSON.parse(PRICES), 'claude-opus-5-5', 500_000)!.read, 0.2 / M)).toBe(true)
  })
})

describe('warming costs', () => {
  const r = ratesFrom(PRICES, 'claude-opus-5-5', 0)!
  test('pingUsd = ctx·read + 150·write_1h + 30·output', async () => {
    expect(close(pingUsd(100_000, r), (100_000 * 0.2 + 150 * 8 + 30 * 20) / M)).toBe(true)
    expect(close(pingUsd(0, r), (150 * 8 + 30 * 20) / M)).toBe(true)
  })
})

describe('pingDecision', () => {
  const r: Rates = ratesFrom(PRICES, 'claude-opus-5-5', 0)!
  const t0 = 1_000_000_000
  const due = t0 + 55 * 60_000
  const a = { status: 'idle', lastRequestAt: t0, ctx: 100_000 }

  test('only idle or completed agents', async () => {
    expect(pingDecision({ ...a, status: 'running' }, due)).toEqual({ ping: false, at: due, why: 'status running' })
    expect(pingDecision({ ...a, status: 'completed' }, due).ping).toBe(true)
    expect(pingDecision(a, due).ping).toBe(true)
  })

  test('due at lastRequestAt + 55 min; at the cache life it is cold, never pinged', async () => {
    expect(pingDecision(a, due - 1)).toEqual({ ping: false, at: due, why: 'not due' })
    expect(pingDecision(a, due)).toEqual({ ping: true, at: due, why: 'size' })
    expect(pingDecision(a, t0 + 3_600_000)).toEqual({ ping: false, at: due, why: 'cold' })
  })

  test('3.28.8: pingCount by role: coders 33 (2.5 h), experts 12 (12 h) or 7 past 250k, Claude by size', async () => {
    expect([0, 20_000, 200_000].map(c => pingCount(c, 'coder'))).toEqual([33, 33, 33])
    expect([0, 50_000, 250_000, 250_001, 900_000].map(c => pingCount(c, 'expert'))).toEqual([12, 12, 12, 7, 7])
    expect([0, 99_999, 100_000, 124_999, 125_000, 174_999, 175_000, 300_000].map(c => pingCount(c, 'claude')))
      .toEqual([12, 12, 6, 6, 1, 1, 0, 0])
  })

  test('3.28.1 T12: no ping after the stretch\'s count; the count is the stretch\'s, not the size now', async () => {
    expect(pingDecision(a, due, { pings: 2, count: 3 }).ping).toBe(true)
    expect(pingDecision(a, due, { pings: 3, count: 3 })).toEqual({ ping: false, at: due, why: 'count' })
    expect(pingDecision(a, due, { pings: 12 })).toEqual({ ping: false, at: due, why: 'count' }) // 3.28.8: an expert's 12
    expect(pingDecision({ ...a, ctx: 250_000 }, due, { pings: 1, count: 5 }).ping).toBe(true)
  })

  test('3.28.8: a 5-minute agent is due at 4 min and cold at 5', async () => {
    const at5 = t0 + PING_AFTER_5M_MS
    expect(pingDecision(a, at5 - 1, { ttl: '5m' })).toEqual({ ping: false, at: at5, why: 'not due' })
    expect(pingDecision(a, at5, { ttl: '5m' })).toEqual({ ping: true, at: at5, why: 'size' })
    expect(pingDecision(a, t0 + 300_000, { ttl: '5m' })).toEqual({ ping: false, at: at5, why: 'cold' })
  })

  test('3.28.1 T12: isCold: the cache life, or the ping time once the pings are spent', async () => {
    expect(isCold(t0, t0 + 3_599_999, '1h', true)).toBe(false)
    expect(isCold(t0, t0 + 3_600_000, '1h', true)).toBe(true)
    expect(isCold(t0, due - 1, '1h', false)).toBe(false)
    expect(isCold(t0, due, '1h', false)).toBe(true)
    expect(isCold(t0, t0 + 299_999, '5m', true)).toBe(false)
    expect(isCold(t0, t0 + 300_000, '5m', true)).toBe(true)
    expect(isCold(t0, t0 + PING_AFTER_5M_MS, '5m', false)).toBe(true)
  })

  test('3.28.1 T12: a partner listed waiting after its own turn ended is idle (TerrainDiffusion a3dff244)', async () => {
    expect(partnerStatus('waiting', t0, true)).toBe('idle')
    expect(partnerStatus('waiting', t0, false)).toBe('waiting')
    expect(partnerStatus('running', t0, true)).toBe('running')
  })

  test('the loop expert dropped from $.agent.list() while it waits counts as completed (T13b.c4)', async () => {
    expect(expertStatus(undefined, 'question', t0, false)).toBe('completed')
    expect(expertStatus(undefined, 'child', t0, false)).toBe('completed')
    expect(expertStatus('running', 'question', t0, false)).toBe('running')
    expect(expertStatus(undefined, '', t0, false)).toBeUndefined()
    expect(expertStatus(undefined, 'question', 0, false)).toBeUndefined()
    expect(expertStatus(undefined, 'question', t0, true)).toBeUndefined()
  })

  test('3.28.1 T8: a child out and its own turn ended: idle, whatever the list says', async () => {
    expect(expertStatus('waiting', 'child', t0, false, true)).toBe('idle')
    expect(expertStatus('running', 'child', t0, false, true)).toBe('idle')
    expect(expertStatus('running', 'child', t0, false, false)).toBe('running')
    expect(expertStatus('waiting', 'question', t0, false, true)).toBe('waiting')
    expect(expertStatus('waiting', 'child', t0, true, true)).toBe('waiting')
  })

  test('3.28.1 T15: an expert waiting on a background shell, its turn ended, is idle', async () => {
    expect(expertStatus('waiting', 'shell', t0, false, true)).toBe('idle')
  })

  test('3.28.1 T11: cacheTtlOf reads the agent file; tierRates writes a 5m agent at write_5m', async () => {
    expect(cacheTtlOf('---\nexperimental:\n  cacheTtl: 5m  # note\n---\n')).toBe('5m')
    expect(cacheTtlOf('---\nexperimental:\n  cacheTtl: 1h\n---\n')).toBe('1h')
    expect(cacheTtlOf('---\nname: x\n---\n')).toBe('1h')
    expect(cacheTtlOf(null)).toBe('1h')
    expect(close(tierRates(r, '5m').write_1h, 5 / M)).toBe(true)
    expect(tierRates(r, '1h')).toEqual(r)
  })

  test('the partner dropped from $.agent.list() after a turn end counts as completed', async () => {
    expect(partnerStatus(undefined, t0)).toBe('completed')
    expect(partnerStatus('running', t0)).toBe('running')
    expect(partnerStatus(undefined, 0)).toBeUndefined()
  })
})

describe('ratesFrom on the real prices --json documents', () => {
  test('--model: the price row under model_key; a flat prompt_tier', async () => {
    const opus = { LONG_CONTEXT_TOKENS: 200000, model: 'claude-opus-5-5', model_key: 'opus-5-5',
      price: { input: 4.0, output: 20.0, read: 0.2, write_1h: 8.0, write_5m: 5.0 } }
    expect(close(ratesFrom(opus, 'claude-opus-5-5', 0)!.write_1h, 8 / 1e6)).toBe(true)
    const haiku = { model: 'claude-haiku-5-5', model_key: 'haiku-5-5', price: { input: 0.1, output: 0.5, read: 0.01, write_1h: 0.2,
      write_5m: 0.125, prompt_tier: { over: 100000, input: 0.5, output: 2.5, read: 0.05, write_1h: 1.0, write_5m: 0.625 } } }
    expect(close(ratesFrom(JSON.stringify(haiku), 'claude-haiku-5-5', 0)!.read, 0.01 / 1e6)).toBe(true)
    expect(close(ratesFrom(haiku, 'claude-haiku-5-5', 150000)!.read, 0.05 / 1e6)).toBe(true)
  })
  test('the whole table under prices', async () => {
    const doc = { LONG_CONTEXT_TOKENS: 200000, prices: { 'opus-5-5': { input: 4.0, output: 20.0, read: 0.2, write_1h: 8.0, write_5m: 5.0 } } }
    expect(close(ratesFrom(doc, 'claude-opus-5-5[1m]', 0)!.read, 0.2 / 1e6)).toBe(true)
  })
})
