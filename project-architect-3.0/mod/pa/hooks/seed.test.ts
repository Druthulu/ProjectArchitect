import { describe, expect, test } from 'claude-code/testing'

import type { LoopState } from '../types'
import { type Entry, renderEntry, splitDay } from './journal'
import { buildSeed, CARD_CAP_CHARS, cardSection, localStamp, loopSection, MEMORY_CAP_CHARS, memorySection, openQuestion } from './seed'

const D = '.claude-state/journal/2026-10-07'
const T0 = Date.UTC(2026, 9, 7, 10, 0, 0) // entry n is n minutes later (3.22 T8g: real ISO ts)

function e(n: number, who: 'dev' | 'claude' | 'partner' | 'thinker', body: string, session?: string, at = T0 + n * 60_000): Entry {
  const kind = who === 'dev' ? 'msg' : who === 'claude' || who === 'partner' ? 'answer' : 'thinking'
  const t = String(100000 + n)
  const meta = { ts: new Date(at).toISOString(), ...(session === undefined ? {} : { session }) }
  return { path: `${D}/${t}-${String(n % 1000).padStart(3, '0')}r-${who}-${kind}.md`, meta, body }
}

// The local date and HH:MM the way the seed must compute them: the JS Date local getters, so any machine zone passes.
function loc(at: number): { date: string; hm: string } {
  const d = new Date(at)
  const p = (n: number): string => String(n).padStart(2, '0')
  return { date: `${String(d.getFullYear())}-${p(d.getMonth() + 1)}-${p(d.getDate())}`, hm: `${p(d.getHours())}:${p(d.getMinutes())}` }
}

// Pairs 1..n: dev "Q<i> line" then partner "A<i> line", each with a second line of `size` chars.
function convo(n: number, size = 20): Entry[] {
  const out: Entry[] = []
  for (let i = 1; i <= n; i++) {
    out.push(e(2 * i, 'dev', `Q${i} line\n${'q'.repeat(size)}`))
    out.push(e(2 * i + 1, 'partner', `A${i} line\n${'a'.repeat(size)}`))
  }
  return out
}

describe('seed', () => {
  test('starts with JOURNAL SEED and has the four sections in order', async () => {
    const s = buildSeed(convo(2), { children: [{ who: 'T6', agentId: 'ag_9', status: 'running', summary: 'pure modules' }], planPaths: ['phase-ends/current/PHASE_PLAN.md'] })
    expect(s.startsWith('JOURNAL SEED\n')).toBe(true)
    const at = ['## Recent conversation', '## Running work', '## Plan', '## Open question'].map((h) => s.indexOf(h))
    expect(at.every((x) => x > 0)).toBe(true)
    expect([...at].sort((a, b) => a - b)).toEqual(at)
    expect(s).toContain('- T6 (ag_9) running: pure modules')
    expect(s).toContain('- phase-ends/current/PHASE_PLAN.md')
    expect(buildSeed([]).includes('(none)')).toBe(true)
  })

  test('pairs are newest first, msg before its answer; thinking entries are left out', async () => {
    const s = buildSeed([...convo(3), e(50, 'thinker', 'secret reasoning')])
    expect(s.indexOf('Q3')).toBeLessThan(s.indexOf('A3'))
    expect(s.indexOf('A3')).toBeLessThan(s.indexOf('Q2'))
    expect(s.indexOf('A2')).toBeLessThan(s.indexOf('Q1'))
    expect(s).not.toContain('secret reasoning')
  })

  test('within budget everything is whole; over it older pairs go to one line, then the oldest drop', async () => {
    const entries = convo(30, 400) // 30 pairs of ~800 chars
    const all = buildSeed(entries, { budgetTokens: 40_000 })
    expect(all).not.toContain('dropped')
    expect(all).toContain(`${loc(T0 + 2 * 60_000).hm} dev:\nQ1 line\n`)
    expect(all).toContain(`${loc(T0 + 3 * 60_000).hm} Claude:\nA1 line\n`)

    const tight = buildSeed(entries, { budgetTokens: 1_000 }) // 4,000 chars
    expect(tight.length / 4).toBeLessThan(1_100)
    expect(tight).toContain(`Q30 line\n${'q'.repeat(400)}`) // the newest pair whole
    expect(tight).toContain(`A30 line\n${'a'.repeat(400)}`)
    expect(tight).toMatch(/^- \d\d:\d\d dev: Q\d+ line \/ Claude: A\d+ line$/m) // older ones as one line
    const sum = tight.match(/^- (\d\d:\d\d) dev: Q(\d+) line \/ Claude: A\d+ line$/m)
    expect(sum?.[1]).toBe(loc(T0 + 2 * Number(sum?.[2]) * 60_000).hm) // the summary's HH:MM is its dev msg's
    expect(tight).not.toContain('###')
    expect(tight).toMatch(/\(\d+ older exchanges dropped; see the journal\)/)
    expect(tight).not.toContain('Q1 line') // the oldest dropped first
  })

  test('localStamp: the local getters; empty for a bad ts', async () => {
    const at = Date.UTC(2026, 9, 7, 23, 59, 30)
    expect(localStamp(new Date(at).toISOString())).toEqual(loc(at))
    expect(localStamp(undefined)).toEqual({ date: '', hm: '' })
    expect(localStamp('2026-10-07T100002')).toEqual({ date: '', hm: '' })
  })

  test('one date line where the local day changes', async () => {
    const day = 86_400_000
    const s = buildSeed([e(1, 'dev', 'Q1', 'S', T0), e(2, 'partner', 'A1', 'S', T0 + 60_000),
      e(3, 'dev', 'Q2', 'S', T0 + 120_000), e(4, 'partner', 'A2', 'S', T0 + 180_000),
      e(5, 'dev', 'Q3', 'S', T0 + day), e(6, 'partner', 'A3', 'S', T0 + day + 60_000)])
    const d1 = loc(T0).date
    const d2 = loc(T0 + day).date
    expect(s.split(`## ${d2}\n`).length).toBe(2)
    expect(s.split(`## ${d1}\n`).length).toBe(2)
    expect(s).toContain(`## ${d2}\n\n${loc(T0 + day).hm} dev:\nQ3`)
    expect(s.indexOf('A3')).toBeLessThan(s.indexOf(`## ${d1}`))
    expect(s.indexOf(`## ${d1}`)).toBeLessThan(s.indexOf('Q2'))
  })

  test('a session boundary line after the earlier session; none after the current one', async () => {
    const old = 'aaaaaaaa-0000-4000-8000-000000000105'
    const cur = 'bbbbbbbb-0000-4000-8000-00000000011a'
    const entries = [e(1, 'dev', 'Q1', old), e(2, 'partner', 'A1', old), e(3, 'dev', 'Q2', cur), e(4, 'partner', 'A2', cur)]
    const s = buildSeed(entries)
    const ended = '— session aaaaaaaa ended; its agents are gone —'
    expect(s.split(ended).length).toBe(2)
    expect(s).not.toContain('session bbbbbbbb ended')
    expect(s.indexOf('A2')).toBeLessThan(s.indexOf(ended)) // newest first: the current session above the line
    expect(s.indexOf(ended)).toBeLessThan(s.indexOf('Q1'))
    expect(s.split(`## ${loc(T0).date}\n`).length).toBe(2) // same day: one date line across the boundary
    expect(buildSeed(entries, { session: cur }).split(ended).length).toBe(2)
    // seeded for a session newer than any entry: every journaled session has ended
    const fresh = buildSeed(entries, { session: 'cccccccc-0' })
    expect(fresh).toContain('— session bbbbbbbb ended; its agents are gone —')
    expect(fresh).toContain(ended)
  })

  test('the newest pair is never cut, even over budget', async () => {
    const big = convo(2, 10_000)
    const s = buildSeed(big, { budgetTokens: 100 })
    expect(s).toContain(`Q2 line\n${'q'.repeat(10_000)}`)
    expect(s).toContain(`A2 line\n${'a'.repeat(10_000)}`)
    expect(s).not.toContain('Q1 line')
    expect(s).toContain('(1 older exchanges dropped; see the journal)')
  })

  test('the open question: the last answer ends in a question and no dev msg followed', async () => {
    const asked = [...convo(1), e(10, 'dev', 'next?'), e(11, 'partner', 'Done.\n\nShould I also run the gate?')]
    expect(openQuestion(asked)).toBe('Should I also run the gate?')
    expect(buildSeed(asked)).toMatch(/## Open question\n\nShould I also run the gate\?\n$/)
    expect(openQuestion([...asked, e(12, 'dev', 'yes')])).toBe(null)
    expect(openQuestion([...convo(1), e(11, 'partner', 'Done.')])).toBe(null)
    expect(openQuestion([...asked, e(12, 'thinker', 'hm')])).toBe('Should I also run the gate?')
    expect(openQuestion([...convo(1), e(10, 'dev', 'next?'), e(11, 'claude', 'Done.\n\nShall I?')])).toBe('Shall I?') // 3.22 T8f
    expect(buildSeed(convo(1))).toMatch(/## Open question\n\n\(none\)\n$/)
  })
})

describe('seed 3.23 T7', () => {
  test('a rolled day and a live day: the same order as per-entry files', async () => {
    const day = 86_400_000
    const live = [e(5, 'dev', 'Q2', 'S', T0 + day), e(6, 'claude', 'A2', 'S', T0 + day + 60_000)]
    const old = [e(1, 'dev', 'Q1', 'S', T0), e(2, 'claude', 'A1', 'S', T0 + 60_000), e(3, 'thinker', 'hm', 'S', T0 + 90_000)]
    const text = `# Journal 2026-10-07 (3 entries, rolled by tools/journal_rollup.py)\n` +
      old.map((x) => `## ${(x.path.split('/').at(-1) ?? '').replace(/\.md$/, '')}\n${renderEntry(x.meta, x.body)}`).join('')
    const rolled = splitDay('2026-10-07', text)
    expect(rolled.every((x) => x.path.startsWith('.claude-state/journal/2026-10-07.md#'))).toBe(true)
    expect(buildSeed([...rolled, ...live])).toBe(buildSeed([...old, ...live]))
    expect(buildSeed([...rolled, ...live])).toContain('Q1')
  })

  const L = {
    v: 1, ownerSid: 'S1', heartbeatAt: 0, state: 'boundary', holds: ['question', 'talk'], queuedEdits: [], chainStep: '', dialog: false,
    waitFor: null, resumeAt: 0, lastDevAt: 0, lastAnswerAt: 0, lastAnswerAsked: false,
    task: { id: 'T4', title: 'wire it', agent: 'expert-opus55', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task', agentId: 'E1',
      startSha: 'a', spawnedAt: 1, lastCompleteAt: 0, contract: null },
  } as LoopState

  test('LOOP: phase, task, holds and the open stop; MEMORY: the index lines; both before the open question', async () => {
    const s = buildSeed(convo(1), { loop: { state: L, phase: '3.23' }, memory: '# Memory\n\n- [A](a.md) — one\n- [B](b.md) — two\n' })
    expect(s).toContain('## LOOP\n\n- phase: 3.23; state: boundary\n- task: T4 wire it (attempt 1)\n- holds: question, talk\n- open stop: question T4\n\n')
    expect(s).toContain('## MEMORY\n\n# Memory\n- [A](a.md) — one\n- [B](b.md) — two\n\n')
    const at = ['## Running work', '## LOOP', '## Plan', '## MEMORY', '## Open question'].map((h) => s.indexOf(h))
    expect([...at].sort((a, b) => a - b)).toEqual(at)
    expect(loopSection({ state: { ...L, holds: [], task: null }, phase: '' })).toBe(
      '## LOOP\n\n- phase: (unknown); state: boundary\n- task: (none)\n- holds: (none)\n- open stop: (none)\n\n')
  })

  test('absent sources omit the sections; MEMORY is capped near 1.5k tokens', async () => {
    const s = buildSeed(convo(1))
    expect(s).not.toContain('## LOOP')
    expect(s).not.toContain('## MEMORY')
    const big = Array.from({ length: 400 }, (_, i) => `- [m${String(i)}](m${String(i)}.md) — ${'x'.repeat(40)}`).join('\n')
    const m = memorySection(big)
    expect(m.length).toBeLessThan(MEMORY_CAP_CHARS + 200)
    expect(m).toContain('- [m0](m0.md)')
    expect(m).toMatch(/\(\d+ more lines; see \.claude-state\/memory\/MEMORY\.md\)\n\n$/)
    expect(memorySection('\n\n')).toBe('')
  })

  test('3.24 T7b: CARD (card.py slice claude) before MEMORY, capped; absent or empty: none', async () => {
    const card = '# HOW_WE_WORK\n\n## Developer <!-- roles: claude -->\n- plain words\n'
    const s = buildSeed(convo(1), { card, memory: '- [A](a.md) — one\n' })
    expect(s).toContain('## CARD\n\n# HOW_WE_WORK\n## Developer <!-- roles: claude -->\n- plain words\n\n## MEMORY\n')
    const at = ['## Plan', '## CARD', '## MEMORY', '## Open question'].map((h) => s.indexOf(h))
    expect([...at].sort((a, b) => a - b)).toEqual(at)
    expect(buildSeed(convo(1))).not.toContain('## CARD')
    expect(cardSection(' \n')).toBe('')
    const c = cardSection(Array.from({ length: 300 }, (_, i) => `- line ${String(i)} ${'y'.repeat(40)}`).join('\n'))
    expect(c.length).toBeLessThan(CARD_CAP_CHARS + 200)
    expect(c).toMatch(/\(\d+ more lines; see HOW_WE_WORK\.md \(PY tools\/card\.py slice claude\)\)\n\n$/)
  })

  test('3.24 T7b-2c: a pending first run: its line under ## Plan; absent: none', async () => {
    const s = buildSeed(convo(1), { planPaths: ['GENERATION_PLAN.md'], firstRun: 'First run line.' })
    expect(s).toContain('## Plan\n\n- GENERATION_PLAN.md\n\nFirst run line.\n\n')
    expect(buildSeed(convo(1))).toContain('## Plan\n\n(none)\n\n')
  })
})
