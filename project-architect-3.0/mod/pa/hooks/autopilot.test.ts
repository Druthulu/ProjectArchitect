// Hook tests of the autopilot's loop (3.23 T5a) through the kit's engine `$`: pick → spawn, capture, routing, the window
// hold, Git Bash, and the router skipping a loop agent's hand-back. Files, processes and agents are answered by maps
// beneath the plugin.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { GIT_WRITE_MS, gitWrite, lastAssistantText, resolveBash, subagentTranscript } from './autopilot'
import { stamp } from './journal'
import { CONTINUE_TEXT, offlineNotice, RELOAD_NOTICE, START_CHECK } from './loop'
import { tailOf } from './trim'

const ROOT = '/r'
/** 3.29.1 T8: the start hook's note for session S1, timed after any test clock's start (the tests' default). */
const START_DONE = '{"session":"S1","at":9999999999,"update":false}\n'
const BASE = `${ROOT}/.run/mod`
const LOOP = `${BASE}/loop.json`
const PLAN = 'phase-ends/current/PHASE_PLAN.md'
const BASH = 'C:/Git/bin/bash.exe'
const PA = JSON.stringify({ mod: 'on', warmer: 'off', upgrade: 'ask', python: 'py', bash: BASH, external: { remind: false } }) // 3.3 T7: no reminder
const START = { cwd: ROOT, surface: null, isInteractive: false } as const
const ISTART = { cwd: ROOT, surface: null, isInteractive: true } as const
const JDIR = `${ROOT}/.claude-state/journal`
const T1 = {
  id: 'T1', status: 'next', agent: 'pa-expert', coder: 'opus55', effort: 'medium', review: false, wait_for: null,
  title: 'add function', done_when: 'add(2, 3) == 5', verify: 'PY -m unittest', reads: [], files: ['calc/add.py'], deps: [], est_ctx: '40k',
}

const norm = (p: string): string => p.replace(/\\/g, '/').replace(/^[A-Za-z]:/, '')

type Agent = { id: string; status: 'running' | 'pending' | 'waiting' | 'idle' | 'completed' | 'killed' | 'failed'; parentId?: string; type: string; description?: string }
type W = {
  files: Map<string, string>
  runs: string[][]
  spawns: { type: string; prompt: string; description: string }[]
  sends: string[]
  /** `$.session.send` delivers (default) or not (T5d1). */
  deliver?: boolean
  list: Agent[]
  /** Process answers: the first whose key every argv element starts the argv with (joined by spaces). */
  answer: (argv: string[]) => { exitCode: number; stdout: string; stderr: string }
  /** A run that does not end until this promise does (3.23 T13d.c4: a slow ledger recalc). */
  hold?: (argv: string[]) => Promise<void> | undefined
  /** 3.24 T10b: the detached recalc's done-file is written at its launch (default), or not (`false`: still running). */
  recalcDone?: boolean
  /** `$.fs.stat` mtimes by path (3.24 T8: a REVIEW.md older than the summary is rebuilt); 0 when absent. */
  mtimes?: Record<string, number>
  /** 3.32.1 T5: the next spawns' refusals, in order (each one taken); empty or absent: spawns succeed. */
  spawnDeny?: string[]
  /** 3.32.15: the journal reads (`journal_rollup.py entries`), answered from the files, kept apart from runs. */
  entryReads: string[][]
  /** 3.32.15: every path the mod read. */
  reads: string[]
}

/** 3.32.15: `tools/journal_rollup.py entries` over the files, as the Python tool answers: both forms (a folder's copy of
 * a stem in both), name order, ASCII JSON lines; --since, --after, --match, --last, --budget-chars, --max-chars. */
function journalEntries(files: ReadonlyMap<string, string>, argv: readonly string[]): { exitCode: number; stdout: string; stderr: string } {
  const opt = (k: string): string => {
    const eq = argv.find(a => a.startsWith(`${k}=`))
    const i = argv.indexOf(k)
    return eq !== undefined ? eq.slice(k.length + 1) : i < 0 ? '' : argv[i + 1] ?? ''
  }
  const [since, after, re] = [opt('--since'), opt('--after'), new RegExp(opt('--match'))]
  const [last, budget, max] = [Number(opt('--last')) || 0, Number(opt('--budget-chars')) || 0, Number(opt('--max-chars')) || 0]
  const STEM_RE = /^[0-9]{6}-[0-9]{3,}[rwx]-(?:T[0-9]+|[a-z]+)-[a-z]+$/
  const got = new Map<string, string>()
  const put = (date: string, stem: string, path: string, text: string): void => {
    if (date >= since && (after === '' || `${date}/${stem}` >= after) && re.test(stem)) {
      got.set(`${date}/${stem}`, JSON.stringify({ path, name: `${date}/${stem}.md`, text: max > 0 ? text.slice(0, max) : text }))
    }
  }
  const at = (p: string, form: RegExp): RegExpExecArray | null => (p.startsWith(`${JDIR}/`) ? form.exec(p.slice(JDIR.length + 1)) : null)
  for (const [p, text] of files) { // packed days first: a folder's copy of a stem replaces its packed one
    const m = at(p, /^(\d{4}-\d{2}-\d{2})\.md$/)
    let stem = ''
    let buf: string[] = []
    const flush = (): void => {
      if (m && stem !== '') put(m[1], stem, `.claude-state/journal/${m[1]}.md#${stem}`, buf.join('\n'))
    }
    for (const line of m ? text.split('\n') : []) {
      const h = /^## (\S+)$/.exec(line)
      if (h && STEM_RE.test(h[1])) {
        flush()
        stem = h[1]
        buf = []
      } else if (stem !== '') buf.push(line)
    }
    flush()
  }
  for (const [p, text] of files) {
    const m = at(p, /^(\d{4}-\d{2}-\d{2})\/([^/]+)\.md$/)
    if (m && STEM_RE.test(m[2])) put(m[1], m[2], `.claude-state/journal/${m[1]}/${m[2]}.md`, text)
  }
  let lines = [...got.keys()].sort().map(k => got.get(k) ?? '')
  lines = last > 0 ? lines.slice(-last) : lines
  if (budget > 0) {
    let total = 0
    const kept: string[] = []
    for (const l of [...lines].reverse()) {
      if (kept.length > 0 && total + l.length + 1 > budget) break
      kept.unshift(l)
      total += l.length + 1
    }
    lines = kept
  }
  return { exitCode: 0, stdout: lines.map(l => `${l}\n`).join(''), stderr: '' }
}

function world(on: On, files: Record<string, string>, env: Record<string, string> = {}): W {
  const w: W = {
    // 3.29.1 T8: the start hook's check has answered (no update) unless a test says otherwise
    files: new Map(Object.entries({ [`${ROOT}/.claude/pa.json`]: PA, [`${ROOT}/${PLAN}`]: '# plan\n', [`${ROOT}/${START_CHECK}`]: START_DONE, ...files })),
    runs: [], spawns: [], sends: [], list: [], entryReads: [], reads: [],
    answer: argv => defaultAnswer(argv),
  }
  // T11-fix2: a seeded running loop.json's agent is listed (idle) unless a test clears the list: absent, it counts as killed.
  const doc = JSON.parse(files[LOOP] ?? '{}') as { state?: string; task?: { agentId?: string; agent?: string } | null }
  const seeded = doc.state === 'running' ? doc.task : null
  if ((seeded?.agentId ?? '') !== '') {
    w.list = [{ id: seeded?.agentId ?? '', status: 'idle', type: seeded?.agent ?? '' }]
  }
  mock.env(on, env)
  on('session.root', async () => ({ value: ROOT }))
  on('session.id', async () => ({ value: 'S1' }))
  on('session.version', async () => ({ value: { version: '2.1.293', base: '2.1.293' } }))
  on('fs.read', async ($, e) => {
    w.reads.push(norm(e.path))
    const t = w.files.get(norm(e.path))
    return t === undefined ? { deny: `ENOENT ${norm(e.path)}` } : { value: t }
  })
  on('fs.write', async ($, e) => {
    w.files.set(norm(e.path), e.text)
    return { value: undefined }
  })
  on('fs.exists', async ($, e) => {
    const d = norm(e.path)
    return { value: [...w.files.keys()].some(p => p === d || p.startsWith(`${d}/`)) }
  })
  on('fs.stat', async ($, e) => {
    const d = norm(e.path)
    const t = w.files.get(d)
    return t === undefined ? { deny: `ENOENT ${d}` } : { value: { kind: 'file', size: t.length, mtimeMs: w.mtimes?.[d] ?? 0, isLink: false } }
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
  on('classic.SessionStart', async () => ({}))
  on('process.run', async ($, e) => {
    if (e.argv[1] === 'tools/journal_rollup.py' && e.argv[2] === 'entries') {
      w.entryReads.push([...e.argv])
      return { value: journalEntries(w.files, e.argv) }
    }
    if (e.argv[1] !== '-c' && e.argv[1] !== 'tools/journal_rollup.py') {
      w.runs.push([...e.argv]) // the router's prune (python -c) and journal roll-up (3.23 T7) are not the loop's
    }
    await w.hold?.([...e.argv])
    const di = e.argv.indexOf('--done-file') // 3.24 T10b: the detached recalc, done at once unless recalcDone is false
    if (di > 0 && w.recalcDone !== false) {
      const sessions = e.argv.flatMap((x, i) => (e.argv[i - 1] === '--session' ? [x] : []))
      w.files.set(norm(e.argv[di + 1] ?? ''), JSON.stringify({ exit: 0, secs: 0.4, sessions, at: 1 }))
    }
    return { value: w.answer([...e.argv]) }
  })
  on('agent.spawn', async ($, e) => {
    w.spawns.push({ type: e.subagentType ?? '', prompt: e.prompt, description: e.description ?? '' })
    const deny = w.spawnDeny?.shift()
    if (deny !== undefined) {
      return { deny }
    }
    const id = `E${String(w.spawns.length)}`
    w.list = [...w.list.filter(a => a.id !== id), { id, status: 'running', type: e.subagentType ?? '' }]
    return { agentId: id, model: 'opus' }
  })
  on('agent.list', async () => ({ value: w.list.map(a => ({ description: '', ...a })) }))
  on('session.send', async ($, e) => {
    w.sends.push(e.text)
    return w.deliver === false ? { isDelivered: false, reason: 'no such agent' } : { isDelivered: true }
  })
  // T11-fix2: a turn end (turn.complete, or the hand-back's task-notification) leaves a running agent idle, as the engine does.
  const ended = (id: string): void => {
    w.list = w.list.map(a => (a.id === id && a.status === 'running' ? { ...a, status: 'idle' } : a))
  }
  on('prompt.submit', async ($, e) => {
    const id = /<task-id>([^<]*)<\/task-id>/.exec(e.text)?.[1]
    if (id !== undefined) {
      ended(id)
    }
    return { text: e.text, origin: e.origin }
  })
  on('turn.complete', async ($, e) => {
    ended(e.agentId ?? '')
    return { text: e.answer }
  })
  return w
}

const ok = (stdout = ''): { exitCode: number; stdout: string; stderr: string } => ({ exitCode: 0, stdout, stderr: '' })
function defaultAnswer(argv: string[]): { exitCode: number; stdout: string; stderr: string } {
  const a = argv.slice(1).join(' ')
  if (argv[0] === 'git') {
    return argv[1] === 'rev-parse' ? ok('abc1234\n') : argv[1] === 'rev-list' ? ok() : ok('C:/Git/mingw64/libexec/git-core\n')
  }
  if (a.startsWith('tools/launch.py')) {
    return ok('{"mode": "router", "reason": "approved plan"}\n')
  }
  if (a.startsWith('tools/plan_edit.py next')) {
    return ok(`${JSON.stringify({ kind: 'task', task: T1, promoted: null })}\n`)
  }
  return ok()
}

type Loop = { state: string; holds: string[]; resumeAt: number; task: { id: string; agent: string; agentId: string; attempt: number; kind: string; startSha: string; contract: string | null } | null }
const loopOf = (w: W): Loop => JSON.parse(w.files.get(LOOP) ?? '{}') as Loop
const entries = (w: W, kind: string): string[] =>
  [...w.files.keys()].filter(p => p.startsWith(`${JDIR}/`) && p.endsWith(`-${kind}.md`)).sort()
const ran = (w: W, prefix: string): string[][] => w.runs.filter(r => r.slice(1).join(' ').startsWith(prefix))
const notification = (id: string, result: string): string =>
  `<task-notification>\n<task-id>${id}</task-id>\n<result>${result}</result>\n</task-notification>`
const complete = (agentId: string, answer: string) =>
  ({ turnId: `t-${agentId}`, agentId, answer, durationMs: 1, isAborted: false, reason: 'answer' as const })

describe('autopilot loop', () => {
  test('no plan, no loop: one launch.py run (cached, T6a2) and no loop.json', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.files.delete(`${ROOT}/${PLAN}`)
    await $.session.start(START)
    await clock.advance(4_000)
    expect(w.runs.map(r => r.slice(1, 3).join(' '))).toEqual(['tools/launch.py --state-json'])
    expect(w.files.has(LOOP)).toBe(false)
  })

  test('pick → spawn: status set, the expert spawned with its brief, loop.json and T1/start', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.answer = argv => (argv[1] === 'tools/card.py' ? ok('## Developer <!-- roles: expert -->\n- plain words\n') : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.runs.map(r => r.slice(1, 3).join(' '))).toEqual([
      'tools/launch.py --state-json', 'tools/managed.py drift', 'tools/plan_edit.py next', 'tools/status.py window-gate', 'tools/status.py set', 'rev-parse HEAD',
      'tools/card.py slice', 'tools/records.py inforce', 'tools/plan_edit.py show',
    ])
    // 3.31 T4: the in-force outcomes that bind work, asked beside the card; none printed: no block.
    expect(ran(w, 'tools/records.py')[0]).toEqual(['py', 'tools/records.py', 'inforce', '--binds-work', '--max-tokens', '4000'])
    expect(w.spawns[0]?.prompt).not.toContain('OUTCOMES IN FORCE')
    // 3.24 T7b: the brief carries the card's expert slice.
    expect(ran(w, 'tools/card.py')[0]).toEqual(['py', 'tools/card.py', 'slice', 'expert'])
    expect(w.spawns[0]?.prompt).toContain('\nCARD (PY tools/card.py slice expert; the card\'s expert sections, already read):\n## Developer <!-- roles: expert -->\n- plain words')
    expect(ran(w, 'tools/status.py set')[0]).toEqual(['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--coder', 'opus55', '--kind', 'task', '--attempt', '1', '--title', 'add function', '--session', 'S1'])
    expect(w.spawns.length).toBe(1)
    // The kit resolves subagentType against its own agent definitions (none here): the type is checked on loop.json.
    expect(w.spawns[0]?.description).toBe('T1 add function')
    expect(w.spawns[0]?.prompt.startsWith('TASK: T1 add function\n')).toBe(true)
    const l = loopOf(w)
    expect([l.state, l.task?.agent, l.task?.agentId, l.task?.startSha, l.task?.attempt]).toEqual(['running', 'pa-expert', 'E1', 'abc1234', 1])
    expect(entries(w, 'T1-start').length).toBe(1)
    // Nothing more while it runs: no second spawn.
    await clock.advance(10_000)
    expect(w.spawns.length).toBe(1)
  })

  // 3.32.1 T5 (#6): Claude Code's auto-mode check refusing with no verdict is transient: the pick tries again.
  const NO_VERDICT = 'Permission for this action was denied: the auto mode classifier gave no verdict'
  const ticks = async (clock: { advance: (ms: number) => Promise<unknown> }, n: number): Promise<void> => {
    for (let i = 0; i < n; i += 1) {
      await clock.advance(2_000)
    }
  }
  const holdLines = (w: W): string => entries(w, 'mod-hold').map(p => w.files.get(p) ?? '').join('\n')
  test('3.32.1 T5: a no-verdict refusal is journaled, not held; the pick runs again after 30 s and spawns', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.spawnDeny = [NO_VERDICT]
    await $.session.start(START)
    await ticks(clock, 1)
    const r = loopOf(w) as Loop & { spawnRetry?: { task: string; n: number } | null }
    expect([w.spawns.length, r.holds, r.task, r.spawnRetry?.task, r.spawnRetry?.n]).toEqual([1, [], null, 'T1', 1])
    expect(holdLines(w)).toContain('the pick runs again in 30 s (1 of 3)')
    await ticks(clock, 13)
    expect(w.spawns.length).toBe(1)
    await ticks(clock, 2)
    const l = loopOf(w) as Loop & { spawnRetry?: unknown }
    expect([w.spawns.length, l.state, l.task?.agentId, l.holds, l.spawnRetry]).toEqual([2, 'running', 'E2', [], null])
  })

  test('3.32.1 T5: the third no-verdict refusal of a task attempt holds question, saying so', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.spawnDeny = [NO_VERDICT, NO_VERDICT, NO_VERDICT]
    await $.session.start(START)
    await ticks(clock, 40)
    expect([w.spawns.length, loopOf(w).holds]).toEqual([3, ['question']])
    expect(holdLines(w)).toContain('auto-mode check gave no answer 3 times')
  })

  test('3.32.1 T5: any other spawn refusal holds question at once', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.spawnDeny = ['Permission for this action was denied by a hook']
    await $.session.start(START)
    await ticks(clock, 20)
    expect([w.spawns.length, loopOf(w).holds]).toEqual([1, ['question']])
    expect(holdLines(w)).not.toContain('the pick runs again')
  })

  test('3.31 T4: the in-force outcomes that bind work ride after the card; a failed run adds none', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.answer = argv => (argv[1] === 'tools/card.py' ? ok('## Developer <!-- roles: expert -->\n- plain words\n')
      : argv[1] === 'tools/records.py' ? ok('- R3 decision: keep the ladder\n  binds work: yes\n') : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.spawns[0]?.prompt).toContain('- plain words\nOUTCOMES IN FORCE (PY tools/records.py inforce --binds-work; binding):\n- R3 decision: keep the ladder\n  binds work: yes')
  })

  test('3.23 T7: an entry the naming rejects (who T6b) is a journal.bad debug event, not a silent drop', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { PA_MOD_DEBUG: '1' })
    w.answer = argv => (argv[1] === 'tools/plan_edit.py' && argv[2] === 'next'
      ? ok(`${JSON.stringify({ kind: 'task', task: { ...T1, id: 'T6b' }, promoted: null })}\n`) : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.spawns.length).toBe(1)
    expect(entries(w, 'start')).toEqual([])
    const bad = [...w.files].filter(([p]) => p.endsWith('-autopilot.journal.bad.json')).map(([, t]) => JSON.parse(t) as Record<string, unknown>)
    expect(bad.length).toBe(1)
    expect([bad[0]?.['who'], bad[0]?.['kind']]).toEqual(['T6b', 'start'])
    expect(String(bad[0]?.['err'])).toContain('bad who')
  })

  for (const [name, task] of [['a task at high', { ...T1, effort: 'high' }], ['an old expert-fable column with no effort', { ...T1, agent: 'expert-fable', effort: null }]] as const) {
    test(`3.25 T6b: ${name} spawns pa-expert with \`[effort high]\``, async ($, on) => {
      const clock = mock.clock(on, { now: 1_000 })
      const w = world(on, {})
      w.answer = argv => (argv[1] === 'tools/plan_edit.py' && argv[2] === 'next'
        ? ok(`${JSON.stringify({ kind: 'task', task, promoted: null })}
`) : defaultAnswer(argv))
      await $.session.start(START)
      await clock.advance(2_000)
      expect(w.spawns[0]?.description).toBe('T1 add function [effort high]') // the type is checked on loop.json (the kit resolves it)
      expect([loopOf(w).task?.agent, loopOf(w).task?.effort]).toEqual(['pa-expert', 'high'])
    })
  }

  test('trial knobs reach the brief only for the task they name', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { PA_MOD_TRIAL_QUESTION: 'T1:use floats', PA_MOD_TRIAL_HANDOFF: 'T2' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.spawns[0]?.prompt).toContain('NOTE: hand back `question` asking whether to use floats')
    expect(w.spawns[0]?.prompt).not.toContain('handoff after')
  })

  test('capture from a hand-back, done: set-status, the commit under Git Bash, T1/end, then the close hold', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    let done = false
    w.answer = argv => {
      const a = argv.slice(1).join(' ')
      if (a.startsWith('tools/plan_edit.py set-status')) {
        done = true
      }
      if (done && a.startsWith('tools/plan_edit.py next')) {
        return ok('{"kind": "none", "reason": "done", "waiting_on": []}\n')
      }
      return defaultAnswer(argv)
    }
    // An interim hand-back without STATUS is ignored.
    await $.prompt.submit({ text: notification('E1', '.'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(2_000)
    expect(loopOf(w).state).toBe('running')
    await $.prompt.submit({ text: notification('E1', 'STATUS: done\nLOG: logs/T1.md\nCOMMIT: def5678'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
    expect(w.runs.find(r => r[1] === 'tools/commit_task.sh')).toEqual([BASH, 'tools/commit_task.sh', 'router', 'T1 done', PLAN])
    const [end] = entries(w, 'T1-end')
    expect(w.files.get(end ?? '')).toContain('COMMIT: def5678')
    await clock.advance(2_000)
    const l = loopOf(w)
    expect([l.state, l.task, l.holds]).toEqual(['boundary', null, ['close']])
    expect(entries(w, 'mod-plan').length).toBe(1)
  })

  // 3.25 T3: the commit comes from git (the newest since the task's start), never the report; none is still done.
  test('a plain done with no new commit is done: set-status, no hold', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'Done. Nothing needed changing.'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
    expect(ran(w, 'tools/status.py wait question').length).toBe(0)
    expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).not.toContain('(commit ')
  })

  test('3.30: a report\'s EXT-HEALTH proposals go to Claude in one notice; the done routes as usual', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'done: 2 of 9 cracked.\n' +
      'EXT-HEALTH: project: Bash(mipsel-linux-gnu-objdump:*) (cG05: objdump denied 3 times)\n' +
      'EXT-HEALTH: model openrouter-minimax/minimax: use each item\'s full budget (cG05: 6 walls in 1-3 min)'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
    const n = notices(w).map(x => x.input.text).filter(t => t.includes('EXT-HEALTH') || t.includes('later external coders'))
    expect(n).toEqual([
      '‹pa› T1\'s expert proposes changes for later external coders. Tell the developer each in plain words with its ' +
      'evidence and apply only the ones they approve (the pa-external-agents skill, "Learning from each run"):\n' +
      '- project: Bash(mipsel-linux-gnu-objdump:*) (cG05: objdump denied 3 times)\n' +
      '- model openrouter-minimax/minimax: use each item\'s full budget (cG05: 6 walls in 1-3 min)'])
  })

  test('a done\'s commit comes from git: rev-list <startSha>..HEAD, in T1/end', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    w.answer = argv => (argv[0] === 'git' && argv[1] === 'rev-list' ? ok('fedcba9876543210\n') : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', '**Done** — added the function.'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(w.runs.find(r => r[0] === 'git' && r[1] === 'rev-list')).toEqual(['git', 'rev-list', '--max-count=1', 'abc1234..HEAD'])
    expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).toContain('(commit fedcba987654)')
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
  })

  test('T13b: the captured contract SUMMARY is kept in loop.json by task id', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc\nSUMMARY: The tool now adds two numbers.'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect((JSON.parse(w.files.get(LOOP) ?? '{}') as { summaries?: unknown }).summaries)
      .toEqual({ T1: { title: T1.title, summary: 'The tool now adds two numbers.' } })
  })

  test('a refused set-status is a block', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    w.answer = argv => (argv[1] === 'tools/plan_edit.py' && argv[2] === 'set-status' ? { exitCode: 2, stdout: '', stderr: 'no Verified: line' } : defaultAnswer(argv))
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect(w.runs.some(r => r[1] === 'tools/commit_task.sh')).toBe(false)
    expect(loopOf(w).holds).toEqual(['question'])
  })

  test('capture from turn.complete, handoff: status set handoff attempt 2, respawn with PROGRESS', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'Wrote the progress file.\n\nSTATUS: handoff\nLOG: logs/T1.md'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py set').at(-1)).toEqual(['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--kind', 'handoff', '--attempt', '2', '--session', 'S1'])
    expect(w.spawns.length).toBe(2)
    expect(w.spawns[1]?.prompt).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md')
    const l = loopOf(w)
    expect([l.state, l.task?.agentId, l.task?.attempt, l.task?.kind]).toEqual(['running', 'E2', 2, 'handoff'])
    expect(entries(w, 'T1-end').length).toBe(1)
    expect(entries(w, 'T1-start').length).toBe(2)
  })

  test('review: task_log review when REVIEW.md is absent, status wait REVIEW.md, held(review)', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review\nLOG: logs/T1.md'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect(ran(w, 'tools/task_log.py review')[0]).toEqual(['py', 'tools/task_log.py', 'review', 'T1'])
    expect(ran(w, 'tools/status.py wait')[0]).toEqual(['py', 'tools/status.py', 'wait', 'REVIEW.md'])
    expect(loopOf(w).holds).toEqual(['review'])
    await clock.advance(6_000)
    expect(ran(w, 'tools/task_log.py review').length).toBe(1)
  })

  test('review with REVIEW.md present skips task_log', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/phase-ends/current/REVIEW.md`]: '# review\n' })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect(ran(w, 'tools/task_log.py').length).toBe(0)
    expect(loopOf(w).holds).toEqual(['review'])
  })

  // 3.24 T8: a REVIEW.md left from an earlier stop (older than the task's summary) is rebuilt.
  test('review with a REVIEW.md older than the summary rebuilds it', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const review = `${ROOT}/phase-ends/current/REVIEW.md`
    const summary = `${ROOT}/phase-ends/current/tasks/T1.md`
    const w = world(on, { [review]: '# old review\n', [summary]: '# T1\n' })
    w.mtimes = { [review]: 100, [summary]: 200 }
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/task_log.py review')[0]).toEqual(['py', 'tools/task_log.py', 'review', 'T1'])
    expect(loopOf(w).holds).toEqual(['review'])
  })

  for (const status of ['question', 'blocked']) {
    test(`${status}: status wait question, held(question)`, async ($, on) => {
      const clock = mock.clock(on, { now: 1_000 })
      const w = world(on, {})
      await $.session.start(START)
      await clock.advance(2_000)
      await $.turn.complete(complete('E1', `STATUS: ${status}
QUESTION: which?
RECOMMENDED: A`))
      await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
      await clock.advance(4_000)
      expect(ran(w, 'tools/status.py wait question').length).toBe(1)
      expect(loopOf(w).holds).toEqual(['question'])
      expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).toContain('QUESTION: which?')
    })
  }

  test('runEnded: a busy child or a recent turn keeps waiting; then the transcript gives the contract', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const tr = subagentTranscript('C:\\p\\S1.jsonl', 'S1', 'E1')
    const rows = [
      { type: 'assistant', message: { content: [{ type: 'text', text: 'Briefing the coder.' }] } },
      { type: 'user', message: { content: 'ok' } },
      { type: 'assistant', message: { content: [{ type: 'text', text: 'STATUS: done\nCOMMIT: 9abcdef' }] } },
    ]
    const w = world(on, { [norm(tr)]: rows.map(r => JSON.stringify(r)).join('\n') })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'startup', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    await clock.advance(2_000)
    releaseReq(w, 3_000, { hold: 'session' }) // 3.23 T13d.c11: the session start waits for the developer
    w.list = [...w.list, { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.turn.complete(complete('E1', 'Waiting on the coder.'))
    await clock.advance(62_000)
    expect(loopOf(w).state).toBe('running')
    w.list = w.list.map(a => (a.id === 'C1' ? { ...a, status: 'completed' } : a))
    await $.turn.complete(complete('E1', 'Still no status line.'))
    await clock.advance(10_000)
    expect(loopOf(w).state).toBe('running')
    await clock.advance(6_000)
    await clock.advance(2_000)
    await clock.advance(4_000) // T5d2a.c2: 15 s from the tick that saw C1 idle
    expect(loopOf(w).state).toBe('running') // T11-fix: no STATUS yet, so 15 s is not the end
    await clock.advance(560_000)
    expect(loopOf(w).state).toBe('running')
    await clock.advance(30_000) // 10 min of silence
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
    expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).toContain('COMMIT: 9abcdef')
  })

  test('an ended run with no contract anywhere is a block ("no contract")', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'I think it works'))
    await clock.advance(64_000)
    expect(loopOf(w).holds).toEqual([]) // T11-fix: no STATUS waits NO_CONTRACT_QUIET_MS
    await clock.advance(540_000)
    expect(loopOf(w).holds).toEqual(['question'])
    expect(entries(w, 'T1-end').length).toBe(1)
    expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).toContain('no contract')
  })

  test('3.25 T3: an ended run whose last text has no status word routes a block carrying that text', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const tr = subagentTranscript('C:\\p\\S1.jsonl', 'S1', 'E1')
    const rows = [{ type: 'assistant', message: { content: [{ type: 'text', text: 'Finished the parser; the gate passes.' }] } }]
    const w = world(on, { [norm(tr)]: rows.map(r => JSON.stringify(r)).join('\n') })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'startup', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    await clock.advance(2_000)
    releaseReq(w, 3_000, { hold: 'session' })
    await clock.advance(4_000) // the release, then the spawn
    await $.turn.complete(complete('E1', 'Finished the parser; the gate passes.'))
    await clock.advance(64_000)
    await clock.advance(540_000)
    await clock.advance(70_000) // 10 min of silence (NO_CONTRACT_QUIET_MS)
    expect(loopOf(w).holds).toEqual(['question'])
    const end = w.files.get(entries(w, 'T1-end')[0] ?? '') ?? ''
    expect(end).toContain('Finished the parser; the gate passes.')
    expect(end).not.toContain('no contract')
  })

  test('the window hold: exit 3 holds until status.json resume_at, then the spawn', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/.run/status.json`]: JSON.stringify({ kind: 'paused', resume_at: '1970-01-01T00:01:00Z' }) })
    let pct = 95
    w.answer = argv => (argv[1] === 'tools/status.py' && argv[2] === 'window-gate' && pct >= 90 ? { exitCode: 3, stdout: 'pause', stderr: '' } : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    let l = loopOf(w)
    expect([l.holds, l.resumeAt]).toEqual([['window'], 60_000])
    expect(w.spawns.length).toBe(0)
    pct = 50
    await clock.advance(30_000)
    expect(w.spawns.length).toBe(0)
    await clock.advance(30_000)
    await clock.advance(2_000)
    l = loopOf(w)
    expect([l.holds, l.resumeAt, l.task?.agentId]).toEqual([[], 0, 'E1'])
  })

  test('another session owning a fresh heartbeat keeps this one out', async ($, on) => {
    const clock = mock.clock(on, { now: 100_000 })
    const other = JSON.stringify({ v: 1, ownerSid: 'S0', heartbeatAt: 99_000, state: 'boundary', holds: [] })
    const w = world(on, { [LOOP]: other })
    await $.session.start(START)
    await clock.advance(4_000)
    expect(w.runs.length).toBe(0)
  })
})

describe('autopilot Git Bash', () => {
  test('resolution order: pa.json, env, git --exec-path; never a bare bash on win32', () => {
    expect(resolveBash('D:/Git/bin/bash.exe', 'E:/b.exe', '', true)).toBe('D:/Git/bin/bash.exe')
    expect(resolveBash('bash', 'E:/b.exe', '', true)).toBe('E:/b.exe')
    expect(resolveBash('bash', '', 'C:\\Program Files\\Git\\mingw64\\libexec\\git-core', true)).toBe('C:/Program Files/Git/bin/bash.exe')
    expect(resolveBash('bash', '', '', true)).toBe('')
    expect(resolveBash('bash', '', '', false)).toBe('bash')
    expect(resolveBash('', '', '/usr/lib/git-core', false)).toBe('bash')
  })

  test('a bare pa.json bash on win32 commits under the bash derived from git --exec-path', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/.claude/pa.json`]: JSON.stringify({ mod: 'on', python: 'py', bash: 'bash' }) }, { OS: 'Windows_NT' })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect(w.runs.some(r => r[0] === 'git' && r[1] === '--exec-path')).toBe(true)
    expect(w.runs.find(r => r[1] === 'tools/commit_task.sh')?.[0]).toBe('C:/Git/bin/bash.exe')
  })

  test('lastAssistantText: the last non-empty assistant text', () => {
    const t = ['{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]}}', 'not json',
      '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}'].join('\n')
    expect(lastAssistantText(t)).toBe('a')
    expect(lastAssistantText('')).toBe(null)
  })
})

describe('autopilot git writes (3.28.1 T10)', () => {
  type R = { exitCode: number; stdout: string; stderr: string }
  const script = (answers: R[], lock: string): { calls: number[]; clears: number; go: () => Promise<{ r: R; note: string }> } => {
    const o = { calls: [] as number[], clears: 0, go: async () => ({ r: answers[0] as R, note: '' }) }
    let i = 0
    o.go = async () => gitWrite(['bash', 'tools/commit_task.sh'], async (_argv, ms) => {
      o.calls.push(ms)
      i += 1
      return answers[i - 1] as R
    }, async () => {
      o.clears += 1
      return lock
    })
    return o
  }
  const killed: R = { exitCode: -1, stdout: '', stderr: 'Error: timed out' }
  const locked: R = { exitCode: 1, stdout: '', stderr: "fatal: Unable to create '.git/index.lock': File exists." }

  test('a git write gets its own limit, longer than a process call\'s 60 s', async () => {
    const s = script([ok()], 'none')
    expect((await s.go()).r.exitCode).toBe(0)
    expect(s.calls).toEqual([GIT_WRITE_MS])
    expect(GIT_WRITE_MS).toBeGreaterThan(60_000)
    expect(s.clears).toBe(0)
  })

  test('an ordinary refusal is neither cleared nor retried', async () => {
    const s = script([{ exitCode: 1, stdout: 'refused: nothing staged', stderr: '' }], 'removed')
    expect((await s.go()).note).toBe('')
    expect([s.calls.length, s.clears]).toEqual([1, 0])
  })

  test('killed: the stale lock is removed and the commit retried once', async () => {
    const s = script([killed, ok()], 'removed\n')
    const out = await s.go()
    expect([out.r.exitCode, out.note]).toEqual([0, ''])
    expect([s.calls.length, s.clears]).toEqual([2, 1])
  })

  test('a lock refusal clears and retries; a second failure says so in one line', async () => {
    const s = script([locked, killed], 'removed')
    const out = await s.go()
    expect(out.r.exitCode).toBe(-1)
    expect(out.note).toBe('retried once after removing a stale index.lock')
    expect(s.calls.length).toBe(2)
  })

  test('a running git process keeps the lock: no retry', async () => {
    for (const lock of ['busy', '']) {
      const s = script([killed, ok()], lock)
      const out = await s.go()
      expect(out.note).toBe('index.lock kept: a git process is running, not retried')
      expect(s.calls.length).toBe(1)
    }
  })
})

describe('router skips loop agents', () => {
  test('a loop agent\'s hand-back is neither filed as coder/end nor forwarded', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const loop = JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: 1_000, state: 'running', holds: ['question'],
      task: { id: 'T1', agentId: 'E1', agent: 'pa-expert' } })
    const w = world(on, { [`${BASE}/S1/state.json`]: prior, [LOOP]: loop })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'completed', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: notification('E1', 'STATUS: done\nCOMMIT: abc'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(w.sends.length).toBe(0)
    expect(entries(w, 'coder-end').length).toBe(0)
  })

  test('a loop agent\'s child\'s hand-back is filed and forwarded to the expert (T5c.c3)', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const loop = JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: 1_000, state: 'running', holds: ['question'],
      task: { id: 'T1', agentId: 'E1', agent: 'pa-expert' } })
    const w = world(on, { [`${BASE}/S1/state.json`]: prior, [LOOP]: loop })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' },
      { id: 'C1', status: 'completed', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: notification('C1', 'STATUS: done\nCOMMIT: c0ffee1'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(1_000)
    await clock.advance(1_000)
    expect(w.sends.filter(t => t.startsWith('Hand-back from your background agent C1:') && t.includes('COMMIT: c0ffee1')).length).toBe(1)
    expect(entries(w, 'coder-end').length).toBe(1)
  })
})

describe('the contract at runEnded (T5c.c3)', () => {
  test('a STATUS while a child is busy is interim; WAITING is ignored; the later done wins, no respawn', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    let done = false
    w.answer = argv => {
      const a = argv.slice(1).join(' ')
      if (a.startsWith('tools/plan_edit.py set-status')) {
        done = true
      }
      return done && a.startsWith('tools/plan_edit.py next') ? ok('{"kind": "none", "reason": "done", "waiting_on": []}\n') : defaultAnswer(argv)
    }
    const pending = (): string | undefined => (loopOf(w).task as unknown as { pendingContract?: string } | null)?.pendingContract
    w.list = [...w.list, { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.prompt.submit({ text: notification('E1', 'STATUS: handoff\nLOG: logs/T1.md'), origin: { kind: 'task-notification' }, wait: false })
    await $.turn.complete(complete('E1', 'STATUS: handoff\nLOG: logs/T1.md'))
    await clock.advance(40_000)
    expect(loopOf(w).state).toBe('running')
    expect(pending()).toContain('STATUS: handoff')
    expect(entries(w, 'T1-interim').length).toBe(1)
    expect(entries(w, 'T1-end').length).toBe(0)
    expect(w.spawns.length).toBe(1)
    expect(ran(w, 'tools/status.py set').length).toBe(1)
    await $.turn.complete(complete('E1', 'WAITING: C1 running'))
    await clock.advance(2_000)
    expect(pending()).toContain('STATUS: handoff')
    w.list = w.list.map(a => (a.id === 'C1' ? { ...a, status: 'completed' } : a))
    await $.prompt.submit({ text: notification('E1', 'STATUS: done\nLOG: logs/T1.md\nCOMMIT: def5678'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(10_000)
    expect(loopOf(w).state).toBe('running') // 3.24 T13A: no quiet after the hand-back, but 15 s from C1 going idle after it
    expect(pending()).toContain('STATUS: done')
    await clock.advance(8_000)
    await clock.advance(4_000) // T5d2a.c2: 15 s from the tick that saw C1 idle
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
    const ends = entries(w, 'T1-end')
    expect(ends.length).toBe(1)
    expect(w.files.get(ends[0] ?? '')).toContain('COMMIT: def5678')
    expect(w.spawns.length).toBe(1)
    expect(entries(w, 'T1-start').length).toBe(1)
  })

  test('3.23 T5d2b: a warm ping\'s `.` answer and a `.` hand-back are ignored by the capture', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = [...w.list.map(a => (a.id === 'E1' ? { ...a, status: 'idle' as const } : a)), { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.turn.complete(complete('E1', '.'))
    await $.prompt.submit({ text: notification('E1', '.'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(40_000)
    const t = loopOf(w).task as unknown as { pendingContract?: string | null; contract: string | null } | null
    expect(loopOf(w).state).toBe('running')
    expect(t?.pendingContract ?? null).toBe(null)
    expect(t?.contract).toBe(null)
    expect(entries(w, 'T1-interim').length).toBe(0)
    expect(entries(w, 'T1-end').length).toBe(0)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(0)
    expect(w.spawns.length).toBe(1)
  })

  test('a lone STATUS is routed only at runEnded', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/phase-ends/current/REVIEW.md`]: '# review\n' })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review\nLOG: logs/T1.md'))
    await clock.advance(12_000)
    expect([loopOf(w).state, loopOf(w).holds]).toEqual(['running', []])
    await clock.advance(6_000)
    expect(loopOf(w).holds).toEqual(['review'])
    expect(entries(w, 'T1-end').length).toBe(1)
  })
})

// T5b: stops reach Claude as notices through the router's inbox; release requests are drained by the loop.
const INBOX = `${BASE}/S1/inbox`
type Notice = { id: string; kind: string; caller: string; input: { text: string; toast?: string; to?: string } }
const notices = (w: W): Notice[] => [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && p.endsWith('-notice.json')).sort()
  .map(p => JSON.parse(w.files.get(p) ?? '{}') as Notice)
/** What reached Claude: sends, and the prompts of spawned partners. */
const delivered = (w: W): string[] => [...w.sends, ...w.spawns.map(s => s.prompt)]
/** The kit reports an unknown agent type as '': experts are told apart by their description. */
const experts = (w: W): W['spawns'] => w.spawns.filter(s => s.description.startsWith('T1'))
const toastCauses = (w: W): string[] => w.runs.filter(r => r[2] === 'toast').map(r => r[6] ?? '')
const NOW = 10 * 60 * 60_000
const held = (hold: string, at: number): string => JSON.stringify({
  v: 1, ownerSid: 'S1', heartbeatAt: NOW, state: 'routing', holds: [hold],
  task: { id: 'T1', title: 'add function', agent: 'pa-expert', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task',
    agentId: 'E1', startSha: 'abc', spawnedAt: at, lastCompleteAt: at, contract: `STATUS: ${hold}`, routed: hold, plan: T1 },
})
const releaseReq = (w: W, ms: number, input: Record<string, string>, caller = 'P1'): string => {
  const id = `${String(ms)}-1-release`
  w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'release', caller, input, at: ms }))
  return id
}
const showStatus = (status: string) => (argv: string[]): { exitCode: number; stdout: string; stderr: string } =>
  argv.slice(1).join(' ').startsWith('tools/plan_edit.py show') ? ok(`${JSON.stringify({ ...T1, status })}\n`) : defaultAnswer(argv)
type Ans = (argv: string[]) => { exitCode: number; stdout: string; stderr: string }
/** 3.30.1: the plan's next pick is T2 (the task the developer put first); everything else `inner`. */
const pickT2 = (inner: Ans): Ans => argv => (argv.slice(1).join(' ').startsWith('tools/plan_edit.py next')
  ? ok(`${JSON.stringify({ kind: 'task', task: { ...T1, id: 'T2', title: 'bank the lessons' }, promoted: null })}\n`) : inner(argv))
/** 3.30.1: loop.json's `setAside` record (null when absent). */
const asideOf = (w: W): unknown => (JSON.parse(w.files.get(LOOP) ?? '{}') as { setAside?: unknown }).setAside ?? null
/** 3.30.1: a plan whose `set-status T1 queued` takes (show reads it back), next picks T2, and task_log parks T1's progress. */
const reorderPlan = (w: W): { status: string } => {
  const plan = { status: 'next' }
  w.answer = pickT2(argv => {
    const a = argv.slice(1).join(' ')
    if (a.startsWith('tools/plan_edit.py set-status T1 queued')) {
      plan.status = 'queued'
      return ok('T1: next -> queued\n')
    }
    if (a.startsWith('tools/plan_edit.py show')) {
      return ok(`${JSON.stringify({ ...T1, status: plan.status })}\n`)
    }
    return a.startsWith('tools/task_log.py aside') ? ok('aside: phase-ends/current/logs/T1.aside.md\n') : defaultAnswer(argv)
  })
  return plan
}

describe('stops and releases (T5b)', () => {
  test('review stop: a notice in the router inbox, delivered to Claude; its review toast at Claude\'s presentation', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/phase-ends/current/REVIEW.md`]: '# review\n', [`${BASE}/S1/state.json`]: PRIOR }, { USERPROFILE: 'H' })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    const n = notices(w)
    expect(n.length).toBe(1)
    expect(n[0]?.caller).toBe('loop')
    expect(n[0]?.input.toast).toBe('review')
    expect(n[0]?.input.text).toContain('‹pa› T1 stopped for your review: phase-ends/current/REVIEW.md.')
    expect(n[0]?.input.text).toContain('call release({hold: "review"}) when the developer approves')
    await clock.advance(4_000)
    expect(delivered(w).some(t => t.includes('A loop prompt arrived: ‹pa› T1 stopped for your review'))).toBe(true)
    expect(JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}').sentKind).toBe('other') // 3.28.1 T9b
    expect(toastCauses(w)).toEqual([]) // T13b: none at the notice; Claude's presentation raises it
    await $.turn.complete(complete('P1', 'T1 built it and waits for your review.'))
    await clock.advance(4_000)
    expect(toastCauses(w)).toEqual([]) // 3.24 T17t: after 60 s of the developer's quiet
    await clock.advance(60_000)
    expect(toastCauses(w)).toEqual(['review'])
    expect(entries(w, 'mod-prompt').some(p => (w.files.get(p) ?? '').includes('T1 stopped for your review'))).toBe(true)
  })

  // 3.24 T8: a missing Review: block is asked of the expert by message first; its next contract reruns the review.
  test('a task_log review refusal asks the expert for the block; the rerun succeeds: held(review)', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { USERPROFILE: 'H' })
    let refusals = 1
    w.answer = argv => argv.slice(1).join(' ').startsWith('tools/task_log.py') && refusals-- > 0
      ? { exitCode: 1, stdout: 'refused: no Review: block in phase-ends/current/tasks/T1.md\n', stderr: '' }
      : defaultAnswer(argv)
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    expect(w.sends.some(t => t.startsWith('NOTE: T1 returned STATUS review') && t.includes('Review:'))).toBe(true)
    expect(loopOf(w).holds).toEqual([])
    expect(loopOf(w).state).toBe('running')
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/task_log.py review').length).toBe(2)
    expect(loopOf(w).holds).toEqual(['review'])
    expect(ran(w, 'tools/status.py wait REVIEW.md').length).toBe(1)
  })

  test('a task_log review refusal after the ask is a review hold presented from the summary', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { USERPROFILE: 'H' })
    w.answer = argv => argv.slice(1).join(' ').startsWith('tools/task_log.py')
      ? { exitCode: 1, stdout: 'refused: no Review: block in phase-ends/current/tasks/T1.md\n', stderr: '' }
      : defaultAnswer(argv)
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(w.sends.filter(t => t.startsWith('NOTE: T1 returned STATUS review')).length).toBe(1)
    await $.turn.complete(complete('E1', 'STATUS: review'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(w.sends.filter(t => t.startsWith('NOTE: T1 returned STATUS review')).length).toBe(1)
    expect(loopOf(w).holds).toEqual(['review'])
    const text = notices(w)[0]?.input.text ?? ''
    expect(text).toContain('refused: no Review: block')
    expect(text).toContain('from the summary phase-ends/current/tasks/T1.md')
    expect(notices(w)[0]?.input.toast).toBe('review')
  })

  test('3.25 T3: a plain question\'s notice carries its text as it is', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { USERPROFILE: 'H' })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'Question: floats or ints? I would use ints.'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    const n = notices(w)[0]
    expect(n?.input.toast).toBe('question')
    expect(n?.input.text).toContain('‹pa› T1 stopped: T1 asks a question:\nQuestion: floats or ints? I would use ints.')
    expect(n?.input.text).not.toContain('RECOMMENDED:')
  })

  test('question stop: QUESTION, RECOMMENDED, the milestone pointer and who decides; cause question, no toast yet', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { USERPROFILE: 'H' })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: question\nQUESTION: floats or ints?\nRECOMMENDED: ints'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(4_000)
    const n = notices(w)[0]
    expect(n?.input.toast).toBe('question')
    expect(n?.input.text).toContain('‹pa› T1 stopped: T1 asks a question.\nQUESTION: floats or ints?\nRECOMMENDED: ints')
    expect(n?.input.text).toContain('milestone: `PY tools/plan_edit.py show --section Milestone`')
    expect(n?.input.text).toContain('Decide small changes inside the milestone yourself (plan_edit, then release({hold: "question", note}))')
    await clock.advance(4_000)
    expect(toastCauses(w)).toEqual([])
  })

  test('a mod-level block (nothing runnable) is a question stop; its toast waits for Claude\'s reply', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR }, { USERPROFILE: 'H' })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    w.answer = argv => argv.slice(1).join(' ').startsWith('tools/plan_edit.py next')
      ? ok(`${JSON.stringify({ kind: 'wait', reason: 'deps', waiting_on: ['T0'] })}\n`)
      : defaultAnswer(argv)
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect(notices(w)[0]?.input.toast).toBe('question')
    expect(notices(w)[0]?.input.text).toContain('the loop stopped: nothing runnable; waiting on: T0')
    await clock.advance(4_000)
    expect(toastCauses(w)).toEqual([])
    await $.turn.complete(complete('P1', 'Nothing can run: T0 is not done.'))
    await clock.advance(4_000)
    expect(toastCauses(w)).toEqual([]) // 3.24 T17t: after 60 s of the developer's quiet
    await clock.advance(60_000)
    expect(toastCauses(w)).toEqual(['question'])
  })

  test('the router leaves release requests to the loop', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 60_000) })
    const id = releaseReq(w, NOW, { hold: 'talk' })
    await $.session.start(START)
    await clock.advance(2_000)
    const st = JSON.parse(w.files.get(`${BASE}/S1/state.json`) ?? '{}') as { inboxDone?: Record<string, number> }
    expect(Object.keys(st.inboxDone ?? {})).not.toContain(id)
    const lp = JSON.parse(w.files.get(LOOP) ?? '{}') as { inboxDone?: Record<string, number> }
    expect(Object.keys(lp.inboxDone ?? {})).toContain(id)
    expect(notices(w)).toEqual([]) // a `talk` release with no `talk` hold is a silent no-op (T5b.c2)
    expect(loopOf(w).holds).toEqual(['question'])
    releaseReq(w, NOW + 3_000, { hold: 'close' })
    await clock.advance(2_000)
    expect(notices(w)[0]?.input).toEqual({ text: 'refused: not held: close', to: 'P1' })
  })

  test('release review: refused while the task is open; then review-done, the commit, the boundary', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const lf = JSON.parse(held('review', NOW - 60_000)) as { task: { contract: string } }
    lf.task.contract = 'STATUS: review\nSUMMARY: The transcripts are in.' // 3.24 T17g: the accepted card carries it
    const w = world(on, { [LOOP]: JSON.stringify(lf) })
    w.answer = showStatus('in-progress')
    releaseReq(w, NOW, { hold: 'review' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(notices(w)[0]?.input.text).toBe('refused: mark T1 done or reopen it first')
    expect(loopOf(w).holds).toEqual(['review'])
    expect(ran(w, 'tools/status.py review-done').length).toBe(0)
    w.answer = showStatus('done')
    releaseReq(w, NOW + 2_000, { hold: 'review' })
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py review-done').length).toBe(1)
    expect(w.runs.find(r => r[1] === 'tools/commit_task.sh')).toEqual([BASH, 'tools/commit_task.sh', 'review', 'T1 review done', 'phase-ends/current'])
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w).map(n => n.input.text)).not.toContain('released: review')
    expect(loopOf(w).holds).toEqual([])
    expect(experts(w).length).toBe(1)
    // 3.24 T13A (decision 4): the accepted card, a `card` request for the router (zero tokens)
    const cards = [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && p.endsWith('-card.json'))
      .map(p => JSON.parse(w.files.get(p) ?? '{}') as { caller: string; input: { text: string } })
    expect(cards.map(c => [c.caller, c.input.text])).toEqual([['loop', '**🟦 ✓ Expert · T1 · accepted**\n\nThe transcripts are in.']])
  })

  test('release question inside the TTL: status relaunch, the same expert resumed with the NOTE', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 10 * 60_000) })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }] // the expert, resumable (T11-fix2)
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py set')[0]).toEqual(['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--kind', 'relaunch', '--attempt', '2', '--session', 'S1'])
    expect(w.sends).toContain('NOTE: use ints')
    expect(experts(w).length).toBe(0)
    const lp = loopOf(w)
    expect(lp.holds).toEqual([])
    expect(lp.state).toBe('running')
    expect(lp.task?.attempt).toBe(2)
    expect(lp.task?.kind).toBe('relaunch')
    expect(lp.task?.contract).toBe(null)
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('E1 resumed with the note')
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w).map(n => n.input.text)).not.toContain('released: question')
  })

  test('release question of a task the plan has since superseded: not resumed; the loop goes on from the plan (3.30)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const lf = JSON.parse(held('question', NOW - 10 * 60_000)) as { task: { agentId: string } }
    lf.task.agentId = 'E9' // the old expert, resumable: the 3.3 trial resumed it attempt after attempt
    const w = world(on, { [LOOP]: JSON.stringify(lf) })
    w.list = [{ id: 'E9', status: 'idle', type: 'pa-expert' }]
    w.answer = showStatus('superseded')
    releaseReq(w, NOW, { hold: 'question', note: 'T1 is superseded' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.sends).not.toContain('NOTE: T1 is superseded')
    expect(ran(w, 'tools/status.py set').filter(r => r.includes('relaunch'))).toEqual([])
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes(
      'T1 question released: the plan has it superseded, so it is not resumed; the loop goes on from the plan.'))).toBe(true)
    expect(notices(w).map(n => n.input.text)).toContain('released: question (T1 is superseded in the plan; the loop goes on from the plan)')
    // the boundary picks from the plan (the fake picks T1 again): a fresh task, not attempt 2 of the old one
    expect(ran(w, 'tools/plan_edit.py next').length > 0).toBe(true)
    expect([loopOf(w).task?.agentId, loopOf(w).task?.kind, loopOf(w).task?.attempt]).toEqual(['E1', 'task', 1])
  })

  test('release stopped of a task the plan has set back to queued: dropped too (3.30)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped'] }) })
    setStatus(w, 'E1', 'killed')
    w.answer = pickT2(showStatus('queued'))
    stoppedReq(w, NOW, { hold: 'stopped', note: 'Go on.' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect([w.sends, ran(w, 'tools/status.py set').filter(r => r.includes('relaunch'))]).toEqual([[], []])
    expect(loopOf(w).holds).not.toContain('stopped')
    expect(notices(w).map(n => n.input.text)).toContain('released: stopped (T1 is queued in the plan; the loop goes on from the plan)')
    expect(asideOf(w)).toEqual({ T1: { attempt: 1 } }) // 3.30.1: kept for its next start
  })

  test('3.30.1: during a stop a plan edit applies at once, after what was queued; the release sets the task aside (bfm T11)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const queued = [{ command: 'add-task --title "Bank" --after T1', why: 'the picker', at: NOW - 60_000, caller: 'P1' }]
    const w = world(on, { [LOOP]: running({ holds: ['stopped'], queuedEdits: queued }) })
    setStatus(w, 'E1', 'killed')
    const plan = reorderPlan(w)
    const id = `${String(NOW)}-1-plan_edit`
    w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'plan_edit', caller: 'P1',
      input: { command: 'set-status T1 queued', why: 'run the bank first' }, at: NOW }))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.runs.filter(r => r[1] === 'tools/plan_edit.py' && r[2] !== 'show').map(r => r[2])).toEqual(['add-task', 'set-status'])
    expect([loop2(w).queuedEdits, plan.status]).toEqual([[], 'queued'])
    expect(toCaller(w).some(t => t.startsWith('queued:'))).toBe(false)
    stoppedReq(w, NOW + 2_000, { hold: 'stopped' })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.sends).toEqual([])
    expect(ran(w, 'tools/task_log.py aside T1').length).toBe(1)
    expect(commits(w).some(c => c.join(' ').includes('T1 set aside: its progress kept as logs/T1.aside.md'))).toBe(true)
    expect(asideOf(w)).toEqual({ T1: { attempt: 1 } })
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes('T1 stopped released: the plan has it queued, so it is ' +
      'not resumed; the loop goes on from the plan (its progress is kept as logs/T1.aside.md for its next start).'))).toBe(true)
    expect(w.spawns.at(-1)?.description.startsWith('T2')).toBe(true)
  })

  test('3.30.1: release question of a task the plan has blocked: set aside too (TerrainDiffusion T9)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 10 * 60_000) })
    w.answer = pickT2(showStatus('blocked'))
    releaseReq(w, NOW, { hold: 'question', note: 'T9 waits for the walk' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.sends).not.toContain('NOTE: T9 waits for the walk')
    expect(notices(w).map(n => n.input.text)).toContain('released: question (T1 is blocked in the plan; the loop goes on from the plan)')
    expect(asideOf(w)).toEqual({ T1: { attempt: 1 } })
    expect(w.spawns.at(-1)?.description.startsWith('T2')).toBe(true)
  })

  test('3.30.1: a set-aside task comes back from the plan as a relaunch with its parked progress, its attempts counted on', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ setAside: { T1: { attempt: 6 } } }) })
    w.answer = argv => (argv.slice(1).join(' ').startsWith('tools/task_log.py aside T1 --restore')
      ? ok('restored: phase-ends/current/TASK_PROGRESS.md\n') : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py set').at(-1)).toEqual(['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--coder', 'opus55', '--kind', 'relaunch', '--attempt', '7', '--title', 'add function', '--session', 'S1'])
    expect(experts(w)[0]?.prompt).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md')
    expect([loopOf(w).task?.kind, loopOf(w).task?.attempt, asideOf(w)]).toEqual(['relaunch', 7, {}])
  })

  test('release question outside the TTL: respawned with the brief plus NOTE', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 61 * 60_000) })
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).not.toContain('NOTE: use ints')
    const sp = experts(w)
    expect(sp.length).toBe(1)
    expect(sp[0]?.prompt).toContain('NOTE: use ints')
    expect(loopOf(w).task?.kind).toBe('relaunch')
    expect(loopOf(w).task?.attempt).toBe(2)
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('and the note')
  })

  test('release question held past the TTL, last ping inside it: resumed, not respawned (3.26 T2)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', warmAgents: { E1: { role: 'pa-expert', lastPingAt: NOW - 20 * 60_000 } } })
    const w = world(on, { [LOOP]: held('question', NOW - 120 * 60_000), [`${BASE}/S1/state.json`]: prior })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toContain('NOTE: use ints')
    expect(experts(w).length).toBe(0)
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('E1 resumed with the note')
  })

  test('release question held past the TTL, last ping outside it: still respawned (3.26 T2)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', warmAgents: { E1: { role: 'pa-expert', lastPingAt: NOW - 60 * 60_000 } } })
    const w = world(on, { [LOOP]: held('question', NOW - 120 * 60_000), [`${BASE}/S1/state.json`]: prior })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).not.toContain('NOTE: use ints')
    expect(experts(w).length).toBe(1)
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('and the note')
  })

  test('release close sets closeRequested; its done-only reply is journaled, not sent (3.24 T13A)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const prior = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
    const w = world(on, { [LOOP]: held('close', NOW - 60_000), [`${BASE}/S1/state.json`]: prior })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'X9', status: 'idle', type: 'discuss' }]
    releaseReq(w, NOW, { hold: 'close' }, 'X9')
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await clock.advance(2_000)
    const lp = JSON.parse(w.files.get(LOOP) ?? '{}') as { closeRequested?: boolean; holds: string[] }
    expect(lp.closeRequested).toBe(true)
    expect(lp.holds).toEqual([])
    expect(notices(w)).toEqual([])
    expect(w.sends).not.toContain('A loop prompt arrived: released: close')
  })
})

// T5b2: queued plan changes land at the boundary; a developer prompt pushes `talk`; silence pops it.
const PRIOR = JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' } })
const loopAt = (o: Record<string, unknown>): string =>
  JSON.stringify({ v: 1, ownerSid: 'S1', heartbeatAt: NOW, state: 'boundary', holds: [], task: null, ...o })
const running = (o: Record<string, unknown> = {}): string => loopAt({
  state: 'running', nextId: 'T2',
  task: { id: 'T1', title: 'add function', agent: 'pa-expert', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task',
    agentId: 'E1', startSha: 'abc', spawnedAt: NOW - 60_000, lastCompleteAt: 0, contract: null, plan: T1 },
  ...o,
})
type Loop2 = Loop & { queuedEdits: unknown[]; lastDevAt: number; lastAnswerAt: number; lastAnswerAsked: boolean }
const loop2 = (w: W): Loop2 => JSON.parse(w.files.get(LOOP) ?? '{}') as Loop2
const commits = (w: W): string[][] => w.runs.filter(r => r[1] === 'tools/commit_task.sh')
const TALK = '‹pa› the queue is held while you talk (T1 running; next: T2). ' +
  'Call release({hold: "talk"}) when the conversation is done or does not touch the plan.'

describe('queued edits and the talk hold (T5b2)', () => {
  test('queued edits land at the boundary before the pick, in order, each committed and answered', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ queuedEdits: [
      { command: 'add-task --title "sub" --after T3', why: 'add sub after T3', at: 1, caller: 'P1' },
      { command: 'python tools/rules_add.py promote r1', why: '', at: 2, caller: 'P1' },
      { command: 'PY tools/status.py set', why: '', at: 3, caller: 'P1' },
    ] }) })
    w.answer = argv => (argv.slice(1).join(' ').startsWith('tools/plan_edit.py add-task') ? ok('added T4 after T3\nmore\n') : defaultAnswer(argv))
    await $.session.start(START)
    await clock.advance(2_000)
    const order = w.runs.map(r => r.slice(1, 3).join(' '))
    expect(order.indexOf('tools/plan_edit.py add-task')).toBeLessThan(order.indexOf('tools/plan_edit.py next'))
    expect(ran(w, 'tools/plan_edit.py add-task')[0]).toEqual(['py', 'tools/plan_edit.py', 'add-task', '--title', 'sub', '--after', 'T3'])
    expect(ran(w, 'tools/rules_add.py')[0]).toEqual(['py', 'tools/rules_add.py', 'promote', 'r1'])
    expect(ran(w, 'tools/status.py set').length).toBe(1) // the pick's own, never the refused edit
    expect(commits(w).slice(0, 2)).toEqual([
      [BASH, 'tools/commit_task.sh', 'router', 'add sub after T3', PLAN],
      [BASH, 'tools/commit_task.sh', 'router', 'python tools/rules_add.py promote r1', PLAN, 'rules'],
    ])
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w).map(n => n.input)).toEqual([
      { text: 'refused: not an allowed script: tools/status.py', to: 'P1' },
    ])
    expect(loop2(w).queuedEdits).toEqual([])
    expect(w.spawns.length).toBe(1)
  })

  test('a running task keeps the queue; a plan_edit outside a stop is queued with its caller', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    const id = `${String(NOW)}-1-plan_edit`
    w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'plan_edit', caller: 'P1',
      input: { command: 'tools/plan_edit.py append-change "x"', why: 'w' }, at: NOW }))
    await $.session.start(START)
    await clock.advance(4_000)
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w)).toEqual([])
    expect(loop2(w).queuedEdits).toEqual([{ command: 'tools/plan_edit.py append-change "x"', why: 'w', at: NOW + 2_000, caller: 'P1' }])
    expect(ran(w, 'tools/plan_edit.py append-change').length).toBe(0)
  })

  test('a developer prompt while the loop runs pushes talk; the first reaches Claude after the talk line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running(), [`${BASE}/S1/state.json`]: PRIOR })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'how is it going?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['talk'])
    expect(loop2(w).lastDevAt).toBeGreaterThan(0)
    expect(delivered(w).some(t => t.includes(`${TALK}\n\n‹pa› running now: T1 add function\n- expert E1\n\nhow is it going?`))).toBe(true)
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes('talk: the queue is held'))).toBe(true)
    await $.prompt.submit({ text: 'and the tests?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(delivered(w).filter(t => t.includes('the queue is held while you talk')).length).toBe(1)
    expect(delivered(w).some(t => t.includes('and the tests?'))).toBe(true)
  })

  test('the loop off: a developer prompt gets no talk line and pushes nothing', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ state: 'off' }), [`${BASE}/S1/state.json`]: PRIOR })
    w.answer = argv => (argv.slice(1).join(' ').startsWith('tools/launch.py') ? ok('{"mode": "planner"}\n') : defaultAnswer(argv))
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual([])
    expect(delivered(w).some(t => t.includes('hello'))).toBe(true)
    expect(delivered(w).some(t => t.includes('the queue is held'))).toBe(false)
  })

  test('while talk holds no task starts; the running expert is routed; release talk starts the next', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['talk'], lastDevAt: NOW }) })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: done\nLOG: l\nCOMMIT: def5678'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(2_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
    expect([loop2(w).state, loop2(w).task, loop2(w).holds]).toEqual(['boundary', null, ['talk']])
    expect(ran(w, 'tools/plan_edit.py next').length).toBe(0)
    expect(w.spawns.length).toBe(0)
    releaseReq(w, NOW + 9_000, { hold: 'talk' })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)).not.toContain('released: talk') // 3.24 T13A: Claude's own release, quiet
    expect(w.spawns.length).toBe(1)
  })

  test('a stop raised under talk stacks beneath it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['talk'], lastDevAt: NOW }) })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: question\nQUESTION: q?\nRECOMMENDED: r'))
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS, T5d1: 15 s)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['question', 'talk'])
  })

  test('silence pops talk after 15 min and the loop goes on', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ holds: ['talk'], lastDevAt: NOW - 15 * 60_000 }) })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes('talk released: 15 min'))).toBe(true)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual([])
    expect(w.spawns.length).toBe(1)
  })

  test('3.23 T13d.c3: a spawned expert gets one start record in the feed, labelled with its id and type', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ holds: ['talk'], lastDevAt: NOW - 15 * 60_000 }) })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.spawns.length).toBe(1)
    const recs = [...w.files.keys()].filter(p => p.startsWith(`${BASE}/S1/feed/in/`) && /-8\d{5}\.json$/.test(p))
    expect(recs.length).toBe(1)
    const r = JSON.parse(w.files.get(recs[0] ?? '') ?? '{}') as Record<string, unknown>
    expect([r['agent'], r['type'], r['text']]).toEqual(['E1', 'pa-expert', 'Starting T1: add function'])
  })

  test('lastAnswerAsked blocks the silence release', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ holds: ['talk'], lastDevAt: NOW - 20 * 60_000, lastAnswerAsked: true }) })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['talk'])
    expect(w.spawns.length).toBe(0)
  })

  test('the partner\'s answers set lastAnswerAsked (asksQuestion) and lastAnswerAt; another agent\'s do not', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['talk'], lastDevAt: NOW }), [`${BASE}/S1/state.json`]: PRIOR })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.turn.complete(complete('X7', 'Shall I?'))
    await clock.advance(2_000)
    expect(loop2(w).lastAnswerAsked ?? false).toBe(false)
    await $.turn.complete(complete('P1', 'It is going well. Shall I add the task now?'))
    await clock.advance(2_000)
    expect(loop2(w).lastAnswerAsked).toBe(true)
    expect(loop2(w).lastAnswerAt).toBeGreaterThan(NOW)
    await $.turn.complete(complete('P1', 'Done: the change is queued.'))
    await clock.advance(2_000)
    expect(loop2(w).lastAnswerAsked).toBe(false)
  })

  test('3.28.1 T9b: an answer to a mod notice (sentKind other) neither keeps talk nor records a question', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ holds: ['talk'], lastDevAt: NOW - 15 * 60_000 + 10_000 }),
      [`${BASE}/S1/state.json`]: JSON.stringify({ v: 1, gen: 1, sid: 'S1', partner: { agentId: 'P1' }, sentKind: 'other' }) })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.turn.complete(complete('P1', 'PA3 updated. Shall I tell you what changed?'))
    await clock.advance(2_000)
    expect(loop2(w).lastAnswerAsked ?? false).toBe(false)
    expect(loop2(w).lastAnswerAt).toBeGreaterThan(NOW)          // the relay and the feed still see the answer
    expect(loop2(w).talkAnswerAt ?? 0).toBe(0)
    expect(loop2(w).holds).toEqual(['talk'])
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(loop2(w).holds).toEqual([])                          // 15 min after the developer's message, talk pops
  })
})

// T5c: the running-now note on developer messages, `steer` to the running expert, the queued count, the trial coder.
const steerReq = (w: W, ms: number, input: Record<string, string | boolean>): string => {
  const id = `${String(ms)}-1-steer`
  w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'steer', caller: 'P1', input, at: ms }))
  return id
}
const facts = (o: Record<string, unknown>): string => JSON.stringify({ model: 'claude-opus-5-5', effort: 'medium', ctx: 1, at: 1, ...o })

describe('visible and steerable (T5c)', () => {
  test('a developer message while T1 runs carries the running-now note: the expert and its descendants only', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [LOOP]: running(), [`${BASE}/S1/state.json`]: PRIOR,
      [`${BASE}/S1/agents/E1.json`]: facts({ type: 'pa-expert', description: 'T1 add function', note: 'Brief a coder for plus.\nThen verify.', steps: ['Read plan', 'Agent coder'] }),
      [`${BASE}/S1/agents/C1.json`]: facts({ type: 'pa-coder', parentId: 'E1', steps: ['Edit calc.py'] }),
      [`${BASE}/S1/agents/X9.json`]: facts({ type: 'pa-coder', parentId: 'P1', steps: ['Edit other.py'] }),
      [`${BASE}/S1/agents/P1.json`]: facts({ type: 'pa-claude' }),
    })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what is it doing?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    const note = ['‹pa› running now: T1 add function', '- expert E1 (claude-opus-5-5)', '  note: Brief a coder for plus.', '  note: Then verify.',
      '- coder C1 (claude-opus-5-5)'].join('\n') // 3.23 T13d.c2: no tool steps
    expect(delivered(w).some(t => t.includes(`${TALK}\n\n${note}\n\nwhat is it doing?`))).toBe(true)
    expect(delivered(w).some(t => t.includes('other.py'))).toBe(false)
    expect(w.files.get(entries(w, 'dev-msg')[0] ?? '')).not.toContain('running now') // the journal keeps the original
  })

  test('3.32.1 T5: the running-now note places a coder known only from parents.json under its expert', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [LOOP]: running(), [`${BASE}/S1/state.json`]: PRIOR, [`${BASE}/S1/parents.json`]: '{"C1":"E1"}',
      [`${BASE}/S1/agents/E1.json`]: facts({ type: 'pa-expert', description: 'T1 add function' }),
      [`${BASE}/S1/agents/C1.json`]: facts({ type: 'pa-coder', steps: ['Edit calc.py'] }),
    })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'running', type: 'pa-expert' },
      { id: 'C1', status: 'running', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what is it doing?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('- expert E1 (claude-opus-5-5)\n- coder C1 (claude-opus-5-5)'))).toBe(true)
  })

  test('3.26 T2: while the task is routed the note still names it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ state: 'routing', holds: ['question'] }), [`${BASE}/S1/state.json`]: PRIOR })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what is it doing?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('‹pa› running now: T1 add function'))).toBe(true)
  })

  test('3.26 T2: after a review release the idle note names the task as the last done', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const lf = JSON.parse(held('review', NOW - 60_000)) as { holds: string[]; task: { contract: string } }
    lf.holds = ['talk', 'review'] // talk stays: the boundary starts nothing
    lf.task.contract = 'STATUS: review\nSUMMARY: The transcripts are in.'
    const w = world(on, { [LOOP]: JSON.stringify(lf), [`${BASE}/S1/state.json`]: PRIOR })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    w.answer = showStatus('done')
    releaseReq(w, NOW, { hold: 'review' })
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect((loop2(w) as unknown as { lastDone?: { id: string } }).lastDone?.id).toBe('T1')
    await $.prompt.submit({ text: 'what is it doing?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('‹pa› running now: no task · T1 done at'))).toBe(true)
  })

  test('steer delivered: the expert gets the correction, the caller steered: T1, mod/steer, steerAt; nothing stopped', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    steerReq(w, NOW, { task: 'T1', text: 'Name it plus.' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual(['‹pa› A correction from the developer, relayed by Claude: Name it plus.\n' +
      'Work out what it means for your task, tell your running coder with SendMessage what to stop, keep or undo (never stop it), and log the correction under Deviations.'])
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w)).toEqual([])
    expect(w.files.get(entries(w, 'mod-steer')[0] ?? '')).toContain('T1 steered: Name it plus.')
    expect(w.files.get(entries(w, 'mod-prompt')[0] ?? '')).toContain('steered: T1') // the silent confirmation is journaled
    expect((loopOf(w).task as unknown as { steerAt: number }).steerAt).toBe(NOW + 2_000)
    expect(loopOf(w).state).toBe('running')
  })

  test('3.29.1 T9: a steer with pause: true records the pause; pause: false clears it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    const paused = (): unknown => (loopOf(w).task as unknown as { paused?: unknown }).paused ?? null
    steerReq(w, NOW, { task: 'T1', text: 'Pause at a clean point.', pause: true })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(paused()).toEqual({ at: NOW + 2_000 })
    steerReq(w, NOW + 3_000, { task: 'T1', text: 'Go on.', pause: false })
    await clock.advance(2_000)
    expect(paused()).toBe(null)
  })

  test('3.29.8: cleanup: a checked list gets the mod\'s picker; its yes runs the deletion and tells Claude', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    const asked = askWith(on, w, ['Delete them'])
    w.answer = argv => (argv[1] === 'tools/run_cleanup.py'
      ? ok(argv[2] === '--check' ? 'checked 3 items, all inside .run/, 4.20 GB\n' : 'deleted 3 of 3 items, 4.20 GB, all inside .run/\n')
      : defaultAnswer(argv))
    w.files.set(`${INBOX}/${String(NOW)}-1-cleanup.json`, JSON.stringify({ id: `${String(NOW)}-1-cleanup`, kind: 'cleanup', caller: 'P1', input: { summary: '3 items, 4.2 GB' }, at: NOW }))
    await $.session.start(START)
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked.map(a => a.q).filter(q => q.startsWith('Delete the items Claude listed above (3 items, 4.2 GB)? checked 3 items'))).toHaveLength(1)
    expect(w.runs.some(r => r[1] === 'tools/run_cleanup.py' && r[2] === '.run/mod/cleanup.json')).toBe(true)
    expect(notices(w).some(n => n.input.text.includes('the mod ran the deletion') && n.input.text.includes('deleted 3 of 3 items'))).toBe(true)
  })

  test('3.29.8: cleanup: a list that fails its check is refused; nothing is asked or deleted', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    const asked = askWith(on, w, [])
    w.answer = argv => (argv[1] === 'tools/run_cleanup.py' ? { exitCode: 1, stdout: 'refused: .run/mod: PA3\'s own entry\n', stderr: '' } : defaultAnswer(argv))
    w.files.set(`${INBOX}/${String(NOW)}-1-cleanup.json`, JSON.stringify({ id: `${String(NOW)}-1-cleanup`, kind: 'cleanup', caller: 'P1', input: {}, at: NOW }))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(asked).toEqual([])
    expect(w.runs.filter(r => r[1] === 'tools/run_cleanup.py').map(r => r[2])).toEqual(['--check'])
    expect(notices(w).some(n => n.input.text.includes('did not pass its check'))).toBe(true)
  })

  test('steer refused when the task is not the running one', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    steerReq(w, NOW, { task: 'T2', text: 'x' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)).toEqual(['refused: T2 is not running'])
    expect(w.sends).toEqual([])
  })

  test('a steer keeps the run from ending until the expert\'s next turn.complete', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const task = (JSON.parse(running()) as { task: Record<string, unknown> }).task
    const w = world(on, { [LOOP]: running({ task: { ...task, lastCompleteAt: NOW - 200_000, steerAt: NOW - 100_000 } }) })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loopOf(w).state).toBe('running') // without the steer: 200 s quiet, ended, "no contract"
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc1234')) // T11-fix: a STATUS, so 15 s quiet ends it
    await clock.advance(62_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
  })

  test('PA_MOD_TRIAL_CODER=T1 adds the trial-coder NOTE to T1\'s brief', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { PA_MOD_TRIAL_CODER: 'T1' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.spawns[0]?.prompt).toContain('NOTE: trial: delegate the change to pa-coder even though it is small')
  })
})

// T5d1: the heartbeat, a takeover's recovery (continue:, then a respawn with PROGRESS), the mod's digest.
const PROGRESS_FILE = `${ROOT}/phase-ends/current/TASK_PROGRESS.md`
/** A task T1 running under S0 (expert E0, dead with its session), heartbeat `beat`. */
const orphan = (beat: number, spawnedAt = NOW - 120_000): string => JSON.stringify({
  v: 1, ownerSid: 'S0', heartbeatAt: beat, state: 'running', holds: [], nextId: 'T2',
  task: { id: 'T1', title: 'add function', agent: 'pa-expert', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task',
    agentId: 'E0', startSha: 'abc', spawnedAt, lastCompleteAt: spawnedAt + 30_000, contract: null, plan: T1 },
})
type Loop3 = Loop & { ownerSid: string; heartbeatAt: number; task: (Loop['task'] & { recovery?: { phase: string } | null }) | null }
const loop3 = (w: W): Loop3 => JSON.parse(w.files.get(LOOP) ?? '{}') as Loop3
const recovers = (w: W): string[] => entries(w, 'mod-recover').map(p => w.files.get(p) ?? '')

describe('recovery and loop ownership (T5d1)', () => {
  test('a live owner keeps the loop; its heartbeat stale (45 s), this session takes over and writes it every 15 s', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 30_000) })
    w.list = [{ id: 'E0', status: 'running', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(14_000)
    expect([loop3(w).ownerSid, w.runs.length, w.sends.length]).toEqual(['S0', 0, 0])
    await clock.advance(4_000)
    expect(loop3(w).ownerSid).toBe('S1')
    const beat = loop3(w).heartbeatAt
    await clock.advance(16_000)
    expect(loop3(w).heartbeatAt).toBeGreaterThanOrEqual(beat + 15_000)
    expect(recovers(w)[0]).toContain('recovery: T1 in flight (E0, attempt 1)')
  })

  test('continue: sent, not seen running within 30 s: status relaunch, respawn with PROGRESS, the digest written', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const [day, time] = stamp(NOW - 30_000)
    const note = `${JDIR}/${day}/${time}-001w-T1-note.md`
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [note]: `---\nts: ${new Date(NOW - 30_000).toISOString()}\n---\nBriefing the coder for add.py.\n` })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/launch.py --state-json').length).toBe(1)
    expect(w.sends).toEqual([CONTINUE_TEXT])
    expect(loop3(w).task?.recovery?.phase).toBe('sent')
    await clock.advance(26_000)
    expect(experts(w).length).toBe(0)
    await clock.advance(6_000)
    expect(ran(w, 'tools/status.py set')).toEqual([['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--kind', 'relaunch', '--attempt', '2', '--session', 'S1']])
    expect(experts(w).length).toBe(1)
    expect(experts(w)[0]?.prompt).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md')
    const l = loop3(w)
    expect([l.state, l.task?.agentId, l.task?.attempt, l.task?.kind, l.task?.recovery ?? null]).toEqual(['running', 'E1', 2, 'relaunch', null])
    const digest = w.files.get(PROGRESS_FILE) ?? ''
    expect(digest).toContain('# TASK_PROGRESS: recovery digest for T1 (attempt 1)')
    expect(digest).toContain('## git log --oneline abc..HEAD')
    expect(digest).toContain('Briefing the coder for add.py.')
    expect(ran(w, 'status --short').length + ran(w, 'log --oneline abc..HEAD').length).toBe(2)
    const r = recovers(w)
    expect(r.length).toBe(3)
    expect(r[1]).toContain('recovery: sent E0 continue: (delivered)')
    expect(r[2]).toContain('respawned with PROGRESS')
    expect(r[2]).toContain('written by the mod (1 journal entries)')
  })

  test('3.32.12: an expert that started on an earlier day: its notes come from the packed day file', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const since = NOW - 30 * 3_600_000 // the day before: journal_rollup.py packed its folder into `<date>.md`
    const [day, t1] = stamp(since + 60_000)
    const [, t2] = stamp(since + 120_000)
    const entry = (at: number, body: string): string => `---\nts: ${new Date(at).toISOString()}\n---\n${body}\n`
    const packed = `# Journal ${day} (2 entries, rolled by tools/journal_rollup.py)\n` +
      `## ${t1}-001w-T1-note\n${entry(since + 60_000, 'Briefing the coder for add.py, from the packed day.')}` +
      `## ${t2}-002w-coder-thinking\n${entry(since + 120_000, 'The import path is wrong; fixing it.')}`
    const w = world(on, { [LOOP]: orphan(NOW - 50_000, since), [`${JDIR}/${day}.md`]: packed })
    await $.session.start(START)
    await clock.advance(40_000)
    expect(experts(w).length).toBe(1)
    const digest = w.files.get(PROGRESS_FILE) ?? ''
    expect(digest).toContain('Briefing the coder for add.py, from the packed day.')
    expect(digest).toContain('The import path is wrong; fixing it.')
    expect(recovers(w).some(t => t.includes('written by the mod (2 journal entries)'))).toBe(true)
    // 3.32.15: through journal_rollup.py entries, the newest notes and reasoning only; the day file never read here (a
    // busy day's passes $.fs's 4 MiB: GitHub #15)
    const [d0, t0] = stamp(since)
    expect(w.entryReads.map(r => r.slice(2).join(' '))).toEqual([
      `entries --since ${d0} --after ${d0}/${t0} --match=^(?!.*-(?:claude|thinker)-note$).*-(?:note|interim)$ --last 30 --max-chars 8000`,
      `entries --since ${d0} --after ${d0}/${t0} --match=^(?!.*-(?:claude|thinker)-thinking$).*-thinking$ --last 10 --max-chars 8000`,
    ])
    expect(w.reads.filter(p => /\/journal\/\d{4}-\d{2}-\d{2}\.md$/.test(p))).toEqual([])
  })

  test('continue: delivered and the expert seen running: no respawn; the run ends only after its next turn', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000) })
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = [{ id: 'E0', status: 'running', type: 'pa-expert' }]
    await clock.advance(40_000)
    expect(experts(w).length).toBe(0)
    const l = loop3(w)
    expect([l.state, l.task?.recovery ?? null, l.task?.agentId]).toEqual(['running', null, 'E0'])
    expect(recovers(w).pop()).toContain('E0 is running again')
    expect(w.files.has(PROGRESS_FILE)).toBe(false)
  })

  test('continue: not delivered: respawned at once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000) })
    w.deliver = false
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual([CONTINUE_TEXT])
    expect(experts(w).length).toBe(1)
    expect(recovers(w).some(t => t.includes('continue: to E0 not delivered (no such agent)'))).toBe(true)
  })

  test('a stale run_in_flight respawns without a send; a TASK_PROGRESS.md present (the hook\'s digest) is kept', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [PROGRESS_FILE]: '# kill digest\n' })
    w.answer = argv => argv[1] === 'tools/launch.py'
      ? ok(`${JSON.stringify({ mode: 'router', run_in_flight: { task: 'T1', run_id: 'r1', killed_at: 'x', age_note: 'stale', stale: true }, running: null })}\n`)
      : defaultAnswer(argv)
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual([])
    expect(experts(w).length).toBe(1)
    expect(w.files.get(PROGRESS_FILE)).toBe('# kill digest\n')
    expect(recovers(w).pop()).toContain('its run_in_flight is stale')
  })

  test('a takeover under a stop runs no recovery', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: JSON.stringify({ ...(JSON.parse(orphan(NOW - 50_000)) as object), holds: ['question'] }) })
    await $.session.start(START)
    await clock.advance(4_000)
    expect([w.sends.length, experts(w).length, recovers(w).length, loop3(w).ownerSid]).toEqual([0, 0, 0, 'S1'])
  })
})

// T5d2a: the router's forward counts as queued to the expert; WAIT-FOR; the health FIX note.
const READY = `${ROOT}/.run/ready.txt`
const T1W = { ...T1, wait_for: '.run/ready.txt contains READY' }
const waitPick = (argv: string[]): { exitCode: number; stdout: string; stderr: string } =>
  argv.slice(1).join(' ').startsWith('tools/plan_edit.py next') ? ok(`${JSON.stringify({ kind: 'task', task: T1W, promoted: null })}\n`) : defaultAnswer(argv)
type Loop4 = Loop & { waitFor: { task: string; file: string; literal: string; since: number } | null; task: (Loop['task'] & { forwardAt?: number }) | null }
const loop4 = (w: W): Loop4 => JSON.parse(w.files.get(LOOP) ?? '{}') as Loop4
const HEALTH = `${ROOT}/.run/health.json`
const flagged = JSON.stringify({ other: { keep: 1 }, savings_missing: { hook_repair: true, row: 'cache_read', session: 'S0' } }, null, 2)

describe('forwards, WAIT-FOR and the health note (T5d2a)', () => {
  test('a coder hand-back forwarded to the loop expert keeps its run from ending until the expert\'s next turn', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const task = (JSON.parse(running()) as { task: Record<string, unknown> }).task
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR, [LOOP]: running({ task: { ...task, lastCompleteAt: NOW - 200_000 } }) })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' },
      { id: 'C1', status: 'completed', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: notification('C1', 'STATUS: done\nCOMMIT: c0ffee1'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop4(w).task?.forwardAt).toBe(NOW)
    await clock.advance(30_000)
    expect(loop4(w).state).toBe('running') // without the forward: 200 s quiet, no busy child, "no contract"
    expect(entries(w, 'T1-end').length).toBe(0)
    expect(w.sends.filter(t => t.startsWith('Hand-back from your background agent C1:')).length).toBe(1)
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc1234'))
    await clock.advance(16_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
    expect(w.files.get(entries(w, 'T1-end')[0] ?? '')).toContain('COMMIT: abc1234')
  })

  test('an undelivered forward (no turn.complete after it) stops holding the run after 5 min (T5d2a.c2)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const task = (JSON.parse(running()) as { task: Record<string, unknown> }).task
    // 400 s: past NO_CONTRACT_QUIET_MS by the time the forward expires (T11-fix: the expert has no STATUS)
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR, [LOOP]: running({ task: { ...task, lastCompleteAt: NOW - 400_000 } }) })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' },
      { id: 'C1', status: 'completed', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await $.prompt.submit({ text: notification('C1', 'STATUS: done\nCOMMIT: c0ffee1'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop4(w).task?.forwardAt).toBe(NOW)
    for (let m = 0; m < 4; m += 1) {
      await clock.advance(60_000)
    }
    await clock.advance(50_000)
    expect(loop4(w).state).toBe('running') // NOW + 294 s: the forward still counts
    await clock.advance(10_000)
    expect(loop4(w).state).not.toBe('running')
  })

  test('a child just gone idle holds the run RUN_QUIET_MS for its hand-back\'s forward (T5d2a.c2 race)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const task = (JSON.parse(running()) as { task: Record<string, unknown> }).task
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR,
      [LOOP]: running({ task: { ...task, lastCompleteAt: NOW - 200_000, pendingContract: 'STATUS: done\nCOMMIT: abc1234' } }) })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' },
      { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect(loop4(w).task?.childBusy).toBe(true)
    w.list = [w.list[0]!, w.list[1]!, { id: 'C1', status: 'completed', parentId: 'E1', type: 'pa-coder' }]
    await clock.advance(2_000)
    expect(loop4(w).task?.childIdleAt).toBe(NOW + 4_000)
    await clock.advance(10_000)
    expect(loop4(w).state).toBe('running') // without the hold: ended at the idle tick, before the forward lands
    await clock.advance(6_000)
    expect(loop4(w).state).not.toBe('running')
  })

  test('WAIT-FOR: polled every 30 s, no spawn while it waits, waitFor recorded; the file ready, the task runs', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    w.answer = waitPick
    await $.session.start(START)
    await clock.advance(2_000)
    expect(loop4(w).waitFor).toEqual({ task: 'T1', file: '.run/ready.txt', literal: 'READY', since: NOW + 2_000 })
    expect(w.spawns.length).toBe(0)
    expect(w.files.get(entries(w, 'mod-hold')[0] ?? '')).toContain('WAIT-FOR: T1 waits for .run/ready.txt to contain READY')
    await clock.advance(20_000)
    expect(ran(w, 'tools/plan_edit.py next').length).toBe(1) // one poll, not one a tick
    w.files.set(READY, 'not yet\n')
    await clock.advance(12_000)
    expect([ran(w, 'tools/plan_edit.py next').length, w.spawns.length]).toEqual([2, 0])
    w.files.set(READY, 'READY\n')
    await clock.advance(30_000)
    expect(w.spawns.length).toBe(1)
    const l = loop4(w)
    expect([l.waitFor, l.state, l.task?.agentId]).toEqual([null, 'running', 'E1'])
  })

  test('WAIT-FOR not met in 50 min: task_log review, status wait REVIEW.md, held(review), the review notice', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    w.answer = waitPick
    await $.session.start(START)
    await clock.advance(2_000)
    // T5d2a.c2: 49 min of ticks ran over the kit's 5 s test timeout; the wait is back-dated 48 min instead.
    const l0 = JSON.parse(w.files.get(LOOP) ?? '{}') as { waitFor: { since: number } }
    w.files.set(LOOP, JSON.stringify({ ...l0, waitFor: { ...l0.waitFor, since: l0.waitFor.since - 48 * 60_000 } }, null, 2))
    await clock.advance(60_000)
    expect([ran(w, 'tools/task_log.py review').length, loop4(w).holds]).toEqual([0, []])
    await clock.advance(62_000)
    expect(ran(w, 'tools/task_log.py review')).toEqual([['py', 'tools/task_log.py', 'review', 'T1']])
    expect(ran(w, 'tools/status.py wait').pop()).toEqual(['py', 'tools/status.py', 'wait', 'REVIEW.md'])
    const l = loop4(w)
    expect([l.holds, l.waitFor, w.spawns.length]).toEqual([['review'], null, 0])
    const n = notices(w).pop()
    expect(n?.input.text).toContain('T1 waited 50 min for .run/ready.txt to contain READY and stopped for your review')
    expect(n?.input.toast).toBe('review')
  })

  test('health FIX: a note to Claude and only savings_missing deleted; with PA_MOD_PA3_DIR set the flag spawns nothing', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [HEALTH]: flagged }, { PA_MOD_PA3_DIR: 'Z:/snap' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(notices(w).map(x => x.input.text)).toContain(
      '‹pa› the hooks\' savings repair is flagged (cache_read); a FIX task promotes PA3\'s machine root, which waits for the release.')
    expect(JSON.parse(w.files.get(HEALTH) ?? '{}')).toEqual({ other: { keep: 1 } })
    expect(w.spawns.map(x => x.description)).toEqual(['T1 add function'])
    await clock.advance(2_000)
    expect(notices(w).filter(x => x.input.text.includes('savings repair')).length).toBe(1)
  })

  test('health FIX spawn (production, 3.24 T6): no PA_MOD_PA3_DIR spawns the FIX expert (TASK: FIX)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [HEALTH]: flagged })
    await $.session.start(START)
    await clock.advance(2_000)
    const fix = w.spawns.find(x => x.description === 'FIX cache_read')
    expect(fix?.prompt).toContain('TASK: FIX\nROW: cache_read\n')
    expect(notices(w).filter(x => x.input.text.includes('savings repair')).length).toBe(0)
    expect(JSON.parse(w.files.get(HEALTH) ?? '{}')).toEqual({ other: { keep: 1 } })
  })
})

// 3.23 T6a1: plans installed by the loop (the router's `install` request), the plan-needed hold, the REPLAN release, the
// loop's own asks answered through the router, and the loop silent while the router's dialog flag is open.
const GEN = `${ROOT}/GENERATION_PLAN.md`
const DRAFT = `${ROOT}/.run/plans/phase.md`
const REPLAN_MD = `${ROOT}/phase-ends/current/REPLAN.md`
const DIALOG = `${BASE}/S1/dialog.json`
type Ran = ReturnType<typeof ok>
const installReq = (w: W, ms: number, input: Record<string, unknown>, caller = 'P1'): void => {
  const id = `${String(ms)}-1-install`
  w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'install', caller, input, at: ms }))
}
/** from-draft writes PHASE_PLAN.md; the loop's `python -c` remove deletes REPLAN.md; `over` answers first. */
const installAnswer = (w: W, over: (a: string) => Ran | null = () => null) => (argv: string[]): Ran => {
  const a = argv.slice(1).join(' ')
  const o = over(a)
  if (o !== null) {
    return o
  }
  if (argv[1] === '-c' && (argv[3] ?? '').endsWith('REPLAN.md')) {
    w.files.delete(norm(argv[3] ?? ''))
    return ok()
  }
  if (a.startsWith('tools/plan_edit.py from-draft')) {
    w.files.set(`${ROOT}/${PLAN}`, '# Phase 1.1 — calc\n')
    return ok('installed\n')
  }
  return defaultAnswer(argv)
}
const toCaller = (w: W, caller = 'P1'): string[] => notices(w).filter(n => n.input.to === caller).map(n => n.input.text)
const phaseWorld = (on: On, files: Record<string, string> = {}, env: Record<string, string> = {}): W => {
  const w = world(on, {
    [GEN]: '# Generation 1\n', [DRAFT]: '# Phase 1.1 — calc\n', [LOOP]: loopAt({ state: 'off', holds: ['plan'] }),
    [`${BASE}/S1/agents/P1.json`]: JSON.stringify({ model: 'claude-opus-5-5', effort: 'high' }), ...files,
  }, env)
  w.files.delete(`${ROOT}/${PLAN}`)
  w.answer = installAnswer(w)
  return w
}

describe('plan install (T6a1)', () => {
  test('generation install: GENERATION_PLAN.md written and committed; then held(plan) and "a phase plan is needed"', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/.run/plans/gen.md`]: '# Generation 4 plan\n\n## Phase 4.1\n' })
    w.files.delete(`${ROOT}/${PLAN}`)
    installReq(w, NOW, { kind: 'generation', path: '.run/plans/gen.md', title: 'G4' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.files.get(GEN)).toBe('# Generation 4 plan\n\n## Phase 4.1\n')
    expect(commits(w)).toEqual([[BASH, 'tools/commit_task.sh', 'plan', 'Generation 4 plan approved', 'GENERATION_PLAN.md']])
    expect(toCaller(w)).toEqual(['Plan decision: approved\ninstalled: GENERATION_PLAN.md (Generation 4)'])
    expect((loopOf(w) as Loop & { install?: unknown }).install).toBe(null)
    await clock.advance(2_000)
    expect(loopOf(w).holds).toEqual(['plan'])
    expect(notices(w).filter(n => n.input.text.includes('a phase plan is needed')).length).toBe(1)
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('A phase plan is needed')
    await clock.advance(4_000)
    expect(notices(w).filter(n => n.input.text.includes('a phase plan is needed')).length).toBe(1)
    expect(w.spawns.length).toBe(0)
  })

  test('phase install: lint, from-draft, approve with the caller\'s model/effort, REPLAN removed, the commit; then T1', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = phaseWorld(on, { [REPLAN_MD]: '# replan\n' })
    installReq(w, NOW, { kind: 'phase', path: '.run/plans/phase.md', title: 'P' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py lint')).toEqual([['py', 'tools/plan_edit.py', 'lint', '.run/plans/phase.md']])
    expect(ran(w, 'tools/plan_edit.py from-draft')).toEqual([['py', 'tools/plan_edit.py', 'from-draft', '.run/plans/phase.md']])
    expect(ran(w, 'tools/plan_edit.py approve')).toEqual([['py', 'tools/plan_edit.py', 'approve', '--planner', 'claude-opus-5-5/high']])
    expect(w.files.has(REPLAN_MD)).toBe(false)
    expect(commits(w)).toEqual([[BASH, 'tools/commit_task.sh', 'plan', 'Phase 1.1 plan approved', 'phase-ends/current', 'rules']])
    expect(toCaller(w)).toEqual(['Plan decision: approved\ninstalled: phase 1.1 plan (PHASE_PLAN.md approved)'])
    const l = loopOf(w) as Loop & { install?: unknown }
    expect([l.state, l.holds, l.install]).toEqual(['off', [], null])
    await clock.advance(2_000)
    expect(experts(w).length).toBe(1)
    expect(entries(w, 'T1-start').length).toBe(1)
  })

  test('3.28.1 T6: a replan\'s install waits while a task runs; at the boundary it installs with the loop off', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = phaseWorld(on, { [REPLAN_MD]: '# replan\n', [LOOP]: running() })
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }]
    installReq(w, NOW, { kind: 'phase', path: '.run/plans/phase.md', title: 'P' })
    await $.session.start(START)
    await clock.advance(4_000)
    const waiting = loopOf(w) as Loop & { install?: { step?: string } | null }
    expect([waiting.state, waiting.task?.id, waiting.install?.step]).toEqual(['running', 'T1', 'start'])
    expect(ran(w, 'tools/plan_edit.py')).toEqual([])
    w.files.set(LOOP, JSON.stringify({ ...waiting, state: 'boundary', task: null })) // T1 done: the boundary
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py from-draft')).toEqual([['py', 'tools/plan_edit.py', 'from-draft', '.run/plans/phase.md']])
    expect(w.files.has(REPLAN_MD)).toBe(false)
    expect(toCaller(w)).toEqual(['Plan decision: approved\ninstalled: phase 1.1 plan (PHASE_PLAN.md approved)'])
    const l = loopOf(w) as Loop & { install?: unknown }
    expect([l.state, l.install]).toEqual(['off', null])
  })

  test('a lint failure is changes asked: lint: …; nothing installed or committed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = phaseWorld(on)
    w.answer = installAnswer(w, a => (a.startsWith('tools/plan_edit.py lint')
      ? { exitCode: 1, stdout: 'ok T1\nERROR: T2 has no verify\n', stderr: '' } : null))
    installReq(w, NOW, { kind: 'phase', path: '.run/plans/phase.md', title: 'P' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(toCaller(w)).toEqual(['Plan decision: changes asked: lint: ERROR: T2 has no verify'])
    expect(ran(w, 'tools/plan_edit.py from-draft').length).toBe(0)
    expect(ran(w, 'tools/plan_edit.py approve').length).toBe(0)
    expect(commits(w)).toEqual([])
    expect(w.files.has(`${ROOT}/${PLAN}`)).toBe(false)
    const l = loopOf(w) as Loop & { install?: unknown }
    expect([l.holds, l.install]).toEqual([['plan'], null])
  })

  test('rules and candidates: one ask each through the router; add, reject; build, defer, drop applied', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = phaseWorld(on)
    const asked: string[] = []
    const picks = ['add (Recommended)', 'reject', 'build this phase', 'defer', 'drop']
    on('prompt.read', async () => ({ value: { text: '', cursor: 0 } }))
    on('tool.call', { tool: 'AskUserQuestion' }, async ($, e) => {
      const q = ((e as { questions?: { question?: string }[] }).questions ?? [])[0]?.question ?? ''
      asked.push(q)
      return { result: { questions: [], answers: { [q]: picks.shift() ?? '' } } } as never
    })
    // 3.32.10: an entry's lines after its first are the planner's note, drawn as a card before its picker
    const WHY = 'Why: a push in phase 1.0 sent unreviewed work out.\nWhat it fixes: nothing leaves this machine unless you push it.'
    installReq(w, NOW, {
      kind: 'phase', path: '.run/plans/phase.md', title: 'P', rules: [`M13: never push\n${WHY}`, 'keep logs terse'],
      candidates: ['Tool candidate: lintfix | does: fixes lint | replaces: hand edits\nWhy: coders fixed lint by hand 9 times.', 'Tool candidate: cache | does: x', 'Tool candidate: old | does: y'],
    })
    await $.session.start(START)
    for (let i = 0; i < 30 && toCaller(w).length === 0; i += 1) {
      await clock.advance(1_000)
    }
    expect(asked).toEqual(['Add this rule? M13: never push', 'Add this rule? keep logs terse', 'Tool candidate: lintfix | does: fixes lint | replaces: hand edits',
      'Tool candidate: cache | does: x', 'Tool candidate: old | does: y'])
    // the two cards, each written before its picker's request; none for an entry with no note
    const inbox = [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && /-(card|ask)\.json$/.test(p)).sort()
      .map(p => JSON.parse(w.files.get(p) ?? '{}') as { kind?: string; input: { text?: string; question?: string } })
    expect(inbox.map(r => r.input.text ?? r.input.question)).toEqual([
      `**🟪 Planning · Proposed rule M13**\n\n> never push\n\n${WHY}`, 'Add this rule? M13: never push', 'Add this rule? keep logs terse',
      '**🟪 Planning · Tool candidate lintfix**\n\n> lintfix | does: fixes lint | replaces: hand edits\n\nWhy: coders fixed lint by hand 9 times.',
      'Tool candidate: lintfix | does: fixes lint | replaces: hand edits', 'Tool candidate: cache | does: x', 'Tool candidate: old | does: y'])
    expect(ran(w, 'tools/rules_add.py')).toEqual([['py', 'tools/rules_add.py', 'add', '--id', 'M13', '--title', 'never push', '--origin', 'phase 1.1 plan']])
    expect(ran(w, 'tools/plan_edit.py add-task')).toEqual([['py', 'tools/plan_edit.py', 'add-task', '--title', 'tool candidate lintfix: fixes lint',
      '--done-when', 'hand edits', '--coder', 'opus55', '--by', 'developer']])
    expect(ran(w, 'tools/plan_edit.py append-change')).toEqual([['py', 'tools/plan_edit.py', 'append-change', 'candidate old dropped: by the developer', '--by', 'developer']])
    expect(toCaller(w)).toEqual(['Plan decision: approved\ninstalled: phase 1.1 plan (PHASE_PLAN.md approved); rule M13 added; rule rejected: keep logs terse; ' +
      'candidate lintfix built as a task; candidate cache deferred; candidate old dropped'])
    expect(commits(w).length).toBe(1)
  })

  test('3.32.11 T14: an auditor\'s Rule candidate is asked as a rule: its card, Add this rule?, rules_add on add; tool candidates unchanged', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = phaseWorld(on)
    const asked: string[] = []
    const picks = ['add (Recommended)', 'reject', 'defer']
    on('prompt.read', async () => ({ value: { text: '', cursor: 0 } }))
    on('tool.call', { tool: 'AskUserQuestion' }, async ($, e) => {
      const q = ((e as { questions?: { question?: string }[] }).questions ?? [])[0]?.question ?? ''
      asked.push(q)
      return { result: { questions: [], answers: { [q]: picks.shift() ?? '' } } } as never
    })
    const WHY = 'Why: coders handed back red runs 4 times.'
    installReq(w, NOW, {
      kind: 'phase', path: '.run/plans/phase.md', title: 'P', rules: [],
      candidates: [`- Rule candidate: coders run the gate before handing back | from: T3 hand-backs\n${WHY}`,
        'Rule candidate: keep briefs short', 'Tool candidate: lintfix | does: fixes lint | replaces: hand edits'],
    })
    await $.session.start(START)
    for (let i = 0; i < 30 && toCaller(w).length === 0; i += 1) {
      await clock.advance(1_000)
    }
    expect(asked).toEqual(['Add this rule? coders run the gate before handing back', 'Add this rule? keep briefs short',
      'Tool candidate: lintfix | does: fixes lint | replaces: hand edits'])
    const inbox = [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && /-(card|ask)\.json$/.test(p)).sort()
      .map(p => JSON.parse(w.files.get(p) ?? '{}') as { input: { text?: string; question?: string } })
    expect(inbox[0]?.input.text).toBe(`**🟪 Planning · Proposed rule**\n\n> coders run the gate before handing back\n\n${WHY}`)
    expect(ran(w, 'tools/rules_add.py')).toEqual([['py', 'tools/rules_add.py', 'add', '--id', 'M1', '--title', 'coders run the gate before handing back',
      '--origin', 'phase 1.1 plan (audit: T3 hand-backs)']])
    expect(ran(w, 'tools/plan_edit.py add-task')).toEqual([])
    expect(toCaller(w)).toEqual(['Plan decision: approved\ninstalled: phase 1.1 plan (PHASE_PLAN.md approved); rule M1 added; ' +
      'rule rejected: keep briefs short; candidate lintfix deferred'])
  })

  test('release question with REPLAN.md: replan-written, held(plan), the REPLAN notice; no expert resumed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 60_000), [REPLAN_MD]: '# replan\n' })
    releaseReq(w, NOW, { hold: 'question', note: 'drop T3' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py replan-written').length).toBe(1)
    expect(loopOf(w).holds).toEqual(['plan'])
    expect(toCaller(w)).toEqual(['released: question\n‹pa› the plan needs replanning (REPLAN.md): draft the phase plan with the pa-planning skill ' +
      'and present it with plan(kind: phase)'])
    expect(w.sends).not.toContain('NOTE: drop T3')
    await clock.advance(4_000)
    expect(experts(w).length).toBe(0)
  })

  test('dialog.json open: the loop drains, spawns and sends nothing; closed, it goes on', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [DIALOG]: JSON.stringify({ open: true, id: 'x', at: NOW }) })
    const id = releaseReq(w, NOW, { hold: 'talk' })
    await $.session.start(START)
    await clock.advance(6_000)
    expect(w.spawns.length).toBe(0)
    expect(ran(w, 'tools/launch.py').length).toBe(0)
    expect(Object.keys((JSON.parse(w.files.get(LOOP) ?? '{}') as { inboxDone?: Record<string, number> }).inboxDone ?? {})).not.toContain(id)
    w.files.set(DIALOG, JSON.stringify({ open: false, id: 'x', at: NOW }))
    await clock.advance(2_000)
    expect(Object.keys((JSON.parse(w.files.get(LOOP) ?? '{}') as { inboxDone?: Record<string, number> }).inboxDone ?? {})).toContain(id)
    expect(experts(w).length).toBe(1)
  })
})

// 3.23 T6a2: the generation start (inbox, the curator ask, memory-curator, the auditor, one notice, `held(plan)`), the
// migration curate ask, `plan` popped by the generation install, and a repeated contract ignored.
const CURATE = 'curate: closing gen 3 -> opening gen 4'
type Gen = { kind: string; sid: string; step: string; agentId: string; recap: string; audit: string }
const genOf = (w: W): Gen | null => (JSON.parse(w.files.get(LOOP) ?? '{}') as { genStart?: Gen | null }).genStart ?? null
const launchDoc = (o: Record<string, unknown>) => (argv: string[]): Ran =>
  argv.slice(1).join(' ').startsWith('tools/launch.py') ? ok(`${JSON.stringify({ mode: 'planner-gen', curate: null, audit_flag: null, deferred: [], ...o })}\n`)
    : defaultAnswer(argv)
/** The router's AskUserQuestion answered from `picks`; each question recorded with the spawns seen at that moment. */
const askWith = (on: On, w: W, picks: string[]): { q: string; spawns: number }[] => {
  const asked: { q: string; spawns: number }[] = []
  on('prompt.read', async () => ({ value: { text: '', cursor: 0 } }))
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e) => {
    const q = ((e as { questions?: { question?: string }[] }).questions ?? [])[0]?.question ?? ''
    asked.push({ q, spawns: w.spawns.length })
    return { result: { questions: [], answers: { [q]: picks.shift() ?? '' } } } as never
  })
  return asked
}
const noPlanWorld = (on: On, files: Record<string, string> = {}): W => {
  const w = world(on, files)
  w.files.delete(`${ROOT}/${PLAN}`)
  return w
}

// 3.23 T13d.c11: nothing starts on its own. Every session start waits in held(session) for the developer's message and
// Claude's release({hold: 'session'}); a plain developer message starts nothing.
describe('the session hold (T13d.c11)', () => {
  for (const source of ['startup', 'resume', 'clear'] as const) {
    test(`an approved plan after SessionStart ${source}: no spawn until a developer message and the release`, async ($, on) => {
      const clock = mock.clock(on, { now: NOW })
      const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR })
      w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
      await $.session.start(ISTART)
      await $.classic.SessionStart({ source, session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
      for (let i = 0; i < 5; i += 1) {
        await clock.advance(2_000)
      }
      expect([loopOf(w).holds, w.spawns.length]).toEqual([['session'], 0])
      await $.prompt.submit({ text: 'morning', origin: { kind: 'composer' }, wait: false })
      for (let i = 0; i < 5; i += 1) {
        await clock.advance(2_000)
      }
      expect([loopOf(w).holds, experts(w).length]).toEqual([['session'], 0]) // a plain message starts nothing, pushes no talk
      expect(delivered(w).some(t => t.includes('the loop is waiting for you') && t.includes('morning'))).toBe(true)
      releaseReq(w, NOW + 22_000, { hold: 'session' })
      for (let i = 0; i < 3 && experts(w).length === 0; i += 1) {
        await clock.advance(2_000)
      }
      expect(experts(w).length).toBe(1)
      expect(loopOf(w).holds).toEqual([])
    })
  }

  test('3.28.1 T9c: a task taken over at a resume: its recovery runs under the session hold, continue: at once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000) })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    expect([loop3(w).holds, loop3(w).task?.recovery?.phase, w.sends]).toEqual([['session'], 'sent', [CONTINUE_TEXT]])
    expect(notices(w).map(n => n.input.text).filter(t => t.includes('cut off when the session ended'))).toHaveLength(1)
  })

  test('3.29.1 T8: no recovery until this start\'s update check answers; an earlier start\'s note does not count', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [`${ROOT}/${START_CHECK}`]: JSON.stringify({ session: 'S1', at: (NOW - 3_600_000) / 1000, update: false }) })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.sends).toEqual([])
    w.files.set(`${ROOT}/${START_CHECK}`, JSON.stringify({ session: 'S1', at: (NOW + 6_000) / 1000, update: false }))
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    expect([loop3(w).task?.recovery?.phase, w.sends]).toEqual(['sent', [CONTINUE_TEXT]])
  })

  test('3.29.1 T8: a check that never answers lets work go on after the wait', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [`${ROOT}/${START_CHECK}`]: '{}' })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 55; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.sends).toEqual([])
    for (let i = 0; i < 8; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.sends).toEqual([CONTINUE_TEXT])
  })

  test('3.29.7: an update\'s after-restart work (the cleanup) comes first: the task is held, then Claude asks to continue', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [`${ROOT}/.run/P37/census/sites.jsonl`]: '{}\n' })
    const asked = askWith(on, w, ['Not now'])
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    const lp = loop3(w)
    const stopped = (lp.task as { stopped?: { status?: string } | null } | null)?.stopped?.status
    expect([lp.holds.includes('stopped'), stopped, w.sends]).toEqual([true, 'post-update', []])
    expect(asked.filter(a => a.q.includes('one-time cleanup'))).toHaveLength(1)
    expect(notices(w).some(n => n.input.text.includes('Ready to continue work?'))).toBe(true)
    expect(notices(w).some(n => n.input.text.includes('cut off when the session ended'))).toBe(false)
  })

  test('3.29.1 T9: a task paused on request is held at the restart, and Claude asks whether to resume', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const doc = JSON.parse(orphan(NOW - 50_000)) as { task: Record<string, unknown> }
    doc.task['paused'] = { at: NOW - 60_000 }
    const w = world(on, { [LOOP]: JSON.stringify(doc) })
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    const lp = loop3(w)
    const stopped = (lp.task as { stopped?: { status?: string } | null } | null)?.stopped?.status
    expect([lp.holds.includes('stopped'), stopped, lp.task?.recovery ?? null, w.sends]).toEqual([true, 'paused', null, []])
    const asks = notices(w).map(n => n.input.text).filter(t => t.includes('paused on the developer\'s request'))
    expect(asks).toHaveLength(1)
    expect(notices(w).some(n => n.input.text.includes('cut off when the session ended'))).toBe(false)
  })

  test('3.24 T12c: a message routed before the resume\'s first owning tick gets the session line, not the talk line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000), [`${BASE}/S1/state.json`]: PRIOR })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    await $.session.start(ISTART)
    await $.classic.SessionStart({ source: 'resume', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    await $.prompt.submit({ text: 'where is the phase at?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000) // the text was made at the submit, before this tick pushed `session`
    const first = delivered(w).filter(t => t.includes('where is the phase at?'))
    expect(first.some(t => t.includes('the loop is waiting for you') && t.includes('release({hold: "session"})'))).toBe(true)
    expect(first.some(t => t.includes('the queue is held while you talk'))).toBe(false)
    expect(loop3(w).holds).toEqual(['session'])
  })

  test('a generation end then a fresh start: no curator question until the developer\'s release', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation'] }) })
    w.answer = launchDoc({ curate: CURATE })
    const asked = askWith(on, w, ['Run it now (Recommended)'])
    await $.session.start(START)
    await $.classic.SessionStart({ source: 'startup', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(2_000)
    }
    expect([asked.length, w.spawns.length, loopOf(w).holds]).toEqual([0, 0, ['generation', 'session']])
    releaseReq(w, NOW + 12_000, { hold: 'session' })
    for (let i = 0; i < 6 && asked.length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked.map(a => a.q)).toEqual([`The memory curator runs at every generation start (${CURATE}). Run it now?`])
  })
})

describe('generation start (T6a2)', () => {
  test('inbox-consume first, launch.py again, then the curator ask; Run it now spawns memory-curator with its brief', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on)
    w.answer = launchDoc({ curate: CURATE, deferred: [{ id: 'D1', text: 'retry the cache' }] })
    const asked = askWith(on, w, ['Run it now (Recommended)'])
    await $.session.start(START)
    for (let i = 0; i < 6 && w.spawns.length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.runs.map(r => r.slice(1, 3).join(' ')).slice(0, 3)).toEqual(['tools/launch.py --state-json', 'tools/status.py inbox-consume', 'tools/launch.py --state-json'])
    expect(asked.map(a => a.q)).toEqual([`The memory curator runs at every generation start (${CURATE}). Run it now?`])
    expect(w.spawns.length).toBe(1)
    expect(w.spawns[0]?.description).toBe('memory curator')
    expect(w.spawns[0]?.prompt).toBe(`CURATE: ${CURATE}\nMEMORY: .claude-state/memory\nRETURN: recap`)
    expect([genOf(w)?.step, genOf(w)?.agentId, loopOf(w).state]).toEqual(['curator', 'E1', 'off'])
  })

  test('the curator\'s recap at runEnded, no PhaseEnd so no auditor; one notice with its parts, then held(plan)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on)
    w.answer = launchDoc({ curate: CURATE, deferred: [{ id: 'D1', text: 'retry the cache' }] })
    askWith(on, w, ['Run it now (Recommended)'])
    await $.session.start(START)
    for (let i = 0; i < 6 && w.spawns.length === 0; i += 1) {
      await clock.advance(2_000)
    }
    await $.turn.complete(complete('E1', 'kept 3, demoted 2'))
    await clock.advance(10_000)
    expect(genOf(w)?.step).toBe('curator')
    await clock.advance(8_000)
    expect(genOf(w)?.step).toBe('done')
    expect(w.spawns.length).toBe(1)
    expect(w.files.get(entries(w, 'mod-end')[0] ?? '')).toContain('role: memory-curator')
    expect(w.files.get(entries(w, 'mod-end')[0] ?? '')).toContain('kept 3, demoted 2')
    const gen = notices(w).filter(n => n.input.text.startsWith('‹pa› Generation start.'))
    expect(gen.length).toBe(1)
    expect(gen[0]?.input.text).toBe(['‹pa› Generation start.', 'memory curator recap:\nkept 3, demoted 2',
      'archived memories: .claude-state/memory/gen3.md (tell the developer, in case he wants to keep something)',
      'deferred items:\n- D1: retry the cache\nsettle these with the developer (tools/discussion.py triage) before drafting',
      'a generation plan is needed: draft it with the pa-planning skill and present it with plan(kind: generation)'].join('\n'))
    expect(loopOf(w).holds).toEqual(['plan'])
    await clock.advance(10_000)
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› Generation start.')).length).toBe(1)
  })

  test('3.24 T13A: the curator\'s hand-back is its recap, a later turn never replaces it; no quiet wait after it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on)
    w.answer = launchDoc({ curate: CURATE })
    askWith(on, w, ['Run it now (Recommended)'])
    await $.session.start(START)
    for (let i = 0; i < 6 && w.spawns.length === 0; i += 1) {
      await clock.advance(2_000)
    }
    await $.prompt.submit({ text: notification('E1', 'kept 3, demoted 2'), origin: { kind: 'task-notification' }, wait: false })
    await $.turn.complete(complete('E1', 'That background find finally finished.'))
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(genOf(w)?.step).toBe('done')
    const gen = notices(w).filter(n => n.input.text.startsWith('‹pa› Generation start.'))
    expect(gen.length).toBe(1)
    expect(gen[0]?.input.text).toContain('memory curator recap:\nkept 3, demoted 2')
    expect(gen[0]?.input.text).not.toContain('background find')
  })

  test('Pause: journaled, held(plan) with a note, no curator; not asked again this session', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on)
    w.answer = launchDoc({ curate: CURATE })
    const asked = askWith(on, w, ['Pause'])
    await $.session.start(START)
    await clock.advance(12_000)
    expect(asked.length).toBe(1)
    expect(w.spawns.length).toBe(0)
    expect(genOf(w)?.step).toBe('done')
    expect(loopOf(w).holds).toEqual(['plan'])
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('paused')
    expect(notices(w).filter(n => n.input.text.includes('paused the memory curator')).length).toBe(1)
    await clock.advance(40_000)
    expect(asked.length).toBe(1)
  })

  test('a PhaseEnd: the auditor with its brief; its contract\'s counts in the notice; no curate line, no ask', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [`${ROOT}/phase-ends/PhaseEnd_3.1.md`]: '# 3.1\n', [`${ROOT}/phase-ends/PhaseEnd_3.2.md`]: '# 3.2\n' })
    w.answer = launchDoc({ audit_flag: 'Audit flag: 3 rows' })
    const asked = askWith(on, w, [])
    await $.session.start(START)
    await clock.advance(4_000)
    expect(asked.length).toBe(0)
    expect(w.spawns.map(s => s.description)).toEqual(['auditor 3.2'])
    expect(w.spawns[0]?.prompt).toBe('AUDIT: Audit flag: 3 rows\nPHASE: 3.2\nTABLES: phase-ends/PhaseEnd_3.2.md\nPREVIOUS: phase-ends/PhaseEnd_3.1.md\nRETURN: contract')
    await $.prompt.submit({ text: notification('E1', 'STATUS: done\nVERDICTS: 4\nFIXED: 1\nCANDIDATES: 2\nFILE: phase-ends/current/AUDIT.md'),
      origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(18_000)
    const gen = notices(w).filter(n => n.input.text.startsWith('‹pa› Generation start.'))
    expect(gen.length).toBe(1)
    expect(gen[0]?.input.text).toContain('auditor: VERDICTS 4, FIXED 1, CANDIDATES 2;')
    expect(gen[0]?.input.text).not.toContain('memory curator recap')
    expect(w.files.get(entries(w, 'mod-end')[0] ?? '')).toContain('role: auditor')
    expect(loopOf(w).holds).toEqual(['plan'])
  })

  test('the generation install pops plan', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, {
      [`${ROOT}/.run/plans/gen.md`]: '# Generation 4 plan\n',
      [LOOP]: loopAt({ state: 'off', holds: ['plan'], genStart: { kind: 'gen', sid: 'S1', step: 'done' } }),
    })
    w.answer = launchDoc({})
    installReq(w, NOW, { kind: 'generation', path: '.run/plans/gen.md', title: 'G4' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.files.get(GEN)).toBe('# Generation 4 plan\n')
    expect(loopOf(w).holds).toEqual([])
  })

  test('migration curate line: asked once before the next spawn; Pause is a notice only, no plan hold; the loop goes on', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    const mig = 'curate: migration -> gen legacy'
    w.answer = argv => (argv.slice(1).join(' ').startsWith('tools/launch.py')
      ? ok(`${JSON.stringify({ mode: 'router', curate: mig, audit_flag: null, deferred: [] })}\n`) : defaultAnswer(argv))
    const asked = askWith(on, w, ['Pause'])
    await $.session.start(START)
    for (let i = 0; i < 40 && experts(w).length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked).toEqual([{ q: `The memory curator runs at every generation start (${mig}). Run it now?`, spawns: 0 }])
    expect(notices(w).filter(n => n.input.text.includes('paused the memory curator')).length).toBe(1)
    expect(ran(w, 'tools/status.py inbox-consume').length).toBe(0)
    expect(experts(w).length).toBe(1)
    expect(loopOf(w).holds).toEqual([])
    await clock.advance(40_000)
    expect(asked.length).toBe(1)
  })

  // 3.32 B9: the PA3 intake: one memory-curator, one run, one marker.
  const paWith = (o: Record<string, unknown>): string => JSON.stringify({ mod: 'on', python: 'py', ...o })
  const FIRST = { first_run: { from: 'pa2', at: '3.24' } }
  const PA_JSON = `${ROOT}/.claude/pa.json`
  const WORK_CARD = `${ROOT}/HOW_WE_WORK.md`
  const PAGE = `${ROOT}/docs/PRE_PA3_STATE.md`
  const LIVING = '# How we work\n\n## Living docs <!-- roles: claude -->\n- `a.md`: a; with a\n\n## Next\n'
  const NO_LIVING = '# How we work\n\n## Living docs <!-- roles: claude -->\n- (none yet: `doc`: what it answers; when to update it)\n'
  const HELLO = { text: 'hello', origin: { kind: 'composer' }, wait: false } as const
  const intakes = (w: W): { prompt: string; description: string }[] => w.spawns.filter(x => x.description === 'PA3 intake')
  const marks = (w: W): string[] => w.runs.filter(r => r[1] === 'tools/history.py').map(r => r.slice(2).join(' '))
  const commits = (w: W): string[][] => w.runs.filter(r => r.some(a => a.endsWith('commit_task.sh'))).map(r => r.slice(r.findIndex(a => a === 'PA3 intake')))
  const drawn = (w: W): string[] => [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && p.endsWith('-card.json')).sort()
    .map(p => (JSON.parse(w.files.get(p) ?? '{}') as { input: { text: string } }).input.text)
  const job = (w: W): unknown => (loopOf(w) as Loop & { bgJob?: unknown }).bgJob ?? null
  const tries = (w: W): number => (loopOf(w) as Loop & { intakeTries?: number }).intakeTries ?? 0
  const curator = (w: W, n = intakes(w).length): string => {
    const at = w.spawns.indexOf(w.spawns.filter(x => x.description === 'PA3 intake')[n - 1] as W['spawns'][number])
    return `E${String(at + 1)}`
  }
  /** A world whose fake tools answer: state-sources, and `mark` writing the marker. */
  const intakeWorld = (w: W, mode = 'router'): void => {
    const doc = launchDoc({ mode })
    w.answer = argv => {
      if (argv[2] === 'mark') {
        const o = JSON.parse(w.files.get(PA_JSON) ?? '{}') as Record<string, unknown>
        w.files.set(PA_JSON, JSON.stringify({ ...o, intake: argv.includes('--incomplete') ? 'incomplete' : 'done' }))
      }
      return argv[1] === 'tools/phaseend_index.py' ? { exitCode: 0, stdout: 'CURRENT_PHASE.md\nGENERATION_PLAN.md\n', stderr: '' } : doc(argv)
    }
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
  }
  type Clock = ReturnType<typeof mock.clock>
  const until = async (clock: Clock, done: () => boolean): Promise<void> => {
    for (let i = 0; i < 12 && !done(); i += 1) {
      await clock.advance(2_000)
    }
  }
  /** The curator of try `n` ends with `status`; the tick that sees it runs. */
  const endTry = async (clock: Clock, w: W, n: number, status: Agent['status']): Promise<void> => {
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: curator(w, n), status, type: 'memory-curator' }]
    await until(clock, () => job(w) === null)
    await clock.advance(2_000)
  }

  test('a first run: no picker: one curator, its card once, the marker, the commit and the end line; no notice, no hold', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ ...FIRST, intake: 'pending' }), [WORK_CARD]: NO_LIVING, [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n' })
    intakeWorld(w)
    const asked = askWith(on, w, [])
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    expect(intakes(w)[0]?.prompt).toContain('CURATE: intake · MEMORY: .claude-state/memory · PAGE: docs/PRE_PA3_STATE.md · FROM: pa2\nSTEPS: memories, living docs, state page\nWRITE: Where does this project stand?')
    expect(intakes(w)[0]?.prompt).toContain('SCOPE: the living docs, git log, the roadmap files, then CURRENT_PHASE.md, GENERATION_PLAN.md.')
    expect(drawn(w)).toEqual(['**PA3 intake** · one agent reads this project once, in the background\n\n1. Sort its memories\n' +
      "2. Map its docs and write the card's living docs\n3. Write where the project stands: `docs/PRE_PA3_STATE.md`\n\n" +
      'Keep talking as usual. A new plan waits until this is done.'])
    await clock.advance(6_000)
    expect([drawn(w).length, intakes(w).length, marks(w), genOf(w)]).toEqual([1, 1, [], null])
    expect(loopOf(w).holds).toEqual([])
    w.files.set(PA_JSON, paWith({ migrated: '3.2', intake: 'pending' }))
    w.files.set(WORK_CARD, LIVING)
    w.files.set(PAGE, '# Pre-PA3 state\n')
    await endTry(clock, w, 1, 'completed')
    expect(marks(w)).toEqual(['mark intake'])
    expect(commits(w)).toEqual([['PA3 intake', 'docs/PRE_PA3_STATE.md', 'HOW_WE_WORK.md', '.claude/pa.json']])
    expect(drawn(w)[1]).toBe('**PA3 intake** done · memories sorted (archive: .claude-state/memory/gen3.2.md), living docs on the card, docs/PRE_PA3_STATE.md written')
    expect(notices(w).filter(n => /intake/i.test(n.input.text))).toEqual([])
    expect(asked).toEqual([])
    await clock.advance(6_000)
    expect([intakes(w).length, marks(w), drawn(w).length]).toEqual([1, ['mark intake'], 2])
  })

  test('an install with no git never wrote the marker: a first run is still owed, and the job is watched after the sort', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith(FIRST), [WORK_CARD]: NO_LIVING })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    w.files.set(PA_JSON, paWith({ migrated: '3.2' })) // the sort is done: intakeDue goes false, the job still runs
    w.files.set(WORK_CARD, LIVING)
    w.files.set(PAGE, '# Pre-PA3 state\n')
    await endTry(clock, w, 1, 'completed')
    expect([marks(w), job(w)]).toEqual([['mark intake'], null])
  })

  test('nothing before the developer\'s first message, under a session hold or not; it lifts no hold', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ ...FIRST, intake: 'pending' }), [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n', [`${BASE}/S1/state.json`]: PRIOR })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.classic.SessionStart({ source: 'startup', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    await clock.advance(10_000)
    expect(intakes(w)).toEqual([])
    await $.prompt.submit({ ...HELLO, text: 'Start the work.' })
    await until(clock, () => intakes(w).length > 0)
    expect(intakes(w).length).toBe(1)
    expect(entries(w, 'mod-hold').map(p => w.files.get(p) ?? '').some(t => t.includes('The PA3 intake, try 1: agent'))).toBe(true)
  })

  test('no spawn while the update check is pending', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n', [`${ROOT}/${START_CHECK}`]: '{}' })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await clock.advance(10_000)
    expect(intakes(w)).toEqual([])
    w.files.set(`${ROOT}/${START_CHECK}`, JSON.stringify({ session: 'S1', at: (NOW + 11_000) / 1000, update: false, state: 'done' }))
    await until(clock, () => intakes(w).length > 0)
    expect(intakes(w).length).toBe(1)
  })

  test('a plan is refused and not installed while it is due', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n' })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    installReq(w, NOW + 30_000, { kind: 'phase', path: '.run/plans/phase.md', title: 'Phase 1.1' })
    await clock.advance(4_000)
    expect((loopOf(w) as { install?: unknown }).install ?? null).toBe(null)
    expect(toCaller(w).some(t => t.includes('not installed: The PA3 intake is reading this project once'))).toBe(true)
    expect(loopOf(w).holds).toEqual([])
  })

  test('a generation start does not begin while it is due, and does after: lifted holds, one curator at a time', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: LIVING, [LOOP]: loopAt({ state: 'off', holds: ['generation', 'session'], genParkSid: 'S0' }) })
    intakeWorld(w, 'planner-gen')
    releaseReq(w, NOW, { hold: 'generation' })
    await $.session.start(ISTART)
    await clock.advance(4_000)
    expect([loopOf(w).holds, genOf(w)]).toEqual([[], null])
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    expect(genOf(w)).toBe(null)
    w.files.set(PAGE, '# Pre-PA3 state\n')
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: curator(w), status: 'running', type: 'memory-curator' }]
    await clock.advance(4_000)
    expect(genOf(w)).toBe(null)
    await endTry(clock, w, 1, 'completed')
    await until(clock, () => genOf(w) !== null)
    expect([genOf(w)?.kind, genOf(w)?.sid, intakes(w).length]).toEqual(['gen', 'S1', 1])
  })

  test('the legacy curate line starts no second curator while it is due', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PA_JSON]: paWith({ intake: 'pending', bash: 'bash' }), [WORK_CARD]: LIVING, [PAGE]: '# Pre-PA3 state\n' })
    const doc = launchDoc({ mode: 'router', curate: 'curate: migration -> gen legacy' })
    w.answer = doc
    await $.session.start(START)
    await clock.advance(6_000)
    expect(genOf(w)).toBe(null)
  })

  test('no task starts during a memory sort', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PA_JSON]: paWith({ ...FIRST, intake: 'pending' }) })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await clock.advance(30_000) // past the talk hold
    expect(intakes(w).length).toBe(1)
    expect(experts(w)).toEqual([])
  })

  test('a job with a first run owed waits for a running task', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PA_JSON]: paWith({ ...FIRST, intake: 'pending' }) })
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect(experts(w).length).toBe(1)
    intakeWorld(w)
    w.list = [...w.list, { id: 'E1', status: 'running', type: 'expert' }]
    await $.prompt.submit(HELLO)
    await clock.advance(10_000)
    expect(intakes(w)).toEqual([])
    w.files.set(LOOP, JSON.stringify({ ...loopOf(w), task: null, state: 'off' }))
    await until(clock, () => intakes(w).length > 0)
    expect(intakes(w).length).toBe(1)
  })

  test('a job with no first run owed runs beside a running task', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PA_JSON]: paWith({ intake: 'pending' }) })
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect(experts(w).length).toBe(1)
    intakeWorld(w)
    w.list = [...w.list, { id: 'E1', status: 'running', type: 'expert' }]
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    expect(intakes(w).length).toBe(1)
    expect(loopOf(w).task?.id).toBe('T1')
  })

  test('a stopped or stateless try leaves it pending; it reruns on the next message with only what is missing; a stop counts', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: NO_LIVING, [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n' })
    intakeWorld(w)
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    w.files.set(WORK_CARD, LIVING)
    await endTry(clock, w, 1, 'killed')
    await clock.advance(6_000)
    expect([job(w), marks(w), commits(w), intakes(w).length, tries(w)]).toEqual([null, [], [], 1, 1])
    expect(w.files.get(PA_JSON)).toContain('"intake":"pending"')
    await $.prompt.submit({ ...HELLO, text: 'and now?' })
    await until(clock, () => intakes(w).length > 1)
    const brief = intakes(w)[1]?.prompt ?? ''
    expect(brief).toContain('CURATE: intake · MEMORY: .claude-state/memory · PAGE: docs/PRE_PA3_STATE.md\nSTEPS: state page\n')
    expect(drawn(w)[1]).toBe('**PA3 intake** · one agent reads this project once, in the background · try 2 of 3\n\n' +
      '1. Write where the project stands: `docs/PRE_PA3_STATE.md`\n\nKeep talking as usual. A new plan waits until this is done.')
    w.files.set(PAGE, '# Pre-PA3 state\n')
    await endTry(clock, w, 2, 'completed')
    expect(marks(w)).toEqual(['mark intake'])
    expect(drawn(w)[2]).toBe('**PA3 intake** done · docs/PRE_PA3_STATE.md written')
  })

  test('the third failed try writes incomplete, tells Claude once with the missing step, and plans go ahead', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: LIVING, [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n' })
    intakeWorld(w)
    await $.session.start(ISTART)
    for (const n of [1, 2, 3]) {
      await $.prompt.submit({ ...HELLO, text: `message ${String(n)}` })
      await until(clock, () => intakes(w).length === n)
      expect(intakes(w)[n - 1]?.prompt).toContain('STEPS: state page\n')
      await endTry(clock, w, n, 'completed')
    }
    expect(marks(w)).toEqual(['mark intake --incomplete'])
    expect(commits(w)).toEqual([['PA3 intake', 'HOW_WE_WORK.md', '.claude/pa.json']])
    const told = notices(w).filter(n => /intake/i.test(n.input.text)).map(n => n.input.text)
    expect(told).toEqual(['‹pa› The PA3 intake stopped after 3 tries; planning goes ahead without: state page. Tell the developer that in one line.'])
    expect(drawn(w).length).toBe(3) // no end line
    await $.prompt.submit({ ...HELLO, text: 'one more' })
    await clock.advance(6_000)
    expect(intakes(w).length).toBe(3)
    installReq(w, NOW + 300_000, { kind: 'phase', path: '.run/plans/phase.md', title: 'Phase 1.1' })
    await clock.advance(4_000)
    expect(toCaller(w).some(t => t.includes('not installed'))).toBe(false)
    expect(notices(w).filter(n => /intake/i.test(n.input.text)).length).toBe(1)
  })

  test('a session that died counts as a try: it ends the job and reruns; its last try ends the intake', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const old = { steps: ['living docs', 'state page'], agentId: 'OLD', sid: 'S0', spawnedAt: NOW - 1_000, lastCompleteAt: 0 }
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: NO_LIVING, [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n',
      [LOOP]: loopAt({ state: 'off', ownerSid: 'S0', heartbeatAt: NOW - 3_600_000, bgJob: old, intakeTries: 1 } as never) })
    intakeWorld(w)
    await $.session.start(ISTART)
    await until(clock, () => job(w) === null)
    await $.prompt.submit(HELLO)
    await until(clock, () => intakes(w).length > 0)
    expect([tries(w), drawn(w)[0]?.includes('try 2 of 3')]).toEqual([2, true])
    w.files.set(LOOP, JSON.stringify({ ...loopOf(w), intakeTries: 3, bgJob: old }))
    await clock.advance(6_000)
    expect(marks(w)).toEqual(['mark intake --incomplete'])
    expect(notices(w).filter(n => n.input.text.includes('planning goes ahead without: living docs, state page')).length).toBe(1)
  })

  test('past the cap with no job, or nothing owed: it ends with the message: no agent, no card', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: LIVING, [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n',
      [LOOP]: loopAt({ state: 'off', intakeTries: 3 } as never) })
    intakeWorld(w)
    await $.session.start(ISTART)
    await clock.advance(6_000)
    expect(marks(w)).toEqual([])
    await $.prompt.submit(HELLO)
    await until(clock, () => marks(w).length > 0)
    expect([marks(w), intakes(w), drawn(w)]).toEqual([['mark intake --incomplete'], [], []])
    expect(notices(w).filter(n => n.input.text.includes('planning goes ahead without: state page')).length).toBe(1)
    w.files.set(PAGE, '# Pre-PA3 state\n')
    w.files.set(PA_JSON, paWith({ intake: 'pending' }))
    await $.prompt.submit({ ...HELLO, text: 'again' })
    await until(clock, () => marks(w).length > 1)
    expect([marks(w)[1], intakes(w), drawn(w)]).toEqual(['mark intake', [], []])
  })

  test('a mark that fails stays pending and runs nothing again until the next message; a failed commit does not stop it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ intake: 'pending' }), [WORK_CARD]: LIVING, [PAGE]: '# Pre-PA3 state\n', [`${ROOT}/GENERATION_PLAN.md`]: '# gen\n' })
    intakeWorld(w)
    const ok = w.answer
    let fails = true
    w.answer = argv => (fails && argv[2] === 'mark' ? { exitCode: 1, stdout: '', stderr: 'locked' }
      : argv.some(a => a.endsWith('commit_task.sh')) ? { exitCode: 1, stdout: 'refused: nothing staged', stderr: '' } : ok(argv))
    await $.session.start(ISTART)
    await $.prompt.submit(HELLO)
    await until(clock, () => marks(w).length > 0)
    await clock.advance(6_000)
    expect([marks(w).length, w.files.get(PA_JSON)?.includes('"intake":"pending"')]).toEqual([1, true])
    fails = false
    await $.prompt.submit({ ...HELLO, text: 'again' })
    await until(clock, () => marks(w).length > 1)
    expect(w.files.get(PA_JSON)).toContain('"intake":"done"')
    expect(entries(w, 'mod-decision').some(p => /The PA3 intake is done; .*refused: nothing staged/s.test(w.files.get(p) ?? ''))).toBe(true)
  })

  test('a project with no marker and no first run gets no intake; no job once the first run is migrated', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [PA_JSON]: paWith({ ...FIRST, migrated: '3.2' }) })
    w.answer = launchDoc({ mode: 'router' })
    const asked = askWith(on, w, [])
    await $.session.start(START)
    await $.prompt.submit(HELLO)
    await clock.advance(10_000)
    expect([asked.length, intakes(w).length, marks(w)]).toEqual([0, 0, []])
  })

  test('a contract identical to the last routed one is ignored, with no journal line (3.25 T3)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    await $.session.start(START)
    await clock.advance(2_000)
    const q = 'STATUS: question\nQUESTION: floats?\nRECOMMENDED: yes'
    await $.turn.complete(complete('E1', q))
    await clock.advance(18_000)
    expect(loopOf(w).holds).toEqual(['question'])
    expect((loopOf(w).task as unknown as { lastRouted?: string } | null)?.lastRouted).toBe(q)
    const interims = entries(w, 'T1-interim').length
    await $.turn.complete(complete('E1', q))
    await $.prompt.submit({ text: notification('E1', q), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(2_000)
    expect(entries(w, 'T1-repeat').length).toBe(0)
    expect(loopOf(w).holds).toEqual(['question'])
    expect(entries(w, 'T1-interim').length).toBe(interims)
  })
})

describe('router skips the generation start\'s agents (T6a2)', () => {
  for (const [step, id] of [['curator', 'C9'], ['audit', 'A9']] as const) {
    test(`the ${step === 'curator' ? 'curator' : 'auditor'}'s hand-back is neither filed nor forwarded`, async ($, on) => {
      const clock = mock.clock(on, { now: NOW })
      const w = world(on, {
        [`${BASE}/S1/state.json`]: PRIOR,
        [LOOP]: loopAt({ state: 'off', holds: ['question'], genStart: { kind: 'gen', sid: 'S1', step, agentId: id, spawnedAt: NOW } }),
      })
      w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id, status: 'completed', type: step === 'curator' ? 'memory-curator' : 'auditor' }]
      await $.session.start(ISTART)
      await $.prompt.submit({ text: notification(id, 'STATUS: done\nVERDICTS: 1'), origin: { kind: 'task-notification' }, wait: false })
      await clock.advance(1_000)
      await clock.advance(1_000)
      expect(w.sends.length).toBe(0)
      expect(entries(w, 'coder-end').length).toBe(0)
    })
  }
})

// 3.23 T6a2.c3: drift at the boundary (stamp, decline, once a session), the hook-context lines (once a session), the
// gated update (off by default and with PA_MOD_PA3_DIR set; on: the ask, then pa3_update.py).
const REL = '.claude/agents/coder-opus55.md'
const DRIFT_Q = `${REL} differs from the installed copy (edited in T3). Keep it as your version?`
/** `managed.py drift` lists REL until a stamp (or, `sticky` false, a decline) runs. */
const driftAnswer = (sticky: boolean) => {
  let listed = true
  return (argv: string[]): Ran => {
    const a = argv.slice(1).join(' ')
    if (a.startsWith('tools/managed.py stamp') || (!sticky && a.startsWith('tools/managed.py decline'))) {
      listed = false
    }
    return a.startsWith('tools/managed.py drift') ? ok(listed ? `${REL} | base 1a2b3c4d | 5e6f7a8b | edited in T3\n` : 'none\n') : defaultAnswer(argv)
  }
}
const driftOfW = (w: W): unknown => (JSON.parse(w.files.get(LOOP) ?? '{}') as { drift?: unknown }).drift ?? null
const UPDATE_LINE = 'PA3: update available: 3.2.1 (installed 3.2.0).'
const RENEWAL_LINE = 'PA3: the plan renewal day is not set; run `py tools/renewal.py set <day>`.'
const hookContext = async ($: { session: { append: (e: never) => Promise<unknown> } }, text: string): Promise<void> => {
  await $.session.append({
    message: { type: 'attachment', name: 'hook_additional_context', content: [{ type: 'text', text }] },
    door: 'hook-context', origin: { kind: 'hook', event: 'SessionStart' }, uuid: `u${String(text.length)}`,
  } as never)
}

describe('drift, hook-context and the gated update (T6a2.c3)', () => {
  test('drift stamp: one ask before the spawn; managed.py stamp, the commit; the record cleared; T1 spawns', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    w.answer = driftAnswer(true)
    const asked = askWith(on, w, ['stamp (Recommended)'])
    await $.session.start(START)
    for (let i = 0; i < 40 && experts(w).length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked).toEqual([{ q: DRIFT_Q, spawns: 0 }])
    expect(ran(w, 'tools/managed.py stamp')).toEqual([['py', 'tools/managed.py', 'stamp', REL]])
    expect(commits(w).map(c => c.slice(2))).toContainEqual(['router', `stamp ${REL}`, REL, '.claude/pa3-managed.json'])
    expect(w.files.get(entries(w, 'mod-decision')[0] ?? '')).toContain(`Drift: stamp ${REL}`)
    expect(driftOfW(w)).toBe(null)
    expect(experts(w).length).toBe(1)
  })

  test('drift decline: managed.py decline and its commit; a row still listed is not asked again this session', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    w.answer = driftAnswer(true)
    const asked = askWith(on, w, ['decline'])
    await $.session.start(START)
    for (let i = 0; i < 40 && experts(w).length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(ran(w, 'tools/managed.py decline')).toEqual([['py', 'tools/managed.py', 'decline', REL]])
    expect(commits(w).map(c => c.slice(2))).toContainEqual(['router', `decline ${REL}`, REL, '.claude/pa3-managed.json'])
    expect(experts(w).length).toBe(1)
    await clock.advance(40_000)
    expect(asked.length).toBe(1)
  })

  test('hook-context: the update and renewal lines each a notice once a session, filed mod/context; no ask in a trial (3.24 T6)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {}, { PA_MOD_PA3_DIR: '/pa3' })
    const asked = askWith(on, w, [])
    await $.session.start(START)
    await hookContext($ as never, `SessionStart hook additional context: ${UPDATE_LINE}\n${RENEWAL_LINE}`)
    await clock.advance(2_000)
    await hookContext($ as never, `${UPDATE_LINE}\n${RENEWAL_LINE}`)
    await clock.advance(4_000)
    const texts = notices(w).map(n => n.input.text)
    expect(texts.filter(t => t.startsWith('‹pa› PA3 says: PA3: update available'))).toEqual([
      '‹pa› PA3 says: PA3: update available: 3.2.1 (installed 3.2.0). Mention it to the developer once.'])
    expect(texts.filter(t => t.startsWith(`‹pa› ${RENEWAL_LINE}`)).length).toBe(1)
    expect(entries(w, 'mod-context').length).toBe(2)
    expect(asked.length).toBe(0)
    expect(ran(w, 'tools/pa3_update.py').length).toBe(0)
  })

  test('3.24 T6: a trial forces the ask with PA_MOD_UPDATE=1; a run that took no lock (exit 2) is reported from its output', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {}, { PA_MOD_UPDATE: '1', PA_MOD_PA3_DIR: '/pa3' })
    w.answer = argv => (argv[1] === 'tools/pa3_update.py' ? { exitCode: 2, stdout: 'pa3_update: no package clone at /c\n', stderr: '' } : defaultAnswer(argv))
    const asked = askWith(on, w, ['Update now (Recommended)'])
    await $.session.start(START)
    await hookContext($ as never, UPDATE_LINE)
    for (let i = 0; i < 40 && experts(w).length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked.length).toBe(1)
    expect(ran(w, 'tools/pa3_update.py').length).toBe(1)
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› the PA3 update did not run (exit 2): pa3_update: no package clone')).length).toBe(1)
  })

  test('3.24 T6 production (no PA_MOD_PA3_DIR): "Update PA3 now?" before the spawn, then pa3_update.py and the marker\'s report', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {})
    w.answer = argv => {
      if (argv[1] === 'tools/pa3_update.py') {
        w.files.set(`${BASE}/update.json`, JSON.stringify({ state: 'done', mode: 'update', rc: 0, at: (NOW + 1_000) / 1000,
          from: { phase: '3.23.0', git: 'aaaa1111cc' }, to: { phase: '3.24.0', git: 'bbbb2222dd' } }))
        w.files.set(`${BASE}/update.log`, 'pa3_update: 1/3: git pull\n      restart: needed\npa3_update: done\n')
        return ok('pa3_update: done\n')
      }
      return defaultAnswer(argv)
    }
    const asked = askWith(on, w, ['Update now (Recommended)'])
    await $.session.start(START)
    await hookContext($ as never, UPDATE_LINE)
    for (let i = 0; i < 40 && experts(w).length === 0; i += 1) {
      await clock.advance(2_000)
    }
    expect(asked).toEqual([{ q: `${UPDATE_LINE}\nUpdate PA3 now?`, spawns: 0 }])
    expect(ran(w, 'tools/pa3_update.py')).toEqual([['py', 'tools/pa3_update.py', '--detach']]) // 3.24 T7b
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 says:')).length).toBe(0)
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.23.0 (aaaa1111) to 3.24.0 (bbbb2222). Read /.claude/pa3/CHANGES.md'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain('(restart: needed)')
    expect(entries(w, 'mod-decision').some(p => (w.files.get(p) ?? '').includes('      restart: needed\npa3_update: done'))).toBe(true)
    expect(experts(w).length).toBe(1)
  })

  // 3.24 T6e: the run marker's aftermath (T6c): the hold, the conflicts' round, the mod's own files.
  const MARKER = `${BASE}/update.json`
  const MANIFEST = '/.claude/pa3/MANIFEST.json' // production: no HOME in the kit's env
  const marker = (o: Record<string, unknown>): string => JSON.stringify({ state: 'done', mode: 'update', rc: 0, at: NOW / 1000,
    from: { phase: '3.23.0', git: 'aaaa1111cc' }, to: { phase: '3.24.0', git: 'bbbb2222dd' }, ...o })
  const until = async (clock: { advance: (ms: number) => Promise<unknown> }, done: () => boolean): Promise<void> => {
    for (let i = 0; i < 40 && !done(); i += 1) {
      await clock.advance(2_000)
    }
  }

  for (const rc of [0, 1]) {
    test(`3.29.2: the start hook's run ends (exit ${String(rc)}): its release reads ${rc === 0 ? 'installed' : 'failed (3.32.5: was nothing)'}`, async ($, on) => {
      const RELM2 = `${BASE}/release.json`
      const clock = mock.clock(on, { now: NOW })
      const w = world(on, { [MARKER]: marker({ state: 'running' }), [RELM2]: '{"version":"3.24.0","kind":"feature update","state":"running"}' })
      await $.session.start(START)
      await clock.advance(4_000)
      expect(JSON.parse(w.files.get(RELM2) ?? '{}')).toEqual({ version: '3.24.0', kind: 'feature update', state: 'running' }) // kept at the start
      w.files.set(MARKER, marker({ rc, at: (NOW + 5_000) / 1000 }))
      w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
      await until(clock, () => (JSON.parse(w.files.get(RELM2) ?? '{}') as Record<string, unknown>)['state'] !== 'running')
      expect(JSON.parse(w.files.get(RELM2) ?? '{}')).toEqual({ version: '3.24.0', kind: 'feature update', state: rc === 0 ? 'installed' : 'failed' })
    })
  }

  // sb, 2026-11-05: the start hook's install showed only on the statusline; the chat's last word stayed the restart line.
  test('3.32.4: the start hook\'s install run: Claude is told it is installing, once a run; a run with no release, never', async ($, on) => {
    const RELM2 = `${BASE}/release.json`
    const INSTALLING = 'Installing Project Architect 3.24.0 now; please wait, I\'ll tell you when it\'s done.'
    const told = (w: W): number => notices(w).filter(n => n.input.text.includes(INSTALLING)).length
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [MARKER]: marker({ state: 'running' }), [RELM2]: '{"version":"3.24.0","kind":"feature update","state":"running"}' })
    await $.session.start(START)
    await clock.advance(10_000)
    expect(told(w)).toBe(1)
    await clock.advance(10_000)
    expect(told(w)).toBe(1) // once a run
    w.files.set(MARKER, marker({ state: 'running', mode: 'repair', at: (NOW + 20_000) / 1000 })) // a heal: no release named
    w.files.set(RELM2, '{}\n')
    await clock.advance(10_000)
    expect(told(w)).toBe(1)
  })

  test('3.24 T6e: a running marker holds T1; the done marker is reported once, then T1 spawns', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [MARKER]: marker({ state: 'running', mode: 'repair' }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(experts(w).length).toBe(0)
    w.files.set(MARKER, marker({ mode: 'repair', at: (NOW + 21_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: repair\npa3_update: done\n')
    await until(clock, () => experts(w).length > 0)
    const done = (): number => notices(w).filter(n => n.input.text.startsWith('‹pa› the PA3 reinstall finished')).length
    expect(done()).toBe(1)
    expect(experts(w).length).toBe(1)
    await clock.advance(20_000)
    expect(done()).toBe(1)
  })

  test('3.24 T6e: a conflict log spawns the UPGRADE expert; on its return pa3_update.py runs again; a clean marker, then T1', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [MARKER]: marker({}),
      [`${BASE}/update.log`]: 'pa3_update: 2/3: project\n  ! .claude/agents/coder-opus55.md kept yours; upstream staged\n',
    })
    w.answer = argv => {
      if (argv[1] === 'tools/pa3_update.py') {
        w.files.set(MARKER, marker({ at: NOW / 1000 + 600 }))
        w.files.set(`${BASE}/update.log`, 'pa3_update: 2/3: project\n  resolved: .claude/agents/coder-opus55.md (upstream)\n')
        return ok('pa3_update: done\n')
      }
      return defaultAnswer(argv)
    }
    await $.session.start(START)
    await until(clock, () => w.spawns.length > 0)
    expect(w.spawns.map(x => x.description)).toEqual(['UPGRADE PA3 conflicts'])
    expect(w.spawns[0]?.prompt).toContain('TASK: UPGRADE\n')
    expect(notices(w).filter(n => n.input.text.includes('an expert resolves them at the next boundary')).length).toBe(1)
    await clock.advance(20_000)
    expect([w.spawns.length, ran(w, 'tools/pa3_update.py').length]).toEqual([1, 0])
    w.list = w.list.map(a => (a.id === 'E1' ? { ...a, status: 'completed' } : a))
    await until(clock, () => experts(w).length > 0)
    expect(ran(w, 'tools/pa3_update.py')).toEqual([['py', 'tools/pa3_update.py', '--detach']]) // 3.24 T7b
    expect(w.spawns.map(x => x.description)).toEqual(['UPGRADE PA3 conflicts', 'T1 add function'])
    expect((JSON.parse(w.files.get(LOOP) ?? '{}') as { upgrade?: unknown }).upgrade).toBe(null)
    expect(notices(w).filter(n => n.input.text.includes(' PA3 updated from ')).length).toBe(2)
  })

  test('3.24 T6e: a changed MANIFEST mod/ entry gives the reload notice once and starts no T1', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [MARKER]: marker({ state: 'running' }),
      [MANIFEST]: JSON.stringify({ files: { 'mod/pa/hooks/loop.ts': 'a1', 'tools/x.py': 'b1' } }),
    })
    await $.session.start(START)
    await clock.advance(20_000)
    w.files.set(MANIFEST, JSON.stringify({ files: { 'mod/pa/hooks/loop.ts': 'a2', 'tools/x.py': 'b1' } }))
    w.files.set(MARKER, marker({ at: (NOW + 21_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(60_000)
    const reload = notices(w).filter(n => n.input.text.startsWith('‹pa› the PA3 mod\'s own files changed'))
    expect(reload.length).toBe(1)
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from')).length).toBe(1)
    expect(w.spawns.length).toBe(0)
  })

  // 3.32.1 T5 (#6): a work hold's release the update gate holds back is answered at once, once, then goes through by itself.
  const waits = (w: W): Notice[] => notices(w).filter(n => n.input.text.startsWith('waiting: question ('))
  test('3.32.1 T5: a running update: one waiting reply to the caller; the release goes through when the update ends', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 10 * 60_000), [MARKER]: marker({ state: 'running' }) })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(waits(w).map(n => [n.input.to, n.input.text.includes('it goes through when the update ends')])).toEqual([['P1', true]])
    expect([loopOf(w).holds, w.sends]).toEqual([['question'], []])
    w.files.set(MARKER, marker({ at: (NOW + 5_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await until(clock, () => w.sends.includes('NOTE: use ints'))
    expect([waits(w).length, loopOf(w).holds, w.sends.includes('NOTE: use ints')]).toEqual([1, [], true])
  })

  test('3.32.1 T5: the update check has not answered: one waiting reply naming the wait; the release goes through after', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 10 * 60_000), [`${ROOT}/${START_CHECK}`]: '{}' })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(waits(w).map(n => n.input.text.includes('it answers by itself within 2 minutes'))).toEqual([true])
    expect(w.sends).toEqual([])
    for (let i = 0; i < 65; i += 1) { // the check's wait is 120 s
      await clock.advance(2_000)
    }
    expect([waits(w).length, loopOf(w).holds, w.sends.includes('NOTE: use ints')]).toEqual([1, [], true])
  })

  test('3.32.1 T5: the new mod not loaded: one waiting reply asking the restart; it goes through once the new mod loads', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('question', NOW - 10 * 60_000), [MARKER]: marker({ state: 'running' }),
      [MANIFEST]: JSON.stringify({ files: { 'mod/pa/hooks/loop.ts': 'a1' } }) })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(20_000)
    w.files.set(MANIFEST, JSON.stringify({ files: { 'mod/pa/hooks/loop.ts': 'a2' } }))
    w.files.set(MARKER, marker({ at: (NOW + 21_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    expect(notices(w).filter(n => n.input.text === RELOAD_NOTICE).length).toBe(1)
    releaseReq(w, NOW + 30_000, { hold: 'question', note: 'use ints' })
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    expect(waits(w).map(n => n.input.text.includes('ask the developer to restart Claude Code (/exit, then claude --continue') &&
      !n.input.text.includes('/reload-plugins'))).toEqual([true])
    expect(w.sends).toEqual([])
    await $.session.start(START) // the reload: a new process, the new mod
    await until(clock, () => w.sends.includes('NOTE: use ints'))
    expect([waits(w).length, loopOf(w).holds]).toEqual([1, []])
  })

  test('3.32.1 T5: a talk release still goes through under the update gate, with no waiting reply', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: held('talk', NOW - 10 * 60_000), [MARKER]: marker({ state: 'running' }) })
    releaseReq(w, NOW, { hold: 'talk' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loopOf(w).holds, notices(w).filter(n => n.input.text.startsWith('waiting: ')).length]).toEqual([[], 0])
  })

  // 3.28.1 T14: the 8-hour release check (pa_ledger.py update-check), its picker, the clean stop, the statusline's marker.
  const CHECK = '/.claude/pa3/pa_ledger.py update-check'
  const STAMP = '/.claude/pa3/update.json'
  const RELM = `${BASE}/release.json`
  const RELEASE = 'PA3: update available: 2 commits (x) (version 3.28.1 -> 3.28.2). Say `update pa3` to run it.'
  const checkAnswer = (w: W) => (argv: string[]): Ran => {
    if (argv.slice(1, 3).join(' ') === CHECK) {
      return ok(`${JSON.stringify({ line: RELEASE, mode: 'auto', version: '3.28.2', kind: 'hotfix', title: 'Fixes' })}\n`)
    }
    if (argv[1] === 'tools/pa3_update.py') {
      w.files.set(MARKER, marker({ at: (NOW + 3_600_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.2', git: 'bbbb2222dd' } }))
      w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
      return ok('pa3_update: done\n')
    }
    return defaultAnswer(argv)
  }
  const relOf = (w: W): unknown => (JSON.parse(w.files.get(LOOP) ?? '{}') as { release?: unknown }).release ?? null
  const old = JSON.stringify({ checked_at: (NOW - 8 * 3_600_000) / 1000 })

  test('3.28.1 T14: no check within 8 hours of the last; the start empties the release marker (the restart loaded it)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [RELM]: '{"version":"3.28.2","kind":"hotfix","state":"install"}', [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    w.answer = checkAnswer(w)
    await $.session.start(START)
    await clock.advance(20_000)
    expect(ran(w, CHECK).length).toBe(0)
    expect(w.files.get(RELM)).toBe('{}\n')
  })

  test('3.28.2 T16: the start after an install says it is loaded, once; a plain start says nothing', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.2 is installed and loaded. Ready to continue work.**'
    const w = world(on, { [RELM]: '{"version":"3.28.2","kind":"hotfix","state":"install"}', '/.claude/pa3/VERSION': '3.28.2\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
    await $.session.start(START) // a plain start: the marker is empty
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
  })

  // sb, 2026-11-05: the check found 3.32.2, the run installed 3.32.3, the marker named 3.32.2: the restart said nothing.
  test('3.32.4: a marker naming an older release than the one installed: the start says the installed one loaded', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.3 is installed and loaded. Ready to continue work.**'
    const w = world(on, { [RELM]: '{"version":"3.28.2","kind":"hotfix","state":"installed"}', '/.claude/pa3/VERSION': '3.28.3\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
    expect(w.files.get(RELM)).toBe('{}\n')
  })

  test('3.32.4: a marker naming a newer release than the one installed: no loaded line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [RELM]: '{"version":"3.28.4","kind":"hotfix","state":"later"}', '/.claude/pa3/VERSION': '3.28.3\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes('is installed and loaded')).length).toBe(0)
  })

  // bfm, 2026-11-08: during 3.32.11's install the start's reset emptied the start hook's `running` marker, so no
  // restart said the release loaded, and Claude kept asking for one.
  test('3.32.12: no release marker, a reported run that installed this release: the start says it loaded, once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.3 is installed and loaded. Ready to continue work.**'
    const at = (NOW - 600_000) / 1000
    const w = world(on, { [RELM]: '{}\n', '/.claude/pa3/VERSION': '3.28.3\n',
      [MARKER]: marker({ at, from: { phase: '3.28.2', git: 'aaaa1111cc' }, to: { phase: '3.28.3', git: 'bbbb2222dd' } }),
      [LOOP]: JSON.stringify({ v: 1, state: 'off', holds: [], updateSeenAt: at * 1000 }),
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
    expect((JSON.parse(w.files.get(MARKER) ?? '{}') as { told?: unknown }).told).toBe(true)
    await $.session.start(START) // the next start: said once
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
  })

  test('3.32.12: a run whose report already says it loaded (a run while Claude Code was closed): no second line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.3 is installed and loaded. Ready to continue work.**'
    const w = world(on, { '/.claude/pa3/VERSION': '3.28.3\n', [`${BASE}/update.log`]: 'pa3_update: done\n',
      [MARKER]: marker({ at: (NOW - 60_000) / 1000, from: { phase: '3.28.2', git: 'aaaa1111cc' }, to: { phase: '3.28.3', git: 'bbbb2222dd' } }),
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
    await $.session.start(START)
    await clock.advance(20_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
  })

  test('3.32.12: a loaded release record is dropped, but a marker naming a newer release (installing) stays', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const running = '{"version":"3.28.4","kind":"hotfix","state":"running"}'
    const w = world(on, { [RELM]: running, '/.claude/pa3/VERSION': '3.28.3\n',
      [LOOP]: JSON.stringify({ v: 1, state: 'off', holds: [], release: { version: '3.28.2', kind: 'hotfix', asked: '', step: 'done' } }),
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    await $.session.start(START)
    await clock.advance(20_000)
    expect(relOf(w)).toBe(null)
    expect(w.files.get(RELM)).toBe(running)
  })

  test('3.29 T6: the first start offers the one-time .run/ cleanup when .run/ holds older work, once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/.run/P37/census/sites.jsonl`]: '{}\n', [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, ['Clean it up (Recommended)'])
    const offers = (): number => asked.filter(a => a.q.includes('one-time cleanup')).length
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toBe(1) // 3.29.3: the mod's own picker
    expect(w.files.has(`${ROOT}/.run/mod/run-review.json`)).toBe(true)
    expect(notices(w).some(n => n.input.text.includes('chose the one-time'))).toBe(true)
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toBe(1)
  })

  test('3.29.5: a project offered in plain text before the picker (3.29-3.29.2) gets the picker once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/.run/P37/census/sites.jsonl`]: '{}\n', [`${ROOT}/.run/mod/run-review.json`]: '{"offered":"3.29","at":1}\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, ['Not now'])
    const offers = (): number => asked.filter(a => a.q.includes('one-time cleanup')).length
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toBe(1)
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toBe(1)
  })

  test('3.29.2: the cleanup offer waits for the update check, then says it is part of the phase end and recommended', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/.run/P37/census/sites.jsonl`]: '{}\n', [`${ROOT}/${START_CHECK}`]: '{}',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, ['Not now'])
    const offers = (): string[] => asked.map(a => a.q).filter(t => t.includes('one-time cleanup'))
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toEqual([])
    w.files.set(`${ROOT}/${START_CHECK}`, JSON.stringify({ session: 'S1', at: (NOW + 21_000) / 1000, update: false, state: 'done' }))
    await clock.advance(4_000)
    expect(offers()).toHaveLength(1)
    expect(offers()[0]).toContain('cleanup step to every phase end')
    expect(offers()[0]).toContain('highly recommended')
    expect(notices(w).some(n => n.input.text.includes('put off the one-time'))).toBe(true)
  })

  test('3.30: lessons marked for the cookbook get the one-time banking picker once; yes tells Claude the exact task', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [`${ROOT}/phase-ends/current/tasks/T1.md`]: '# T1\n- generalizable: a pin falls to an in-place update\nGotchas: generalizable: x\n',
      [`${ROOT}/phase-ends/phase-3.39/tasks/T7.md`]: '- generalizable: an older one\n- harness: not counted\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, ['Add the banking task (Recommended)'])
    const offers = (): string[] => asked.map(a => a.q).filter(t => t.includes('brings back cookbook banking'))
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toHaveLength(1)
    expect(offers()[0]).toContain('mark 3 lessons for the cookbook (2 in the current phase)')
    expect(w.files.has(`${ROOT}/.run/mod/bank-backlog.json`)).toBe(true)
    const n = notices(w).map(x => x.input.text).filter(t => t.includes('one-time cookbook banking'))
    expect(n).toHaveLength(1)
    expect(n[0]).toContain('command `add-task --title "Bank the waiting cookbook lessons (one time, PA3 3.30)"')
    await $.session.start(START)
    await clock.advance(20_000)
    expect(offers()).toHaveLength(1)   // once per project
  })

  test('3.30: no lesson marked for the cookbook: no banking picker, and the marker says it was checked', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/phase-ends/current/tasks/T1.md`]: '- harness: only this\n',
      [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, [])
    await $.session.start(START)
    await clock.advance(20_000)
    expect(asked.filter(a => a.q.includes('cookbook banking'))).toEqual([])
    expect(w.files.get(`${ROOT}/.run/mod/bank-backlog.json`)).toContain('"asked":""')
  })

  test('3.29 T6: a .run/ holding only PA3\'s own entries gets no offer', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/.run/status.json`]: '{}\n', [STAMP]: JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 }) })
    const asked = askWith(on, w, [])
    await $.session.start(START)
    await clock.advance(20_000)
    expect(asked.filter(a => a.q.includes('one-time cleanup'))).toEqual([])
    expect(w.files.has(`${ROOT}/.run/mod/run-review.json`)).toBe(true)
  })

  test('3.28.1 T14: 8 hours on, a picker while the task runs; install: at the clean stop, then a toast and no new task', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [STAMP]: old })
    w.answer = checkAnswer(w)
    const asked = askWith(on, w, ['Install after the current task'])
    await $.session.start(START)
    await clock.advance(10_000)
    expect(experts(w).length).toBe(1)
    expect(ran(w, CHECK).length).toBe(1)
    expect(asked.map(a => a.q)).toEqual(['Project Architect 3.28.2, a hotfix (Fixes), is out. Install it? Tasks keep running ' +
      'while you decide.\nNot now: I ask again in 8 hours. When you\'re back at this machine, just tell Claude you\'re back ' +
      'and want the update — it installs after the current task. Or restart (/exit, then claude --continue) and it installs ' +
      'on the way in.'])
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 3.28.2, a hotfix, is out')).length).toBe(1)
    expect(w.files.get(RELM)).toBe('{"version":"3.28.2","kind":"hotfix","state":"install"}\n')
    expect(ran(w, 'tools/pa3_update.py').length).toBe(0) // the task runs on
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/pa3_update.py')).toEqual([['py', 'tools/pa3_update.py', '--detach']])
    expect(relOf(w)).toMatchObject({ version: '3.28.2', step: 'done' })
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.28.1'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain('"Project Architect 3.28.2, a hotfix (<the title of its CHANGES.md heading>), is installed."') // 3.28.2 T16
    expect(done[0]?.input.text).toContain('\n\n**To load it: /exit, then claude --continue (not /clear)**\n\n')
    expect([...w.files.keys()].filter(p => p.endsWith('-toast.json')).length + toastCauses(w).length).toBeGreaterThan(0)
    await clock.advance(30_000)
    expect(experts(w).length).toBe(1) // installed: no task starts on the old code
  })

  test('3.28.6 T19: the release check passes Claude and the journal the facts only, never the hook\'s pa3_update.py line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [STAMP]: old })
    const STEPS = 'PA3: update available: 2 commits (x) (version 3.28.4 -> 3.28.5). Upgrade: auto: before the first task run ' +
      '`py tools/pa3_update.py` (one command, never chained); then announce from /c/project-architect-3.0/CHANGES.md.'
    w.answer = (argv: string[]): Ran => argv.slice(1, 3).join(' ') === CHECK
      ? ok(`${JSON.stringify({ line: STEPS, mode: 'auto', version: '3.28.5', kind: 'hotfix', title: 'Fixes' })}\n`)
      : defaultAnswer(argv)
    askWith(on, w, [])
    await $.session.start(START)
    await clock.advance(10_000)
    expect(ran(w, CHECK).length).toBe(1)
    expect(notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 3.28.5, a hotfix, is out')).length).toBe(1)
    expect(notices(w).filter(n => n.input.text.includes('pa3_update.py')).length).toBe(0)
    const ctx = entries(w, 'mod-context').map(p => w.files.get(p) ?? '')
    expect(ctx.length).toBe(1)
    expect(ctx[0]).toContain('PA3 3.28.5, a hotfix (Fixes), is out')
    expect(ctx[0]).not.toContain('pa3_update.py')
  })

  test('3.28.1 T14: not now: the work goes on, no install; release({hold: "update"}) installs it after all', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [STAMP]: old })
    w.answer = checkAnswer(w)
    const asked = askWith(on, w, ['Not now (I\'m away or busy)'])
    await $.session.start(START)
    await clock.advance(10_000)
    expect(relOf(w)).toMatchObject({ version: '3.28.2', step: 'later' })
    expect(w.files.get(RELM)).toBe('{"version":"3.28.2","kind":"hotfix","state":"later"}\n')
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect([ran(w, 'tools/pa3_update.py').length, experts(w).length]).toEqual([0, 2]) // the work goes on
    w.files.set(STAMP, old) // 3.32.5: a later check finds the same version: asked again after "Not now" (was: not asked)
    await $.session.start(START)
    await clock.advance(10_000)
    expect([ran(w, CHECK).length, asked.length]).toEqual([2, 2])
    releaseReq(w, clock.now(), { hold: 'update' })
    await clock.advance(10_000)
    expect([ran(w, 'tools/pa3_update.py').length, relOf(w)]).toEqual([0, expect.objectContaining({ step: 'install' })])
    await $.turn.complete(complete(`E${String(w.spawns.length)}`,'STATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000)
    await clock.advance(4_000)
    expect(ran(w, 'tools/pa3_update.py').length).toBe(1)
  })

  // 3.28.2 T17: a paused task is a clean stop; release update with the install chosen is accepted; the hook's install.
  const chosen = { version: '3.28.2', kind: 'hotfix', asked: 'a1', step: 'install' }
  const fresh = JSON.stringify({ checked_at: (NOW - 7 * 3_600_000) / 1000 })

  test('3.32.5: the 8-hour check that cannot reach PA3\'s source says so, with the manual update\'s command', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [STAMP]: old })
    w.answer = (argv: string[]): Ran => (argv.slice(1, 3).join(' ') === CHECK ? ok('{"offline": true}\n') : defaultAnswer(argv))
    askWith(on, w, [])
    await $.session.start(START)
    await clock.advance(10_000)
    expect(ran(w, CHECK).length).toBe(1)
    expect(notices(w).filter(n => n.input.text === offlineNotice('py tools/pa3_update.py', 'check')).length).toBe(1)
    expect(relOf(w)).toBeNull() // nothing was found, so nothing is asked
  })

  // his concern, 2026-11-05: a user whose update fails must hear it in the session, with the command, and no work runs
  test('3.32.5: a failed update: manual update required with the command; held until skip-update; a run that goes through ends it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [MARKER]: marker({ state: 'running' }), [RELM]: '{"version":"3.24.0","kind":"feature update","state":"running"}', [STAMP]: fresh })
    const relm = (): Record<string, unknown> => JSON.parse(w.files.get(RELM) ?? '{}') as Record<string, unknown>
    const told = (): Notice[] => notices(w).filter(n => n.input.text.includes('**Automatic update failed: manual update required.**'))
    await $.session.start(START)
    await clock.advance(4_000)
    w.files.set(MARKER, marker({ rc: 1, at: (NOW + 5_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: 1/3: git pull --ff-only failed\n')
    await until(clock, () => relm()['state'] === 'failed')
    expect(relm()).toEqual({ version: '3.24.0', kind: 'feature update', state: 'failed' })
    expect(told().length).toBe(1)
    expect(told()[0]?.input.text).toContain('`! py tools/pa3_update.py`')
    expect(told()[0]?.input.text).toContain('git pull --ff-only failed')
    await clock.advance(20_000)
    expect([experts(w).length, told().length]).toEqual([0, 1]) // held, and told once
    releaseReq(w, clock.now(), { hold: 'skip-update' })
    await clock.advance(20_000)
    expect(experts(w).length).toBe(1) // the developer goes on without it, for this session
    expect(relm()['state']).toBe('failed') // the red line stays until PA3 is updated
    w.files.set(MARKER, marker({ at: (NOW + 60_000) / 1000 })) // then an update goes through (a manual run)
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await until(clock, () => relm()['state'] !== 'failed')
    expect(relm()).toEqual({ version: '3.24.0', kind: 'feature update', state: 'installed' })
  })

  test('3.32.5: a failure from before holds the next start, told once; release({hold: "update"}) tries the install again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [RELM]: '{"version":"3.24.0","kind":"feature update","state":"failed"}', [STAMP]: fresh })
    w.answer = (argv: string[]): Ran => {
      if (argv[1] === 'tools/pa3_update.py') { // detached: the run goes on
        w.files.set(MARKER, marker({ state: 'running', at: (NOW + 20_000) / 1000 }))
        return ok('pa3_update: started\n')
      }
      return defaultAnswer(argv)
    }
    await $.session.start(START)
    await clock.advance(10_000)
    expect(experts(w).length).toBe(0)
    expect(notices(w).filter(n => n.input.text.includes('the last automatic PA3 update to 3.24.0 failed')).length).toBe(1)
    expect(w.files.get(RELM)).toBe('{"version":"3.24.0","kind":"feature update","state":"failed"}') // kept at the start
    releaseReq(w, clock.now(), { hold: 'update' })
    await clock.advance(10_000)
    expect(ran(w, 'tools/pa3_update.py')).toEqual([['py', 'tools/pa3_update.py', '--detach']])
    expect(relOf(w)).toMatchObject({ version: '3.24.0', step: 'running' })
  })
  test('3.28.2 T17: a stopped task with no agent working is a clean stop; a working agent still waits', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped', 'talk'], release: chosen }), [STAMP]: fresh })
    w.answer = checkAnswer(w)
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(10_000)
    expect(ran(w, 'tools/pa3_update.py').length).toBe(0) // its expert is at work
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    await clock.advance(10_000)
    expect(ran(w, 'tools/pa3_update.py')).toEqual([['py', 'tools/pa3_update.py', '--detach']])
  })

  test('3.28.5 T18: the run starts: installing on the marker, Claude told once; it ends with exit 0: installed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const INSTALLING = 'Installing Project Architect 3.28.2 now; please wait, I\'ll tell you when it\'s done.'
    const w = world(on, { [LOOP]: running({ holds: ['stopped', 'talk'], release: chosen }), [STAMP]: fresh })
    w.answer = (argv: string[]): Ran => {
      if (argv[1] === 'tools/pa3_update.py') { // detached: the run goes on
        w.files.set(MARKER, marker({ state: 'running', at: (NOW + 1_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.2', git: 'bbbb2222dd' } }))
        return ok('pa3_update: started\n')
      }
      return defaultAnswer(argv)
    }
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(10_000)
    expect(ran(w, 'tools/pa3_update.py').length).toBe(1)
    expect(w.files.get(RELM)).toBe('{"version":"3.28.2","kind":"hotfix","state":"running"}\n')
    await clock.advance(30_000)
    expect(notices(w).filter(n => n.input.text.includes(INSTALLING)).length).toBe(1)
    w.files.set(MARKER, marker({ at: (NOW + 40_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.2', git: 'bbbb2222dd' } }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    expect(w.files.get(RELM)).toBe('{"version":"3.28.2","kind":"hotfix","state":"installed"}\n')
    expect(notices(w).filter(n => n.input.text.includes(INSTALLING)).length).toBe(1)
  })

  test('3.32.4: the run installs a newer release than the one asked about: the toast and the marker name it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped', 'talk'], release: chosen }), [STAMP]: fresh })
    w.answer = (argv: string[]): Ran => {
      if (argv[1] === 'tools/pa3_update.py') {
        w.files.set(MARKER, marker({ state: 'running', at: (NOW + 1_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.3', git: 'cccc3333ee' } }))
        return ok('pa3_update: started\n')
      }
      return defaultAnswer(argv)
    }
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(40_000)
    w.files.set(MARKER, marker({ at: (NOW + 40_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.3', git: 'cccc3333ee' } }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    expect(w.files.get(RELM)).toBe('{"version":"3.28.3","kind":"hotfix","state":"installed"}\n')
    expect(toastReqs(w)).toContainEqual({ cause: 'input', body: 'Project Architect 3.28.3 hotfix installed · /exit, then claude --continue (not /clear)' })
  })

  test('3.28.2 T17: release({hold: "update"}) with the install chosen is accepted; with no newer release, refused so', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ release: chosen }), [STAMP]: fresh })
    await $.session.start(START)
    releaseReq(w, clock.now(), { hold: 'update' })
    await clock.advance(10_000)
    expect(notices(w).map(n => n.input.text).filter(t => t.includes(': update') || t.includes('PA3 release'))).toEqual([
      'released: update (the install of PA3 3.28.2 is already chosen; it runs at the clean stop, now if no expert or coder is working)'])
    w.files.set(LOOP, JSON.stringify({ ...(JSON.parse(w.files.get(LOOP) ?? '{}') as object), release: null }))
    releaseReq(w, clock.now(), { hold: 'update' })
    await clock.advance(10_000)
    expect(notices(w).map(n => n.input.text).filter(t => t.startsWith('refused:'))).toEqual(['refused: there is no newer PA3 release'])
  })

  test('3.28.2 T17: the session start\'s run installs the chosen release: the next start says loaded, once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.2 is installed and loaded. Ready to continue work.**'
    const w = world(on, { [LOOP]: loopAt({ release: chosen }), [RELM]: '{"version":"3.28.2","kind":"hotfix","state":"install"}',
      [MARKER]: marker({ state: 'running' }), '/.claude/pa3/VERSION': '3.28.1\n', [STAMP]: fresh })
    await $.session.start(START)
    await clock.advance(10_000)
    expect([notices(w).filter(n => n.input.text.includes(LOADED)).length, ran(w, 'tools/pa3_update.py').length]).toEqual([0, 0])
    w.files.set('/.claude/pa3/VERSION', '3.28.2\n')
    w.files.set(MARKER, marker({ at: (NOW + 11_000) / 1000 }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    expect(relOf(w)).toMatchObject({ step: 'done' })
    expect(w.files.get(RELM)).toBe('{"version":"3.28.2","kind":"hotfix","state":"install"}\n')
    await $.session.start(START)
    await clock.advance(10_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
    await $.session.start(START)
    await clock.advance(10_000)
    expect(notices(w).filter(n => n.input.text.includes(LOADED)).length).toBe(1)
  })

  test('3.28.2 T17: the hook\'s run starts, a restart loads the new mod, then the run ends: loaded, no restart line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.28.2 is installed and loaded. Ready to continue work.**'
    const w = world(on, { [LOOP]: loopAt({ release: chosen }), [RELM]: '{"version":"3.28.2","kind":"hotfix","state":"install"}',
      [MARKER]: marker({ state: 'running' }), '/.claude/pa3/VERSION': '3.28.1\n', [STAMP]: fresh })
    await $.session.start(START) // the old mod; the session-start hook's run is going
    await clock.advance(10_000)
    w.files.set('/.claude/pa3/VERSION', '3.28.2\n') // the run's install step is done; its project step goes on
    await $.session.start(START) // the restart loads the new mod
    await clock.advance(10_000)
    expect([relOf(w), w.files.get(RELM)]).toEqual([null, '{}\n'])
    w.files.set(MARKER, marker({ at: (NOW + 21_000) / 1000, from: { phase: '3.28.1', git: 'aaaa1111cc' }, to: { phase: '3.28.2', git: 'bbbb2222dd' } }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.28.1'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain(LOADED)
    expect(done[0]?.input.text).not.toContain('To load it')
    expect(ran(w, 'tools/pa3_update.py').length).toBe(0)
  })

  // 3.30.2: the build the install stamps in the mod's own code (build.ts; a test's PA_MOD_BUILD) says what this process
  // runs; VERSION may already name the release the start hook's background install is putting in place.
  test('3.30.2: VERSION rewritten before this start read it: the old build claims nothing loaded; the end asks the restart', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.30.2 is installed and loaded. Ready to continue work.**'
    const RUNNING = '{"version":"3.30.2","kind":"hotfix","state":"running"}'
    const w = world(on, { [RELM]: RUNNING, [MARKER]: marker({ state: 'running' }), '/.claude/pa3/VERSION': 'version: 3.30.2\ngit: bbbb2222\n',
      [STAMP]: fresh }, { PA_MOD_BUILD: '{"version":"3.30.1","git":"aaaa1111"}' })
    await $.session.start(START) // bfm 21:14: the hook's install wrote VERSION a second before this start read it
    await clock.advance(10_000)
    expect([notices(w).filter(n => n.input.text.includes(LOADED)).length, w.files.get(RELM)]).toEqual([0, RUNNING])
    w.files.set(MARKER, marker({ at: (NOW + 11_000) / 1000, from: { phase: '3.30.1', git: 'aaaa1111' }, to: { phase: '3.30.2', git: 'bbbb2222' } }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.30.1'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain('To load it: /exit, then claude --continue')
    expect(done[0]?.input.text).not.toContain('already runs')
    expect(notices(w).filter(n => n.input.text === RELOAD_NOTICE).length).toBe(1)
  })

  test('3.30.2: a start whose own build is the installed one says loaded, once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const LOADED = '**Project Architect 3.30.2 is installed and loaded. Ready to continue work.**'
    const w = world(on, { [RELM]: '{"version":"3.30.2","kind":"hotfix","state":"install"}', '/.claude/pa3/VERSION': 'version: 3.30.2\ngit: bbbb2222\n',
      [STAMP]: fresh }, { PA_MOD_BUILD: '{"version":"3.30.2","git":"bbbb2222"}' })
    await $.session.start(START)
    await clock.advance(10_000)
    expect([notices(w).filter(n => n.input.text.includes(LOADED)).length, w.files.get(RELM)]).toEqual([1, '{}\n'])
  })

  test('3.30.2: a rebuild of the same version is new code too: its end asks the restart', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [MARKER]: marker({ state: 'running' }), '/.claude/pa3/VERSION': 'version: 3.30.2\ngit: bbbb2222\n', [STAMP]: fresh },
      { PA_MOD_BUILD: '{"version":"3.30.2","git":"aaaa1111"}' })
    await $.session.start(START)
    await clock.advance(10_000)
    w.files.set(MARKER, marker({ at: (NOW + 11_000) / 1000, from: { phase: '3.30.2', git: 'aaaa1111' }, to: { phase: '3.30.2', git: 'bbbb2222' } }))
    w.files.set(`${BASE}/update.log`, 'pa3_update: done\n')
    await clock.advance(10_000)
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.30.2'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain('To load it: /exit, then claude --continue')
  })

  test('3.30.3: a run that ended before this start: the start loaded it, so its restart: needed asks nothing', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [MARKER]: marker({ at: (NOW - 60_000) / 1000, from: { phase: '3.30.2', git: 'aaaa1111' }, to: { phase: '3.30.3', git: 'bbbb2222' } }),
      [`${BASE}/update.log`]: 'pa3_update: 1/3: git pull\n      restart: needed\npa3_update: done\n',
      '/.claude/pa3/VERSION': 'version: 3.30.3\ngit: bbbb2222\n', [STAMP]: fresh }, { PA_MOD_BUILD: '{"version":"3.30.3","git":"bbbb2222"}' })
    await $.session.start(START) // TerrainDiffusion: its update was run by hand, then Claude Code started
    await clock.advance(10_000)
    const done = notices(w).filter(n => n.input.text.startsWith('‹pa› PA3 updated from 3.30.2'))
    expect(done.length).toBe(1)
    expect(done[0]?.input.text).toContain('**Project Architect 3.30.3 is installed and loaded. Ready to continue work.**')
    expect(done[0]?.input.text).not.toContain('restart: needed')
    expect(done[0]?.input.text).not.toContain('To load it')
  })

  test('3.30.2: a start whose update check got no answer says so, once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${START_CHECK}`]: JSON.stringify({ session: 'S1', at: NOW / 1000, update: false, state: 'offline' }) })
    await $.session.start(START)
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(notices(w).filter(n => n.input.text === offlineNotice('py tools/pa3_update.py')).length).toBe(1)
    expect(notices(w).some(n => n.input.text.includes('`! py tools/pa3_update.py`'))).toBe(true) // 3.32.5: the command
    expect(w.spawns.length).toBe(1) // still an answer: work goes on
  })
})

// T6b: the phase close. The plan names phase 1.1 of generation 1; 3.25 T2: the mod closes it, no agent.
const PE_PLAN = '# Phase 1.1 — Core        (implements GENERATION_PLAN.md phase 1.1)\n'
const genPlan = (other: string): string => `# Generation 1\n- 1.1 Core | milestone: m | status: open\n${other}`
type R = { exitCode: number; stdout: string; stderr: string }
const NEXT_DONE = ok('{"kind": "none", "reason": "done", "waiting_on": []}\n')
const T4 = { ...T1, id: 'T4', title: 'sub function' }
const closeLoop = (o: Record<string, unknown>): string => JSON.stringify({
  v: 1, ownerSid: 'S1', heartbeatAt: NOW, state: 'boundary', holds: [], task: null, queuedEdits: [], chainStep: '', ...o,
})
/** next at NONE-done unless `over` answers. */
const closeAnswer = (over: (a: string) => R | undefined = () => undefined) => (argv: string[]): R => {
  const a = argv.slice(1).join(' ')
  return over(a) ?? (a.startsWith('tools/plan_edit.py next') ? NEXT_DONE : defaultAnswer(argv))
}
const ARCHIVE_OUT = 'archived: phase-ends/current -> phase-ends/phase-1.1\nstage: .claude-state/ledger/phase-1.1.jsonl\n' +
  'RUN: python tools/plan_edit.py gen-close 1.1 --by router\nRUN: python tools/genend_index.py assemble 1\n'
/** The chain's runs: the ledger, phaseend_index, commits, the RUN: scripts and status clear, in order. */
const chainRuns = (w: W): string[] => w.runs.filter(r => /pa_ledger|phaseend_index|commit_task|gen-close|genend_index/.test(r.join(' ')) ||
  r.slice(1, 3).join(' ') === 'tools/status.py clear').map(r => r.slice(1).join(' '))
/** The router's feed records in `in/` (start and closed lines, `-8nnnnn` names), in name order (3.23 T13d.c4). */
const feedRecs = (w: W): Record<string, unknown>[] => [...w.files.keys()]
  .filter(p => p.startsWith(`${BASE}/S1/feed/in/`) && /-8\d{5}\.json$/.test(p)).sort()
  .map(p => JSON.parse(w.files.get(p) ?? '{}') as Record<string, unknown>)
/** The loop's toast requests' inputs (3.23 T13d.c4). */
const toastReqs = (w: W): unknown[] => [...w.files.keys()].filter(p => p.startsWith(`${INBOX}/`) && p.endsWith('-toast.json')).sort()
  .map(p => (JSON.parse(w.files.get(p) ?? '{}') as { input?: unknown }).input)
type Loop5 = Loop & { closeRequested?: boolean; chainStep: string; chainRuns?: string[]; queuedEdits: unknown[] }
const loop5 = (w: W): Loop5 => JSON.parse(w.files.get(LOOP) ?? '{}') as Loop5

describe('the phase close (T6b)', () => {
  test('plan end: held(close) and the close question to Claude, once, cause input (no toast at the notice)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n') })
    w.answer = closeAnswer()
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop5(w).holds).toEqual(['close'])
    const n = notices(w).filter(x => x.input.text.includes('has no task left'))
    expect(n.map(x => x.input)).toEqual([{ text: '‹pa› Plan end: phase 1.1 has no task left. No task left a summary. ' +
      'Report the phase to the developer, then ask "Phase 1.1: every task is done. Is the milestone met: does what' +
      ' the phase built run?" with the answers "Yes, close the phase (recommended)" and "Not yet: <what is missing>",' +
      ' and wait. What is missing goes through plan_edit; on yes, call release({hold: "close", note: "<your report>"}).', toast: 'input' }])
    expect(toastCauses(w)).toEqual([])
    expect(w.spawns.length).toBe(0)
  })

  test('3.30: plan end with external coder runs since the last review: the close notice asks for the auditor\'s review', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n') })
    w.answer = closeAnswer(a => (a === 'tools/pa_ext.py runs --count' ? ok('3\n') : undefined))
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/pa_ext.py runs --count').length).toBe(1)
    const n = notices(w).filter(x => x.input.text.includes('has no task left'))
    expect(n.length).toBe(1)
    expect(n[0]?.input.text).toContain(' 3 external coder runs finished since the last external review: once the close is' +
      ' released, start the auditor in the background with `EXTERNAL REVIEW · PHASE 1.1`')
  })

  test('held(close): a plan_edit that adds a task lands at once, inbox-consume runs, close pops and the task runs', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '' }) })
    let added = false
    w.answer = closeAnswer(a => {
      if (a.startsWith('tools/plan_edit.py add-task')) {
        added = true
      }
      return added && a.startsWith('tools/plan_edit.py next') ? ok(`${JSON.stringify({ kind: 'task', task: T4, promoted: null })}\n`) : undefined
    })
    const id = `${String(NOW)}-1-plan_edit`
    w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'plan_edit', caller: 'P1',
      input: { command: 'tools/plan_edit.py add-task T4 "sub function"', why: 'the developer adds sub' }, at: NOW }))
    await $.session.start(START)
    for (let i = 0; i < 6 && w.spawns.length === 0; i += 1) {
      await clock.advance(2_000)
    }
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w).map(n => n.input.text).filter(t => t.startsWith('queued: '))).toEqual([])
    expect(entries(w, 'mod-prompt').map(p => w.files.get(p) ?? '').some(t => t.includes('queued: tools/plan_edit.py add-task T4'))).toBe(true) // journaled
    expect(ran(w, 'tools/plan_edit.py add-task').length).toBe(1)
    expect(ran(w, 'tools/status.py inbox-consume').length).toBe(1)
    expect(loop5(w).holds).toEqual([])
    expect(w.spawns.map(s => s.description)).toEqual(['T4 sub function'])
  })

  // 3.25 T2: the close with no closer and no lint: release(close)'s note is the recap; the mod runs the chain itself.
  test('release(close) with a note: RECAP.md with ## Recap and ## Generation Recap, no spawn, the chain in order without lint', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan(''),
      [`${ROOT}/.run/status.json`]: '{"router_session": "RS1"}', [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '',
        summaries: { T1: { title: 'add function', summary: 'It adds.' } } }) },
    { PA_MOD_PA3_DIR: '/pa3' })
    w.answer = closeAnswer(a => {
      if (a.startsWith('tools/status.py inbox-consume')) {
        return ok('- add a CHANGES line\n')
      }
      if (a.startsWith('tools/phaseend_index.py archive')) {
        w.files.delete(`${ROOT}/${PLAN}`)
        return ok(ARCHIVE_OUT)
      }
      return undefined
    })
    releaseReq(w, NOW, { hold: 'close', note: 'Phase 1.1 built add.\nIt runs.' })
    await $.session.start(START)
    for (let i = 0; i < 6 && loop5(w).state !== 'off'; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.files.get(`${ROOT}/phase-ends/current/RECAP.md`))
      .toBe('## Recap\nPhase 1.1 built add.\nIt runs.\n\n## Generation Recap\nPhase 1.1 built add.\nIt runs.\n')
    expect([w.spawns.length, ran(w, 'tools/status.py set').length]).toEqual([0, 0])
    expect(notices(w).some(n => n.input.text.includes('INBOX items before the phase closes') && n.input.text.includes('add a CHANGES line'))).toBe(true)
    expect(chainRuns(w)).toEqual([
      // 3.23 T13d.c6a: the loop's owner S1 wins over status.json's router_session RS1; 3.24 T10b: detached, rows only
      '/pa3/pa_ledger.py recalc --detach --rows-only --from turns --session S1 --done-file /r/.run/mod/recalc.done',
      'tools/phaseend_index.py assemble 1.1',
      'tools/commit_task.sh phase-end PhaseEnd 1.1 phase-ends/PhaseEnd_Phase1.1.md',
      'tools/phaseend_index.py archive',
      'tools/plan_edit.py gen-close 1.1 --by router', 'tools/genend_index.py assemble 1',
      'tools/commit_task.sh phase-end Phase 1.1 archived phase-ends GENERATION_PLAN.md .claude-state/ledger/phase-1.1.jsonl',
      'tools/status.py clear',
      'tools/phaseend_index.py scratch 1.1', // 3.29 T5: the phase's temp folder
      'tools/commit_task.sh phase-end Phase 1.1 closed', // 3.24 T13f: the closed journal entry lands in a commit
    ])
    expect(entries(w, 'mod-close').some(p => (w.files.get(p) ?? '').includes('Phase 1.1 close started (generation end)'))).toBe(true)
    const lp = loop5(w)
    expect([lp.state, lp.task, lp.chainStep, lp.chainRuns, lp.closeRequested]).toEqual(['off', null, '', undefined, false])
    // T13b: the summaries and chainGenEnd cleared at the close; the note dropped
    expect([(lp as { summaries?: unknown }).summaries, (lp as { chainGenEnd?: unknown }).chainGenEnd, (lp as { closeRecap?: unknown }).closeRecap])
      .toEqual([undefined, undefined, undefined])
    // 3.23 T13d.c4: no recap notice to Claude; one feed line from the mod, and the generation end's toast at once
    const line = 'Phase 1.1 closed. Push when ready. The generation is complete; the next one is yours to start.'
    expect(notices(w).filter(n => n.input.text.includes('closed'))).toEqual([])
    expect(feedRecs(w).map(r => [r['agent'], r['type'], r['desc'], r['text']])).toEqual([['mod', 'pa', '', line]])
    expect(toastReqs(w)).toEqual([{ cause: 'input', body: line }])
    expect(entries(w, 'mod-close').some(p => (w.files.get(p) ?? '').includes(line))).toBe(true)
    expect(lp.holds).toEqual(['generation']) // T13b.c3: parked; no plan hold, no gen.start
    expect((lp as { genParkSid?: unknown }).genParkSid).toBe('S1') // 3.24 T13A: the parking session
    expect((lp as { genStart?: unknown }).genStart).toBeUndefined()
  })

  test('release(close) with no note, not a generation end: RECAP.md is ## Recap and -', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '' }) })
    w.answer = closeAnswer()
    releaseReq(w, NOW, { hold: 'close' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.files.get(`${ROOT}/phase-ends/current/RECAP.md`)).toBe('## Recap\n-\n')
    expect(w.spawns.length).toBe(0)
  })

  test('release(close) with a task left: the close is called off, the note dropped, the task runs', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '' }) })
    w.answer = defaultAnswer // next names T1
    releaseReq(w, NOW, { hold: 'close', note: 'Phase 1.1 built add.' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.spawns.map(s => s.description)).toEqual(['T1 add function'])
    expect(loop5(w).closeRequested).toBe(false)
    expect((loop5(w) as { closeRecap?: unknown }).closeRecap).toBeUndefined()
    expect(w.files.has(`${ROOT}/phase-ends/current/RECAP.md`)).toBe(false)
  })

  test('3.23 T13d.c4: not a generation end: the closed line alone, no toast, no notice', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [LOOP]: closeLoop({ state: 'closing', chainStep: 'archived', chainPhase: '1.1' }) })
    w.files.delete(`${ROOT}/${PLAN}`)
    w.answer = closeAnswer()
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loop5(w).state).toBe('off')
    expect(feedRecs(w).map(r => r['text'])).toEqual(['Phase 1.1 closed. Push when ready.'])
    expect([toastReqs(w), notices(w).filter(n => n.input.text.includes('closed'))]).toEqual([[], []])
    // 3.24 T13f: the closed entry is written, then committed: the close's last run is that commit
    expect(entries(w, 'mod-close').some(p => (w.files.get(p) ?? '').includes('Phase 1.1 closed. Push when ready.'))).toBe(true)
    expect(chainRuns(w).at(-1)).toBe('tools/commit_task.sh phase-end Phase 1.1 closed')
  })

  test('3.23 T13d.c4: the recalc at the close\'s start; the chain waits at most 10 s for its done-file, then goes on', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [`${ROOT}/.run/status.json`]: '{"router_session": "RS1"}', [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '' }) },
    { PA_MOD_PA3_DIR: '/pa3' })
    w.recalcDone = false // 3.24 T10b: the detached recalc still runs; its launch returned at once
    w.answer = closeAnswer()
    releaseReq(w, NOW, { hold: 'close' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(chainRuns(w)).toEqual(['/pa3/pa_ledger.py recalc --detach --rows-only --from turns --session S1 --done-file /r/.run/mod/recalc.done'])
    for (let i = 0; i < 8; i += 1) {
      await clock.advance(2_000)
    }
    expect(chainRuns(w).filter(r => r.includes('pa_ledger')).length).toBe(1)
    expect(chainRuns(w)).toContain('tools/phaseend_index.py assemble 1.1')
    expect(feedRecs(w).map(r => r['text'])).toContain('Phase 1.1 closed. Push when ready.') // archived
    const closeText = (): string => entries(w, 'mod-close').map(p => w.files.get(p) ?? '').join('\n')
    expect(closeText()).toContain('Ledger recalc still running after the chain\'s 10 s wait')
  })

  test('3.23 T13d.c4: a recalc that ended before its step: the chain does not run it again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [`${ROOT}/.run/status.json`]: '{"router_session": "RS1"}', [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '' }) },
    { PA_MOD_PA3_DIR: '/pa3' })
    w.answer = closeAnswer()
    releaseReq(w, NOW, { hold: 'close' })
    await $.session.start(START)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(2_000)
    }
    expect(feedRecs(w).map(r => r['text'])).toContain('Phase 1.1 closed. Push when ready.') // archived
    expect(chainRuns(w).filter(r => r.includes('pa_ledger')).length).toBe(1)
    expect(entries(w, 'mod-close').map(p => w.files.get(p) ?? '').join('\n')).toContain('Ledger recalc (sessions S1): exit 0 after 0.4 s.')
  })

  test('3.24 T10b: every session that owned the phase is recalced; the close resets the list to its own session', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [LOOP]: closeLoop({ holds: ['close'], closeAsked: true, closeInbox: '', phaseSids: ['A0', 'B0'] }) },
    { PA_MOD_PA3_DIR: '/pa3' })
    w.answer = closeAnswer()
    releaseReq(w, NOW, { hold: 'close' })
    await $.session.start(START)
    for (let i = 0; i < 6; i += 1) {
      await clock.advance(2_000)
    }
    expect(chainRuns(w)[0]).toBe('/pa3/pa_ledger.py recalc --detach --rows-only --from turns --session A0 --session B0 --session S1 ' +
      '--done-file /r/.run/mod/recalc.done')
    expect(entries(w, 'mod-close').map(p => w.files.get(p) ?? '').join('\n')).toContain('Ledger recalc (sessions A0, B0, S1): exit 0 after 0.4 s.')
    expect((loop5(w) as { phaseSids?: unknown }).phaseSids).toEqual(['S1'])
  })

  // T13b.c3: the generation-end park: no gen.start while held; release(generation) or a fresh session starts it; a resume keeps it.
  test('held(generation): no gen.start while held, however many ticks', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation'] }) })
    w.answer = launchDoc({ curate: CURATE })
    await $.session.start(START)
    for (let i = 0; i < 5; i += 1) {
      await clock.advance(2_000)
    }
    expect([loopOf(w).holds, genOf(w), w.spawns.length]).toEqual([['generation'], null, 0])
  })

  // 3.24 T13A (decision 1): the next generation starts only in a new session or after /clear
  test('release(generation) is refused: the park stays, no gen.start; the refusal says a new session or /clear', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation'], genStart: { kind: 'gen', sid: 'S1', step: 'done' } }) })
    w.answer = launchDoc({ curate: CURATE })
    releaseReq(w, NOW, { hold: 'generation' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loopOf(w).holds, genOf(w)?.step]).toEqual([['generation'], 'done'])
    expect(notices(w).map(n => n.input.text)).toEqual(
      ['refused: the next generation starts in a new session or after /clear, not in the session that closed this one'])
  })

  test('release(generation) at a new session\'s start (session held) lifts both and begins gen.start', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation', 'session'], genParkSid: 'S0' }) })
    w.answer = launchDoc({ curate: CURATE })
    releaseReq(w, NOW, { hold: 'generation' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect([loopOf(w).holds, genOf(w)?.kind, genOf(w)?.sid]).toEqual([[], 'gen', 'S1'])
  })

  test('release(session) in the session that parked is refused too (genParkSid)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation', 'session'], genParkSid: 'S1' }) })
    w.answer = launchDoc({ curate: CURATE })
    releaseReq(w, NOW, { hold: 'session' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect([loopOf(w).holds, genOf(w)]).toEqual([['generation', 'session'], null])
  })

  // 3.23 T13d.c11: a session start (startup, resume or clear) keeps the park under held(session); only the developer's
  // message and Claude's release({hold: 'session'}) begin the next generation's start (was T13b.c3: startup/clear lifted it).
  for (const source of ['startup', 'resume', 'clear'] as const) {
    test(`SessionStart ${source} holds the generation park in session; the release begins gen.start`, async ($, on) => {
      const clock = mock.clock(on, { now: NOW })
      const w = noPlanWorld(on, { [LOOP]: loopAt({ state: 'off', holds: ['generation'] }), [`${BASE}/S1/state.json`]: PRIOR })
      w.answer = launchDoc({ curate: CURATE })
      const asked = askWith(on, w, ['Run it now (Recommended)'])
      w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
      await $.session.start(ISTART)
      await $.classic.SessionStart({ source, session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
      for (let i = 0; i < 3; i += 1) {
        await clock.advance(2_000)
      }
      expect([loopOf(w).holds, genOf(w), asked.length, w.spawns.length]).toEqual([['generation', 'session'], null, 0, 0])
      await $.prompt.submit({ text: 'hi, what is next?', origin: { kind: 'composer' }, wait: false })
      for (let i = 0; i < 3; i += 1) {
        await clock.advance(2_000)
      }
      expect([loopOf(w).holds, genOf(w), asked.length]).toEqual([['generation', 'session'], null, 0])
      releaseReq(w, NOW + 12_000, { hold: 'session' })
      await clock.advance(2_000)
      expect([loopOf(w).holds, genOf(w)?.kind, genOf(w)?.sid]).toEqual([[], 'gen', 'S1'])
    })
  }

  test('a failed step is a block; the chain keeps its step, and release(question) retries it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n'),
      [LOOP]: closeLoop({ state: 'closing', chainStep: 'assemble', chainPhase: '1.1' }) })
    let fail = true
    w.answer = closeAnswer(a => (a.startsWith('tools/phaseend_index.py assemble') && fail
      ? { exitCode: 1, stdout: '', stderr: 'no PHASE_PLAN.md\n' } : undefined))
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    const lp = loop5(w)
    expect([lp.state, lp.chainStep, lp.holds]).toEqual(['closing', 'assemble', ['question']])
    expect(notices(w).map(n => n.input.text).join('\n')).toContain('no PHASE_PLAN.md')
    expect(chainRuns(w)).toEqual(['tools/phaseend_index.py assemble 1.1'])
    fail = false
    releaseReq(w, NOW + 4_000, { hold: 'question', note: 'fixed' })
    for (let i = 0; i < 4 && loop5(w).state !== 'off'; i += 1) {
      await clock.advance(2_000)
    }
    expect(chainRuns(w).slice(0, 3)).toEqual(['tools/phaseend_index.py assemble 1.1', 'tools/phaseend_index.py assemble 1.1',
      'tools/commit_task.sh phase-end PhaseEnd 1.1 phase-ends/PhaseEnd_Phase1.1.md'])
    expect(entries(w, 'mod-close').some(p => (w.files.get(p) ?? '').includes('The close chain resumes at assemble (fixed).'))).toBe(true)
  })

  test('a restart resumes at chainStep: the RUN: line left runs, the stage paths are committed, then clear', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: closeLoop({ state: 'closing', chainStep: 'run', chainPhase: '1.1', ownerSid: 'S0', heartbeatAt: NOW - 120_000,
      chainRuns: ['python tools/genend_index.py assemble 1'], chainStage: ['.claude-state/ledger/phase-1.1.jsonl', 'docs/ops/INDEX.md'] }) })
    w.files.delete(`${ROOT}/${PLAN}`)
    w.files.set(`${ROOT}/GENERATION_PLAN.md`, genPlan(''))
    w.answer = closeAnswer()
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(chainRuns(w)).toEqual([
      'tools/genend_index.py assemble 1',
      'tools/commit_task.sh phase-end Phase 1.1 archived phase-ends GENERATION_PLAN.md .claude-state/ledger/phase-1.1.jsonl docs/ops/INDEX.md',
      'tools/status.py clear',
      'tools/phaseend_index.py scratch 1.1',
      'tools/commit_task.sh phase-end Phase 1.1 closed',
    ])
    expect(loop5(w).state).toBe('off')
  })
})

// T11-fix: the attended run's loop bug (a STATUS-less turn end read as the end).
const FACTS = `${BASE}/S1/agents/E1.json`
const factsAt = (at: number): string => JSON.stringify({ model: 'opus', effort: 'medium', ctx: 1, at, updatedAt: at })

describe('no contract yet (T11-fix)', () => {
  test('no STATUS: a facts updatedAt refresh resets the 10 min clock; 10 min of silence then ends the run', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const task = (JSON.parse(running()) as { task: Record<string, unknown> }).task
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR, [LOOP]: running({ task: { ...task, lastCompleteAt: NOW - 590_000 } }),
      [FACTS]: factsAt(NOW - 590_000) })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    expect(loopOf(w).state).toBe('running')
    w.files.set(FACTS, factsAt(NOW + 2_000)) // a step: the background shell's verify
    await clock.advance(10_000)
    expect(loopOf(w).state).toBe('running') // 602 s since the turn end, 10 s since the step
    await clock.advance(580_000)
    expect(loopOf(w).state).toBe('running')
    await clock.advance(14_000)
    expect(loopOf(w).holds).toEqual(['question'])
    expect(entries(w, 'T1-end').length).toBe(1)
  })

  // 3.24 T13A: the plan archived already when the chain reaches assemble: the chain goes on at the archived commit and
  // reaches the closed line, the park and the toast.
  test('the plan archived already: the chain skips to the archived commit, the closed line, the park and the toast', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = noPlanWorld(on, { [`${ROOT}/GENERATION_PLAN.md`]: genPlan(''), [`${ROOT}/phase-ends/phase-1.1/PHASE_PLAN.md`]: PE_PLAN,
      [LOOP]: closeLoop({ state: 'closing', chainStep: 'assemble', chainPhase: '1.1', chainGenEnd: true }) })
    w.answer = closeAnswer(a => (a.startsWith('tools/launch.py') ? ok('{"mode": "router"}\n') : undefined))
    await $.session.start(START)
    for (let i = 0; i < 6 && loop5(w).state !== 'off'; i += 1) {
      await clock.advance(2_000)
    }
    const runs = chainRuns(w)
    expect(runs.some(r => r.startsWith('tools/phaseend_index.py') && !r.startsWith('tools/phaseend_index.py scratch'))).toBe(false)
    expect(runs).toContain('tools/phaseend_index.py scratch 1.1') // 3.29 T5: the temp folder still goes
    expect(runs).toContain('tools/commit_task.sh phase-end Phase 1.1 archived phase-ends GENERATION_PLAN.md')
    const line = 'Phase 1.1 closed. Push when ready. The generation is complete; the next one is yours to start.'
    expect(loop5(w).holds).toEqual(['generation'])
    expect(toastReqs(w)).toEqual([{ cause: 'input', body: line }])
    expect(entries(w, 'mod-close').some(p => (w.files.get(p) ?? '').includes('archived already'))).toBe(true)
    expect(notices(w).filter(n => n.input.text.includes('a phase plan is needed'))).toEqual([])
  })
})

// T11-fix2: liveness every tick. killed, failed or gone with no contract: the `stopped` hold and a notice, never a resume
// or a respawn by the loop; release(stopped) resumes by message, else (or `fresh`) respawns; `waiting` is busy.
const stoppedReq = (w: W, ms: number, input: Record<string, unknown>): void => {
  const id = `${String(ms)}-1-release`
  w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'release', caller: 'P1', input, at: ms }))
}
const setStatus = (w: W, id: string, status: Agent['status']): void => {
  w.list = w.list.map(a => (a.id === id ? { ...a, status } : a))
}
const STOP_NOTICE = '‹pa› T1\'s expert was stopped (Ctrl+C); its coder too if it had one. The task is paused until the developer wants it to go on. ' +
  'A go they already gave counts: if their latest message asked for this task or the work to go on, ' +
  'resume it now (release stopped); otherwise ask them.'

describe('a stopped loop agent (T11-fix2)', () => {
  test('killed with no contract: the stopped hold and one notice, no send, no spawn, no toast; no new task while held', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    setStatus(w, 'E1', 'killed')
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['stopped'])
    expect(notices(w).map(n => n.input.text)).toEqual([STOP_NOTICE])
    expect(notices(w)[0]?.input.toast ?? '').toBe('')
    expect(toastCauses(w)).toEqual([])
    expect(w.files.get(entries(w, 'mod-hold').pop() ?? '')).toContain('E1 killed with no contract: T1 held (stopped)')
    await clock.advance(60_000)
    expect([w.sends, w.spawns.length, ran(w, 'tools/plan_edit.py next').length, notices(w).length]).toEqual([[], 0, 0, 1])
  })

  test('failed with no contract: the stopped hold; the notice says failed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    setStatus(w, 'E1', 'failed')
    await $.session.start(START)
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['stopped'])
    expect(notices(w)[0]?.input.text).toContain('T1\'s expert failed: reason unknown')
    expect([w.sends.length, w.spawns.length]).toEqual([0, 0])
  })

  test('absent from the list 15 s after its spawn: treated as killed, and the notice says so', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = []
    await clock.advance(2_000)
    expect(loop2(w).holds).toEqual(['stopped'])
    expect(notices(w)[0]?.input.text).toContain('E1 is gone from the agent list, so I treat it as killed')
    expect([w.sends.length, w.spawns.length]).toEqual([0, 0])
  })

  test('3.28.1 T9c: a takeover with the recorded agent gone (a restart): continue: under its id, no stopped hold', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: orphan(NOW - 50_000) })
    w.list = []
    await $.session.start(START)
    await clock.advance(4_000)
    expect([loop3(w).holds, loop3(w).task?.recovery?.phase, w.sends, experts(w).length]).toEqual([[], 'sent', [CONTINUE_TEXT], 0])
    expect(notices(w)[0]?.input.text).toContain('I resumed it under its id (E0)')
  })

  test('3.28.1 T9c: killed and never seen at work by this process (a restart, heartbeat fresh): recovered, no stopped hold', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    setStatus(w, 'E1', 'killed')
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loop2(w).holds, loop2(w).task?.recovery?.phase, w.sends]).toEqual([[], 'sent', [CONTINUE_TEXT]])
    expect(notices(w).map(n => n.input.text).some(t => t.includes('stopped (Ctrl+C'))).toBe(false)
  })

  test('3.30.1: paused and found cut off after a quick restart (heartbeat fresh, no takeover): held, never recovered (bfm T11)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const doc = JSON.parse(running()) as { task: Record<string, unknown> }
    doc.task['paused'] = { at: NOW - 60_000 }
    const w = world(on, { [LOOP]: JSON.stringify(doc) })
    setStatus(w, 'E1', 'killed')
    await $.session.start(START)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    const task = (loop2(w).task ?? {}) as { stopped?: { status?: string } | null; recovery?: unknown }
    expect([loop2(w).holds.includes('stopped'), task.stopped?.status, task.recovery ?? null, w.sends]).toEqual([true, 'paused', null, []])
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes(
      'T1 was paused on the developer\'s request: not resumed after the restart; Claude asks.'))).toBe(true)
    expect(notices(w).filter(n => n.input.text.includes('paused on the developer\'s request'))).toHaveLength(1)
  })

  test('3.30.1: a recovery lands the queue first; a task the plan now has queued is set aside, never resumed (bfm T11)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const queued = [{ command: 'set-status T1 queued', why: 'run the bank first', at: NOW - 30_000, caller: 'P1' }]
    const w = world(on, { [LOOP]: running({ queuedEdits: queued }) })
    setStatus(w, 'E1', 'killed')
    const plan = reorderPlan(w)
    await $.session.start(START)
    for (let i = 0; i < 3; i += 1) {
      await clock.advance(2_000)
    }
    expect([w.sends, loop2(w).queuedEdits, plan.status, asideOf(w)]).toEqual([[], [], 'queued', { T1: { attempt: 1 } }])
    expect(entries(w, 'mod-hold').some(p => (w.files.get(p) ?? '').includes(
      'T1 recovery: the plan has it queued, so it is not resumed; the loop goes on from the plan'))).toBe(true)
    expect(notices(w).some(n => n.input.text.includes('it is set aside, not resumed'))).toBe(true)
    expect(w.spawns.at(-1)?.description.startsWith('T2')).toBe(true)
  })

  test('3.30.1: a paused task\'s handoff is held, never respawned; the release respawns it with PROGRESS', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const doc = JSON.parse(running()) as { task: Record<string, unknown> }
    doc.task['paused'] = { at: NOW - 60_000 }
    const w = world(on, { [LOOP]: JSON.stringify(doc) })
    setStatus(w, 'E1', 'running')
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'Staying paused as asked.\n\nSTATUS: handoff\nLOG: logs/T1.md'))
    await clock.advance(60_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    const task = (loop2(w).task ?? {}) as { stopped?: { status?: string } | null; agentId?: string }
    expect([w.spawns.length, loop2(w).holds.includes('stopped'), task.stopped?.status, task.agentId]).toEqual([0, true, 'paused', ''])
    expect(notices(w).filter(n => n.input.text.includes('handed back while T1 is paused'))).toHaveLength(1)
    expect(notices(w).some(n => n.input.text.includes('before the last session ended'))).toBe(false)
    stoppedReq(w, NOW + 66_000, { hold: 'stopped', note: 'Go on.' })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.sends).toEqual([])
    expect(experts(w)[0]?.prompt).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md')
    expect([loop2(w).task?.kind, loop2(w).task?.attempt]).toEqual(['relaunch', 2])
  })

  test('3.32.19: a paused task its expert finishes holds the queue (session); the next task waits for the release (bfm T4, T14)', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const doc = JSON.parse(running()) as { task: Record<string, unknown> }
    doc.task['paused'] = { at: NOW - 60_000 }
    const w = world(on, { [LOOP]: JSON.stringify(doc) })
    setStatus(w, 'E1', 'running')
    await $.session.start(START)
    stoppedReq(w, NOW + 1_000, { hold: 'session' })        // the session start's hold, released: only the pause holds
    await clock.advance(2_000)
    expect(loop2(w).holds.includes('session')).toBe(false)
    await $.turn.complete(complete('E1', 'The health check passed, so the safe point is the end of T1.\n\nSTATUS: done\nCOMMIT: abc'))
    await clock.advance(60_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loop2(w).task, loop2(w).holds.includes('session'), w.spawns.length]).toEqual([null, true, 0])
    expect(entries(w, 'T1-end').length).toBe(1)
    expect(notices(w).filter(n => n.input.text.includes('T1 finished while paused on the developer\'s request'))).toHaveLength(1)
    stoppedReq(w, NOW + 66_000, { hold: 'session' })       // the developer's go
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    // the loop goes on: the hold lifts and the plan's next task spawns (this world's stub plan offers T1 again)
    expect([loop2(w).holds.includes('session'), w.spawns.length]).toEqual([false, 1])
  })

  test('killed with a pending contract: routed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: done\nLOG: l\nCOMMIT: def5678'))
    setStatus(w, 'E1', 'killed')
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
    expect(loop2(w).holds).toEqual([])
  })

  test('waiting (its own background work) is never ended; idle again, the run ends', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    setStatus(w, 'E1', 'waiting')
    await $.session.start(START)
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'STATUS: done\nLOG: l\nCOMMIT: def5678'))
    await clock.advance(11 * 60_000)
    expect([ran(w, 'tools/plan_edit.py set-status').length, loop2(w).holds, loop2(w).state]).toEqual([0, [], 'running'])
    setStatus(w, 'E1', 'idle')
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status').length).toBe(1)
  })

  test('release(stopped) resumes the same agent by message with the note; the hold pops', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped'] }) })
    setStatus(w, 'E1', 'killed')
    stoppedReq(w, NOW, { hold: 'stopped', note: 'Use ints.' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual(['continue: the developer stopped you and now wants you to go on. Use ints. ' +
      'Check what your coder left (git status, git diff) before continuing; a coder stopped mid-edit may have left partial changes.'])
    expect([loop2(w).holds, loop2(w).state, w.spawns.length]).toEqual([[], 'running', 0])
  // 3.23 T13d.c3: a confirmation that only says done is not sent to the partner
    expect(notices(w).map(n => n.input.text)).not.toContain('released: stopped (resumed)')
    setStatus(w, 'E1', 'running')
    await clock.advance(20_000)
    expect(loop2(w).holds).toEqual([])
  })

  test('release(stopped) whose send fails: respawned with PROGRESS and the NOTE, status relaunch attempt 2', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped'] }) })
    w.deliver = false
    w.list = []
    stoppedReq(w, NOW, { hold: 'stopped', note: 'Use ints.' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/status.py set')[0]).toEqual(['py', 'tools/status.py', 'set', '--task', 'T1', '--agent', 'pa-expert',
      '--kind', 'relaunch', '--attempt', '2', '--session', 'S1'])
    expect(experts(w).length).toBe(1)
    expect(experts(w)[0]?.prompt).toContain('PROGRESS: phase-ends/current/TASK_PROGRESS.md')
    expect(experts(w)[0]?.prompt).toContain('Use ints.')
    expect(w.files.has(PROGRESS_FILE)).toBe(true)
    expect([loop2(w).holds, loopOf(w).task?.attempt, loopOf(w).task?.kind]).toEqual([[], 2, 'relaunch'])
  })

  test('release(stopped, fresh): respawned without a send', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ holds: ['stopped'] }) })
    setStatus(w, 'E1', 'killed')
    stoppedReq(w, NOW, { hold: 'stopped', note: 'Start over with ints.', fresh: true })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual([])
    expect(experts(w).length).toBe(1)
    expect(experts(w)[0]?.prompt).toContain('Start over with ints.')
    expect(notices(w).map(n => n.input.text)).toContain('released: stopped (restarted, attempt 2)')
  })

  test('the running-now note shows a killed expert as stopped, a waiting one as waiting', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [LOOP]: running({ holds: ['stopped'] }), [`${BASE}/S1/state.json`]: PRIOR,
      [`${BASE}/S1/agents/E1.json`]: facts({ type: 'pa-expert', description: 'T1 add function' }),
    })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'killed', type: 'pa-expert' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what happened?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('‹pa› running now: T1 add function\n- expert E1 (claude-opus-5-5): stopped'))).toBe(true)
    setStatus(w, 'E1', 'waiting')
    await $.prompt.submit({ text: 'and now?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('- expert E1 (claude-opus-5-5): waiting'))).toBe(true)
  })

  test('3.32.1: an expert gone from the list with a child listed busy is waiting on it; the child shows without a facts parentId', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, {
      [LOOP]: running(), [`${BASE}/S1/state.json`]: PRIOR,
      [`${BASE}/S1/agents/E1.json`]: facts({ type: 'pa-expert', description: 'T1 add function' }),
      [`${BASE}/S1/agents/C1.json`]: facts({ type: 'pa-coder', parentId: '' }),
    })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what happened?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    expect(delivered(w).some(t => t.includes('- expert E1 (claude-opus-5-5): waiting on its coder\n- coder C1 (claude-opus-5-5)'))).toBe(true)
    expect(loopJ(w).holds).not.toContain('stopped')
  })
})

// 3.28.1 T15: a `WAITING: shell <what>` hand-back is told to Claude; a live shell keeps its run waiting; a woken
// expert's SendMessage report is its report.
type LoopJ = Loop2 & { task: { forwardAt?: number; recovery?: unknown; contract?: string } | null }
const loopJ = (w: W): LoopJ => JSON.parse(w.files.get(LOOP) ?? '{}') as LoopJ
describe('background shells (3.28.1 T15)', () => {
  test('a coder hands back `WAITING: shell …`: Claude gets one notice naming the task, the coder and what runs', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: running() })
    w.list = [...w.list, { id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await $.session.start(START)
    await clock.advance(2_000)
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'C1', input: { message: 'WAITING: shell bake_rest.sh (the full bake)' } })
    await clock.advance(2_000)
    const n = notices(w).filter(x => x.input.text.includes('background shell'))
    expect(n.map(x => x.input.text)).toEqual([
      "‹pa› T1's coder (C1) is waiting on a background shell: bake_rest.sh (the full bake). It picks up by itself when the shell ends. " +
      'Tell the developer in one plain line: what is running, for which task, and that it picks up by itself.'])
  })

  test('an agent outside the running task waiting on a shell: no notice', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'Z9', input: { message: 'WAITING: shell x.sh' } })
    await clock.advance(2_000)
    expect(notices(w).filter(x => x.input.text.includes('background shell'))).toEqual([])
  })

  test('the expert dropped from the list while its shell runs (shells.json): waiting, not stopped', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    w.files.set(`${BASE}/S1/shells.json`, JSON.stringify({ E1: [{ id: 'b1', name: 'gate.sh', at: NOW, type: 'pa-expert' }] }))
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = []
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loopJ(w).holds, loopJ(w).task?.recovery ?? null]).toEqual([[], null])
  })

  test('a woken expert reports with SendMessage: the note\'s contract is captured as its report', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    await $.prompt.submit({ text: '<agent-message from="E1">\nSTATUS: done\nLOG: logs/T1.md\nCOMMIT: def5678\n</agent-message>', origin: { kind: 'peer' }, wait: false })
    w.list = [{ id: 'E1', status: 'completed', type: 'pa-expert' }]
    await clock.advance(60_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
  })

  // 3.28.1 T12: nothing is sent to a cold agent; a fresh one, seeded, takes the message and the old one's place.
  const x1 = (o: Record<string, unknown>): Record<string, unknown> => ({ ...(JSON.parse(running()) as { task: Record<string, unknown> }).task, agentId: 'X1', ...o })
  test('3.28.1 T12: the router\'s forward with text for a cold expert: a fresh expert has the text, nothing sent', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running({ task: x1({ spawnedAt: NOW - 3 * 3_600_000, lastCompleteAt: NOW - 2 * 3_600_000 }) }) })
    w.list = [{ id: 'X1', status: 'idle', type: 'pa-expert' }]
    const id = `${String(NOW)}-1-forward`
    w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind: 'forward', caller: 'router', input: { agentId: 'X1', child: 'C9', text: 'Hand-back from your background agent C9:\ngreen' }, at: NOW }))
    await $.session.start(START)
    await clock.advance(2_000)
    expect(w.sends).toEqual([])
    expect(experts(w).length).toBe(1)
    expect(experts(w)[0]?.prompt).toContain('Hand-back from your background agent C9:\ngreen')
  })
})

// 3.28.8: TerrainDiffusion T12 stalled with tasks left: its expert, waiting on its coder, dropped from the list, was
// taken for killed (`stopped`), and its done report at 10:50 sat under the hold.
describe('no stall on a false stop (3.28.8)', () => {
  const handback = (from: string, report: string): string =>
    `Another Claude session sent a message:\n<agent-message from="${from}">\n[Subagent hand-back] The report follows:\n` +
    `${report.split('\n').map(l => `  ${l}`).join('\n')}\n</agent-message>`

  test('the expert dropped from the list while its coder works: waiting, not stopped', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = [{ id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder' }]
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect([loopJ(w).holds, loopJ(w).task?.recovery ?? null]).toEqual([[], null])
    expect(notices(w).filter(x => x.input.text.includes('stopped (Ctrl+C'))).toEqual([])
  })

  // 3.32.3: Claude Code's notices (2.1.299 and later, as TerrainDiffusion's journal holds them): a helper that stopped
  // while its own background work runs; and the one after a SubagentHandback, which only points at the report.
  const waitingNotice = (id: string): string => `<task-notification>\n<task-id>${id}</task-id>\n<status>completed</status>\n` +
    '<summary>Agent "T1.c1 long shell" is waiting on its own background work</summary>\n<note>This agent stopped with ' +
    'background work of its own still running. It may resume on its own when that work completes or reports, and the same ' +
    'task-id notifies again if it does; the result below may be interim.</note>\n<result>This agent has not reported yet: it ' +
    'is waiting on its own background work and will deliver its report through SubagentHandback when that finishes.\n' +
    '</result>\n</task-notification>'
  const pointsAt = (id: string): string => `<task-notification>\n<task-id>${id}</task-id>\n<status>completed</status>\n` +
    `<result>This agent's report was delivered to you as a message from "${id}" (its SubagentHandback call). Read it ` +
    'there; it is not repeated here.\n</result>\n</task-notification>'

  // 3.32.3: a user's long-shell tasks on 3.32.1: the coder's report, after its waiting notice and under the same task id,
  // was taken for a repeat and dropped; nothing was queued to the expert, dropped from the list meanwhile, so the loop
  // took it for stopped ("absent with no contract").
  test('3.32.3: a coder\'s report after its waiting notice reaches the expert, which is not taken for stopped', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [`${BASE}/S1/state.json`]: PRIOR, [LOOP]: running() })
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'E1', status: 'idle', type: 'pa-expert' }]
    await $.session.start(ISTART) // the router routes in an interactive session
    await clock.advance(2_000)
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'C1', status: 'waiting', parentId: 'E1', type: 'pa-coder' }]
    await $.prompt.submit({ text: waitingNotice('C1'), origin: { kind: 'task-notification' }, wait: false })
    await clock.advance(2_000)
    await $.turn.complete(complete('E1', 'WAITING: coder C1 runs its shell.'))
    await clock.advance(2_000)
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }, { id: 'C1', status: 'completed', parentId: 'E1', type: 'pa-coder' }]
    await $.prompt.submit({ text: handback('C1', 'STATUS: done\nCOMMIT: b0b9d78'), origin: { kind: 'peer' }, wait: false })
    await $.prompt.submit({ text: pointsAt('C1'), origin: { kind: 'task-notification' }, wait: false })
    for (let i = 0; i < 5; i++) await clock.advance(2_000)
    expect(loopJ(w).holds).toEqual([])
    expect(w.sends.filter(t => t.startsWith('Hand-back from your background agent C1:')).map(t => t.includes('COMMIT: b0b9d78')))
      .toEqual([false, true])
    await $.turn.complete(complete('E1', 'STATUS: done\nCOMMIT: b0b9d78'))
    for (let i = 0; i < 10; i++) await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py set-status').map(r => r.slice(2, 5))).toEqual([['set-status', 'T1', 'done']])
  })

  for (const known of [true, false]) {
    test(`3.32.1 T5: a coder the list gives no parent, ${known ? 'known from parents.json, keeps its expert waiting' : 'and none in parents.json: stopped'}`, async ($, on) => {
      const clock = mock.clock(on, { now: NOW })
      const w = world(on, { [LOOP]: running(), ...(known ? { [`${BASE}/S1/parents.json`]: '{"C1":"E1"}' } : {}) })
      await $.session.start(START)
      await clock.advance(2_000)
      w.list = [{ id: 'C1', status: 'running', type: 'pa-coder' }]
      await clock.advance(2_000)
      await clock.advance(2_000)
      expect(loopJ(w).holds).toEqual(known ? [] : ['stopped'])
    })
  }

  test('a stop taken from absence is lifted by the expert\'s own report after it; the report is routed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    w.list = []
    await clock.advance(2_000)
    expect(loopJ(w).holds).toEqual(['stopped'])
    await $.prompt.submit({ text: handback('E1', 'done. Roads reach every town.\nSummary: logs/T1.md (commit def5678).'), origin: { kind: 'peer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loopJ(w).holds).toEqual([])
    expect(ran(w, 'tools/plan_edit.py set-status')[0]).toEqual(['py', 'tools/plan_edit.py', 'set-status', 'T1', 'done', '--by', 'router'])
    expect(notices(w).filter(x => x.input.text.includes('it was never stopped')).length).toBe(1)
  })

  test('a stop seen as killed keeps its hold, report or not', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    await $.session.start(START)
    await clock.advance(2_000)
    setStatus(w, 'E1', 'killed')
    await clock.advance(2_000)
    expect(loopJ(w).holds).toEqual(['stopped'])
    await $.prompt.submit({ text: handback('E1', 'done. x'), origin: { kind: 'peer' }, wait: false })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(loopJ(w).holds).toEqual(['stopped'])
    expect(ran(w, 'tools/plan_edit.py set-status')).toEqual([])
  })
})

// 3.32.1: no forced route. A helper done in all but name (handed back, no tool call since) is a leftover: its busy
// listing and live shells hold nothing; real work waits as before, with one snapshot and notice at 30 minutes.
describe('leftovers after a hand-back, and the 30-minute notice (3.32.1)', () => {
  const DONE = 'done. Roads reach every town.\nSummary: logs/T1.md (commit def5678).'
  const reported = (contract: string | null, o: Record<string, unknown> = {}): string => running({
    task: { id: 'T1', title: 'add function', agent: 'pa-expert', coder: 'opus55', effort: 'medium', attempt: 1, kind: 'task',
      agentId: 'E1', startSha: 'abc', spawnedAt: NOW - 60 * 60_000, lastCompleteAt: NOW, contract: null, plan: T1,
      ...(contract === null ? {} : { pendingContract: contract }), ...o },
  })
  const CODER = { id: 'C1', status: 'running' as const, parentId: 'E1', type: 'pa-coder', description: 'T1.c1 roads' }
  const SHELLS = `${BASE}/S1/shells.json`
  const routed = (w: W): number => ran(w, 'tools/plan_edit.py set-status').length
  const leftNotices = (w: W): string[] => notices(w).map(x => x.input.text).filter(x => x.includes('was left running'))
  const stallNotices = (w: W): string[] => notices(w).map(x => x.input.text).filter(x => x.includes('the loop is still waiting for'))
  const tick = async (clock: { advance: (ms: number) => Promise<unknown> }, ms: number): Promise<void> => {
    for (let i = 0; i < ms / 2_000; i++) await clock.advance(2_000)
  }
  const handBack = async ($: { tool: { check: (e: { tool: string; agentId: string; input: { message: string } }) => Promise<unknown> } }, id: string, message: string): Promise<void> => {
    await $.tool.check({ tool: 'SubagentHandback', agentId: id, input: { message } })
  }

  test('a coder handed back with only a moved shell left: the done routes after the quiet time, with snapshot and notice', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE) })
    w.list = [CODER]
    w.files.set(SHELLS, JSON.stringify({ C1: [{ id: 'm7', name: 'python', at: NOW, type: 'pa-coder', moved: true }] }))
    await $.session.start(START)
    await handBack($, 'C1', 'done. Roads added.')
    await tick(clock, 30_000)
    expect([routed(w), snapshots(w).length]).toEqual([1, 1])
    const rel = (snapshots(w)[0] ?? '').slice(ROOT.length + 1)
    expect(leftNotices(w)).toEqual([
      "‹pa› T1's expert reported done and the loop went on, but pa-coder C1 (T1.c1 roads), the shell python (moved into the " +
      "background when it timed out) was left running in Claude Code after handing back. Tell the developer in one line, " +
      `naming it: if they don't need it, they can stop it in /tasks. A diagnostic snapshot is at ${rel}.`])
    expect(hold(w)).toContain('routed past what was left running after handing back')
  })

  test('a coder handed back with a deliberate background shell left: routes, the notice names the shell', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE) })
    w.list = [{ ...CODER, status: 'idle' }]
    w.files.set(SHELLS, JSON.stringify({ C1: [{ id: 'b1', name: 'gate.sh', at: NOW, type: 'pa-coder' }] }))
    await $.session.start(START)
    await handBack($, 'C1', 'done. Roads added.')
    await tick(clock, 30_000)
    expect(routed(w)).toBe(1)
    expect(leftNotices(w)).toHaveLength(1)
    expect(leftNotices(w)[0]).toContain('but the shell gate.sh was left running')
  })

  test('a coder still working with no hand-back: not routed at 2 minutes, nor said', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: reported(DONE) })
    w.list = [CODER]
    await $.session.start(START)
    await tick(clock, 124_000)
    expect([routed(w), stallNotices(w), snapshots(w)]).toEqual([0, [], []])
  })

  test('a coder still working with no hand-back: at 30 minutes one snapshot and notice, not routed; still not at 60', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    // the report came 28 minutes before the start: 30 at 2 minutes in; the loop's file keeps what was said
    const w = world(on, { [LOOP]: reported(DONE, { lastCompleteAt: NOW - 28 * 60_000 }) })
    w.list = [CODER]
    await $.session.start(START)
    await tick(clock, 60_000)
    expect([routed(w), stallNotices(w)]).toEqual([0, []])
    await tick(clock, 64_000)
    expect([routed(w), snapshots(w).length, loopJ(w).state]).toEqual([0, 1, 'running'])
    const rel = (snapshots(w)[0] ?? '').slice(ROOT.length + 1)
    expect(stallNotices(w)).toEqual([
      "‹pa› T1's expert reported done 30 minutes ago, and the loop is still waiting for pa-coder C1 (T1.c1 roads). " +
      `A diagnostic snapshot is at ${rel}. Tell the developer in one line; if it isn't needed, they can stop it in /tasks.`])
    expect(hold(w)).toContain("T1's done has waited 30 minutes for: pa-coder C1 (T1.c1 roads).")
  })

  test('at 60 minutes, already said for this attempt: not routed, nothing said again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: reported(DONE, { lastCompleteAt: NOW - 60 * 60_000, stallNoted: '1#E1' }) })
    w.list = [CODER]
    await $.session.start(START)
    await tick(clock, 10_000)
    expect([routed(w), snapshots(w), stallNotices(w), loopJ(w).state]).toEqual([0, [], [], 'running'])
  })

  test('a coder that handed back and then made a tool call counts as busy again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE) })
    w.list = [CODER]
    await $.session.start(START)
    await handBack($, 'C1', 'done. Roads added.')
    w.files.set(`${BASE}/S1/agents/C1.json`, JSON.stringify({ type: 'pa-coder', updatedAt: NOW + 10_000 }))
    await tick(clock, 124_000)
    expect([routed(w), leftNotices(w)]).toEqual([0, []])
  })

  test('the expert listed waiting with only its own leftover shell after its hand-back: its report is captured and routes', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(null) })
    w.list = [{ id: 'E1', status: 'waiting', type: 'pa-expert' }]
    w.files.set(SHELLS, JSON.stringify({ E1: [{ id: 'm8', name: 'gate.sh', at: NOW, type: 'pa-expert', moved: true }] }))
    await $.session.start(START)
    await clock.advance(2_000)
    await handBack($, 'E1', DONE)
    await tick(clock, 30_000)
    expect(routed(w)).toBe(1)
    expect(leftNotices(w)).toHaveLength(1)
    expect(leftNotices(w)[0]).toContain('the shell gate.sh (moved into the background when it timed out), pa-expert E1 itself (still listed as waiting)')
  })

  test('the expert listed waiting on a working child: waits, with the notice at 30 minutes', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE, { lastCompleteAt: NOW - 28 * 60_000 }) })
    w.list = [{ id: 'E1', status: 'waiting', type: 'pa-expert' }, CODER]
    await $.session.start(START)
    await handBack($, 'E1', DONE)
    await tick(clock, 124_000)
    expect([routed(w), stallNotices(w).length]).toEqual([0, 1])
    expect(stallNotices(w)[0]).toContain('still waiting for pa-coder C1 (T1.c1 roads).')
  })

  test('the expert listed running with a report: waits; the notice at 30 names it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: reported(DONE, { lastCompleteAt: NOW - 28 * 60_000 }) })
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(START)
    await tick(clock, 124_000)
    expect([loopJ(w).state, routed(w), stallNotices(w).length]).toEqual(['running', 0, 1])
    expect(stallNotices(w)[0]).toContain('still waiting for pa-expert E1 itself (listed as running).')
  })

  test('no report with a busy child: still waiting at 30 minutes, never stopped, no notice', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: reported(null, { lastCompleteAt: NOW - 28 * 60_000 }) })
    w.list = [CODER]
    await $.session.start(START)
    await tick(clock, 2 * 60_000 + 4_000)
    expect([loopJ(w).state, loopJ(w).holds, routed(w), stallNotices(w)]).toEqual(['running', [], 0, []])
  })

  test('a steer queued to the expert keeps it waiting past a leftover', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE, { steerAt: NOW + 1 }) })
    w.list = [{ id: 'E1', status: 'idle', type: 'pa-expert' }, CODER]
    await $.session.start(START)
    await handBack($, 'C1', 'done. Roads added.')
    await tick(clock, 4 * 60_000)
    expect([loopJ(w).state, routed(w), leftNotices(w)]).toEqual(['running', 0, []])
  })

  // T3: Claude Code holds a hand-back while the agent's background command runs; the mod passes the report on.
  const held = (w: W): string[] => w.sends.filter(t => t.includes('is holding its report'))
  test('T3: a coder hands back with a live shell: its parent gets the held report once, journaled once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(null) })
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }, CODER]
    w.files.set(SHELLS, JSON.stringify({ C1: [{ id: 'm7', name: 'python', at: NOW, type: 'pa-coder', moved: true }] }))
    await $.session.start(START)
    await clock.advance(2_000)
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'C1', input: { message: 'done. Roads added.\nCommit: def5678' } })
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'C1', input: { message: 'done. Roads added.\nCommit: def5678' } })
    expect(held(w)).toEqual(['‹pa› Your pa-coder C1 handed back while its background command python is still running, so Claude Code ' +
      'is holding its report until that command ends. Its report, as sent:\n\ndone. Roads added.\nCommit: def5678'])
    expect(entries(w, 'mod-shell').filter(p => (w.files.get(p) ?? '').includes('C1 handed back'))).toHaveLength(1)
  })

  test('T3: a hand-back with no live shell changes nothing', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(null) })
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }, CODER]
    w.files.set(SHELLS, JSON.stringify({ C1: [{ id: 'm7', name: 'python', at: NOW, type: 'pa-coder', exit: 1 }] }))
    await $.session.start(START)
    await clock.advance(2_000)
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'C1', input: { message: 'done. Roads added.' } })
    await $.tool.check({ tool: 'SubagentHandback', agentId: 'E1', input: { message: DONE } })
    await tick(clock, 2 * 60_000 + 4_000)
    expect([routed(w), held(w), entries(w, 'mod-shell'), loopJ(w).task?.pendingContract ?? null]).toEqual([0, [], [], null])
  })

  // T2: the diagnostic snapshot, written before the route; a failure is journaled and the route goes on.
  const CODER_TR = norm(subagentTranscript('C:\\p\\S1.jsonl', 'S1', 'C1'))
  const coderRows = [
    { type: 'assistant', timestamp: '2026-11-02T10:00:03Z', version: '2.1.300',
      message: { content: [{ type: 'tool_use', id: 'u1', name: 'Bash', input: { command: 'bash tools/run.sh gate -- pytest', run_in_background: true } }] } },
    { type: 'user', timestamp: '2026-11-02T10:00:04Z', message: { content: [{ type: 'tool_result', tool_use_id: 'u1', content: 'Command running in background with ID: g7' }] } },
    { type: 'assistant', timestamp: '2026-11-02T10:00:05Z', message: { content: [{ type: 'text', text: 'Waiting on the gate.' }] } },
  ].map(r => JSON.stringify(r)).join('\n')
  const hold = (w: W): string => entries(w, 'mod-hold').map(p => w.files.get(p) ?? '').join('\n')
  const snapshots = (w: W): string[] => [...w.files.keys()].filter(p => p.startsWith(`${ROOT}/.run/mod/stalls/T1-`))

  test('T2: the snapshot holds the list, the busy child\'s last tool calls and the processes; the notice names it; routed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE), [CODER_TR]: coderRows, [`${BASE}/S1/agents/C1.json`]: '{"type":"pa-coder"}',
      [norm(subagentTranscript('C:\\p\\S1.jsonl', 'S1', 'E1'))]: JSON.stringify({ type: 'assistant', version: '2.1.301', message: { content: 'done.' } }) })
    w.answer = argv => (argv[0] === 'ps' ? ok('PID PPID ELAPSED COMMAND\n42 1 05:00 node gate.js --token s3cr3t\n') : defaultAnswer(argv))
    w.list = [CODER]
    await $.session.start(START)
    // the transcript path only: a startup's SessionStart would hold the loop for the developer
    await $.classic.SessionStart({ source: 'compact', session_id: 'S1', transcript_path: 'C:\\p\\S1.jsonl' } as never)
    await handBack($, 'C1', 'done. Roads added.')
    await tick(clock, 30_000)
    expect(routed(w)).toBe(1)
    const [file] = snapshots(w)
    const text = w.files.get(file ?? '') ?? ''
    expect(text).toContain('- C1 | pa-coder | - | T1.c1 roads | running | parent E1')
    expect(text).toContain('- 2026-11-02T10:00:03 Bash [background] bash tools/run.sh gate -- pytest')
    expect(text).toContain('- shell g7 started 2026-11-02T10:00:04, no completion notice')
    expect(text).toContain('42 1 05:00 node gate.js --token [redacted]')
    expect(text).toContain('- PA3 ')
    expect(text).toContain('; Claude Code 2.1.301; platform not Windows; session S1')
    expect(text).toContain('### E1\n\n(none)') // the expert's facts file is absent here
    const rel = (file ?? '').slice(ROOT.length + 1)
    expect(leftNotices(w)).toHaveLength(1)
    expect(leftNotices(w)[0]?.endsWith(`they can stop it in /tasks. A diagnostic snapshot is at ${rel}.`)).toBe(true)
    expect(hold(w)).toContain(`Snapshot: ${rel}.`)
    expect(ran(w, '-eo pid,ppid,etime,args')).toHaveLength(1)
  })

  test('T2: a failing process run and an unreadable transcript: the snapshot says so, the journal line too, routed', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    on('tool.check', async () => ({ decision: 'allow', reason: 'core' }))
    const w = world(on, { [LOOP]: reported(DONE) })
    w.answer = argv => (argv[0] === 'ps' ? { exitCode: 1, stdout: '', stderr: 'ps: not found' } : defaultAnswer(argv))
    w.list = [CODER]
    await $.session.start(START)
    await handBack($, 'C1', 'done. Roads added.')
    await tick(clock, 30_000)
    expect(routed(w)).toBe(1)
    expect(leftNotices(w)).toHaveLength(1)
    expect(snapshots(w)).toHaveLength(1)
    expect(w.files.get(snapshots(w)[0] ?? '')).toContain('## Processes\n\n(not read)')
    expect(hold(w)).toContain('Gaps: the transcript of E1 was unreadable (no session transcript path yet); ' +
      'the transcript of C1 was unreadable (no session transcript path yet); the process list failed (exit 1): ps: not found.')
  })
})

describe('the band (3.23 T13)', () => {
  test("the tick writes it: the loop line with task n of m, the expert and its coder's last step; AbovePrompt draws it on terminal and desktop, idle and a survey draw the engine", async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, { [`${ROOT}/${PLAN}`]: '# Phase 3.23 — Autopilot\n' })
    w.answer = argv => (argv.slice(1).join(' ').startsWith('tools/plan_edit.py show') ? ok('T1 | running | x\nT2 | queued | x\n') : defaultAnswer(argv))
    on('ui.render', async ($, e) => {
      const { Text } = $.ui.resolve(e)
      return Text({ children: 'engine' })
    })
    const mount = async (surface: 'terminal' | 'desktop', hasSurvey = false): Promise<string> => JSON.stringify(await (await $.ui.mount({
      plugin: 'pa', surface, component: 'AbovePrompt',
      props: { hasSurvey, isWorking: false, maxRows: 10, bodyColumns: 60, scroll: { offset: 0, bodyRows: 9 }, view: {} },
    })).drawn())
    // 3.32.17: since Claude Code 2.1.323 every mod's AbovePrompt is drawn, stacked, each its own `part`; the band is
    // PA3's part, which draws without the engine's (`next`), as before
    const paPart = (node: unknown): unknown => {
      if (typeof node !== 'object' || node === null) {
        return undefined
      }
      const n = node as { part?: { plugin?: unknown }; children?: unknown }
      if (n.part?.plugin === 'pa') {
        return n
      }
      for (const k of Array.isArray(n.children) ? n.children : [n.children]) {
        const found = paPart(k)
        if (found !== undefined) {
          return found
        }
      }
      return undefined
    }
    await $.session.start(START)
    expect(await mount('terminal')).toContain('engine')
    await clock.advance(2_000)
    expect(w.spawns).toHaveLength(1)
    w.list.push({ id: 'C1', status: 'running', parentId: 'E1', type: 'pa-coder', description: 'T1.c1 add' })
    w.files.set(`${BASE}/S1/agents/C1.json`, JSON.stringify({ model: 'opus', steps: ['Read calc/add.py', 'Edit calc/add.py'] }))
    await clock.advance(2_000)
    for (const surface of ['terminal', 'desktop'] as const) {
      const drawn = await mount(surface)
      expect(drawn).toContain('Phase 3.23 · T1 (1 of 2)')
      expect(drawn).toContain('pa-expert')
      expect(drawn).toContain('#0087ff')
      expect(drawn).toContain(' T1 · c1')
      expect(drawn).toContain('  Edit calc/add.py · ')
      const mine = JSON.stringify(paPart(JSON.parse(drawn)) ?? null)
      expect(mine).toContain('Phase 3.23 · T1 (1 of 2)')
      expect(mine).not.toContain('engine')
      expect(await mount(surface, true)).toContain('engine')
    }
  })
})

// 3.29.9: the display trim. A task's first spawn marks its place in the main conversation; its done (routed or a review
// release) asks the watcher's `pa-trim` compaction to keep the rows from there on; a resumed start catches up.
describe('display trim (3.29.9)', () => {
  type Row = { role: 'user' | 'assistant'; text: string; toolUses: never[] }
  const row = (role: Row['role'], text: string): Row => ({ role, text, toolUses: [] })
  const BEFORE = [row('user', 'hello'), row('assistant', 'Sent.'), row('user', 'go on'), row('assistant', 'Going on.')]
  const AFTER = [...BEFORE, row('user', 'how is T1?'), row('assistant', 'Running.'), row('user', 'ok'), row('assistant', 'Done soon.')]
  const MARK = { sid: 'S1', task: 'T1', len: 4, tail: tailOf(BEFORE, 4), anchor: 'goon' } // H1.c2: the last user row, no whitespace
  const CUT = `pa-trim ${JSON.stringify({ k: 4, tail: tailOf(AFTER, 4) })}`
  type Seen = { rows: Row[]; calls: { instructions?: string; agentId?: string }[]; fail: string[] }
  /** The main conversation's rows and the compactions the router asks for, answered as the watcher would. */
  const main = (on: On, rows: Row[]): Seen => {
    const m: Seen = { rows, calls: [], fail: [] }
    on('session.messages', async () => ({ value: m.rows as never }))
    on('session.compact', async ($, e) => {
      m.calls.push({ instructions: e.instructions, agentId: e.agentId })
      const f = m.fail.shift()
      if (f !== undefined) {
        throw new Error(f)
      }
      const k = (JSON.parse((e.instructions ?? '').slice('pa-trim '.length)) as { k: number }).k
      return { messages: m.rows.slice(k) as never }
    })
    return m
  }
  const markIn = (w: W, key: string): unknown => (JSON.parse(w.files.get(LOOP) ?? '{}') as Record<string, unknown>)[key]
  const trimDebug = (w: W, kind: string): Array<Record<string, unknown>> => [...w.files].filter(([p]) => p.endsWith(`-autopilot.trim.${kind}.json`))
    .map(([, t]) => JSON.parse(t) as Record<string, unknown>)
  const doneAnswer = (w: W): void => {
    let done = false
    w.answer = argv => {
      const a = argv.slice(1).join(' ')
      if (a.startsWith('tools/plan_edit.py set-status')) {
        done = true
      }
      return done && a.startsWith('tools/plan_edit.py next') ? ok('{"kind": "none", "reason": "done", "waiting_on": []}\n') : defaultAnswer(argv)
    }
  }
  const DONE = notification('E1', 'STATUS: done\nLOG: logs/T1.md')
  const finish = async (clock: { advance: (ms: number) => Promise<unknown> }): Promise<void> => {
    await clock.advance(60_000) // the run ends after the quiet (RUN_QUIET_MS)
    await clock.advance(2_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
  }

  test('the first attempt marks its place; the routed done asks for the trim at it and journals it', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    const m = main(on, BEFORE)
    await $.session.start(START)
    await clock.advance(2_000)
    expect(markIn(w, 'trimMark')).toEqual(MARK)
    m.rows = AFTER
    doneAnswer(w)
    await $.prompt.submit({ text: DONE, origin: { kind: 'task-notification' }, wait: false })
    await finish(clock)
    expect(markIn(w, 'doneMark')).toEqual(MARK)
    expect(m.calls).toEqual([{ instructions: CUT, agentId: undefined }]) // the engine sets trigger `plugin`; the kit none
    const [note] = entries(w, 'mod-note')
    expect(w.files.get(note ?? '')).toContain('Display trimmed (done): the main conversation kept from T1\'s start, 4 rows dropped, 4 kept.')
  })

  test('a handoff keeps the first attempt\'s mark', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {})
    const m = main(on, BEFORE)
    await $.session.start(START)
    await clock.advance(2_000)
    m.rows = AFTER
    await $.turn.complete(complete('E1', 'Wrote the progress file.\n\nSTATUS: handoff\nLOG: logs/T1.md'))
    await finish(clock)
    expect([loopOf(w).task?.kind, loopOf(w).task?.attempt]).toEqual(['handoff', 2])
    expect(markIn(w, 'trimMark')).toEqual(MARK)
    expect(m.calls).toEqual([])
  })

  test('a relaunch keeps the mark it found', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const lf = { ...(JSON.parse(held('question', NOW - 61 * 60_000)) as Record<string, unknown>), trimMark: MARK }
    const w = world(on, { [LOOP]: JSON.stringify(lf) })
    main(on, AFTER)
    releaseReq(w, NOW, { hold: 'question', note: 'use ints' })
    await $.session.start(START)
    await clock.advance(2_000)
    expect(loopOf(w).task?.kind).toBe('relaunch')
    expect(markIn(w, 'trimMark')).toEqual(MARK)
  })

  test('a review release marks the task done and trims to it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const lf = { ...(JSON.parse(held('review', NOW - 60_000)) as Record<string, unknown>), trimMark: MARK }
    const w = world(on, { [LOOP]: JSON.stringify(lf) })
    const m = main(on, AFTER)
    w.answer = showStatus('done')
    releaseReq(w, NOW, { hold: 'review' })
    await $.session.start(START)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(markIn(w, 'doneMark')).toEqual(MARK)
    expect(m.calls.map(c => c.instructions)).toEqual([CUT])
    expect(w.files.get(entries(w, 'mod-note')[0] ?? '')).toContain('4 rows dropped, 4 kept')
  })

  for (const [name, mark] of [['another session\'s mark', { ...MARK, sid: 'S0' }], ['no mark', undefined]] as const) {
    test(`${name}: the done asks for no trim`, async ($, on) => {
      const clock = mock.clock(on, { now: 1_000 })
      const w = world(on, {})
      const m = main(on, BEFORE)
      await $.session.start(START)
      await clock.advance(2_000)
      const lp = JSON.parse(w.files.get(LOOP) ?? '{}') as Record<string, unknown>
      w.files.set(LOOP, JSON.stringify({ ...lp, trimMark: mark }))
      m.rows = AFTER
      doneAnswer(w)
      await $.prompt.submit({ text: DONE, origin: { kind: 'task-notification' }, wait: false })
      await finish(clock)
      expect(loopOf(w).task).toBe(null)
      expect(markIn(w, 'doneMark')).toBe(null)
      expect(m.calls).toEqual([])
      expect(entries(w, 'mod-note')).toEqual([])
    })
  }

  // The kit runs a throwing hook's dispatch on to its bottom, which rejects (no messages): a refusal, retried. The
  // headless refusal's skip is trimFailure's (trim.test.ts): the kit cannot raise the engine's own message.
  test('a refusal while a turn runs retries about every 2 s', async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    const w = world(on, {}, { PA_MOD_DEBUG: '1' })
    const m = main(on, BEFORE)
    await $.session.start(START)
    await clock.advance(2_000)
    m.rows = AFTER
    m.fail = ['a turn is running', 'a turn is running']
    doneAnswer(w)
    await $.prompt.submit({ text: DONE, origin: { kind: 'task-notification' }, wait: false })
    await finish(clock)
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(m.calls.length).toBe(3)
    expect(trimDebug(w, 'retry').map(d => d['tries'])).toEqual([1, 2])
    expect(entries(w, 'mod-note').length).toBe(1)
  })

  test('an interactive start catches up a trim that never ran', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ doneMark: MARK }) })
    const m = main(on, AFTER)
    await $.session.start(ISTART)
    await clock.advance(5_000)
    expect(m.calls.map(c => c.instructions)).toEqual([CUT])
    expect(w.files.get(entries(w, 'mod-note')[0] ?? '')).toContain('Display trimmed (start)')
    // H1.c3: a missing trim file is "not trimmed yet"; the trim records its mark there
    expect(JSON.parse(w.files.get(`${BASE}/trim.json`) ?? '{}')).toEqual({ sid: 'S1', task: 'T1', len: 4, at: NOW + 4_000 })
  })

  test('a start after the trim ran asks for nothing; a headless start never asks', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    world(on, { [LOOP]: loopAt({ doneMark: MARK }) })
    const m = main(on, AFTER.slice(4))
    await $.session.start(ISTART)
    await clock.advance(5_000)
    m.rows = AFTER
    await $.session.start(START)
    await clock.advance(5_000)
    expect(m.calls).toEqual([])
  })

  // H1.c3: a mark whose trim already ran is never searched again, even when its anchor text comes back later.
  test('a start skips a mark already trimmed; another task\'s record does not stop it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: loopAt({ doneMark: MARK }), [`${BASE}/trim.json`]: JSON.stringify({ sid: 'S1', task: 'T1', len: 4, at: 1 }) }, { PA_MOD_DEBUG: '1' })
    const m = main(on, [...AFTER.slice(4), row('user', 'go on'), row('assistant', 'Going on.'), row('user', 'later')])
    await $.session.start(ISTART)
    await clock.advance(5_000)
    expect(m.calls).toEqual([])
    expect(trimDebug(w, 'skip').map(d => d['reason'])).toEqual(['trimmed'])
    w.files.set(`${BASE}/trim.json`, JSON.stringify({ sid: 'S1', task: 'T0', len: 2, at: 1 }))
    m.rows = AFTER
    await $.session.start(ISTART)
    await clock.advance(5_000)
    expect(m.calls.map(c => c.instructions)).toEqual([CUT])
  })
})

// 3.32 B8: `drive`: coop is talk with no hold; the queue moves only for a locked-in batch, and nothing is said.
describe('the drive (3.32 B8)', () => {
  const PAC = JSON.stringify({ ...(JSON.parse(PA) as Record<string, unknown>), drive: 'coop' })
  const PAJ = `${ROOT}/.claude/pa.json`
  type LoopB = Loop5 & { batch?: string[] }
  const loopB = (w: W): LoopB => JSON.parse(w.files.get(LOOP) ?? '{}') as LoopB
  const req = (w: W, ms: number, kind: string, input: unknown): void => {
    const id = `${String(ms)}-1-${kind}`
    w.files.set(`${INBOX}/${id}.json`, JSON.stringify({ id, kind, caller: 'P1', input, at: ms }))
  }
  const rows = (...ids: string[]) => ok(`${ids.map(id => `${id} | queued | pa-expert | x`).join('\n')}\n`)
  /** A plan of T1, T2: `next --only` walks them, one a set-status; `show --tasks` lists them. */
  const twoTasks = (w: W): void => {
    let left = ['T1', 'T2']
    w.answer = argv => {
      const a = argv.slice(1).join(' ')
      if (a.startsWith('tools/plan_edit.py set-status')) {
        left = left.filter(id => id !== argv[3])
      }
      if (a.startsWith('tools/plan_edit.py show --tasks')) {
        return rows('T1', 'T2')
      }
      if (a.startsWith('tools/plan_edit.py next')) {
        return left.length === 0 ? NEXT_DONE : ok(`${JSON.stringify({ kind: 'task', task: { ...T1, id: left[0] }, promoted: null })}\n`)
      }
      return defaultAnswer(argv)
    }
  }
  const finish = async ($: { prompt: { submit: (e: never) => Promise<unknown> } }, clock: { advance: (ms: number) => Promise<void> }, id: string): Promise<void> => {
    await $.prompt.submit({ text: notification(id, 'STATUS: done\nLOG: logs/T.md\nCOMMIT: def5678'), origin: { kind: 'task-notification' }, wait: false } as never)
    await clock.advance(60_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
    await clock.advance(2_000)
  }

  test('coop: at a boundary with no batch the queue stays, no hold, no notice, no pick', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN })
    twoTasks(w)
    await $.session.start(START)
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(w.spawns).toEqual([])
    expect(ran(w, 'tools/plan_edit.py next')).toEqual([])
    expect(loopB(w).holds).toEqual([])
    expect(notices(w)).toEqual([])
  })

  test('autopilot: the same boundary picks with the old call and spawns', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN })
    twoTasks(w)
    await $.session.start(START)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py next')[0]).toEqual(['py', 'tools/plan_edit.py', 'next', '--brief', '--json'])
    expect(w.spawns.length).toBe(1)
  })

  test('coop: a session start holds nothing, and the developer\'s messages carry no hold, waiting or idle line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({ lastDone: { id: 'T1', at: NOW - 1 }, nextId: 'T2' }), [`${BASE}/S1/state.json`]: PRIOR })
    twoTasks(w)
    w.list = [{ id: 'P1', status: 'idle', type: 'pa-claude' }]
    await $.session.start(ISTART)
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'what about the seams?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    await $.prompt.submit({ text: 'and the masks?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    expect(loopB(w).holds).toEqual([])
    expect(delivered(w).some(t => t.includes('and the masks?'))).toBe(true)
    expect(delivered(w).filter(t => /‹pa› the (queue|loop)|no task is running|nothing is running/i.test(t))).toEqual([])
    expect(notices(w)).toEqual([])
    expect(w.spawns.filter(s => s.description.startsWith('T'))).toEqual([])
  })

  test('coop: a locked-in batch runs through, talk does not stop it, its end is one notice and everything stops', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN, [`${BASE}/S1/state.json`]: PRIOR })
    twoTasks(w)
    await $.session.start(START)
    await clock.advance(2_000)
    req(w, NOW + 2_500, 'release', { hold: 'batch', tasks: ['T1', 'T2'] })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py next')[0]).toEqual(['py', 'tools/plan_edit.py', 'next', '--brief', '--json', '--only', 'T1,T2'])
    expect(w.spawns.map(s => s.description.split(' ')[0])).toEqual(['T1'])
    await $.prompt.submit({ text: 'how is it going?', origin: { kind: 'composer' }, wait: false })
    await clock.advance(2_000)
    expect(loopB(w).holds).toEqual([])
    await finish($, clock, 'E1')
    const tasks = (): (string | undefined)[] => w.spawns.filter(s => /^T\d/.test(s.description)).map(s => s.description.split(' ')[0])
    expect(tasks()).toEqual(['T1', 'T2'])
    const second = `E${String(w.spawns.findIndex(s => s.description.startsWith('T2')) + 1)}`
    expect(notices(w)).toEqual([])
    await finish($, clock, second)
    const n = notices(w)
    expect(n.map(x => x.input.text)).toEqual(['‹pa› The batch is done (T1, T2). No task left a summary. Bring the results to the developer.'])
    expect(n[0]?.input.text).not.toMatch(/Is the milestone met|has no task left/)
    expect(loopB(w).batch).toEqual([])
    expect(loopB(w).holds).toEqual([])
    const picks = ran(w, 'tools/plan_edit.py next').length
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(ran(w, 'tools/plan_edit.py next').length).toBe(picks)
    expect(notices(w).length).toBe(1)
    expect(tasks().length).toBe(2)
  })

  test('coop: a batch names real tasks; autopilot refuses a batch', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN })
    twoTasks(w)
    await $.session.start(START)
    await clock.advance(2_000)
    req(w, NOW + 2_500, 'release', { hold: 'batch', tasks: ['T9'] })
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)).toEqual(['refused: no such task: T9; add it with plan_edit first'])
    expect(loopB(w).batch ?? []).toEqual([])
    w.files.set(PAJ, PA)
    req(w, NOW + 4_500, 'release', { hold: 'batch', tasks: ['T1'] })
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)[1]).toBe('refused: the drive is autopilot: the tasks run on their own')
  })

  test('coop: a plan_edit with nothing running lands at once, with no batch', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN })
    twoTasks(w)
    await $.session.start(START)
    await clock.advance(2_000)
    req(w, NOW + 2_500, 'plan_edit', { command: 'add-task --title "x" --done-when d', why: 'agreed' })
    await clock.advance(2_000)
    expect(ran(w, 'tools/plan_edit.py add-task').length).toBe(1)
    expect(loopB(w).queuedEdits).toEqual([])
    expect(notices(w)).toEqual([])
    expect(w.spawns).toEqual([])
  })

  test('coop: the phase closes on his word with no close hold, not over an open task or a running batch', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({}), [`${ROOT}/${PLAN}`]: PE_PLAN, [`${ROOT}/GENERATION_PLAN.md`]: genPlan('- 1.2 Next | status: open\n') })
    let open = true
    w.answer = closeAnswer(a => (a.startsWith('tools/plan_edit.py show --tasks') ? (open ? rows('T5') : ok('T1 | done | pa-expert | x\n')) : undefined))
    await $.session.start(START)
    await clock.advance(2_000)
    req(w, NOW + 2_500, 'release', { hold: 'close', note: 'report' })
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)).toEqual(['refused: T5 is still open: supersede it with plan_edit, or run it first'])
    expect(loopB(w).closeRequested ?? false).toBe(false)
    open = false
    req(w, NOW + 4_500, 'release', { hold: 'close', note: 'report' })
    await clock.advance(2_000)
    await clock.advance(2_000)
    expect(w.files.get(`${ROOT}/phase-ends/current/RECAP.md`)).toContain('report')
    expect(chainRuns(w).some(r => r.startsWith('tools/phaseend_index.py'))).toBe(true) // the chain ran on his word alone
    expect(notices(w).length).toBe(1)
  })

  test('coop: no plan under a generation plan: no plan hold and no nudge', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [PAJ]: PAC, [LOOP]: closeLoop({ state: 'off' }), [`${ROOT}/GENERATION_PLAN.md`]: genPlan('') })
    w.files.delete(`${ROOT}/${PLAN}`)
    w.answer = launchDoc({ mode: 'router' })
    await $.session.start(START)
    for (let i = 0; i < 4; i += 1) {
      await clock.advance(2_000)
    }
    expect(loopB(w).holds).toEqual([])
    expect(notices(w)).toEqual([])
  })

  test('the drive tool sets pa.json and commits it, quietly; the next boundary reads it with no restart', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const w = world(on, { [LOOP]: running() })
    w.list = [{ id: 'E1', status: 'running', type: 'pa-expert' }]
    await $.session.start(START)
    await clock.advance(2_000)
    req(w, NOW + 2_500, 'drive', { value: 'coop' })
    await clock.advance(2_000)
    expect((JSON.parse(w.files.get(PAJ) ?? '{}') as { drive?: string }).drive).toBe('coop')
    expect(commits(w).at(-1)).toEqual([BASH, 'tools/commit_task.sh', 'router', 'Drive: coop', '.claude/pa.json'])
    expect(notices(w)).toEqual([])
    expect(loopB(w).batch).toEqual(['T1']) // the running task comes back with its results
    req(w, NOW + 4_500, 'drive', { value: 'autopilot' })
    await clock.advance(2_000)
    expect((JSON.parse(w.files.get(PAJ) ?? '{}') as { drive?: string }).drive).toBe('autopilot')
    expect(loopB(w).batch).toEqual([])
    req(w, NOW + 6_500, 'drive', { value: 'nap' })
    await clock.advance(2_000)
    expect(notices(w).map(n => n.input.text)).toEqual(['refused: drive is autopilot or coop'])
  })
})
