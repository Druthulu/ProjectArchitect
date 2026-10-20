// The autopilot's loop state and its pure decisions (3.23-autopilot.md, "The loop (router `pa`)" and "Stops, holds and
// decisions"): `.run/mod/loop.json`, project-level, written at every transition and read first at session start. Its
// types live in the contract, types/index.d.ts. Pure, no `$`: autopilot.tsx reads the clock and files and acts.
import type { HoldName, LoopState, LoopStateName, LoopTask, Shell, WaitFor } from '../types'

import { parseExpertContract } from './contract'

export const LOOP_V = 1
export const LOOP_FILE = '.run/mod/loop.json'
/** The owner writes its heartbeat this often (3.23 T5d1). */
export const HEARTBEAT_MS = 15_000 // was 30_000 (autopilot.tsx)
/** The loop runs only in the owner session while its heartbeat is younger than this; older, a session takes it over. */
export const OWNER_STALE_MS = 45_000 // was 120_000; T5d1: three missed heartbeats
/**
 * A run has ended only this long after its last turn.complete (with no busy child and nothing queued to it). T5d1: a
 * `-p` run ended ~25 s after its expert's `done`, before 60 s of quiet, so the routing was never seen (T5c.c3 log).
 */
export const RUN_QUIET_MS = 15_000 // was 60_000
/**
 * With no STATUS yet (no `pendingContract`), a run has ended only after this much silence since the agent's last activity
 * (3.23 T11-fix: a closer ended a turn to wait on its own background shell; read as ended at 15 s, "no contract").
 */
export const NO_CONTRACT_QUIET_MS = 10 * 60_000
/** `talk` ends after this much developer silence, unless Claude's last reply asked something. */
export const TALK_SILENCE_MS = 15 * 60_000
/** A WAIT-FOR's file is polled this often (3.23 T5d2a; pa-session step 2). */
export const WAIT_POLL_MS = 30_000
/** A WAIT-FOR not met this long goes to the review stop. */
export const WAIT_MAX_MS = 50 * 60_000
/** A forward with no turn.complete after it stops counting as queued after this (3.23 T5d2a.c2: undelivered, parent gone). */
export const FORWARD_TTL_MS = 5 * 60_000

const STATES: readonly LoopStateName[] = ['off', 'boundary', 'running', 'routing', 'closing']
const HOLDS: readonly HoldName[] = ['talk', 'review', 'question', 'plan', 'close', 'window', 'stopped', 'generation', 'session']

export function defaultLoop(): LoopState {
  return {
    v: LOOP_V, ownerSid: '', heartbeatAt: 0, state: 'off', holds: [], task: null, queuedEdits: [], chainStep: '',
    dialog: false, waitFor: null, resumeAt: 0, lastDevAt: 0, lastAnswerAt: 0, talkAnswerAt: 0, lastAnswerAsked: false,
  }
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

function parseTask(v: unknown): LoopTask | null {
  if (typeof v !== 'object' || v === null) {
    return null
  }
  const t = v as Record<string, unknown>
  if (str(t['id']) === '') {
    return null
  }
  return {
    ...t,
    id: str(t['id']), title: str(t['title']), agent: str(t['agent']), coder: str(t['coder']), effort: str(t['effort']),
    attempt: num(t['attempt']) || 1, kind: str(t['kind']) || 'task', agentId: str(t['agentId']), startSha: str(t['startSha']),
    spawnedAt: num(t['spawnedAt']), lastCompleteAt: num(t['lastCompleteAt']),
    contract: typeof t['contract'] === 'string' ? t['contract'] : null,
  }
}

function parseWaitFor(v: unknown): WaitFor | null {
  const w = typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null
  return w === null || str(w['task']) === '' || str(w['file']) === ''
    ? null
    : { task: str(w['task']), file: str(w['file']), literal: str(w['literal']), since: num(w['since']) }
}

/** A plan entry's `wait-for` (`<file> contains <literal>`, a quoted or backticked literal unwrapped); null when it is not one. */
export function waitSpec(spec: string): { file: string; literal: string } | null {
  const m = /^\s*`?(.+?)`?\s+contains\s+(.+?)\s*$/.exec(spec)
  if (m === null) {
    return null
  }
  const literal = (m[2] ?? '').replace(/^(["'`])(.*)\1$/, '$2')
  return literal === '' ? null : { file: m[1] ?? '', literal }
}

/** 3.24 T17b: whose model and effort an approved plan records (`plan_edit.py approve --planner`): the latest `pa-planner`
 * the presenting caller spawned (Claude briefs it, then presents its draft), else the caller itself. */
export function plannerIdOf(list: readonly { id: string; type: string; parentId?: string }[], caller: string): string {
  const mine = list.filter(a => a.type === 'pa-planner' && a.parentId === caller)
  return mine[mine.length - 1]?.id ?? caller
}

/** loop.json text as a LoopState: tolerant (a bad field takes its default, unknown fields are kept); default when absent or unreadable. */
export function parseLoop(text: string | null): LoopState {
  const d = defaultLoop()
  if (text === null) {
    return d
  }
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return d
  }
  if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) {
    return d
  }
  const o = doc as Record<string, unknown>
  const state = STATES.find(s => s === o['state']) ?? d.state
  const holds = Array.isArray(o['holds']) ? (o['holds'] as unknown[]).filter((h): h is HoldName => HOLDS.includes(h as HoldName)) : []
  const queued = Array.isArray(o['queuedEdits']) ? (o['queuedEdits'] as unknown[]) : []
  return {
    ...o,
    v: LOOP_V, ownerSid: str(o['ownerSid']), heartbeatAt: num(o['heartbeatAt']), state, holds, task: parseTask(o['task']),
    queuedEdits: queued.flatMap(q => {
      const e = typeof q === 'object' && q !== null ? (q as Record<string, unknown>) : {}
      return str(e['command']) === '' ? [] : [{ command: str(e['command']), why: str(e['why']), at: num(e['at']), caller: str(e['caller']) }]
    }),
    chainStep: str(o['chainStep']), dialog: o['dialog'] === true, waitFor: parseWaitFor(o['waitFor']), resumeAt: num(o['resumeAt']),
    lastDevAt: num(o['lastDevAt']), lastAnswerAt: num(o['lastAnswerAt']),
    talkAnswerAt: num(o['talkAnswerAt'] ?? o['lastAnswerAt']), lastAnswerAsked: o['lastAnswerAsked'] === true,
  }
}

export function serializeLoop(s: LoopState): string {
  return `${JSON.stringify(s, null, 1)}\n`
}

/** The hold on top of the stack, or null. */
export function topHold(s: LoopState): HoldName | null {
  return s.holds[s.holds.length - 1] ?? null
}

/** Pushes a hold once: `talk` overlays any other (always on top); another hold goes beneath a `talk`. */
export function pushHold(s: LoopState, hold: HoldName): LoopState {
  if (s.holds.includes(hold)) {
    return s
  }
  const holds = [...s.holds]
  if (hold !== 'talk' && holds[holds.length - 1] === 'talk') {
    holds.splice(holds.length - 1, 0, hold)
  } else {
    holds.push(hold)
  }
  return { ...s, holds }
}

/** Removes the named hold wherever it sits. */
export function popHold(s: LoopState, hold: HoldName): LoopState {
  return s.holds.includes(hold) ? { ...s, holds: s.holds.filter(h => h !== hold) } : s
}

/**
 * This session runs the loop: it owns it, nobody does, or the owner's heartbeat is older than OWNER_STALE_MS. T13b.c4:
 * or the owner is `prevSid`, this process's session before a `/clear` (its heartbeat is this process's own).
 */
export function ownsLoop(s: LoopState, sid: string, now: number, prevSid = ''): boolean {
  return s.ownerSid === sid || s.ownerSid === '' || (prevSid !== '' && s.ownerSid === prevSid) || now - s.heartbeatAt > OWNER_STALE_MS
}

/**
 * This session takes the loop over (T5d1): it runs it, and the owner is another session or the heartbeat is stale (a
 * restart under the same id). A takeover with a task running and no stop runs recovery.
 */
export function takesOver(s: LoopState, sid: string, now: number, prevSid = ''): boolean {
  return ownsLoop(s, sid, now, prevSid) && (s.ownerSid !== sid || now - s.heartbeatAt > OWNER_STALE_MS)
}

/** 3.28.1 T15: background shells (Bash `run_in_background`), recorded by pa-watch per agent in
 * `.run/mod/<sid>/shells.json` (`{ <agentId>: Shell[] }`) from start to completion notice; `exit` set when it ended badly. */
export const SHELLS_FILE = 'shells.json'

/** shells.json's records by agent, each well-formed; {} for a missing or broken file. */
export function shellsOf(text: string | null): Record<string, Shell[]> {
  let v: unknown
  try {
    v = JSON.parse(text ?? '')
  } catch {
    return {}
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return {}
  const out: Record<string, Shell[]> = {}
  for (const [agentId, list] of Object.entries(v as Record<string, unknown>)) {
    const shells = (Array.isArray(list) ? list : []).flatMap((x: unknown) => {
      const j = typeof x === 'object' && x !== null ? (x as Record<string, unknown>) : {}
      if (str(j['id']) === '') return []
      const sh: Shell = { id: str(j['id']), name: str(j['name']), at: num(j['at']), type: str(j['type']) }
      if (typeof j['exit'] === 'number') sh.exit = j['exit']
      return [sh]
    })
    if (shells.length > 0) out[agentId] = shells
  }
  return out
}

/** What a hand-back or answer waits on (`WAITING: shell <what is running>`), null when it is not a shell wait. */
export function waitingShell(text: string): string | null {
  const m = /^\s*WAITING:\s*shell\b[ \t:]*(.*)/i.exec(text)
  return m === null ? null : (m[1] ?? '').trim()
}

/** The live shells (no `exit`) of these agents. */
export function shellsOut(shells: Record<string, Shell[]>, ids: string[]): Shell[] {
  return ids.flatMap((id) => (shells[id] ?? []).filter((s) => s.exit === undefined))
}

/** The spinner's frames; the frame comes from the clock. */
export const SPINNER = ['◐', '◓', '◑', '◒'] as const

/** The note Claude gets when a task's expert or coder hands back to wait on its background shell (3.28.1 T15 part d). */
export const shellNotice = (task: string, role: string, id: string, what: string): string =>
  `‹pa› ${task}'s ${role} (${id}) is waiting on a background shell${what === '' ? '' : `: ${what}`}. It picks up by itself ` +
  'when the shell ends. Tell the developer in one plain line: what is running, for which task, and that it picks up by itself.'

/** The text recovery sends the running task's expert (T5d1). */
export const CONTINUE_TEXT = 'continue: the session restarted; continue your task from your last step; your brief and the plan are unchanged'

/**
 * The text `release(stopped)` sends the stopped agent to resume it under the same id (3.23 T11-fix2): a stop is the
 * developer's (Ctrl+C), so the loop never resumes one on its own.
 */
export function stoppedText(note: string): string {
  return `continue: the developer stopped you and now wants you to go on.${note.trim() === '' ? '' : ` ${note.trim()}`} ` +
    'Check what your coder left (git status, git diff) before continuing; a coder stopped mid-edit may have left partial changes.'
}
/** A loop agent absent from `$.agent.list()` this long after its spawn counts as killed (T11-fix2). */
export const ABSENT_MS = 15_000

/**
 * A loop agent's liveness from its `$.agent.list()` status (T11-fix2; engine AgentStatus): `pending`, `running` and
 * `waiting` (on its own background work) are busy, never ended; `idle` and `completed` go to runEnded's rules;
 * `killed` and `failed` are stopped, and so is an agent absent from the list ABSENT_MS after its spawn.
 */
export function liveness(status: string | undefined, sinceSpawn: number): 'busy' | 'idle' | 'stopped' {
  if (status === undefined) {
    return sinceSpawn > ABSENT_MS ? 'stopped' : 'busy'
  }
  return status === 'killed' || status === 'failed' ? 'stopped' : status === 'idle' || status === 'completed' ? 'idle' : 'busy'
}

/** The running-now note's word for an agent's status (T11-fix2): `stopped`, `waiting`, else '' (at work or idle). */
export function statusWord(status: string | undefined): string {
  return status === undefined || status === 'killed' || status === 'failed' ? 'stopped' : status === 'waiting' ? 'waiting' : ''
}

/**
 * The task's run has ended: no busy child, nothing queued to it, and RUN_QUIET_MS since its last turn.complete (one seen).
 * T5d2a.c2: and RUN_QUIET_MS since its children went idle (`childIdleAt`): a coder's `completed` can be listed a tick
 * before its hand-back's forward request lands.
 * T11-fix: with no `pendingContract`, NO_CONTRACT_QUIET_MS since the latest of lastCompleteAt, childIdleAt and
 * `activityAt` (the watcher facts' `updatedAt`, reset by any step the agent takes).
 * 3.24 T13A: `handbackAt`, the agent's hand-back time (0 none): once it is in, no RUN_QUIET_MS wait from the last turn
 * (round 2: 15 s of silence after the curator's hand-back); the wait stays where no hand-back arrived, and from children
 * that went idle after it (their hand-back's forward may still land).
 */
export function runEnded(task: LoopTask, childrenBusy: number, queuedToIt: number, now: number, activityAt = 0, handbackAt = 0): boolean {
  const idleAt = task.childIdleAt ?? 0
  const quiet = childrenBusy === 0 && queuedToIt === 0 && task.lastCompleteAt > 0 &&
    (handbackAt > 0 || now - task.lastCompleteAt >= RUN_QUIET_MS) &&
    ((handbackAt > 0 && handbackAt >= idleAt) || now - idleAt >= RUN_QUIET_MS)
  return quiet && ((task.pendingContract ?? null) !== null ||
    now - Math.max(task.lastCompleteAt, task.childIdleAt ?? 0, activityAt) >= NO_CONTRACT_QUIET_MS)
}

/**
 * Messages queued to the task's expert (3.23 T5c): a `steer` sent after its last turn.complete counts 1 until the next;
 * T5d2a: so does the router's forward of its child's hand-back (a revived run read as ended before it, "no contract"),
 * for at most FORWARD_TTL_MS (T5d2a.c2: an undelivered forward never gets its turn.complete).
 */
export function queuedTo(task: LoopTask, now: number): number {
  const fwd = task.forwardAt ?? 0
  return ((task.steerAt ?? 0) > task.lastCompleteAt ? 1 : 0) + (fwd > task.lastCompleteAt && now - fwd < FORWARD_TTL_MS ? 1 : 0)
}

/** The text a `steer` sends the running task's expert. */
export function steerText(text: string): string {
  return `‹pa› A correction from the developer, relayed by Claude: ${text}\n` +
    'Work out what it means for your task, tell your running coder with SendMessage what to stop, keep or undo (never stop it), and log the correction under Deviations.'
}

/** 3.23 T13d.c3: a confirmation that only says done is not sent to Claude (each one wakes it for a turn): a bare
 * `released: review|generation|question`, `released: stopped (resumed)`, `queued: …`, a plain `applied: …`, `steered: T<n>`. Refusals,
 * `applied: … (not committed: …)`, `released: stopped (restarted, …)` and a release with added text are sent.
 * 3.24 T13A (decision 3): `released: talk|close` too are quiet: a release Claude made itself, nothing refused or changed
 * (round 2: `released: talk` woke Claude for a reply that repeated its answer). The 3.23 record (`fbd6850f`) sent them
 * only because c3's brief did not list them; it gives no reason. */
export function quietReply(line: string): boolean {
  const l = line.trim()
  if (l.includes('\n')) {
    return false
  }
  return /^released: (review|generation|question|session|talk|close)$/.test(l) || l === 'released: stopped (resumed)' || /^queued: /.test(l) ||
    (/^applied: /.test(l) && !l.includes('(not committed: ')) || /^steered: \S+$/.test(l)
}

export type Route = 'done' | 'handoff' | 'review' | 'question' | 'block'

/** The route of an expert's contract text: a missing or unparseable one, `blocked` or any other status is a block. */
export function routeStatus(contract: string | null): Route {
  const c = contract === null ? null : parseExpertContract(contract)
  switch (c?.status) {
    case 'done':
      return 'done'
    case 'handoff':
      return 'handoff'
    case 'review':
      return 'review'
    case 'question':
      return 'question'
    default:
      return 'block'
  }
}

export type Release = { ok: true; state: LoopState; note: string } | { ok: false; refused: string }

/** `release({hold, note})`: ends the named hold; `review` is refused while the task is still open. */
export function releaseHold(s: LoopState, r: { hold: HoldName; note: string }, taskOpen: boolean): Release {
  if (!s.holds.includes(r.hold)) {
    return { ok: false, refused: `not held: ${r.hold}` }
  }
  if (r.hold === 'review' && taskOpen) {
    return { ok: false, refused: `mark ${s.task?.id ?? 'the task'} done or reopen it first` }
  }
  return { ok: true, state: popHold(s, r.hold), note: r.note }
}

/** A `plan_edit` now is a decision on the stop (applied at once), not a change queued for the boundary. */
export function editIsDecision(s: LoopState): boolean {
  return s.holds.includes('review') || s.holds.includes('question')
}

/**
 * Silence ends `talk` (the only hold it ends): on top, 15 min since the developer's last message and Claude's last
 * answer to him (3.28.1 T9b: an answer after a mod notice does not count), and that answer asked nothing.
 */
export function silenceReleases(s: LoopState, now: number): boolean {
  return topHold(s) === 'talk' && !s.lastAnswerAsked && now - Math.max(s.lastDevAt, s.talkAnswerAt) >= TALK_SILENCE_MS
}

/** The task after `running` from `plan_edit.py show --tasks` rows (`T3 | next | agent | …`): the first next or queued one; '' none. */
export function nextTaskOf(rows: string, running: string): string {
  for (const line of rows.split(/\r?\n/)) {
    const [id = '', status = ''] = line.split('|').map(c => c.trim())
    if (/^T\d/.test(id) && id !== running && (status === 'next' || status === 'queued')) {
      return id
    }
  }
  return ''
}

/** The `nextId` the loop keeps in loop.json (an extra field, set at each spawn); '' none. */
export const nextIdOf = (s: LoopState): string => {
  const n = (s as LoopState & { nextId?: unknown }).nextId
  return typeof n === 'string' ? n : ''
}

/** The running task's place in the plan from `plan_edit.py show --tasks` rows: `[n, m]`, n its row among the m task rows; null unlisted. */
export function taskPosOf(rows: string, running: string): [number, number] | null {
  const ids = rows.split(/\r?\n/).map(l => (l.split('|')[0] ?? '').trim()).filter(id => /^T\d/.test(id))
  const n = ids.indexOf(running)
  return n < 0 ? null : [n + 1, ids.length]
}

/** The `taskPos` the loop keeps in loop.json (an extra field, set at each spawn like `nextId`, 3.23 T13); null none. */
export const taskPosIn = (s: LoopState): [number, number] | null => {
  const p = (s as LoopState & { taskPos?: unknown }).taskPos
  return Array.isArray(p) && p.length === 2 && p.every(v => typeof v === 'number' && Number.isFinite(v)) ? [p[0] as number, p[1] as number] : null
}

/** The band's loop line (3.23 T13): `Phase <p> · <task> (n of m) · held: <holds>`, the close step (`close: assemble`) in place of
 * the task while the chain runs; '' when the loop is off, or has no task, hold or close step. */
export function bandLine(s: LoopState, phase: string): string {
  if (s.state === 'off' || (s.task === null && s.holds.length === 0 && s.chainStep === '')) {
    return ''
  }
  const parts = [`Phase ${s.chainPhase || phase || '?'}`]
  const pos = taskPosIn(s)
  if (s.chainStep !== '') {
    parts.push(`close: ${s.chainStep}`)
  } else if (s.task !== null) {
    parts.push(pos === null ? s.task.id : `${s.task.id} (${String(pos[0])} of ${String(pos[1])})`)
  }
  if (s.holds.length > 0) {
    parts.push(`held: ${s.holds.join(', ')}`)
  }
  return parts.join(' · ')
}

/**
 * The line the router puts before the developer's first message while the loop is active and `talk` is not held yet;
 * null otherwise. 3.23 T13d.c11: while `session` is held (a session start; nothing has started since), the waiting line
 * instead, on each developer message until the release.
 */
export function talkLine(s: LoopState): string | null {
  if (s.holds.includes('session')) {
    const what = s.task !== null ? s.task.id : s.state === 'closing' ? 'the phase close' : s.holds.includes('generation')
      ? "the next generation's start" : 'the next step'
    return `‹pa› the loop is waiting for you: nothing has started or continued since this session began (waiting: ${what}). ` +
      'Call release({hold: "session"}) only when the developer asks for the work to go on. A question about where ' +
      'things stand, or any other talk, is not that: just answer it.'
  }
  if (s.state === 'off' || s.holds.includes('talk')) {
    return null
  }
  const running = s.task === null ? 'no task' : s.task.id
  return `‹pa› the queue is held while you talk (${running} running; next: ${nextIdOf(s) || 'none listed'}). ` +
    'Call release({hold: "talk"}) when the conversation is done or does not touch the plan.'
}

/** `held(window)` is over: its `resume_at` has passed. */
export function windowResumes(s: LoopState, now: number): boolean {
  return s.holds.includes('window') && s.resumeAt > 0 && now >= s.resumeAt
}

/** The agent to spawn for a plan entry's column (3.25 T6b: one agent per role): no column, or an old expert type
 * (`expert-opus55`, `expert-fable`, their `-5m` twins), is `pa-expert`; another column passes through, a `-5m` twin as
 * its 1h type. The effort is the spawn's `[effort <level>]` hint (effortFor), not the type. */
export function agentFor(column: string | null, _effort: string | null): string {
  const c = (column ?? '').trim()
  if (c === '' || c === '-' || c === '—' || /^expert-/.test(c)) {
    return 'pa-expert'
  }
  return c.endsWith('-5m') ? c.slice(0, -3) : c
}

/** A plan entry's effort: its own, else high for an old `expert-fable…` column, else medium (3.25 T6b). */
export function effortFor(column: string | null, effort: string | null): string {
  const e = (effort ?? '').trim()
  if (e !== '') {
    return e
  }
  return /^expert-fable/.test((column ?? '').trim()) ? 'high' : 'medium'
}

/** A spawn description's trailing `[effort <level>]` hint (3.25 T6b): the watcher resolves the row from it; medium (the base
 * row) goes without one. */
export const effortHint = (effort: string): string => `[effort ${effort}]`

// 3.23 T6a1: an approved generation or phase plan being installed (loop.json `install`, an extra field), a step a tick so
// a crash resumes at the recorded step: `start` (a generation plan written and committed; a phase draft linted),
// `from-draft`, `approve`, `asks` (one ask per rules line, then per candidates line; `asked` the request id, `answer`
// its answer, null while owed), `commit`.
export type Install = {
  kind: 'generation' | 'phase'
  path: string
  title: string
  rules: string[]
  candidates: string[]
  caller: string
  step: 'start' | 'from-draft' | 'approve' | 'asks' | 'commit'
  i: number
  asked: string
  answer: string | null
  applied: string[]
  phase: string
}

const STEPS: readonly Install['step'][] = ['start', 'from-draft', 'approve', 'asks', 'commit']
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

/** loop.json's install in progress, null none. */
export function installOf(s: LoopState): Install | null {
  const v = (s as LoopState & { install?: unknown }).install
  if (typeof v !== 'object' || v === null) {
    return null
  }
  const o = v as Record<string, unknown>
  const kind = o['kind'] === 'generation' || o['kind'] === 'phase' ? o['kind'] : null
  if (kind === null || str(o['path']) === '') {
    return null
  }
  return {
    kind, path: str(o['path']), title: str(o['title']), rules: strs(o['rules']), candidates: strs(o['candidates']), caller: str(o['caller']),
    step: STEPS.find(x => x === o['step']) ?? 'start', i: num(o['i']), asked: str(o['asked']),
    answer: typeof o['answer'] === 'string' ? o['answer'] : null, applied: strs(o['applied']), phase: str(o['phase']),
  }
}

export const RULE_OPTIONS: readonly string[] = ['add (Recommended)', 'reject']
export const CANDIDATE_OPTIONS: readonly string[] = ['build this phase', 'defer', 'drop']

/** A lint run's ERROR lines, joined; '' none. */
export function lintErrors(out: string): string {
  return out.split(/\r?\n/).map(l => l.trim()).filter(l => l.startsWith('ERROR')).join('; ')
}

/** A rules line (`M13: <title>`, else the title alone under `nextId`) as `rules_add.py add` arguments. */
export function ruleArgs(line: string, nextId: string, origin: string): string[] {
  const m = /^\s*(?:-\s*)?([A-Za-z]+\d+(?:\.\d+)?)\s*[:|—]\s*(.+)$/.exec(line)
  const id = m?.[1] ?? nextId
  const title = (m?.[2] ?? line.replace(/^\s*-\s*/, '')).trim()
  return ['add', '--id', id, '--title', title, '--origin', origin]
}

/** The next free `M<n>` among rule file names. */
export function nextRuleId(names: readonly string[]): string {
  const ns = names.map(n => /^M(\d+)\.md$/.exec(n)?.[1]).filter((x): x is string => x !== undefined).map(Number)
  return `M${String(ns.length === 0 ? 1 : Math.max(...ns) + 1)}`
}

/** An audit candidates line (`- Tool candidate: <name> | does: … | replaces: … | …`): its name, what it does, what it replaces. */
export function candidateOf(line: string): { name: string; does: string; replaces: string } {
  const body = line.replace(/^\s*-?\s*(?:Tool|Rule)\s+candidate:\s*/i, '').trim()
  const fields = body.split('|').map(f => f.trim())
  const name = fields[0] ?? body
  const get = (k: string): string => fields.find(f => f.startsWith(`${k}:`))?.slice(k.length + 1).trim() ?? ''
  return { name, does: get('does') || name, replaces: get('replaces') || `${name} is in place` }
}

// 3.23 T6a2: the generation start (launch.py mode `planner-gen`) or the migration curate line (any mode), loop.json
// `genStart`, a step a tick: `inbox` (status.py inbox-consume, then launch.py --state-json for curate, audit_flag and
// deferred), `ask` (the curator ask, `asked` the request id, `answer` null while owed), `curator` and `audit` (a loop
// agent: `agentId` spawned, its last answer read at runEnded), `notice` (one notice to Claude), `done`. `sid`: the
// session it ran in (a pause is asked again next session).
export type GenStart = {
  kind: 'gen' | 'migration'
  sid: string
  step: 'inbox' | 'ask' | 'curator' | 'audit' | 'notice' | 'done'
  curate: string
  auditFlag: string
  deferred: { id: string; text: string }[]
  asked: string
  answer: string | null
  agentId: string
  spawnedAt: number
  lastCompleteAt: number
  childBusy: boolean
  childIdleAt: number
  recap: string
  audit: string
}

const GEN_STEPS: readonly GenStart['step'][] = ['inbox', 'ask', 'curator', 'audit', 'notice', 'done']

/** A fresh generation-start record. */
export function newGenStart(kind: GenStart['kind'], sid: string, curate = ''): GenStart {
  return {
    kind, sid, step: 'inbox', curate, auditFlag: '', deferred: [], asked: '', answer: null, agentId: '', spawnedAt: 0,
    lastCompleteAt: 0, childBusy: false, childIdleAt: 0, recap: '', audit: '',
  }
}

/** launch.py --state-json's `deferred` list. */
export function deferredOf(v: unknown): { id: string; text: string }[] {
  return (Array.isArray(v) ? (v as unknown[]) : []).flatMap(x => {
    const o = typeof x === 'object' && x !== null ? (x as Record<string, unknown>) : {}
    return str(o['id']) === '' ? [] : [{ id: str(o['id']), text: str(o['text']) }]
  })
}

/** loop.json's generation start, null none. */
export function genStartOf(s: LoopState): GenStart | null {
  const v = (s as LoopState & { genStart?: unknown }).genStart
  if (typeof v !== 'object' || v === null) {
    return null
  }
  const o = v as Record<string, unknown>
  const kind = o['kind'] === 'gen' || o['kind'] === 'migration' ? o['kind'] : null
  if (kind === null) {
    return null
  }
  return {
    kind, sid: str(o['sid']), step: GEN_STEPS.find(x => x === o['step']) ?? 'inbox', curate: str(o['curate']), auditFlag: str(o['auditFlag']),
    deferred: deferredOf(o['deferred']), asked: str(o['asked']), answer: typeof o['answer'] === 'string' ? o['answer'] : null,
    agentId: str(o['agentId']), spawnedAt: num(o['spawnedAt']), lastCompleteAt: num(o['lastCompleteAt']), childBusy: o['childBusy'] === true,
    childIdleAt: num(o['childIdleAt']), recap: str(o['recap']), audit: str(o['audit']),
  }
}

export const CURATE_OPTIONS: readonly string[] = ['Run it now (Recommended)', 'Pause']

/** 3.24 T7b: the project's first run in 3.2 from `.claude/pa.json` (T5's `first_run: {from, at}`, pending while
 * `migrated` ≠ "3.2"): the curator's line `curate: migration 3.2 -> gen 3.2 · FROM: <from> · MEMORY: <dir>`; '' none. */
export function firstRunLine(paText: string | null, memoryDir: string): string {
  let o: Record<string, unknown>
  try {
    const v: unknown = JSON.parse(paText ?? '')
    o = typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
  } catch {
    return ''
  }
  const fr = o['first_run']
  if (typeof fr !== 'object' || fr === null || o['migrated'] === '3.2') {
    return ''
  }
  return `curate: migration 3.2 -> gen 3.2 · FROM: ${str((fr as Record<string, unknown>)['from']) || 'unknown'} · MEMORY: ${memoryDir}`
}

/** A first-run curate line (firstRunLine). */
export const isFirstRun = (curate: string): boolean => /^curate: migration 3\.2\b/.test(curate)

/** 3.24 T7b-2c: why plans wait while a first run is pending (firstRunLine): Claude's seed line, and the refusal's reason
 * ("Not presented:", "not installed:"; all three reach Claude only). 3.24 T13g: names the mod as the one who asks. */
export const FIRST_RUN_WAITS = "The project's first run in PA 3.2 sorts its memories first: no plan is presented or installed " +
  'until the memory curator has run. The mod asks the developer itself, in its own picker; do not ask about the curator ' +
  "yourself. If they pause it, the mod asks again at the next session start. Relay the curator's recap when it arrives."

/** The curator ask's question (pa-session.md:86-89); 3.24 T7b: the project's first run in 3.2 says so. */
export const curateQuestion = (curate: string): string => isFirstRun(curate)
  ? `This project's first run in PA 3.2: the memory curator sorts its memories once (${curate}), card first, ` +
    'memory kept lean, the rest archived, and recaps after. Run it now?'
  : `The memory curator runs at every generation start (${curate}). Run it now?`

/** Where the curator archives demoted memories: `gen<G>.md` (G the closing generation); for a migration the line's
 * target (`-> gen 3.2`: `gen3.2.md`, 3.24 T7b), else `genlegacy.md`. */
export function archiveOf(curate: string): string {
  const g = /closing gen\s+(\d+)/.exec(curate)?.[1]
  const m = /migration.*?->\s*gen\s+([\w.]+)/.exec(curate)?.[1]
  return `.claude-state/memory/gen${/migration/.test(curate) ? (m ?? 'legacy') : (g ?? '<G>')}.md`
}

const natKey = (x: string): (string | number)[] => x.split(/(\d+)/).map((p, i) => (i % 2 === 1 ? Number(p) : p))
function natCmp(a: string, b: string): number {
  const x = natKey(a)
  const y = natKey(b)
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const p = x[i]
    const q = y[i]
    if (p === undefined || q === undefined) {
      return p === undefined ? -1 : 1
    }
    const c = typeof p === 'number' && typeof q === 'number' ? p - q : String(p).localeCompare(String(q))
    if (c !== 0) {
      return c
    }
  }
  return 0
}

/** The newest `PhaseEnd_*.md` among `phase-ends/` names (natural order), its phase id and the one before; null none. */
export function phaseEndsOf(names: readonly string[]): { newest: string; previous: string; phase: string } | null {
  const pes = names.flatMap(n => {
    const m = /^PhaseEnd_(?:Phase)?(.+)\.md$/.exec(n)
    return m === null ? [] : [{ name: n, phase: m[1] ?? '' }]
  }).sort((a, b) => natCmp(a.phase, b.phase))
  const last = pes[pes.length - 1]
  return last === undefined ? null : { newest: last.name, previous: pes[pes.length - 2]?.name ?? '', phase: last.phase }
}

/** The generation start's one notice to Claude: the recap and archive, the audit counts, the deferred items, the plan. */
export function genNotice(o: {
  kind: GenStart['kind']
  curate: string
  recap: string
  audit: { verdicts: string; fixed: string; candidates: string } | null
  auditText: string
  deferred: readonly { id: string; text: string }[]
}): string {
  const out = [o.kind === 'gen' ? '‹pa› Generation start.' : isFirstRun(o.curate)
    ? `‹pa› The project's first run in PA 3.2: the memory curator ran (${o.curate}). Relay its recap to the developer in plain words.`
    : `‹pa› The memory curator ran (${o.curate}).`]
  if (o.curate !== '') {
    out.push(`memory curator recap:\n${o.recap.trim() === '' ? '(no recap)' : o.recap.trim()}`,
      `archived memories: ${archiveOf(o.curate)} (tell the developer, in case he wants to keep something)`)
  }
  if (o.audit !== null) {
    out.push(`auditor: VERDICTS ${o.audit.verdicts || '?'}, FIXED ${o.audit.fixed || '?'}, CANDIDATES ${o.audit.candidates || '?'}; ` +
      'its candidates are in phase-ends/current/AUDIT.md; carry them in plan(candidates)')
  } else if (o.auditText.trim() !== '') {
    out.push(`auditor: no contract: ${o.auditText.trim().slice(0, 400)}`)
  }
  if (o.deferred.length > 0) {
    out.push(`deferred items:\n${o.deferred.map(d => `- ${d.id}: ${d.text}`).join('\n')}\n` +
      'settle these with the developer (tools/discussion.py triage) before drafting')
  }
  if (o.kind === 'gen') {
    out.push('a generation plan is needed: draft it with the pa-planning skill and present it with plan(kind: generation)')
  }
  return out.join('\n')
}

// 3.23 T6a2: drift at boundaries (pa-session.md:166-170): loop.json `drift`, one ask per `managed.py drift` row.
export type DriftRow = { rel: string; ids: string }
export type Drift = { rows: DriftRow[]; i: number; asked: string; answer: string | null; done: string[] }
export const DRIFT_OPTIONS: readonly string[] = ['stamp (Recommended)', 'decline']

/** `managed.py drift` output (`none`, or `<rel> | base … | <sha8> | edited in <ids>` lines) as rows. */
export function driftRows(out: string): DriftRow[] {
  return out.split(/\r?\n/).map(l => l.trim()).filter(l => l !== '' && l !== 'none' && l.includes('|')).map(l => {
    const f = l.split('|').map(x => x.trim())
    const ed = f.find(x => x.startsWith('edited in')) ?? ''
    return { rel: f[0] ?? '', ids: ed.replace(/^edited in\s*/, '') || '—' }
  }).filter(r => r.rel !== '')
}

export const driftQuestion = (r: DriftRow): string =>
  `${r.rel} differs from the installed copy (edited in ${r.ids}). Keep it as your version?`

/** loop.json's drift asks in progress, null none. */
export function driftOf(s: LoopState): Drift | null {
  const v = (s as LoopState & { drift?: unknown }).drift
  if (typeof v !== 'object' || v === null) {
    return null
  }
  const o = v as Record<string, unknown>
  const rows = (Array.isArray(o['rows']) ? (o['rows'] as unknown[]) : []).flatMap(x => {
    const r = typeof x === 'object' && x !== null ? (x as Record<string, unknown>) : {}
    return str(r['rel']) === '' ? [] : [{ rel: str(r['rel']), ids: str(r['ids']) }]
  })
  return rows.length === 0
    ? null
    : { rows, i: num(o['i']), asked: str(o['asked']), answer: typeof o['answer'] === 'string' ? o['answer'] : null, done: strs(o['done']) }
}

// 3.23 T6a2: hook-context lines (session_start.py's update lines, renewal.py's renewal line).
const CONTEXT_RE = /PA3: (?:update available|the installed copy|this project's files|the plan renewal day)/

/** The update and renewal lines in a hook-context row's text, each from its `PA3:` on. */
export function contextLines(text: string): { kind: 'update' | 'renewal'; line: string }[] {
  return text.split(/\r?\n/).flatMap(l => {
    const m = CONTEXT_RE.exec(l)
    if (m === null) {
      return []
    }
    const line = l.slice(m.index).trim()
    return [{ kind: line.startsWith('PA3: the plan renewal day') ? 'renewal' as const : 'update' as const, line }]
  })
}

/** 3.28.6 T19: an update line's fact without its steps (`Upgrade: auto: … run …`, `Say \`update pa3\` to run …`): in a
 * `mod: on` session the mod owns the update, so neither Claude nor the journal's seed gets a pre-3.2 router's command. */
export function updateFact(line: string): string {
  const i = line.search(/\s(?:Upgrade: |Say `update pa3`)/)
  return i < 0 ? line : line.slice(0, i).trim()
}

// 3.24 T6: the update ask is on in production, so this notice (a trial without PA_MOD_UPDATE=1) only reports the line.
export const updateNotice = (line: string): string =>
  `‹pa› PA3 says: ${line.replace(/\.\s*$/, '')}. Mention it to the developer once.`
export const renewalNotice = (line: string): string =>
  `‹pa› ${line} Ask the developer for the day once this session, then run the line's command with the day in place of <day>.`

// The update ask (3.24 T6: on in production, PA_MOD_PA3_DIR empty; a trial forces it with PA_MOD_UPDATE=1): loop.json `update`.
export type UpdateAsk = { line: string; asked: string; answer: string | null }
export const UPDATE_OPTIONS: readonly string[] = ['Update now (Recommended)', 'Later']

/** loop.json's update ask, null none. */
export function updateOf(s: LoopState): UpdateAsk | null {
  const v = (s as LoopState & { update?: unknown }).update
  const o = typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null
  return o === null || str(o['line']) === ''
    ? null
    : { line: str(o['line']), asked: str(o['asked']), answer: typeof o['answer'] === 'string' ? o['answer'] : null }
}

/** 3.28.1 T14: a running session checks for a newer release this often (the session start checks too). */
export const RELEASE_CHECK_MS = 8 * 3_600_000
/** The check's release (`{version, kind, state}`, state `later` | `install`) for the statusline's green line
 * (pa/statusline.py): "available", "installs after the current task", then "installed" once the installed VERSION
 * reaches it. The mod empties it when it starts. */
export const RELEASE_MARKER = '.run/mod/release.json'
/** The restart that loads an installed update: a resume is a new process (the mod, the agents, the session-start
 * check all load) and keeps the session id; /clear starts none of it. */
export const RESTART = '/exit, then claude --continue (not /clear); you\'ll pick up where you left off'
/** 3.28.2 T16: the restart line Claude relays on its own line, after a blank line, in bold. */
export const RESTART_LINE = '**To load it: /exit, then claude --continue (not /clear)**'
/** 3.28.2 T16: the first start after an installed release: Claude's first line, so the restart doesn't read as a loop. */
export const loadedNotice = (version: string): string =>
  `‹pa› PA3 ${version} was installed and this start loaded it. Say this one line first, before anything else:\n\n` +
  `**Project Architect ${version} is installed and loaded. Ready to continue work.**\n\nThen carry on as usual.`
/** 3.29.1 T8: the start hook's note that this session start's update check has answered (pa/hooks/session_start.py
 * `_start_checked`): `{session, at (s), update}`. */
export const START_CHECK = '.run/mod/start-check.json'
/** 3.29.1 T8: how long work waits for that note before going on (a broken hook must not freeze the project). */
export const START_CHECK_WAIT_MS = 120_000
/** A note this much older than the mod's session start belongs to an earlier start of the same session (a resume). */
const START_CHECK_SKEW_MS = 60_000
/** 3.29.1 T8: this start's check: `pending` (no answer yet), `update` (it started an update run), `done`, or `timeout`. */
export function startCheckState(text: string | null, sid: string, startAt: number, now: number): 'pending' | 'update' | 'done' | 'timeout' {
  let doc: unknown = null
  try {
    doc = text === null ? null : JSON.parse(text)
  } catch {
    doc = null
  }
  const o = typeof doc === 'object' && doc !== null ? (doc as Record<string, unknown>) : {}
  const at = typeof o['at'] === 'number' ? o['at'] * 1000 : 0
  if (o['session'] === sid && at >= startAt - START_CHECK_SKEW_MS && o['state'] !== 'checking') { // 3.29.2: still checking
    return o['update'] === true ? 'update' : 'done'
  }
  return now - startAt >= START_CHECK_WAIT_MS ? 'timeout' : 'pending'
}
/** 3.29.1 T9: a task paused on the developer's request, found at a session start once the update check let work go on. */
export const pausedNotice = (id: string): string =>
  `‹pa› ${id} was paused on the developer's request before the last session ended, and it has not resumed. Ask them in ` +
  'one line, such as "The session was paused on your request. Ready to resume?"; on their yes, in any words, call ' +
  'release({hold: "stopped"}).'
/** 3.29 T6: the one-time `.run/` cleanup's marker: offered once per project, at its first start on 3.29. */
export const RUN_REVIEW_MARKER = '.run/mod/run-review.json'
/** PA3's own `.run/` entries, never older work. */
const RUN_OWN = new Set(['mod', 'status.json', 'health.json', 'plans', 'PHASE_PLAN.draft.md', 'GENERATION_PLAN.draft.md'])
/** 3.29 T6: whether `.run/` holds work from before 3.29: an entry that is not PA3's own, or run.sh logs in `.run/logs/`. */
export const olderRunWork = (names: readonly string[], logsCount: number): boolean =>
  names.some(n => !RUN_OWN.has(n) && n !== 'logs') || logsCount > 0
/** 3.29 T6: Claude offers the one-time cleanup (agents/pa-claude.md, "The one-time `.run/` cleanup"). */
/** 3.29.3: the mod's own picker for the one-time cleanup (the recommended answer first). */
export const RUN_REVIEW_QUESTION = 'PA3 3.29 added a cleanup step to every phase end: each phase keeps its temp work in ' +
  '.run/<phase>/, and the phase close clears it. This project\'s .run/ still holds work from before 3.29, which that ' +
  'step never covers. A one-time cleanup is highly recommended: Claude reviews it folder by folder and shows you what it ' +
  'would keep, rebuild or delete, with sizes. Nothing is deleted until you say yes. Clean it up?'
export const RUN_REVIEW_OPTIONS: readonly string[] = ['Clean it up (Recommended)', 'Not now']
/** 3.29.3: the developer's answer, to Claude (agents/pa-claude.md, "The one-time `.run/` cleanup"). */
/** 3.29.8: the cleanup's list (Claude writes it), the picker's ask (the mod writes it), the picker. */
export const CLEANUP_LIST = '.run/mod/cleanup.json'
export const CLEANUP_ASK = '.run/mod/cleanup-ask.json'
export const CLEANUP_OPTIONS: readonly string[] = ['Delete them', 'Not now']
export const cleanupQuestion = (summary: string, checked: string): string =>
  `Delete the items Claude listed above${summary === '' ? '' : ` (${summary})`}? ${checked}. Only those items are ` +
  'deleted, each by name; nothing outside .run/ is touched.'
/** 3.29.7: work waits for the developer's go after the update's after-restart work. */
const CONTINUE_ASK = ' No work resumes until they say so: ask "Ready to continue work?", and on their yes, in any words, ' +
  'call release({hold: "session"}), and release({hold: "stopped"}) too when a task is held.'
export const runReviewNotice = (go: boolean): string => go
  ? '‹pa› The developer chose the one-time `.run/` cleanup in the mod\'s picker. Do it now, as your instructions say: ' +
    'review, then show them what you would keep, rebuild or delete; write exactly the items to delete to ' +
    '.run/mod/cleanup.json and call cleanup({summary}): the mod asks them with its picker and deletes on their yes ' +
    '(never delete anything yourself). When the cleanup is done (or they stop it), the update\'s after-restart work ' +
    'is done.' + CONTINUE_ASK
  : '‹pa› The developer put off the one-time `.run/` cleanup; do nothing about it unless they ask, it is not asked again. ' +
    'The update\'s after-restart work is done.' + CONTINUE_ASK
/** loop.json `release`: the check's release, asked once per version (`asked` the picker's request id); `step` asked →
 * install (the developer's yes) → running (pa3_update.py started) → done, or later (not now, or no answer). */
export type Release = { version: string; kind: string; asked: string; step: 'asked' | 'install' | 'running' | 'later' | 'done' }
export const RELEASE_OPTIONS: readonly string[] = ['Install after the current task', 'Not now (I\'m away or busy)']

/** loop.json's release record, null none. */
export function releaseOf(s: LoopState): Release | null {
  const v = (s as LoopState & { release?: unknown }).release
  const o = typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null
  const step = str(o?.['step'])
  return o === null || str(o['version']) === '' || !['asked', 'install', 'running', 'later', 'done'].includes(step)
    ? null
    : { version: str(o['version']), kind: str(o['kind']) || 'hotfix', asked: str(o['asked']), step: step as Release['step'] }
}

/** The release picker's question: the release, its title, what "Not now" does. */
export function releaseQuestion(version: string, kind: string, title: string): string {
  return `Project Architect ${version}, a ${kind}${title === '' ? '' : ` (${title})`}, is out. Install it? Tasks keep ` +
    'running while you decide.\nNot now: when you\'re back at this machine, just tell Claude you\'re back and want the ' +
    'update — it installs after the current task. Or restart (/exit, then claude --continue) and it installs on the way in.'
}

/** The check's line to Claude, once, with the picker. */
export function releaseNotice(version: string, kind: string): string {
  return `‹pa› PA3 ${version}, a ${kind}, is out; the developer is asked with a picker whether to install it after the ` +
    'current task. Tasks keep running; no answer means not now. Tell the developer this once, in one line. Whenever ' +
    'they say, in any words, that they want the update now, call release({hold: "update"}).'
}
/** 3.28.5 T18: the chosen release's run started (once per install): Claude says so in one line. */
export const installingNotice = (version: string): string =>
  `‹pa› the install of PA3 ${version} has started. Tell the developer this one line now:\n\n` +
  `Installing Project Architect ${version} now; please wait, I'll tell you when it's done.`

// 3.24 T6: the update run's marker and log (tools/pa3_update.py writes both; pa/health.py writes the running marker of a
// run the session-start hook starts). The mod starts no task while the marker says running and reports a finished run once.
export const UPDATE_MARKER = '.run/mod/update.json'
export const UPDATE_LOG = '.run/mod/update.log'
export const UPGRADE_MD = '.claude/pa3-upgrade/UPGRADE.md'
/** A running marker this old is stale (pa/health.py RUN_STALE_S, pa3_update.py's lock: 20 min). */
export const RUN_STALE_MS = 20 * 60_000
/** A finished run first seen this long after it ended is not reported (a marker older than this loop.json's record). */
export const RUN_REPORT_MS = 24 * 3_600_000
/** Conflict rounds (UPGRADE.md's expert, then the rerun) before the conflicts go to Claude instead. */
export const UPGRADE_ROUNDS = 2
export type RunRecord = { phase: string; git: string }
export type UpdateMarker = { state: 'running' | 'done'; mode: string; rc: number; atMs: number; from: RunRecord; to: RunRecord }

const recordOf = (v: unknown): RunRecord => {
  const o = typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {}
  return { phase: str(o['phase']), git: str(o['git']) }
}

/** The run marker (`at` in seconds, as Python writes it), null when absent or malformed. */
export function markerOf(text: string | null): UpdateMarker | null {
  let o: Record<string, unknown>
  try {
    const v: unknown = JSON.parse(text ?? '')
    o = typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
  } catch {
    return null
  }
  const state = o['state'] === 'running' || o['state'] === 'done' ? o['state'] : null
  if (state === null || num(o['at']) === 0) {
    return null
  }
  return { state, mode: str(o['mode']) || 'update', rc: num(o['rc']), atMs: Math.round(num(o['at']) * 1000), from: recordOf(o['from']), to: recordOf(o['to']) }
}

export const runRunning = (m: UpdateMarker | null, now: number): boolean =>
  m !== null && m.state === 'running' && now - m.atMs < RUN_STALE_MS
/** A finished run not yet reported: newer than loop.json `updateSeenAt` and recent. */
export const runToReport = (m: UpdateMarker | null, seenAt: number, now: number): boolean =>
  m !== null && m.state === 'done' && m.atMs > seenAt && now - m.atMs < RUN_REPORT_MS

/** What a run's whole output says: conflicts staged (managed.py `! <rel> kept yours; upstream staged`) and the
 * installer's `restart: needed` (both indented note lines). */
export function updateOutcome(log: string): { conflicts: boolean; restart: boolean } {
  return { conflicts: /^\s*! \S.* kept yours; upstream staged\s*$/m.test(log), restart: /^\s*restart: needed\b/m.test(log) }
}

const recordText = (r: RunRecord): string => (r.phase || '?') + (r.git === '' ? '' : ` (${r.git.slice(0, 8)})`)

/** The finished run's notice to Claude: from X to Y and where the changes are, else the failure; a restart; conflicts. */
export function updateDoneNotice(m: UpdateMarker, o: { conflicts: boolean; restart: boolean; loaded?: boolean }, pa3: string, tail: string): string {
  const what = m.mode === 'repair' ? 'reinstall' : 'update'
  if (m.rc !== 0) {
    return `‹pa› the PA3 ${what} failed (exit ${String(m.rc)}); its whole output is in ${UPDATE_LOG}. Its end:\n${tail}\nTell the developer in plain words what failed.`
  }
  const head = m.mode === 'repair'
    ? `‹pa› the PA3 reinstall finished: the installed release is restored (the project at ${recordText(m.to)}). Tell the developer in plain words.`
    : m.from.phase === m.to.phase && m.from.git === m.to.git
      ? `‹pa› the PA3 update finished; the project stays at ${recordText(m.to)}. Tell the developer once.`
      : `‹pa› PA3 updated from ${recordText(m.from)} to ${recordText(m.to)}. Read ${pa3}/CHANGES.md for the entries after ${m.from.phase || 'the old version'} up to ${m.to.phase || 'the new one'} and tell the developer in plain words what changed.` +
        // 3.28.1 T14: the mod and the agents load at a session start; /clear starts none, /reload-plugins skips the agents
        // 3.28.2 T16: the restart on its own bold line after a blank line, so it stands out
        // 3.28.2 T17: a restart already loaded the new mod before the run ended: the loaded line, no restart
        (o.loaded === true
          ? ` This session already runs ${m.to.phase} (a restart loaded it before the run ended): no restart for it. Then say this one line:\n\n**Project Architect ${m.to.phase} is installed and loaded. Ready to continue work.**\n\nThen carry on as usual.`
          : ` Then tell them once, in this shape: the line "Project Architect ${m.to.phase}, a ${m.to.phase.split('.').length >= 3 ? 'hotfix' : 'feature update'} (<the title of its CHANGES.md heading>), is installed.", a blank line, then on its own line:\n\n${RESTART_LINE}\n\nStart no work until then: if they ask for work, say that bold line again.`)
  return head +
    (o.restart ? ' A setting changed that takes effect only once Claude Code restarts (restart: needed): tell the developer to restart it.' : '') +
    (o.conflicts ? ` Some files the developer edited also changed in PA3 (${UPGRADE_MD}): an expert resolves them at the next boundary, then the project step runs once more.` : '')
}

/** Said once when the mod's own files changed under a running session (3.24 T2 row P11i: no hot reload). */
export const RELOAD_NOTICE = `‹pa› the PA3 mod's own files changed in this update: the new mod and agents load at the next start of Claude Code (${RESTART}). No task starts until then; tell the developer once, with the update.`

/** The conflicts' notice when the rounds are spent. */
export const upgradeLeftNotice = (rounds: number): string =>
  `‹pa› PA3 update conflicts remain in ${UPGRADE_MD} after ${String(rounds)} rounds; tell the developer: each line wants \`| resolve: keep|upstream|merged: <path>\`, then \`update pa3\` applies them.`

/** The `mod/` entries of a root MANIFEST.json (`files: {rel: sha256}`) as one comparable key; '' when unreadable. */
export function modFilesKey(text: string | null): string {
  try {
    const v: unknown = JSON.parse(text ?? '')
    const files = typeof v === 'object' && v !== null ? (v as Record<string, unknown>)['files'] : undefined
    if (typeof files !== 'object' || files === null) {
      return ''
    }
    return JSON.stringify(Object.entries(files as Record<string, unknown>).filter(([k]) => k.startsWith('mod/')).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
  } catch {
    return ''
  }
}

/** loop.json's conflict task (3.24 T6): its expert, spawn time and round; null none. */
export type UpgradeTask = { agentId: string; at: number; round: number }
export function upgradeOf(s: LoopState): UpgradeTask | null {
  const v = (s as LoopState & { upgrade?: unknown }).upgrade
  const o = typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null
  return o === null ? null : { agentId: str(o['agentId']), at: num(o['at']), round: num(o['round']) }
}
export const updateSeenOf = (s: LoopState): number => num((s as LoopState & { updateSeenAt?: unknown }).updateSeenAt)

export type TaskSummary = { title: string; summary: string }

/** loop.json's kept SUMMARY lines (3.23 T13b), by task id in capture order; malformed entries dropped. */
export function summariesOf(s: LoopState): Record<string, TaskSummary> {
  const v = (s as LoopState & { summaries?: unknown }).summaries
  const o = typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
  const out: Record<string, TaskSummary> = {}
  for (const [id, x] of Object.entries(o)) {
    const r = typeof x === 'object' && x !== null ? (x as Record<string, unknown>) : {}
    if (str(r['summary']).trim() !== '') {
      out[id] = { title: str(r['title']), summary: str(r['summary']).trim() }
    }
  }
  return out
}

/** `s` with task `t`'s contract SUMMARY kept (a later capture of the same task replaces it); `s` when it has none. */
export function keepSummary(s: LoopState, t: LoopTask, contract: string | null): LoopState {
  const summary = (parseExpertContract(contract ?? '')?.summary ?? '').trim()
  return summary === '' ? s : { ...s, summaries: { ...summariesOf(s), [t.id]: { title: t.title, summary } } }
}

// The phase close (3.23 T6b): the close question, the chain's steps in order (chainStep names the next), the recap.
// T13b: the notice carries each finished task's summary; Claude's card says how to report it. 3.25 T2: the yes carries
// Claude's report as release's note (the mod writes it to RECAP.md); `gen`, the generation a generation-end close ends.
export const closeNotice = (phase: string, sums: Record<string, TaskSummary> = {}, gen = ''): string => {
  const lines = Object.entries(sums).map(([id, x]) => `${id} ${x.title}: ${x.summary.replace(/\s*\r?\n\s*/g, ' ')}`)
  return `‹pa› Plan end: phase ${phase} has no task left.` +
    (lines.length === 0 ? ' No task left a summary.' : ` Each task's summary:\n${lines.join('\n')}\n`) +
    ' Report the phase to the developer, then ask "Phase ' + phase + ': every task is done. Is the milestone met: does' +
    ' what the phase built run?" with the answers "Yes, close the phase (recommended)" and "Not yet: <what is missing>",' +
    ' and wait. What is missing goes through plan_edit; on yes, call release({hold: "close", note: "<your report>"}).' +
    (gen === '' ? '' : ` This close also ends Generation ${gen}, so the report covers the whole generation.`)
}
// 3.23 T13d.c4: the archived line, one feed record (3.25 T2: the mod's own; no recap turn).
// The generation end parks the loop in `held(generation)` (T13b.c3); its sentence says the next one is the developer's.
export const closedLine = (phase: string, genEnd = false): string =>
  `Phase ${phase} closed. Push when ready.` + (genEnd ? ' The generation is complete; the next one is yours to start.' : '')
export const CHAIN_STEPS: readonly string[] = ['recalc', 'assemble', 'commit', 'archive', 'run', 'archived', 'clear', 'recap'] // 3.25 T2: no lint

/** The plan's first line `# Phase <N> — … (implements … <G>)`: the phase id and its generation phase ('' unknown). */
export function planPhase(text: string): { phase: string; gen: string } {
  const head = (text.split(/\r?\n/)[0] ?? '').trim()
  const m = /^#\s*Phase\s+(\S+)\s*[-—]+\s*(.*?)\s*(?:\(implements(.*)\))?$/.exec(head)
  const g = m?.[3] === undefined ? null : /([0-9]+(?:\.[0-9]+)*)/.exec(m[3])
  return { phase: m?.[1] ?? '', gen: g?.[1] ?? '' }
}

/** The phase plan's task rows, counted as the status line counts them (pa/statusline.py scan_phase_plan): a row
 * `- T<digits/dots> | <status> | …` (plan_edit TASK_RE); `superseded` is not in the total, `done` is done (3.23 T13d.c2). */
export function planCounts(text: string): { rows: number; done: number; total: number } {
  const out = { rows: 0, done: 0, total: 0 }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line.startsWith('- T') || !line.includes('|')) {
      continue
    }
    const fields = line.split('|')
    const id = (fields[0] ?? '').trim().slice(2).trim()
    if (!/^T[0-9]+(\.[0-9]+)*$/.test(id)) { // plan_edit TASK_RE: `T<digits/dots> |`, never a Context bullet (3.24 T13f)
      continue
    }
    const status = (fields[1] ?? '').trim().toLowerCase()
    out.rows += 1
    out.total += status === 'superseded' ? 0 : 1
    out.done += status === 'done' ? 1 : 0
  }
  return out
}

/** The project's name for the session title: pa.json's `"project"`, else the root folder's name. */
export function projectName(paJson: string | null, root: string): string {
  try {
    const p = paJson === null ? null : (JSON.parse(paJson) as Record<string, unknown>)['project']
    if (typeof p === 'string' && p.trim() !== '') {
      return p.trim()
    }
  } catch {
    // not JSON: the folder's name
  }
  return root.split(String.fromCharCode(92)).join('/').split('/').filter(x => x !== '').pop() ?? '' // the loader's parser trips on a backslash regex
}

/** The session title (3.23 T13d.c2), the status line header's form: `<project> · Phase <id> · <done>/<total>`;
 * no count when every row is superseded. With no open phase or no task rows (3.23 T13d.c6a): `<project> · Phase <next>`,
 * the next phase GENERATION_PLAN.md names (nextPhaseOf); `<project>` alone when it names none. */
export function sessionTitle(project: string, planText: string, genText = ''): string {
  const phase = planPhase(planText).phase
  const c = planCounts(planText)
  if (phase === '' || c.rows === 0) {
    const next = nextPhaseOf(genText)
    return next === '' ? project : `${project} · Phase ${next}`
  }
  return [project, `Phase ${phase}`, ...(c.total > 0 ? [`${String(c.done)}/${String(c.total)}`] : [])].filter(x => x !== '').join(' · ')
}

/** The next phase of GENERATION_PLAN.md's `## Phases` (3.23 T13d.c6a): the first row not `status: closed`; every row
 * closed (a generation end), the next generation's first phase `<generation + 1>.1`; '' with no phase rows. */
export function nextPhaseOf(genText: string): string {
  let inPhases = false
  let top = 0
  for (const raw of genText.split(/\r?\n/)) {
    if (/^##\s/.test(raw)) {
      inPhases = /^##\s+Phases\s*$/.test(raw.trim())
      continue
    }
    const m = inPhases ? /^\s*-\s+([0-9]+)((?:\.[0-9]+)*)\s/.exec(raw) : null
    if (m === null) {
      continue
    }
    if (!/status:\s*closed(\s|$)/.test(raw)) {
      return `${m[1] ?? ''}${m[2] ?? ''}`
    }
    top = Math.max(top, Number(m[1]))
  }
  return top === 0 ? '' : `${String(top + 1)}.1`
}

/** loop.json's `phaseSids` (3.24 T10b: every session that owned the open phase) with `sid` appended once; tolerant. */
export function withPhaseSid(v: unknown, sid: string): string[] {
  const a = Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x !== '') : []
  return sid === '' || a.includes(sid) ? a : [...a, sid]
}

/** The sessions the close's ledger recalc reads (3.24 T10b): loop.json's `phaseSids`, every session that owned the
 * phase (a `/clear`, a restart); else 3.23 T13d.c6a's one: `ownerSid`, else status.json's Gen 3 `router_session`, else
 * the router's own session; [] none. */
export function recalcSessions(phaseSids: unknown, ownerSid: string, routerSession: string, sid: string): string[] {
  const all = withPhaseSid(phaseSids, '')
  const one = [ownerSid, routerSession, sid].find(x => x !== '') ?? ''
  return all.length > 0 ? all : one === '' ? [] : [one]
}

/** Every other phase of `gen`'s generation is `status: closed` in GENERATION_PLAN.md (phaseend_index._other_open_phases). */
export function genEndOf(genText: string, gen: string): boolean {
  if (gen === '') {
    return false
  }
  const major = gen.split('.')[0]
  return genText.split(/\r?\n/).every(line => {
    const m = /^\s*-\s+([0-9]+(?:\.[0-9]+)*)\s/.exec(line)
    if (m === null || m[1] === gen || m[1]?.split('.')[0] !== major) {
      return true
    }
    return /status:\s*closed(\s|$)/.test(line)
  })
}

/** archive's output: its `stage:` paths and `RUN:` commands, in order. */
export function archiveLines(out: string): { stage: string[]; runs: string[] } {
  const lines = out.split(/\r?\n/).map(l => l.trim())
  return {
    stage: lines.filter(l => l.startsWith('stage:')).map(l => l.slice('stage:'.length).trim()).filter(l => l !== ''),
    runs: lines.filter(l => l.startsWith('RUN:')).map(l => l.slice('RUN:'.length).trim()).filter(l => l !== ''),
  }
}

/** The loop's own agents (never forwarded, never filed as children): the task's expert and the generation start's. */
export function loopAgentIds(s: LoopState): string[] {
  const g = genStartOf(s)
  return [s.task?.agentId ?? '', g === null || g.step === 'done' ? '' : g.agentId].filter(x => x !== '')
}
