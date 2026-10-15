import { describe, expect, test } from 'claude-code/testing'

import type { RunningAgent } from '../types'
import { DIGEST_CAP, idleNow, recoveryDigest, runningNow, taskAgents } from './digest'

const at = 1_000
const expert: RunningAgent = {
  id: 'a1', role: 'expert', model: 'Opus 5.5', effort: 'medium', ctx: 40_000, at,
  note: 'Brief one coder for loop.ts and its tests.\nThen contract.ts.\nThen brief.ts.', steps: ['Read plan', 'Agent coder', 'Bash git log', 'Read T3.md'],
}
const coder: RunningAgent = { id: 'c1', role: 'coder', model: '', effort: '', ctx: 0, at, steps: ['Edit loop.ts'] }

describe('running now', () => {
  test('task, expert first, note ≤ 2 lines, no tool steps (3.23 T13d.c2)', async () => {
    expect(runningNow({ id: 'T4', title: 'pure modules' }, [coder, expert])).toBe([
      '‹pa› running now: T4 pure modules',
      '- expert a1 (Opus 5.5)',
      '  note: Brief one coder for loop.ts and its tests.',
      '  note: Then contract.ts.',
      '- coder c1',
    ].join('\n'))
  })

  test('no agents yet; capped near 800 chars', async () => {
    expect(runningNow({ id: 'T4', title: 'x' }, [])).toBe('‹pa› running now: T4 x')
    const many = Array.from({ length: 12 }, (_, i): RunningAgent => ({ ...expert, id: `c${String(i)}`, role: 'coder', steps: ['y'.repeat(300)] }))
    const text = runningNow({ id: 'T4', title: 'x' }, many)
    expect(text.length).toBe(DIGEST_CAP)
    expect(text.endsWith('…')).toBe(true)
  })

  test('no task runs: the last done, the next, the holds (3.24 T17p)', async () => {
    expect(idleNow({ id: 'T1', hm: '18:37' }, 'T2', ['talk'])).toBe('‹pa› running now: no task · T1 done at 18:37 · next: T2 · waiting on: talk')
    expect(idleNow(null, '', [])).toBe('‹pa› running now: no task · next: none listed')
  })
})

describe('the running task\'s agents (T5c)', () => {
  test('the expert, then its descendants by parentId; others and bad files left out', async () => {
    const f = (o: Record<string, unknown>): string => JSON.stringify({ model: 'm', effort: '', ctx: 0, at: 0, ...o })
    const got = taskAgents('E1', [['C2', f({ parentId: 'C1' })], ['C1', f({ parentId: 'E1' })], ['X', f({ parentId: 'P' })], ['B', '{'], ['E1', f({ note: 'n' })]])
    expect(got.map(a => [a.id, a.role])).toEqual([['E1', 'expert'], ['C1', 'coder'], ['C2', 'coder']])
    expect(got[0]?.note).toBe('n')
    expect(taskAgents('E9', []).map(a => [a.id, a.role, a.model])).toEqual([['E9', 'expert', '']])
  })
})

describe('recovery digest (T5d1)', () => {
  const entry = (ts: string, body: string): string => `---\nts: ${ts}\nsession: S0\nagentId: E1\n---\n${body}\n`
  test('git state, then the task agents\' notes and thinking since the start, oldest first', async () => {
    const text = recoveryDigest({
      id: 'T1', attempt: 1, startSha: 'abc', since: Date.parse('2026-10-07T10:00:00Z'), status: ' M calc/add.py', log: 'd8504d0 T1.c1 add',
      entries: [
        ['2026-10-07/100502-003w-coder-note.md', entry('2026-10-07T10:05:02Z', 'Editing add.py.')],
        ['2026-10-07/100101-001w-T1-note.md', entry('2026-10-07T10:01:01Z', 'Brief one coder.')],
        ['2026-10-07/095900-000w-T1-note.md', entry('2026-10-07T09:59:00Z', 'Before the start.')],
        ['2026-10-07/100200-002w-claude-thinking.md', entry('2026-10-07T10:02:00Z', 'Claude thinks.')],
        ['2026-10-07/100300-501r-mod-hold.md', entry('2026-10-07T10:03:00Z', 'A hold.')],
      ],
    })
    expect(text.startsWith('# TASK_PROGRESS: recovery digest for T1 (attempt 1)\n')).toBe(true)
    expect(text).toContain('## git status --short\n```\n M calc/add.py\n```')
    expect(text).toContain('## git log --oneline abc..HEAD\n```\nd8504d0 T1.c1 add\n```')
    expect(text.indexOf('Brief one coder.')).toBeLessThan(text.indexOf('Editing add.py.'))
    for (const gone of ['Before the start.', 'Claude thinks.', 'A hold.']) {
      expect(text).not.toContain(gone)
    }
  })
  test('nothing to say: (none)', async () => {
    const text = recoveryDigest({ id: 'T1', attempt: 2, startSha: '', since: 0, status: '', log: '', entries: [] })
    expect(text).toContain('## git status --short\n```\n(none)\n```')
    expect(text.trimEnd().endsWith('(none)')).toBe(true)
  })
})
