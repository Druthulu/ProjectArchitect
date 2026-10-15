import { describe, expect, test } from 'claude-code/testing'

import { decideRoute, expertStatus, freshStartUsd, partnerStatus, pingDecision, pingUsd, ratesFrom, type Rates } from './policy'

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

  test('the TTL margin boundary: < 3,300,000 ms', async () => {
    expect(decideRoute({ ...p, lastTurnAt: now - 3_299_999 }, now)).toBe('resume')
    expect(decideRoute({ ...p, lastTurnAt: now - 3_300_000 }, now)).toBe('fresh')
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

  test('with a child out it always resumes', async () => {
    const stale = { lastTurnAt: 0, ctx: 900_000, topicFlag: true, modelSwitchAt: now }
    expect(decideRoute(stale, now)).toBe('fresh')
    expect(decideRoute(stale, now, { childOut: true })).toBe('resume')
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
  test('freshStartUsd = (seed + prefix)·write_1h', async () => {
    expect(close(freshStartUsd(40_000, 20_000, r), (60_000 * 8) / M)).toBe(true)
  })
})

describe('pingDecision', () => {
  const r: Rates = ratesFrom(PRICES, 'claude-opus-5-5', 0)!
  const t0 = 1_000_000_000
  const due = t0 + 55 * 60_000
  const a = { status: 'idle', lastRequestAt: t0, ctx: 100_000 }
  const dev = { seedTokens: 40_000, prefixTokens: 20_000 } // fresh = $0.48; one ping = $0.0218

  test('only idle or completed agents', async () => {
    expect(pingDecision({ ...a, status: 'running' }, due, r, dev)).toEqual({ ping: false, at: due, why: 'status running' })
    expect(pingDecision({ ...a, status: 'completed' }, due, r, dev).ping).toBe(true)
    expect(pingDecision(a, due, r, dev).ping).toBe(true)
  })

  test('due at lastRequestAt + 55 min', async () => {
    expect(pingDecision(a, due - 1, r, dev)).toEqual({ ping: false, at: due, why: 'not due' })
    expect(pingDecision(a, due, r, dev)).toEqual({ ping: true, at: due, why: 'rent' })
  })

  test('child out: ping while now - waitStart < 24 h, whatever the cost', async () => {
    const opts = { childOut: true, waitStart: t0, spentUsd: 1000 }
    expect(pingDecision(a, t0 + 24 * 3_600_000 - 1, r, opts)).toEqual({ ping: true, at: due, why: 'child out' })
    expect(pingDecision(a, t0 + 24 * 3_600_000, r, opts)).toEqual({ ping: false, at: due, why: 'child out 24h' })
  })

  test('waiting on the developer: ping while spent + ping <= fresh start', async () => {
    const fresh = freshStartUsd(40_000, 20_000, r)
    const one = pingUsd(a.ctx, r)
    expect(pingDecision(a, due, r, { ...dev, spentUsd: fresh - one })).toEqual({ ping: true, at: due, why: 'rent' })
    expect(pingDecision(a, due, r, { ...dev, spentUsd: fresh - one + 1e-6 })).toEqual({ ping: false, at: due, why: 'buy' })
  })

  test('skip300k: only while waiting on the developer; a child out stays warm at any size', async () => {
    expect(pingDecision({ ...a, ctx: 299_999 }, due, r, { childOut: true, waitStart: t0 }).ping).toBe(true)
    expect(pingDecision({ ...a, ctx: 300_000 }, due, r, { childOut: true, waitStart: t0 })).toEqual({ ping: true, at: due, why: 'child out' })
    expect(pingDecision({ ...a, ctx: 300_000 }, due, r, dev)).toEqual({ ping: false, at: due, why: 'skip300k' })
  })

  test('the loop expert dropped from $.agent.list() while it waits counts as completed (T13b.c4)', async () => {
    expect(expertStatus(undefined, 'question', t0, false)).toBe('completed')
    expect(expertStatus(undefined, 'child', t0, false)).toBe('completed')
    expect(expertStatus('running', 'question', t0, false)).toBe('running')
    expect(expertStatus(undefined, '', t0, false)).toBeUndefined()
    expect(expertStatus(undefined, 'question', 0, false)).toBeUndefined()
    expect(expertStatus(undefined, 'question', t0, true)).toBeUndefined()
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
