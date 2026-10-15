import { describe, expect, test } from 'claude-code/testing'

import { auditorBrief, curatorBrief, EXPERT_RETURN, expertBrief, fixBrief, type PlanTask, readsPath } from './brief'

// As `plan_edit.py show --task T1 --json` printed it for templates/PHASE_PLAN.template.md (3.23 T4), plus reads.
const T1: PlanTask = {
  id: 'T1', status: 'next', agent: 'expert-opus55', coder: 'opus55', effort: 'medium', review: false, wait_for: null,
  title: 'the parser', done_when: '…', verify: '<cmd>', reads: [], files: ['a.py', 'b.py'], deps: [], est_ctx: '80k',
}

describe('expert brief', () => {
  test('the fields in order, the plan by commands', async () => {
    const b = expertBrief({ ...T1, reads: ['T2', 'tasks/T3.md'] })
    expect(b.split('\n').map(l => l.split(':')[0])).toEqual(['TASK', 'PLAN', 'HOW', 'LOGS TO READ', 'CODER', 'EFFORT', 'DONE WHEN', 'RETURN'])
    expect(b).toContain('TASK: T1 the parser\n')
    expect(b).toContain('PLAN: PY tools/plan_edit.py show --section Context; show --section Interfaces; show --section Cookbook; show --section Research; show --task T1')
    expect(b).toContain('HOW: a.py, b.py\n')
    expect(b).toContain('LOGS TO READ: phase-ends/current/tasks/T2.md, phase-ends/current/tasks/T3.md\n')
    expect(b).toContain('DONE WHEN: … · verify: <cmd>\n')
    expect(b).not.toContain('PHASE_PLAN.md')
  })

  test('respawn progress, note, and the trial knobs', async () => {
    const b = expertBrief(T1, { progress: 'phase-ends/current/TASK_PROGRESS.md', note: 'keep b.py', trialHandoff: true, trialQuestion: 'drop b.py' })
    expect(b).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md\n')
    expect(b).toContain(
      "NOTE: keep b.py · hand back `handoff` after your first coder's report, with TASK_PROGRESS.md written · hand back `question` asking whether to drop b.py\n",
    )
  })

  test('3.26 T2: the RETURN line asks for the plain first word, no STATUS', async () => {
    const ret = expertBrief(T1).split('\n').find(l => l.startsWith('RETURN: ')) ?? ''
    expect(ret).toBe('RETURN: your report: one plain first word, done, question, blocked, review or handoff, then free text')
    expect(ret).not.toContain('STATUS')
  })

  test('absent fields', async () => {
    const b = expertBrief({ ...T1, coder: null, effort: null, files: [], verify: null, title: null })
    expect(b).toContain('TASK: T1\n')
    expect(b).toContain('HOW: none named\nLOGS TO READ: none\nCODER: none\nEFFORT: medium\nDONE WHEN: …\n')
    expect(readsPath('phase-ends/phase-2.2/tasks/T1.md')).toBe('phase-ends/phase-2.2/tasks/T1.md')
  })

  test('3.24 T7b: the expert slice after the fields; absent or blank: none', async () => {
    const b = expertBrief(T1, { card: '## Paths <!-- roles: expert -->\n- src/\n' })
    expect(b).toContain(`RETURN: ${EXPERT_RETURN}\nCARD (PY tools/card.py slice expert; the card's expert sections, already read):\n## Paths <!-- roles: expert -->\n- src/`)
    expect(b.endsWith('- src/')).toBe(true)
    expect(expertBrief(T1, { card: ' \n' })).toBe(expertBrief(T1))
  })

  test('a review task adds the review NOTE', async () => {
    expect(expertBrief({ ...T1, review: true })).toContain(
      "NOTE: this task ends at a developer review: fill the summary's Review: block and hand back `review`\n")
  })
})

describe('other briefs', () => {
  test('curator, auditor, fix', async () => {
    expect(curatorBrief('curate: gen 2 start', 'Z:/p/.claude-state/memory')).toBe('CURATE: curate: gen 2 start\nMEMORY: Z:/p/.claude-state/memory\nRETURN: recap')
    // 3.24 T8b: the first-run line carries MEMORY itself; no second field.
    expect(curatorBrief('curate: migration 3.2 -> gen 3.2 · FROM: vanilla · MEMORY: M', 'M')).toBe('CURATE: curate: migration 3.2 -> gen 3.2 · FROM: vanilla · MEMORY: M\nRETURN: recap')
    expect(auditorBrief({ flag: '', phase: '3.22', tables: 'phase-ends/PhaseEnd-3.22.md', previous: '' })).toBe(
      'AUDIT: generation start\nPHASE: 3.22\nTABLES: phase-ends/PhaseEnd-3.22.md\nPREVIOUS: —\nRETURN: contract',
    )
    expect(fixBrief('cache_read')).toContain('TASK: FIX\nROW: cache_read\nDONE WHEN: doctor 0 FAIL and the statusline shows the value\n')
  })
})
