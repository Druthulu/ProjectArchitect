import { describe, expect, test } from 'claude-code/testing'

import { decide, errorText, ledgerPath, paSetting, releaseOf, heartbeatText, versionAtLeast, versionOf } from './activation'
import { clip, eventFile, isStale, pruneArgv } from './debug'
import { handle, parseRequest, parseRequestName, pendingRequests, requestName } from './inbox'
import { defaultState, lastJson, markDone, parseLast, parseState, pruneDone, serializeState, STATE_V } from './state'

const ON = '{"mod":"on","warmer":"off","upgrade":"ask"}'
const V = '2.1.293'

describe('activation', () => {
  test('pa.json mod: on, and nothing else, turns it on', async () => {
    expect(decide(ON, undefined, V).active).toBe(true)
    expect(decide(null, undefined, V)).toEqual({ active: false, reason: 'no .claude/pa.json' })
    expect(decide('{"mod":"off"}', undefined, V).active).toBe(false)
    expect(decide('{"mod":true}', undefined, V).active).toBe(false)
    expect(decide('{}', undefined, V).active).toBe(false)
    expect(decide('not json', undefined, V).active).toBe(false)
  })

  test('PA_MOD_OFF, non-empty and not 0, turns it off', async () => {
    expect(decide(ON, '1', V).active).toBe(false)
    expect(decide(ON, 'yes', V).active).toBe(false)
    expect(decide(ON, '0', V).active).toBe(true)
    expect(decide(ON, '', V).active).toBe(true)
  })

  test('the version floor compares numerically', async () => {
    expect(decide(ON, undefined, '2.1.292').active).toBe(true)
    expect(decide(ON, undefined, '2.1.291').active).toBe(false)
    expect(decide(ON, undefined, '2.1.1000').active).toBe(true)
    expect(decide(ON, undefined, '2.2.0').active).toBe(true)
    expect(decide(ON, undefined, '10.0.0').active).toBe(true)
    expect(decide(ON, undefined, '1.9.999').active).toBe(false)
    expect(decide(ON, undefined, 'dev').active).toBe(false)
    expect(versionAtLeast('2.1.300-dev.20260920', '2.1.292')).toBe(true)
    expect(versionAtLeast('x', '2.1.292')).toBe(null)
    expect(versionOf({ version: '2.1.293-dev.20261001.t1.sha1', base: '2.1.293-dev' })).toBe('2.1.293-dev')
    expect(versionOf({ version: '2.1.293' })).toBe('2.1.293')
  })

  test('pa.json settings', async () => {
    expect(paSetting('{"python":"C:/py/python.exe"}', 'python')).toBe('C:/py/python.exe')
    expect(paSetting('{"python":3}', 'python')).toBe('')
    expect(paSetting(null, 'python')).toBe('')
    expect(paSetting('{', 'python')).toBe('')
  })

  test('ledger path: PA_MOD_PA3_DIR, else home', async () => {
    expect(ledgerPath('Z:\\snap\\project-architect-3.0\\', 'C:\\Users\\u')).toBe('Z:/snap/project-architect-3.0/pa_ledger.py')
    expect(ledgerPath(undefined, 'C:\\Users\\u')).toBe('C:/Users/u/.claude/pa3/pa_ledger.py')
    expect(ledgerPath('', '/home/u')).toBe('/home/u/.claude/pa3/pa_ledger.py')
    expect(ledgerPath(undefined, '')).toBe('')
  })
})

describe('state file', () => {
  test('default, serialize and parse round-trip', async () => {
    const s = { ...defaultState('S1', 3), markers: { '1': 'journal/a.md' } }
    const back = parseState(serializeState(s), 'other', 0)
    expect(back).toEqual(s)
    expect(back?.v).toBe(STATE_V)
    expect(serializeState(s).endsWith('\n')).toBe(true)
  })

  test('migration from v0 fills defaults and stamps listed inbox ids', async () => {
    const v0 = JSON.stringify({ gen: 2, partner: { agentId: 'a1', model: 'opus' }, inboxDone: ['1-1-plan'], warm: { pings: 4 } })
    const s = parseState(v0, 'S9', 500)
    expect(s?.v).toBe(STATE_V)
    expect(s?.sid).toBe('S9')
    expect(s?.gen).toBe(2)
    expect(s?.inboxDone).toEqual({ '1-1-plan': 500 })
    expect(s?.partner).toEqual({ agentId: 'a1', spawnedAt: 0, lastTurnAt: 0, model: 'opus', ctx: 0, topic: false, modelSwitchAt: 0, pingInFlight: false, firstCtx: 0 })
    expect(s?.warm).toEqual({ pings: 4, pingUsd: 0, avoidedUsd: 0, lastPingAt: 0, waitStart: 0, waitUsd: 0, waitPings: 0 })
    expect([s?.journal, s?.toasts]).toEqual([[], []])
    expect(s?.children).toEqual({})
    expect(s?.hold).toBe(null)
    expect(s?.pending).toEqual([])
    expect([s?.outbox, s?.pendingMarkers, s?.forwarded, s?.awaiting]).toEqual([[], [], {}, null])
  })

  test('unreadable or newer state is refused', async () => {
    expect(parseState('{', 'S', 0)).toBe(null)
    expect(parseState('[]', 'S', 0)).toBe(null)
    expect(parseState(JSON.stringify({ v: STATE_V + 1 }), 'S', 0)).toBe(null)
  })

  test('inbox ids: marked, pruned by age', async () => {
    const s = markDone(markDone(defaultState('S', 1), 'old', 0), 'new', 10_000)
    expect(Object.keys(pruneDone(s, 10_000, 5_000).inboxDone)).toEqual(['new'])
  })

  test('last.json', async () => {
    expect(parseLast(lastJson('S1', 5))).toBe('S1')
    expect(parseLast(null)).toBe(null)
    expect(parseLast('{"sid":""}')).toBe(null)
  })
})

describe('inbox', () => {
  test('request names', async () => {
    expect(requestName(1700000000000, 2, 'plan_edit')).toBe('1700000000000-2-plan_edit.json')
    expect(parseRequestName('1700000000000-2-plan_edit.json')).toEqual({ id: '1700000000000-2-plan_edit', ms: 1700000000000, seq: 2, kind: 'plan_edit' })
    expect(parseRequestName('notes.txt')).toBe(null)
    expect(parseRequestName('1-2-Plan.json')).toBe(null)
    expect(() => requestName(1, 1, 'Bad Kind')).toThrow('bad request kind')
  })

  test('request bodies must agree with their names', async () => {
    const body = { id: '5-1-ask', kind: 'ask', caller: 'a1', input: { q: 'x' }, at: 5 }
    expect(parseRequest(JSON.stringify(body), '5-1-ask.json')).toEqual(body)
    expect(parseRequest(JSON.stringify({ ...body, kind: 'plan' }), '5-1-ask.json')).toBe(null)
    expect(parseRequest('{', '5-1-ask.json')).toBe(null)
    expect(parseRequest(JSON.stringify({ id: '5-1-ask', kind: 'ask' }), '5-1-ask.json')).toEqual({ id: '5-1-ask', kind: 'ask', caller: '', input: null, at: 5 })
  })

  test('pending: not done, inside the horizon, oldest first', async () => {
    const names = ['20-1-ask.json', '10-2-plan.json', '10-10-plan.json', '10-1-topic.json', 'x.json', '1-1-old.json']
    expect(pendingRequests(names, { '10-2-plan': 99 }, 100, 95)).toEqual(['10-1-topic.json', '10-10-plan.json', '20-1-ask.json'])
  })

  test('an unhandled kind only logs; topic flags its caller, ask raises the input toast', async () => {
    expect(handle({ id: '1-1-note', kind: 'note', caller: '', input: null, at: 1 }))
      .toEqual([{ do: 'log', kind: 'inbox.drain', data: { id: '1-1-note', kind: 'note' } }])
    expect(handle({ id: '1-2-topic', kind: 'topic', caller: 'P1', input: {}, at: 1 }).at(-1)).toEqual({ do: 'topic', agentId: 'P1' })
    expect(handle({ id: '1-3-ask', kind: 'ask', caller: 'P1', input: { question: 'A or B?' }, at: 1 })[1])
      .toEqual({ do: 'toast', cause: 'input', body: 'A or B?' })
  })
})

describe('wiring helpers', () => {
  test('generation staleness', async () => {
    expect(isStale(1, 1)).toBe(false)
    expect(isStale(1, 2)).toBe(true)
    expect(isStale(3, 0)).toBe(true)
  })

  test('event files sort by time and counter', async () => {
    expect(eventFile('d', 1700000000000, 7, 'pa.activation')).toBe('d/1700000000000-00007-pa.activation.json')
    expect(eventFile('d', 1, 1, 'a b/c')).toBe('d/1-00001-a_b_c.json')
    expect(clip('abcdef', 3)).toBe('abc…')
    expect(clip({ a: 1 }, 50)).toBe('{"a":1}')
  })

  test('prune argv needs an interpreter and keeps the named folders', async () => {
    expect(pruneArgv('', 'b', ['S1'], 14)).toBe(null)
    const argv = pruneArgv('py', '/r/.run/mod', ['S1', ''], 14)
    expect(argv?.slice(0, 2)).toEqual(['py', '-c'])
    expect(argv?.slice(3)).toEqual(['/r/.run/mod', '14', 'S1'])
  })
})

describe('health records (3.24 T6)', () => {
  test('releaseOf: the version: line, else the first line; empty when absent', async () => {
    expect(releaseOf('name: pa3\r\nversion: 3.24.0 \n')).toBe('3.24.0')
    expect(releaseOf(' 3.24.1\nmore\n')).toBe('3.24.1')
    expect(releaseOf(null)).toBe('')
    expect(releaseOf('')).toBe('')
  })
  test('heartbeatText and errorText: the fields pa/health.py reads, at in seconds, the error clipped', async () => {
    expect(JSON.parse(heartbeatText('S1', '3.24.0', '2.1.293', 1_500, 'pa'))).toEqual({ sid: 'S1', version: '3.24.0', engine: '2.1.293', at: 1.5, plugin: 'pa' })
    const e = JSON.parse(errorText('S1', 'pa-watch', 'session.start', 'x'.repeat(600), 2_000)) as Record<string, unknown>
    expect({ ...e, error: (e['error'] as string).length }).toEqual({ sid: 'S1', plugin: 'pa-watch', hook: 'session.start', error: 500, at: 2 })
  })
})
