// Hook tests of the router's wiring through the kit's engine `$`. Tests have no fs: each test answers the fs.* events
// from a map with its own hooks, beneath the plugin's, and records the order of writes.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine, On } from 'claude-code'

import { freshStartUsd, pingUsd } from './policy'
import { SEED_BUDGET_TOKENS } from './seed'

const ROOT = '/r'
const BASE = `${ROOT}/.run/mod`
const ON = '{"mod":"on","warmer":"off","upgrade":"ask"}'

/** The kit hands hooks resolved host paths (`Z:\r\...` on Windows); the map keys are `/r/...`. */
const norm = (p: string): string => p.replace(/\\/g, '/').replace(/^[A-Za-z]:/, '')

type World = { files: Map<string, string>; writes: string[]; sid: { now: string } }

/** The test's world beneath the plugin: files, session, a bottom for the events it raises. */
function world(on: On, files: Record<string, string>, env: Record<string, string>, denyWrite = ''): World {
  const w: World = { files: new Map(Object.entries(files)), writes: [], sid: { now: 'S1' } }
  mock.env(on, env)
  on('session.root', async () => ({ value: ROOT }))
  on('session.id', async () => ({ value: w.sid.now }))
  on('session.version', async () => ({ value: { version: '2.1.293', base: '2.1.293' } }))
  on('fs.read', async ($, e) => {
    const t = w.files.get(norm(e.path))
    return t === undefined ? { deny: `ENOENT ${norm(e.path)}` } : { value: t }
  })
  on('fs.write', async ($, e) => {
    if (norm(e.path) === denyWrite) {
      return { deny: `EACCES ${denyWrite}` }
    }
    w.files.set(norm(e.path), e.text)
    w.writes.push(norm(e.path))
    return { value: undefined }
  })
  on('fs.exists', async ($, e) => {
    const d = norm(e.path)
    return { value: [...w.files.keys()].some(p => p === d || p.startsWith(`${d}/`)) }
  })
  on('fs.list', async ($, e) => {
    const d = norm(e.path)
    const names = new Map<string, 'file' | 'dir'>()
    for (const p of w.files.keys()) {
      if (p.startsWith(`${d}/`)) {
        const rest = p.slice(d.length + 1)
        const i = rest.indexOf('/')
        names.set(i < 0 ? rest : rest.slice(0, i), i < 0 ? 'file' : 'dir')
      }
    }
    return { value: [...names].map(([name, kind]) => ({ name, kind, size: 1, mtimeMs: 0, isLink: false })) }
  })
  on('session.start', async () => ({ cwd: ROOT }))
  on('session.compact', async () => ({ skip: 'bottom' }))
  on('classic.SessionStart', async () => ({}))
  return w
}

const START = { cwd: ROOT, surface: null, isInteractive: false } as const
const MSGS = [{ role: 'user' as const, text: 'hi', toolUses: [] }]
const debugKinds = (w: World): string[] =>
  w.writes.filter(p => p.includes('/debug/')).map(p => (/-\d{5}-(.+)\.json$/.exec(p) ?? [])[1] ?? '')
const request = (sid: string, ms: number, seq: number, kind: string): [string, string] =>
  [`${BASE}/${sid}/inbox/${String(ms)}-${String(seq)}-${kind}.json`, JSON.stringify({ id: `${String(ms)}-${String(seq)}-${kind}`, kind, caller: 'a1', input: {}, at: ms })]

describe('router wiring', () => {
  test('inactive without pa.json: no state, no debug files, compaction passes through', async ($, on) => {
    mock.clock(on)
    const w = world(on, {}, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    expect(w.files.has(`${BASE}/S1/state.json`)).toBe(false)
    expect(debugKinds(w)).toEqual([]) // 3.24 T8: an inert session writes nothing, debug or not
    const r = await $.session.compact({ trigger: 'manual', messages: MSGS })
    expect(r).toEqual({ skip: 'bottom' })
  })

  test('PA_MOD_OFF keeps it inactive', async ($, on) => {
    mock.clock(on)
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, { PA_MOD_OFF: '1' })
    await $.session.start(START)
    expect(w.files.has(`${BASE}/S1/state.json`)).toBe(false)
  })

  test('active with mod: on: state and last.json written, compaction skipped', async ($, on) => {
    mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    const s = JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { gen: number; sid: string; v: number }
    expect([s.gen, s.sid, s.v]).toEqual([1, 'S1', 1])
    expect(JSON.parse(w.files.get(`${BASE}/last.json`) ?? '{}')).toEqual({ sid: 'S1', at: 1_000 })
    expect(debugKinds(w)).toEqual(['pa.activation', 'pa.drawn.load', 'pa.gen', 'autopilot.armed'])
    const r = await $.session.compact({ trigger: 'auto', messages: MSGS })
    expect(r).toEqual({ skip: 'PA 3.2: the journal replaces compaction' })
    expect(debugKinds(w)).toContain('pa.compact.skip')
  })

  test('3.23 T13d.c6c: pa.drawn.load carries the counts loaded from drawn.json and the reason (start, resume)', async ($, on) => {
    const clock = mock.clock(on, { now: 5_000 })
    const kept = JSON.stringify({ cards: ['a', 'b'], feedRuns: [{ text: 'f', runs: [] }] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/drawn.json`]: kept }, { PA_MOD_DEBUG: '1' })
    const loads = (): Array<Record<string, unknown>> => w.writes.filter(p => p.endsWith('-pa.drawn.load.json'))
      .map(p => JSON.parse(w.files.get(p) ?? '{}') as Record<string, unknown>)
    await $.session.start(START)
    w.sid.now = 'S2'
    await $.classic.SessionStart({ source: 'resume', session_id: 'S2' })
    await clock.settle()
    expect(loads().map(e => [e['reason'], e['cards'], e['feedTexts']])).toEqual([['start', 2, 1], ['resume', 2, 1]])
  })

  test('the drain marks a request done before dispatching it, once', async ($, on) => {
    const clock = mock.clock(on, { now: 5_000 })
    const [path, body] = request('S1', 4_000, 1, 'plan')
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [path]: body }, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    await clock.advance(1_000)
    const kinds = debugKinds(w)
    expect(kinds).toContain('pa.inbox.drain')
    const doneAt = w.writes.lastIndexOf(`${BASE}/S1/state.json`)
    const drainAt = w.writes.findIndex(p => p.endsWith('-pa.inbox.drain.json'))
    expect(doneAt).toBeLessThan(drainAt)
    const s = JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { inboxDone: Record<string, number> }
    expect(s.inboxDone).toEqual({ '4000-1-plan': 6_000 })
    await clock.advance(3_000)
    expect(debugKinds(w).filter(k => k === 'pa.inbox.drain').length).toBe(1)
  })

  test('a /clear re-arms under gen + 1 and the stale tick returns early', async ($, on) => {
    const clock = mock.clock(on, { now: 5_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    w.sid.now = 'S2'
    await $.classic.SessionStart({ source: 'clear', session_id: 'S2' })
    await clock.settle()
    const s2 = JSON.parse(w.files.get(`${BASE}/S2/state.json`) ?? '{}') as { gen: number; sid: string }
    expect([s2.gen, s2.sid]).toEqual([2, 'S2'])
    expect(JSON.parse(w.files.get(`${BASE}/last.json`) ?? '{}')).toEqual({ sid: 'S2', at: 5_000 })
    // A request left in the old session's inbox: only the stale gen-1 tick would see it, and it stops first.
    const [oldPath, oldBody] = request('S1', 5_000, 1, 'ask')
    w.files.set(oldPath, oldBody)
    const [newPath, newBody] = request('S2', 5_000, 2, 'plan')
    w.files.set(newPath, newBody)
    await clock.advance(1_000)
    await clock.advance(1_000)
    const kinds = debugKinds(w)
    expect(kinds.filter(k => k === 'pa.gen.stale').length).toBe(1)
    expect(kinds.filter(k => k === 'pa.inbox.drain').length).toBe(1)
    const s1 = JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { inboxDone: Record<string, number> }
    expect(s1.inboxDone).toEqual({})
    // A second re-arm for the same session is a no-op: no third generation, no second set of ticks.
    await $.classic.SessionStart({ source: 'resume', session_id: 'S2' })
    await clock.settle()
    expect(JSON.parse(w.files.get(`${BASE}/S2/state.json`) ?? '{}').gen).toBe(2)
  })
})

// The route's world: the agents, sends, marker submits, processes and the engine's model step beneath the plugin.
type Net = {
  spawns: string[]
  sends: { to: string; text: string }[]
  submits: string[]
  list: { id: string; status: 'running' | 'pending' | 'idle' | 'completed'; parentId?: string }[]
  apiSteps: number
  /** Process runs (`run:<subcommand>`) and sends (`send:<text>`), in order. */
  order: string[]
  runs: string[][]
  /** The answer of a process run, by argv. */
  run: (argv: readonly string[]) => { exitCode: number; stdout: string; stderr: string }
  /** `$.session.send` delivers (default) or not (3.23 T5d2b). */
  deliver?: boolean
}

function net(on: On): Net {
  const n: Net = {
    spawns: [], sends: [], submits: [], list: [], apiSteps: 0, order: [], runs: [],
    run: () => ({ exitCode: 2, stdout: '', stderr: 'invalid choice' }),
  }
  on('process.run', async ($, e) => {
    n.runs.push([...e.argv])
    n.order.push(`run:${e.argv[2] ?? ''}`)
    return { value: n.run(e.argv) }
  })
  on('agent.spawn', async ($, e) => {
    n.spawns.push(e.prompt)
    // The kit's Agent tool answers `{ model }` with no agentId: the router finds the new partner in the list.
    n.list = [...n.list, { id: `P${String(n.spawns.length)}`, status: 'pending' }]
    return { agentId: `P${String(n.spawns.length)}`, model: 'opus' }
  })
  on('agent.list', async () => ({ value: n.list.map(a => ({ ...a, description: '', type: 'pa-claude' })) }))
  on('session.send', async ($, e) => {
    n.sends.push({ to: e.to, text: e.text })
    n.order.push(`send:${e.text}`)
    return n.deliver === false ? { isDelivered: false, reason: 'no such agent' } : { isDelivered: true }
  })
  on('prompt.submit', async ($, e) => {
    if (e.origin.kind === 'plugin') {
      n.submits.push(e.text)
    }
    return { text: e.text, origin: e.origin }
  })
  on('turn.start', async ($, e) => ({ turnId: e.turnId }))
  on('turn.complete', async ($, e) => ({ text: e.answer }))
  on('turn.step', async function* ($, e) {
    n.apiSteps += 1
    yield { kind: 'text' as const, index: 0, text: 'from the api' }
    return { turnId: e.turnId, index: e.index, answer: 'from the api', toolUses: [], stopReason: 'end_turn' as const, usage: null }
  })
  return n
}

const ISTART = { cwd: ROOT, surface: null, isInteractive: true } as const
const USAGE = { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 100, cache_creation_input_tokens: 1_000, model: 'opus' }
type St = {
  partner: { agentId: string; ctx: number; pingInFlight: boolean; topic: boolean } | null
  pendingMarkers: number[]
  markers: Record<string, string>
  outbox: unknown[]
  warm: { pings: number; pingUsd: number; avoidedUsd: number }
}
const stateOf = (w: World): St => JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as St
const JDIR = `${ROOT}/.claude-state/journal`
/** The journal's entry files, by name kind (`dev-msg`, `partner-answer`, ...). */
const entries = (w: World, kind: string): string[] =>
  [...w.files.keys()].filter(p => p.startsWith(`${JDIR}/`) && new RegExp(`/\\d{6}-\\d{3}[rw]-${kind}\\.md$`).test(p)).sort()
const indexOf = (w: World): string => [...w.files.entries()].find(([p]) => /\/INDEX-\d{4}-\d{2}\.md$/.test(p))?.[1] ?? ''
const PY = '{"mod":"on","warmer":"off","upgrade":"ask","python":"py"}'
const PRICES = JSON.stringify({ model: 'claude-opus-5-5', model_key: 'opus-5-5', price: { read: 0.5, write_5m: 6.25, write_1h: 10, input: 5, output: 25 } })
const MARK1 = '‹pa reply 1›'
const SENT = '‹sent›'
/** 3.24 T13B: a reply of Claude's as its turn answers it, an orange Claude card. */
const CLAUDE = (reply: string, shown = ''): string => `**🟧 Claude${shown === '' ? '' : ` · ${shown}`}**\n\n${reply}` // 3.24 T17g: shown
/** 3.24 T13B: the route's `pa.route.marker` debug events' modes, in order (`feed` or `marker`). */
const markerModes = (w: World): unknown[] => w.writes.filter(p => p.endsWith('-pa.route.marker.json'))
  .map(p => (JSON.parse(w.files.get(p) ?? '{}') as Record<string, unknown>)['mode'])
/** An interactive hand-back as the engine frames it (3.22 T8d): the report after its header, every line indented. */
const handback = (from: string, report: string): string =>
  `<agent-message from="${from}">\n[Subagent hand-back] The text below is the final report … The report follows:\n`
  + `${report.split('\n').map(l => `  ${l}`).join('\n')}\n  \n</agent-message>`

/** Reads a turn.step stream to its end: the chunks, and the result its generator returned. */
async function drain<C, R>(stream: AsyncGenerator<C, R>): Promise<{ chunks: C[]; result: R }> {
  const chunks: C[] = []
  let r = await stream.next()
  while (r.done !== true) {
    chunks.push(r.value)
    r = await stream.next()
  }
  return { chunks, result: r.value }
}

describe('router route', () => {
  test('a developer prompt, the local turns, the reply and its marker', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    await $.session.start(ISTART)
    const r = await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false })
    expect(r.text).toBe('hello')
    await clock.advance(1_000)
    expect(n.spawns.length).toBe(1)
    expect(n.spawns[0]?.startsWith('JOURNAL SEED\n')).toBe(true)
    expect(n.spawns[0]?.endsWith('\n\nhello')).toBe(true)
    expect(stateOf(w).partner?.agentId).toBe('P1')
    // The message filed as dev/msg and the rotation as mod/start; the month's index lists both.
    const [msg] = entries(w, 'dev-msg')
    expect(w.files.get(msg ?? '')?.endsWith('\n\nhello\n')).toBe(true)
    expect(entries(w, 'mod-start').length).toBe(1)
    expect(indexOf(w)).toContain(`| dev | msg | hello | ${(msg ?? '').slice(ROOT.length + 1)}`)
    expect(indexOf(w).trim().split('\n').length).toBe(2)

    await $.turn.start({ text: 'hello', turnId: 'T1' })
    const s1 = await drain($.turn.step({ turnId: 'T1', index: 0, model: 'opus', messageCount: 1 }))
    expect(s1.result.answer).toBe(SENT)
    expect(s1.chunks[0]).toEqual({ kind: 'text', index: 0, text: SENT })
    await $.turn.complete({ turnId: 'T1', answer: SENT, durationMs: 1, isAborted: false, reason: 'answer' })

    // 3.22 T8f: the watcher's last-step facts of P1 stamp model and effort on the answer.
    w.files.set(`${ROOT}/.run/mod/S1/agents/P1.json`, '{"model":"claude-opus-5-5","effort":"high","ctx":1110,"at":1}')
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'A fun fact.', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    expect(stateOf(w).pendingMarkers).toEqual([])
    expect(stateOf(w).partner?.ctx).toBe(1_110)
    // The marker names the partner/answer entry, written by the tick before the marker is submitted.
    const answerPath = stateOf(w).markers['1'] ?? ''
    expect(answerPath).toMatch(/\/\.claude-state\/journal\/\d{4}-\d{2}-\d{2}\/\d{6}-\d{3}r-claude-answer\.md$/)
    // T8d: no hand-back came: the marker is the fallback after 5 s.
    await clock.advance(1_000)
    expect(n.submits).toEqual([])
    await clock.advance(5_000)
    expect(n.submits).toEqual([MARK1])
    expect(debugKinds(w)).toContain('pa.route.fallback')
    expect(stateOf(w).pendingMarkers).toEqual([])
    const entry = w.files.get(answerPath) ?? ''
    expect(entry).toContain('\nagentId: P1\n')
    expect(entry).toContain('\nmodel: claude-opus-5-5\neffort: high\n') // was model: opus (the usage's); 3.22 T8f: the watcher's step
    expect(entry).toContain('\nusage: {input: 10, cacheRead: 100, cacheWrite: 1000, output: 5}\n')
    expect(entry.endsWith('---\n\nA fun fact.\n')).toBe(true)
    expect(indexOf(w).trim().split('\n').length).toBe(3)

    await $.turn.start({ text: MARK1, turnId: 'T2' })
    const s2 = await drain($.turn.step({ turnId: 'T2', index: 0, model: 'opus', messageCount: 3 }))
    expect(s2.result.answer).toBe(CLAUDE('A fun fact.', 'Opus 5.5 · high')) // 3.24 T17g: the answer's model and effort
    // An unregistered main turn still gets the stub, never the API.
    const s3 = await drain($.turn.step({ turnId: 'T9', index: 0, model: 'opus', messageCount: 3 }))
    expect(s3.result.answer).toBe(SENT)
    expect(n.apiSteps).toBe(0)
    expect(debugKinds(w)).toContain('pa.main.unexpected')

    // The second message resumes the recorded partner; seen running, the send is confirmed.
    n.list = [{ id: 'P1', status: 'running' }]
    await $.prompt.submit({ text: 'more', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['more'])
    expect(n.sends[0]?.to).toContain('P1')
    await clock.advance(1_000)
    expect(n.spawns.length).toBe(1)
  })

  test('a send to a partner never seen running seeds a fresh one after ~30 s', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'OLD' } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'still there?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.sends.length).toBe(1)
    expect(n.spawns).toEqual([])
    await clock.advance(31_000)
    expect(n.spawns.length).toBe(1)
    expect(n.spawns[0]?.startsWith('JOURNAL SEED\n')).toBe(true)
    expect(n.spawns[0]?.endsWith('\n\nstill there?')).toBe(true)
    expect(stateOf(w).partner?.agentId).toBe('P1')
  })

  test('a coder hand-back with an idle parent is forwarded once; the partner hand-back is not', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    n.list = [{ id: 'C1', status: 'completed', parentId: 'P1' }, { id: 'P1', status: 'idle' }]
    await $.session.start(ISTART)
    const r = await $.prompt.submit({ text: '<task-notification><task-id>C1</task-id>done</task-notification>', origin: { kind: 'task-notification' }, wait: false })
    expect(r.text).toContain('C1')
    await $.prompt.submit({ text: '<agent-message from="C1">done</agent-message>', origin: { kind: 'peer' }, wait: false })
    await $.prompt.submit({ text: '<task-notification><task-id>P1</task-id>bye</task-notification>', origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.sends.length).toBe(1)
    expect(n.sends[0]?.to).toContain('P1')
    expect(n.sends[0]?.text.startsWith('Hand-back from your background agent C1:\n')).toBe(true)
    // T8d: a coder's hand-back turn still gets the stub.
    const coder = handback('C1', 'Coder done.')
    await $.prompt.submit({ text: coder, origin: { kind: 'peer' }, wait: false })
    await $.turn.start({ text: coder, turnId: 'T1' })
    expect((await drain($.turn.step({ turnId: 'T1', index: 0, model: 'opus', messageCount: 1 }))).result.answer).toBe(SENT)
  })

  test('T8d: a partner hand-back before turn.complete answers its turn, and no marker follows', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    await $.session.start(ISTART)
    const step = async (turnId: string, text: string): Promise<string> => {
      await $.turn.start({ text, turnId })
      return (await drain($.turn.step({ turnId, index: 0, model: 'opus', messageCount: 1 }))).result.answer
    }
    const hb = handback('P1', 'A fun fact.\n\n  with code')
    await $.prompt.submit({ text: hb, origin: { kind: 'peer' }, wait: false })
    expect(await step('T1', hb)).toBe(CLAUDE('A fun fact.\n\n  with code'))
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'A fun fact.\n\n  with code', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(7_000)
    expect(n.submits).toEqual([])
    expect(entries(w, 'claude-answer').length).toBe(1)
    // The notification that only points at the hand-back, and a `.` ping's hand-back: the stub.
    const pointer = '<task-notification>\n<task-id>P1</task-id>\n<result>This agent\'s report was delivered to you as a message from "P1"</result>\n</task-notification>'
    await $.prompt.submit({ text: pointer, origin: { kind: 'task-notification' }, wait: false })
    expect(await step('T2', pointer)).toBe(SENT)
    const ping = handback('P1', '.')
    await $.prompt.submit({ text: ping, origin: { kind: 'peer' }, wait: false })
    expect(await step('T3', ping)).toBe(SENT)
    await $.turn.complete({ turnId: 'A2', agentId: 'P1', answer: '.', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(7_000)
    expect(n.submits).toEqual([])
    expect(n.apiSteps).toBe(0)
  })

  test('T8d: no hand-back within 5 s: the marker; a late hand-back gets the stub', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    await $.session.start(ISTART)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'Late fact.', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(4_000)
    expect(n.submits).toEqual([])
    await clock.advance(2_000)
    expect(n.submits).toEqual([MARK1])
    const hb = handback('P1', 'Late fact.')
    await $.prompt.submit({ text: hb, origin: { kind: 'peer' }, wait: false })
    await $.turn.start({ text: hb, turnId: 'T1' })
    expect((await drain($.turn.step({ turnId: 'T1', index: 0, model: 'opus', messageCount: 1 }))).result.answer).toBe(SENT)
    await $.turn.start({ text: MARK1, turnId: 'T2' })
    expect((await drain($.turn.step({ turnId: 'T2', index: 0, model: 'opus', messageCount: 1 }))).result.answer).toBe(CLAUDE('Late fact.', 'opus')) // 3.24 T17g: the usage's model, no effort
  })

  test('3.24 T13B: the band: Claude\'s row with its step; `‹pa› working` with cycling dots; the conversation-only note', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'running' }]
    w.files.set(`${BASE}/S1/agents/P1.json`, JSON.stringify({ line: 'Write .run/PHASE_PLAN.draft.md' }))
    const band = async (): Promise<string> => JSON.stringify(await (await $.ui.mount({
      plugin: 'pa', surface: 'terminal', component: 'AbovePrompt',
      props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 9 }, view: {} },
    })).drawn())
    await $.session.start(ISTART)
    await clock.advance(500)
    const claude = await band()
    expect(claude).toContain('Claude')
    expect(claude).toContain('#d7875f')
    expect(claude).toContain('Write .run/PHASE_PLAN.draft.md')
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'R1', status: 'running' }]
    const seen: string[] = []
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(500)
      seen.push((/‹pa› working\.*/.exec(await band()) ?? [''])[0])
    }
    expect(new Set(seen)).toEqual(new Set(['‹pa› working.', '‹pa› working..', '‹pa› working...']))
    w.files.set(`${BASE}/loop.json`, JSON.stringify({ ownerSid: 'OTHERSID-1', heartbeatAt: 3_000, state: 'off', holds: [] }))
    await clock.advance(500)
    const noted = await band()
    expect(noted).toContain('conversation only: the loop runs in OTHERSID')
    expect(noted).not.toContain('working')
  })

  test('an attachment message gets the notice', async ($, on) => {
    mock.clock(on, { now: 1_000 })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    net(on)
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'look', origin: { kind: 'composer' }, wait: false, attachments: [{ type: 'image' }] })
    await $.turn.start({ text: 'look', turnId: 'T1' })
    const s = await drain($.turn.step({ turnId: 'T1', index: 0, model: 'opus', messageCount: 1 }))
    expect(s.result.answer).toBe('(Claude sees text only; your attachment stays here.)')
  })

  test('the ping hold queues messages and flushes them in order at the partner turn end', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1', pingInFlight: true } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'running' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'one', origin: { kind: 'composer' }, wait: false })
    await $.prompt.submit({ text: 'two', origin: { kind: 'composer' }, wait: false })
    await clock.advance(3_000)
    expect(n.sends).toEqual([])
    expect(stateOf(w).outbox.length).toBe(2)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: '.', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    expect(stateOf(w).pendingMarkers).toEqual([])
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['one', 'two'])
  })

  test('render rules draw a marker row and the stub as an empty Box', async ($, on) => {
    mock.clock(on, { now: 1_000 })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    net(on)
    await $.session.start(ISTART)
    const row = await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'UserMessage', props: { text: MARK1, origin: { kind: 'plugin', name: 'pa' }, isExpanded: true } })
    expect(await row.drawn()).toMatchObject({ type: 'Box' })
    const stub = await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text: SENT, isFirstOfReply: true } })
    expect(await stub.drawn()).toMatchObject({ type: 'Box' })
  })

  test('a non-interactive session without the flag leaves the route off', async ($, on) => {
    mock.clock(on, { now: 1_000 })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    const n = net(on)
    await $.session.start(START)
    const s = await drain($.turn.step({ turnId: 'T1', index: 0, model: 'opus', messageCount: 1 }))
    expect(n.apiSteps).toBe(1)
  })
})

// T8a: the journal seed and rotation, warming, the waiting toast, the index rebuild.
// the kit has no toBeCloseTo: compare with Math.abs(a - b) < 1e-9.)
const OCT = Date.UTC(2026, 9, 15, 12) // mid-October in every time zone
const prior = (partner: Record<string, unknown>): string => JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner })
const entryFile = (day: string, name: string, ts: string, body: string): [string, string] =>
  [`${JDIR}/${day}/${name}`, `---\nts: ${ts}\nsession: S0\n---\n\n${body}\n`]

/** The ledger runs only: the startup sweep's inline `python -c` run, the journal roll-up (3.23 T7, `run:`) and the loop's
 * launch.py mode check (T6a2) are left out. */
const lib = (order: string[]): string[] => order.filter(o => !o.startsWith('run:import ') && o !== 'run:--state-json' && o !== 'run:')
const libRuns = (n: Net): string[][] =>
  n.runs.filter(r => !(r[2] ?? '').startsWith('import ') && r[2] !== '--state-json' && r[1] !== 'tools/journal_rollup.py')

describe('router journal, rotation, warming, toast', () => {
  test('the topic flag rotates: a fresh partner seeded from the journal', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [qp, qt] = entryFile('2026-10-14', '090000-001r-dev-msg.md', '2026-10-14T09:00:00.000Z', 'What is 2+2?')
    const [ap, at] = entryFile('2026-10-14', '090010-002r-partner-answer.md', '2026-10-14T09:00:10.000Z', 'Four.')
    const [ip, it] = request('S1', OCT - 500, 1, 'topic')
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: ON, [qp]: qt, [ap]: at, [ip]: it.replace('"caller":"a1"', '"caller":"OLD"'),
      [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 5_000 }),
      [`${ROOT}/GENERATION_PLAN.md`]: '# plan',
    }, {})
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }]
    await $.session.start(ISTART)
    await clock.advance(1_000)
    expect(stateOf(w).partner?.topic).toBe(true)
    await $.prompt.submit({ text: 'new subject', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.sends).toEqual([])
    expect(n.spawns.length).toBe(1)
    const seed = n.spawns[0] ?? ''
    expect(seed.startsWith('JOURNAL SEED\n')).toBe(true)
    expect(seed).toContain('What is 2+2?')
    expect(seed).toContain('Four.')
    expect(seed).toContain('- GENERATION_PLAN.md')
    expect(seed.endsWith('\n\nnew subject')).toBe(true)
    expect(seed.split('new subject').length).toBe(2)
    expect(stateOf(w).partner?.agentId).toBe('P1')
    expect(stateOf(w).partner?.topic).toBe(false)
    expect(w.files.get(entries(w, 'mod-start')[0] ?? '')).toMatch(/Fresh partner P1 \(rotate\); seed ~\d+ tokens\. Replaces OLD\./)
  })

  test('PA_MOD_ROTATE_AT forces fresh at that ctx', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 1_110 }) },
      { PA_MOD_ROTATE_AT: '1000' })
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'go on', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.sends).toEqual([])
    expect(n.spawns.length).toBe(1)
    expect(n.spawns[0]?.startsWith('JOURNAL SEED\n')).toBe(true)
  })

  // 3.23 T13d.c5: the partner's ctx is its last step's (the watcher's facts file), not turn.complete's summed usage.
  const lastStep = async ($: Engine, on: On, stepCtx: number): Promise<Net> => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 500 }) },
      { PA_MOD_ROTATE_AT: '1000' })
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }]
    await $.session.start(ISTART)
    w.files.set(`${BASE}/S1/agents/OLD.json`, JSON.stringify({ model: 'claude-opus-5-5', effort: 'medium', ctx: stepCtx, at: OCT }))
    const sum = { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 5_000, cache_creation_input_tokens: 400, model: 'opus' }
    await $.turn.complete({ turnId: 'A1', agentId: 'OLD', answer: 'ok', durationMs: 1, isAborted: false, reason: 'answer', usage: sum })
    expect(stateOf(w).partner?.ctx).toBe(stepCtx)
    await clock.advance(6_000)
    await $.prompt.submit({ text: 'go on', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    return n
  }

  test('a turn whose summed usage is over the knob but whose last step is small keeps the partner', async ($, on) => {
    const n = await lastStep($, on, 900)
    expect(n.spawns).toEqual([])
    expect(n.sends.map(s => [s.to, s.text])).toEqual([['OLD', 'go on']])
  })

  test('a last step over the knob rotates the partner', async ($, on) => {
    const n = await lastStep($, on, 1_500)
    expect(n.spawns.length).toBe(1)
    expect(n.spawns[0]?.startsWith('JOURNAL SEED\n')).toBe(true)
  })

  test('never fresh with a child out, even flagged and over the knob', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 1_110, topic: true }) },
      { PA_MOD_ROTATE_AT: '1000' })
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }, { id: 'C1', status: 'running', parentId: 'OLD' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'status?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.spawns).toEqual([])
    expect(n.sends.map(s => [s.to, s.text])).toEqual([['OLD', 'status?']])
  })

  test('warming: the event is spooled before the ping; the . turn lifts the hold and is filed nowhere', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: PY,
      [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', model: 'claude-opus-5-5', ctx: 50_000, firstCtx: 30_000, lastTurnAt: OCT - 56 * 60_000 }),
    }, { USERPROFILE: 'H' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    n.run = argv => (argv.includes('prices') ? { exitCode: 0, stdout: PRICES, stderr: '' } : { exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(60_000)
    expect(lib(n.order)).toEqual(['run:prices', 'run:event', 'send:.'])
    const ev = libRuns(n)[1] ?? []
    expect(ev.slice(0, 9)).toEqual(['py', 'H/.claude/pa3/pa_ledger.py', 'event', 'warm_ping', '--session', 'S1', '--run', 'P1', '--detail'])
    const detail = JSON.parse(ev[9] ?? '{}') as Record<string, unknown>
    expect([detail['run_id'], detail['ttl'], detail['via'], detail['n'], detail['cap']]).toEqual(['P1', '1h', 'mod', 1, null])
    expect(Object.keys(detail['cap_inputs'] as object).sort()).toEqual(['ctx', 'model', 'ping_usd', 'rewrite_usd'])
    expect(libRuns(n)[0]).toEqual(['py', 'H/.claude/pa3/pa_ledger.py', 'prices', '--json', '--model', 'claude-opus-5-5'])
    expect(stateOf(w).partner?.pingInFlight).toBe(true)
    expect(stateOf(w).warm.pings).toBe(1)
    expect(Math.abs(stateOf(w).warm.avoidedUsd - 50_000 * (10 - 0.5) / 1e6) < 1e-9).toBe(true)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: '.', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    expect(stateOf(w).partner?.pingInFlight).toBe(false)
    await clock.advance(1_000)
    expect(entries(w, 'claude-answer')).toEqual([])
    expect(n.submits).toEqual([])
    await clock.advance(60_000)
    expect(n.runs.filter(r => r.includes('prices')).length).toBe(1)
  })

  test('no rates: no ping while waiting on the developer, norates logged once', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: PY,
      [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', model: 'claude-opus-5-5', ctx: 50_000, lastTurnAt: OCT - 56 * 60_000 }),
    }, { USERPROFILE: 'H', PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    await $.session.start(ISTART)
    await clock.advance(60_000)
    await clock.advance(60_000)
    expect(n.sends).toEqual([])
    expect(debugKinds(w).filter(k => k === 'pa.warm.norates').length).toBe(1)
  })

  test('no rates: a child out still gets its ping', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    world(on, {
      [`${ROOT}/.claude/pa.json`]: PY,
      [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', model: 'claude-opus-5-5', ctx: 50_000, lastTurnAt: OCT - 56 * 60_000 }),
    }, { USERPROFILE: 'H' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'C1', status: 'running', parentId: 'P1' }]
    await $.session.start(ISTART)
    await clock.advance(60_000)
    expect(lib(n.order)).toEqual(['run:prices', 'run:prices', 'run:event', 'send:.'])
  })

  // 3.23 T13b: one toast per stop. A loop notice with a cause raises none; Claude's next reply (its presentation)
  // raises that one; plain conversation raises none.
  const stopNotice = (ms: number, input: Record<string, string>): [string, string] => {
    const id = `${String(ms)}-1-notice`
    return [`${BASE}/S1/inbox/${id}.json`, JSON.stringify({ id, kind: 'notice', caller: 'loop', input, at: ms })]
  }
  const ANSWER = (turnId: string, answer: string) =>
    ({ turnId, agentId: 'P1', answer, durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE }) as const
  // 3.24 T17t: a picker held open until `open()`.
  const gated = (d: Dialog): (() => void) => {
    let open = (): void => undefined
    d.gate = new Promise<void>(r => {
      open = r
    })
    return open
  }

  test('no toast on a plain answer, even one ending in a question', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }) }, { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'Two ways.\n\nShall I use A or B?'))
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(0)
  })

  test('a stop notice raises no toast; Claude\'s presentation raises one with its tail, then none', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = stopNotice(OCT - 500, { text: '‹pa› T1 stopped for your review.', toast: 'review' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), [r1]: b1 },
      { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(0)
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').stopToast).toBe('review')
    await $.turn.complete(ANSWER('A1', 'T1 built the masks. It waits for your review.'))
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(0) // 3.24 T17t: after 60 s of the developer's quiet
    await clock.advance(60_000)
    const toasts = n.runs.filter(r => r[2] === 'toast')
    expect(toasts).toEqual([['py', 'H/.claude/pa3/pa_ledger.py', 'toast', '--session', 'S1', '--cause', 'review',
      '--title', 'PA3 · r · review waiting', '--body', 'T1 built the masks. It waits for your review.', '--project', ROOT]])
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').stopToast).toBe(null)
    await $.turn.complete(ANSWER('A2', 'Anything else?'))
    await clock.advance(1_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(1)
  })

  test('Claude\'s picker first: its input toast is the stop\'s one; the reply after raises none', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = stopNotice(OCT - 500, { text: '‹pa› T1 stopped: T1 asks a question.', toast: 'question' })
    const [r2, b2] = inboxReq(OCT - 400, 2, 'ask', { question: 'Floats or ints?', options: ['Floats', 'Ints'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), [r1]: b1, [r2]: b2 },
      { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    const d = dialog(on, w)
    const open = gated(d) // 3.24 T17t: an answer before 60 s would drop the toast
    d.answer = 'Ints'
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(61_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[6])).toEqual(['input'])
    open()
    await clock.advance(1_000)
    await $.turn.complete(ANSWER('A1', 'Ints it is; T1 goes on.'))
    await clock.advance(1_000)
    await clock.advance(61_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[6])).toEqual(['input'])
  })

  test('a loop ask during a stop (T13b.c2: the gen-end recap and its picker): one toast, at Claude\'s reply', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = stopNotice(OCT - 500, { text: '‹pa› Phase 1.1 closed. Give the developer the recap.', toast: 'input' })
    const r2 = `${BASE}/S1/inbox/${String(OCT - 400)}-2-ask.json`
    const b2 = JSON.stringify({ id: `${String(OCT - 400)}-2-ask`, kind: 'ask', caller: 'loop', input: { question: 'Next generation?', options: ['Yes', 'No'] }, at: OCT - 400 })
    world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), [r1]: b1, [r2]: b2 },
      { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    const d = dialog(on)
    const open = gated(d) // 3.24 T17t: an answer before 60 s would drop the toast
    d.answer = 'Yes'
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(0)
    await $.turn.complete(ANSWER('A1', 'Phase 1.1 is done. Push: git push'))
    await clock.advance(1_000)
    await clock.advance(61_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[6])).toEqual(['input'])
    open()
    await clock.advance(1_000)
  })

  // 3.24 T17t: a stop's toast waits for 60 s of the developer's quiet (the your-turn rule); typing or answering drops it.
  test('3.24 T17t: a question right after a developer message raises no toast before 60 s', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }) }, { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    const d = dialog(on, w)
    const open = gated(d)
    d.answer = 'Keep'
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'Hi, let\'s pick this project back up', origin: { kind: 'composer' }, wait: false })
    const [r1, b1] = inboxReq(OCT + 1_000, 1, 'ask', { question: 'Curate the memories?', options: ['Keep', 'Curate'] })
    w.files.set(r1, b1)
    await clock.advance(2_000)
    await clock.advance(50_000)
    expect(n.runs.filter(r => r[2] === 'toast')).toEqual([])
    open()
    await clock.advance(1_000)
  })

  test('3.24 T17t: the toast fires at 60 s while the question is open and the developer is quiet, once', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }) }, { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    const d = dialog(on, w)
    const open = gated(d)
    d.answer = 'Keep'
    await $.session.start(ISTART)
    const [r1, b1] = inboxReq(OCT, 1, 'ask', { question: 'Curate the memories?', options: ['Keep', 'Curate'] })
    w.files.set(r1, b1)
    await clock.advance(1_000)
    await clock.advance(55_000)
    expect(n.runs.filter(r => r[2] === 'toast')).toEqual([])
    await clock.advance(6_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => [r[6], r[10]])).toEqual([['input', 'Curate the memories?']])
    await clock.advance(2_000)
    expect(n.runs.filter(r => r[2] === 'toast').length).toBe(1)
    open()
    await clock.advance(1_000)
  })

  test('3.24 T17t: a message before 60 s cancels the toast', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = stopNotice(OCT - 500, { text: '‹pa› T1 stopped for your review.', toast: 'review' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), [r1]: b1 },
      { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(1_000)
    // a stop's toast at Claude's presentation; the developer types at 10 s
    await $.turn.complete(ANSWER('A1', 'T1 waits for your review.'))
    await clock.advance(10_000)
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').toasts.map((t: { cause: string }) => t.cause)).toEqual(['review'])
    await $.prompt.submit({ text: 'looking now', origin: { kind: 'composer' }, wait: false })
    await clock.advance(70_000)
    expect(n.runs.filter(r => r[2] === 'toast')).toEqual([])
  })

  test('3.24 T17t: a picker answered before 60 s cancels its toast', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT, 1, 'ask', { question: 'Floats or ints?', options: ['Floats', 'Ints'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), [r1]: b1 },
      { USERPROFILE: 'H' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    const d = dialog(on, w)
    const open = gated(d)
    d.answer = 'Ints'
    await $.session.start(ISTART)
    await clock.advance(10_000)
    expect(d.calls.length).toBeGreaterThan(0)
    open()
    await clock.advance(1_000)
    await clock.advance(60_000)
    expect(n.runs.filter(r => r[2] === 'toast')).toEqual([])
  })

  // 3.23 T13d.c10: the your-turn toast: Claude's reply landed, no agent live, 60 s without a developer message.
  const turnWorld = (on: On, extra: Record<string, string> = {}): { w: World; n: Net } => {
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 }), ...extra },
      { USERPROFILE: 'H', PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.run = () => ({ exitCode: 0, stdout: '{}', stderr: '' })
    n.list = [{ id: 'P1', status: 'idle' }]
    return { w, n }
  }
  const turnToasts = (n: Net): string[][] => n.runs.filter(r => r[2] === 'toast' && r[8] === 'PA3 · r · your turn')

  test('your turn: fires once, 60 s after the reply with every agent idle, its first line the body', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { w, n } = turnWorld(on)
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', '\nThe masks are built.\n\nNext: the gate.'))
    await clock.advance(58_000)
    expect(turnToasts(n)).toEqual([])
    await clock.advance(3_000)
    expect(turnToasts(n)).toEqual([['py', 'H/.claude/pa3/pa_ledger.py', 'toast', '--session', 'S1', '--cause', 'input',
      '--title', 'PA3 · r · your turn', '--body', 'The masks are built.', '--project', ROOT]])
    await clock.advance(120_000)
    expect(turnToasts(n).length).toBe(1)
    expect(debugKinds(w).filter(k => k === 'pa.yourturn.fire').length).toBe(1)
    // a new reply re-arms it
    await $.turn.complete(ANSWER('A2', 'Anything else?'))
    await clock.advance(61_000)
    expect(turnToasts(n).length).toBe(2)
  })

  test('your turn: waits while a thinker runs, fires once it is done; a later agent drops it', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { w, n } = turnWorld(on)
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'T1', status: 'running', parentId: 'P1' }]
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'A thinker is on it.'))
    await clock.advance(90_000)
    expect(turnToasts(n)).toEqual([])
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'T1', status: 'completed', parentId: 'P1' }]
    await clock.advance(2_000)
    expect(turnToasts(n).length).toBe(1)
    await $.turn.complete(ANSWER('A2', 'Asking a retriever.'))
    n.list = [...n.list, { id: 'R1', status: 'running', parentId: 'P1' }]
    await clock.advance(2_000)
    n.list = n.list.map(a => (a.id === 'R1' ? { ...a, status: 'completed' as const } : a))
    await clock.advance(61_000)
    expect(turnToasts(n).length).toBe(1)
    expect(debugKinds(w).filter(k => k === 'pa.yourturn.drop').length).toBe(1)
  })

  test('your turn: none when the developer typed after the reply', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { w, n } = turnWorld(on)
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'Done.'))
    await clock.advance(10_000)
    await $.prompt.submit({ text: 'and the tests?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(70_000)
    expect(turnToasts(n)).toEqual([])
    expect(debugKinds(w).filter(k => k === 'pa.yourturn.drop').length).toBe(1)
  })

  test('your turn: none on top of a stop\'s toast', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = stopNotice(OCT - 500, { text: '‹pa› T1 stopped for your review.', toast: 'review' })
    const { n } = turnWorld(on, { [r1]: b1 })
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.turn.complete(ANSWER('A1', 'T1 waits for your review.'))
    await clock.advance(70_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[6])).toEqual(['review'])
  })

  test('3.24 T13E: your turn: a loop toast drained (the generation end) drops it, one toast', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { w, n } = turnWorld(on)
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'X1', status: 'running', parentId: 'P1' }]
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'Closing the phase.'))
    await clock.advance(2_000)
    const id = `${String(OCT + 3_000)}-1-toast`
    w.files.set(`${BASE}/S1/inbox/${id}.json`, JSON.stringify({ id, kind: 'toast', caller: 'loop', input: { cause: 'input', body: 'Generation closed.' }, at: OCT + 3_000 }))
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'X1', status: 'completed', parentId: 'P1' }]
    await clock.advance(70_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[10])).toEqual(['Generation closed.'])
    expect(turnToasts(n)).toEqual([])
  })

  test('3.24 T17j: your turn: a hand-back the loop routes to review: no your-turn toast, its review toast alone', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const task = { id: 'T2', title: 't', agent: 'expert-opus55', agentId: 'E1', attempt: 1, contract: 'STATUS: review' }
    const { w, n } = turnWorld(on, { [`${BASE}/loop.json`]: JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: OCT, state: 'routing', holds: [], task }) })
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'T2 is back; the loop routes it.'))
    await clock.advance(70_000)
    expect(turnToasts(n)).toEqual([])
    const id = `${String(OCT + 71_000)}-1-toast`
    w.files.set(`${BASE}/S1/inbox/${id}.json`, JSON.stringify({ id, kind: 'toast', caller: 'loop', input: { cause: 'review', body: 'T2 waits for your review.' }, at: OCT + 71_000 }))
    w.files.set(`${BASE}/loop.json`, JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: OCT + 71_000, state: 'routing', holds: ['review'], task: { ...task, routed: 'review' } }))
    await clock.advance(70_000)
    expect(n.runs.filter(r => r[2] === 'toast').map(r => r[10])).toEqual(['T2 waits for your review.'])
    expect(debugKinds(w).filter(k => k === 'pa.yourturn.wait').length).toBe(1)
  })

  test('3.24 T17j: your turn: none while the close chain runs', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { n } = turnWorld(on, { [`${BASE}/loop.json`]: JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: OCT, state: 'closing', chainStep: 'lint', holds: [], task: null }) })
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'The closer is back.'))
    await clock.advance(130_000)
    expect(turnToasts(n)).toEqual([])
  })

  test('your turn: none while a picker is open', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const { w, n } = turnWorld(on)
    const d = dialog(on, w)
    let open = (): void => undefined
    d.gate = new Promise<void>(r => {
      open = r
    })
    d.answer = 'Ints'
    await $.session.start(ISTART)
    await $.turn.complete(ANSWER('A1', 'Two ways; pick one.'))
    const [r2, b2] = inboxReq(OCT + 5_000, 2, 'ask', { question: 'Floats or ints?', options: ['Floats', 'Ints'] })
    w.files.set(r2, b2)
    await clock.advance(70_000)
    expect(turnToasts(n)).toEqual([])
    expect(debugKinds(w).filter(k => k === 'pa.yourturn.drop').length).toBe(1)
    open()
    await clock.advance(1_000)
  })

  test('the index is rebuilt at session start when it disagrees with the entries', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [qp, qt] = entryFile('2026-10-14', '090000-001r-dev-msg.md', '2026-10-14T09:00:00.000Z', 'Hello | there')
    const [tp, tt] = entryFile('2026-10-14', '090005-001w-partner-thinking.md', '2026-10-14T09:00:05.000Z', 'Pondering.')
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: ON, [qp]: qt, [tp]: tt,
      [`${JDIR}/INDEX-2026-10.md`]: 'x | dev | msg | gone | .claude-state/journal/2026-10-01/000000-001r-dev-msg.md\n',
    }, {})
    net(on)
    await $.session.start(START)
    await clock.settle()
    expect(w.files.get(`${JDIR}/INDEX-2026-10.md`)).toBe(
      '2026-10-14T09:00:00.000Z | dev | msg | Hello \\| there | .claude-state/journal/2026-10-14/090000-001r-dev-msg.md\n' +
      '2026-10-14T09:00:05.000Z | partner | thinking | Pondering. | .claude-state/journal/2026-10-14/090005-001w-partner-thinking.md\n')
  })

  // 3.23 T7: a rolled day file (tools/journal_rollup.py) beside a live day folder.
  const rolledDay = (date: string, files: [string, string][]): string =>
    `# Journal ${date} (${String(files.length)} entries, rolled by tools/journal_rollup.py)\n` +
    files.map(([p, t]) => `## ${(p.split('/').at(-1) ?? '').replace(/\.md$/, '')}\n${t}`).join('')

  test('3.23 T7: the index lists a rolled day from <date>.md#<stem> and a live day from its folder', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const r1 = entryFile('2026-10-13', '080000-001r-dev-msg.md', '2026-10-13T08:00:00.000Z', 'Rolled hello')
    const r2 = entryFile('2026-10-13', '080010-002r-claude-answer.md', '2026-10-13T08:00:10.000Z', 'Rolled answer')
    const [qp, qt] = entryFile('2026-10-14', '090000-001r-dev-msg.md', '2026-10-14T09:00:00.000Z', 'Live hello')
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: ON, [qp]: qt, [`${JDIR}/2026-10-13.md`]: rolledDay('2026-10-13', [r1, r2]),
      [`${JDIR}/INDEX-2026-10.md`]: 'x | dev | msg | gone | .claude-state/journal/2026-10-13/080000-001r-dev-msg.md\n',
    }, {})
    net(on)
    await $.session.start(START)
    await clock.settle()
    expect(w.files.get(`${JDIR}/INDEX-2026-10.md`)).toBe(
      '2026-10-13T08:00:00.000Z | dev | msg | Rolled hello | .claude-state/journal/2026-10-13.md#080000-001r-dev-msg\n' +
      '2026-10-13T08:00:10.000Z | claude | answer | Rolled answer | .claude-state/journal/2026-10-13.md#080010-002r-claude-answer\n' +
      '2026-10-14T09:00:00.000Z | dev | msg | Live hello | .claude-state/journal/2026-10-14/090000-001r-dev-msg.md\n')
  })

  test('3.23 T7: the roll-up runs after the prune at session start, its output in the debug log', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY }, { USERPROFILE: 'H', PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.run = argv => ({ exitCode: 0, stdout: argv[1] === 'tools/journal_rollup.py' ? 'rolled: 2026-10-13 (2 entries)\n' : '', stderr: '' })
    await $.session.start(START)
    await clock.settle()
    const i = n.runs.findIndex(r => r[1] === 'tools/journal_rollup.py')
    expect(n.runs[i]).toEqual(['py', 'tools/journal_rollup.py'])
    expect(n.runs.findIndex(r => r[1] === '-c')).toBeLessThan(i)
    const f = w.writes.find(p => p.endsWith('-pa.rollup.json')) ?? ''
    expect(w.files.get(f)).toContain('rolled: 2026-10-13 (2 entries)')
  })

  test('3.23 T7: a fresh seed reads a rolled day and a live day, with LOOP and MEMORY', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const r1 = entryFile('2026-10-13', '080000-001r-dev-msg.md', '2026-10-13T08:00:00.000Z', 'Rolled question')
    const r2 = entryFile('2026-10-13', '080010-002r-claude-answer.md', '2026-10-13T08:00:10.000Z', 'Rolled answer')
    const r3 = entryFile('2026-10-13', '080020-003w-claude-thinking.md', '2026-10-13T08:00:20.000Z', 'Rolled reasoning')
    const [qp, qt] = entryFile('2026-10-14', '090000-001r-dev-msg.md', '2026-10-14T09:00:00.000Z', 'Live question')
    const [ap, at] = entryFile('2026-10-14', '090010-002r-claude-answer.md', '2026-10-14T09:00:10.000Z', 'Live answer')
    const loop = JSON.stringify({ v: 1, state: 'boundary', holds: ['review'],
      task: { id: 'T3', title: 'add flag', agent: 'expert-opus55', coder: 'opus55', effort: 'medium', attempt: 2, kind: 'task',
        agentId: 'E1', startSha: 'a', spawnedAt: 1, lastCompleteAt: 0, contract: null } })
    world(on, {
      [`${ROOT}/.claude/pa.json`]: ON.replace('}', ',"python":"py"}'), [qp]: qt, [ap]: at, [`${JDIR}/2026-10-13.md`]: rolledDay('2026-10-13', [r1, r2, r3]),
      [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 1_110 }),
      [`${BASE}/loop.json`]: loop, [`${ROOT}/phase-ends/current/PHASE_PLAN.md`]: '# Phase 4.1 — flags (implements 4)\n',
      [`${ROOT}/.claude-state/memory/MEMORY.md`]: '- [Dev](dev.md) — prefers terse\n- [Repo](repo.md) — monorepo\n',
    }, { PA_MOD_ROTATE_AT: '1000' })
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }]
    const base = n.run // 3.24 T7b: Claude's card slice
    n.run = argv => (argv[1] === 'tools/card.py' && argv[3] === 'claude' ? { exitCode: 0, stdout: '## Developer <!-- roles: claude -->\n- plain words\n', stderr: '' } : base(argv))
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'go on', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    const seed = n.spawns[0] ?? ''
    expect(seed.indexOf('Live answer')).toBeLessThan(seed.indexOf('Rolled question'))
    expect(seed.indexOf('Rolled question')).toBeLessThan(seed.indexOf('Rolled answer'))
    expect(seed).not.toContain('Rolled reasoning')
    expect(seed).toContain('## LOOP\n\n- phase: 4.1; state: boundary\n- task: T3 add flag (attempt 2)\n- holds: review\n- open stop: review T3\n')
    expect(seed).toContain('## MEMORY\n\n- [Dev](dev.md) — prefers terse\n- [Repo](repo.md) — monorepo\n')
    expect(seed).toContain('## CARD\n\n## Developer <!-- roles: claude -->\n- plain words\n\n## MEMORY\n')
    expect(seed).not.toContain('first run in PA 3.2')
  })

  test('3.24 T7b-2c: a pending first run (pa.json) puts its one line in the seed', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    world(on, {
      [`${ROOT}/.claude/pa.json`]: ON.replace('}', ',"first_run":{"from":"gen3","at":"3.24"}}'),
      [`${BASE}/S1/state.json`]: prior({ agentId: 'OLD', lastTurnAt: OCT - 1_000, ctx: 1_110 }),
    }, { PA_MOD_ROTATE_AT: '1000' })
    const n = net(on)
    n.list = [{ id: 'OLD', status: 'idle' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'go on', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.spawns[0] ?? '').toContain("The project's first run in PA 3.2 sorts its memories first")
  })
})

/** The developer's side of the dialogs (T8c): the prompt box, plan mode's tools, the picker, the pane, the log. */
type Dialog = {
  draft: string
  /**
   * Each plan-mode or picker call, in order: `enter`, `learn` (an exit in plan mode with no plan file written since the
   * enter), `exit`, `exit-out` (outside plan mode), `ask:<question>`, `open:<id>`, `close:<id>`.
   */
  calls: string[]
  /** ExitPlanMode in plan mode after a plan file was written answers this. */
  exit: unknown
  enter: unknown
  /** The picker's answer; null: dismissed (it rejects). */
  answer: string | null
  logs: string[]
  inPlan: boolean
  /** The picker holds its answer until this resolves (a dialog left open). */
  gate: Promise<void> | null
  /** What `ui.open` answers: placed, or waiting undrawn (below 144 columns). */
  placed: boolean
  /** `logs.length` when the picker was asked. */
  logsAtAsk: number
  /** 3.23 T13b: state's `learning` flag at each ExitPlanMode in plan mode, `learn:<flag>` or `exit:<flag>`. */
  checks: string[]
  /** 3.23 T13b: the ExitPlanMode mock first passes the call down (`await next(e)`), to the kit's core beneath. */
  down: boolean
}

const LEARNED = { result: { plan: null, isAgent: false, filePath: 'C:\\Users\\u\\.claude\\plans\\wild-petting-raccoon.md' }, text: 'User has approved exiting plan mode. You can now proceed.' }
const PLANFILE = '/Users/u/.claude/plans/wild-petting-raccoon.md' // as norm() keys it
const APPROVED_EXIT = { result: { plan: '# P\n', isAgent: false, filePath: 'C:\\Users\\u\\.claude\\plans\\wild-petting-raccoon.md' }, text: 'User has approved your plan. You can now start coding.' }
/** ExitPlanMode outside plan mode on 2.1.293 (T11, 7e778bd0). */
const NOT_IN_PLAN = { result: 'Error: You are not in plan mode. To enter plan mode, call the EnterPlanMode tool first.', text: '<tool_use_error>Error: You are not in plan mode.</tool_use_error>', isError: true }
const questionOf = (e: unknown): string => ((e as { questions?: { question?: string }[] }).questions ?? [])[0]?.question ?? ''

function dialog(on: On, w?: World): Dialog {
  const d: Dialog = {
    draft: '', calls: [], exit: APPROVED_EXIT, enter: { result: { message: 'Entered plan mode.' }, text: 'Entered plan mode.' },
    answer: null, logs: [], inPlan: false, gate: null, placed: true, logsAtAsk: -1, checks: [], down: false,
  }
  let mark = 0
  on('prompt.read', async () => ({ value: { text: d.draft, cursor: d.draft.length } }))
  on('tool.call', { tool: 'EnterPlanMode' }, async () => {
    d.calls.push('enter')
    d.inPlan = (d.enter as { deny?: string }).deny === undefined
    mark = w?.writes.length ?? 0
    return d.enter as never
  })
  on('tool.call', { tool: 'ExitPlanMode' }, async ($, e, next) => {
    if (d.down) {
      await next(e).catch(() => undefined)
    }
    if (!d.inPlan) {
      d.calls.push('exit-out')
      return NOT_IN_PLAN as never
    }
    const wrote = (w?.writes.slice(mark) ?? []).some(p => p.includes('/.claude/plans/'))
    const flag = JSON.parse(w?.files.get(`${BASE}/S1/state.json`) ?? '{}').learning === true
    // 3.24 T13F: an allowed exit after the plan was written leaves plan mode (`leave`); a refused one stays in it.
    const kind = !wrote ? 'learn' : flag ? 'leave' : 'exit'
    d.calls.push(kind)
    d.checks.push(`${kind}:${String(flag)}`)
    const out = kind === 'exit' ? d.exit : kind === 'leave' ? APPROVED_EXIT : LEARNED
    d.inPlan = kind === 'exit' && ((out as { deny?: string }).deny !== undefined || (out as { isError?: boolean }).isError === true)
    return out as never
  })
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e) => {
    const q = questionOf(e)
    d.calls.push(`ask:${q}`)
    d.logsAtAsk = d.logs.length
    if (d.gate !== null) {
      await d.gate
      d.gate = null
    }
    return (d.answer === null ? { deny: 'dismissed' } : { result: { questions: [], answers: { [q]: d.answer } } }) as never
  })
  on('ui.open', async ($, e) => {
    d.calls.push(`open:${e.id}`)
    return { value: d.placed ? { isPlaced: true } : { isPlaced: false, reason: 'unasked below 144 columns (110 now)' } }
  })
  on('ui.close', async ($, e) => {
    d.calls.push(`close:${e.id}`)
    return { value: undefined }
  })
  on('ui.log', async ($, e) => {
    d.logs.push(e.text)
    return { value: undefined }
  })
  return d
}

const inboxReq = (ms: number, seq: number, kind: string, input: unknown): [string, string] =>
  [`${BASE}/S1/inbox/${String(ms)}-${String(seq)}-${kind}.json`, JSON.stringify({ id: `${String(ms)}-${String(seq)}-${kind}`, kind, caller: 'P1', input, at: ms })]
const PLAN_MD = `${ROOT}/.run/plans/p.md`
const P1 = (): string => prior({ agentId: 'P1', lastTurnAt: OCT - 1_000 })

describe('router plans and asks (T8c)', () => {
  test('path 0: learn once, enter, write, exit; the decision goes to the caller and the journal', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: '.run/plans/p.md', title: 'Add a flag' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: '# Add a flag\n\n1. Do it.\n', [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(d.calls).toEqual(['enter', 'learn', 'enter', 'exit'])
    // 3.23 T13b: `learning` is up just around the learning exit, down for the real one and after.
    expect(d.checks).toEqual(['learn:true', 'exit:false'])
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').learning).toBe(false)
    expect(w.files.get(PLANFILE)).toBe('# Add a flag\n\n1. Do it.\n')
    expect(d.logs).toEqual([]) // 3.23 T13d.c3: no `▣ plan presented` line
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: approved'])
    const plan = w.files.get(entries(w, 'mod-plan')[0] ?? '') ?? ''
    expect(plan).toContain('\nagentId: P1\n')
    expect(plan.endsWith('---\n\nAdd a flag\n.run/plans/p.md\n\n# Add a flag\n\n1. Do it.\n')).toBe(true)
    expect(w.files.get(entries(w, 'dev-decision')[0] ?? '')?.endsWith('\n\nPlan decision: approved\n')).toBe(true)
    expect(debugKinds(w)).toContain('pa.plan.learn')
    expect((JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { planFile: unknown }).planFile)
      .toEqual({ sid: 'S1', path: LEARNED.result.filePath })

    // The second plan of the session skips the learn step; feedback comes back as changes asked.
    d.exit = { deny: "The user doesn't want to proceed with this tool use. To tell you how to proceed, the user said:\nSplit step 1." }
    const [r2, b2] = inboxReq(OCT + 2_500, 2, 'plan', { path: PLAN_MD, title: 'Again' })
    w.files.set(r2, b2)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'ok', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(1_000)
    await clock.advance(1_000)
    // 3.24 T13F: changes asked: one more exit, flag up, leaves plan mode.
    expect(d.calls).toEqual(['enter', 'learn', 'enter', 'exit', 'enter', 'exit', 'leave'])
    expect(d.checks).toEqual(['learn:true', 'exit:false', 'exit:false', 'leave:true'])
    expect(d.inPlan).toBe(false)
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').learning).toBe(false)
    expect(debugKinds(w)).toContain('pa.plan.leave')
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: approved', 'Plan decision: changes asked: Split step 1.'])
  })

  test('3.24 T13F: a 4,926-char Milestone line is presented wrapped; the draft is unchanged and installed', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const milestone = `Milestone: ${Array.from({ length: 94 }, (_, i) => `check ${String(i)} passes on the bed with its evidence logged`).join('; ')}.`
    const draft = `# Phase\n${milestone}\n\n1. Do it.\n`
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: PLAN_MD, title: 'Phase', planKind: 'phase' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: draft, [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(milestone.length).toBeGreaterThan(4096)
    expect(d.calls).toEqual(['enter', 'learn', 'enter', 'exit'])
    const shown = w.files.get(PLANFILE) ?? ''
    expect(Math.max(...shown.split('\n').map(l => l.length))).toBeLessThanOrEqual(4000)
    expect(shown.replace(/\n(?=check )/g, ' ')).toBe(draft)
    expect(w.files.get(PLAN_MD)).toBe(draft)
    expect(debugKinds(w)).toContain('pa.plan.wrap')
  })

  test('deferred while an agent runs, then presented', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: '.run/plans/p.md', title: 'T' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [PLAN_MD]: 'x', [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'C1', status: 'running' }]
    const d = dialog(on, w)
    await $.session.start(START)
    await clock.advance(3_000)
    expect(d.calls).toEqual([])
    expect(debugKinds(w).filter(k => k === 'pa.present.defer').length).toBe(1)
    n.list = [{ id: 'C1', status: 'completed' }]
    await clock.advance(1_000)
    expect(d.calls).toEqual(['enter', 'learn', 'enter', 'exit'])
  })

  for (const learning of [true, false]) {
    test(`the learning exit (T13b): learning ${String(learning)}: ${learning ? 'allowed' : 'the real exit keeps its dialog'}`, async ($, on) => {
      const st = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1', lastTurnAt: OCT - 1_000 }, learning })
      world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: st }, {})
      net(on)
      on('tool.check', async () => ({ decision: 'ask', reason: 'core: the plan dialog' })) // core beneath the plugin
      mock.clock(on, { now: OCT })
      await $.session.start(ISTART)
      expect((await $.tool.check({ tool: 'ExitPlanMode', input: {} })).decision).toBe(learning ? 'allow' : 'ask')
    })
  }

  test('3.24 T17h: a `.` hand-back is refused (no ping in flight); a report, or the mod off, keeps the verdict beneath', async ($, on) => {
    const st = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1', lastTurnAt: OCT - 1_000 } })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: st }, {})
    net(on)
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    mock.clock(on, { now: OCT })
    expect((await $.tool.check({ tool: 'SubagentHandback', input: { message: '.' } })).decision).toBe('allow') // mod not started
    await $.session.start(ISTART)
    const dot = await $.tool.check({ tool: 'SubagentHandback', input: { message: '.' } })
    expect(dot.decision).toBe('deny')
    expect(dot.reason).toMatch(/one final report/)
    expect((await $.tool.check({ tool: 'SubagentHandback', input: { message: 'Review: 1. the closer blocks' } })).decision).toBe('allow')
  })

  test('3.26 T2: the partner\'s `.` hand-back passes; another agent\'s is refused unless pinged', async ($, on) => {
    const st = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1', lastTurnAt: OCT - 1_000 },
      warmAgents: { E1: { role: 'pa-expert' }, E2: { role: 'pa-expert', pingInFlight: true } } })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: st }, {})
    net(on)
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    mock.clock(on, { now: OCT })
    await $.session.start(ISTART)
    const dot = (agentId: string) => $.tool.check({ tool: 'SubagentHandback', agentId, input: { message: '.' } })
    expect((await dot('P1')).decision).toBe('allow')
    expect((await dot('E1')).decision).toBe('deny')
    expect((await dot('E2')).decision).toBe('allow')
  })

  test('the learning exit (T13b.c2): the kit raises no tool.check beneath the router\'s own ExitPlanMode calls', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: '.run/plans/p.md', title: 'Add a flag' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: '# A\n', [r1]: b1 }, {})
    net(on).list = [{ id: 'P1', status: 'idle' }]
    const seen: string[] = []
    on('tool.check', async ($, e) => {
      seen.push(String((e as { tool?: string }).tool))
      return { decision: 'ask', reason: 'core' }
    })
    const d = dialog(on, w)
    d.down = true // the mock passes each call to the kit's core first
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    // The real exit runs with the flag down, so the router's hook would pass a check on: none comes.
    expect(d.checks).toEqual(['learn:true', 'exit:false'])
    expect(seen).toEqual([])
  })

  test('deferred while the prompt box holds a draft', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'ask', { question: 'A or B?', options: ['A', 'B'] })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [r1]: b1 }, {})
    net(on)
    const d = dialog(on)
    d.draft = 'half a thought'
    d.answer = 'A'
    await $.session.start(START)
    await clock.advance(3_000)
    expect(d.calls).toEqual([])
    d.draft = ''
    await clock.advance(1_000)
    expect(d.calls).toEqual(['ask:A or B?'])
  })

  test('one dialog at a time: a second waits for the first to be answered', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'ask', { question: 'First?', options: ['A', 'B'] })
    const [r2, b2] = inboxReq(OCT - 500, 2, 'ask', { question: 'Second?', options: ['C', 'D'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [r1]: b1, [r2]: b2 }, {})
    net(on)
    const d = dialog(on, w)
    d.answer = 'A'
    let release: () => void = () => undefined
    d.gate = new Promise<void>(res => {
      release = res
    })
    await $.session.start(START)
    await clock.advance(3_000)
    expect(d.calls).toEqual(['ask:First?'])
    expect((JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { present: unknown[] }).present.length).toBe(1)
    release()
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(d.calls).toEqual(['ask:First?', 'ask:Second?'])
  })

  test('the fallback when EnterPlanMode is denied: pane, picker, zero tokens', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: PLAN_MD, title: 'T' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: 'the plan', [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    d.enter = { deny: 'plan mode is not available' }
    d.answer = 'Make it smaller.'
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(d.calls).toEqual(['enter', 'open:pa-plan', 'ask:Approve this plan?', 'close:pa-plan'])
    expect(debugKinds(w)).toContain('pa.plan.fallback')
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: changes asked: Make it smaller.'])
    expect(n.apiSteps).toBe(0)
  })

  test('path 0 by the transcript slug: no learning exit', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: PLAN_MD, title: 'Slugged' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: PY, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: 'the plan', [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    n.run = argv => argv[1] === '-c' && argv[3] === 'C:\\t\\S1.jsonl'
      ? { exitCode: 0, stdout: 'C:\\Users\\u\\.claude\\plans\\cozy-snuggling-bonbon.md\n', stderr: '' }
      : { exitCode: 2, stdout: '', stderr: 'invalid choice' }
    const d = dialog(on, w)
    await $.classic.SessionStart({ source: 'startup', session_id: 'S1', transcript_path: 'C:\\t\\S1.jsonl' } as never)
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(d.calls).toEqual(['enter', 'exit'])
    expect(w.files.get('/Users/u/.claude/plans/cozy-snuggling-bonbon.md')).toBe('the plan')
    expect(debugKinds(w)).toContain('pa.plan.slug')
    expect(debugKinds(w)).not.toContain('pa.plan.learn')
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: approved'])
  })

  test('the fallback pane not placed: the plan goes to the transcript before the picker', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'plan', { path: PLAN_MD, title: 'T' })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: '# T\r\n\n1. One.\n', [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    d.enter = { deny: 'plan mode is not available' }
    d.placed = false
    d.answer = 'Approve'
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(d.calls).toEqual(['enter', 'open:pa-plan', 'ask:Approve this plan?', 'close:pa-plan'])
    expect(d.logs).toEqual(['▣ plan: T', '# T', '', '1. One.'])
    expect(d.logsAtAsk).toBe(4)
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: approved'])
  })

  test('an ask answered and an ask dismissed, each journaled', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'ask', { question: 'A or B?', options: ['A', 'B'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [r1]: b1 }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    d.answer = 'B'
    await $.session.start(ISTART)
    await clock.advance(1_000)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Answer: B'])
    expect(w.files.get(entries(w, 'mod-prompt')[0] ?? '')?.endsWith('\n\nA or B?\n- A\n- B\n')).toBe(true)
    expect(w.files.get(entries(w, 'dev-decision')[0] ?? '')?.endsWith('\n\nAnswer: B\n')).toBe(true)

    d.answer = null
    const [r2, b2] = inboxReq(OCT + 2_500, 2, 'ask', { question: 'C or D?', options: ['C', 'D'] })
    w.files.set(r2, b2)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'ok', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(1_000)
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(n.sends.map(s => s.text)).toEqual(['Answer: B', 'Answer: dismissed'])
    expect(entries(w, 'dev-decision').length).toBe(2)
  })
})

// 3.23 T5d2b: the loop's expert warmed by the router's tick (loop.json read only, the watcher's facts for ctx and model).
type WarmA = { pings: number; pingUsd: number; avoidedUsd: number; pingInFlight: boolean; waitKind: string; role: string }
const warmA = (w: World, id: string): WarmA | undefined =>
  (JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { warmAgents?: Record<string, WarmA> }).warmAgents?.[id]
const loopFile = (task: Record<string, unknown>, holds: string[] = []): string =>
  JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: OCT, state: 'running', holds, task: {
    id: 'T1', title: 't', agent: 'expert-opus55', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task', agentId: 'E1',
    startSha: 'abc', spawnedAt: OCT - 60 * 60_000, lastCompleteAt: OCT - 56 * 60_000, contract: null, ...task,
  } })
const RATES = { input: 5e-6, write_5m: 6.25e-6, write_1h: 10e-6, read: 0.5e-6, output: 25e-6 }
const expertWorld = (on: On, loop: string, ctx: number, env: Record<string, string> = {}): World => world(on, {
  [`${ROOT}/.claude/pa.json`]: PY,
  [`${BASE}/S1/state.json`]: JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: null }),
  [`${BASE}/loop.json`]: loop,
  [`${BASE}/S1/agents/E1.json`]: JSON.stringify({ model: 'claude-opus-5-5', effort: 'medium', ctx, at: OCT - 56 * 60_000 }),
}, { USERPROFILE: 'H', ...env })

describe('router warms the loop expert (3.23 T5d2b)', () => {
  test('a child out: the event spooled first with role and id, then the ping; the . turn lifts the hold', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = expertWorld(on, loopFile({ childBusy: true }), 80_000)
    const n = net(on)
    n.list = [{ id: 'E1', status: 'idle' }, { id: 'C1', status: 'running', parentId: 'E1' }]
    n.run = argv => (argv.includes('prices') ? { exitCode: 0, stdout: PRICES, stderr: '' } : { exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(60_000)
    expect(lib(n.order)).toEqual(['run:prices', 'run:event', 'send:.'])
    expect(n.sends).toEqual([{ to: 'E1', text: '.' }])
    const ev = libRuns(n)[1] ?? []
    expect(ev.slice(0, 9)).toEqual(['py', 'H/.claude/pa3/pa_ledger.py', 'event', 'warm_ping', '--session', 'S1', '--run', 'E1', '--detail'])
    const detail = JSON.parse(ev[9] ?? '{}') as Record<string, unknown>
    expect([detail['run_id'], detail['role'], detail['task'], detail['ttl'], detail['via'], detail['n']]).toEqual(['E1', 'expert-opus55', 'T1', '1h', 'mod', 1])
    const a = warmA(w, 'E1')
    expect([a?.pings, a?.pingInFlight, a?.waitKind, a?.role]).toEqual([1, true, 'child', 'expert-opus55'])
    expect(Math.abs((a?.avoidedUsd ?? 0) - 80_000 * (10 - 0.5) / 1e6) < 1e-9).toBe(true)
    expect(Math.abs((a?.pingUsd ?? 0) - pingUsd(80_000, RATES)) < 1e-9).toBe(true)
    await clock.advance(60_000)
    expect(n.sends.length).toBe(1) // in flight: no second ping
    await $.turn.complete({ turnId: 'X1', agentId: 'E1', answer: '.', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
    expect(warmA(w, 'E1')?.pingInFlight).toBe(false)
    expect(stateOf(w).warm.pings).toBe(0) // the partner's totals untouched
  })

  test('held on a question: rent-or-buy, pings stop once they cost a fresh start', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const ctx = 200_000
    const w = expertWorld(on, loopFile({ lastCompleteAt: OCT - 3 * 60_000 }, ['question']), ctx, { PA_MOD_PING_AFTER_MS: '120000' })
    const n = net(on)
    n.list = [{ id: 'E1', status: 'idle' }]
    n.run = argv => (argv.includes('prices') ? { exitCode: 0, stdout: PRICES, stderr: '' } : { exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    for (let i = 0; i < 14; i += 1) {
      const before = n.sends.length
      await clock.advance(60_000)
      if (n.sends.length > before) {
        await $.turn.complete({ turnId: `X${String(i)}`, agentId: 'E1', answer: '.', durationMs: 1, isAborted: false, reason: 'answer', usage: USAGE })
      }
    }
    const k = Math.floor(freshStartUsd(SEED_BUDGET_TOKENS, 0, RATES) / pingUsd(ctx, RATES))
    expect(k).toBe(3)
    expect(n.sends.length).toBe(k)
    expect(warmA(w, 'E1')?.waitKind).toBe('question')
  })

  test('no ping while the expert works (running, pending) or outside a wait', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = expertWorld(on, loopFile({ childBusy: true }, ['question']), 80_000)
    const n = net(on)
    n.list = [{ id: 'E1', status: 'running' }, { id: 'C1', status: 'running', parentId: 'E1' }]
    n.run = argv => (argv.includes('prices') ? { exitCode: 0, stdout: PRICES, stderr: '' } : { exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(60_000)
    n.list = [{ id: 'E1', status: 'pending' }]
    await clock.advance(60_000)
    w.files.set(`${BASE}/loop.json`, loopFile({}))
    n.list = [{ id: 'E1', status: 'idle' }]
    await clock.advance(60_000)
    expect(n.sends).toEqual([])
    expect(n.order.filter(o => o === 'run:event')).toEqual([])
  })

  test('a failed delivery clears the flag and counts nothing', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const w = expertWorld(on, loopFile({ childBusy: true }), 80_000)
    const n = net(on)
    n.deliver = false
    n.list = [{ id: 'E1', status: 'idle' }, { id: 'C1', status: 'running', parentId: 'E1' }]
    n.run = argv => (argv.includes('prices') ? { exitCode: 0, stdout: PRICES, stderr: '' } : { exitCode: 0, stdout: '{}', stderr: '' })
    await $.session.start(ISTART)
    await clock.advance(60_000)
    expect(lib(n.order)).toEqual(['run:prices', 'run:event', 'send:.'])
    const a = warmA(w, 'E1')
    expect([a?.pingInFlight, a?.pings, a?.pingUsd]).toEqual([false, 0, 0])
  })
})

// 3.23 T6a1: the presentation tick reads loop.json (a dialog only while the loop is off or held) and flags dialog.json
// open meanwhile; an approved generation or phase plan becomes the loop's `install`, the loop's own ask its `answer`;
// the decision line goes to the request's caller; PA_MOD_TRIAL_APPROVE=1 answers with no dialog.
const callerReq = (ms: number, seq: number, kind: string, caller: string, input: unknown): [string, string] =>
  [`${BASE}/S1/inbox/${String(ms)}-${String(seq)}-${kind}.json`, JSON.stringify({ id: `${String(ms)}-${String(seq)}-${kind}`, kind, caller, input, at: ms })]
const loopState = (state: string, holds: string[] = []): string => JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: OCT, state, holds, task: null })
const inboxOf = (w: World, kind: string): { caller: string; input: unknown }[] => [...w.files.keys()]
  .filter(p => p.startsWith(`${BASE}/S1/inbox/`) && p.endsWith(`-${kind}.json`)).sort()
  .map(p => JSON.parse(w.files.get(p) ?? '{}') as { caller: string; input: unknown })

describe('plans via the loop (T6a1)', () => {
  test('no dialog while loop.json is running; held, the ask shows with dialog.json open, then closed', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = inboxReq(OCT - 500, 1, 'ask', { question: 'A or B?', options: ['A', 'B'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/loop.json`]: loopState('running'), [r1]: b1 }, { PA_MOD_DEBUG: '1' })
    net(on)
    const d = dialog(on, w)
    d.answer = 'A'
    let release: () => void = () => undefined
    d.gate = new Promise<void>(res => {
      release = res
    })
    await $.session.start(START)
    await clock.advance(3_000)
    expect(d.calls).toEqual([])
    expect(debugKinds(w)).toContain('pa.present.defer')
    w.files.set(`${BASE}/loop.json`, loopState('boundary', ['talk']))
    await clock.advance(2_000)
    expect(d.calls).toEqual([])
    w.files.set(`${BASE}/loop.json`, loopState('boundary', ['question']))
    await clock.advance(1_000)
    expect(d.calls).toEqual(['ask:A or B?'])
    expect(JSON.parse(w.files.get(`${BASE}/S1/dialog.json`) ?? '{}')).toMatchObject({ open: true, id: `${String(OCT - 500)}-1-ask` })
    release()
    await clock.advance(1_000)
    expect(JSON.parse(w.files.get(`${BASE}/S1/dialog.json`) ?? '{}')).toMatchObject({ open: false })
  })

  test('PA_MOD_TRIAL_APPROVE=1: no dialog; a phase plan becomes install, the loop\'s ask an answer, an adhoc plan a reply', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = callerReq(OCT - 500, 1, 'plan', 'P1', {
      path: '.run/plans/p.md', kind: 'phase', title: 'Phase 1.1', rules: ['M13: never push'], candidates: ['Tool candidate: x | does: y'],
    })
    const [r2, b2] = callerReq(OCT - 500, 2, 'ask', 'loop', { question: 'Add this rule? M13: never push', options: ['add (Recommended)', 'reject'] })
    const [r3, b3] = callerReq(OCT - 500, 3, 'plan', 'P1', { path: '.run/plans/p.md', title: 'Small fix' })
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [PLAN_MD]: '# Phase 1.1\n', [r1]: b1, [r2]: b2, [r3]: b3,
    }, { PA_MOD_TRIAL_APPROVE: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    const d = dialog(on, w)
    await $.session.start(ISTART)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(1_000)
    }
    expect(d.calls).toEqual([])
    expect(inboxOf(w, 'install')).toMatchObject([{ caller: 'P1', input: {
      kind: 'phase', path: '.run/plans/p.md', title: 'Phase 1.1', rules: ['M13: never push'], candidates: ['Tool candidate: x | does: y'],
    } }])
    expect(inboxOf(w, 'answer')).toMatchObject([{ caller: 'router', input: { id: `${String(OCT - 500)}-2-ask`, line: 'Answer: add (Recommended)' } }])
    expect(n.sends.map(s => s.text)).toEqual(['Plan decision: approved'])
    expect(entries(w, 'dev-decision').length).toBe(3)
    expect(entries(w, 'mod-plan').length).toBe(2)
  })

  test('3.24 T7b-2c: a pending first run: a plan is not presented nor sent to install; Claude is told why; an ask still shows', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = callerReq(OCT - 500, 1, 'plan', 'P1', { path: '.run/plans/p.md', kind: 'phase', title: 'Phase 1.1' })
    const [r2, b2] = callerReq(OCT - 500, 2, 'ask', 'loop', { question: 'Run it now?', options: ['Run it now (Recommended)', 'Pause'] })
    const w = world(on, {
      [`${ROOT}/.claude/pa.json`]: ON.replace('}', ',"first_run":{"from":"gen3","at":"3.24"}}'), [`${BASE}/S1/state.json`]: P1(),
      [PLAN_MD]: '# Phase 1.1\n', [r1]: b1, [r2]: b2,
    }, { PA_MOD_TRIAL_APPROVE: '1' })
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    dialog(on, w)
    await $.session.start(ISTART)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(1_000)
    }
    expect(inboxOf(w, 'install')).toEqual([])
    expect(entries(w, 'mod-plan').length).toBe(0)
    expect(n.sends.map(s => s.text)).toEqual([
      "Not presented: The project's first run in PA 3.2 sorts its memories first: no plan is presented or installed until the memory curator has run. The mod asks the developer itself, in its own picker; do not ask about the curator yourself. If they pause it, the mod asks again at the next session start. Relay the curator's recap when it arrives.",
    ])
    expect(inboxOf(w, 'answer')).toMatchObject([{ input: { line: 'Answer: Run it now (Recommended)' } }])
  })

  test('the decision line goes to its caller when that is a live agent other than the partner', async ($, on) => {
    const clock = mock.clock(on, { now: OCT })
    const [r1, b1] = callerReq(OCT - 500, 1, 'ask', 'X9', { question: 'A or B?', options: ['A', 'B'] })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: P1(), [r1]: b1 }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }, { id: 'X9', status: 'idle' }]
    const d = dialog(on, w)
    d.answer = 'B'
    await $.session.start(ISTART)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(1_000)
    }
    expect(n.sends).toEqual([{ to: 'X9', text: 'Answer: B' }])
  })
})

// 3.23 T8: the second-reply carrier; T13d.c11: the marker is the only one (the `notice` mode retired).
describe('the second-reply carrier (3.23 T8)', () => {
  const lateReply = async ($: Engine, on: On, appends: unknown[]): Promise<Net> => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, {})
    const n = net(on)
    n.list = [{ id: 'P1', status: 'idle' }]
    on('session.append', async ($, e, next) => {
      appends.push(e.message)
      return next(e)
    })
    await $.session.start(ISTART)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'Late fact.', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(6_000)
    return n
  }

  test('the marker prompt, no append', async ($, on) => {
    const appends: unknown[] = []
    const n = await lateReply($, on, appends)
    expect(n.submits).toEqual([MARK1])
    expect(appends).toEqual([])
  })
})

describe('cards (3.23 T13)', () => {
  const LOOP = JSON.stringify({ state: 'running', task: { id: 'T3', title: 'Parser', agentId: 'E1', kind: 'task' } })
  /** A loop expert E1 with a coder C1 and a retriever R1 under it; `git show` answers a two-line diff. */
  const cards = async ($: Engine, on: On, render = false, more: Record<string, string> = {}, env: Record<string, string> = {}): Promise<{ w: World; runs: string[][]; answer: (turnId: string, text: string) => Promise<string> }> => {
    mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior, [`${ROOT}/.run/mod/loop.json`]: LOOP, ...more }, env)
    const runs: string[][] = []
    on('process.run', async ($, e) => {
      runs.push([...e.argv])
      return { value: e.argv[1] === 'show' ? { exitCode: 0, stdout: '1\t1\tsrc/split.py\n', stderr: '' } : { exitCode: 2, stdout: '', stderr: 'no' } }
    })
    on('agent.list', async () => ({
      value: [
        { id: 'P1', status: 'idle', type: 'pa-claude', description: '' },
        { id: 'E1', status: 'running', type: 'expert-opus55', description: '' },
        { id: 'C1', status: 'completed', type: 'coder-opus55', parentId: 'E1', description: '' },
        { id: 'R1', status: 'completed', type: 'retriever-code', parentId: 'C1', description: '' },
      ],
    }))
    on('session.send', async () => ({ isDelivered: true }))
    on('prompt.submit', async ($, e) => ({ text: e.text, origin: e.origin }))
    on('turn.start', async ($, e) => ({ turnId: e.turnId }))
    on('turn.step', async function* ($, e) {
      yield { kind: 'text' as const, index: 0, text: 'from the api' }
      return { turnId: e.turnId, index: e.index, answer: 'from the api', toolUses: [], stopReason: 'end_turn' as const, usage: null }
    })
    if (render) {
      on('ui.render', async ($, e) => {
        const { Text } = $.ui.resolve(e)
        return Text({ children: 'engine' })
      })
    }
    await $.session.start(ISTART)
    const answer = async (turnId: string, text: string): Promise<string> => {
      await $.prompt.submit({ text, origin: { kind: 'peer' }, wait: false })
      await $.turn.start({ text, turnId })
      return (await drain($.turn.step({ turnId, index: 0, model: 'opus', messageCount: 1 }))).result.answer
    }
    return { w, runs, answer }
  }

  test("a coder's note: a card, never filed or forwarded; a loop expert's note: a card; a retriever's: the stub", async ($, on) => {
    const { w, answer } = await cards($, on)
    expect(await answer('T1', '<agent-message from="C1">\nT3.c1 approach: split on quotes.\n</agent-message>'))
      .toBe('**🟩 Coder · T3 · c1**\n\nT3.c1 approach: split on quotes.')
    expect(entries(w, 'coder-end')).toEqual([])
    expect(w.writes.some(p => p.includes('/inbox/'))).toBe(false)
    expect(stateOf(w).outbox).toEqual([])
    expect(await answer('T2', '<agent-message from="E1">\nApproach: a coder edits route.ts.\n</agent-message>'))
      .toBe('**🟦 Expert · T3**\n\nApproach: a coder edits route.ts.')
    expect(await answer('T3', '<agent-message from="R1">\nlooking\n</agent-message>')).toBe(SENT)
  })

  test("finishes: the expert's is its summary; a coder's its commit's files (3.24 T8b); a retriever's, a WAITING and a ping stay the stub", async ($, on) => {
    const { runs, answer } = await cards($, on)
    expect(await answer('T1', handback('C1', 'STATUS: done\nLOG: logs/T3.c1.md\nCOMMIT: 9f8e7d6\nSUMMARY: Split on quotes.')))
      .toBe('**🟩 ✓ Coder · T3 · c1, committed 9f8e7d6**\n\nSplit on quotes.\n\n- `src/split.py` +1 −1')
    expect(runs.filter(r => r[1] === 'show')).toEqual([['git', 'show', '--numstat', '--format=', '9f8e7d6']])
    expect(await answer('T2', handback('R1', 'STATUS: done\nCOMMIT: 1111111'))).toBe(SENT)
    expect(await answer('T3', handback('E1', 'WAITING: coder T3.c2'))).toBe(SENT)
    expect(await answer('T4', handback('E1', '.'))).toBe(SENT)
    expect(await answer('T5', handback('E1', 'STATUS: done\nCOMMIT: 1a2b3c4\nSUMMARY: Quotes parse.')))
      .toBe('**🟦 ✓ Expert · T3**\n\nQuotes parse.')
  })

  test('3.24 T17g: a notification that only points at a spent hand-back: the expert\'s last text is its done card', async ($, on) => {
    on('session.messages', async () => ({ value: [{ role: 'user', text: 'refused' }, { role: 'assistant', text: 'STATUS: done\nSUMMARY: The tests pass.\nCOMMIT: 5a7f0f7', toolUses: [] }] as never }))
    const { answer } = await cards($, on)
    const tn = '<task-notification>\n<task-id>E1</task-id>\n<status>completed</status>\n<result>This agent\'s report was delivered to you as a message from "E1" (its SubagentHandback call). Read it there; it is not repeated here.\n</result>\n</task-notification>'
    expect(await answer('T1', tn)).toBe('**🟦 ✓ Expert · T3**\n\nThe tests pass.')
  })

  test('3.23 T13d.c11: PA_MOD_DEBUG logs pa.answer, then one pa.draw per text with its kind and delay', async ($, on) => {
    const { w, answer } = await cards($, on, true, {}, { PA_MOD_DEBUG: '1' })
    const card = await answer('T1', '<agent-message from="E1">\nApproach: one edit.\n</agent-message>')
    for (const text of [card, card, `${card}\n`, 'Plain words of Claude.']) {
      const ui = await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text, isFirstOfReply: true } })
      await ui.drawn()
    }
    const evs = (kind: string): Record<string, unknown>[] => [...w.files].filter(([p]) => p.includes('/debug/') && p.endsWith(`-pa.${kind}.json`))
      .map(([, t]) => JSON.parse(t) as Record<string, unknown>)
    expect(evs('answer')).toEqual([expect.objectContaining({ kind: 'card', turnId: 'T1', head: card.slice(0, 60) })])
    const draws = evs('draw')
    expect(draws.map(d => [d.kind, d.head])).toEqual([['card', card.slice(0, 60)], ['other', 'Plain words of Claude.']])
    expect(typeof draws[0]?.delayMs).toBe('number')
    expect(draws[1]).not.toHaveProperty('delayMs')
  })

  test('the registry: a card draws on terminal and desktop, a look-alike reply of Claude does not', async ($, on) => {
    const { answer } = await cards($, on, true)
    const card = await answer('T1', '<agent-message from="E1">\nApproach: one edit.\n</agent-message>')
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: 'pa', surface, component: 'AssistantMessage', props: { text: card, isFirstOfReply: true } })
      const drawn = JSON.stringify(await ui.drawn())
      expect(drawn).toContain('Expert · T3')
      expect(drawn).toContain('#0087ff')
      expect(drawn).toContain('quote')
      expect(drawn).not.toContain('engine')
      const fake = await $.ui.mount({ plugin: 'pa', surface, component: 'AssistantMessage', props: { text: '**🟦 Expert · T3**\n\nNot a card.', isFirstOfReply: true } })
      expect(JSON.stringify(await fake.drawn())).toContain('engine')
    }
  })

  test('3.23 T13d.c6a: cards and feed answers kept in drawn.json draw after a resume or restart; a new card is added', async ($, on) => {
    const CARD = '**🟦 Expert · T3**\n\nApproach: kept.'
    const FED = '**🟩 Coder · T3**\n Kept line.'
    const kept = JSON.stringify({ cards: [CARD], feedRuns: [{ text: FED, runs: [{ label: 'Coder · T3', role: 'coder', titled: true, lines: ['Kept line.'] }] }] })
    const { w, answer } = await cards($, on, true, { [`${BASE}/drawn.json`]: kept })
    const card = JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text: CARD, isFirstOfReply: true } })).drawn())
    expect(card).toContain('#0087ff')
    expect(card).not.toContain('engine')
    const fed = JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text: FED, isFirstOfReply: true } })).drawn())
    expect(fed).toContain('#00af00')
    expect(fed).not.toContain('engine')
    const next = await answer('T1', '<agent-message from="E1">\nApproach: one edit.\n</agent-message>')
    expect((JSON.parse(w.files.get(`${BASE}/drawn.json`) ?? '{}') as { cards: string[] }).cards).toEqual([CARD, next])
  })

  test('card_style plain: no border, the bullet and the colored title', { options: { card_style: 'plain' } }, async ($, on) => {
    const { answer } = await cards($, on)
    const card = await answer('T1', handback('E1', 'STATUS: done\nSUMMARY: Done it.'))
    const drawn = JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text: card, isFirstOfReply: true } })).drawn())
    expect(drawn).toContain('● ✓ Expert · T3')
    expect(drawn).not.toContain('round')
    expect(drawn).not.toContain('quote')
  })
})

describe('the live feed (3.23 T13d)', () => {
  const LOOP = JSON.stringify({ state: 'running', task: { id: 'T3', title: 'Parser', agentId: 'E1', kind: 'task' } })
  const FEED = `${BASE}/S1/feed`
  const TYPES: Record<string, string> = { P1: '', E1: 'expert-opus55', C1: 'coder-opus55', K1: 'thinker', D1: 'deep-thinker', G1: 'planner-phase' }
  /** A feed event line as the feed script prints it. */
  const rec = (n: number, agent: string, text: string, planning = false): string =>
    JSON.stringify({ pa: 'feed', n, agent, type: TYPES[agent] ?? '', desc: 'd', planning, text, at: n })
  /** A feed notification as Claude Code 2.1.295 delivers it. */
  const note = (...lines: string[]): string =>
    `<task-notification>\n<task-id>bm1</task-id>\n<summary>Monitor event: "pa feed"</summary>\n<event>${lines.join('\n')}</event>\n</task-notification>`
  const feed = async ($: Engine, on: On) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior, [`${BASE}/loop.json`]: LOOP }, { PA_MOD_DEBUG: '1' })
    const n = { sends: [] as string[], spawns: [] as string[], apiSteps: 0 }
    const list = [
      { id: 'P1', status: 'idle' }, { id: 'E1', status: 'running' }, { id: 'C1', status: 'running', parentId: 'E1' },
      { id: 'K1', status: 'running' }, { id: 'D1', status: 'running' }, { id: 'G1', status: 'running' },
    ]
    on('agent.list', async () => ({ value: list.map(a => ({ ...a, description: '', type: a.id === 'P1' ? 'pa-claude' : TYPES[a.id] ?? '' })) }))
    on('agent.spawn', async ($, e) => {
      n.spawns.push(e.prompt)
      return { agentId: 'P9', model: 'opus' }
    })
    on('session.send', async ($, e) => {
      n.sends.push(e.text)
      return { isDelivered: true }
    })
    on('prompt.submit', async ($, e) => ({ text: e.text, origin: e.origin }))
    on('turn.start', async ($, e) => ({ turnId: e.turnId }))
    on('turn.complete', async ($, e) => ({ text: e.answer }))
    on('turn.step', async function* ($, e) {
      n.apiSteps += 1
      yield { kind: 'text' as const, index: 0, text: 'from the api' }
      return { turnId: e.turnId, index: e.index, answer: 'from the api', toolUses: [], stopReason: 'end_turn' as const, usage: null }
    })
    await $.session.start(ISTART)
    let t = 0
    const submit = async (text: string, turnId?: string): Promise<void> => {
      await $.prompt.submit({ text, origin: { kind: 'task-notification' }, wait: false, ...(turnId === undefined ? {} : { turnId }) })
    }
    /** One main turn of `text` (a feed turn when it is a feed notification), its local answer. */
    const turn = async (text: string): Promise<string> => {
      t += 1
      const turnId = `T${String(t)}`
      await $.turn.start({ text, turnId })
      const a = (await drain($.turn.step({ turnId, index: 0, model: 'opus', messageCount: 1 }))).result.answer
      await $.turn.complete({ turnId, answer: a, durationMs: 1, isAborted: false, reason: 'answer' })
      return a
    }
    const answer = async (text: string): Promise<string> => {
      await submit(text)
      return turn(text)
    }
    return { w, n, clock, submit, turn, answer }
  }

  test('a feed notification is not forwarded, filed, carded or sent; its turn draws the line', async ($, on) => {
    const { w, n, clock, answer } = await feed($, on)
    expect(await answer(note(rec(1, 'C1', 'Adding the operator.')))).toBe('**🟩 Coder · T3**\n> Adding the operator.')
    const st = JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { journal: unknown[]; forwarded: Record<string, number> }
    expect(st.journal).toEqual([])
    expect(st.forwarded).toEqual({})
    await clock.advance(2_000)
    expect(n.sends).toEqual([])
    expect(n.spawns).toEqual([])
    expect(n.apiSteps).toBe(0)
    expect(stateOf(w).outbox).toEqual([])
    expect(entries(w, 'coder-end')).toEqual([])
    expect(w.writes.some(p => p.includes('/inbox/'))).toBe(false)
    expect(debugKinds(w)).toContain('pa.feed.in')
    expect(debugKinds(w)).not.toContain('pa.card')
    expect((JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { feed?: { dir: string } }).feed?.dir).toBe('.run/mod/S1/feed')
  })

  test('3.24 T13B: a reply with no hand-back goes as a feed reply record while the feed lives; its feed turn draws a Claude card', async ($, on) => {
    const { w, clock, answer } = await feed($, on)
    w.files.set(`${FEED}/alive`, JSON.stringify({ pid: 7, started: 100, beat: 1_000, n: 0 }))
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'Unasked news.', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(6_000)
    expect(markerModes(w)).toEqual(['feed'])
    const [path] = [...w.files.keys()].filter(p => p.startsWith(`${FEED}/in/`) && /-7\d{5}\.json$/.test(p))
    const body = JSON.parse(w.files.get(path ?? '') ?? '{}') as Record<string, unknown>
    expect([body['agent'], body['type'], body['reply'], body['text']]).toEqual(['P1', '', 1, 'Unasked news.'])
    expect(stateOf(w).markers['1']).toMatch(/-claude-answer\.md$/)
    const text = await answer(note(JSON.stringify({ pa: 'feed', n: 1, ...body })))
    expect(text).toBe(CLAUDE('Unasked news.', 'opus')) // 3.24 T17g
    const drawn = JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text, isFirstOfReply: true } })).drawn())
    expect(drawn).toContain('#d7875f')
    expect(drawn).toContain('Unasked news.')
  })

  test('3.24 T13B: no live feed (no heartbeat): the marker prompt, its turn a Claude card', async ($, on) => {
    const { w, clock } = await feed($, on)
    await $.turn.complete({ turnId: 'A1', agentId: 'P1', answer: 'Unasked news.', durationMs: 9, isAborted: false, reason: 'answer', usage: USAGE })
    await clock.advance(6_000)
    expect(markerModes(w)).toEqual(['marker'])
    expect([...w.files.keys()].some(p => p.startsWith(`${FEED}/in/`))).toBe(false)
  })

  // 3.24 T13E: the loop's `card` request (the review release's accepted card): journaled, drawn as itself at zero tokens.
  const ACCEPTED = '**🟦 ✓ Expert · T2 · accepted**'
  const cardReq = (ms: number): [string, string] => {
    const id = `${String(ms)}-1-card`
    return [`${BASE}/S1/inbox/${id}.json`, JSON.stringify({ id, kind: 'card', caller: 'loop', input: { text: ACCEPTED }, at: ms })]
  }

  test('3.24 T13E: a loop card, no feed: journaled, the marker prompt, its turn answers the card itself', async ($, on) => {
    const { w, n, clock, turn } = await feed($, on)
    w.files.set(...cardReq(500))
    await clock.advance(2_000)
    expect(debugKinds(w)).toContain('pa.inbox.card')
    const entry = [...w.files.keys()].find(p => p.endsWith('-mod-card.md'))
    expect(w.files.get(entry ?? '')).toContain(ACCEPTED)
    expect(stateOf(w).markers['1']).toBe(entry)
    expect(markerModes(w)).toEqual(['marker'])
    expect(await turn(MARK1)).toBe(ACCEPTED)
    expect(n.apiSteps).toBe(0)
    const drawn = JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text: ACCEPTED, isFirstOfReply: true } })).drawn())
    expect(drawn).toContain('Expert · T2 · accepted')
    expect(drawn).not.toContain('Claude')
  })

  test('3.24 T13E: a loop card while the feed lives: a feed reply record, drawn as the card, not a Claude card', async ($, on) => {
    const { w, clock, answer } = await feed($, on)
    w.files.set(`${FEED}/alive`, JSON.stringify({ pid: 7, started: 100, beat: 2_500, n: 0 }))
    w.files.set(...cardReq(500))
    await clock.advance(2_000)
    expect(markerModes(w)).toEqual(['feed'])
    const [path] = [...w.files.keys()].filter(p => p.startsWith(`${FEED}/in/`) && /-7\d{5}\.json$/.test(p))
    const body = JSON.parse(w.files.get(path ?? '') ?? '{}') as Record<string, unknown>
    expect(body['text']).toBe(ACCEPTED)
    expect(await answer(note(JSON.stringify({ pa: 'feed', n: 1, ...body })))).toBe(ACCEPTED)
  })

  test('two queued notifications drain both records in one answer; a duplicate draws once', async ($, on) => {
    const { submit, turn, answer } = await feed($, on)
    await submit(note(rec(1, 'E1', 'Reading the plan.')))
    await submit(note(rec(1, 'E1', 'Reading the plan.')))
    const last = note(rec(2, 'E1', 'Briefing a coder.'))
    await submit(last)
    expect(await turn(last)).toBe('**🟦 Expert · T3**\n> Reading the plan.\n>\n> Briefing a coder.')
    expect(await answer(note(rec(2, 'E1', 'Briefing a coder.')))).toBe(SENT)
  })

  test('a skipped number is read from log.jsonl', async ($, on) => {
    const { w, answer } = await feed($, on)
    w.files.set(`${FEED}/log.jsonl`, `${[1, 2, 3].map(i => rec(i, 'C1', `line ${String(i)}`)).join('\n')}\n`)
    expect(await answer(note(rec(1, 'C1', 'line 1')))).toBe('**🟩 Coder · T3**\n> line 1')
    expect(await answer(note(rec(3, 'C1', 'line 3')))).toBe('> line 2\n>\n> line 3')
  })

  test('3.23 T13d.c6a: a record line Claude Code cut (the last of its event) is read from log.jsonl at once', async ($, on) => {
    const { w, answer } = await feed($, on)
    w.files.set(`${FEED}/log.jsonl`, `${[1, 2].map(i => rec(i, 'C1', `line ${String(i)}`)).join('\n')}\n`)
    const cut = `${rec(2, 'C1', 'line 2').slice(0, 30)}…(truncated)`
    expect(await answer(note(rec(1, 'C1', 'line 1'), cut))).toBe('**🟩 Coder · T3**\n> line 1\n>\n> line 2')
    const fed = JSON.parse(w.files.get(`${BASE}/drawn.json`) ?? '{}') as { feedRuns: { text: string }[] }
    expect(fed.feedRuns.map(x => x.text)).toEqual(['**🟩 Coder · T3**\n> line 1\n>\n> line 2'])
  })

  test('a delivery into a running turn writes a nudge file; a nudge alone does not', async ($, on) => {
    const { w, submit } = await feed($, on)
    await submit(note(rec(1, 'E1', 'x')), 'T0')
    const nudges = [...w.files.keys()].filter(p => p.startsWith(`${FEED}/in/`))
    expect(nudges).toEqual([`${FEED}/in/0000000001000-900001.json`])
    expect(w.files.get(nudges[0] ?? '')).toBe('{"nudge":true}\n')
    await submit(note('{"pa":"feed","nudge":true}'), 'T0')
    expect([...w.files.keys()].filter(p => p.startsWith(`${FEED}/in/`)).length).toBe(1)
  })

  test('the title shows on a speaker change and after another main turn, not between one speaker\'s records', async ($, on) => {
    const { turn, answer } = await feed($, on)
    expect(await answer(note(rec(1, 'E1', 'a'), rec(2, 'E1', 'b\nc'), rec(3, 'C1', 'd'))))
      .toBe('**🟦 Expert · T3**\n> a\n>\n> b\n> c\n\n**🟩 Coder · T3**\n> d')
    expect(await answer(note(rec(4, 'C1', 'e')))).toBe('> e')
    expect(await turn('hello')).toBe(SENT)
    expect(await answer(note(rec(5, 'C1', 'f')))).toBe('**🟩 Coder · T3**\n> f')
  })

  test('labels: Claude, Planning (the partner planning, a planner), the loop expert, its coder, the thinkers', async ($, on) => {
    const { answer } = await feed($, on)
    const got = await answer(note(rec(1, 'P1', 'a'), rec(2, 'P1', 'b', true), rec(3, 'G1', 'c'), rec(4, 'E1', 'd'), rec(5, 'C1', 'e'), rec(6, 'K1', 'f'), rec(7, 'D1', 'g')))
    expect(got.split('\n').filter(l => l.startsWith('**'))).toEqual([
      '**🟧 Claude**', '**🟪 Planning**', '**🟪 Planning**', '**🟦 Expert · T3**', '**🟩 Coder · T3**', '**🟦 Thinker**', '**🟦 Thinker**',
    ])
  })

  // 3.23 T13d.c2: the terminal draws a stored feed answer in the cards' look; other text falls through.
  const engine = (on: On): void => {
    on('ui.render', async ($, e) => {
      const { Text } = $.ui.resolve(e)
      return Text({ children: 'engine' })
    })
  }
  const drawnOf = async ($: Engine, text: string): Promise<string> =>
    JSON.stringify(await (await $.ui.mount({ plugin: 'pa', surface: 'terminal', component: 'AssistantMessage', props: { text, isFirstOfReply: true } })).drawn())

  test('a feed answer draws run by run: the side bar in the role\'s hex, the label on a titled run only, no squares', async ($, on) => {
    engine(on)
    const { answer } = await feed($, on)
    const a = await answer(note(rec(1, 'C1', 'Adding the operator.'), rec(2, 'E1', 'Checking it.')))
    const drawn = await drawnOf($, a)
    for (const s of ['Coder · T3', 'Expert · T3', '#00af00', '#0087ff', 'quote', 'Adding the operator.', 'Checking it.']) {
      expect(drawn).toContain(s)
    }
    expect(drawn).not.toContain('🟩')
    expect(drawn).not.toContain('engine')
    const b = await answer(note(rec(3, 'E1', 'Still checking.')))
    const cont = await drawnOf($, b)
    expect(cont).toContain('Still checking.')
    expect(cont).toContain('#0087ff')
    expect(cont).not.toContain('Expert · T3')
    expect(await drawnOf($, '**🟩 Coder · T3**\n Not the feed.')).toContain('engine')
  })

  test('card_style plain: no border, the bullet and the label', { options: { card_style: 'plain' } }, async ($, on) => {
    engine(on)
    const { answer } = await feed($, on)
    const drawn = await drawnOf($, await answer(note(rec(1, 'C1', 'Adding the operator.'))))
    expect(drawn).toContain('● Coder · T3')
    expect(drawn).not.toContain('quote')
    expect(drawn).not.toContain('round')
  })

  // 3.24 T8b: an edit record: stored as the header and a ```diff fence (the phone); drawn as the native look in a note.
  test('3.24 T8b: an edit record is stored as a diff fence and drawn as numbered hunks with ... between, in the coder\'s note', async ($, on) => {
    engine(on)
    const { answer } = await feed($, on)
    const edit = { path: 'calc.py', created: false, add: 1, del: 1, hunks: ['@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2', '@@ -9,1 +9,1 @@\n-a\n+b'], more: 2 }
    const line = JSON.stringify({ pa: 'feed', n: 1, agent: 'C1', type: 'coder-opus55', desc: 'd', planning: false, text: 'Updated calc.py (+1 -1)', at: 1, kind: 'edit', edit })
    const a = await answer(note(line))
    expect(a).toBe('**🟩 Coder · T3**\n> Updated calc.py (+1 -1)\n> ```diff\n> @@ -1,2 +1,2 @@\n>  def f():\n> -    return 1\n> +    return 2\n> @@ -9,1 +9,1 @@\n> -a\n> +b\n> ```\n> … +2 lines')
    const drawn = await drawnOf($, a)
    for (const s of ['Coder · T3', '#00af00', 'quote', 'Updated calc.py', ' (+1 -1)', '"format":"diff"', '...', '… +2 lines']) {
      expect(drawn).toContain(s)
    }
    expect(drawn).not.toContain('```')
  })

  test('card_style boxed: the round border', { options: { card_style: 'boxed' } }, async ($, on) => {
    engine(on)
    const { answer } = await feed($, on)
    expect(await drawnOf($, await answer(note(rec(1, 'C1', 'Adding the operator.'))))).toContain('round')
  })

  // 3.24 T13r: Claude Code escapes `&`, `<`, `>` inside `<event>` (the 5.2 run: `2&gt;&amp;1`); a run is one Markdown block.
  test('3.24 T13r: a feed line draws as the agent wrote it, through Markdown, with no entities', async ($, on) => {
    engine(on)
    const { w, answer } = await feed($, on)
    const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    const said = '## Plan\n- run `cmd 2>&1`\n- keep <x> & "q" \'s\nA literal &amp; stays.'
    const a = await answer(note(esc(rec(1, 'E1', said)), esc(rec(2, 'E1', 'Then this.'))))
    const body = `${said}\n\nThen this.`
    expect(a).toBe(`**🟦 Expert · T3**\n${said.split('\n').map(l => `> ${l}`).join('\n')}\n>\n> Then this.`)
    const drawn = await drawnOf($, a)
    expect(drawn).toContain(JSON.stringify(body).slice(1, -1))
    expect(drawn).toContain('Markdown')
    expect(drawn).not.toContain('&gt;')
    expect(drawn).not.toContain('&lt;')
    expect(w.files.get(`${BASE}/drawn.json`) ?? '').not.toContain('&gt;')
  })

  test('a developer message carries the feed drawn since Claude\'s last answer, without Claude\'s own; the journal keeps the text', async ($, on) => {
    const { w, n, clock, answer } = await feed($, on)
    const lines = [rec(1, 'C1', 'Before the answer.'), rec(2, 'P1', 'My own line.'), rec(3, 'E1', 'Checking it.'), rec(4, 'C1', 'Adding.\nTesting.')]
    w.files.set(`${BASE}/loop.json`, JSON.stringify({ ...(JSON.parse(LOOP) as object), lastAnswerAt: 1 }))
    w.files.set(`${FEED}/log.jsonl`, `${[...lines, rec(5, 'C1', 'Not drawn yet.')].join('\n')}\n`)
    await answer(note(...lines))
    await $.prompt.submit({ text: 'how is it going?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    const block = ['‹pa› the feed you have not seen (since your last answer; older lines: .run/mod/S1/feed/log.jsonl):',
      'Expert · T3: Checking it.', 'Coder · T3: Adding.', 'Coder · T3: Testing.'].join('\n')
    expect(n.sends.some(t => t.endsWith(`${block}\n\nhow is it going?`))).toBe(true)
    expect(n.sends.some(t => t.includes('Before the answer.') || t.includes('My own line.') || t.includes('Not drawn yet.'))).toBe(false)
    expect(w.files.get(entries(w, 'dev-msg')[0] ?? '')).not.toContain('the feed you have not seen')
  })

  test('no feed block when nothing was drawn since Claude\'s last answer', async ($, on) => {
    const { w, n, clock, answer } = await feed($, on)
    w.files.set(`${BASE}/loop.json`, JSON.stringify({ ...(JSON.parse(LOOP) as object), lastAnswerAt: 10 }))
    w.files.set(`${FEED}/log.jsonl`, `${rec(1, 'C1', 'Old.')}\n`)
    await answer(note(rec(1, 'C1', 'Old.')))
    await $.prompt.submit({ text: 'and now?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(1_000)
    expect(n.sends.some(t => t.endsWith('and now?'))).toBe(true)
    expect(n.sends.some(t => t.includes('the feed you have not seen'))).toBe(false)
  })
})

describe('the feed restart (3.23 T13d.c1c)', () => {
  const FEED = `${BASE}/S1/feed`
  const CMD = /^bash "[^"]+\/feed\/pa_feed\.sh" "\/r" --dir "\/r\/\.run\/mod\/S1\/feed"$/
  const END = '<task-notification>\n<task-id>bm1</task-id>\n<status>failed</status>\n<summary>Monitor "pa feed" script failed (exit 143)</summary>\n</task-notification>'
  const event = (line: string): string =>
    `<task-notification>\n<task-id>bm1</task-id>\n<summary>Monitor event: "pa feed"</summary>\n<event>${line}</event>\n</task-notification>`
  const EXPIRED = event('[Monitor expired after 30m with 12 events delivered. Re-arm it if still needed.]')
  const STOPPED = event('[Monitor stopped — too much output (events suppressed for 30s)]')
  /** A session (interactive unless `headless`: then `-p` under the trial flag), the Monitor calls the router makes, held
   * open while `hold` is set until `release` (the test's own hook may not raise a check), and the core beneath that asks. */
  const life = async ($: Engine, on: On, headless = false) => {
    const clock = mock.clock(on, { now: 100_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const env: Record<string, string> = headless ? { PA_MOD_DEBUG: '1', PA_MOD_SDK_AS_USER: '1' } : { PA_MOD_DEBUG: '1' }
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [`${BASE}/S1/state.json`]: prior }, env)
    net(on).list = [{ id: 'P1', status: 'idle' }]
    on('tool.check', async () => ({ decision: 'ask', reason: 'core' }))
    const calls: { command: string; description: string; timeout: number }[] = []
    const gate = { hold: false, release: (): void => undefined }
    on('tool.call', { tool: 'Monitor' }, async ($, e) => {
      const x = e as unknown as { command: string; description: string; timeout_ms: number }
      calls.push({ command: x.command, description: x.description, timeout: x.timeout_ms })
      if (gate.hold) {
        await new Promise<void>(resolve => {
          gate.release = resolve
        })
      }
      return { result: { taskId: 'bm2' }, text: 'Monitor started' } as never
    })
    await $.session.start(headless ? START : ISTART)
    const beat = (at: number): void => {
      w.files.set(`${FEED}/alive`, JSON.stringify({ pid: 7, started: 100_000, beat: at, n: 0 }))
    }
    const notify = async (text: string): Promise<void> => {
      await $.prompt.submit({ text, origin: { kind: 'task-notification' }, wait: false })
      await clock.advance(10)
    }
    return { w, clock, calls, gate, beat, notify }
  }

  for (const [name, text] of [['the end notice', END], ['an expiry line', EXPIRED], ['a stop line', STOPPED]] as const) {
    test(`${name} restarts the feed once with the launcher; the allow holds only during the call`, async ($, on) => {
      const { w, clock, calls, gate, notify } = await life($, on)
      expect((await $.tool.check({ tool: 'Monitor', input: {} })).decision).toBe('ask')
      gate.hold = true
      await notify(text)
      expect(calls.length).toBe(1)
      expect(calls[0]?.command).toMatch(CMD)
      expect(calls[0]?.description).toBe('pa feed')
      expect(calls[0]?.timeout).toBe(1_800_000)
      expect((await $.tool.check({ tool: 'Monitor', input: {} })).decision).toBe('allow')
      gate.release()
      await clock.advance(10)
      expect((await $.tool.check({ tool: 'Monitor', input: {} })).decision).toBe('ask')
      expect(debugKinds(w)).toContain('pa.feed.restart')
    })
  }

  test('a heartbeat older than 20 s restarts the feed once', async ($, on) => {
    const { clock, calls, beat } = await life($, on)
    beat(100_000)
    await clock.advance(5_000)
    expect(calls.length).toBe(0)
    await clock.advance(25_000)
    expect(calls.length).toBe(1)
    expect(calls[0]?.command).toMatch(CMD)
  })

  test('no heartbeat ever: a restart 30 s after the first composer prompt, never under -p', async ($, on) => {
    const { w, clock, calls } = await life($, on, true)
    await $.prompt.submit({ text: 'hello', origin: { kind: 'sdk' }, wait: false })
    await clock.advance(120_000)
    expect(calls).toEqual([])
    expect(debugKinds(w).filter(k => k.startsWith('pa.feed.'))).toEqual([])
  })

  test('no heartbeat ever, interactive: a restart 30 s after the first composer prompt', async ($, on) => {
    const { clock, calls } = await life($, on)
    await clock.advance(60_000)
    expect(calls.length).toBe(0)
    await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false })
    await clock.advance(25_000)
    expect(calls.length).toBe(0)
    await clock.advance(10_000)
    expect(calls.length).toBe(1)
  })

  test('at most 6 restarts in 10 minutes; each refusal is a debug event', async ($, on) => {
    const { w, clock, calls, beat } = await life($, on)
    beat(100_000)
    await clock.advance(5_000)
    await clock.advance(500_000)
    expect(calls.length).toBe(6)
    expect(debugKinds(w)).toContain('pa.feed.refuse')
    await clock.advance(125_000) // the first (at 125 s) leaves the window at 725 s
    expect(calls.length).toBe(7)
  })

  // 3.23 T13d.c2: a feed that cannot start.
  test('the launcher\'s error stops the restarts and tells the developer once, journaled', async ($, on) => {
    const logs: string[] = []
    on('ui.log', async ($, e) => {
      logs.push(e.text)
      return { value: undefined }
    })
    const { w, clock, calls, notify } = await life($, on)
    const ERR = event('{"pa":"feed","error":"no python"}')
    await notify(ERR)
    await notify(ERR)
    await notify(END)
    await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false })
    await clock.advance(120_000)
    expect(calls).toEqual([])
    const line = "The live feed could not start: no Python found (pa.json's python key)."
    expect(logs.filter(l => l === line).length).toBe(1)
    expect(debugKinds(w).filter(k => k === 'pa.feed.fail').length).toBe(1)
    const j = entries(w, 'mod-feed')
    expect(j.length).toBe(1)
    expect(w.files.get(j[0] ?? '')).toContain(line)
  })

  test('a second "no heartbeat" restart waits for the first one\'s end notice', async ($, on) => {
    const { clock, calls, notify } = await life($, on)
    await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false })
    await clock.advance(35_000)
    expect(calls.length).toBe(1)
    await clock.advance(120_000)
    expect(calls.length).toBe(1)
    await notify(END)
    expect(calls.length).toBe(2)
    await clock.advance(120_000)
    expect(calls.length).toBe(2)
  })
})

describe('the session title (3.23 T13d.c2)', () => {
  const PLAN = '# Phase 3.23 — Autopilot (implements 3.2)\n\n## Tasks\n- T1 | done | title: a |\n- T2 | next | title: b |\n'
  const titled = async ($: Engine, on: On, files: Record<string, string>, pa = '{"mod":"on","warmer":"off","upgrade":"ask","project":"Calc"}') => {
    mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: pa, ...files }, { PA_MOD_DEBUG: '1' })
    net(on).list = [{ id: 'P1', status: 'idle' }]
    on('classic.UserPromptSubmit', async () => ({}))
    await $.session.start(ISTART)
    return w
  }

  test('both hooks return <project> · Phase <id> · <done>/<total>; each prompt is a debug event', async ($, on) => {
    const w = await titled($, on, { [`${ROOT}/phase-ends/current/PHASE_PLAN.md`]: PLAN })
    const s = await $.classic.SessionStart({ source: 'startup', session_id: 'S1' } as never)
    expect((s as { sessionTitle?: string }).sessionTitle).toBe('Calc · Phase 3.23 · 1/2')
    const u = await $.classic.UserPromptSubmit({ prompt: 'hi', session_id: 'S1', source: 'system' } as never)
    expect((u as { sessionTitle?: string }).sessionTitle).toBe('Calc · Phase 3.23 · 1/2')
    expect(debugKinds(w)).toContain('pa.title')
  })

  test('no open phase: the project folder\'s name alone', async ($, on) => {
    await titled($, on, {}, '{"mod":"on","warmer":"off","upgrade":"ask"}')
    const u = await $.classic.UserPromptSubmit({ prompt: 'hi', session_id: 'S1' } as never)
    expect((u as { sessionTitle?: string }).sessionTitle).toBe('r')
  })

  test('the mod off: no title', async ($, on) => {
    await titled($, on, { [`${ROOT}/phase-ends/current/PHASE_PLAN.md`]: PLAN }, '{"mod":"off"}')
    const u = await $.classic.UserPromptSubmit({ prompt: 'hi', session_id: 'S1' } as never)
    expect((u as { sessionTitle?: string }).sessionTitle).toBeUndefined()
  })
})

describe('health records (3.24 T6)', () => {
  type Rec = Record<string, unknown>
  const rec = (w: World, f: string): Rec | null => (w.files.has(`${BASE}/${f}`) ? JSON.parse(w.files.get(`${BASE}/${f}`) ?? '{}') as Rec : null)
  test('an active start writes the heartbeat; an inert one writes neither record', async ($, on) => {
    mock.clock(on, { now: 3_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    const hb = rec(w, 'heartbeat.json') ?? {}
    expect({ ...hb, version: typeof hb['version'] }).toEqual({ sid: 'S1', version: 'string', engine: '2.1.293', at: 3, plugin: 'pa' })
    expect(rec(w, 'error.json')).toBe(null)
  })
  test('inert: no heartbeat', async ($, on) => {
    mock.clock(on)
    const w = world(on, {}, {})
    await $.session.start(START)
    expect([rec(w, 'heartbeat.json'), rec(w, 'error.json')]).toEqual([null, null])
  })
  test('a session.start that throws leaves error.json', async ($, on) => {
    mock.clock(on, { now: 4_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, `${BASE}/S1/state.json`)
    await $.session.start(START).catch(() => undefined)
    expect(w.files.has(`${BASE}/S1/state.json`)).toBe(false)
    const er = rec(w, 'error.json') ?? {}
    expect([er['sid'], er['plugin'], er['hook'], er['at']]).toEqual(['S1', 'pa', 'session.start', 4])
    expect(String(er['error'])).toContain('EACCES')
  })
})
