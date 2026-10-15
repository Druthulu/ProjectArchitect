import { describe, expect, test } from 'claude-code/testing'

import { argLine, depthOf, handbackOf, isPing, isPingStep, planningAfter } from './stream'

describe('step helpers', () => {
  test('the arg line and the depth', async () => {
    expect(argLine({ command: 'git status', description: 'x' })).toBe('git status')
    expect(argLine({ file_path: 'x'.repeat(1000) }).length).toBe(80)
    expect(argLine({ n: 1 })).toBe('{"n":1}')
    const list = [{ id: 'P' }, { id: 'C', parentId: 'P' }, { id: 'G', parentId: 'C' }]
    expect([depthOf('P', list), depthOf('C', list), depthOf('G', list), depthOf('X', list)]).toEqual([1, 2, 3, 1])
  })

  test('a ping step and the hand-back', async () => {
    expect(isPing('.', 0)).toBe(true)
    expect(isPing(' .\n', 0)).toBe(true)
    expect(isPing('.', 1)).toBe(false)
    expect(isPing('ok.', 0)).toBe(false)
    const hb = (message: unknown) => ({ name: 'SubagentHandback', input: { message } })
    expect(handbackOf([{ name: 'Read', input: {} }, hb('Done.')])).toBe('Done.')
    expect(handbackOf([{ name: 'Read', input: {} }])).toBe(null)
    expect(handbackOf([hb(3)])).toBe('')
    expect(isPingStep('', [hb('.')])).toBe(true)
    expect(isPingStep('.', [])).toBe(true)
    expect(isPingStep('', [hb('Done.')])).toBe(false)
    expect(isPingStep('', [hb('.'), { name: 'Read', input: {} }])).toBe(false)
  })
})

describe('planning (3.23 T8)', () => {
  test('planning: on at a pa-planning Skill call, off at plan, else unchanged', async () => {
    expect(planningAfter(false, 'Skill', { skill: 'pa-planning' })).toBe(true)
    expect(planningAfter(false, 'Skill', { skill: 'pa:pa-planning' })).toBe(true)
    expect(planningAfter(false, 'Skill', { skill: 'pa-review' })).toBe(false)
    expect(planningAfter(true, 'Read', { file_path: 'x' })).toBe(true)
    expect(planningAfter(true, 'mcp__pa-watch__plan', { kind: 'phase' })).toBe(false)
    expect(planningAfter(false, 'Bash', { command: 'pa-planning' })).toBe(false)
  })
})
