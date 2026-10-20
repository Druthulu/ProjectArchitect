import { describe, expect, test } from 'claude-code/testing'

import {
  addReply, ATTACH_NOTICE, applyReply, classify, clearTurn, dueMarkers, HANDBACK_WAIT_MS, handbackChild, handbackReport, hideAssistant,
  hideSpinner, hideUser, isSeedName, localAnswer, markerText, newestPartner, newPartner, nextMarker, outboxHeld, parentOf, parseMarker, partnerReport, registerTurn,
  replyKey, stepFactsOf, STUB, takeHandback, takeReply, usageCtx, isPointer, lateReport, markerFacts, workingIds,
  bareMessage, seedEntries, noReportHandback, loopPending, yourTurnStep, YOUR_TURN_MS, bangOf, bangText,
} from './route'
import type { Entry } from './journal'
import { defaultState } from './state'
import { defaultLoop } from './loop'

describe('classify', () => {
  test('every origin kind', async () => {
    const T = 'hello'
    expect(classify({ kind: 'composer' }, T, false)).toBe('developer')
    // the router's marker is its own under any origin (submitted asUser since the T11 recheck)
    expect(classify({ kind: 'composer' }, '‹pa reply 3›', false)).toBe('own')
    expect(classify({ kind: 'plugin', name: 'pa', asUser: true } as never, '‹pa reply 3›', false)).toBe('own')
    expect(classify({ kind: 'composer' }, 'what is ‹pa reply 3› for?', false)).toBe('developer')
    expect(classify({ kind: 'bridge' }, T, false)).toBe('developer')
    expect(classify({ kind: 'sdk' }, T, true)).toBe('developer')
    expect(classify({ kind: 'sdk' }, T, false)).toBe('other')
    expect(classify({ kind: 'plugin', name: 'pa' }, markerText(1), false)).toBe('own')
    expect(classify({ kind: 'plugin', name: 'other' }, T, false)).toBe('other')
    expect(classify({ kind: 'task-notification' }, '<task-id>c1</task-id>', false)).toBe('handback')
    // 3.23 T13: an agent-message without "The report follows:" is a note (a SendMessage to main), else a hand-back.
    expect(classify({ kind: 'peer' }, '<agent-message from="c1">done</agent-message>', false)).toBe('note')
    expect(classify({ kind: 'peer-send-message' }, '<agent-message from="c1">x', false)).toBe('note')
    expect(classify({ kind: 'peer' }, '<agent-message from="c1">\nThe report follows:\n  STATUS: done\n</agent-message>', false)).toBe('handback')
    expect(classify({ kind: 'peer' }, 'no id here', false)).toBe('other')
    for (const k of ['scheduled-trigger', 'projects-relay', 'channel', 'coordinator', 'observer', 'observer-activity',
      'auto-continuation', 'unclassified', 'slack-ping']) {
      expect(classify({ kind: k }, T, true)).toBe('other')
    }
  })

  test('hand-back child ids', async () => {
    expect(handbackChild('<task-notification><task-id> a7 </task-id></task-notification>')).toBe('a7')
    expect(handbackChild('  <agent-message from="b2">x</agent-message>')).toBe('b2')
    expect(handbackChild('<task-id></task-id>')).toBe(null)
  })
})

describe('turns', () => {
  test('marker, attachment and stub kinds; answers carry text', async () => {
    expect(parseMarker(`x ${markerText(12)} y`)).toBe(12)
    let r = registerTurn(undefined, 'T1', markerText(3))
    expect(r.kinds['T1']).toEqual({ kind: 'marker', n: 3 })
    r = registerTurn({ kinds: r.kinds, attach: ['look at this'] }, 'T2', 'look at this')
    expect(r.kinds['T2']).toEqual({ kind: 'attach' })
    expect(r.attach).toEqual([])
    r = registerTurn(r, 'T3', 'look at this')
    expect(r.kinds['T3']).toEqual({ kind: 'stub' })
    expect(clearTurn(r, 'T1').kinds['T1']).toBeUndefined()
    expect(localAnswer({ kind: 'marker', n: 1 }, 'reply')).toBe('reply')
    expect(localAnswer({ kind: 'marker', n: 1 }, null)).toBe(STUB)
    expect(localAnswer({ kind: 'attach' }, null)).toBe(ATTACH_NOTICE)
    expect(localAnswer({ kind: 'stub' }, null)).toBe(STUB)
  })

  test('the registry stays bounded', async () => {
    let r = registerTurn(undefined, 'T0', 'x')
    for (let i = 1; i < 100; i += 1) {
      r = registerTurn(r, `T${String(i)}`, 'x')
    }
    expect(Object.keys(r.kinds).length).toBe(64)
    expect(r.kinds['T99']).toEqual({ kind: 'stub' })
  })
})

describe('state transitions', () => {
  test('a reply records its marker, the usage, and lifts the hold', async () => {
    const s0 = { ...defaultState('S', 1), partner: { ...newPartner('P', 'opus', 1), pingInFlight: true }, markers: { '2': 'a' } }
    expect(nextMarker(s0)).toBe(3)
    const s = applyReply(s0, 3, '/r/3.md', 'P:k', 9, usageCtx({ input_tokens: 1, cache_read_input_tokens: 10, cache_creation_input_tokens: 100 }))
    expect(s.markers['3']).toBe('/r/3.md')
    // T8d: the marker waits for the hand-back; queued once HANDBACK_WAIT_MS passed without one
    expect(s.pendingMarkers).toEqual([])
    expect(dueMarkers(s, 9 + HANDBACK_WAIT_MS - 1)).toBe(s)
    expect(dueMarkers(s, 9 + HANDBACK_WAIT_MS).pendingMarkers).toEqual([3])
    expect([s.partner?.lastTurnAt, s.partner?.ctx, s.partner?.pingInFlight]).toEqual([9, 111, false])
    expect(outboxHeld(s0)).toBe(true)
    expect(outboxHeld(s)).toBe(false)
  })

  test('a hand-back is marked and queued; the parent comes from the record, else the list', async () => {
    const s = takeHandback(defaultState('S', 1), 'C', 'done', 5)
    expect(s.forwarded).toEqual({ C: 5 })
    expect(s.outbox).toEqual([{ kind: 'forward', text: 'Hand-back from your background agent C:\ndone', child: 'C', at: 5 }])
    expect(parentOf({ C: { parent: 'X', type: '', startedAt: 0, status: '' } }, 'C', [])).toBe('X')
    expect(parentOf({}, 'C', [{ id: 'C', parentId: 'P' }])).toBe('P')
    expect(parentOf({}, 'C', [{ id: 'C' }])).toBe(null)
  })
})

// T8d: the partner's reply rides its hand-back; the marker is the fallback.
const FRAME = '<agent-message from="P">\n[Subagent hand-back] The text below is the final report of a subagent this session '
  + 'delegated to. … The report follows:\n'
const handback = (report: string): string => `${FRAME}${report.split('\n').map(l => `  ${l}`).join('\n')}\n  \n</agent-message>`

describe('hand-back replies (T8d)', () => {
  test('handbackReport: an agent-message de-indented, a task-notification result, neither', async () => {
    const report = 'Line one.\n\n  indented code\n</agent-message> lookalike\nLast.'
    expect(handbackReport(handback(report))).toBe(report)
    const tn = '<task-notification>\n<task-id>P</task-id>\n<status>completed</status>\n<result>A <b>fact</b>.\n</result>\n</task-notification>'
    expect(handbackReport(tn)).toBe('A <b>fact</b>.')
    // 3.24 T13r: Claude Code escapes `&`, `<`, `>` in a result; undone once (a literal `&amp;` comes back as written).
    const esc = '<task-notification>\n<task-id>P</task-id>\n<result>Run `x 2&gt;&amp;1`, keep &lt;y&gt; "q", a literal &amp;amp;.</result>\n</task-notification>'
    expect(handbackReport(esc)).toBe('Run `x 2>&1`, keep <y> "q", a literal &amp;.')
    expect(handbackReport(handback('Raw 2>&1 &amp; <x>.'))).toBe('Raw 2>&1 &amp; <x>.')
    expect(handbackReport('<agent-message from="P">hi</agent-message>')).toBe(null)
    expect(handbackReport('<task-notification><task-id>P</task-id></task-notification>')).toBe(null)
    expect(handbackReport('plain')).toBe(null)
    const pointer = '<task-notification><task-id>P</task-id><result>This agent\'s report was delivered to you as a message from "P"</result></task-notification>'
    expect(handbackReport(pointer)).not.toBe(null)
    expect(partnerReport(pointer)).toBe(null)
    expect(replyKey('P', 'a  b\n')).toBe(replyKey('P', 'a b'))
    expect(replyKey('P', 'a b')).not.toBe(replyKey('Q', 'a b'))
  })

  test('the turn of a shown hand-back answers with the report; the key matches its answer', async () => {
    const report = 'A fun fact.'
    const text = handback(report)
    const key = replyKey('P', report)
    const r = registerTurn(addReply(undefined, key, 'not the text', report), 'T1', `wrapped: ${text}`)
    expect(r.kinds['T1']).toEqual({ kind: 'reply', text: report })
    expect(r.replies).toEqual([])
    expect(localAnswer({ kind: 'reply', text: report }, null)).toBe(report)
    expect(registerTurn(r, 'T2', text).kinds['T2']).toEqual({ kind: 'stub' })
  })

  test('hand-back first: shown, the answer then queues no marker; after a fallback marker: the stub', async () => {
    const s0 = { ...defaultState('S', 1), partner: newPartner('P', 'opus', 1) }
    const t = takeReply(s0, 'P:k', 10)
    expect(t.show).toBe(true)
    const s1 = applyReply(t.state, 1, '/r/1.md', 'P:k', 11, null)
    expect(s1.markers).toEqual({})
    expect(dueMarkers(s1, 11 + HANDBACK_WAIT_MS).pendingMarkers).toEqual([])
    // a second delivery of the same report: the stub
    expect(takeReply(s1, 'P:k', 12).show).toBe(false)
    // answer first, hand-back within the wait: shown, no marker
    const w = applyReply(s0, 1, '/r/1.md', 'P:j', 20, null)
    const t2 = takeReply(w, 'P:j', 21)
    expect(t2.show).toBe(true)
    expect(dueMarkers(t2.state, 20 + HANDBACK_WAIT_MS).pendingMarkers).toEqual([])
    // answer first, no hand-back in the wait: the marker; a late hand-back gets the stub
    const m = dueMarkers(w, 20 + HANDBACK_WAIT_MS)
    expect(m.pendingMarkers).toEqual([1])
    expect(takeReply(m, 'P:j', 20 + HANDBACK_WAIT_MS + 1).show).toBe(false)
  })
})

describe('render predicates', () => {
  test('marker and hand-back rows, the stub, the waiting line', async () => {
    expect(hideUser({ kind: 'plugin', name: 'pa' })).toBe(true)
    expect(hideUser({ kind: 'task-notification' })).toBe(true)
    expect(hideUser({ kind: 'peer' })).toBe(true)
    expect(hideUser({ kind: 'composer' })).toBe(false)
    expect(hideUser({ kind: 'plugin', name: 'pa-watch' })).toBe(false)
    // 3.22 T11: the row's origin may not say plugin `pa`; its text does
    const framed = 'The pa plugin sent a message:\n‹pa reply 1›\n\nThis is how Claude Code surfaces a prompt a plugin submits'
    expect(hideUser({ kind: 'unclassified' }, framed)).toBe(true)
    expect(hideUser(undefined, framed)).toBe(true)
    expect(hideUser({ kind: 'composer' }, '<task-notification><task-id>a1</task-id></task-notification>')).toBe(true)
    expect(hideUser({ kind: 'composer' }, '<agent-message from="a1">\n[Subagent hand-back] …')).toBe(true)
    expect(hideUser({ kind: 'composer' }, 'please explain ‹pa reply› markers')).toBe(false)
    expect(hideUser({ kind: 'composer' }, 'Hello Claude')).toBe(false)
    expect(hideAssistant(` ${STUB}\n`)).toBe(true)
    expect(hideAssistant('a reply')).toBe(false)
    expect(hideSpinner('Waiting on 1 background agent')).toBe(true)
    expect(hideSpinner(null)).toBe(false)
  })
})

// 3.22 T8g: the partner's type is `pa-claude` (`claude` at T8f); its answers carry the watcher's last-step model and effort.
describe('pa-claude', () => {
  test('the newest live pa-claude, the seed names, the step facts', async () => {
    const list = [{ id: 'A', type: 'pa-claude', status: 'running' }, { id: 'B', type: 'partner', status: 'running' },
      { id: 'C', type: 'claude-code-guide', status: 'running' }, { id: 'D', type: 'claude', status: 'running' }]
    expect(newestPartner(list, '')).toBe('A')
    expect(newestPartner(list, 'A')).toBe(undefined)
    expect(isSeedName('090010-002r-claude-answer.md')).toBe(true)
    expect(isSeedName('090010-002r-partner-answer.md')).toBe(true)
    expect(isSeedName('090010-002w-claude-thinking.md')).toBe(false)
    expect(stepFactsOf('{"model":"claude-opus-5-5","effort":"high","ctx":5,"at":1}')).toEqual({ model: 'claude-opus-5-5', effort: 'high', ctx: 5 })
    expect(stepFactsOf(null)).toEqual({ model: '', effort: '', ctx: 0 })
    expect(stepFactsOf('{"effort":3}')).toEqual({ model: '', effort: '', ctx: 0 })
  })
})

// 3.23 T8: the second-reply carrier and the seed's dedupe of a prefixed message.
describe('seed dedupe (3.23 T8)', () => {
  test('the message less its ‹pa› blocks; the seed drops its journal entry', async () => {
    const talk = '‹pa› the queue is held while you talk (T1 running; next: T2). Call release({hold: "talk"}) when done.'
    const running = '‹pa› running now: T1 Add plus\n- expert e1 (opus)\n  note: reading\n  steps: Read a.py'
    expect(bareMessage(`${talk}\n\n${running}\n\nWhat runs now?`)).toBe('What runs now?')
    expect(bareMessage('What runs now?')).toBe('What runs now?')
    expect(bareMessage(`${running}\n\nfirst\n\n‹pa› quoted later`)).toBe('first\n\n‹pa› quoted later')
    const e = (path: string, ts: string, body: string): Entry => ({ path, meta: { ts }, body } as Entry)
    const mine = e('.claude-state/journal/2026-10-08/101500-003r-dev-msg.md', '2026-10-08T10:15:00Z', 'What runs now?')
    const older = e('.claude-state/journal/2026-10-08/101000-001r-dev-msg.md', '2026-10-08T10:10:00Z', 'Hello')
    expect(seedEntries([older, mine], `${talk}\n\n${running}\n\nWhat runs now?`)).toEqual([older])
    expect(seedEntries([older, mine], 'Something else')).toEqual([mine, older])
  })
})

describe('3.24 T17h: a hand-back with no report', () => {
  test('`.` or blank is refused unless the agent answers a ping; a report never is', async () => {
    expect(noReportHandback({ message: '.' }, false)).toBe(true)
    expect(noReportHandback({ message: ' .\n' }, false)).toBe(true)
    expect(noReportHandback({ message: '' }, false)).toBe(true)
    expect(noReportHandback({}, false)).toBe(true)
    expect(noReportHandback({ message: '.' }, true)).toBe(false)
    expect(noReportHandback({ message: '1. The closer will block.' }, false)).toBe(false)
  })
})

describe('3.24 T17g: a late report, a marker\'s model and effort, the agents still at work', () => {
  test('a pointer result; the last text after a spent hand-back is the report', async () => {
    expect(isPointer('This agent\'s report was delivered to you as a message from "E1" (its SubagentHandback call).')).toBe(true)
    expect(isPointer('STATUS: done')).toBe(false)
    expect(lateReport([{ role: 'user', text: 'go' }, { role: 'assistant', text: 'STATUS: done\nSUMMARY: x', toolUses: [] }])).toBe('STATUS: done\nSUMMARY: x')
    expect(lateReport([{ role: 'assistant', text: '', toolUses: [{ name: 'SubagentHandback' }] }])).toBe(null)
    expect(lateReport([{ role: 'assistant', text: 'Handing back.', toolUses: [{ name: 'SubagentHandback' }] }, { role: 'user', text: '' }])).toBe(null)
    expect(lateReport({ deny: 'no' })).toBe(null)
    expect(lateReport([])).toBe(null)
  })

  test('a marker\'s journal entry gives the answer\'s model and effort', async () => {
    expect(markerFacts('---\nts: 2026-10-12T06:26:57Z\nmodel: claude-opus-5-5[1m]\neffort: medium\n---\n\nThe answer.')).toEqual({ model: 'claude-opus-5-5[1m]', effort: 'medium' })
    expect(markerFacts('no entry')).toEqual({ model: '', effort: '' })
  })

  test('an agent waiting on its own Agent call still works: no your-turn toast while it plans', async () => {
    const list = [{ id: 'P', status: 'idle' }, { id: 'L', status: 'waiting' }, { id: 'K', status: 'running' }, { id: 'R', status: 'completed' }]
    expect(workingIds(list)).toEqual(['L', 'K'])
    expect(workingIds(list, 'K')).toEqual(['L'])
  })

  test('3.24 T17j: a hand-back the loop has yet to act on holds the your-turn toast; its reason is named', async () => {
    const task = { id: 'T2', title: 't', agent: 'expert-opus55', coder: '', effort: '', attempt: 1, kind: 'task', agentId: 'E1', startSha: '', spawnedAt: 0, lastCompleteAt: 0, contract: null }
    const l = defaultLoop()
    expect(loopPending(l)).toBe('')
    expect(loopPending({ ...l, state: 'running', task })).toBe('')
    expect(loopPending({ ...l, state: 'running', task: { ...task, pendingContract: 'STATUS: review' } })).toBe('loop:handback')
    expect(loopPending({ ...l, state: 'routing', task: { ...task, contract: 'STATUS: review' } })).toBe('loop:routing')
    expect(loopPending({ ...l, state: 'routing', task: { ...task, contract: 'STATUS: review', routed: 'review' } as typeof task })).toBe('')
    expect(loopPending({ ...l, state: 'closing', chainStep: 'lint' })).toBe('loop:closing')
    expect(loopPending({ ...l, state: 'closing', chainStep: 'lint', holds: ['question'] })).toBe('')
    const y = { turnId: 'A1', at: 0, body: 'b', known: [], fired: false }
    expect(yourTurnStep(y, YOUR_TURN_MS + 1, [], 'P', '', 'loop:routing')).toEqual({ do: 'wait', why: 'loop:routing' })
    expect(yourTurnStep(y, YOUR_TURN_MS + 1, [], 'P', 'stop', 'loop:closing')).toEqual({ do: 'drop', why: 'stop', agents: [] })
    expect(yourTurnStep(y, YOUR_TURN_MS + 1, [], 'P', '')).toEqual({ do: 'fire' })
  })
})

describe('a `!` command at the prompt (3.29.8)', () => {
  test('bangOf reads the input row and the output row; bangText gives Claude the command and the tail', () => {
    expect(bangOf('<bash-input>bash /tmp/run_cleanup.sh</bash-input>')).toEqual({ input: 'bash /tmp/run_cleanup.sh', output: null })
    expect(bangOf('<bash-stdout>done</bash-stdout><bash-stderr></bash-stderr>')).toEqual({ input: null, output: 'done' })
    expect(bangOf('<bash-stdout></bash-stdout><bash-stderr>rm: no such file</bash-stderr>')).toEqual({ input: null, output: 'rm: no such file' })
    expect(bangOf('hello')).toEqual({ input: null, output: null })
    const t = bangText('bash x.sh', 'a\nb')
    expect(t).toContain('`bash x.sh`')
    expect(t).toContain('a\nb')
    expect(t).toContain('tell them now whether it worked')
    const long = Array.from({ length: 50 }, (_, i) => String(i)).join('\n')
    expect(bangText('c', long)).toContain('(the last 40 lines)')
    expect(bangText('c', long)).not.toContain('\n9\n')
  })
})

