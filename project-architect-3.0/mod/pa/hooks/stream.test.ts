import { describe, expect, test } from 'claude-code/testing'

import { modelLabel, routerLine, tokens } from './stream'

describe('router stream lines', () => {
  test('start, answer, plan', async () => {
    expect(routerLine('start', { model: 'Opus 5.5' })).toBe('▸ Claude started (Opus 5.5)')
    expect(routerLine('start', { model: 'claude-opus-5-5[1m]' })).toBe('▸ Claude started (Opus 5.5)')
    expect(routerLine('answer', { ctx: 12_345, usd: 0.04 })).toBe('◂ Claude answered · 12.3k ctx · $0.04')
    expect(routerLine('answer')).toBe('◂ Claude answered')
    expect(routerLine('plan', { title: '3.22 the partner' })).toBe('▣ plan presented: 3.22 the partner')
    expect(routerLine('start', { who: 'T6', model: 'claude-fable-5-1' })).toBe('▸ T6 started (Fable 5.1)')
  })

  test('indent two spaces per level', async () => {
    expect(routerLine('start', { depth: 2, model: 'Opus 5.5' })).toBe('    ▸ Claude started (Opus 5.5)')
    expect(routerLine('answer', { depth: 1 })).toBe('  ◂ Claude answered')
  })

  test('clip to 160 chars on one line', async () => {
    const line = routerLine('plan', { depth: 3, title: 'x'.repeat(300) })
    expect(line.length).toBe(160)
    expect(line.endsWith('…')).toBe(true)
    expect(routerLine('plan', { title: 'a\nb' })).toBe('▣ plan presented: a b')
  })

  test('helpers', async () => {
    expect(tokens(950)).toBe('950')
    expect(tokens(1_250_000)).toBe('1.3M')
    expect(modelLabel('claude-sonnet-5-5')).toBe('Sonnet 5.5')
    expect(modelLabel('mystery')).toBe('mystery')
  })
})
