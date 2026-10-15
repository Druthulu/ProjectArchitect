// 3.24 T17b: the ladder's pure helpers (ladder.ts).
import { describe, expect, test } from 'claude-code/testing'

import { hintOf, keepRow, ladderArgv, rowKey, rowOf, spawnFields, spawnModel, stepEffort } from './ladder'
import type { LadderRow } from './ladder'

const ROW: LadderRow = { agent: 'pa-planner', managed: true, model: 'claude-opus-5-5', effort: 'max', tier: 'max20', source: 'credentials' }
const SPAWN = { subagentType: 'pa-planner', fork: false }

describe('the ladder (3.24 T17b)', () => {
  test('the resolver argv, none without an interpreter or a ledger', async () => {
    expect(ladderArgv('py', 'L/pa_ledger.py', 'pa-planner', '/r')).toEqual(['py', 'L/pa_ledger.py', 'ladder-row', '--agent', 'pa-planner', '--project', '/r', '--json'])
    expect(ladderArgv('', 'L', 'a', '/r')).toBe(null)
    expect(ladderArgv('py', '', 'a', '/r')).toBe(null)
  })
  test('3.25 T6b: the `[effort <level>]` hint: its level, the description without it, the row key, the argv', async () => {
    expect(hintOf('T1 add function [effort high]')).toEqual({ level: 'high', desc: 'T1 add function' })
    expect(hintOf('Review the plan [effort deep]')).toEqual({ level: 'deep', desc: 'Review the plan' })
    expect(hintOf('T1 add function')).toEqual({ level: '', desc: 'T1 add function' })
    expect(hintOf('T1 [effort huge]')).toEqual({ level: '', desc: 'T1 [effort huge]' })
    expect([rowKey('pa-expert', 'high'), rowKey('pa-expert', '')]).toEqual(['pa-expert@high', 'pa-expert'])
    expect(ladderArgv('py', 'L', 'pa-expert', '/r', 'high')).toEqual(['py', 'L', 'ladder-row', '--agent', 'pa-expert', '--effort', 'high', '--project', '/r', '--json'])
    expect(rowOf(JSON.stringify({ ...ROW, agent: 'pa-expert@high' }), 'pa-expert@high')?.agent).toBe('pa-expert@high')
  })
  test('a row is the last line, for the agent asked, well formed; an unknown effort is none', async () => {
    expect(rowOf(`warning\n${JSON.stringify(ROW)}\n`, 'pa-planner')).toEqual(ROW)
    expect(rowOf(JSON.stringify(ROW), 'pa-coder')).toBe(null)
    expect(rowOf(JSON.stringify({ ...ROW, managed: 'yes' }), 'pa-planner')).toBe(null)
    expect(rowOf('not json', 'pa-planner')).toBe(null)
    expect(rowOf(JSON.stringify({ ...ROW, effort: 'huge' }), 'pa-planner')?.effort).toBe('')
  })
  test('a row of another tier drops the rows kept', async () => {
    const coder = { ...ROW, agent: 'pa-coder' }
    expect(Object.keys(keepRow({ 'pa-coder': coder }, ROW))).toEqual(['pa-coder', 'pa-planner'])
    expect(keepRow({ 'pa-coder': coder }, { ...ROW, tier: 'pro' })).toEqual({ 'pa-planner': { ...ROW, tier: 'pro' } })
  })
  test('the model is set only for a managed row, never for Claude, a fork or a workflow agent', async () => {
    expect(spawnModel(ROW, SPAWN)).toBe('claude-opus-5-5')
    expect(spawnModel(null, SPAWN)).toBe(null)
    expect(spawnModel({ ...ROW, managed: false }, SPAWN)).toBe(null)
    expect(spawnModel(ROW, { ...SPAWN, fork: true })).toBe(null)
    expect(spawnModel(ROW, { ...SPAWN, workflow: { runId: 'wf_1', agentIndex: 1 } })).toBe(null)
    expect(spawnModel({ ...ROW, agent: 'pa-claude' }, { ...SPAWN, subagentType: 'pa-claude' })).toBe(null)
  })
  test('the effort is set only for a managed row, never for Claude or an unknown type', async () => {
    expect(stepEffort(ROW, 'pa-planner')).toBe('max')
    expect(stepEffort(undefined, 'pa-planner')).toBe(null)
    expect(stepEffort({ ...ROW, managed: false }, 'pa-planner')).toBe(null)
    expect(stepEffort({ ...ROW, effort: '' }, 'pa-planner')).toBe(null)
    expect(stepEffort(ROW, 'pa-claude')).toBe(null)
    expect(stepEffort(ROW, '')).toBe(null)
  })
  test('the spawn entry fields', async () => {
    const f = spawnFields({ ts: 't', session: 'S', agentId: 'P1', type: 'pa-planner', model: 'claude-opus-5-5', effort: 'max', row: ROW })
    expect(f).toEqual({ ts: 't', session: 'S', agentId: 'P1', agentType: 'pa-planner', model: 'claude-opus-5-5', effort: 'max', managed: 'yes', row_model: 'claude-opus-5-5', row_effort: 'max', tier: 'max20', source: 'credentials' })
    expect(spawnFields({ ts: 't', session: 'S', agentId: 'P1', type: 'x', model: 'm', effort: '', row: null })['managed']).toBe('unknown')
  })
})
