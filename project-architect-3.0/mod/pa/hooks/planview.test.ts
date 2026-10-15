// Pure tests of planview.ts: the decision line from ExitPlanMode's raw results (3.21 shapes), the fallback's and the
// ask's lines, the queue items, the deferral, the plan file's path.
import { describe, expect, test } from 'claude-code/testing'

import {
  agentsHold, answerLine, decisionLine, dialogKey, dropAgentDialogs, fallbackLine, holdingAgent, learnedFile, planFilePath, planLogLines, presentHold, presentOf, slugArgv, slugFile,
} from './planview'

const FILE = 'C:\\Users\\you\\.claude\\plans\\wild-petting-raccoon.md'
const REFUSED = "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file)."

describe('decisionLine', () => {
  test('approved: the plan comes back (3.21 6d7709f6), or the plain approval text', async () => {
    expect(decisionLine({ result: { plan: '# Plan\n', isAgent: false, filePath: FILE }, text: 'User has approved your plan. You can now start coding.' }))
      .toEqual({ line: 'Plan decision: approved', known: true })
    expect(decisionLine({ result: { plan: null, isAgent: false, filePath: FILE }, text: 'User has approved exiting plan mode. You can now proceed.' }))
      .toEqual({ line: 'Plan decision: approved', known: true })
  })

  test('a refusal with feedback: changes asked, in their words', async () => {
    expect(decisionLine({ deny: `${REFUSED} To tell you how to proceed, the user said:\nSplit step 2.\n` }))
      .toEqual({ line: 'Plan decision: changes asked: Split step 2.', known: true })
    expect(decisionLine({ result: 'x', text: `${REFUSED} To tell you how to proceed, the user said: Fewer steps.`, isError: true }))
      .toEqual({ line: 'Plan decision: changes asked: Fewer steps.', known: true })
  })

  test('a plain refusal or a deny: rejected', async () => {
    expect(decisionLine({ deny: `${REFUSED} STOP what you are doing and wait for the user to tell you how to proceed.` }))
      .toEqual({ line: 'Plan decision: rejected', known: true })
    expect(decisionLine({ deny: 'denied by a hook' })).toEqual({ line: 'Plan decision: rejected', known: true })
    expect(decisionLine({ result: { plan: null }, text: 'Error', isError: true })).toEqual({ line: 'Plan decision: rejected', known: true })
  })

  test('an unrecognized shape: rejected, not known', async () => {
    expect(decisionLine(null)).toEqual({ line: 'Plan decision: rejected', known: false })
    expect(decisionLine({ result: { message: 'Entered plan mode.' }, text: 'Entered plan mode.' })).toEqual({ line: 'Plan decision: rejected', known: false })
  })
})

describe('answer lines', () => {
  test('the fallback picker', async () => {
    expect(fallbackLine('Approve')).toBe('Plan decision: approved')
    expect(fallbackLine('Reject')).toBe('Plan decision: rejected')
    expect(fallbackLine(null)).toBe('Plan decision: rejected')
    expect(fallbackLine('Ask for changes')).toBe('Plan decision: changes asked: (no words given)')
    expect(fallbackLine(' Drop step 3. ')).toBe('Plan decision: changes asked: Drop step 3.')
  })

  test('an ask', async () => {
    expect(answerLine('B')).toBe('Answer: B')
    expect(answerLine(null)).toBe('Answer: dismissed')
  })
})

describe('queue items and deferral', () => {
  test('plan and ask requests; a bad input is null', async () => {
    expect(presentOf({ id: '1-1-plan', kind: 'plan', caller: 'P1', input: { path: '.run/plans/a.md', title: 'A' }, at: 5 }))
      .toEqual({ id: '1-1-plan', kind: 'plan', caller: 'P1', at: 5, path: '.run/plans/a.md', title: 'A', question: '', options: [], planKind: 'adhoc', rules: [], candidates: [] })
    expect(presentOf({ id: '1-1-plan', kind: 'plan', caller: 'P1', input: { path: 'p.md', kind: 'phase', rules: ['M13: r', 4], candidates: ['- Tool candidate: x'] }, at: 5 }))
      .toMatchObject({ planKind: 'phase', rules: ['M13: r'], candidates: ['- Tool candidate: x'] })
    expect(presentOf({ id: '1-1-plan', kind: 'plan', caller: 'P1', input: { path: 'a.md' }, at: 5 })?.title).toBe('a.md')
    expect(presentOf({ id: '1-2-ask', kind: 'ask', caller: 'P1', input: { question: 'Q?', options: ['a', 'b', 3, 'c', 'd', 'e'] }, at: 5 })?.options)
      .toEqual(['a', 'b', 'c', 'd'])
    expect(presentOf({ id: '1-1-plan', kind: 'plan', caller: 'P1', input: {}, at: 5 })).toBe(null)
    expect(presentOf({ id: '1-2-ask', kind: 'ask', caller: 'P1', input: null, at: 5 })).toBe(null)
  })

  test('deferral: a busy agent, then a draft', async () => {
    expect(presentHold({ busy: true, draft: '' })).toBe('agent busy')
    expect(presentHold({ busy: false, draft: 'typing' })).toBe('draft')
    expect(presentHold({ busy: false, draft: '  ' })).toBe('')
  })

  // 3.23 T13d.c7: an ask waits only for its caller running or pending; a plan for any busy agent. 3.24 T8: `waiting` no
  // longer holds an ask (L18); an open permission dialog does.
  test('agents hold: an ask for its caller or an open dialog, a plan for any busy agent', async () => {
    const ask = { kind: 'ask', caller: 'P1' }
    const plan = { kind: 'plan', caller: 'P1' }
    expect(agentsHold(ask, [{ id: 'P1', status: 'completed' }, { id: 'T1', status: 'running' }, { id: 'E1', status: 'pending' }])).toBe(false)
    expect(agentsHold(ask, [{ id: 'P1', status: 'running' }])).toBe(true)
    expect(agentsHold(ask, [{ id: 'P1', status: 'pending' }])).toBe(true)
    expect(agentsHold(ask, [{ id: 'E1', status: 'waiting' }])).toBe(false)
    expect(agentsHold(plan, [{ id: 'E1', status: 'waiting' }])).toBe(true)
    expect(agentsHold(plan, [{ id: 'E1', status: 'running' }])).toBe(true)
    expect(agentsHold(plan, [{ id: 'E1', status: 'completed' }])).toBe(false)
  })

  // 3.24 T8 (L18): the picker no longer waits on Claude's thinker.
  test('Claude waiting on its running thinker does not hold its own ask', async () => {
    const ask = { kind: 'ask', caller: 'C1' }
    const agents = [{ id: 'C1', type: 'pa-claude', status: 'waiting' }, { id: 'K1', type: 'pa-thinker', status: 'running' }]
    expect(holdingAgent(ask, agents)).toBe(null)
  })

  test('an open dialog holds, a closed one releases', async () => {
    const ask = { kind: 'ask', caller: 'C1' }
    const key = dialogKey('E1', 'Bash', { command: 'ls' })
    const open = [key]
    expect(holdingAgent(ask, [], open)).toEqual({ id: key, status: 'dialog' })
    expect(agentsHold({ kind: 'plan', caller: 'C1' }, [], open)).toBe(true)
    const closed = open.filter(k => k !== dialogKey('E1', 'Bash', { command: 'ls' }))
    expect(agentsHold(ask, [], closed)).toBe(false)
    expect(dropAgentDialogs([key, dialogKey(undefined, 'Edit', {})], 'E1')).toEqual([dialogKey(undefined, 'Edit', {})])
  })

  test("a loop ask holds during Claude's running turn, not while Claude waits", async () => {
    const loop = { kind: 'ask', caller: 'loop' }
    expect(holdingAgent(loop, [{ id: 'C1', type: 'pa-claude', status: 'running' }])).toEqual({ id: 'C1', status: 'running' })
    expect(holdingAgent(loop, [{ id: 'C1', type: 'pa-claude', status: 'waiting' }])).toBe(null)
    expect(holdingAgent({ kind: 'ask', caller: 'E1' }, [{ id: 'C1', type: 'pa-claude', status: 'running' }])).toBe(null)
  })

  test('the plan file and the learned main plan file', async () => {
    expect(planFilePath('/r', '.run/plans/a.md')).toBe('/r/.run/plans/a.md')
    expect(planFilePath('Z:\\repo\\', './a.md')).toBe('Z:\\repo/a.md')
    expect(planFilePath('/r', 'C:\\x\\a.md')).toBe('C:\\x\\a.md')
    expect(planFilePath('/r', '/abs/a.md')).toBe('/abs/a.md')
    expect(learnedFile({ result: { plan: null, isAgent: false, filePath: FILE }, text: 'User has approved exiting plan mode.' })).toBe(FILE)
    expect(learnedFile({ deny: 'no' })).toBe('')
  })

  test('the transcript slug run, its plan file, the fallback transcript lines', async () => {
    expect(slugArgv('', '/t.jsonl')).toBe(null)
    expect(slugArgv('py', '')).toBe(null)
    expect(slugArgv('py', '/t.jsonl')?.slice(0, 2)).toEqual(['py', '-c'])
    expect(slugArgv('py', '/t.jsonl')?.[3]).toBe('/t.jsonl')
    expect(slugFile({ exitCode: 0, stdout: `${FILE}\r\n` })).toBe(FILE)
    expect(slugFile({ exitCode: 0, stdout: '\n' })).toBe('')
    expect(slugFile({ exitCode: 1, stdout: FILE })).toBe('')
    expect(slugFile(null)).toBe('')
    expect(planLogLines('A', '# A\r\n\n1. x\n\n')).toEqual(['▣ plan: A', '# A', '', '1. x'])
  })
})
