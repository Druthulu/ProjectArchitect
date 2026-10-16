// Hook tests of the watcher's activation through the kit's engine `$`. Tests have no fs: the test answers fs.* from a
// map with its own hooks. The kit hands hooks resolved host paths (`Z:\r\...` on Windows); the map keys are `/r/...`.
// The router plugin `pa` is not loaded here: pa-watch depends on it only through files.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine, On } from 'claude-code'

const ROOT = '/r'
const ON = '{"mod":"on","warmer":"off","upgrade":"ask"}'
const norm = (p: string): string => p.replace(/\\/g, '/').replace(/^[A-Za-z]:/, '')

type Row = { id: string; description: string; type: string; status: 'running'; parentId?: string }
const PARTNER: Row[] = [{ id: 'A1', description: 'p', type: 'pa-claude', status: 'running' }]

function world(on: On, files: Record<string, string>, env: Record<string, string>, list: Row[] = PARTNER, denyRegister = false): Map<string, string> {
  const m = new Map(Object.entries(files))
  mock.env(on, env)
  on('session.root', async () => ({ value: ROOT }))
  on('session.id', async () => ({ value: 'S1' }))
  on('session.version', async () => ({ value: { version: '2.1.293', base: '2.1.293' } }))
  on('fs.read', async ($, e) => {
    const t = m.get(norm(e.path))
    return t === undefined ? { deny: `ENOENT ${norm(e.path)}` } : { value: t }
  })
  on('fs.write', async ($, e) => {
    m.set(norm(e.path), e.text)
    return { value: undefined }
  })
  on('session.start', async () => ({ cwd: ROOT }))
  on('tool.register', async ($, e) => (denyRegister ? { deny: 'EACCES tool.register' } : { value: { tool: `mcp__pa-watch__${e.name}` } }))
  on('agent.list', async () => ({ value: list }))
  return m
}

const START = { cwd: ROOT, surface: null, isInteractive: false } as const

function activation(m: Map<string, string>): { decision: { active: boolean } } | null {
  for (const [p, t] of m) {
    if (p.startsWith(`${ROOT}/.run/mod/S1/debug/`) && p.endsWith('-watch.activation.json')) {
      return JSON.parse(t) as { decision: { active: boolean } }
    }
  }
  return null
}

describe('watcher wiring', () => {
  test('inactive without pa.json: no debug file (3.24 T8: an inert session writes nothing)', async ($, on) => {
    mock.clock(on)
    const m = world(on, {}, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    expect(activation(m)).toBe(null)
    expect([...m.keys()].some(p => p.startsWith(`${ROOT}/.run/`))).toBe(false)
  })

  test('active with mod: on', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, { PA_MOD_DEBUG: '1' })
    await $.session.start(START)
    expect(activation(m)).toEqual(expect.objectContaining({ decision: { active: true, reason: 'pa.json mod: on' } }))
  })

  test('no debug log without PA_MOD_DEBUG', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    expect(activation(m)).toBe(null)
  })
})

const inbox = (m: Map<string, string>): [string, string][] => [...m].filter(([p]) => p.startsWith(`${ROOT}/.run/mod/S1/inbox/`))

describe('watcher tools and thinking', () => {
  test('a plan call writes one request in the router\'s format and returns a string', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    const r = await $.tool.call({ tool: 'mcp__pa-watch__plan', path: 'plan.md', title: 'T', agentId: 'A1' } as never)
    const files = inbox(m)
    expect(files.length).toBe(1)
    const [path, text] = files[0] ?? ['', '']
    const name = path.slice(path.lastIndexOf('/') + 1)
    expect(name).toMatch(/^\d+-\d+-plan\.json$/)
    const id = name.slice(0, -'.json'.length)
    expect(JSON.parse(text)).toEqual({ id, kind: 'plan', caller: 'A1', input: { path: 'plan.md', title: 'T' }, at: expect.any(Number) })
    expect(r.result).toBe(`queued plan ${id}; the answer arrives later as a message — end your turn now`)
  })

  test('bad input writes nothing', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    const r = await $.tool.call({ tool: 'mcp__pa-watch__ask', question: 'q?', options: ['only'], agentId: 'A1' } as never)
    expect(r.result).toBe('error: options must be 2-4 strings')
    expect(inbox(m).length).toBe(0)
  })

  test('turn.step passes chunks through and writes one thinking entry', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    const chunks = [
      { kind: 'thinking', index: 0, text: 'Weighing ' },
      { kind: 'thinking', index: 0, text: 'the plan.' },
      { kind: 'text', index: 1, text: 'ok' },
    ] as const
    on('turn.step', async function* ($, e) {
      for (const c of chunks) {
        yield c
      }
      return { turnId: e.turnId, index: e.index, answer: 'ok', toolUses: [], stopReason: 'end_turn', usage: null }
    })
    await $.session.start(START)
    const s = $.turn.step({ turnId: 'U1', index: 0, model: 'claude-opus-5-5', effort: 'medium', messageCount: 1, agentId: 'A1' })
    const seen: unknown[] = []
    for await (const c of s) {
      seen.push(c)
    }
    await s.result
    expect(seen).toEqual([...chunks])
    const entries = [...m].filter(([p]) => p.includes('/.claude-state/journal/'))
    expect(entries.length).toBe(1)
    const [path, text] = entries[0] ?? ['', '']
    expect(path).toMatch(/\/\.claude-state\/journal\/\d{4}-\d{2}-\d{2}\/\d{6}-\d{3}w-claude-thinking\.md$/)
    expect(text).toContain('agentId: A1')
    expect(text).toContain('turnId: U1')
    expect(text).toContain('effort: medium')
    expect(text.endsWith('---\nWeighing the plan.\n')).toBe(true)
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/A1.json`) ?? '{}') as Record<string, unknown>
    expect([f['model'], f['effort']]).toEqual(['claude-opus-5-5', 'medium']) // 3.22 T8f: the router stamps these
  })
})

function screen(on: On): { logs: string[]; status: (string | undefined)[] } {
  const out = { logs: [] as string[], status: [] as (string | undefined)[] }
  on('ui.log', async ($, e) => {
    out.logs.push(e.text)
    return { value: undefined }
  })
  on('ui.status', async ($, e) => {
    out.status.push(e.text)
    return { value: undefined }
  })
  on('tool.call', { tool: 'Bash' }, async () => ({ result: 'clean' }))
  return out
}

const PA_PY = '{"mod":"on","python":"C:/py/python.exe"}'

/** Answers every step with `answer` (a turn's end) from beneath the plugins; registered before the first `$` call. */
function stepAnswers(on: On, answer: string): void {
  on('turn.step', async function* ($, e) {
    yield { kind: 'text', index: 0, text: answer } as const
    return {
      turnId: e.turnId, index: e.index, answer, toolUses: [], stopReason: 'end_turn',
      usage: { input_tokens: 10, output_tokens: 20, cache_read_input_tokens: 50_000, cache_creation_input_tokens: 1000, model: 'claude-opus-5-5' },
    }
  })
}

async function step($: Engine): Promise<void> {
  const s = $.turn.step({ turnId: 'U1', index: 0, model: 'claude-opus-5-5', effort: 'medium', messageCount: 1, agentId: 'A1' })
  for await (const c of s) {
    void c
  }
  await s.result
}

describe('no transcript lines (3.23 T13d.c3); no usage line (3.24 T13C)', () => {
  test('a tool call logs nothing', async ($, on) => {
    mock.clock(on)
    world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    const out = screen(on)
    await $.session.start(START)
    await $.tool.call({ tool: 'Bash', command: 'git status', agentId: 'A1' } as never)
    expect(out.logs).toEqual([])
  })

  // 3.24 T13C: the usage line is gone: no status line, no prices run; the facts file still carries the step's ctx.
  test('a step ending the turn logs nothing and sets no status; the facts keep ctx', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const out = screen(on)
    const runs: (readonly string[])[] = []
    on('process.run', async ($, e) => {
      runs.push(e.argv)
      return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    })
    stepAnswers(on, 'Tree is clean.')
    await $.session.start(START)
    await step($)
    expect(runs).toEqual([])
    expect(out.logs).toEqual([])
    expect(out.status).toEqual([])
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/A1.json`) ?? '{}') as Record<string, unknown>
    expect(f['ctx']).toBe(51_010)
  })

  test('a ping turn draws nothing', async ($, on) => {
    mock.clock(on)
    world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { HOME: '/home/u' })
    const out = screen(on)
    stepAnswers(on, '.')
    await $.session.start(START)
    await step($)
    expect(out.logs).toEqual([])
    expect(out.status).toEqual([])
  })
})

/**
 * Step 0 says `text` and hands back `message` (a SubagentHandback call); step 1 of the same turn ends with text. The
 * hand-back call itself is answered beneath the plugins.
 */
function stepHandsBack(on: On, text: string, message: string): void {
  const input = { message }
  on('turn.step', async function* ($, e) {
    if (e.index === 0) {
      yield { kind: 'text', index: 0, text } as const
      yield { kind: 'tool', index: 1, id: 'tu1', name: 'SubagentHandback' } as const
      return { turnId: e.turnId, index: 0, answer: text, toolUses: [{ name: 'SubagentHandback', input }], stopReason: 'tool_use', usage: null } as never
    }
    yield { kind: 'text', index: 0, text: 'Also this.' } as const
    return { turnId: e.turnId, index: e.index, answer: 'Also this.', toolUses: [], stopReason: 'end_turn', usage: null }
  })
  on('tool.call', { tool: 'SubagentHandback' }, async () => ({ result: 'handed back' }))
}

async function handBackTurn($: Engine, message: string): Promise<void> {
  await step($)
  await $.tool.call({ tool: 'SubagentHandback', message, agentId: 'A1' } as never)
  const s = $.turn.step({ turnId: 'U1', index: 1, model: 'claude-opus-5-5', effort: 'medium', messageCount: 3, agentId: 'A1' })
  for await (const c of s) {
    void c
  }
  await s.result
}

describe('the hand-back draws nothing (3.23 T13d.c3)', () => {
  const MSG = 'Tree is clean.\nNothing to commit.'

  test('a hand-back turn: no line', async ($, on) => {
    mock.clock(on)
    world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    const out = screen(on)
    stepHandsBack(on, 'Tree is clean.', MSG)
    await $.session.start(START)
    await handBackTurn($, MSG)
    expect(out.logs).toEqual([])
  })
})

/** Step 0 of `agentId`'s turn `turnId`: `text`, then one Bash call. */
async function toolStep($: Engine, on: On, agentId: string, turnId: string, text: string, command: string): Promise<void> {
  const s = $.turn.step({ turnId, index: 0, model: 'claude-opus-5-5', effort: 'medium', messageCount: 1, agentId })
  for await (const c of s) {
    void c
  }
  await s.result
  await $.tool.call({ tool: 'Bash', command, agentId } as never)
}

describe('approach notes and the facts file (3.23 T5c)', () => {
  const LIST: Row[] = [
    ...PARTNER,
    { id: 'E1', description: 'T1 Add the function', type: 'pa-expert', status: 'running' },
    { id: 'C1', description: 'T1.c1 plus', type: 'pa-coder', status: 'running', parentId: 'E1' },
  ]
  const steps = (on: On, text: string): void => {
    on('turn.step', async function* ($, e) {
      yield { kind: 'text', index: 0, text } as const
      yield { kind: 'tool', index: 1, id: 'tu1', name: 'Bash' } as const
      return { turnId: e.turnId, index: e.index, answer: text, toolUses: [{ name: 'Bash', input: {} }], stopReason: 'tool_use', usage: null } as never
    })
    on('tool.call', { tool: 'Bash' }, async () => ({ result: 'ok' }))
  }
  const notes = (m: Map<string, string>): [string, string][] => [...m].filter(([p]) => /\/\.claude-state\/journal\/.*-note\.md$/.test(p))

  test('an expert\'s note is a T<n> entry; a coder\'s a coder entry; once per turn; the partner files none', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    steps(on, 'I will add plus to calc.py.\nThen run the tests.\nThird line.')
    await $.session.start(START)
    await toolStep($, on, 'E1', 'U1', '', 'git status')
    await toolStep($, on, 'C1', 'U2', '', 'pytest')
    const s2 = $.turn.step({ turnId: 'U1', index: 1, model: 'claude-opus-5-5', effort: 'medium', messageCount: 3, agentId: 'E1' })
    for await (const c of s2) {
      void c
    }
    await s2.result
    await toolStep($, on, 'A1', 'U3', '', 'ls')
    const n = notes(m)
    expect(n.map(([p]) => p.replace(/^.*\d{3}w-/, ''))).toEqual(['T1-note.md', 'coder-note.md'])
    expect(n[0]?.[1]).toContain('agentId: E1')
    expect(n[0]?.[1].endsWith('---\nI will add plus to calc.py.\nThen run the tests.\nThird line.\n')).toBe(true)
  })

  test('the facts file: type, description, parentId, a 2-line note, the last three steps, updatedAt', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    steps(on, 'I will add plus.\nThen test.\nThird.')
    await $.session.start(START)
    await toolStep($, on, 'C1', 'U2', '', 'git status')
    for (const c of ['pytest -q', 'git diff', 'git commit -m x']) {
      await $.tool.call({ tool: 'Bash', command: c, agentId: 'C1' } as never)
    }
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/C1.json`) ?? '{}') as Record<string, unknown>
    expect(f).toEqual(expect.objectContaining({
      model: 'claude-opus-5-5', effort: 'medium', type: 'pa-coder', description: 'T1.c1 plus', parentId: 'E1',
      note: 'I will add plus.\nThen test.', steps: ['Bash pytest -q', 'Bash git diff', 'Bash git commit -m x'], updatedAt: expect.any(Number),
    }))
  })

  test('a steer call writes one steer request', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    const r = await $.tool.call({ tool: 'mcp__pa-watch__steer', task: 'T1', text: 'Call it plus.', agentId: 'A1' } as never)
    const files = inbox(m)
    expect(files.length).toBe(1)
    expect(JSON.parse(files[0]?.[1] ?? '{}')).toEqual(expect.objectContaining({ kind: 'steer', caller: 'A1', input: { task: 'T1', text: 'Call it plus.' } }))
    expect(r.result).toMatch(/^queued steer /)
  })
})

describe('the live feed records (3.23 T13d)', () => {
  const LIST: Row[] = [
    { id: 'E1', description: 'T1 Add the function', type: 'pa-expert', status: 'running' },
    { id: 'R1', description: 'find x', type: 'retriever-code', status: 'running' },
    { id: 'A1', description: 'Claude', type: '', status: 'running' },
  ]
  const STATE = `${ROOT}/.run/mod/S1/state.json`
  type Chunk = { kind: 'text'; index: number; text: string } | { kind: 'thinking'; index: number; text: string } | { kind: 'tool'; index: number; id: string; name: string }
  const T = (text: string): Chunk => ({ kind: 'text', index: 0, text })
  const TOOL = (name = 'Bash'): Chunk => ({ kind: 'tool', index: 1, id: 'tu', name })
  /** Every step yields `chunks` and ends as `answer` with `uses`. */
  const steps = (on: On, chunks: Chunk[], answer: string, uses: { name: string; input: unknown }[]): void => {
    on('turn.step', async function* ($, e) {
      for (const c of chunks) {
        yield c as never
      }
      return { turnId: e.turnId, index: e.index, answer, toolUses: uses, stopReason: uses.length > 0 ? 'tool_use' : 'end_turn', usage: null } as never
    })
  }
  const run = async ($: Engine, agentId: string, index = 1): Promise<void> => {
    const s = $.turn.step({ turnId: 'U1', index, model: 'claude-opus-5-5', effort: 'medium', messageCount: 1, agentId })
    for await (const c of s) {
      void c
    }
    await s.result
  }
  const records = (m: Map<string, string>, dir = `${ROOT}/.run/mod/S1/feed`): Record<string, unknown>[] =>
    [...m].filter(([p]) => p.startsWith(`${dir}/in/`)).sort(([a], [b]) => a.localeCompare(b)).map(([p, t]) => {
      expect(p.slice(p.lastIndexOf('/') + 1)).toMatch(/^\d{13}-\d{6}\.json$/)
      expect(t.endsWith('}\n')).toBe(true)
      return JSON.parse(t) as Record<string, unknown>
    })
  const BASH = [{ name: 'Bash', input: {} }]

  test('text then a tool call writes one record', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    steps(on, [T('  I will add plus.\n'), TOOL()], 'I will add plus.', BASH)
    await $.session.start(START)
    await run($, 'E1')
    expect(records(m)).toEqual([{ agent: 'E1', type: 'pa-expert', desc: 'T1 Add the function', planning: false, text: 'I will add plus.', at: expect.any(Number), kind: 'text' }])
  })

  // 3.23 T13d.c7: statements only; a step's summary (its `thinking` chunks) never feeds.
  test('3.23 T13d.c7: a step with summaries and text writes only the text records', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    const TH = (text: string): Chunk => ({ kind: 'thinking', index: 0, text })
    const chunks: Chunk[] = [TH('I need the plan, '), TH('so let me read it.'), T('Reading.'), TOOL(), TH('Now the tests.'), T('Testing.'), TOOL(), TH('All green.')]
      .map((c, index) => ({ ...c, index, ...(c.kind === 'tool' ? { id: `tu${index}` } : {}) }))
    steps(on, chunks, '', [...BASH, ...BASH])
    await $.session.start(START)
    await run($, 'E1')
    expect(records(m).map(r => [r['kind'], r['text']])).toEqual([['text', 'Reading.'], ['text', 'Testing.']])
  })

  test('two tool calls with text before each write two; text after the last tool call one more', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    const chunks: Chunk[] = [T('First.'), TOOL(), T('Second.'), TOOL(), T('After.')].map((c, index) => ({ ...c, index, ...(c.kind === 'tool' ? { id: `tu${index}` } : {}) }))
    steps(on, chunks, 'First.Second.After.', [...BASH, ...BASH])
    await $.session.start(START)
    await run($, 'E1')
    expect(records(m).map(r => r['text'])).toEqual(['First.', 'Second.', 'After.'])
  })

  // 3.23 T13d.c7: no summary feeds (a summary-only step, a ping, a hand-back, a final answer write nothing).
  test('a summary-only step, a ping step, the hand-back and the final answer write no record', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    let phase = 0
    on('turn.step', async function* ($, e) {
      phase += 1
      if (phase === 1) { // thinking, then a tool call with no text
        yield { kind: 'thinking', index: 0, text: 'Weighing.' } as const
        yield { kind: 'tool', index: 1, id: 'tu', name: 'Bash' } as const
        return { turnId: e.turnId, index: e.index, answer: '', toolUses: BASH, stopReason: 'tool_use', usage: null } as never
      }
      if (phase === 2) { // a ping
        yield { kind: 'thinking', index: 0, text: 'A ping.' } as const
        yield { kind: 'text', index: 0, text: '.' } as const
        return { turnId: e.turnId, index: e.index, answer: '.', toolUses: [], stopReason: 'end_turn', usage: null }
      }
      if (phase === 3) { // text before the hand-back, and the hand-back
        yield { kind: 'thinking', index: 0, text: 'Handing back.' } as const
        yield { kind: 'text', index: 0, text: 'All done.' } as const
        yield { kind: 'tool', index: 1, id: 'tu', name: 'SubagentHandback' } as const
        return { turnId: e.turnId, index: e.index, answer: 'All done.', toolUses: [{ name: 'SubagentHandback', input: { message: 'Report.' } }], stopReason: 'tool_use', usage: null } as never
      }
      yield { kind: 'thinking', index: 0, text: 'Answering.' } as const
      yield { kind: 'text', index: 0, text: 'The final answer.' } as const // an end_turn answer with no tool uses
      return { turnId: e.turnId, index: e.index, answer: 'The final answer.', toolUses: [], stopReason: 'end_turn', usage: null }
    })
    await $.session.start(START)
    await run($, 'E1', 1)
    await run($, 'E1', 0)
    await run($, 'E1', 1)
    await run($, 'E1', 2)
    expect(records(m)).toEqual([])
  })

  // 3.24 T13n: a narration block (told by its signature in the api messages at the step's end) feeds like text, in block
  // order; an ordinary thinking block, or a message that does not line up with the stream, feeds none.
  const NARR = 'Eg0KC0IJbmFycmF0aW9u' // field 2 > 1 > 8 = "narration"
  const PLAIN = 'EgwKCkIIdGhpbmtpbmc=' // the same layout, "thinking"
  const interleaved: Chunk[] = [
    { kind: 'thinking', index: 0, text: 'Weighing it.' }, { kind: 'text', index: 1, text: 'First.' },
    { kind: 'thinking', index: 2, text: "I'll write the script." }, { kind: 'tool', index: 3, id: 'tu3', name: 'Bash' },
    { kind: 'text', index: 4, text: 'Second.' }, { kind: 'tool', index: 5, id: 'tu5', name: 'Bash' },
    { kind: 'thinking', index: 6, text: 'Now the tests.' },
  ]
  const apiOf = (sigs: string[], toolId = 'tu5', after: unknown[] = []): unknown[] => [{ role: 'user', content: [{ type: 'text', text: 'go' }] }, {
    role: 'assistant', content: [
      { type: 'thinking', thinking: '', signature: PLAIN }, { type: 'text', text: 'First.' },
      { type: 'thinking', thinking: '', signature: sigs[0] }, { type: 'tool_use', id: 'tu3', name: 'Bash', input: {} },
      { type: 'text', text: 'Second.' }, { type: 'tool_use', id: toolId, name: 'Bash', input: {} },
      { type: 'thinking', thinking: '', signature: sigs[1] },
    ],
  }, ...after]

  test('3.24 T13n: text and narration interleave in block order; ordinary thinking feeds none', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    let call = 0 // the second time a quick tool's result already follows the step's message (seen live, T13n)
    on('session.messages', async () => ({ value: apiOf([NARR, NARR], 'tu5', (call += 1) === 1 ? [] : [{ role: 'user', content: [{ type: 'tool_result', tool_use_id: 'tu5', content: 'ok' }] }]) as never }))
    steps(on, interleaved, 'First.Second.', [...BASH, ...BASH])
    await $.session.start(START)
    await run($, 'E1')
    const one = [['text', 'First.'], ['text', "I'll write the script."], ['text', 'Second.'], ['text', 'Now the tests.']]
    expect(records(m).map(r => [r['kind'], r['text']])).toEqual(one)
    await run($, 'E1')
    expect(records(m).map(r => [r['kind'], r['text']])).toEqual([...one, ...one])
    const facts = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/E1.json`) ?? '{}') as Record<string, unknown>
    expect(facts['line']).toBe('Now the tests.')
  })

  test('3.24 T13n: plain signatures, a message that does not line up, or no messages: the text records only', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    let call = 0
    on('session.messages', async () => {
      call += 1
      return call === 1 ? { value: apiOf([PLAIN, 'garbage']) as never } : call === 2 ? { value: apiOf([NARR, NARR], 'other') as never } : { deny: 'no' } as never
    })
    steps(on, interleaved, 'First.Second.', [...BASH, ...BASH])
    await $.session.start(START)
    await run($, 'E1')
    await run($, 'E1')
    await run($, 'E1')
    expect(records(m).map(r => r['text'])).toEqual(['First.', 'Second.', 'First.', 'Second.', 'First.', 'Second.'])
  })

  test('a retriever writes none', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    steps(on, [T('Searching.'), TOOL('Grep')], 'Searching.', [{ name: 'Grep', input: {} }])
    await $.session.start(START)
    await run($, 'R1')
    expect(records(m)).toEqual([])
  })

  test('3.24 T13C: the memory curator and the auditor feed, by their role', async ($, on) => {
    mock.clock(on)
    const list: Row[] = [...LIST, { id: 'M1', description: 'curate', type: 'memory-curator', status: 'running' }, { id: 'U1', description: 'audit', type: 'auditor', status: 'running' }]
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, list)
    steps(on, [T('Sorting memories.'), TOOL()], 'Sorting memories.', BASH)
    await $.session.start(START)
    await run($, 'M1')
    await run($, 'U1')
    expect(records(m).map(r => [r['type'], r['text']])).toEqual([['memory-curator', 'Sorting memories.'], ['auditor', 'Sorting memories.']])
  })

  test('the partner\'s record names it by id with type \'\'', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [STATE]: '{"partner":{"agentId":"A1"}}' }, {}, LIST)
    steps(on, [T('Looking.'), TOOL()], 'Looking.', BASH)
    await $.session.start(START)
    await run($, 'A1')
    expect(records(m)).toEqual([expect.objectContaining({ agent: 'A1', type: '', text: 'Looking.' })])
  })

  test('the folder follows state.json\'s feed.dir', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON, [STATE]: '{"feed":{"dir":".run/mod/S0/feed"}}' }, {}, LIST)
    steps(on, [T('Carry on.'), TOOL()], 'Carry on.', BASH)
    await $.session.start(START)
    await run($, 'E1')
    expect(records(m)).toEqual([])
    expect(records(m, `${ROOT}/.run/mod/S0/feed`).map(r => r['text'])).toEqual(['Carry on.'])
  })

  test('3.23 T13d.c2: the facts file carries the latest segment\'s last line, whitespace collapsed', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    const chunks: Chunk[] = [T('Reading the plan.'), TOOL(), T('Two parts:\n  add   the operator\n\n'), TOOL()]
      .map((c, index) => ({ ...c, index, ...(c.kind === 'tool' ? { id: `tu${index}` } : {}) }))
    steps(on, chunks, '', [...BASH, ...BASH])
    await $.session.start(START)
    await run($, 'E1')
    const facts = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/E1.json`) ?? '{}') as Record<string, unknown>
    expect(facts['line']).toBe('add the operator')
    expect(facts['type']).toBe('pa-expert')
  })

  // 3.24 T8b: a successful file edit by an expert, a coder or Claude is one `edit` record; failures and other tools none.
  const EDIT_IN = { tool: 'Edit', file_path: `${ROOT}/a.py`, old_string: 'return 1', new_string: 'return 2' }
  const EDIT_OUT = { filePath: `${ROOT}/a.py`, oldString: 'return 1', newString: 'return 2', originalFile: 'def f():\n    return 1\n', structuredPatch: [], userModified: false, replaceAll: false }
  test('3.24 T8b: an expert\'s Edit writes one edit record and the band\'s line', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    on('tool.call', { tool: 'Edit' }, async () => ({ result: EDIT_OUT }) as never)
    await $.session.start(START)
    await $.tool.call({ ...EDIT_IN, agentId: 'E1' } as never)
    expect(records(m)).toEqual([{
      agent: 'E1', type: 'pa-expert', desc: 'T1 Add the function', planning: false, text: 'Updated a.py (+1 -1)', at: expect.any(Number), kind: 'edit',
      edit: { path: 'a.py', created: false, add: 1, del: 1, hunks: ['@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2'], more: 0 },
    }])
    expect((JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/E1.json`) ?? '{}') as Record<string, unknown>)['line']).toBe('Updated a.py (+1 -1)')
  })

  test('3.24 T8b: a failed or denied Edit, a retriever\'s Write and a Bash write no record', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    let answer: unknown = { isError: true, text: 'String to replace not found', result: undefined }
    on('tool.call', { tool: 'Edit' }, async () => answer as never)
    on('tool.call', { tool: 'Write' }, async () => ({ result: { type: 'create', filePath: `${ROOT}/r.md`, content: 'x\n', structuredPatch: [], originalFile: null } }) as never)
    on('tool.call', { tool: 'Bash' }, async () => ({ result: 'ok' }) as never)
    await $.session.start(START)
    await $.tool.call({ ...EDIT_IN, agentId: 'E1' } as never)
    answer = { deny: 'denied by the developer' }
    await $.tool.call({ ...EDIT_IN, agentId: 'E1' } as never)
    await $.tool.call({ tool: 'Write', file_path: `${ROOT}/r.md`, content: 'x\n', agentId: 'R1' } as never)
    await $.tool.call({ tool: 'Bash', command: 'sed -i s/1/2/ a.py', agentId: 'E1' } as never)
    expect(records(m)).toEqual([])
  })
})

describe('health records (3.24 T6)', () => {
  type Rec = Record<string, unknown>
  const rec = (m: Map<string, string>, f: string): Rec | null => (m.has(`${ROOT}/.run/mod/${f}`) ? JSON.parse(m.get(`${ROOT}/.run/mod/${f}`) ?? '{}') as Rec : null)
  test('an active start writes the heartbeat; an inert one writes neither record', async ($, on) => {
    mock.clock(on, { now: 3_000 })
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {})
    await $.session.start(START)
    const hb = rec(m, 'heartbeat.json') ?? {}
    expect({ ...hb, version: typeof hb['version'] }).toEqual({ sid: 'S1', version: 'string', engine: '2.1.293', at: 3, plugin: 'pa-watch' })
    expect(rec(m, 'error.json')).toBe(null)
  })
  test('inert: no heartbeat', async ($, on) => {
    mock.clock(on)
    const m = world(on, {}, {})
    await $.session.start(START)
    expect([rec(m, 'heartbeat.json'), rec(m, 'error.json')]).toEqual([null, null])
  })
  test('a session.start that throws leaves error.json', async ($, on) => {
    mock.clock(on, { now: 4_000 })
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, PARTNER, true)
    await $.session.start(START).catch(() => undefined)
    const er = rec(m, 'error.json') ?? {}
    expect([er['sid'], er['plugin'], er['hook'], er['at']]).toEqual(['S1', 'pa-watch', 'session.start', 4])
    expect(String(er['error'])).toContain('EACCES')
  })
})

describe('the ladder: model at spawn, effort per request (3.24 T17b)', () => {
  const ROW = '{"agent":"pa-planner","managed":true,"model":"claude-opus-5-5","effort":"max","tier":"max20","source":"credentials"}'
  /** The resolver answered by `out` (null: it fails), the engine's spawn beneath (it records the model it was given),
   * and its step beneath (it records the effort of each request by agent). */
  function ladder(on: On, out: string | null): { runs: (readonly string[])[]; models: (string | undefined)[]; efforts: Record<string, unknown> } {
    const got = { runs: [] as (readonly string[])[], models: [] as (string | undefined)[], efforts: {} as Record<string, unknown> }
    on('process.run', async ($, e) => {
      got.runs.push(e.argv)
      return out === null ? { deny: 'ENOENT python' } : { value: { exitCode: 0, stdout: `${out}\n`, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    })
    on('agent.spawn', async ($, e) => {
      got.models.push(e.model)
      return { model: e.model ?? 'claude-opus-5-5', agentId: 'P1' }
    })
    on('turn.step', async function* ($, e) {
      got.efforts[e.agentId ?? 'main'] = e.effort
      yield { kind: 'text', index: 0, text: 'ok' } as const
      return { turnId: e.turnId, index: e.index, answer: 'ok', toolUses: [], stopReason: 'end_turn', usage: null }
    })
    return got
  }
  async function stepOf($: Engine, agentId: string): Promise<void> {
    const s = $.turn.step({ turnId: `U-${agentId}`, index: 0, model: 'claude-opus-5-5', effort: 'medium', messageCount: 1, agentId })
    for await (const c of s) {
      void c
    }
    await s.result
  }

  test('a managed agent is spawned on its row model; its requests get its effort, Claude keeps its own', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const got = ladder(on, ROW)
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'pa-planner', prompt: 'Draft the phase plan.', description: 'plan' })
    expect(got.models).toEqual(['claude-opus-5-5'])
    expect(got.runs.length).toBe(1)
    expect((got.runs[0] ?? []).filter((_, i) => i !== 6)).toEqual(['C:/py/python.exe', 'C:/Users/u/.claude/pa3/pa_ledger.py', 'ladder-row', '--agent', 'pa-planner', '--project', '--json'])
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/P1.json`) ?? '{}') as Record<string, unknown>
    expect([f['model'], f['effort'], f['type']]).toEqual(['claude-opus-5-5', 'max', 'pa-planner'])
    await stepOf($, 'P1')
    await stepOf($, 'A1')
    expect(got.efforts).toEqual({ P1: 'max', A1: 'medium' })
    const spawns = [...m].filter(([p]) => p.endsWith('-spawn.md'))
    expect(spawns.length).toBe(1)
    const [path, text] = spawns[0] ?? ['', '']
    expect(path).toMatch(/\d{6}-\d{3}w-planner-spawn\.md$/)
    for (const line of ['agentId: P1', 'agentType: pa-planner', 'model: claude-opus-5-5', 'effort: max', 'managed: yes', 'row_model: claude-opus-5-5', 'row_effort: max', 'tier: max20', 'source: credentials']) {
      expect(text).toContain(line)
    }
  })

  test('the row is read once per session; a planner spawn reads it again', async ($, on) => {
    mock.clock(on)
    world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const got = ladder(on, ROW.replace('pa-planner', 'pa-coder'))
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'pa-coder', prompt: 'x' })
    await $.agent.spawn({ subagentType: 'pa-coder', prompt: 'y' })
    expect(got.runs.length).toBe(1)
    await $.agent.spawn({ subagentType: 'pa-planner', prompt: 'z' })
    await $.agent.spawn({ subagentType: 'pa-planner', prompt: 'z' })
    expect(got.runs.length).toBe(3)
  })

  test('a resolver failure or a Claude spawn leaves the spawn and its requests as they are', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const got = ladder(on, null)
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'pa-planner', prompt: 'x' })
    await stepOf($, 'P1')
    expect(got.models).toEqual([undefined])
    expect(got.efforts).toEqual({ P1: 'medium' })
    expect([...m].find(([p]) => p.endsWith('-spawn.md'))?.[1]).toContain('managed: unknown')
    await $.agent.spawn({ subagentType: 'pa-claude', prompt: 'x' })
    expect(got.runs.length).toBe(1) // Claude asks for no row
    expect(got.models).toEqual([undefined, undefined])
  })

  test('3.24 T17g: a thinker\'s first request writes its start line, its facts naming that request\'s model and effort', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    ladder(on, '{"agent":"pa-thinker","managed":false,"model":"","effort":"","tier":"max20","source":"default"}')
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'pa-thinker', prompt: 'x', description: 'Review the plan' })
    await stepOf($, 'P1')
    const recs = [...m].filter(([p]) => p.startsWith(`${ROOT}/.run/mod/S1/feed/in/`)).map(([, t]) => JSON.parse(t) as Record<string, unknown>)
    expect(recs.map(r => [r['agent'], r['type'], r['text']])).toEqual([['P1', 'pa-thinker', 'Starting: Review the plan']])
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/P1.json`) ?? '{}') as Record<string, unknown>
    expect([f['model'], f['effort']]).toEqual(['claude-opus-5-5', 'medium'])
  })

  test('3.25 T6b: a hinted spawn gets its own row (model, effort on every request); a second expert at medium keeps medium; the hint is not shown', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const runs: (readonly string[])[] = []
    const models: (string | undefined)[] = []
    const efforts: Record<string, unknown[]> = {}
    const BASE = { managed: true, tier: 'max20', source: 'credentials' }
    on('process.run', async ($, e) => {
      runs.push(e.argv)
      const hi = e.argv.includes('--effort')
      const row = hi ? { agent: 'pa-expert@high', model: 'claude-fable-5-1', effort: 'high' } : { agent: 'pa-expert', model: 'claude-opus-5-5', effort: 'medium' }
      return { value: { exitCode: 0, stdout: `${JSON.stringify({ ...row, ...BASE })}
`, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    })
    let n = 0
    on('agent.spawn', async ($, e) => {
      models.push(e.model)
      n += 1
      return { model: e.model ?? 'claude-opus-5-5', agentId: `E${String(n)}` }
    })
    on('turn.step', async function* ($, e) {
      ;(efforts[e.agentId ?? 'main'] ??= []).push(e.effort)
      yield { kind: 'text', index: 0, text: 'ok' } as const
      return { turnId: e.turnId, index: e.index, answer: 'ok', toolUses: [], stopReason: 'end_turn', usage: null }
    })
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'pa-expert', prompt: 'x', description: 'T1 add function [effort high]' })
    await $.agent.spawn({ subagentType: 'pa-expert', prompt: 'y', description: 'T2 sub function' })
    expect(models).toEqual(['claude-fable-5-1', 'claude-opus-5-5'])
    expect(runs.map(a => a.includes('--effort') ? a[a.indexOf('--effort') + 1] : '')).toEqual(['high', ''])
    const f = JSON.parse(m.get(`${ROOT}/.run/mod/S1/agents/E1.json`) ?? '{}') as Record<string, unknown>
    expect([f['model'], f['effort'], f['description']]).toEqual(['claude-fable-5-1', 'high', 'T1 add function'])
    for (let i = 0; i < 2; i += 1) {
      await stepOf($, 'E1')
      await stepOf($, 'E2')
    }
    expect(efforts).toEqual({ E1: ['high', 'high'], E2: ['medium', 'medium'] })
  })

  test('an unmanaged row sets nothing', async ($, on) => {
    mock.clock(on)
    world(on, { [`${ROOT}/.claude/pa.json`]: PA_PY }, { USERPROFILE: 'C:\\Users\\u' })
    const got = ladder(on, '{"agent":"general-purpose","managed":false,"model":"claude-sonnet-5-5","effort":"high","tier":"max20","source":"default"}')
    await $.session.start(START)
    await $.agent.spawn({ subagentType: 'general-purpose', prompt: 'x' })
    await stepOf($, 'P1')
    expect(got.models).toEqual([undefined])
    expect(got.efforts).toEqual({ P1: 'medium' })
  })
})

// 3.28.1 T15: background shells per agent in shells.json, from the Bash call to its completion notice.
describe('background shells (3.28.1 T15)', () => {
  const SHELLS = `${ROOT}/.run/mod/S1/shells.json`
  const LIST: Row[] = [{ id: 'C1', description: 'T1.c1', type: 'pa-coder', status: 'running', parentId: 'E1' } as Row]
  const notice = (id: string, status: string, summary: string): string =>
    `<task-notification>\n<task-id>${id}</task-id>\n<status>${status}</status>\n<summary>${summary}</summary>\n</task-notification>`
  const append = async ($: Engine, text: string): Promise<void> => {
    await $.session.append({ message: { type: 'user', role: 'user', content: [{ type: 'text', text }] },
      door: 'delivery', origin: { kind: 'task-notification' }, uuid: `u${String(text.length)}`, agentId: 'C1' } as never)
  }
  const shells = (m: Map<string, string>): Record<string, { id: string; name: string; type: string; exit?: number }[]> =>
    JSON.parse(m.get(SHELLS) ?? '{}') as Record<string, { id: string; name: string; type: string; exit?: number }[]>
  test('a background Bash call is recorded by its script name; a clean completion notice removes it', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    on('tool.call', { tool: 'Bash' }, async () => ({ result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b1' } }) as never)
    await $.session.start(START)
    await $.tool.call({ tool: 'Bash', command: 'cd x && bash tools/bake_rest.sh --full', run_in_background: true, agentId: 'C1' } as never)
    expect(shells(m)['C1']?.map(s => [s.id, s.name, s.type])).toEqual([['b1', 'bake_rest.sh', 'pa-coder']])
    await append($, notice('b1', 'completed', 'Background command "bake" completed (exit code 0)'))
    expect(shells(m)).toEqual({})
  })

  test('a failed shell keeps its record with the exit code until its agent reports', async ($, on) => {
    mock.clock(on)
    const m = world(on, { [`${ROOT}/.claude/pa.json`]: ON }, {}, LIST)
    on('tool.call', { tool: 'Bash' }, async () => ({ result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b2' } }) as never)
    on('tool.call', { tool: 'SendMessage' }, async () => ({ result: 'sent' }) as never)
    await $.session.start(START)
    await $.tool.call({ tool: 'Bash', command: 'make gate', run_in_background: true, agentId: 'C1' } as never)
    await append($, notice('b2', 'failed', 'Background command "make gate" failed with exit code 2'))
    expect(shells(m)['C1']?.map(s => [s.name, s.exit])).toEqual([['make', 2]])
    await $.tool.call({ tool: 'SendMessage', to: 'E1', message: 'STATUS: blocked', agentId: 'C1' } as never)
    expect(shells(m)).toEqual({})
  })
})
