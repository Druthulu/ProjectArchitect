import { describe, expect, test } from 'claude-code/testing'

import type { LoopState, LoopTask } from '../types'
import {
  agentFor, effortFor, effortHint, bandLine, closeNotice, plannerIdOf, defaultLoop, keepSummary, summariesOf, editIsDecision, FORWARD_TTL_MS, NO_CONTRACT_QUIET_MS, OWNER_STALE_MS, ownsLoop, parseLoop, popHold, pushHold, queuedTo, releaseHold, routeStatus,
  runEnded, RUN_QUIET_MS, serializeLoop, silenceReleases, takesOver, taskPosIn, taskPosOf, topHold, waitSpec, windowResumes, planCounts, projectName, sessionTitle,
  quietReply, nextPhaseOf, recalcSessions, withPhaseSid, archiveOf, curateQuestion, firstRunLine,
  markerOf, modFilesKey, externalReminder, setupNotice, runRunning, runToReport, RUN_REPORT_MS, RUN_STALE_MS, updateDoneNotice, updateOutcome, UPDATE_LOG, UPGRADE_MD,
  extHealthOf, extHealthNotice, generalizableCount, bankQuestion, bankTask, bankNotice, startCheckOffline, startCheckState,
} from './loop'

describe('the one-time cookbook banking (3.30)', () => {
  test('marks counted as task_log.py counts them: a list item or a labelled line; not other kinds, not empty', () => {
    expect(generalizableCount('- generalizable: a\n  generalizable: b\nGotchas: generalizable: c\n- harness: d\n- generalizable:\n')).toBe(3)
    expect(generalizableCount('')).toBe(0)
  })
  test('the picker, the task after the running one, and the notice', () => {
    expect(bankQuestion(1, 0)).toContain('mark 1 lesson for the cookbook (0 in the current phase)')
    expect(bankTask('T11')).toBe('add-task --title "Bank the waiting cookbook lessons (one time, PA3 3.30)" --done-when ' +
      '"every lesson a task summary marks generalizable is banked in the cookbook or ruled out, by the project cookbook ' +
      'guidelines" --coder none --after T11')
    expect(bankTask('')).not.toContain('--after')
    expect(bankNotice(true, 'T11')).toContain(`command \`${bankTask('T11')}\``)
    expect(bankNotice(false)).toContain('put off the one-time cookbook banking')
  })
})

describe('external coder health (3.30)', () => {
  test('EXT-HEALTH lines anywhere in a report, prefix dropped, any case; none is []', () => {
    expect(extHealthOf('done: ok\n  EXT-HEALTH: project: Bash(objdump:*)\nother\r\next-health: model a/b: x\nEXT-HEALTH:  \n')).toEqual(
      ['project: Bash(objdump:*)', 'model a/b: x'])
    expect(extHealthOf('done: nothing to change')).toEqual([])
  })
  test('the notice names the task and lists each proposal', () => {
    expect(extHealthNotice('T11', ['project: a', 'model c/d: b']).split('\n').slice(1)).toEqual(['- project: a', '- model c/d: b'])
    expect(extHealthNotice('T11', ['x'])).toContain('‹pa› T11\'s expert proposes changes for later external coders.')
  })
})

const TASK: LoopTask = {
  id: 'T4', title: 'pure modules', agent: 'expert-opus55', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task',
  agentId: 'a1', startSha: 'abc1234', spawnedAt: 1_000, lastCompleteAt: 0, contract: null,
}
const with_ = (p: Partial<LoopState>): LoopState => ({ ...defaultLoop(), ...p })

describe('loop.json', () => {
  test('round trip keeps unknown fields', async () => {
    const s = with_({ ownerSid: 'S1', heartbeatAt: 5, state: 'running', holds: ['review', 'talk'], task: TASK,
      waitFor: { task: 'T5', file: '.run/ready', literal: 'READY', since: 7 } })
    const back = parseLoop(serializeLoop({ ...s, future: { x: 1 } } as LoopState))
    expect(back).toEqual({ ...s, future: { x: 1 } } as LoopState)
    expect(serializeLoop(s).endsWith('\n')).toBe(true)
  })

  test('tolerant: absent, broken, bad fields', async () => {
    expect(parseLoop(null)).toEqual(defaultLoop())
    expect(parseLoop('{')).toEqual(defaultLoop())
    expect(parseLoop('[1]')).toEqual(defaultLoop())
    const s = parseLoop('{"state":"flying","holds":["talk","nap",3],"task":{"title":"no id"},"resumeAt":"x","queuedEdits":[{"command":"PY tools/plan_edit.py show"},{}]}')
    expect([s.state, s.holds, s.task, s.resumeAt]).toEqual(['off', ['talk'], null, 0])
    expect(s.queuedEdits).toEqual([{ command: 'PY tools/plan_edit.py show', why: '', at: 0, caller: '' }])
    expect(parseLoop('{"task":{"id":"T2"}}').task?.attempt).toBe(1)
  })
})

describe('holds', () => {
  test('talk overlays; others go beneath it; once each', async () => {
    let s = pushHold(defaultLoop(), 'review')
    s = pushHold(s, 'talk')
    s = pushHold(s, 'window')
    s = pushHold(s, 'window')
    expect(s.holds).toEqual(['review', 'window', 'talk'])
    expect(topHold(s)).toBe('talk')
    s = popHold(s, 'talk')
    expect(topHold(s)).toBe('window')
    expect(popHold(s, 'plan')).toBe(s)
    expect(topHold(defaultLoop())).toBe(null)
  })

  test('release: names its hold; review refused while the task is open', async () => {
    const s = with_({ holds: ['review', 'talk'], task: TASK })
    expect(releaseHold(s, { hold: 'review', note: '' }, true)).toEqual({ ok: false, refused: 'mark T4 done or reopen it first' })
    const r = releaseHold(s, { hold: 'review', note: 'ok' }, false)
    expect(r.ok && r.state.holds).toEqual(['talk'])
    expect(releaseHold(s, { hold: 'question', note: '' }, false)).toEqual({ ok: false, refused: 'not held: question' })
    const t = releaseHold(s, { hold: 'talk', note: 'go' }, true)
    expect(t).toEqual({ ok: true, state: with_({ holds: ['review'], task: TASK }), note: 'go' })
  })

  test('a plan_edit is a decision in review or question', async () => {
    expect(editIsDecision(with_({ holds: ['review'] }))).toBe(true)
    expect(editIsDecision(with_({ holds: ['question', 'talk'] }))).toBe(true)
    expect(editIsDecision(with_({ holds: ['talk'] }))).toBe(false)
    expect(editIsDecision(defaultLoop())).toBe(false)
    expect(editIsDecision(with_({ holds: ['stopped', 'talk'] }))).toBe(true) // 3.30.1: the developer's stop
    expect(editIsDecision(with_({ holds: ['session'] }))).toBe(false)
  })

  test('silence ends talk only, after 15 min, unless Claude asked', async () => {
    const s = with_({ holds: ['talk'], lastDevAt: 0 })
    expect(silenceReleases(s, 15 * 60_000 - 1)).toBe(false)
    expect(silenceReleases(s, 15 * 60_000)).toBe(true)
    expect(silenceReleases({ ...s, lastAnswerAsked: true }, 99 * 60_000)).toBe(false)
    expect(silenceReleases({ ...s, talkAnswerAt: 60_000 }, 15 * 60_000)).toBe(false)
    expect(silenceReleases({ ...s, talkAnswerAt: 60_000 }, 16 * 60_000)).toBe(true)
    expect(silenceReleases({ ...s, lastAnswerAt: 60_000 }, 15 * 60_000)).toBe(true) // 3.28.1 T9b: an answer after a notice
    expect(silenceReleases(with_({ holds: ['talk', 'review'] }), 99 * 60_000)).toBe(false)
  })

  test('window resumes at resume_at', async () => {
    const s = with_({ holds: ['window'], resumeAt: 10_000 })
    expect(windowResumes(s, 9_999)).toBe(false)
    expect(windowResumes(s, 10_000)).toBe(true)
    expect(windowResumes(with_({ holds: ['window'] }), 1e12)).toBe(false)
    expect(windowResumes(with_({ resumeAt: 1 }), 2)).toBe(false)
  })
})

describe('decisions', () => {
  test('owner, nobody, or a heartbeat older than 45 s (T5d1)', async () => {
    expect(OWNER_STALE_MS).toBe(45_000)
    const s = with_({ ownerSid: 'S1', heartbeatAt: 100_000 })
    expect(ownsLoop(s, 'S1', 100_000)).toBe(true)
    expect(ownsLoop(s, 'S2', 145_000)).toBe(false)
    expect(ownsLoop(s, 'S2', 145_001)).toBe(true)
    expect(ownsLoop(defaultLoop(), 'S2', 0)).toBe(true)
  })

  test('after a /clear: the owner is this process\'s previous session, owned at once (T13b.c4)', async () => {
    const s = with_({ ownerSid: 'S1', heartbeatAt: 100_000 })
    expect(ownsLoop(s, 'S2', 100_001, 'S1')).toBe(true)
    expect(takesOver(s, 'S2', 100_001, 'S1')).toBe(true)
    expect(ownsLoop(s, 'S2', 100_001, 'S3')).toBe(false)
    expect(ownsLoop(s, 'S2', 100_001, '')).toBe(false)
  })

  test('takeover: another owner, or a stale heartbeat under the same id; never a live one', async () => {
    const s = with_({ ownerSid: 'S1', heartbeatAt: 100_000 })
    expect(takesOver(s, 'S1', 140_000)).toBe(false)
    expect(takesOver(s, 'S1', 145_001)).toBe(true)
    expect(takesOver(s, 'S2', 140_000)).toBe(false)
    expect(takesOver(s, 'S2', 145_001)).toBe(true)
    expect(takesOver(defaultLoop(), 'S2', 0)).toBe(true)
  })

  test('a run ends: no busy child, nothing queued, 15 s quiet (T5d1)', async () => {
    expect(RUN_QUIET_MS).toBe(15_000)
    const t = { ...TASK, lastCompleteAt: 10_000, pendingContract: 'STATUS: done' }
    expect(runEnded(t, 0, 0, 25_000)).toBe(true)
    expect(runEnded(t, 0, 0, 24_999)).toBe(false)
    expect(runEnded(t, 1, 0, 1e9)).toBe(false)
    expect(runEnded(t, 0, 1, 1e9)).toBe(false)
    expect(runEnded(TASK, 0, 0, 1e9)).toBe(false)
  })

  test('3.24 T13A: a hand-back in: no 15 s quiet; children idle after it still wait; no hand-back keeps the wait', async () => {
    const t = { ...TASK, lastCompleteAt: 10_000, pendingContract: 'STATUS: done' }
    expect(runEnded(t, 0, 0, 10_001, 0, 10_000)).toBe(true)
    expect(runEnded(t, 0, 0, 10_001)).toBe(false)
    expect(runEnded(t, 1, 0, 1e9, 0, 10_000)).toBe(false)
    expect(runEnded(t, 0, 1, 1e9, 0, 10_000)).toBe(false)
    expect(runEnded({ ...t, childIdleAt: 9_000 }, 0, 0, 10_001, 0, 10_000)).toBe(true)
    expect(runEnded({ ...t, childIdleAt: 12_000 }, 0, 0, 26_999, 0, 10_000)).toBe(false)
    expect(runEnded({ ...t, childIdleAt: 12_000 }, 0, 0, 27_000, 0, 10_000)).toBe(true)
  })

  test('a steer or a forward after the last turn.complete is queued until the next (T5d2a)', async () => {
    const t = { ...TASK, lastCompleteAt: 10_000 }
    expect(queuedTo({ ...t, forwardAt: 9_000 }, 20_000)).toBe(0)
    expect(queuedTo({ ...t, forwardAt: 11_000 }, 20_000)).toBe(1)
    expect(queuedTo({ ...t, forwardAt: 11_000, steerAt: 12_000 }, 20_000)).toBe(2)
  })

  test('a forward expires after FORWARD_TTL_MS; runEnded waits RUN_QUIET_MS after the children went idle (T5d2a.c2)', async () => {
    const t = { ...TASK, lastCompleteAt: 10_000, forwardAt: 11_000, pendingContract: 'STATUS: done' }
    expect(FORWARD_TTL_MS).toBe(300_000)
    expect(queuedTo(t, 310_999)).toBe(1)
    expect(queuedTo(t, 311_000)).toBe(0)
    expect(runEnded({ ...t, childIdleAt: 30_000 }, 0, 0, 44_999)).toBe(false)
    expect(runEnded({ ...t, childIdleAt: 30_000 }, 0, 0, 45_000)).toBe(true)
  })

  test('no STATUS yet: ended only after NO_CONTRACT_QUIET_MS since the last activity, facts updatedAt included (T11-fix)', async () => {
    expect(NO_CONTRACT_QUIET_MS).toBe(600_000)
    const t = { ...TASK, lastCompleteAt: 10_000 }
    expect(runEnded(t, 0, 0, 25_000)).toBe(false)
    expect(runEnded(t, 0, 0, 609_999)).toBe(false)
    expect(runEnded(t, 0, 0, 610_000)).toBe(true)
    expect(runEnded({ ...t, childIdleAt: 20_000 }, 0, 0, 610_000)).toBe(false)
    expect(runEnded(t, 0, 0, 610_000, 300_000)).toBe(false)
    expect(runEnded(t, 0, 0, 900_000, 300_000)).toBe(true)
  })

  test('waitSpec: <file> contains <literal>', async () => {
    expect(waitSpec('.run/ready.txt contains READY')).toEqual({ file: '.run/ready.txt', literal: 'READY' })
    expect(waitSpec('`out/a.csv` contains "rows: 9"')).toEqual({ file: 'out/a.csv', literal: 'rows: 9' })
    expect(waitSpec('REVIEW.md')).toBe(null)
  })

  test('route by STATUS; missing or unparseable is a block', async () => {
    expect(routeStatus('STATUS: done\nCOMMIT: abc1234')).toBe('done')
    expect(routeStatus('```\nSTATUS: handoff\n```')).toBe('handoff')
    expect(routeStatus('STATUS: review')).toBe('review')
    expect(routeStatus('STATUS: question\nQUESTION: which?')).toBe('question')
    expect(routeStatus('STATUS: blocked')).toBe('block')
    expect(routeStatus('STATUS: error')).toBe('block')
    expect(routeStatus('all good')).toBe('block')
    expect(routeStatus(null)).toBe('block')
  })

  test('agent for a column (3.25 T6b): none or an old expert type is pa-expert; another passes through, -5m to 1h', async () => {
    expect(agentFor('expert-opus55-5m', 'medium')).toBe('pa-expert')
    expect(agentFor('expert-fable-5m', 'high')).toBe('pa-expert')
    expect(agentFor('expert-fable', 'medium')).toBe('pa-expert')
    expect(agentFor(null, 'high')).toBe('pa-expert')
    expect(agentFor('-', null)).toBe('pa-expert')
    expect(agentFor('my-agent-5m', null)).toBe('my-agent')
  })

  test('effort for an entry (3.25 T6b): its own, else high for an old expert-fable column, else medium; the hint', async () => {
    expect(effortFor('expert-fable', null)).toBe('high')
    expect(effortFor('expert-fable-5m', '')).toBe('high')
    expect(effortFor('expert-fable', 'medium')).toBe('medium')
    expect(effortFor(null, 'high')).toBe('high')
    expect(effortFor('-', null)).toBe('medium')
    expect(effortHint('high')).toBe('[effort high]')
  })
})

describe("the band's loop line (3.23 T13)", () => {
  const ROWS = 'id | status | agent\nT1 | done | -\nT2 | done | -\nT4 | running | -\nT5 | queued | -\n'

  test('task n of m from the rows, kept in loop.json; any hold; nothing when off or idle', () => {
    expect(taskPosOf(ROWS, 'T4')).toEqual([3, 4])
    expect(taskPosOf(ROWS, 'T9')).toBeNull()
    const s = parseLoop(serializeLoop({ ...with_({ state: 'running', task: TASK, holds: ['review', 'talk'] }), taskPos: [3, 4] } as LoopState))
    expect(taskPosIn(s)).toEqual([3, 4])
    expect(bandLine(s, '3.23')).toBe('Phase 3.23 · T4 (3 of 4) · held: review, talk')
    expect(bandLine(with_({ state: 'running', task: TASK }), '3.23')).toBe('Phase 3.23 · T4')
    expect(bandLine(with_({ state: 'off', task: TASK }), '3.23')).toBe('')
    expect(bandLine(with_({ state: 'boundary' }), '3.23')).toBe('')
  })

  test('the close step in place of the task while the chain runs, under the kept phase', () => {
    const s = { ...with_({ state: 'closing', chainStep: 'assemble' }), chainPhase: '3.23' } as LoopState
    expect(bandLine(s, '')).toBe('Phase 3.23 · close: assemble')
    expect(bandLine({ ...s, chainStep: 'archived', holds: ['question'] }, '')).toBe('Phase 3.23 · close: archived · held: question')
  })
})

describe('plan-end summaries (3.23 T13b)', () => {
  test('keepSummary keeps the contract SUMMARY by task id (a later capture replaces it); none keeps nothing', () => {
    const s0 = defaultLoop()
    expect(keepSummary(s0, TASK, 'STATUS: done\nCOMMIT: abc')).toBe(s0)
    const s1 = keepSummary(s0, TASK, 'STATUS: review\nSUMMARY: The modules are pure now.\nCOMMIT: abc')
    expect(summariesOf(s1)).toEqual({ T4: { title: 'pure modules', summary: 'The modules are pure now.' } })
    const s2 = keepSummary(s1, TASK, 'STATUS: done\nSUMMARY: The modules are pure and tested.\nCOMMIT: def')
    expect(summariesOf(s2)).toEqual({ T4: { title: 'pure modules', summary: 'The modules are pure and tested.' } })
    expect(summariesOf(parseLoop(serializeLoop(s2)))).toEqual(summariesOf(s2))
    expect(summariesOf({ ...s0, summaries: { T1: { title: 'x', summary: ' ' } } } as LoopState)).toEqual({})
  })

  test('closeNotice: one line per task, the report and the close question, release(close)', () => {
    const text = closeNotice('3.23', { T1: { title: 'add function', summary: 'The tool adds.\nIt is tested.' }, T2: { title: 'docs', summary: 'Docs say how.' } })
    expect(text).toBe('‹pa› Plan end: phase 3.23 has no task left. Each task\'s summary:\n' +
      'T1 add function: The tool adds. It is tested.\nT2 docs: Docs say how.\n' +
      ' Report the phase to the developer, then ask "Phase 3.23: every task is done. Is the milestone met: does what' +
      ' the phase built run?" with the answers "Yes, close the phase (recommended)" and "Not yet: <what is missing>",' +
      ' and wait. What is missing goes through plan_edit; on yes, call release({hold: "close", note: "<your report>"}).')
    expect(closeNotice('3.23')).toContain('No task left a summary.')
    // 3.25 T2: a generation end adds one sentence
    expect(closeNotice('3.23', {}, '3').endsWith('note: "<your report>"}). This close also ends Generation 3, so the report covers the whole generation.')).toBe(true)
    // 3.30: external coder runs since the last review add the auditor's external review; none adds nothing
    expect(closeNotice('3.40', {}, '', 4).endsWith(' 4 external coder runs finished since the last external review: once the' +
      ' close is released, start the auditor in the background with `EXTERNAL REVIEW · PHASE 3.40` and bring its proposals' +
      ' to the developer (the pa-external-agents skill, "Learning from each run").')).toBe(true)
    expect(closeNotice('3.40', {}, '', 1)).toContain(' 1 external coder run finished')
    expect(closeNotice('3.40', {}, '', 0)).toBe(closeNotice('3.40'))
  })
})

describe('the session title (3.23 T13d.c2)', () => {
  const PLAN = [
    '# Phase 3.23 — Autopilot (implements 3.2)', '', '## Tasks',
    '- T1 | done | title: a |', '- T2 | done | title: b |', '- T3 | next | title: c |', '- T4 | superseded | title: d |', '- Tx | done |', 'T5 | done |',
  ].join('\n')
  test('done/total as the status line counts them (superseded not in the total)', async () => {
    expect(planCounts(PLAN)).toEqual({ rows: 4, done: 2, total: 3 })
    expect(sessionTitle('calc', PLAN)).toBe('calc · Phase 3.23 · 2/3')
  })
  test('a ## Context bullet is not a task row (plan_edit TASK_RE; 3.24 T13f: the specimen showed 4/6)', async () => {
    const ctx = ['# Phase 5.2 — Keep going (implements 5)', '', '## Context',
      "- T2 transcripts (the review's evidence): `printf 'a' | calc` |", '- T3 release docs (`coder: opus55`) | anchors', '- T1x | done |',
      '## Tasks', '- T1 | done | title: a |', '- T2 | done | title: b |', '- T3 | done | title: c |', '- T4 | done | title: d |'].join('\n')
    expect(planCounts(ctx)).toEqual({ rows: 4, done: 4, total: 4 })
    expect(sessionTitle('pa3-calc', ctx)).toBe('pa3-calc · Phase 5.2 · 4/4')
  })
  test('no open phase or no tasks: the project alone', async () => {
    expect(sessionTitle('calc', '')).toBe('calc')
    expect(sessionTitle('calc', '# Phase 3.24 — Next\n\n## Tasks\n')).toBe('calc')
  })
  test('between phases (3.23 T13d.c6a): the next phase GENERATION_PLAN.md names; at a generation end, <gen + 1>.1', async () => {
    const gen = (rows: string[]): string => ['# Generation 1', '', '## Phases', '', ...rows, '', '## Changes', '- 2.1 x | status: open |'].join('\n')
    const closed = '- 1.1 Core | milestone: m | depends: — | status: closed | phase-end: phase-ends/PhaseEnd_Phase1.1.md'
    expect(nextPhaseOf(gen([closed, '- 1.2 More | milestone: m | status: planned |']))).toBe('1.2')
    expect(sessionTitle('pa3-loop', '', gen([closed, '- 1.2 More | milestone: m | status: planned |']))).toBe('pa3-loop · Phase 1.2')
    expect(sessionTitle('calc', '# Phase 1.2 — More\n\n## Tasks\n', gen([closed, '- 1.2 More | status: open |']))).toBe('calc · Phase 1.2')
    expect(sessionTitle('pa3-loop', '', gen([closed]))).toBe('pa3-loop · Phase 2.1')
    expect(nextPhaseOf(gen([closed, '- 1.2 More | status: closed |']))).toBe('2.1')
    expect(sessionTitle('calc', PLAN, gen([closed]))).toBe('calc · Phase 3.23 · 2/3')
    expect(sessionTitle('calc', '', '')).toBe('calc')
  })
  test('the close\'s recalc sessions (3.24 T10b): phaseSids, else the loop owner, then router_session, then the router\'s own', async () => {
    expect(recalcSessions(['A', 'B'], 'O1', 'RS1', 'S1')).toEqual(['A', 'B'])
    expect(recalcSessions(undefined, 'O1', 'RS1', 'S1')).toEqual(['O1'])
    expect(recalcSessions([], '', 'RS1', 'S1')).toEqual(['RS1'])
    expect(recalcSessions('junk', '', '', 'S1')).toEqual(['S1'])
    expect(recalcSessions(null, '', '', '')).toEqual([])
    // appended once per new owner, in order; a bad list starts over
    expect(withPhaseSid(['A'], 'B')).toEqual(['A', 'B'])
    expect(withPhaseSid(['A', 'B'], 'A')).toEqual(['A', 'B'])
    expect(withPhaseSid([1, '', 'A'], '')).toEqual(['A'])
    expect(withPhaseSid(undefined, 'S1')).toEqual(['S1'])
  })
  test('the project: pa.json\'s project, else the folder\'s name', async () => {
    expect(projectName('{"mod":"on","project":"Calc"}', 'C:/w/calc')).toBe('Calc')
    expect(projectName('{"mod":"on"}', 'C:/w/calc/')).toBe('calc')
    expect(projectName(null, 'C:\\w\\calc')).toBe('calc')
  })
})

describe('quiet confirmations (3.23 T13d.c3)', () => {
  test('a confirmation that only says done is silent; a refusal or a changed outcome is sent', () => {
    for (const l of ['released: review', 'released: stopped (resumed)', 'released: generation', 'released: question',
      'released: close', 'released: talk', // 3.24 T13A: Claude's own releases, nothing refused or changed
      'queued: tools/plan_edit.py append-change "x" (it lands at the next task boundary)', 'applied: added T4 after T3', 'steered: T1']) {
      expect([l, quietReply(l)]).toEqual([l, true])
    }
    for (const l of ['refused: mark T1 done or reopen it first', 'applied: added T4 (not committed: nothing to commit)',
      'released: stopped (restarted, attempt 2)', 'released: question\n<REPLAN>', 'released: talk (the developer moved on)']) {
      expect([l, quietReply(l)]).toEqual([l, false])
    }
  })
})

describe('the update run (3.24 T6)', () => {
  const NOW = 1_800_000_000_000
  const mk = (o: Record<string, unknown>): string => JSON.stringify({ state: 'done', mode: 'update', rc: 0, at: NOW / 1000, from: { phase: '3.23', git: 'aaaaaaaaaa11' }, to: { phase: '3.24', git: 'bbbbbbbbbb22' }, ...o })
  test('markerOf: at in seconds to ms; absent, broken, unknown state or no at is null', async () => {
    const m = markerOf(mk({ at: 1_800_000_000.5 }))
    expect(m).toEqual({ state: 'done', mode: 'update', rc: 0, atMs: 1_800_000_000_500, from: { phase: '3.23', git: 'aaaaaaaaaa11' }, to: { phase: '3.24', git: 'bbbbbbbbbb22' } })
    expect(markerOf('{"state":"running","at":5}')).toEqual({ state: 'running', mode: 'update', rc: 0, atMs: 5000, from: { phase: '', git: '' }, to: { phase: '', git: '' } })
    for (const t of [null, '', '{', '[1]', '{"state":"flying","at":5}', '{"state":"done"}']) {
      expect([t, markerOf(t)]).toEqual([t, null])
    }
  })
  test('runRunning: a running marker younger than RUN_STALE_MS', async () => {
    const r = markerOf(mk({ state: 'running' }))
    expect(runRunning(r, NOW + 1000)).toBe(true)
    expect(runRunning(r, NOW + RUN_STALE_MS)).toBe(false)
    expect(runRunning(markerOf(mk({})), NOW)).toBe(false)
    expect(runRunning(null, NOW)).toBe(false)
  })
  test('runToReport: a done marker newer than updateSeenAt and recent', async () => {
    const d = markerOf(mk({}))
    expect(runToReport(d, 0, NOW + 1000)).toBe(true)
    expect(runToReport(d, NOW, NOW + 1000)).toBe(false)
    expect(runToReport(d, 0, NOW + RUN_REPORT_MS)).toBe(false)
    expect(runToReport(markerOf(mk({ state: 'running' })), 0, NOW)).toBe(false)
    expect(runToReport(null, 0, NOW)).toBe(false)
  })
  test('updateOutcome: the conflict and restart note lines only', async () => {
    expect(updateOutcome('  ! .claude/agents/x.md kept yours; upstream staged\n  restart: needed (settings)\n')).toEqual({ conflicts: true, restart: true })
    expect(updateOutcome('install ok\n  restart: not needed\n')).toEqual({ conflicts: false, restart: false })
    expect(updateOutcome('note: ! x kept yours; upstream staged later\n')).toEqual({ conflicts: false, restart: false })
    expect(updateOutcome('')).toEqual({ conflicts: false, restart: false })
  })
  test('updateDoneNotice: update from X to Y, unchanged, repair, failure, restart, conflicts', async () => {
    const no = { conflicts: false, restart: false }
    const up = updateDoneNotice(markerOf(mk({}))!, no, '/h/.claude/pa3', '')
    expect(up).toContain('PA3 updated from 3.23 (aaaaaaaa) to 3.24 (bbbbbbbb)')
    expect(up).toContain('/h/.claude/pa3/CHANGES.md for the entries after 3.23 up to 3.24')
    expect(up).not.toContain('restart')
    expect(up).toContain('"Project Architect 3.24, a feature update (<the title of its CHANGES.md heading>), is installed.", a blank line, ' +
      'then on its own line:\n\n**To load it: /exit, then claude --continue (not /clear)**\n\n') // 3.28.2 T16
    const same = updateDoneNotice(markerOf(mk({ to: { phase: '3.23', git: 'aaaaaaaaaa11' } }))!, no, '/p', '')
    expect(same).toContain('the project stays at 3.23 (aaaaaaaa)')
    const rep = updateDoneNotice(markerOf(mk({ mode: 'repair' }))!, no, '/p', '')
    expect(rep).toContain('PA3 reinstall finished')
    expect(rep).toContain('3.24 (bbbbbbbb)')
    const bad = updateDoneNotice(markerOf(mk({ rc: 2, mode: 'repair' }))!, no, '/p', 'boom')
    expect(bad).toContain('PA3 reinstall failed (exit 2)')
    expect(bad).toContain(UPDATE_LOG)
    expect(bad).toContain('\nboom\n')
    const both = updateDoneNotice(markerOf(mk({}))!, { conflicts: true, restart: true }, '/p', '')
    expect(both).toContain('restart: needed')
    expect(both).toContain(UPGRADE_MD)
    // 3.28.2 T17: a restart already loaded the new mod: the loaded line, no restart line; restart: needed still asks
    const loaded = updateDoneNotice(markerOf(mk({}))!, { conflicts: false, restart: true, loaded: true }, '/p', '')
    expect(loaded).toContain('\n\n**Project Architect 3.24 is installed and loaded. Ready to continue work.**\n\n')
    expect(loaded).not.toContain('To load it')
    expect(loaded).toContain('restart: needed')
  })
  test('modFilesKey: the sorted mod/ entries; another file or unreadable does not count', async () => {
    const a = modFilesKey('{"files":{"mod/pa/b.ts":"2","tools/x.py":"9","mod/pa/a.ts":"1"}}')
    expect(a).toBe('[["mod/pa/a.ts","1"],["mod/pa/b.ts","2"]]')
    expect(modFilesKey('{"files":{"mod/pa/a.ts":"1","tools/x.py":"8","mod/pa/b.ts":"2"}}')).toBe(a)
    expect(modFilesKey('{"files":{"mod/pa/a.ts":"1","mod/pa/b.ts":"3"}}')).not.toBe(a)
    for (const t of [null, '{', '{}', '{"files":null}']) {
      expect([t, modFilesKey(t)]).toEqual([t, ''])
    }
  })
})

describe('the first run in 3.2 (3.24 T7b)', () => {
  test('firstRunLine: pending while first_run is present and migrated is not "3.2"', async () => {
    const pa = (o: Record<string, unknown>): string => JSON.stringify({ python: 'py', ...o })
    expect(firstRunLine(pa({ first_run: { from: 'vanilla', at: '3.24' } }), '.claude-state/memory')).toBe(
      'curate: migration 3.2 -> gen 3.2 · FROM: vanilla · MEMORY: .claude-state/memory')
    expect(firstRunLine(pa({ first_run: {} }), 'm')).toBe('curate: migration 3.2 -> gen 3.2 · FROM: unknown · MEMORY: m')
    for (const t of [pa({ first_run: { from: 'pa2' }, migrated: '3.2' }), pa({ migrated: '3.2' }), pa({}), pa({ first_run: 'x' }), '{', null]) {
      expect([t, firstRunLine(t, 'm')]).toEqual([t, ''])
    }
  })

  test('the ask names the first run; the archive is gen3.2.md; the generation start and legacy lines unchanged', async () => {
    const fr = 'curate: migration 3.2 -> gen 3.2 · FROM: gen3 · MEMORY: .claude-state/memory'
    expect(curateQuestion(fr)).toContain("This project's first run in PA 3.2: the memory curator sorts its memories once (")
    expect(curateQuestion('curate: closing gen 3 -> opening gen 4')).toBe(
      'The memory curator runs at every generation start (curate: closing gen 3 -> opening gen 4). Run it now?')
    expect(archiveOf(fr)).toBe('.claude-state/memory/gen3.2.md')
    expect(archiveOf('curate: migration -> gen legacy')).toBe('.claude-state/memory/genlegacy.md')
    expect(archiveOf('curate: closing gen 3 -> opening gen 4')).toBe('.claude-state/memory/gen3.md')
  })
})

describe('the planner an approval records (3.24 T17b)', () => {
  test('the latest pa-planner the caller spawned, else the caller', async () => {
    const list = [{ id: 'L1', type: 'pa-planner', parentId: 'P1' }, { id: 'C1', type: 'coder-opus55', parentId: 'P1' }, { id: 'L2', type: 'pa-planner', parentId: 'P1' }, { id: 'L3', type: 'pa-planner', parentId: 'X' }]
    expect(plannerIdOf(list, 'P1')).toBe('L2')
    expect(plannerIdOf(list, 'Q1')).toBe('Q1')
    expect(plannerIdOf([], 'P1')).toBe('P1')
  })
})

describe('external coders (3.3 T7)', () => {
  test('externalReminder: absent, the line; coder set, remind: false, use: false or bad JSON, none', async () => {
    const line = externalReminder('{"mod":"on"}')
    expect(line.startsWith('PA3: external coders are available (OpenRouter or local models do the coding;')).toBe(true)
    expect(line.endsWith('(PY tools/pa_ext.py set --remind off).')).toBe(true)
    expect(externalReminder('{"mod":"on","external":{"use":true}}')).toBe(line)
    expect(externalReminder('{"external":{"coder":"deepseek"}}')).toBe('')
    expect(externalReminder('{"external":{"remind":false}}')).toBe('')
    expect(externalReminder('{"external":{"use":false}}')).toBe('')
    expect(externalReminder('{not json')).toBe('')
    expect(externalReminder(null)).toBe('')
  })

  test('setupNotice: a new status under 24 h, the line; old, already seen or bad JSON, null', async () => {
    const st = (at: number, text = 'saved: OpenRouter, deepseek.'): string => JSON.stringify({ at, ok: true, text })
    const now = 1_000_000
    expect(setupNotice(st(now - 60), null, now)).toBe('PA3: the developer ran the external-coder setup: saved: OpenRouter, ' +
      'deepseek. If it failed, explain why and the fix from the pa-external-agents skill\'s troubleshooting table. If it ' +
      'saved, load that skill, lay out the config, run PY tools/pa_ext.py check, and ask how and when they want external ' +
      'coders used in this project.')
    expect(setupNotice(st(now - 25 * 3_600), null, now)).toBe(null)
    expect(setupNotice(st(now - 60), JSON.stringify({ at: now - 60 }), now)).toBe(null)
    expect(setupNotice(st(now - 60), JSON.stringify({ at: now - 600 }), now)).not.toBe(null)
    expect(setupNotice('{bad', null, now)).toBe(null)
    expect(setupNotice(null, null, now)).toBe(null)
    expect(setupNotice(st(now - 60), '{bad', now)).not.toBe(null)
  })
})

describe('the start check (3.30.2)', () => {
  test('startCheckOffline: the start note says its fetch got no answer; still an answer for the gate', () => {
    const t = (o: Record<string, unknown>): string => JSON.stringify({ session: 'S1', at: 100, update: false, ...o })
    expect(startCheckOffline(t({ state: 'offline' }), 'S1', 100_000)).toBe(true)
    expect(startCheckOffline(t({ state: 'done' }), 'S1', 100_000)).toBe(false)
    expect(startCheckOffline(t({ state: 'offline', session: 'S0' }), 'S1', 100_000)).toBe(false)
    expect(startCheckOffline(null, 'S1', 100_000)).toBe(false)
    expect(startCheckState(t({ state: 'offline' }), 'S1', 100_000, 100_000)).toBe('done')
  })
})
