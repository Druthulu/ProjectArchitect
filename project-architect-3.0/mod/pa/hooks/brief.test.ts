import { describe, expect, test } from 'claude-code/testing'

import { auditorBrief, coderLine, curatorBrief, EXPERT_RETURN, expertBrief, fixBrief, intakeBrief, type PlanTask, readsPath } from './brief'

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
    expect(b).toContain('HOW: none named\nLOGS TO READ: none\nCODER: opus55\nEFFORT: medium\nDONE WHEN: …\n')
    expect(readsPath('phase-ends/phase-2.2/tasks/T1.md')).toBe('phase-ends/phase-2.2/tasks/T1.md')
  })

  test('3.24 T7b: the expert slice after the fields; absent or blank: none', async () => {
    const b = expertBrief(T1, { card: '## Paths <!-- roles: expert -->\n- src/\n' })
    expect(b).toContain(`RETURN: ${EXPERT_RETURN}\nCARD (PY tools/card.py slice expert; the card's expert sections, already read):\n## Paths <!-- roles: expert -->\n- src/`)
    expect(b.endsWith('- src/')).toBe(true)
    expect(expertBrief(T1, { card: ' \n' })).toBe(expertBrief(T1))
  })

  test('3.31 T4: OUTCOMES IN FORCE after the CARD block; absent or blank: none', async () => {
    const b = expertBrief(T1, { card: '## Paths\n- src/\n', outcomes: '- R2 decision: one task per context\n  binds work: yes\n' })
    expect(b.endsWith('- src/\nOUTCOMES IN FORCE (PY tools/records.py inforce --binds-work; binding):\n- R2 decision: one task per context\n  binds work: yes')).toBe(true)
    expect(expertBrief(T1, { outcomes: '- R2 decision: x' })).toBe(`${expertBrief(T1)}\nOUTCOMES IN FORCE (PY tools/records.py inforce --binds-work; binding):\n- R2 decision: x`)
    expect(expertBrief(T1, { card: '## Paths\n', outcomes: ' \n' })).toBe(expertBrief(T1, { card: '## Paths\n' }))
  })

  test('3.3 T7: the CODER line: the task coder, else pa.json external, else opus55; external ones say how they run', async () => {
    const EXT = { coder: 'openrouter/deepseek', lanes: { openrouter: 2 }, max_coders: 3, escalate: ['opus55', 'fable'] }
    const run = 'external: brief it in a file and run it with tools/pa_ext.py'
    expect(coderLine('local/qwen', null)).toBe(`local/qwen (${run}; up to 4 at once; on blocked or no report: in-house)`)
    expect(coderLine(null, EXT)).toBe(`openrouter/deepseek (${run}; up to 2 at once; on blocked or no report: opus55 then fable)`)
    expect(coderLine('local/qwen', EXT)).toBe(`local/qwen (${run}; up to 3 at once; on blocked or no report: opus55 then fable)`)
    expect(coderLine(null, { ...EXT, use: false })).toBe('opus55')
    expect(coderLine(null, null)).toBe('opus55')
    expect(coderLine(null, {})).toBe('opus55')
    expect(coderLine('in-house', EXT)).toBe('opus55')
    expect(coderLine('none', EXT)).toBe('none')
    expect(coderLine('opus55', EXT)).toBe('opus55')
    expect(expertBrief({ ...T1, coder: null }, { external: EXT })).toContain(`\nCODER: openrouter/deepseek (${run}; up to 2 at once;`)
  })

  test('a review task adds the review NOTE', async () => {
    expect(expertBrief({ ...T1, review: true })).toContain(
      "NOTE: this task ends at a developer review: fill the summary's Review: block and hand back `review`\n")
  })
})

describe('other briefs', () => {
  test('curator, auditor, fix', async () => {
    const o = { memoryDir: 'M', from: '', page: 'docs/PRE_PA3_STATE.md', sources: ['a.md', 'b.md'] }
    expect(intakeBrief({ ...o, steps: ['living docs'] })).toBe('CURATE: intake · MEMORY: M · PAGE: docs/PRE_PA3_STATE.md\nSTEPS: living docs\nRETURN: recap')
    const full = intakeBrief({ ...o, from: 'pa2', steps: ['memories', 'living docs', 'state page'] })
    expect(full).toContain('CURATE: intake · MEMORY: M · PAGE: docs/PRE_PA3_STATE.md · FROM: pa2\nSTEPS: memories, living docs, state page\nWRITE: Where does this project stand?')
    expect(full).toContain('\nFOR: every fresh Claude and the planner\nSCOPE: the living docs, git log, the roadmap files, then a.md, b.md. In a checkpoint file')
    expect(full).toContain('\nCAP: one page, 7,000 characters at most\nRETURN: recap')
    expect(curatorBrief('curate: gen 2 start', 'Z:/p/.claude-state/memory')).toBe('CURATE: curate: gen 2 start\nMEMORY: Z:/p/.claude-state/memory\nRETURN: recap')
    // 3.24 T8b: the first-run line carries MEMORY itself; no second field.
    expect(curatorBrief('curate: migration 3.2 -> gen 3.2 · FROM: vanilla · MEMORY: M', 'M')).toBe('CURATE: curate: migration 3.2 -> gen 3.2 · FROM: vanilla · MEMORY: M\nRETURN: recap')
    expect(auditorBrief({ flag: '', phase: '3.22', tables: 'phase-ends/PhaseEnd-3.22.md', previous: '' })).toBe(
      'AUDIT: generation start\nPHASE: 3.22\nTABLES: phase-ends/PhaseEnd-3.22.md\nPREVIOUS: —\nRETURN: contract',
    )
    expect(fixBrief('cache_read')).toContain('TASK: FIX\nROW: cache_read\nDONE WHEN: doctor 0 FAIL and the statusline shows the value\n')
  })
})
