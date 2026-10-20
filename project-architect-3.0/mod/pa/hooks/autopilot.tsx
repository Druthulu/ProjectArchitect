// PA 3.2 router plugin `pa`, the autopilot's effects (phase 3.23; 3.23-autopilot.md, "The loop (router `pa`)").
// T4's probe: hooks.json `modules` takes one hooks module per plugin (validate refuses a second entry), so this is an
// `on`-taking helper that register.tsx calls last; its hooks nest inside the router's (a plugin's registrations nest in
// order, first outermost). An event the router also hooks repeats only under a matcher (`{}` matches every event).
// T5a: the task loop's core. `.run/mod/loop.json` (project-level, loop.ts) is read at every tick and written after every
// transition while this session owns the loop; the tick lives in session.start's closure under the router's generation
// token. Boundary: launch.py --state-json, plan_edit.py next, the window gate, status.py set, the expert's spawn. Capture:
// the loop agent's last STATUS block (hand-back report or turn.complete answer), held as `pendingContract` (journal
// `T<n>/interim`) and routed only at loop.runEnded; else (run ended) its transcript, else a block (T5c.c3). Routing:
// done / handoff / review / question-or-block (loop.routeStatus). Commits run under Git Bash (resolveBash). Hooks record
// facts and return; steps run from the tick, each re-derived from loop.json, status.json and the plan.
// T5b: stops reach Claude as `notice` requests in the router's inbox (the router's drain frames them for the partner,
// or the caller, and raises the toast); the tick drains its own `release` requests from that inbox (done-marked in
// loop.json `inboxDone` before acting; the router skips the kind) and applies loop.releaseHold. A `plan_edit` during a
// review or question stop is a decision, applied at once and committed; otherwise it is queued in `queuedEdits`.
// T5b2: queued edits land at the next boundary, before the pick, each leaving the queue once run. A developer prompt
// while the loop is active pushes `talk` (recorded by this prompt.submit, pushed by the tick): no new task starts, the
// running expert finishes and is routed. Claude's answers set `lastAnswerAsked`; silence (loop.silenceReleases) pops it.
// T5c: `steer` requests (a loop kind) reach the running task's expert through `$.session.send`; nothing is ever stopped.
// T5d1: the heartbeat every 15 s, stale after 45 s (loop.ts). A session that takes the loop over (another owner, or a
// stale heartbeat) with a task running and no stop runs recovery: `continue:` to the recorded expert, then, not
// delivered or not seen running within 30 s (or a stale run_in_flight), a respawn with PROGRESS, TASK_PROGRESS.md
// written by the mod when the hook's kill digest is absent. A second live session is conversation-only (the watcher's
// usage line says so).
// T5d2a: the router's forward of a child's hand-back to the loop's expert reaches the tick as a `forward` request
// (`task.forwardAt`, queued to the expert until its next turn.complete). A picked task's WAIT-FOR (`<file> contains
// <literal>`, loop.json `waitFor`) is polled every 30 s and holds the queue; met, the task runs; after 50 min,
// `task_log.py review` and the review stop. `.run/health.json` `savings_missing.hook_repair` at a boundary is a note to
// Claude (a FIX promotes the root); the FIX spawn runs only with PA_MOD_FIX_SPAWN=1 and PA_MOD_PA3_DIR unset.
// T6a1: with no phase plan and a generation plan, `held(plan)` and a "phase plan is needed" notice to Claude. The router's
// `install` request (an approved generation or phase plan) runs here, a step a tick in loop.json `install`: a generation
// plan written and committed; a phase draft linted (ERROR lines go back as changes asked), from-draft, approve with the
// caller's model/effort (the watcher's facts), one `ask` per rules and candidates line (the loop's own asks, caller
// `loop`, answered by the router's `answer` request), REPLAN.md removed, committed; the decision line plus `installed:`
// to the caller. A REPLAN.md at a question's release: status.py replan-written and `held(plan)`. While the router's
// dialog flag is open the tick drains, spawns, resumes and sends nothing.
// T6a2: launch.py mode `planner-gen` with no phase plan opens the generation start (loop.json `genStart`, a step a tick):
// status.py inbox-consume, launch.py again, the curator ask (Run it now / Pause: `held(plan)`, asked next session),
// `memory-curator`, `auditor` when a PhaseEnd exists, one notice to Claude, `held(plan)`; the migration curate line asks
// once a session before the next spawn. The curator and the auditor are loop agents (loop.loopAgentIds): captured here,
// never forwarded, steered or warmed. `managed.py drift` at each boundary: one ask per row (loop.json `drift`), stamp or
// decline, committed. hook-context rows (`session.append`) are filed `mod/context`, once a session: an update line is a
// notice to Claude (the update ask and `pa3_update.py` run only with PA_MOD_UPDATE=1 and PA_MOD_PA3_DIR unset), the
// renewal line a notice. A STATUS block textually identical to the task's last routed contract (a `.` ping re-sends an
// expert's last hand-back, T5d2b) is ignored, with no journal line (3.25 T3).
// T6b: the phase close. At plan end `held(close)` and the close question to Claude; in it a queued `plan_edit` or a
// changed INBOX.md runs a boundary (queued edits, status.py inbox-consume to Claude, `next`; a task pops `close`).
// `release(close)` sets `closeRequested` and keeps its note (Claude's phase report) as `closeRecap`: the same boundary,
// then at NONE-done the close itself, no agent (3.25 T2: the developer's yes is the milestone): RECAP.md from the note,
// then the chain (state `closing`, loop.json `chainStep` the next step, archive's RUN:/stage: lines kept): recalc,
// assemble, the PhaseEnd commit, archive, its RUN: lines, the archived commit, status.py clear, the closed line; then the
// no-plan boundary (plan needed, or the generation start).
// T11-fix2: the loop agent's `$.agent.list()` status every tick: pending/running/waiting busy; killed, failed, or absent
// 15 s after its spawn, with no contract: the `stopped` hold and a notice (a Ctrl+C is the developer's; never resumed by
// the loop); with a contract, routed. `release(stopped)` resumes it by message, else (or `fresh`) respawns it.
// 3.23 T13: after each step the tick writes the band (`pa.band`): the loop's line (phase, task n of m from loop.json
// `taskPos`, kept at spawn, holds, the close step) and a row per live agent under the loop task or the side agent.
// 3.23 T13d.c11: nothing starts on its own. Every session start (startup, resume, /clear) whose loop would act (a spawn, a
// recovery, the close chain, the next generation's start, a plan to draft) pushes `held(session)`; silence never ends it,
// the developer's message pushes no `talk` under it, and Claude's `release({hold: "session"})` lets the loop go on.
// 3.28.1 T9c: a task cut off by a session end (a restart, an update, a closed terminal) is no stop: its recovery runs
// under `held(session)` and Claude tells the developer in one line. Only an agent this process saw at work and then
// killed is the developer's stop.
// 3.24 T6: the update ask and run and the health FIX spawn are on in production (PA_MOD_PA3_DIR empty); a trial forces
// them with PA_MOD_UPDATE=1 (was: only PA_MOD_UPDATE=1 / PA_MOD_FIX_SPAWN=1 with PA_MOD_PA3_DIR unset). Under `upgrade:
// auto` the session-start hook starts `pa3_update.py` itself (its line does not match contextLines); under `ask` the
// update line becomes the picker ask, and a yes runs it here. The run marker (`.run/mod/update.json`): no task starts
// while it says running; a finished run (the hook's detached one, a heal, or ours) is reported once (loop.json
// `updateSeenAt`): its whole log to the journal and the debug log, "from X to Y" and CHANGES.md to Claude, a restart;
// conflicts become a one-off UPGRADE expert at the next boundary (loop.json `upgrade`), then `pa3_update.py` once more;
// the mod's own files changed (MANIFEST.json `mod/` entries against the ones read at arm): Claude says once that the
// new mod loads at the next start or /reload-plugins, and no task starts on the old code.
// The engine allows `$` only as `$.noun.method(...)` at the call site (design.md, "Execution model").
import { atom, read, update } from 'claude-code'
import type { On } from 'claude-code'

import type { Band, HoldName, LoopState, LoopTask, ToastOut } from '../types'

import { paSetting, versionAtLeast } from './activation'
import { auditorBrief, curatorBrief, expertBrief, type ExpertOpts, fixBrief, type PlanTask, upgradeBrief } from './brief'
import { acceptedCard, type BandAgent, bandIds, bandRows, bandStepOf } from './card'
import { CHAIN_SCRIPTS, parseAuditorReturn, parseExpertContract, splitCommand } from './contract'
import { clip, eventFile, isStale } from './debug'
import { isProgressEntry, recoveryDigest } from './digest'
import { isLoopKind, noticeRequest, parseRequest, pendingRequests, type Request, requestFile } from './inbox'
import { JOURNAL_DIR, stamp, type Meta } from './journal'
import {
  agentFor, candidateOf, effortFor, effortHint, CANDIDATE_OPTIONS, CONTINUE_TEXT, editIsDecision, HEARTBEAT_MS, type Install, installOf, lintErrors, LOOP_FILE,
  nextRuleId, ownsLoop, parseLoop, plannerIdOf, popHold, pushHold, queuedTo, releaseHold, routeStatus, ruleArgs, RULE_OPTIONS, runEnded, nextTaskOf,
  serializeLoop, silenceReleases, steerText, stoppedText, quietReply, liveness, ABSENT_MS, takesOver, WAIT_MAX_MS, WAIT_POLL_MS, waitSpec, windowResumes,
  contextLines, CURATE_OPTIONS, curateQuestion, deferredOf, FIRST_RUN_WAITS, firstRunLine, type Drift, DRIFT_OPTIONS, driftOf, driftQuestion, driftRows, type GenStart,
  genNotice, genStartOf, newGenStart, phaseEndsOf, renewalNotice, type UpdateAsk, updateNotice, updateOf, UPDATE_OPTIONS,
  archiveLines, bandLine, CHAIN_STEPS, closeNotice, genEndOf, keepSummary, planPhase, closedLine, recalcSessions, summariesOf, taskPosOf, withPhaseSid,
  installingNotice, loadedNotice, olderRunWork, RUN_REVIEW_MARKER, RUN_REVIEW_OPTIONS, RUN_REVIEW_QUESTION, runReviewNotice, CLEANUP_ASK, CLEANUP_LIST, CLEANUP_OPTIONS, cleanupQuestion, START_CHECK, startCheckState, pausedNotice, markerOf, modFilesKey, RELOAD_NOTICE, RELEASE_CHECK_MS, RELEASE_MARKER, releaseNotice, releaseOf, RELEASE_OPTIONS, releaseQuestion, runRunning, runToReport, UPDATE_LOG, UPDATE_MARKER, updateDoneNotice, updateFact, updateOutcome, updateSeenOf,
  UPGRADE_MD, upgradeLeftNotice, upgradeOf, UPGRADE_ROUNDS, FORWARD_TTL_MS, SHELLS_FILE, shellNotice, shellsOf, shellsOut, waitingShell,
} from './loop'
import { APPROVED, changesAsked, DIALOG_FILE, dialogOpen, planFilePath } from './planview'
import { asksQuestion, classify, handbackChild, isNote, journalOut, noteBody, partnerReport } from './route'
import { DONE_TTL_MS, feedDirOf } from './state'
import { cacheTtlOf, isCold } from './policy'
import { modRecord, nudgeName, startRecord } from './feed'
import { anchorOf, cutPoint, markOf, tailOf, trimFailure, trimInstructions } from './trim'

/** The router's generation token (register.tsx `genAtom`, the same key): a re-arm moves it on and old ticks stop. */
const genAtom = atom({ plugin: 'pa', key: 'gen' } as const, 0)
/** The router's atoms (register.tsx, same keys), read only: its state (the partner), its route switch, sdk-as-user. */
const stateAtom = atom({ plugin: 'pa', key: 'state' } as const, null)
const routeAtom = atom({ plugin: 'pa', key: 'route' } as const, false)
const sdkAtom = atom({ plugin: 'pa', key: 'sdkAsUser' } as const, false)
/** The band above the prompt (register.tsx `bandAtom`, the same key): this tick its one writer (3.23 T13). */
const bandAtom = atom({ plugin: 'pa', key: 'band' } as const, { line: '', rows: [] } as Band)

const PLAN = 'phase-ends/current/PHASE_PLAN.md'
const REVIEW = 'phase-ends/current/REVIEW.md'
const PROGRESS = 'phase-ends/current/TASK_PROGRESS.md'
const GEN_PLAN = 'GENERATION_PLAN.md'
const REPLAN = 'phase-ends/current/REPLAN.md'
const INBOX_MD = 'phase-ends/current/INBOX.md'
/** The close's recap (3.25 T2): `## Recap` (and `## Generation Recap` at a generation end) from Claude's report. */
const RECAP = 'phase-ends/current/RECAP.md'
/** How long the chain's recalc step waits for that run before it goes on (3.23 T13d.c4). */
const RECALC_WAIT_MS = 10_000
/** The chain's recalc step reads the done-file this often (3.24 T10b). */
const RECALC_POLL_MS = 2_000
/** The detached recalc's done-file (3.24 T10b: `pa_ledger.py recalc --done-file`, removed by its launcher). */
const RECALC_DONE = '.run/mod/recalc.done'
const PLAN_NEEDED = '‹pa› a phase plan is needed: draft the next phase plan with the pa-planning skill and present it with plan(kind: phase)'
/** 3.24 T13A: after a first run's recap notice, PLAN_NEEDED waits for Claude's turn that relayed it, at most this long. */
const RELAY_WAIT_MS = 5 * 60_000
const REPLAN_NOTICE = '‹pa› the plan needs replanning (REPLAN.md): draft the phase plan with the pa-planning skill and present it with plan(kind: phase)'
/** Removes a file if present (no `$.fs` remove); no double quote, so Windows argv quoting cannot bend it. */
const RM_PY = 'import os,sys;p=sys.argv[1];os.path.isfile(p) and os.remove(p)'
const TICK_MS = 2_000
// 3.29.9: the display trim runs this long after its request (off the loop's path), retries a refusal while a turn runs
// every TRIM_RETRY_MS up to TRIM_TRIES times (about 30 s), and the session start's catch-up waits TRIM_START_MS.
const TRIM_DELAY_MS = 1_000
const TRIM_RETRY_MS = 2_000
const TRIM_TRIES = 15
const TRIM_START_MS = 3_000
const TRIM_FILE = '.run/mod/trim.json' // H1.c3: the last mark trimmed: { sid, task, len, at }
/** Recovery (T5d1): an expert sent `continue:` and not seen running this long after is respawned. */
const RECOVER_WAIT_MS = 30_000
/** An agent `$.agent.list()` shows at work (recovery's "seen running"). */
const AT_WORK = new Set(['pending', 'running'])
/** While `off` (no approved plan), launch.py is asked at most this often. */
const OFF_RECHECK_MS = 30_000
/** Every process call's timeout, at most. */
const PROC_MS = 60_000
/**
 * 3.28.1 T10: a git write's timeout (`commit_task.sh`, every mod commit goes through it). A commit in a repo of several
 * GB outran PROC_MS and was killed mid-commit, leaving `.git/index.lock` (TerrainDiffusion 2026-10-15). Five minutes: a
 * held child stalls the session's queue while it runs (3.24 T10b), but only a slow commit holds it, and a killed one costs
 * the commit and a stale lock.
 */
export const GIT_WRITE_MS = 300_000
/**
 * Prints `none` (no lock), `busy` (a git process that may hold it is running: kept) or `removed`. Any repo's git process
 * counts (Windows cannot tell which repo a process works in), except `--no-optional-locks` ones (Claude Code's own
 * `git status`; they take no lock). A failing check exits non-zero, printing nothing: kept. No double quote.
 */
const GIT_LOCK_PY = "import os,sys,subprocess as s;p=sys.argv[1];w=os.name=='nt';o=os.path.isfile(p) and s.run(['powershell','-NoProfile','-Command','Get-CimInstance Win32_Process | ? Name -eq git.exe | % CommandLine'] if w else ['ps','-eo','args'],capture_output=True,text=True,check=True).stdout;g=[l for l in (o or '').splitlines() if l.strip() and (w or os.path.basename(l.split()[0])=='git') and '--no-optional-locks' not in l];print('none' if o is False else 'busy' if g else (os.remove(p) or 'removed'))"
/** A spawn recorded without its agentId this long is lost (a crash between the spawn and its save): a block. */
const SPAWN_LOST_MS = 60_000
const HANDBACK_KINDS = new Set(['task-notification', 'peer', 'peer-send-message'])
const HOLD_NAMES: readonly string[] = ['talk', 'review', 'question', 'plan', 'close', 'window', 'stopped', 'generation', 'session']
const MILESTONE = 'milestone: `PY tools/plan_edit.py show --section Milestone`'
const DECIDE = 'Decide small changes inside the milestone yourself (plan_edit, then release({hold: "question", note})); ask the developer about the milestone or the scope.'
const BUSY = new Set(['pending', 'running', 'waiting'])
/** 3.29.1 T8: the releases that start work (resume a stop, go past a review or question, close); they wait for the gate. */
const WORK_HOLDS = new Set(['stopped', 'review', 'question', 'close'])
/** A request file's `release` hold, '' for any other kind or a bad file. */
const holdOf = (text: string): string => {
  try {
    const o = JSON.parse(text) as { kind?: unknown; input?: { hold?: unknown } }
    return o.kind === 'release' && typeof o.input?.hold === 'string' ? o.input.hold : ''
  } catch {
    return ''
  }
}
const MEMORY_DIR = '.claude-state/memory'
/** launch.py's mode, cached this long while no plan is installed (T6a2). */
const GEN_MODE_MS = 30_000
const strOf = (v: unknown): string => (typeof v === 'string' ? v : '')

/**
 * What the hooks saw of a loop agent: its last STATUS-bearing text (hand-back report or turn.complete answer, whichever
 * came last) and its last turn end (a turn.complete or a hand-back); for the generation start's agent (T6a2), `answer`,
 * its last answer of any form (the curator's recap has no STATUS). 3.24 T13A: `handbackAt`, its hand-back's time (the run
 * ends without the quiet wait, loop.runEnded); after it, a side agent's later turns never replace the hand-back's report.
 */
type Fact = { status: string | null; lastCompleteAt: number; answer?: string; handbackAt?: number }

// Module variables are lost on a hot reload; each is re-derived (loopAgent from loop.json at the next tick, a lost fact
// through the transcript fallback).
let counter = 0
let facts: Record<string, Fact> = {}
let loopAgent = '' // the running task's expert, from loop.json at each tick
let ticking = false
let offCheckedAt = 0
let jseq = 500 // the autopilot's journal sequence, apart from the router's
let nseq = 0 // the notices' sequence in the router's inbox
let sseq = 0 // the start records' sequence in the feed's `in/` (3.23 T13d.c3)
// The close's ledger recalc (3.23 T13d.c4), launched detached at the close's start (3.24 T10b; 3.25 T2): its phase and, when it
// was not launched, the journal line ('' launched: the chain reads RECALC_DONE). Lost on a hot reload: the chain starts one.
let recalc: { phase: string; line: string } | null = null
let armedAp = { sid: '', gen: 0 }
let transcriptSeen = { sid: '', path: '' }
let apRearm: ((sid: string) => Promise<void>) | null = null
// 3.29.9: the armed session's display trim request (null until armed), and its single flight: a request while one runs
// is run once more after it.
let apTrim: ((why: string) => void) | null = null
let trimBusy = false
let trimAgain = ''
// A session whose SessionStart (startup, resume or clear) its first owning tick has not seen: it pushes `held(session)`
// when the loop would act (3.23 T13d.c11; was T13b.c3's freshSid, which lifted `held(generation)` on startup or clear).
let startSid = ''
let clearSid = '' // a session whose SessionStart was `clear`: it owns the loop at once from this process's earlier session (T13b.c4)
const procSids = new Set<string>() // every session this process armed the loop for (T13b.c4)
let devAt = 0 // the developer's last prompt (ms), for the tick's `talk`
let devSid = '' // the session of the developer's last prompt: a pending first run is asked with it (3.24 T7b-2c)
let partnerSaid = { at: 0, asked: false, notice: false } // Claude's last answer (ms), whether it asked the developer something, whether it followed a mod notice
let waitPolledAt = 0 // the WAIT-FOR file's last poll (ms); 0 polls at once (T5d2a)
let genMode: { sid: string; at: number; doc: Record<string, unknown> | null } = { sid: '', at: 0, doc: null } // launch.py, cached (T6a2)
let sideAgent = '' // the generation start's running agent (curator or auditor), from loop.json at each tick (T6a2)
let routedText = '' // the running task's last routed contract, from loop.json at each tick (T6a2)
let contextQ: { sid: string; kind: 'update' | 'renewal'; line: string }[] = [] // hook-context lines owed (T6a2)
const bandSince = new Map<string, number>() // agent id -> first seen by the band's tick (ms; the expert's spawnedAt)
const contextSeen = new Set<string>() // `<sid>\n<line>`: each handled once a session
const driftAsked = new Set<string>() // `<sid>\n<rel>`: each drift row asked once a session
// 3.28.1 T9c: loop agents this process saw not stopped in `$.agent.list()`: only these can be the developer's stop; one
// killed or absent and never seen was cut off by a session end and is recovered.
const seenLive = new Set<string>()
// 3.28.1 T15: `WAITING: shell <what>` hand-backs seen by the hooks, for the tick to tell Claude (part d).
let shellQ: { agentId: string; what: string; at: number }[] = []
function waitsOnShell(agentId: string | undefined, text: string, at: number): void {
  const what = waitingShell(text)
  if (what !== null && agentId !== undefined && agentId !== '') {
    shellQ = [...shellQ.filter(q => q.agentId !== agentId), { agentId, what, at }]
  }
}
// 3.24 T6: the root MANIFEST.json's `mod/` entries read at the first arm of this load (null unread), and whether an
// update changed them since: the loaded code is then the old one, so no task starts until a reload or the next start
// (both drop these). `updWait`: the last no-start reason logged, so a long wait logs once.
let modSeen: string | null = null
let modStale = false
let updWait = ''
let startAt = 0 // 3.29.1 T8: this process's session start (ms): the update check's note must be this start's
let pausedAskedSid = '' // 3.29.1 T9: the session Claude was asked about a paused task in (once a session start)
let runReviewDue = false // 3.29.2: the one-time `.run/` cleanup is to be offered once the update gate opens
let releaseCheckedAt = 0 // 3.28.1 T14: the 8-hour release check's last run (ms); 0 until the first tick
let modLoaded = '' // 3.28.2 T17: the PA3 release this process loaded (`<pa3>/VERSION` at its session start)

const quiet = (): undefined => undefined

/**
 * Git Bash for `commit_task.sh`: pa.json `bash`, else CLAUDE_CODE_GIT_BASH_PATH, else derived from `git --exec-path`
 * (…/mingw64/libexec/git-core → …/bin/bash.exe). On win32 a bare name (no path separator) is never used; '' when
 * nothing resolves there. Elsewhere `bash` is the last resort.
 */
export function resolveBash(paBash: string, envBash: string, execPath: string, win32: boolean): string {
  const usable = (p: string): boolean => p.trim() !== '' && (!win32 || /[\\/]/.test(p))
  if (usable(paBash)) {
    return paBash.trim()
  }
  if (usable(envBash)) {
    return envBash.trim()
  }
  const m = /^(.*)\/mingw(?:64|32)\/libexec\/git-core\/?$/i.exec(execPath.trim().replace(/\\/g, '/'))
  if (m !== null && (m[1] ?? '') !== '') {
    return `${m[1] ?? ''}/bin/bash.exe`
  }
  return win32 ? '' : 'bash'
}

type GitRan = { exitCode: number; stdout: string; stderr: string }
/**
 * 3.28.1 T10: a git write under GIT_WRITE_MS. Killed or timed out (exit -1), or refused on `index.lock`: `clearLock`
 * (GIT_LOCK_PY's answer), then one retry unless a git process kept the lock; `note` says what happened when it still fails.
 */
export async function gitWrite(argv: string[], run: (argv: string[], ms: number) => Promise<GitRan>,
  clearLock: () => Promise<string>): Promise<{ r: GitRan; note: string }> {
  const r = await run(argv, GIT_WRITE_MS)
  if (r.exitCode === 0 || (r.exitCode !== -1 && !/index\.lock/.test(`${r.stderr}${r.stdout}`))) {
    return { r, note: '' }
  }
  const lock = (await clearLock()).trim()
  if (lock !== 'removed' && lock !== 'none') {
    return { r, note: 'index.lock kept: a git process is running, not retried' }
  }
  const again = await run(argv, GIT_WRITE_MS)
  return { r: again, note: again.exitCode === 0 ? '' : `retried once${lock === 'removed' ? ' after removing a stale index.lock' : ''}` }
}

/** The last assistant text of a transcript (JSONL, `{type: "assistant", message: {content: [...]}}` rows); null if none. */
export function lastAssistantText(jsonl: string): string | null {
  let last: string | null = null
  for (const line of jsonl.split(/\r?\n/)) {
    if (line.trim() === '') {
      continue
    }
    let row: unknown
    try {
      row = JSON.parse(line)
    } catch {
      continue
    }
    const r = row as { type?: unknown; message?: { content?: unknown } }
    if (r.type !== 'assistant') {
      continue
    }
    const content = r.message?.content
    const text = typeof content === 'string'
      ? content
      : Array.isArray(content)
        ? content.map(c => (typeof c === 'object' && c !== null && (c as { type?: unknown }).type === 'text' ? String((c as { text?: unknown }).text ?? '') : '')).join('')
        : ''
    if (text.trim() !== '') {
      last = text
    }
  }
  return last
}

/** `<project transcript dir>/<sid>/subagents/agent-<id>.jsonl` from the session's own `transcript_path`. */
export function subagentTranscript(transcriptPath: string, sid: string, agentId: string): string {
  const p = transcriptPath.replace(/\\/g, '/')
  return `${p.slice(0, p.lastIndexOf('/'))}/${sid}/subagents/agent-${agentId}.jsonl`
}

function jsonOf(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text.trim().split(/\r?\n/).pop() ?? '')
    return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** agentId is optional in a spawn's result: the newest agent the list added, of the spawned type when one is. */
const newAgent = (added: readonly { id: string; type: string }[], type: string): string =>
  (added.filter(a => a.type === type).pop() ?? added[added.length - 1])?.id ?? ''

/** A trial knob (`T<n>` or `T<n>:<text>`) names this task. */
const knob = (value: string, id: string): string | null => {
  const [who, ...rest] = value.split(':')
  return (who ?? '').trim() === id ? rest.join(':').trim() : null
}

/** The plan entry a loop task was spawned from (kept on the task in loop.json). */
const planOf = (t: LoopTask): PlanTask | null => {
  const p = (t as LoopTask & { plan?: unknown }).plan
  return typeof p === 'object' && p !== null ? (p as PlanTask) : null
}
const routedOf = (t: LoopTask): string => {
  const r = (t as LoopTask & { routed?: unknown }).routed
  return typeof r === 'string' ? r : ''
}

/** The question-or-block stop's notice: what stopped, the milestone pointer, who decides. */
export function questionNotice(task: string, what: string): string {
  return [`‹pa› ${task === '' ? 'the loop' : task} stopped: ${what}`, MILESTONE, DECIDE].join('\n')
}

/** The review stop's notice. */
export function reviewNotice(task: string): string {
  return `‹pa› ${task} stopped for your review: phase-ends/current/REVIEW.md. Present it to the developer with your recommendation; ` +
    'apply their choices with plan_edit (during this stop they apply at once); call release({hold: "review"}) when the developer approves. ' +
    `The release is refused while ${task} is open: when the developer approves, mark it done first with plan_edit (\`set-status ${task} done\`).`
}

/** 3.24 T8: the message that asks a task's expert for its missing `Review:` block (it resumes; nothing is killed). */
export function reviewAskText(task: string, summary: string, why: string): string {
  return `NOTE: ${task} returned STATUS review, but \`task_log.py review ${task}\` found no Review: block in ${summary} (${why}). ` +
    'Add it to the summary: a flush-left `Review:` line, then indented `ran:`, `results:`, `seen:`, `decisions:` and `edits:` lines ' +
    '(decisions and edits as `- ` items under their key). Commit the summary, then return the same contract again.'
}

/** 3.24 T8: the review stop's notice when the summary still has no Review: block: Claude presents from the summary. */
export function reviewSummaryNotice(task: string, summary: string, why: string): string {
  return `‹pa› ${task} stopped for your review, but its summary has no Review: block (\`task_log.py review\`: ${why}), so there is ` +
    `no REVIEW.md. Present the review to the developer from the summary ${summary}, with your recommendation; ` +
    'apply their choices with plan_edit (during this stop they apply at once); call release({hold: "review"}) when the developer approves. ' +
    `The release is refused while ${task} is open: when the developer approves, mark it done first with plan_edit (\`set-status ${task} done\`).`
}

/** The WAIT-FOR timeout's review notice (T5d2a). */
export function waitReviewNotice(task: string, file: string, literal: string): string {
  return `‹pa› ${task} waited 50 min for ${file} to contain ${literal} and stopped for your review: phase-ends/current/REVIEW.md. ` +
    'Present it to the developer with your recommendation; apply their choices with plan_edit (during this stop they apply at once); ' +
    `call release({hold: "review"}) when they decide. Unchanged, ${task} waits again.`
}

/** A whole JSON file's object (health.json is indented), null when it is not one. */
function objectOf(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text)
    return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** loop.json's `inboxDone` (request id -> ms done), the loop's own drained requests. */
const doneOf = (s: LoopState): Record<string, number> => {
  const d = (s as LoopState & { inboxDone?: unknown }).inboxDone
  return typeof d === 'object' && d !== null && !Array.isArray(d) ? (d as Record<string, number>) : {}
}

/** 3.24 T12c: `sid`'s session start has not had its first owning tick yet (its `session` hold not judged): the router's
 * first message must not get the talk line that tick is about to supersede (a `--continue` restart routed the developer's
 * message ~2 s before the hold, and Claude released `talk`, a silent no-op). */
export const sessionStartPending = (sid: string): boolean => sid !== '' && startSid === sid

/** Registers the autopilot's hooks on the router's `on`; called once, last, from register.tsx's `register`. */
export function armAutopilot(on: On): void {
  on('session.start', {}, async ($, e, next) => {
    const out = await next(e)
    const root = await $.session.root()
    const sid = await $.session.id()
    const debug = (await $.env.get('PA_MOD_DEBUG')) === '1'
    if (debug && (await read($, genAtom)) !== 0) { // 3.24 T8: gen 0 is an inert router, which writes nothing
      const ms = Date.now()
      counter += 1
      const file = eventFile(`${root}/.run/mod/${sid}/debug`, ms, counter, 'autopilot.armed')
      await $.fs.write(file, JSON.stringify({ ms, ev: 'autopilot.armed', sid }, null, 1)).catch(quiet)
    }

    // Armed under the router's current generation (0: the router is inactive, no loop). Its one tick re-reads loop.json.
    const arm = async (sidNow: string, fresh = false): Promise<void> => {
      const gen = await read($, genAtom)
      if (gen === 0 || (!fresh && armedAp.sid === sidNow && armedAp.gen === gen)) {
        return
      }
      const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
      const python = paSetting(paText, 'python')
      if (python === '') {
        return
      }
      armedAp = { sid: sidNow, gen }
      procSids.add(sidNow)
      const loopPath = `${root}/${LOOP_FILE}`
      // 3.24 T6: PA3's machine root (a trial's snapshot under PA_MOD_PA3_DIR); production is PA_MOD_PA3_DIR empty.
      const pa3Env = (await $.env.get('PA_MOD_PA3_DIR')) ?? ''
      const pa3 = pa3Env || `${((await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '').replace(/\\/g, '/')}/.claude/pa3`
      const updateOn = pa3Env === '' || (await $.env.get('PA_MOD_UPDATE')) === '1'
      if (modSeen === null) {
        modSeen = modFilesKey(await $.fs.read(`${pa3}/MANIFEST.json`).catch(() => null))
      }
      const log = async (kind: string, data: Record<string, unknown>): Promise<void> => {
        if (debug) {
          const ms = Date.now()
          counter += 1
          await $.fs.write(eventFile(`${root}/.run/mod/${sidNow}/debug`, ms, counter, `autopilot.${kind}`),
            JSON.stringify({ ms, ev: `autopilot.${kind}`, ...data }, null, 1)).catch(quiet)
        }
      }
      type Ran = { exitCode: number; stdout: string; stderr: string }
      const run = async (argv: string[], timeoutMs = PROC_MS): Promise<Ran> => {
        const t0 = Date.now() // 3.24 T10b: each call's start and duration, so a stall shows in the debug log
        try {
          const r = await $.process.run(argv, { cwd: root, timeoutMs })
          await log('run', { argv: argv.slice(1, 4), exitCode: r.exitCode, t0, durMs: Date.now() - t0 })
          return { exitCode: r.exitCode, stdout: r.stdout, stderr: r.stderr }
        } catch (err) {
          await log('run.error', { argv: argv.slice(1, 4), err: clip(String(err), 300), t0, durMs: Date.now() - t0 })
          return { exitCode: -1, stdout: '', stderr: clip(String(err), 300) }
        }
      }
      const py = async (...args: string[]): Promise<Ran> => run([python, ...args])
      let bash = ''
      const commit = async (msg: string, paths: string[], id = 'router'): Promise<string> => {
        if (bash === '') {
          const win32 = (await $.env.get('OS')) === 'Windows_NT'
          const paBash = paSetting(paText, 'bash')
          const envBash = (await $.env.get('CLAUDE_CODE_GIT_BASH_PATH')) ?? ''
          const named = (p: string): boolean => p.trim() !== '' && (!win32 || /[\\/]/.test(p))
          let exec = ''
          if (!named(paBash) && !named(envBash)) {
            const r = await run(['git', '--exec-path'])
            exec = r.exitCode === 0 ? r.stdout.trim() : ''
          }
          bash = resolveBash(paBash, envBash, exec, win32)
          await log('bash', { bash })
        }
        if (bash === '') {
          return 'no Git Bash (pa.json bash, CLAUDE_CODE_GIT_BASH_PATH, git --exec-path)'
        }
        // 3.28.1 T10: its own limit; a killed one clears a stale lock and retries once, its note in the caller's one line.
        const { r, note } = await gitWrite([bash, 'tools/commit_task.sh', id, msg, ...paths], run, async () => {
          const at = await run(['git', 'rev-parse', '--git-path', 'index.lock'])
          const lock = at.exitCode === 0 ? at.stdout.trim() : ''
          const out = lock === '' ? 'busy' : (await run([python, '-c', GIT_LOCK_PY, lock])).stdout
          await log('git.lock', { lock, out: clip(out.trim(), 100) })
          return out
        })
        return r.exitCode === 0 ? '' : `commit "${msg}" exit ${String(r.exitCode)}: ${clip(r.stderr || r.stdout, 200)}${note === '' ? '' : ` (${note})`}`
      }
      const journal = async (who: string, kind: string, meta: Meta, body: string): Promise<void> => {
        jseq += 1
        let o: { rel: string; text: string }
        try {
          o = journalOut(await $.clock.now(), jseq, who, kind, { session: sidNow, ...meta }, body)
        } catch (err) {
          // The naming rejected it (an unknown who or kind): visible, never silently dropped (3.23 T7).
          await log('journal.bad', { who, kind, err: clip(String(err), 300) })
          return
        }
        try {
          await $.fs.write(`${root}/${o.rel}`, o.text)
        } catch (err) {
          await log('journal.error', { err: clip(String(err), 300) })
        }
      }

      // 3.29.9: the display trim (trim.ts). The loop state's `doneMark` (the last completed task's `trimMark`, this
      // session's) is found again in the main conversation and the rows before it are compacted away by the watcher
      // (pa-watch answers the `pa-trim` compaction; no model). Off the loop's path: a refusal while a turn runs retries,
      // a headless run skips; a trim is one `mod/note` journal line, a skip or a give-up a debug event.
      const trimFile = `${root}/${TRIM_FILE}`
      const trimRun = async (why: string): Promise<void> => {
        for (let tries = 1; ; tries += 1) {
          const dm = markOf((parseLoop(await $.fs.read(loopPath).catch(() => null)) as { doneMark?: unknown }).doneMark)
          if (dm === null || dm.sid !== sidNow) {
            await log('trim.skip', { why, reason: 'no mark' })
            return
          }
          // H1.c3: a mark already trimmed (its own file, apart from loop.json's saves) is never searched again
          const done = objectOf((await $.fs.read(trimFile).catch(() => null)) ?? '')
          if (done !== null && done['sid'] === dm.sid && done['task'] === dm.task && done['len'] === dm.len) {
            await log('trim.skip', { why, task: dm.task, reason: 'trimmed' })
            return
          }
          const rows = await $.session.messages().catch(() => null)
          const k = rows === null ? -1 : cutPoint(rows, dm)
          if (rows === null || k < 0) {
            await log('trim.skip', { why, task: dm.task, reason: 'no cut point', rows: rows?.length ?? -1 })
            return
          }
          try {
            const r = await $.session.compact({ instructions: trimInstructions(rows, k) })
            if (r.skip !== undefined) {
              await log('trim.skip', { why, task: dm.task, reason: r.skip })
              return
            }
            await $.fs.write(trimFile, `${JSON.stringify({ sid: dm.sid, task: dm.task, len: dm.len, at: await $.clock.now() })}\n`).catch(quiet)
            await journal('mod', 'note', { task: dm.task },
              `Display trimmed (${why}): the main conversation kept from ${dm.task}'s start, ${String(k)} rows dropped, ${String(r.messages.length)} kept.`)
            return
          } catch (err) {
            const fail = trimFailure(err, tries, TRIM_TRIES)
            if (fail === 'headless') {
              await log('trim.skip', { why, task: dm.task, reason: 'headless' })
              return
            }
            if (fail === 'giveup') {
              await log('trim.giveup', { why, task: dm.task, tries, err: clip(String(err), 200) })
              return
            }
            await log('trim.retry', { why, task: dm.task, tries, err: clip(String(err), 200) })
          }
          await new Promise<void>(res => { $.clock.after(TRIM_RETRY_MS, res) })
        }
      }
      const trimSoon = (why: string): void => {
        $.clock.after(TRIM_DELAY_MS, () => {
          void (async () => {
            if (trimBusy) {
              trimAgain = why
              return
            }
            trimBusy = true
            try {
              let w = why
              while (w !== '') {
                trimAgain = ''
                await trimRun(w)
                w = trimAgain
              }
            } finally {
              trimBusy = false
            }
          })().catch(quiet)
        })
      }
      apTrim = trimSoon
      // The loop state's `doneMark` at task `id`'s done: its `trimMark` when this session marked it, else none.
      const doneMarkOf = (st: LoopState, id: string): { doneMark: unknown } => {
        const m = markOf((st as { trimMark?: unknown }).trimMark)
        return { doneMark: m !== null && m.task === id && m.sid === sidNow ? m : null }
      }

      // A notice to Claude through the router's inbox (the router writes its own state.json): text, a toast cause, and
      // `to`, a request's caller for a reply.
      const inboxDir = `${root}/.run/mod/${sidNow}/inbox`
      const notice = async (text: string, toast: ToastOut['cause'] | '' = '', to = ''): Promise<void> => {
        nseq += 1
        const r = noticeRequest(await $.clock.now(), nseq, text, toast, to)
        try {
          await $.fs.write(`${inboxDir}/${r.name}`, r.body)
          await log('notice', { name: r.name, toast, to, text: clip(text, 200) })
        } catch (err) {
          await log('notice.error', { err: clip(String(err), 300) })
        }
      }

      // 3.23 T13d.c3: a confirmation to its caller: sent as a notice, or, when it only says done (quietReply), journaled
      // as the entry the sent notice gets (`mod prompt`, role loop; register.tsx) without waking Claude.
      const confirm = async (line: string, to: string): Promise<void> => {
        if (quietReply(line)) {
          await journal('mod', 'prompt', { role: 'loop' }, line)
        } else {
          await notice(line, '', to)
        }
      }

      const dialogFile = `${root}/.run/mod/${sidNow}/${DIALOG_FILE}`
      const dialogIsOpen = async (): Promise<boolean> => dialogOpen(await $.fs.read(dialogFile).catch(() => null))

      // 3.23 T13: the band above the prompt, from loop.json, the plan's phase, `$.agent.list()` and the watcher's facts
      // files; the atom is written only when it changed, and the AbovePrompt rule (register.tsx) only draws it.
      const bandTick = async (): Promise<void> => {
        const now = await $.clock.now()
        const s = parseLoop(await $.fs.read(loopPath).catch(() => null))
        const t = s.task
        const roots = [
          ...(t !== null && t.agentId !== '' ? [{ id: t.agentId, task: t.id }] : []),
          ...(sideAgent === '' ? [] : [{ id: sideAgent, task: '' }]),
        ]
        const listed = roots.length === 0 ? [] : await $.agent.list()
        const agents: BandAgent[] = listed.map(a => {
          if (!bandSince.has(a.id)) {
            bandSince.set(a.id, a.id === t?.agentId && t.spawnedAt > 0 ? t.spawnedAt : now)
          }
          return { id: a.id, type: a.type || (a.id === t?.agentId ? t.agent : ''), name: a.name, status: a.status, parentId: a.parentId, description: a.description, since: bandSince.get(a.id) ?? now }
        })
        const shells = shellsOf(await $.fs.read(`${root}/.run/mod/${sidNow}/${SHELLS_FILE}`).catch(() => null))
        for (const { id } of bandIds(agents, roots, new Set(Object.keys(shells)))) {
          // 3.23 T13d.c2: the agent's latest working line (the facts' `line`), else its last step.
          const step = bandStepOf(jsonOf((await $.fs.read(`${root}/.run/mod/${sidNow}/agents/${id}.json`).catch(() => null)) ?? ''))
          const a = agents.find(x => x.id === id)
          if (a !== undefined && step !== undefined) {
            a.step = step
          }
        }
        const phase = planPhase((await $.fs.read(`${root}/${PLAN}`).catch(() => null)) ?? '').phase
        const band: Band = { line: bandLine(s, phase), rows: bandRows(agents, roots, now, shells) }
        if (JSON.stringify(band) !== JSON.stringify(await read($, bandAtom))) {
          await update($, bandAtom, () => band)
        }
      }

      const step = async (): Promise<void> => {
        const hasPlan = await $.fs.exists(`${root}/${PLAN}`)
        const now = await $.clock.now()
        let s: LoopState = parseLoop(await $.fs.read(loopPath).catch(() => null))
        // launch.py's mode, cached GEN_MODE_MS for this session (T6a2).
        const modeOf = async (): Promise<string> => {
          if (genMode.sid !== sidNow || genMode.at === 0 || now - genMode.at >= GEN_MODE_MS) {
            const st = await py('tools/launch.py', '--state-json')
            genMode = { sid: sidNow, at: now, doc: st.exitCode === 0 ? jsonOf(st.stdout) : null }
          }
          return strOf(genMode.doc?.['mode'])
        }
        const g0 = genStartOf(s)
        // 3.24 T7b: the project's first run in 3.2 still owed this session (pa.json `first_run`; firstRunLine), '' none.
        // T7b-2c: `firstPending` while it is not done (asked or paused this session too): no plan is installed or held for.
        const firstLine = firstRunLine(await $.fs.read(`${root}/.claude/pa.json`).catch(() => null), MEMORY_DIR)
        const firstPending = firstLine !== ''
        const firstRun = g0 === null || g0.sid !== sidNow ? firstLine : ''
        // No phase plan: the loop wakes only for a generation plan, an install in progress or its own requests (T6a1); T6a2:
        // also for a live generation start of this session, owed hook-context lines, an update ask, or launch.py's
        // planner-gen with none started this session; 3.24 T7b: or a first run owed.
        if (!hasPlan && installOf(s) === null && !(await $.fs.exists(`${root}/${GEN_PLAN}`))) {
          const owed = (await $.fs.exists(inboxDir))
            ? pendingRequests((await $.fs.list(inboxDir)).filter(f => f.kind === 'file').map(f => f.name), doneOf(s), now).filter(isLoopKind)
            : []
          const live = (g0 !== null && g0.sid === sidNow && g0.step !== 'done') || contextQ.some(c => c.sid === sidNow) || updateOf(s) !== null
          if (owed.length === 0 && !live && firstRun === '' && !((g0 === null || g0.sid !== sidNow) && (await modeOf()) === 'planner-gen')) {
            startSid = startSid === sidNow ? '' : startSid // nothing would act: the session start needs no hold
            return
          }
        }
        // T13b.c4: after a `/clear` the owner is this process's previous session (its heartbeat still fresh): owned at once.
        const prevSid = clearSid === sidNow && procSids.has(s.ownerSid) ? s.ownerSid : ''
        if (!ownsLoop(s, sidNow, now, prevSid)) {
          loopAgent = ''
          return
        }
        const save = async (n: LoopState, what: string): Promise<void> => {
          s = { ...n, ownerSid: sidNow, phaseSids: withPhaseSid(n.phaseSids, sidNow), heartbeatAt: await $.clock.now() }
          await $.fs.write(loopPath, serializeLoop(s))
          if (what !== '') {
            await log('loop', { what, state: s.state, holds: s.holds, task: s.task?.id ?? '' })
          }
        }
        // T5d1: a takeover (another owner, or a stale heartbeat: a restart), judged before this tick's first save.
        const taken = takesOver(s, sidNow, now, prevSid)
        // 3.23 T13d.c11: nothing starts on its own. A session start (SessionStart startup, resume or clear) holds the loop
        // in `session` at this session's first owning tick when it would act: a task to spawn or recover, the close chain,
        // the next generation's start (the generation park stays under this hold; its release lifts both), a generation
        // start in flight or a phase plan to draft. Silence never ends it; Claude's release({hold: "session"}) does, when
        // the developer's message asks for the loop to go on. (Was T13b.c3: a fresh session lifted the generation park at
        // once; a resume kept it.)
        if (startSid === sidNow) {
          startSid = ''
          const parked = s.holds.includes('generation')
          const held = s.holds.some(h => h !== 'talk' && h !== 'window')
          const genLive = g0 !== null && g0.sid === sidNow && g0.step !== 'done'
          const acts = parked || (!held && (s.state !== 'off' || genLive || firstRun !== '' || (hasPlan
            ? (await modeOf()) === 'router'
            : (await modeOf()) === 'planner-gen' || (await $.fs.exists(`${root}/${GEN_PLAN}`)))))
          if (acts) {
            await save(pushHold(s, 'session'), 'session')
            await journal('mod', 'hold', { task: s.task?.id }, 'session: a session start; the loop waits for the developer (release session).')
          }
        }
        // 3.28.1 T15: the running task's expert or one of its children handed back `WAITING: shell <what>`: Claude tells
        // the developer in one line (part d); the shell's completion notice wakes the agent by itself.
        if (shellQ.length > 0) {
          const seen = shellQ
          shellQ = []
          const t = s.task
          const list = t === null || t.agentId === '' ? [] : await $.agent.list().catch(() => [])
          for (const q of seen) {
            const child = list.find(a => a.id === q.agentId && a.parentId === t?.agentId)
            if (t === null || (q.agentId !== t.agentId && child === undefined)) {
              continue
            }
            const role = q.agentId === t.agentId ? 'expert' : /coder/.test(child?.type ?? '') ? 'coder' : child?.type || 'agent'
            await journal(t.id, 'shell', { task: t.id, agentId: q.agentId }, `${q.agentId} waits on its background shell: ${q.what}`)
            await notice(shellNotice(t.id, role, q.agentId, q.what))
          }
        }
        // A block: status.json waits on a question, `held(question)`; the journal says why; Claude gets the stop's notice
        // (`what`, else the reason). Every block is a stop: its toast cause is `question` by default (T13b; raised by
        // Claude's presentation, router side).
        const block = async (n: LoopState, reason: string, who: string, kind: string, body: string, what = '',
          toast: ToastOut['cause'] | '' = 'question'): Promise<void> => {
          await py('tools/status.py', 'wait', 'question', '--note', clip(reason, 200))
          await journal(who, kind, { task: n.task?.id }, body === '' ? reason : `${body}\n\n(${reason})`)
          await save(pushHold(n, 'question'), `block: ${reason}`)
          await notice(questionNotice(n.task?.id ?? '', what === '' ? reason : what), toast)
        }
        // The agent's last step as the watcher saw it (facts `updatedAt`, ms; 0 when absent): runEnded's activity (T11-fix).
        const activityAt = async (agentId: string): Promise<number> => {
          const v = jsonOf((await $.fs.read(`${root}/.run/mod/${sidNow}/agents/${agentId}.json`).catch(() => null)) ?? '')?.['updatedAt']
          return typeof v === 'number' && Number.isFinite(v) ? v : 0
        }
        // T5d1: the heartbeat every 15 s. A takeover (`taken`, above) of a running task with no stop starts recovery, a step
        // a tick in `task.recovery`.
        if (s.ownerSid !== sidNow || now - s.heartbeatAt >= HEARTBEAT_MS) {
          await save(s, taken ? 'takeover' : '')
        }
        // 3.23 T13d.c11: under `held(session)` the recovery is recorded; 3.28.1 T9c: it runs at once (no release needed).
        if (taken && s.state === 'running' && s.task !== null && !s.holds.some(h => h !== 'talk' && h !== 'session')) {
          if (runReviewDue) {
            // 3.29.7: an update's after-restart work (the one-time `.run/` cleanup) comes first: the task is held as
            // stopped until it is done and the developer says to continue (Claude asks; release stopped).
            const pt = s.task
            await save(pushHold({ ...s, task: { ...pt, stopped: { at: now, status: 'post-update' }, recovery: null } }, 'stopped'), 'post-update.held')
            await journal('mod', 'hold', { task: pt.id, agentId: pt.agentId },
              `${pt.id} held at this session start: the update's after-restart work (the .run/ cleanup) comes first.`)
          } else if ((s.task.paused ?? null) !== null) {
            // 3.29.1 T9: paused on the developer's request: never recovered on its own; held as stopped, and once the
            // update check lets work go on, Claude asks whether to resume (release stopped).
            const pt = s.task
            await save(pushHold({ ...s, task: { ...pt, stopped: { at: now, status: 'paused' }, recovery: null } }, 'stopped'), 'paused.held')
            await journal('mod', 'hold', { task: pt.id, agentId: pt.agentId },
              `${pt.id} was paused on the developer's request: not resumed at this session start; Claude asks.`)
          } else {
            await save({ ...s, task: { ...s.task, recovery: { phase: 'start', at: now } } }, 'recover')
          }
        }
        loopAgent = s.task?.agentId ?? ''
        routedText = s.task?.lastRouted ?? ''
        const gs = genStartOf(s)
        sideAgent = gs === null || gs.step === 'done' ? '' : gs.agentId
        // hook-context lines (T6a2), each once a session: filed `mod/context`; the renewal line a notice; an update line
        // the update ask's record (3.24 T6: production, or PA_MOD_UPDATE=1), else a notice.
        const ctx = contextQ.filter(c => c.sid === sidNow)
        if (ctx.length > 0) {
          contextQ = contextQ.filter(c => c.sid !== sidNow)
          for (const c of ctx) {
            const line = c.kind === 'renewal' ? c.line : updateFact(c.line) // 3.28.6 T19: the fact, never the steps
            await journal('mod', 'context', {}, line)
            if (c.kind === 'renewal') {
              await notice(renewalNotice(c.line))
            } else if (updateOn && updateOf(s) === null) {
              await save({ ...s, update: { line, asked: '', answer: null } } as LoopState, 'update')
            } else {
              await notice(updateNotice(line))
            }
          }
        }
        // 3.24 T6: a finished update run reported once (the hook's detached run or heal, or the mod's own): the whole
        // log to the journal and the debug log; "from X to Y", CHANGES.md, a restart, or the failure to Claude; conflicts
        // open the next UPGRADE round (at most UPGRADE_ROUNDS); the mod's own files changed: the reload notice, once.
        const um = markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null))
        if (um !== null && runToReport(um, updateSeenOf(s), now)) {
          const whole = (await $.fs.read(`${root}/${UPDATE_LOG}`).catch(() => null)) ?? ''
          const o = updateOutcome(whole)
          const conflicts = um.rc === 0 && (o.conflicts || (await $.fs.exists(`${root}/${UPGRADE_MD}`)))
          const rounds = upgradeOf(s)?.round ?? 0
          const upgrade = conflicts && rounds < UPGRADE_ROUNDS ? { agentId: '', at: 0, round: rounds + 1 } : null
          await journal('mod', 'decision', {}, `PA3 ${um.mode} finished (exit ${String(um.rc)}), the project from ` +
            `${um.from.phase || '?'} ${um.from.git} to ${um.to.phase || '?'} ${um.to.git}; its whole output:\n\n${whole}`)
          await log('update.done', { mode: um.mode, rc: um.rc, from: um.from, to: um.to, conflicts, restart: o.restart, output: whole })
          await save({ ...s, updateSeenAt: um.atMs, upgrade } as LoopState, 'update.seen')
          const loaded = um.mode !== 'repair' && versionAtLeast(modLoaded, um.to.phase) === true // 3.28.2 T17: a restart loaded it
          await notice(updateDoneNotice(um, { restart: o.restart, conflicts: upgrade !== null, loaded }, pa3, whole.trimEnd().split(/\r?\n/).slice(-12).join('\n')))
          if (conflicts && upgrade === null) {
            await notice(upgradeLeftNotice(rounds))
          }
          const key = um.rc === 0 && !modStale ? modFilesKey(await $.fs.read(`${pa3}/MANIFEST.json`).catch(() => null)) : ''
          if (key !== '' && (modSeen ?? '') !== '' && key !== modSeen) {
            modStale = true
            await journal('mod', 'hold', {}, 'The update changed the mod\'s own files: no task starts until the new mod loads (the next start or /reload-plugins).')
            await notice(RELOAD_NOTICE)
          }
          // 3.28.1 T14: the release the developer chose to install is installed: no task starts on the old code until
          // the restart (updateDoneNotice told Claude: /exit, then claude --continue); a toast says so too.
          // 3.29.2: a run the start hook began records its release in the marker (`running`): installed, the statusline's
          // "installed" stage until the restart; failed, the line goes. The mod's own install (below) does the same.
          const hookRel = objectOf((await $.fs.read(`${root}/${RELEASE_MARKER}`).catch(() => null)) ?? '')
          if (releaseOf(s) === null && hookRel?.['state'] === 'running' && um.mode !== 'repair') {
            const done = um.rc === 0 ? { ...hookRel, state: 'installed' } : {}
            await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify(done)}\n`).catch(quiet)
          }
          const rl = releaseOf(s)
          if (rl !== null && rl.step === 'running') {
            if (um.rc === 0 && !loaded) {
              modStale = true
              await journal('mod', 'hold', {}, `PA3 ${rl.version} is installed: no task starts until Claude Code restarts (/exit, then claude --continue).`)
              nseq += 1
              const r = requestFile(now, nseq, 'toast', 'loop', { cause: 'input', body: `Project Architect ${rl.version} ${rl.kind} installed · /exit, then claude --continue (not /clear)` })
              await $.fs.write(`${inboxDir}/${r.name}`, r.body).catch(quiet)
              // 3.28.5 T18: the run finished with exit 0: the statusline's "installed" stage
              await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify({ version: rl.version, kind: rl.kind, state: 'installed' })}\n`).catch(quiet)
            } else if (um.rc !== 0) {
              await $.fs.write(`${root}/${RELEASE_MARKER}`, '{}\n').catch(quiet) // failed (told above): the line goes
            }
            await save({ ...s, release: { ...rl, step: 'done' } } as LoopState, 'release.done')
          } else if (rl !== null && rl.step !== 'done' && um.rc === 0) {
            // 3.28.2 T17: the session start's run installed the release (a restart before the clean stop): the marker
            // names it again, so the next start gives the loaded line once (the start emptied it before the run ended).
            const installed = ((await $.fs.read(`${pa3}/VERSION`).catch(() => null)) ?? '').split(/\r?\n/)[0]?.replace(/^version:/, '').trim() ?? ''
            if (installed === rl.version) {
              await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify({ version: rl.version, kind: rl.kind, state: 'install' })}\n`).catch(quiet)
              await save({ ...s, release: { ...rl, step: 'done' } } as LoopState, 'release.done')
            }
          }
        }
        // 3.28.2 T17: this process runs the release (whichever path installed it): the record and the statusline's line go.
        const rlLoaded = releaseOf(s)
        if (rlLoaded !== null && versionAtLeast(modLoaded, rlLoaded.version) === true) {
          await save({ ...s, release: null } as LoopState, 'release.loaded')
          await $.fs.write(`${root}/${RELEASE_MARKER}`, '{}\n').catch(quiet)
        }
        // 3.28.1 T14: every RELEASE_CHECK_MS of a running session, the session start's release check (pa_ledger.py
        // update-check: the clone's fetch, I48). A release not asked about before: the loop's picker (tasks keep running;
        // no answer is not now), one line to Claude, and the statusline's marker.
        if (releaseCheckedAt === 0) { // the last check's time: the session start's (update.json `checked_at`, s), else now
          const at = jsonOf((await $.fs.read(`${pa3}/update.json`).catch(() => null)) ?? '')?.['checked_at']
          releaseCheckedAt = typeof at === 'number' && Number.isFinite(at) && at > 0 && at * 1000 <= now ? at * 1000 : now
        } else if (now - releaseCheckedAt >= RELEASE_CHECK_MS && updateOn && !modStale && !runRunning(um, now)) {
          releaseCheckedAt = now
          const r = await run([python, `${pa3}/pa_ledger.py`, 'update-check', root])
          const found = r.exitCode === 0 ? jsonOf(r.stdout.trim()) : null
          const version = strOf(found?.['version'])
          await log('release.check', { exitCode: r.exitCode, version })
          if (version !== '' && version !== releaseOf(s)?.version) {
            const kind = strOf(found?.['kind']) || 'hotfix'
            const title = strOf(found?.['title'])
            // 3.28.6 T19: the facts only; the hook's line (its pa3_update.py steps) never reaches Claude or the seed
            await journal('mod', 'context', {}, `PA3 ${version}, a ${kind}${title === '' ? '' : ` (${title})`}, is out (the release check).`)
            nseq += 1
            const q = requestFile(now, nseq, 'ask', 'loop', { question: releaseQuestion(version, kind, title), options: [...RELEASE_OPTIONS] })
            await $.fs.write(`${inboxDir}/${q.name}`, q.body)
            await save({ ...s, release: { version, kind, asked: q.name.slice(0, -'.json'.length), step: 'asked' } } as LoopState, 'release.ask')
            await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify({ version, kind, state: 'later' })}\n`).catch(quiet)
            await notice(releaseNotice(version, kind))
          }
        }
        const t0 = s.task
        const f0 = t0 === null ? undefined : facts[t0.agentId]
        if (t0 !== null && f0 !== undefined && f0.lastCompleteAt > t0.lastCompleteAt) {
          await save({ ...s, task: { ...t0, lastCompleteAt: f0.lastCompleteAt } }, '')
        }
        if (windowResumes(s, now)) {
          await save({ ...popHold(s, 'window'), resumeAt: 0 }, 'window.resume')
        }
        // The hold (T5b2): the hooks' facts land here. A developer prompt while the loop is active pushes `talk`; Claude's
        // answers record whether it asked; silence pops `talk`.
        // 3.23 T13d.c11: under `held(session)` no `talk` (the session hold holds everything; Claude's release lets it go).
        if (devAt > s.lastDevAt) {
          const still = s.state === 'off' || s.holds.includes('session')
          const fresh = !still && !s.holds.includes('talk')
          await save({ ...(still ? s : pushHold(s, 'talk')), lastDevAt: devAt }, fresh ? 'talk' : '')
          if (fresh) {
            await journal('mod', 'hold', { task: s.task?.id }, `talk: the queue is held while the developer talks${s.task === null ? '' : ` (${s.task.id} running)`}.`)
          }
        }
        if (partnerSaid.at > s.lastAnswerAt) {
          const said = partnerSaid
          await save({ ...s, lastAnswerAt: said.at, ...(said.notice ? {} : { talkAnswerAt: said.at, lastAnswerAsked: said.asked }) }, '')
        }
        if (silenceReleases(s, now)) {
          await save(popHold(s, 'talk'), 'talk.silence')
          await journal('mod', 'hold', { task: s.task?.id }, 'talk released: 15 min with no developer message and no question pending; the loop goes on.')
        }

        // Status, then the spawn; agentId, spawnedAt and startSha recorded, and a `T<n>/start` entry.
        const spawnTask = async (plan: PlanTask, attempt: number, kind: string, opts: ExpertOpts): Promise<void> => {
          const agent = agentFor(plan.agent, plan.effort)
          const effort = effortFor(plan.agent, plan.effort)
          const title = plan.title ?? ''
          const startSha = (await run(['git', 'rev-parse', 'HEAD'])).stdout.trim()
          const spawnedAt = await $.clock.now()
          const task: LoopTask = {
            id: plan.id, title, agent, coder: plan.coder ?? 'none', effort, attempt, kind, agentId: '',
            startSha, spawnedAt, lastCompleteAt: 0, contract: null,
          }
          // 3.29.9: a task's first spawn marks where it began in the main conversation (the display trim's `trimMark`);
          // a relaunch or handoff keeps the mark already there.
          const msgs = kind === 'task' ? await $.session.messages().catch(() => null) : null
          const mark = msgs === null ? {} : { trimMark: { sid: sidNow, task: plan.id, len: msgs.length, tail: tailOf(msgs, msgs.length), anchor: anchorOf(msgs, msgs.length) } }
          await save({ ...s, state: 'running', task: { ...task, plan } as LoopTask, ...mark } as LoopState, '')
          // 3.24 T7b: the brief carries the card's expert slice, so the card reaches every task by construction.
          const sl = await py('tools/card.py', 'slice', 'expert')
          const card = sl.exitCode === 0 ? sl.stdout : ''
          const before = new Set((await $.agent.list()).map(a => a.id))
          const r = await $.agent.spawn({ subagentType: agent, prompt: expertBrief(plan, { ...opts, card }),
            description: `${`${plan.id} ${title}`.trim()}${effort === 'medium' ? '' : ` ${effortHint(effort)}`}` })
          const id = r.deny !== undefined
            ? ''
            : r.agentId ?? newAgent((await $.agent.list()).filter(a => !before.has(a.id)), agent)
          if (id === '') {
            await block(s, `spawn of ${agent} for ${plan.id} failed: ${clip(r.deny ?? 'no agentId', 200)}`, 'mod', 'hold', '')
            return
          }
          loopAgent = id
          // The task after this one, for the router's talk line (T5b2).
          const rows = await py('tools/plan_edit.py', 'show', '--tasks')
          const nextId = rows.exitCode === 0 ? nextTaskOf(rows.stdout, plan.id) : ''
          // Its place in the plan, for the band's `T3 (2 of 5)` (3.23 T13).
          const taskPos = rows.exitCode === 0 ? taskPosOf(rows.stdout, plan.id) : null
          await save({ ...s, task: { ...task, plan, agentId: id } as LoopTask, nextId, taskPos } as LoopState, 'spawn')
          await journal(plan.id, 'start', { agentId: id, role: agent, task: plan.id },
            `${agent} ${id} started on ${plan.id} ${title} (${kind}, attempt ${String(attempt)}); start ${startSha || '?'}.`)
          // 3.23 T13d.c3: the start card is one feed record, labelled as this task's expert (zero tokens).
          await feedIn(at => startRecord({ agent: id, type: agent, id: plan.id, title, at }))
        }

        // A record the router writes into the feed's `in/` (3.23 T13d.c3/c4), named `nudgeName(ms, seq, '8')`.
        const feedIn = async (body: (at: number) => string): Promise<void> => {
          const fdir = (await read($, stateAtom) as { feed?: { dir?: string } } | null)?.feed?.dir ?? feedDirOf(sidNow)
          const at = await $.clock.now()
          sseq += 1
          await $.fs.write(`${root}/${fdir}/in/${nudgeName(at, sseq, '8')}`, body(at)).catch(quiet)
        }

        // A plan change, split without a shell (contract.splitCommand), run, then committed (PLAN, plus `rules` for
        // rules_add.py); `ran` follows the run (or the refusal). The caller's `applied: <first output line>` or `refused:`.
        const applyEdit = async (command: string, why: string, what: string, ran = async (): Promise<void> => undefined): Promise<string> => {
          const sp = splitCommand(command, python)
          if ('refused' in sp) {
            await ran()
            return `refused: ${sp.refused}`
          }
          const r = await run(sp.argv)
          await ran()
          if (r.exitCode !== 0) {
            return `refused: ${command}: exit ${String(r.exitCode)} ${clip((r.stderr || r.stdout).trim(), 200)}`
          }
          const rules = sp.argv[1] === 'tools/rules_add.py'
          const failed = await commit(clip(why.trim() === '' ? command : why.trim(), 90), rules ? [PLAN, 'rules'] : [PLAN])
          await journal('mod', 'hold', { task: s.task?.id }, `${what} applied: ${command}${why === '' ? '' : ` (${why})`}.`)
          const first = r.stdout.trim().split(/\r?\n/)[0]?.trim() || command
          return failed === '' ? `applied: ${first}` : `applied: ${first} (not committed: ${failed})`
        }

        // Queued plan changes, oldest first; each leaves the queue once run (a restart mid-way re-runs only the one in flight).
        const landQueued = async (): Promise<void> => {
          for (let e = s.queuedEdits[0]; e !== undefined; e = s.queuedEdits[0]) {
            const head = e
            const line = await applyEdit(head.command, head.why, 'Queued change',
              async () => save({ ...s, queuedEdits: s.queuedEdits.slice(1) }, 'plan_edit.landed'))
            await confirm(line, head.caller)
            await log('plan_edit', { line: clip(line, 200), queued: true, sent: !quietReply(line) })
          }
        }

        // The close's boundary (T6b): queued edits, inbox-consume's items to Claude, then `next` (null: blocked).
        const closeBoundary = async (): Promise<Record<string, unknown> | null> => {
          await landQueued()
          const ic = await py('tools/status.py', 'inbox-consume')
          const items = ic.stdout.split(/\r?\n/).filter(l => l.trim() !== '' && l.trim() !== 'none').join('\n')
          if (items !== '') {
            await journal('mod', 'hold', {}, `INBOX consumed at the close:\n${items}`)
            await notice(`‹pa› INBOX items before the phase closes; route each (plan_edit, or tell the developer):\n${items}`)
          }
          await save({ ...s, closeInbox: (await $.fs.read(`${root}/${INBOX_MD}`).catch(() => null)) ?? '' }, '')
          const nx = await py('tools/plan_edit.py', 'next', '--brief', '--json')
          const pick = nx.exitCode === 0 ? jsonOf(nx.stdout) : null
          if (pick === null) {
            await block(s, `plan_edit next: exit ${String(nx.exitCode)} ${clip(nx.stderr || nx.stdout, 200)}`, 'mod', 'hold', '')
          }
          return pick
        }

        // 3.23 T13d.c4: the ledger recalc (the PhaseEnd's `## Agent runs` source) in the background, so it runs while the
        // close goes on; one per phase. 3.24 T10b: launched detached (`--detach`, back at once) with `--rows-only` (the
        // PhaseEnd reads only the rows; the refit and the summary rebuild are the statusline child's), over every session
        // that owned the phase. A mod `$.process.run` child alive for minutes stalled the session's message queue, one
        // item per 60 s, and the closer's card never came (3.23 c6c; logs/T10.md Part b). A failure is only journaled.
        const startRecalc = async (phase: string): Promise<void> => {
          if (recalc !== null && recalc.phase === phase) {
            return
          }
          const status = jsonOf((await $.fs.read(`${root}/.run/status.json`).catch(() => null)) ?? '')
          const lp = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
          const sids = recalcSessions(lp.phaseSids, lp.ownerSid, strOf(status?.['router_session']), sidNow)
          if (sids.length === 0) {
            recalc = { phase, line: 'Ledger recalc skipped: no session to recalc.' }
            return
          }
          const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
          const dir = ((await $.env.get('PA_MOD_PA3_DIR')) ?? '') || `${home.replace(/\\/g, '/')}/.claude/pa3`
          const x = await run([python, `${dir}/pa_ledger.py`, 'recalc', '--detach', '--rows-only', '--from', 'turns',
            ...sids.flatMap(id => ['--session', id]), '--done-file', `${root}/${RECALC_DONE}`])
          recalc = { phase, line: x.exitCode === 0 ? ''
            : `Ledger recalc not started: exit ${String(x.exitCode)} ${clip((x.stderr || x.stdout).trim(), 200)}.` }
        }
        // The detached recalc's done-file as its journal line; null while it runs.
        const recalcDone = async (): Promise<string | null> => {
          const d = jsonOf((await $.fs.read(`${root}/${RECALC_DONE}`).catch(() => null)) ?? '')
          if (d === null) {
            return null
          }
          const sids = Array.isArray(d['sessions']) ? (d['sessions'] as unknown[]).map(strOf).join(', ') : ''
          return `Ledger recalc (sessions ${sids}): exit ${String(d['exit'])} after ${String(d['secs'])} s.`
        }
        // 3.25 T2: the close itself, no agent (the developer's yes at the close question is the milestone): RECAP.md from
        // Claude's phase report (`release(close)`'s note, kept as `closeRecap`; `-` when empty), with `## Generation Recap`
        // too at a generation end (genend_index.py reads it), the recalc started, then the chain from `recalc`.
        const startClose = async (): Promise<void> => {
          const pp = planPhase((await $.fs.read(`${root}/${PLAN}`).catch(() => null)) ?? '')
          const phase = pp.phase !== '' ? pp.phase : s.chainPhase ?? '?'
          const genEnd = genEndOf((await $.fs.read(`${root}/${GEN_PLAN}`).catch(() => null)) ?? '', pp.gen)
          const note = (s.closeRecap ?? '').trim() || '-'
          try {
            await $.fs.write(`${root}/${RECAP}`, `## Recap\n${note}\n` + (genEnd ? `\n## Generation Recap\n${note}\n` : ''))
          } catch (err) {
            await block({ ...s, closeRequested: false }, `the close: ${RECAP} not written: ${clip(String(err), 200)}`, 'mod', 'hold', '')
            return
          }
          await journal('mod', 'close', {}, `Phase ${phase} close started${genEnd ? ' (generation end)' : ''}: ${RECAP} written from Claude's report.`)
          await startRecalc(phase)
          const n = { ...s, state: 'closing', task: null, closeRequested: false, chainPhase: phase, chainGenEnd: genEnd, chainStep: 'recalc' } as LoopState
          delete n.closeRecap
          await save(n, 'close.start')
        }

        // The close chain (T6b), from loop.json `chainStep` (the next step; each saved once it ran, so a crash resumes
        // there). Nothing waits on the developer; a failed step is a block for Claude and keeps its chainStep.
        const runChain = async (): Promise<void> => {
          const phase = s.chainPhase ?? '?'
          const stop = async (reason: string, what: string): Promise<void> => {
            await block(s, `phase ${phase} close, ${s.chainStep}: ${reason}`, 'mod', 'close', what === '' ? reason : what, what)
          }
          const go = async (to: string, extra: Partial<LoopState> = {}): Promise<void> => {
            await save({ ...s, ...extra, chainStep: to } as LoopState, `chain:${to}`)
          }
          const okCommit = (why: string): boolean => why === '' || /nothing staged/.test(why)
          while (CHAIN_STEPS.includes(s.chainStep) && !s.holds.some(h => h !== 'talk')) {
            const step = s.chainStep
            // 3.24 T13A: the plan archived already (round 2: the closer ran the archive itself): the steps up to the archive
            // are done, and the chain goes on at `archived`, so it always reaches the closed line, the park and the toast.
            if (['assemble', 'commit', 'archive'].includes(step) && !(await $.fs.exists(`${root}/${PLAN}`)) &&
              (await $.fs.exists(`${root}/phase-ends/phase-${phase}/PHASE_PLAN.md`))) {
              await journal('mod', 'close', {}, `Phase ${phase} was archived already (phase-ends/phase-${phase}/): the chain goes on at the archived commit.`)
              await go('archived', { chainRuns: [], chainStage: [] })
              continue
            }
            if (step === 'recalc') {
              // 3.23 T13d.c4: the recalc started at the close's start (here when this process has none for the phase),
              // waited for at most RECALC_WAIT_MS more. The ledger is the PhaseEnd's Agent runs source, not a gate: the
              // chain goes on with whatever it holds, and the journal says whether the recalc had finished. 3.24 T10b: its
              // done-file is polled; nothing else waits on it.
              if (recalc === null || recalc.phase !== phase) {
                await startRecalc(phase)
              }
              const r = recalc
              let line = r === null ? 'Ledger recalc: none.' : r.line
              if (r !== null && r.line === '') {
                const until = (await $.clock.now()) + RECALC_WAIT_MS
                let done = await recalcDone()
                while (done === null && (await $.clock.now()) < until) {
                  await new Promise<void>(res => { $.clock.after(RECALC_POLL_MS, res) })
                  done = await recalcDone()
                }
                line = done ?? `Ledger recalc still running after the chain's ${String(RECALC_WAIT_MS / 1000)} s wait; the PhaseEnd uses the ledger as it is.`
              }
              await journal('mod', 'close', {}, line)
              await go('assemble')
            } else if (step === 'assemble') {
              // 3.25 T2: once; RECAP.md exists before the chain (startClose).
              const r = await py('tools/phaseend_index.py', 'assemble', phase)
              if (r.exitCode !== 0) {
                await stop(`assemble exit ${String(r.exitCode)}`, clip((r.stderr || r.stdout).trim(), 600))
                return
              }
              await go('commit')
            } else if (step === 'commit') {
              const why = await commit(`PhaseEnd ${phase}`, [`phase-ends/PhaseEnd_Phase${phase}.md`], 'phase-end')
              if (!okCommit(why)) {
                await stop(why, '')
                return
              }
              await go('archive')
            } else if (step === 'archive') {
              const r = await py('tools/phaseend_index.py', 'archive')
              if (r.exitCode !== 0) {
                await stop(`archive exit ${String(r.exitCode)}`, clip((r.stderr || r.stdout).trim(), 600))
                return
              }
              const a = archiveLines(r.stdout)
              await journal('mod', 'close', {}, `Phase ${phase} archived:\n${r.stdout.trim()}`)
              await go('run', { chainRuns: a.runs, chainStage: a.stage })
            } else if (step === 'run') {
              const line = (s.chainRuns ?? [])[0]
              if (line === undefined) {
                await go('archived')
                continue
              }
              const sp = splitCommand(line, python, CHAIN_SCRIPTS)
              if ('refused' in sp) {
                await stop(`RUN: ${line} refused: ${sp.refused}`, '')
                return
              }
              const r = await run(sp.argv)
              if (r.exitCode !== 0) {
                await stop(`RUN: ${line}: exit ${String(r.exitCode)} ${clip((r.stderr || r.stdout).trim(), 300)}`, '')
                return
              }
              await journal('mod', 'close', {}, `RUN: ${line}\n${clip(r.stdout.trim(), 400)}`)
              await go('run', { chainRuns: (s.chainRuns ?? []).slice(1) })
            } else if (step === 'archived') {
              const why = await commit(`Phase ${phase} archived`, ['phase-ends', GEN_PLAN, ...(s.chainStage ?? [])], 'phase-end')
              if (!okCommit(why)) {
                await stop(why, '')
                return
              }
              await go('clear')
            } else if (step === 'clear') {
              await py('tools/status.py', 'clear')
              // 3.29 T5: the phase's temp folder `.run/<phase>/` goes once the plan's phase-cleanup task is done; never a stop
              try {
                const sc = await py('tools/phaseend_index.py', 'scratch', phase)
                await journal('mod', 'close', {}, clip((sc.stdout || sc.stderr).trim() || `scratch exit ${String(sc.exitCode)}`, 300))
              } catch (err) {
                await log('close.scratch', { err: clip(String(err), 300) })
              }
              await go('recap')
            } else {
              const genEnd = s.chainGenEnd === true // T13b: the generation end is a stop (s is replaced by the save)
              const text = closedLine(phase, genEnd)
              await journal('mod', 'close', {}, text)
              // 3.24 T13f: the closed entry is written after the archived commit; commit it (commit_task.sh stages the
              // journal), or the close leaves it untracked (the specimen's 004806-534r-mod-close.md).
              const closedWhy = await commit(`Phase ${phase} closed`, [], 'phase-end')
              if (!okCommit(closedWhy)) {
                await log('close.commit', { why: clip(closedWhy, 300) })
              }
              // T13b.c3: the generation end parks in `held(generation)`: no gen.start until a release, or a session start's
              // `held(session)` released (3.23 T13d.c11). 3.24 T13A: `genParkSid`, the parking session: the park is never
              // released in it (the next generation starts in a new session or after /clear).
              const n = { ...(genEnd ? { ...pushHold(s, 'generation'), genParkSid: sidNow } : s), state: 'off', task: null, chainStep: '', closeRequested: false, closeAsked: false } as LoopState
              for (const k of ['closeInbox', 'chainPhase', 'chainRuns', 'chainStage', 'chainGenEnd', 'summaries', 'phaseSids'] as const) {
                delete n[k]
              }
              await save(n, 'closed')
              // 3.23 T13d.c4: one feed line, no recap turn (3.25 T2: the mod's own record); at a generation end the waiting
              // toast is raised here, since no Claude turn follows.
              await feedIn(at => modRecord({ text, at }))
              if (genEnd) {
                nseq += 1
                const r = requestFile(await $.clock.now(), nseq, 'toast', 'loop', { cause: 'input', body: text })
                await $.fs.write(`${inboxDir}/${r.name}`, r.body).catch(quiet)
              }
              genMode = { sid: '', at: 0, doc: null } // launch.py's mode moved on (planner-gen when the generation closed)
              offCheckedAt = 0
              return
            }
          }
        }

        // `plan_edit({command, why})`: during a review or question stop a decision, run without a shell and committed;
        // otherwise queued for the next boundary. The answer is an `applied:`, `queued:` or `refused:` line to the caller.
        const planEdit = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const command = typeof input['command'] === 'string' ? input['command'].trim() : ''
          const why = typeof input['why'] === 'string' ? input['why'] : ''
          const reply = async (line: string): Promise<void> => {
            await confirm(line, req.caller) // 3.23 T13d.c3: a confirmation that only says done is journaled, not sent
            await log('plan_edit', { id: req.id, line: clip(line, 200), sent: !quietReply(line) })
          }
          if (command === '') {
            await reply('refused: no command')
            return
          }
          if (!editIsDecision(s)) {
            await save({ ...s, queuedEdits: [...s.queuedEdits, { command, why, at: await $.clock.now(), caller: req.caller }] }, 'plan_edit.queued')
            await reply(`queued: ${command} (it lands at the next task boundary)`)
            return
          }
          await reply(await applyEdit(command, why, 'Decision'))
        }

        // `release({hold, note})` (T5b): loop.releaseHold, then the hold's own step; the answer goes back to the caller as
        // a `released:` or `refused:` notice line (a `talk` release with no `talk` hold is a silent no-op).
        const release = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const hold = typeof input['hold'] === 'string' ? input['hold'] : ''
          const note = typeof input['note'] === 'string' ? input['note'] : ''
          const reply = async (line: string): Promise<void> => {
            await confirm(line, req.caller) // 3.23 T13d.c3: a confirmation that only says done is journaled, not sent
            await log('release', { id: req.id, hold, line: clip(line, 200), sent: !quietReply(line) })
          }
          if (hold === 'update') { // 3.28.1 T14: the developer wants the found release installed after the current task
            const rel = releaseOf(s)
            if (rel === null || rel.step === 'done') {
              await reply('refused: there is no newer PA3 release')
              return
            }
            if (rel.step === 'install' || rel.step === 'running') { // 3.28.2 T17: already chosen: accepted, never refused
              await reply(rel.step === 'running' ? `released: update (PA3 ${rel.version} is installing now)`
                : `released: update (the install of PA3 ${rel.version} is already chosen; it runs at the clean stop, now if no expert or coder is working)`)
              return
            }
            await releaseAnswer(rel, true)
            await reply('released: update')
            return
          }
          if (!HOLD_NAMES.includes(hold)) {
            await reply(`refused: unknown hold "${hold}"`)
            return
          }
          if (hold === 'talk' && !s.holds.includes('talk')) {
            // T5b.c2: Claude releases `talk` unprompted; a refusal would be a spurious extra reply. Debug only.
            await log('release.noop', { id: req.id, hold })
            return
          }
          const t = s.task
          let taskOpen = false
          if (hold === 'review' && t !== null) {
            const sh = await py('tools/plan_edit.py', 'show', '--task', t.id, '--json')
            const st = sh.exitCode === 0 ? jsonOf(sh.stdout)?.['status'] : undefined
            taskOpen = st !== 'done' && st !== 'superseded'
          }
          const r = releaseHold(s, { hold: hold as HoldName, note }, taskOpen)
          if (!r.ok) {
            await reply(`refused: ${r.refused}`)
            return
          }
          if (hold === 'review') {
            // review-done archives REVIEW.md; a failed commit keeps the hold (a retry re-runs both, each idempotent).
            const id = t?.id ?? ''
            await py('tools/status.py', 'review-done')
            const why = await commit(`${id} review done`.trim(), ['phase-ends/current'], 'review')
            if (why !== '') {
              await reply(`refused: ${why}`)
              return
            }
            await journal('mod', 'hold', { task: id }, `${id} review done: REVIEW.md archived; the loop goes on.`)
            if (t !== null) {
              delete facts[t.agentId]
              // 3.24 T13A (decision 4): the accepted card, zero tokens: a `card` request for the router, drawn by the cards path.
              // 3.24 T17g: with the task's SUMMARY, from the contract the review was routed on.
              nseq += 1
              const cr = requestFile(await $.clock.now(), nseq, 'card', 'loop', { text: acceptedCard(id, [t.contract, t.pendingContract, t.lastRouted].find(x => typeof x === 'string' && x !== '') ?? '') })
              await $.fs.write(`${inboxDir}/${cr.name}`, cr.body).catch(quiet)
            }
            // 3.26 T2: the review release names the last done task too, as the done path does (T17p).
            // 3.29.9: and its trim mark, when this session marked it: the display is trimmed to it.
            const dm = t === null ? {} : doneMarkOf(r.state, t.id)
            await save({ ...r.state, state: 'boundary', task: null, ...(t === null ? {} : { lastDone: { id: t.id, at: await $.clock.now() } }), ...dm } as LoopState,
              'release:review')
            if (markOf((dm as { doneMark?: unknown }).doneMark) !== null) {
              trimSoon('done')
            }
            await reply('released: review')
            return
          }
          if (hold === 'stopped' && t !== null) {
            // T11-fix2: the developer's go after a stop. The same agent resumed under its id by message; not delivered, or
            // `fresh`, respawned: a task with PROGRESS (the T5d1 digest when absent) and the NOTE.
            const fresh = input['fresh'] === true
            const k = t.attempt + 1
            const meta: Meta = { task: t.id, agentId: t.agentId }
            if (!fresh && t.agentId !== '' && !(await expertCold(t))) {
              const sent = await $.session.send({ to: { agentId: t.agentId }, text: stoppedText(note) })
              await log('release.resume', { agentId: t.agentId, delivered: sent.isDelivered, reason: sent.reason ?? '' })
              if (sent.isDelivered) {
                await save({ ...r.state, task: { ...t, stopped: { at: await $.clock.now(), status: 'resumed' }, recovery: null, paused: null } }, 'release:stopped resumed')
                await journal('mod', 'hold', meta, `${t.id} stopped released: ${t.agentId} resumed under its id${note === '' ? '' : ` (${note})`}.`)
                await reply('released: stopped (resumed)')
                return
              }
            }
            delete facts[t.agentId]
            await save(r.state, '')
            const plan = planOf(t)
            if (plan === null) {
              await block(s, `release of ${t.id}: no plan entry to respawn from`, 'mod', 'hold', '')
              return
            }
            const progress = await progressDigest(t)
            await py('tools/status.py', 'set', '--task', t.id, '--agent', t.agent, '--kind', 'relaunch', '--attempt', String(k), '--session', sidNow)
            await journal('mod', 'hold', meta,
              `${t.id} stopped released: respawned with PROGRESS (TASK_PROGRESS.md ${progress})${fresh ? ', fresh' : ''} (attempt ${String(k)}).`)
            await spawnTask(plan, k, 'relaunch', { progress: PROGRESS, note })
            await reply(`released: stopped (restarted, attempt ${String(k)})`)
            return
          }
          if (hold === 'generation' || (hold === 'session' && s.holds.includes('generation'))) {
            // T13b.c3: the developer asked for the next generation here: its start begins for this session. 3.23 T13d.c11:
            // either release lifts both the generation park and the session hold. 3.24 T13A (decision 1): the next generation
            // starts only in a new session or after /clear: refused in the session that parked, and release(generation) is
            // refused unless a session start's `session` hold is up (live genend leg 2: Claude released `generation` there).
            if ((s as LoopState & { genParkSid?: unknown }).genParkSid === sidNow || !s.holds.includes('session')) {
              await reply('refused: the next generation starts in a new session or after /clear, not in the session that closed this one')
              return
            }
            await save({ ...popHold(popHold(r.state, 'generation'), 'session'), state: 'off', genStart: newGenStart('gen', sidNow) } as LoopState, 'gen.start')
            await journal('mod', 'hold', {}, `The ${hold} hold released: the next generation's start begins${note === '' ? '' : ` (${note})`}.`)
            await reply(`released: ${hold}`)
            return
          }
          if (hold === 'question' && (await $.fs.exists(`${root}/${REPLAN}`))) {
            // T6a1: the answer changed the milestone or the scope; the loop holds until a new phase plan is installed.
            await py('tools/status.py', 'replan-written')
            await save(pushHold(r.state, 'plan'), 'replan')
            await journal('mod', 'hold', { task: t?.id }, 'REPLAN.md written: the loop holds for a new phase plan (plan(kind: phase)).')
            await reply(`released: question
${REPLAN_NOTICE}`)
            return
          }
          if (hold === 'question' && s.state === 'closing') {
            // T6b: a close chain's block (assemble, a commit, a RUN: line) released: the chain retries at its chainStep.
            await save(r.state, 'release:question chain')
            await journal('mod', 'close', { task: t?.id }, `The close chain resumes at ${s.chainStep}${note === '' ? '' : ` (${note})`}.`)
            await reply('released: question')
            return
          }
          if (hold === 'question' && t !== null) {
            // The task continues with the note: its expert resumed unless cold (3.28.1 T12), else respawned with PROGRESS and NOTE.
            const k = t.attempt + 1
            await py('tools/status.py', 'set', '--task', t.id, '--agent', t.agent, '--kind', 'relaunch', '--attempt', String(k), '--session', sidNow)
            const at = await $.clock.now()
            let how = 'respawned'
            // 3.26 T2: a keep-warm ping refreshes the cache too, so the clock counts the last one delivered (coldOf).
            if (t.agentId !== '' && !(await coldOf(t.agentId, t.agent, Math.max(t.lastCompleteAt, 1), t.spawnedAt))) {
              const sent = await $.session.send({ to: { agentId: t.agentId }, text: `NOTE: ${note}` })
              await log('release.resume', { agentId: t.agentId, delivered: sent.isDelivered })
              if (sent.isDelivered) {
                how = 'resumed'
              }
            }
            delete facts[t.agentId]
            if (how === 'resumed') {
              // lastCompleteAt 0: the run counts as ended only after its next turn.complete.
              await save({ ...r.state, state: 'running', task: { ...t, attempt: k, kind: 'relaunch', contract: null, pendingContract: null, lastCompleteAt: 0, routed: '' } as LoopTask },
                'release:question resumed')
              await journal('mod', 'hold', { task: t.id, agentId: t.agentId },
                `${t.id} question released: ${t.agentId} resumed with the note (attempt ${String(k)}).`)
            } else {
              const plan = planOf(t)
              await save(r.state, '')
              if (plan === null) {
                await block(s, `release of ${t.id}: no plan entry to respawn from`, 'mod', 'hold', '')
                return
              }
              const progress = await progressDigest(t)
              await spawnTask(plan, k, 'relaunch', { progress: PROGRESS, note })
              await journal('mod', 'hold', { task: t.id },
                `${t.id} question released: respawned with PROGRESS (TASK_PROGRESS.md ${progress}) and the note (attempt ${String(k)}).`)
            }
            await reply('released: question')
            return
          }
          // `close`: the flag T6b's close reads, and its note (3.25 T2: Claude's phase report, the recap); any other hold simply pops.
          await save(hold === 'close' ? ({ ...r.state, closeRequested: true, closeRecap: note } as LoopState) : r.state, `release:${hold}`)
          await reply(`released: ${hold}`)
        }

        // 3.28.1 T12 (developer 2026-10-15): nothing is sent to a cold agent (policy.isCold: its turn ended, `lastAt`, and
        // its last request, that end or a warming ping, older than its cache's life, or than the ping time with its idle
        // stretch's pings spent). A fresh agent of its role, seeded from the journal, takes the message and its place.
        const coldOf = async (id: string, role: string, lastAt: number, spawnedAt: number): Promise<boolean> => {
          if (id === '' || lastAt <= 0) {
            return false
          }
          const w = (await read($, stateAtom) as { warmAgents?: Record<string, { lastPingAt: number; waitPings: number; waitCount: number }> } | null)
            ?.warmAgents?.[id]
          const ttl = cacheTtlOf(role === '' ? null : await $.fs.read(`${root}/.claude/agents/${role}.md`).catch(() => null))
          const pingsLeft = w === undefined || w.waitCount === 0 || w.waitPings < w.waitCount
          return isCold(Math.max(lastAt, spawnedAt, w?.lastPingAt ?? 0), await $.clock.now(), ttl, pingsLeft)
        }
        const expertCold = async (t: LoopTask): Promise<boolean> => coldOf(t.agentId, t.agent, t.lastCompleteAt, t.spawnedAt)
        // A cold expert's message goes to a fresh expert: the brief, PROGRESS (the digest when absent) and the message.
        const relaunchCold = async (t: LoopTask, note: string, why: string): Promise<boolean> => {
          const plan = planOf(t)
          if (plan === null) {
            return false
          }
          const k = t.attempt + 1
          const progress = await progressDigest(t)
          await py('tools/status.py', 'set', '--task', t.id, '--agent', t.agent, '--kind', 'relaunch', '--attempt', String(k), '--session', sidNow)
          await journal('mod', 'hold', { task: t.id, agentId: t.agentId }, `${t.id}: ${t.agentId} is cold; nothing sent to it (${why}). ` +
            `A fresh expert takes it with PROGRESS (TASK_PROGRESS.md ${progress}) (attempt ${String(k)}).`)
          delete facts[t.agentId]
          await spawnTask(plan, k, 'relaunch', { progress: PROGRESS, note })
          return true
        }
        // TASK_PROGRESS.md for a respawn after a restart (T5d1): the hook's kill digest when present (`claude --continue`),
        // else the mod's: git status, the commits since the task's start, its agents' journal notes and reasoning.
        const progressDigest = async (t: LoopTask): Promise<string> => {
          if (await $.fs.exists(`${root}/${PROGRESS}`)) {
            return 'present, kept'
          }
          const status = await run(['git', 'status', '--short'])
          const gitLog = t.startSha === '' ? null : await run(['git', 'log', '--oneline', `${t.startSha}..HEAD`])
          const [day, time] = stamp(t.spawnedAt)
          const jdir = `${root}/${JOURNAL_DIR}`
          const entries: [string, string][] = []
          if (await $.fs.exists(jdir)) {
            for (const d of (await $.fs.list(jdir)).filter(f => f.kind === 'dir' && f.name >= day)) {
              for (const f of (await $.fs.list(`${jdir}/${d.name}`)).filter(x => x.kind === 'file' && isProgressEntry(x.name))) {
                const name = `${d.name}/${f.name}`
                if (name >= `${day}/${time}`) {
                  entries.push([name, await $.fs.read(`${jdir}/${name}`).catch(() => '')])
                }
              }
            }
          }
          // 3.28.1 T12: what it waits on: its children still at work.
          const waiting = [
            ...(await $.agent.list()).filter(a => a.parentId === t.agentId && BUSY.has(a.status)).map(a => `${a.type || 'agent'} ${a.id}: ${a.status}`),
          ]
          await $.fs.write(`${root}/${PROGRESS}`, recoveryDigest({
            id: t.id, attempt: t.attempt, startSha: t.startSha, since: t.spawnedAt, status: status.stdout,
            log: gitLog === null ? '' : gitLog.exitCode === 0 ? gitLog.stdout : `(git log exit ${String(gitLog.exitCode)})`, entries, waiting,
          }))
          return `written by the mod (${String(entries.length)} journal entries)`
        }

        // A stopped loop agent (T11-fix2): killed (Ctrl+C), failed, or gone from `$.agent.list()` (as killed), with no
        // contract, after this process saw it at work (3.28.1 T9c; else a session end, recovered). The stop is the
        // developer's: the `stopped` hold and one notice to Claude (no toast); nothing is resumed or respawned until
        // release(stopped), and nothing new starts while it is held.
        const stopHold = async (t: LoopTask, status: string): Promise<void> => {
          const who = `${t.id}'s expert`
          const how = status === 'failed'
            ? `${who} failed: reason unknown (an error, not the developer). The task is paused; you may resume it yourself.`
            : `${who} was stopped (Ctrl+C${status === 'absent' ? `; ${t.agentId} is gone from the agent list, so I treat it as killed` : ''}); ` +
              'its coder too if it had one. The task is paused until the developer wants it to go on. ' +
              'A go they already gave counts: if their latest message asked for this task or the work to go on, ' +
              'resume it now (release stopped); otherwise ask them.'
          await save(pushHold({ ...s, task: { ...t, stopped: { at: now, status } } }, 'stopped'), 'stopped')
          await journal('mod', 'hold', { task: t.id, agentId: t.agentId }, `${t.agentId} ${status} with no contract: ${t.id} held (stopped).`)
          await notice(`‹pa› ${how}`)
        }

        // Recovery after a takeover (T5d1), a step a tick, each journaled (`mod/recover`): launch.py's view; a stale
        // `run_in_flight` or no agentId respawns at once; else the expert is sent `continue:` and watched in
        // `$.agent.list()`; not delivered, or not seen running within RECOVER_WAIT_MS, it is respawned (status relaunch,
        // the brief plus PROGRESS). Nothing is stopped. 3.28.1 T9c: Claude tells the developer in one line.
        const cutOff = (t: LoopTask): string => `‹pa› ${t.id}'s expert was cut off when the session ended (a restart, an update ` +
          "or a closed terminal), not by the developer's stop; "
        const recover = async (t: LoopTask, r: { phase: 'start' | 'sent'; at: number }): Promise<void> => {
          const meta: Meta = { task: t.id, agentId: t.agentId }
          const respawn = async (why: string): Promise<void> => {
            const plan = planOf(t)
            if (plan === null) {
              await block(s, `recovery of ${t.id}: no plan entry to respawn from`, 'mod', 'hold', '')
              return
            }
            const k = t.attempt + 1
            const progress = await progressDigest(t)
            await py('tools/status.py', 'set', '--task', t.id, '--agent', t.agent, '--kind', 'relaunch', '--attempt', String(k), '--session', sidNow)
            await journal('mod', 'recover', meta,
              `recovery: ${t.id} respawned with PROGRESS: ${PROGRESS} (attempt ${String(k)}): ${why}. TASK_PROGRESS.md ${progress}.`)
            delete facts[t.agentId]
            await spawnTask(plan, k, 'relaunch', { progress: PROGRESS })
            await notice(`${cutOff(t)}I respawned it with its progress (attempt ${String(k)}). Tell the developer in one line.`)
          }
          if (r.phase === 'start') {
            const st = await py('tools/launch.py', '--state-json')
            const doc = st.exitCode === 0 ? jsonOf(st.stdout) : null
            const flight = doc?.['run_in_flight'] ?? null
            const fl = typeof flight === 'object' && flight !== null ? (flight as Record<string, unknown>) : null
            const stale = fl !== null && fl['task'] === t.id && fl['stale'] === true
            await journal('mod', 'recover', meta,
              `recovery: ${t.id} in flight (${t.agentId || 'no agentId'}, attempt ${String(t.attempt)}); launch.py run_in_flight ` +
              `${JSON.stringify(flight)}, running ${JSON.stringify(doc?.['running'] ?? null)}.`)
            if (t.agentId === '') {
              await respawn('no agentId recorded')
              return
            }
            if (stale) {
              await respawn('its run_in_flight is stale')
              return
            }
            if (await expertCold(t)) {
              await respawn('its cache is cold')
              return
            }
            const sent = await $.session.send({ to: { agentId: t.agentId }, text: CONTINUE_TEXT })
            await log('recover.send', { agentId: t.agentId, delivered: sent.isDelivered, reason: sent.reason ?? '' })
            if (!sent.isDelivered) {
              await journal('mod', 'recover', meta, `recovery: continue: to ${t.agentId} not delivered (${clip(sent.reason ?? '', 120)}).`)
              await respawn('continue: not delivered')
              return
            }
            await save({ ...s, task: { ...t, recovery: { phase: 'sent', at: await $.clock.now() } } }, 'recover.sent')
            await journal('mod', 'recover', meta, `recovery: sent ${t.agentId} continue: (delivered); watching for it running.`)
            await notice(`${cutOff(t)}I resumed it under its id (${t.agentId}). Tell the developer in one line.`)
            return
          }
          if ((await $.agent.list()).some(a => a.id === t.agentId && AT_WORK.has(a.status))) {
            // lastCompleteAt 0: the run counts as ended only after its next turn.complete.
            await save({ ...s, task: { ...t, recovery: null, lastCompleteAt: 0 } }, 'recover.running')
            await journal('mod', 'recover', meta, `recovery: ${t.agentId} is running again; ${t.id} goes on.`)
            return
          }
          if (now - r.at >= RECOVER_WAIT_MS) {
            await respawn(`${t.agentId} not seen running ${String(RECOVER_WAIT_MS / 1000)} s after continue:`)
          }
        }

        // 3.29.8: `cleanup({summary})`: the one-time `.run/` cleanup's deletion. The list Claude wrote is checked
        // (tools/run_cleanup.py --check: inside .run/, no PA3 entry, not the open phase); good, the mod's own picker asks
        // the developer, and its yes (answered, below) is the approval: the mod deletes exactly those items.
        const cleanupReq = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const summary = strOf(input['summary']).trim()
          const chk = await py('tools/run_cleanup.py', '--check', CLEANUP_LIST)
          const out = (chk.stdout || chk.stderr).trim()
          if (chk.exitCode !== 0) {
            await confirm(`refused: the cleanup list did not pass its check, so nothing was asked or deleted:\n${clip(out, 1200)}`, req.caller)
            return
          }
          nseq += 1
          const at = await $.clock.now()
          const q = requestFile(at, nseq, 'ask', 'loop', { question: cleanupQuestion(summary, out.split(/\r?\n/).pop() ?? ''), options: [...CLEANUP_OPTIONS] })
          await $.fs.write(`${inboxDir}/${q.name}`, q.body)
          await $.fs.write(`${root}/${CLEANUP_ASK}`, `${JSON.stringify({ asked: q.name.slice(0, -'.json'.length), caller: req.caller, at })}\n`).catch(quiet)
          await journal('mod', 'decision', {}, `The .run/ cleanup list passed its check (${out}); the developer is asked.`)
        }

        // `steer({task, text})` (T5c): a correction sent to the running task's expert, never a kill; `steerAt` keeps the run
        // from counting as ended until the expert's next turn.complete. The answer is `steered: T<n>` or `refused: …`.
        const steer = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const id = typeof input['task'] === 'string' ? input['task'].trim() : ''
          const text = typeof input['text'] === 'string' ? input['text'].trim() : ''
          // 3.29.1 T9: `pause: true` records the developer's pause (kept across a restart), `pause: false` clears it
          const pause = typeof input['pause'] === 'boolean' ? input['pause'] : null
          const pausePatch = (at: number): Partial<LoopTask> => (pause === true ? { paused: { at } } : pause === false ? { paused: null } : {})
          const reply = async (line: string): Promise<void> => {
            await confirm(line, req.caller) // 3.23 T13d.c3: a confirmation that only says done is journaled, not sent
            await log('steer', { id: req.id, task: id, line: clip(line, 200), sent: !quietReply(line) })
          }
          const t = s.task
          if (t === null || t.id !== id || s.state !== 'running' || t.agentId === '') {
            await reply(`refused: ${id === '' ? 'the task' : id} is not running`)
            return
          }
          if (text === '') {
            await reply('refused: no text')
            return
          }
          const at = await $.clock.now()
          if (await expertCold(t)) {
            if (await relaunchCold(t, text, 'a steer')) {
              if (pause !== null && s.task !== null) {
                await save({ ...s, task: { ...s.task, ...pausePatch(at) } }, pause ? 'paused' : 'unpaused')
              }
              await journal('mod', 'steer', { task: id }, `${id} steered (a fresh expert): ${text}`)
              await reply(`steered: ${id} (its expert was cold: a fresh one has the correction)`)
              return
            }
          }
          const sent = await $.session.send({ to: { agentId: t.agentId }, text: steerText(text) })
          await log('steer.send', { agentId: t.agentId, delivered: sent.isDelivered, reason: sent.reason ?? '' })
          if (!sent.isDelivered) {
            await reply(`refused: ${id} did not take the message (${clip(sent.reason ?? 'not delivered', 120)})`)
            return
          }
          await save({ ...s, task: { ...t, steerAt: at, ...pausePatch(at) } }, pause === true ? 'steer.paused' : 'steer')
          await journal('mod', 'steer', { task: id, agentId: t.agentId }, `${id} steered: ${text}`)
          await reply(`steered: ${id}`)
        }

        // `forward` (T5d2a, the router's): a forward of a child's hand-back to the running expert is queued to it from
        // the request's time until the expert's next turn.complete (loop.queuedTo).
        const forwarded = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const t = s.task
          // 3.28.1 T12: with `text`, the router found the expert cold and sent nothing: a fresh expert takes the text.
          const text = typeof input['text'] === 'string' ? input['text'] : ''
          if (text !== '' && t !== null && s.state === 'running' && t.agentId !== '' && input['agentId'] === t.agentId &&
            (await expertCold(t)) && (await relaunchCold(t, text, 'a message for it'))) {
            return
          }
          if (text !== '') { // not sent to the cold agent; with no plan to relaunch from, the journal keeps it
            await journal('mod', 'hold', { agentId: String(input['agentId'] ?? '') }, `Not delivered (its agent is cold, no fresh one started): ${text}`)
          }
          if (t === null || s.state !== 'running' || t.agentId === '' || input['agentId'] !== t.agentId || (t.forwardAt ?? 0) >= req.at) {
            await log('forward.skip', { id: req.id, agentId: input['agentId'] })
            return
          }
          await save({ ...s, task: { ...t, forwardAt: req.at } }, 'forward')
        }

        // T6a1: an approved plan to install (the router's `install`): recorded in loop.json, run a step a tick.
        const installStart = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const kind = input['kind'] === 'generation' || input['kind'] === 'phase' ? input['kind'] : null
          const path = typeof input['path'] === 'string' ? input['path'].trim() : ''
          const list = (k: string): string[] => (Array.isArray(input[k]) ? (input[k] as unknown[]).filter((x): x is string => typeof x === 'string') : [])
          if (kind === null || path === '' || installOf(s) !== null) {
            await notice(`${APPROVED}\nnot installed: ${installOf(s) !== null ? 'another plan is being installed' : 'no kind or path'}`, '', req.caller)
            return
          }
          if (firstPending) { // 3.24 T7b-2c: plans wait for the project's first run
            await journal('mod', 'hold', {}, `Plan not installed (${path}): the project's first run in PA 3.2 is pending.`)
            await notice(`${APPROVED}\nnot installed: ${FIRST_RUN_WAITS}`, '', req.caller)
            return
          }
          const inst: Install = {
            kind, path, title: typeof input['title'] === 'string' ? input['title'] : '', rules: list('rules'), candidates: list('candidates'),
            caller: req.caller, step: 'start', i: 0, asked: '', answer: null, applied: [], phase: '',
          }
          await save({ ...s, install: inst } as LoopState, `install:${kind}`)
        }
        // 3.28.1 T14: the release picker's answer, or Claude's release({hold: "update"}): install after the current task
        // (the statusline's line says so), else not now (the update installs at the next start).
        const releaseAnswer = async (rel: NonNullable<ReturnType<typeof releaseOf>>, install: boolean): Promise<void> => {
          if (rel.step !== 'asked' && rel.step !== 'later') {
            return
          }
          const step = install ? 'install' : 'later'
          await journal('mod', 'decision', {}, `PA3 ${rel.version}: ${install ? 'installs after the current task' : 'not now; it installs at the next start'} (the developer's answer).`)
          await save({ ...s, release: { ...rel, step } } as LoopState, `release.${step}`)
          await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify({ version: rel.version, kind: rel.kind, state: step })}\n`).catch(quiet)
        }
        // The answer to the loop's own ask (the router's `answer`, `{id, line}`), kept for the asking record's step: the
        // install or the generation start (T6a2).
        const answered = async (req: Request): Promise<void> => {
          const input = typeof req.input === 'object' && req.input !== null ? (req.input as Record<string, unknown>) : {}
          const answer = strOf(input['line']).replace(/^Answer:\s*/, '').trim()
          const inst = installOf(s)
          const g = genStartOf(s)
          const d = driftOf(s)
          const u = updateOf(s)
          const rel = releaseOf(s)
          if (inst !== null && inst.asked !== '' && input['id'] === inst.asked) {
            await save({ ...s, install: { ...inst, answer } } as LoopState, 'install.answer')
          } else if (g !== null && g.asked !== '' && input['id'] === g.asked) {
            await save({ ...s, genStart: { ...g, answer } } as LoopState, 'gen.answer')
          } else if (d !== null && d.asked !== '' && input['id'] === d.asked) {
            await save({ ...s, drift: { ...d, answer } } as LoopState, 'drift.answer')
          } else if (u !== null && u.asked !== '' && input['id'] === u.asked) {
            await save({ ...s, update: { ...u, answer } } as LoopState, 'update.answer')
          } else if (rel !== null && rel.asked !== '' && input['id'] === rel.asked) {
            await releaseAnswer(rel, /^install\b/i.test(answer)) // 3.28.1 T14
          } else if (typeof input['id'] === 'string' && input['id'] !== '' &&
            objectOf((await $.fs.read(`${root}/${RUN_REVIEW_MARKER}`).catch(() => null)) ?? '')?.['asked'] === input['id']) {
            const go = /^clean\b/i.test(answer) // 3.29.3: the one-time `.run/` cleanup picker
            await journal('mod', 'decision', {}, go ? 'The developer chose the one-time .run/ cleanup.' : 'The developer put off the one-time .run/ cleanup.')
            await notice(runReviewNotice(go))
          } else if (typeof input['id'] === 'string' && input['id'] !== '' &&
            objectOf((await $.fs.read(`${root}/${CLEANUP_ASK}`).catch(() => null)) ?? '')?.['asked'] === input['id']) {
            // 3.29.8: the deletion picker: yes deletes exactly the checked list (the tool checks it again first)
            await $.fs.write(`${root}/${CLEANUP_ASK}`, '{}\n').catch(quiet)
            if (/^delete\b/i.test(answer)) {
              const del = await py('tools/run_cleanup.py', CLEANUP_LIST)
              const out = clip((del.stdout || del.stderr).trim(), 1500)
              await journal('mod', 'decision', {}, `The developer said yes to the .run/ cleanup; the mod ran it (exit ${String(del.exitCode)}):\n${out}`)
              await notice(`‹pa› The developer said yes in the cleanup picker, and the mod ran the deletion (exit ${String(del.exitCode)}):\n${out}\n` +
                'Tell them the result in plain words now. If this was an update\'s after-restart work, then ask "Ready to continue work?".')
            } else {
              await journal('mod', 'decision', {}, 'The developer said not now to the .run/ cleanup deletion.')
              await notice('‹pa› The developer said not now in the cleanup picker: nothing was deleted. Tell them in one line; the list stays in .run/mod/cleanup.json if they want it later.')
            }
          } else {
            await log('answer.skip', { id: req.id, for: input['id'] })
          }
        }
        // One install step after another until it waits (an ask owed) or ends; each step saved before the next, so a
        // crash resumes at the recorded one (from-draft over an unapproved plan, an "already approved" approve: idempotent).
        const runInstall = async (first: Install): Promise<void> => {
          let inst = first
          const put = async (n: Install, what: string): Promise<void> => {
            inst = n
            await save({ ...s, install: n } as LoopState, what)
          }
          const finish = async (line: string, installed: boolean): Promise<void> => {
            await journal('mod', 'plan', {}, line)
            const n = { ...s, install: null } as LoopState
            // T6a2: a generation install pops `plan` too (the generation start held it); launch.py's mode is asked again.
            await save(!installed ? n : inst.kind === 'phase' ? { ...popHold(n, 'plan'), state: 'off', task: null } : popHold(n, 'plan'),
              installed ? 'install.done' : 'install.refused')
            offCheckedAt = 0
            genMode = { sid: '', at: 0, doc: null }
            await notice(line, '', inst.caller)
          }
          if (inst.kind === 'generation') {
            const text = await $.fs.read(planFilePath(root, inst.path)).catch(() => null)
            if (text === null) {
              await finish(changesAsked(`the draft ${inst.path} could not be read`), false)
              return
            }
            await $.fs.write(`${root}/${GEN_PLAN}`, text)
            const g = /Generation\s+(\d+)/.exec(text)?.[1] ?? '?'
            const why = await commit(`Generation ${g} plan approved`, [GEN_PLAN], 'plan')
            await finish(`${APPROVED}\ninstalled: ${GEN_PLAN} (Generation ${g})${why === '' ? '' : `; not committed: ${why}`}`, true)
            return
          }
          if (inst.step === 'start') {
            const lint = await py('tools/plan_edit.py', 'lint', inst.path)
            if (lint.exitCode !== 0) {
              const errs = lintErrors(`${lint.stdout}\n${lint.stderr}`)
              await finish(changesAsked(`lint: ${errs || clip((lint.stderr || lint.stdout).trim(), 300)}`), false)
              return
            }
            await put({ ...inst, step: 'from-draft' }, 'install.lint')
          }
          if (inst.step === 'from-draft') {
            const fd = await py('tools/plan_edit.py', 'from-draft', inst.path)
            if (fd.exitCode !== 0) {
              const errs = lintErrors(`${fd.stdout}\n${fd.stderr}`)
              await finish(errs !== ''
                ? changesAsked(`lint: ${errs}`)
                : `${APPROVED}\nnot installed: from-draft: ${clip((fd.stderr || fd.stdout).trim(), 300)}`, false)
              return
            }
            await put({ ...inst, step: 'approve' }, 'install.from-draft')
          }
          if (inst.step === 'approve') {
            // 3.24 T17b: the planner's facts (the pa-planner Claude spawned), else the caller's.
            const planner = plannerIdOf(await $.agent.list().catch(() => []), inst.caller)
            const facts = jsonOf((await $.fs.read(`${root}/.run/mod/${sidNow}/agents/${planner}.json`).catch(() => null)) ?? '')
            const fact = (k: string): string => (typeof facts?.[k] === 'string' && facts[k] !== '' ? (facts[k] as string) : 'unknown')
            const ap = await py('tools/plan_edit.py', 'approve', '--planner', `${fact('model')}/${fact('effort')}`)
            if (ap.exitCode !== 0 && !/already approved/.test(`${ap.stdout}${ap.stderr}`)) {
              await finish(changesAsked(`approve: ${clip((ap.stderr || ap.stdout).trim(), 300)}`), false)
              return
            }
            const head = (await $.fs.read(`${root}/${PLAN}`).catch(() => '')).split(/\r?\n/, 1)[0] ?? ''
            await put({ ...inst, step: 'asks', i: 0, phase: /^#\s*Phase\s+(\d+(?:\.\d+)*)/.exec(head)?.[1] ?? '?' }, 'install.approve')
          }
          if (inst.step === 'asks') {
            const items = [...inst.rules, ...inst.candidates]
            while (inst.i < items.length) {
              const rule = inst.i < inst.rules.length
              const item = items[inst.i] ?? ''
              if (inst.asked === '') {
                nseq += 1
                const r = requestFile(await $.clock.now(), nseq, 'ask', 'loop', rule
                  ? { question: `Add this rule? ${item}`, options: [...RULE_OPTIONS] }
                  : { question: `Tool candidate: ${item.replace(/^\s*-?\s*Tool\s+candidate:\s*/i, '')}`, options: [...CANDIDATE_OPTIONS] })
                await $.fs.write(`${inboxDir}/${r.name}`, r.body)
                await put({ ...inst, asked: r.name.slice(0, -'.json'.length), answer: null }, 'install.ask')
                return
              }
              if (inst.answer === null) {
                return
              }
              const a = inst.answer
              let done: string
              if (rule) {
                if (/^add\b/i.test(a)) {
                  const names = (await $.fs.exists(`${root}/rules`)) ? (await $.fs.list(`${root}/rules`)).map(f => f.name) : []
                  const args = ruleArgs(item, nextRuleId(names), `phase ${inst.phase} plan`)
                  const rr = await py('tools/rules_add.py', ...args)
                  done = rr.exitCode === 0 ? `rule ${args[2] ?? ''} added` : `rule ${args[2] ?? ''} not added: ${clip((rr.stderr || rr.stdout).trim(), 120)}`
                } else {
                  done = `rule rejected: ${clip(item, 80)}`
                }
              } else {
                const c = candidateOf(item)
                if (/^build/i.test(a)) {
                  const at = await py('tools/plan_edit.py', 'add-task', '--title', `tool candidate ${c.name}: ${c.does}`, '--done-when', c.replaces,
                    '--coder', 'opus55', '--by', 'developer')
                  done = at.exitCode === 0 ? `candidate ${c.name} built as a task` : `candidate ${c.name} not added: ${clip((at.stderr || at.stdout).trim(), 120)}`
                } else if (/^drop/i.test(a)) {
                  await py('tools/plan_edit.py', 'append-change', `candidate ${c.name} dropped: by the developer`, '--by', 'developer')
                  done = `candidate ${c.name} dropped`
                } else {
                  done = `candidate ${c.name} deferred`
                }
              }
              await put({ ...inst, i: inst.i + 1, asked: '', answer: null, applied: [...inst.applied, done] }, 'install.applied')
            }
            await put({ ...inst, step: 'commit' }, 'install.asked')
          }
          await run([python, '-c', RM_PY, `${root}/${REPLAN}`])
          const why = await commit(`Phase ${inst.phase} plan approved`, ['phase-ends/current', 'rules'], 'plan')
          const extra = inst.applied.length === 0 ? '' : `; ${inst.applied.join('; ')}`
          await finish(`${APPROVED}\ninstalled: phase ${inst.phase} plan (PHASE_PLAN.md approved)${extra}${why === '' ? '' : `; not committed: ${why}`}`, true)
        }

        // T6a2: the generation start (or the migration curate line), a step a tick, each saved before the next; the state
        // stays `off` so the router presents the curator ask. The curator and the auditor are loop agents: their last
        // answer is read at runEnded (facts, else the transcript).
        const runGen = async (first: GenStart): Promise<void> => {
          let g = first
          const put = async (n: GenStart, what: string, extra: Partial<LoopState> = {}): Promise<void> => {
            g = n
            await save({ ...s, ...extra, genStart: n } as LoopState, what)
          }
          const spawnSide = async (type: string, prompt: string, description: string): Promise<string> => {
            const before = new Set((await $.agent.list()).map(a => a.id))
            const r = await $.agent.spawn({ subagentType: type, prompt, description })
            const id = r.deny !== undefined ? '' : r.agentId ?? newAgent((await $.agent.list()).filter(a => !before.has(a.id)), type)
            const who = g.kind === 'migration' ? "The project's first run in PA 3.2" : 'Generation start' // 3.24 T7b-2c
            await journal('mod', 'hold', {}, id === ''
              ? `${who}: the ${type} spawn was refused (${clip(r.deny ?? 'no agentId', 200)}).`
              : `${who}: ${type} ${id} spawned.`)
            if (id !== '') {
              sideAgent = id
              await put({ ...g, agentId: id, spawnedAt: now, lastCompleteAt: 0, childBusy: false, childIdleAt: 0 }, `gen.spawn:${type}`)
            }
            return id
          }
          // The running side agent's last answer once its run has ended (loop.runEnded on a pseudo task); null meanwhile.
          const sideAnswer = async (): Promise<string | null> => {
            const f = facts[g.agentId]
            if (f !== undefined && f.lastCompleteAt > g.lastCompleteAt) {
              await put({ ...g, lastCompleteAt: f.lastCompleteAt }, '')
            }
            const list = await $.agent.list()
            const me = list.find(a => a.id === g.agentId)
            // 3.28.8: missing from the list with a child at work (an auditor's coder) is waiting, not stopped.
            const busy = list.filter(a => a.parentId === g.agentId && BUSY.has(a.status)).length
            const live = me === undefined && busy > 0 ? 'idle' : liveness(me?.status, now - g.spawnedAt)
            // T11-fix2: a stopped curator or auditor with no answer is the developer's stop: its step ends without one.
            if (live === 'stopped' && (f?.answer ?? '') === '') {
              const role = g.step === 'curator' ? 'memory curator' : 'auditor'
              await journal('mod', 'hold', { agentId: g.agentId }, `${g.kind === 'migration' ? "The project's first run in PA 3.2" : 'Generation start'}: the ${role} ${g.agentId} was ${me?.status ?? 'absent'}; not restarted.`)
              delete facts[g.agentId]
              return `(the ${role} was stopped: ${me?.status ?? 'absent'}; not restarted)`
            }
            if ((busy > 0) !== g.childBusy) {
              await put({ ...g, childBusy: busy > 0, ...(busy > 0 ? {} : { childIdleAt: now }) }, busy > 0 ? 'gen.child.busy' : 'gen.child.idle')
            }
            const pseudo: LoopTask = {
              id: 'gen', title: '', agent: '', coder: '', effort: '', attempt: 1, kind: 'gen', agentId: g.agentId, startSha: '',
              spawnedAt: g.spawnedAt, lastCompleteAt: g.lastCompleteAt, contract: null, childBusy: g.childBusy, childIdleAt: g.childIdleAt,
              // T11-fix: the auditor's contract is an answer with a STATUS line; the curator's recap (no STATUS) is any answer.
              pendingContract: (f?.answer ?? '') !== '' && (g.step === 'curator' || /^\s*STATUS:/m.test(f?.answer ?? '')) ? f?.answer ?? null : null,
            }
            if (live === 'busy' || (live === 'idle' && !runEnded(pseudo, busy, 0, now, (pseudo.pendingContract ?? null) === null ? await activityAt(g.agentId) : 0,
              f?.handbackAt ?? 0))) {
              return null
            }
            let answer = f?.answer ?? ''
            if (answer === '') {
              const tp = transcriptSeen.sid === sidNow ? transcriptSeen.path : ''
              const text = tp === '' ? null : await $.fs.read(subagentTranscript(tp, sidNow, g.agentId)).catch(() => null)
              answer = (text === null ? null : lastAssistantText(text)) ?? ''
            }
            delete facts[g.agentId]
            return answer
          }
          const cleared = { agentId: '', spawnedAt: 0, lastCompleteAt: 0, childBusy: false, childIdleAt: 0 }
          if (g.step === 'inbox') {
            let doc: Record<string, unknown> | null = null
            if (g.kind === 'gen') {
              await py('tools/status.py', 'inbox-consume')
              const st = await py('tools/launch.py', '--state-json')
              doc = st.exitCode === 0 ? jsonOf(st.stdout) : null
            }
            const curate = g.kind === 'gen' ? strOf(doc?.['curate']) : g.curate
            await put({
              ...g, step: curate === '' ? 'audit' : 'ask', curate, auditFlag: strOf(doc?.['audit_flag']), deferred: deferredOf(doc?.['deferred']),
            }, 'gen.inbox', { state: 'off' })
          }
          if (g.step === 'ask') {
            if (g.asked === '') {
              nseq += 1
              const r = requestFile(now, nseq, 'ask', 'loop', { question: curateQuestion(g.curate), options: [...CURATE_OPTIONS] })
              await $.fs.write(`${inboxDir}/${r.name}`, r.body)
              await put({ ...g, asked: r.name.slice(0, -'.json'.length), answer: null }, 'gen.ask', { state: 'off' })
              return
            }
            if (g.answer === null) {
              return
            }
            if (!/^run\b/i.test(g.answer)) {
              await journal('mod', 'hold', {}, `The memory curator was paused (${g.curate}); it is asked again next session.`)
              offCheckedAt = 0
              if (g.kind === 'gen') {
                await put({ ...g, step: 'done' }, 'gen.paused', pushHold(s, 'plan'))
                await notice('‹pa› The developer paused the memory curator; it is asked again next session.\n' +
                  'a generation plan is needed: draft it with the pa-planning skill and present it with plan(kind: generation)')
              } else {
                await put({ ...g, step: 'done' }, 'gen.paused')
                await notice(`‹pa› The developer paused the memory curator (${g.curate}); it is asked again next session.`)
              }
              return
            }
            await put({ ...g, step: 'curator' }, 'gen.run')
          }
          if (g.step === 'curator') {
            if (g.agentId === '') {
              if ((await spawnSide('memory-curator', curatorBrief(g.curate, MEMORY_DIR), 'memory curator')) === '') {
                await put({ ...g, ...cleared, step: g.kind === 'gen' ? 'audit' : 'notice', recap: '(the memory curator could not be spawned)' }, 'gen.curator.refused')
              }
              return
            }
            const a = await sideAnswer()
            if (a === null) {
              return
            }
            await journal('mod', 'end', { agentId: g.agentId, role: 'memory-curator' }, a === '' ? 'no recap' : a)
            await put({ ...g, ...cleared, step: g.kind === 'gen' ? 'audit' : 'notice', recap: a }, 'gen.curator.done')
          }
          if (g.step === 'audit') {
            if (g.agentId === '') {
              const names = g.kind === 'gen' && (await $.fs.exists(`${root}/phase-ends`))
                ? (await $.fs.list(`${root}/phase-ends`)).filter(f => f.kind === 'file').map(f => f.name)
                : []
              const pe = phaseEndsOf(names)
              if (pe === null) {
                await put({ ...g, step: 'notice' }, 'gen.audit.none')
              } else {
                const brief = auditorBrief({
                  flag: g.auditFlag, phase: pe.phase, tables: `phase-ends/${pe.newest}`, previous: pe.previous === '' ? '' : `phase-ends/${pe.previous}`,
                })
                if ((await spawnSide('auditor', brief, `auditor ${pe.phase}`)) === '') {
                  await put({ ...g, ...cleared, step: 'notice', audit: '(the auditor could not be spawned)' }, 'gen.audit.refused')
                }
                return
              }
            } else {
              const a = await sideAnswer()
              if (a === null) {
                return
              }
              await journal('mod', 'end', { agentId: g.agentId, role: 'auditor' }, a === '' ? 'no contract' : a)
              await put({ ...g, ...cleared, step: 'notice', audit: a }, 'gen.audit.done')
            }
          }
          if (g.step === 'notice') {
            const ar = g.audit === '' ? null : parseAuditorReturn(g.audit)
            const text = genNotice({
              kind: g.kind, curate: g.curate, recap: g.recap, audit: ar === null ? null : { verdicts: ar.verdicts, fixed: ar.fixed, candidates: ar.candidates },
              auditText: g.audit, deferred: g.deferred,
            })
            offCheckedAt = 0
            // 3.24 T13A: a first run's recap is relayed first: PLAN_NEEDED waits for Claude's turn after it (relayAt).
            await put({ ...g, step: 'done' }, 'gen.done', g.kind === 'gen' ? pushHold(s, 'plan') : ({ relayAt: now } as Partial<LoopState>))
            await journal('mod', 'plan', {}, g.kind === 'gen' ? 'Generation start done; Claude drafts the generation plan.' : `Memory curator ran (${g.curate}).`)
            await notice(text)
          }
        }

        // T6a2: drift at a boundary, one ask per `managed.py drift` row (state `off` while one is owed); stamp or
        // decline, committed; each row asked once a session (driftAsked). The record goes once every row is answered.
        const runDrift = async (first: Drift): Promise<void> => {
          let d = first
          const put = async (n: Drift, what: string, extra: Partial<LoopState> = {}): Promise<void> => {
            d = n
            await save({ ...s, ...extra, drift: n } as LoopState, what)
          }
          for (let row = d.rows[d.i]; row !== undefined; row = d.rows[d.i]) {
            if (d.asked === '') {
              nseq += 1
              const r = requestFile(now, nseq, 'ask', 'loop', { question: driftQuestion(row), options: [...DRIFT_OPTIONS] })
              await $.fs.write(`${inboxDir}/${r.name}`, r.body)
              await put({ ...d, asked: r.name.slice(0, -'.json'.length), answer: null }, 'drift.ask', { state: 'off' })
              return
            }
            if (d.answer === null) {
              return
            }
            const verb = /^stamp\b/i.test(d.answer) ? 'stamp' : 'decline'
            const m = await py('tools/managed.py', verb, row.rel)
            const why = m.exitCode !== 0
              ? `managed.py ${verb} exit ${String(m.exitCode)}: ${clip((m.stderr || m.stdout).trim(), 200)}`
              : await commit(`${verb} ${row.rel}`, [row.rel, '.claude/pa3-managed.json'])
            await journal('mod', 'decision', {}, `Drift: ${verb} ${row.rel} (the developer's answer)${why === '' ? '' : `; ${why}`}.`)
            await put({ ...d, i: d.i + 1, asked: '', answer: null, done: [...d.done, `${verb} ${row.rel}`] }, `drift.${verb}`)
          }
          offCheckedAt = 0
          await save({ ...s, drift: null } as LoopState, 'drift.done')
        }

        // 3.24 T6: `pa3_update.py` run here. A run that took its lock rewrites the marker, and the report follows from it
        // at a later tick; one that never did (no project, no clone, another run going) reports here, its whole output
        // kept. True when the run took place. 3.24 T7b: launched `--detach` (back at once, the running marker written),
        // as T10b launches the recalc: a mod `$.process.run` child held for minutes (was UPDATE_MS, 10 min) stalls the
        // session's queue, one delivery per 60 s; the marker's `done` brings the report, as the hook's detached run's.
        const runPa3 = async (why: string): Promise<boolean> => {
          const before = markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null))?.atMs ?? 0
          const r = await run([python, 'tools/pa3_update.py', '--detach'])
          const after = markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null))
          const whole = [r.stdout.trim(), r.stderr.trim()].filter(x => x !== '').join('\n')
          await log('update.ran', { why, exitCode: r.exitCode, output: whole })
          if (r.exitCode === 0 && after !== null && after.atMs > before) {
            await journal('mod', 'decision', {}, `PA3 update started detached (${why}); its report follows from the run marker.`)
            return true
          }
          await journal('mod', 'decision', {}, `PA3 update (${why}) did not run (exit ${String(r.exitCode)}); its whole output:\n\n${whole}`)
          await notice(`‹pa› the PA3 update did not run (exit ${String(r.exitCode)}): ${clip(whole, 600)}\nTell the developer in plain words.`)
          return false
        }

        // T6a2: the update ask (3.24 T6: production, or PA_MOD_UPDATE=1), only with no task running: "Update PA3 now?"
        // through the present queue, then `pa3_update.py` (runPa3); the record goes after the answer.
        const runUpdate = async (u: UpdateAsk): Promise<void> => {
          if (u.asked === '') {
            nseq += 1
            const r = requestFile(now, nseq, 'ask', 'loop', { question: `${u.line}\nUpdate PA3 now?`, options: [...UPDATE_OPTIONS] })
            await $.fs.write(`${inboxDir}/${r.name}`, r.body)
            await save({ ...s, state: 'off', update: { ...u, asked: r.name.slice(0, -'.json'.length), answer: null } } as LoopState, 'update.ask')
            return
          }
          if (u.answer === null) {
            return
          }
          if (/^update\b/i.test(u.answer)) {
            if (runRunning(markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null)), now)) {
              return // a run is going already (a heal the hook started): its report covers this ask once it ends
            }
            await runPa3('the developer said yes')
          } else {
            await journal('mod', 'decision', {}, `PA3 update put off by the developer: ${u.line}`)
          }
          offCheckedAt = 0
          await save({ ...s, update: null } as LoopState, 'update.done')
        }

        // While the router shows a dialog (its flag file), nothing is drained, spawned, resumed, forwarded or sent (T6a1).
        if (await dialogIsOpen()) {
          return
        }
        // 3.29.1 T8: the session start's update gate (used below): this start's check, a running update, the stale mod.
        const startState = startCheckState(await $.fs.read(`${root}/${START_CHECK}`).catch(() => null), sidNow, startAt, now)
        const gateWhy = startState === 'pending' ? 'the update check has not answered'
          : runRunning(markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null)), now) ? 'an update is running'
            : modStale ? 'the new mod is not loaded yet' : ''
        // The loop's own inbox kinds, oldest first, each marked done in loop.json before it acts.
        if (await $.fs.exists(inboxDir)) {
          const names = (await $.fs.list(inboxDir)).filter(f => f.kind === 'file').map(f => f.name)
          for (const name of pendingRequests(names, doneOf(s), now).filter(isLoopKind)) {
            // 3.29.1 T8: a release that would start work waits, unread, until the update gate opens
            if (gateWhy !== '' && WORK_HOLDS.has(holdOf(await $.fs.read(`${inboxDir}/${name}`).catch(() => '')))) {
              continue
            }
            const id = name.slice(0, -'.json'.length)
            const kept = Object.fromEntries(Object.entries(doneOf(s)).filter(([, at]) => now - at <= DONE_TTL_MS))
            await save({ ...s, inboxDone: { ...kept, [id]: now } } as LoopState, '')
            const req = parseRequest(await $.fs.read(`${inboxDir}/${name}`).catch(() => ''), name)
            if (req === null) {
              await log('release.bad', { name })
              continue
            }
            if (req.kind === 'plan_edit') {
              await planEdit(req)
            } else if (req.kind === 'steer') {
              await steer(req)
            } else if (req.kind === 'cleanup') {
              await cleanupReq(req)
            } else if (req.kind === 'forward') {
              await forwarded(req)
            } else if (req.kind === 'install') {
              await installStart(req)
            } else if (req.kind === 'answer') {
              await answered(req)
            } else {
              await release(req)
            }
          }
        }
        const inst = installOf(s)
        // 3.28.1 T6: a replan's install waits while a task runs and nothing holds the loop (talk aside), so it lands at
        // the next task boundary.
        if (inst !== null && (s.task === null || s.holds.some(h => h !== 'talk'))) {
          await runInstall(inst)
          return
        }
        const gl = genStartOf(s)
        if (gl !== null && gl.sid === sidNow && gl.step !== 'done') {
          // 3.23 T13d.c11: no curator ask while the session start waits; 3.24 T7b-2c: a first run's ask comes with the
          // developer's first message of the session (a question, not work), and its run follows the answer.
          if (!s.holds.includes('session') || (gl.kind === 'migration' && devSid === sidNow)) {
            await runGen(gl)
          }
          return
        }
        const dl = driftOf(s)
        if (dl !== null) {
          await runDrift(dl)
          return
        }
        const ul = updateOf(s)
        if (ul !== null && s.task === null) {
          await runUpdate(ul)
          return
        }
        // 3.28.1 T14: the release the developer chose installs at this clean stop (no task running); no task starts while
        // the run's marker says running (the task gate below), and its report (above) ends the step. 3.28.2 T17: a task
        // held (a stop, a question) with no expert or coder working is a clean stop too; a paused task never ends.
        const rel = releaseOf(s)
        const t1 = s.task
        if (rel !== null && rel.step === 'install' && (t1 === null || (s.holds.some(h => h !== 'talk') &&
          !(await $.agent.list()).some(a => (a.id === t1.agentId || a.parentId === t1.agentId) && BUSY.has(a.status))))) {
          if (!runRunning(markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null)), now)) {
            const ok = await runPa3(`PA3 ${rel.version}, the developer's choice`)
            await save({ ...s, release: { ...rel, step: ok ? 'running' : 'done' } } as LoopState, ok ? 'release.run' : 'release.failed')
            if (!ok) {
              await $.fs.write(`${root}/${RELEASE_MARKER}`, '{}\n').catch(quiet) // runPa3 told Claude why; the line goes
            } else { // 3.28.5 T18: the statusline's "installing" stage and Claude's one line, once per install
              await $.fs.write(`${root}/${RELEASE_MARKER}`, `${JSON.stringify({ version: rel.version, kind: rel.kind, state: 'running' })}\n`).catch(quiet)
              await notice(installingNotice(rel.version))
            }
          }
          return
        }
        // 3.29.1 T8: no work (the close chain, the first run, a plan, a recovery, a task) until this session's update check
        // has answered, none while an update runs, and none until its restart loads it (modStale). The developer's
        // requests, update runs and installs above still go.
        if (gateWhy !== updWait) {
          updWait = gateWhy
          await log('update.wait', { why: gateWhy })
          if (gateWhy === '' && startState === 'timeout') {
            await journal('mod', 'hold', {}, `The update check had not answered ${String(START_CHECK_WAIT_MS / 1000)} s after the session start; work goes on.`)
          }
        }
        if (gateWhy !== '') {
          return
        }
        // 3.29.2: the one-time `.run/` cleanup offer (3.29 T6), after the update check and any restart it needed.
        if (runReviewDue) { // 3.29.3: the mod's picker; the answer comes back through `answered`, matched by the marker
          runReviewDue = false
          nseq += 1
          const q = requestFile(now, nseq, 'ask', 'loop', { question: RUN_REVIEW_QUESTION, options: [...RUN_REVIEW_OPTIONS] })
          await $.fs.write(`${inboxDir}/${q.name}`, q.body)
          await $.fs.write(`${root}/${RUN_REVIEW_MARKER}`,
            `${JSON.stringify({ offered: modLoaded, at: now, asked: q.name.slice(0, -'.json'.length) })}\n`).catch(quiet)
        }
        // 3.29.1 T9: after the update check, a task paused on the developer's request: Claude asks, once a session start.
        if (s.task !== null && s.task.stopped?.status === 'paused' && s.holds.includes('stopped') && pausedAskedSid !== sidNow) {
          pausedAskedSid = sidNow
          await notice(pausedNotice(s.task.id))
        }
        // The close chain (T6b) runs before the no-plan path: the archive moves the plan away mid-chain.
        if (s.state === 'closing') {
          if (!s.holds.some(h => h !== 'talk')) {
            await runChain()
          }
          return
        }
        // 3.24 T7b: the project's first run in 3.2 (pa.json `first_run`, pending while `migrated` ≠ "3.2"), in any mode and
        // before the no-plan hold: the curator's one ask, then its run (runGen, above); planning waits until it is done or
        // paused. Once a session (a pause is asked again next session), never while a task runs. T7b-2c: under the session
        // hold too, once the developer has written this session (the ask comes with the first message).
        if (firstRun !== '' && s.task === null && (!s.holds.includes('session') || devSid === sidNow)) {
          await journal('mod', 'hold', {}, `The project's first run in PA 3.2: the memory curator is asked once (${firstRun}).`)
          await save({ ...s, state: 'off', genStart: newGenStart('migration', sidNow, firstRun) } as LoopState, 'gen.firstrun')
          return
        }
        // No phase plan under a generation plan (T6a1): `held(plan)` once, and Claude drafts one. T6a2: launch.py's
        // planner-gen with no generation start this session opens one. 3.24 T13A: not while a task runs.
        if (!hasPlan && !(s.task !== null && (s.state === 'running' || s.state === 'routing'))) {
          if (s.holds.includes('generation') || s.holds.includes('session') || firstPending) {
            return // T13b.c3: parked at the generation end; 3.23 T13d.c11: or held at the session start; 3.24 T7b-2c: or a first run pending (a pause holds planning this session)
          }
          const mode = await modeOf()
          if (mode === 'planner-gen' && (gl === null || gl.sid !== sidNow || gl.kind !== 'gen')) { // 3.24 T7b: after a first run
            await save({ ...s, state: 'off', genStart: newGenStart('gen', sidNow) } as LoopState, 'gen.start')
            return
          }
          // 3.24 T13A: relay first: after a first run's recap notice, not before Claude's turn that relayed it has ended.
          const relayAt = (s as LoopState & { relayAt?: number }).relayAt ?? 0
          if (relayAt > 0 && s.lastAnswerAt <= relayAt && now - relayAt < RELAY_WAIT_MS) {
            return
          }
          if (mode !== 'planner-gen' && !s.holds.includes('plan') && (await $.fs.exists(`${root}/${GEN_PLAN}`))) {
            await journal('mod', 'hold', {}, 'A phase plan is needed: no phase plan under the generation plan; Claude drafts one.')
            await save(pushHold(s, 'plan'), 'plan needed')
            await notice(PLAN_NEEDED)
          }
          return
        }
        // T6b: in `held(close)` alone, a queued plan_edit or a changed INBOX.md runs the close's boundary; a task named by
        // `next` pops `close` and runs.
        if (s.holds.includes('close') && s.holds.every(h => h === 'close' || h === 'talk') && (s.state === 'off' || s.state === 'boundary')) {
          const inbox = (await $.fs.read(`${root}/${INBOX_MD}`).catch(() => null)) ?? ''
          if (s.queuedEdits.length > 0 || inbox !== (s.closeInbox ?? inbox)) {
            const pick = await closeBoundary()
            if (pick !== null && pick['kind'] === 'task') {
              const id = strOf((pick['task'] as Record<string, unknown> | undefined)?.['id'])
              await journal('mod', 'hold', { task: id }, `Close held: ${id || 'a task'} added; the queue resumes.`)
              await save({ ...popHold(s, 'close'), state: 'boundary', closeAsked: false }, 'close.resume')
            }
          }
          return
        }
        // 3.28.1 T9c: a recovery runs under `talk` and `session` (a session end is not the developer's stop).
        if (s.state === 'running' && s.task !== null && (s.task.recovery ?? null) !== null &&
          s.holds.every(h => h === 'talk' || h === 'session')) {
          await recover(s.task, s.task.recovery!)
          return
        }
        // 3.28.8: a stop taken from absence alone (the expert missing from the list, never seen killed) is lifted by that
        // expert's own report after it, which is then captured and routed as any other (TerrainDiffusion T12: its done
        // report sat under the hold and the loop stalled). A stop seen as `killed` or `failed` keeps its hold.
        const heldTask = s.task
        if (s.state === 'running' && heldTask !== null && heldTask.stopped?.status === 'absent' && s.holds.includes('stopped') &&
          (facts[heldTask.agentId]?.handbackAt ?? 0) >= heldTask.stopped.at) {
          await save({ ...popHold(s, 'stopped'), task: { ...heldTask, stopped: null } }, 'stopped.lifted')
          await journal('mod', 'hold', { task: heldTask.id, agentId: heldTask.agentId },
            `${heldTask.id}: ${heldTask.agentId} reported after it went missing, so it was not killed; the stopped hold is lifted.`)
          await notice(`‹pa› ${heldTask.id}'s expert reported after the stop notice: it was never stopped. The stopped hold is lifted and its report goes on as usual.`)
        }
        // Any hold but `talk` stops the loop; `talk` only stops a new task (the running one is captured and routed).
        if (s.holds.some(h => h !== 'talk')) {
          return
        }
        const talking = s.holds.includes('talk')

        // T6b: `release(close)` ran: the close's boundary, then the close itself at NONE-done (the developer's agreement to
        // close is the go, so `talk` does not hold it); a task named by `next` runs instead, and the note is dropped.
        if (s.closeRequested === true && (s.state === 'off' || s.state === 'boundary')) {
          const pick = await closeBoundary()
          if (pick === null) {
            return
          }
          if (pick['kind'] === 'task') {
            const n = { ...s, state: 'boundary', closeRequested: false, closeAsked: false } as LoopState
            delete n.closeRecap
            await save(n, 'close.task')
          } else if (pick['reason'] === 'done') {
            await startClose()
          } else {
            const waiting = Array.isArray(pick['waiting_on']) ? (pick['waiting_on'] as unknown[]).map(String).join(', ') : ''
            await block({ ...s, closeRequested: false }, `the close: nothing runnable; waiting on: ${waiting || 'none listed'}`, 'mod', 'hold', '')
          }
          return
        }

        if (s.state === 'off' || s.state === 'boundary') {
          if (s.state === 'off' && offCheckedAt !== 0 && now - offCheckedAt < OFF_RECHECK_MS) {
            return
          }
          if (talking && s.queuedEdits.length === 0) {
            return
          }
          offCheckedAt = now
          const st = await py('tools/launch.py', '--state-json')
          if (st.exitCode !== 0 || jsonOf(st.stdout)?.['mode'] !== 'router') {
            if (s.state !== 'off') {
              await save({ ...s, state: 'off' }, 'off')
            }
            return
          }
          if (s.state === 'off') {
            await save({ ...s, state: 'boundary' }, 'boundary')
          }
          // Queued plan changes land here, oldest first, before the pick (also while `talk` holds: no expert runs now).
          // Each leaves the queue once run (a restart mid-way re-runs only the one in flight).
          await landQueued()
          if (talking) {
            return
          }
          // 3.24 T6: no task starts while an update runs (the run marker) or on the old mod's code (modStale).
          const waitWhy = runRunning(markerOf(await $.fs.read(`${root}/${UPDATE_MARKER}`).catch(() => null)), now)
            ? 'an update is running' : modStale ? 'the new mod is not loaded yet' : ''
          if (waitWhy !== updWait) {
            updWait = waitWhy
            await log('update.wait', { why: waitWhy })
          }
          if (waitWhy !== '') {
            return
          }
          // 3.24 T6: the update's conflicts (loop.json `upgrade`): UPGRADE.md as a one-off expert task; on its return
          // (not busy, or gone ABSENT_MS after its spawn) `pa3_update.py` once more applies the verdicts.
          const ug = upgradeOf(s)
          if (ug !== null) {
            if (ug.agentId === '') {
              const before = new Set((await $.agent.list()).map(a => a.id))
              const r = await $.agent.spawn({ subagentType: 'pa-expert', prompt: upgradeBrief(UPGRADE_MD), description: 'UPGRADE PA3 conflicts' })
              // T6e: no agentId on the result (as the task spawns): the new pa-expert in the list
              const id = r.deny !== undefined ? '' : r.agentId ?? newAgent((await $.agent.list()).filter(a => !before.has(a.id)), 'pa-expert')
              if (id === '') {
                await save({ ...s, upgrade: null } as LoopState, 'upgrade.refused')
                await journal('mod', 'hold', {}, `PA3 update conflicts: the UPGRADE expert's spawn failed (${clip(r.deny ?? 'no agentId', 200)}).`)
                await notice(upgradeLeftNotice(ug.round - 1))
                return
              }
              await save({ ...s, upgrade: { ...ug, agentId: id, at: now } } as LoopState, 'upgrade.spawn')
              await journal('mod', 'hold', { agentId: id }, `PA3 update conflicts (round ${String(ug.round)}): pa-expert ${id} resolves ${UPGRADE_MD}.`)
              return
            }
            if (ug.agentId === '-') {
              return // the rerun ran: its report (above, from the marker) closes or renews the round
            }
            const me = (await $.agent.list()).find(a => a.id === ug.agentId)
            if (me !== undefined ? BUSY.has(me.status) : now - ug.at < ABSENT_MS) {
              return
            }
            await save({ ...s, upgrade: { ...ug, agentId: '-' } } as LoopState, 'upgrade.rerun')
            await journal('mod', 'decision', { agentId: ug.agentId }, `PA3 update conflicts: the UPGRADE expert ${ug.agentId} returned (${me?.status ?? 'gone'}); pa3_update.py runs once more to apply the verdicts.`)
            if (!(await runPa3('the conflicts\' verdicts'))) {
              await save({ ...s, upgrade: null } as LoopState, 'upgrade.done')
            }
            return
          }
          // The migration curate line (T6a2): asked once a session before the next spawn.
          const curateLine = strOf(jsonOf(st.stdout)?.['curate'])
          if (curateLine.startsWith('curate: migration') && (genStartOf(s)?.sid ?? '') !== sidNow) {
            await save({ ...s, state: 'off', genStart: newGenStart('migration', sidNow, curateLine) } as LoopState, 'gen.migration')
            return
          }
          // Drift (T6a2): managed files edited since the install, each row asked once a session (runDrift).
          const dm = await py('tools/managed.py', 'drift')
          const fresh = (dm.exitCode === 0 ? driftRows(dm.stdout) : []).filter(r => !driftAsked.has(`${sidNow}\n${r.rel}`))
          if (fresh.length > 0) {
            for (const r of fresh) {
              driftAsked.add(`${sidNow}\n${r.rel}`)
            }
            await save({ ...s, state: 'off', drift: { rows: fresh, i: 0, asked: '', answer: null, done: [] } } as LoopState, 'drift')
            return
          }
          // `.run/health.json` FIX (T5d2a, pa-session.md:163-165): a note to Claude in 3.23, since a FIX promotes PA3's
          // machine root; the spawn in production (3.24 T6: PA_MOD_PA3_DIR empty; a trial forces it with PA_MOD_UPDATE=1;
          // was PA_MOD_FIX_SPAWN=1 with PA_MOD_PA3_DIR unset). Then only the `savings_missing` key is deleted.
          const healthPath = `${root}/.run/health.json`
          const health = objectOf((await $.fs.read(healthPath).catch(() => null)) ?? '')
          const missing = health?.['savings_missing']
          const sm = typeof missing === 'object' && missing !== null ? (missing as Record<string, unknown>) : null
          if (health !== null && sm !== null && Boolean(sm['hook_repair'])) {
            const row = typeof sm['row'] === 'string' ? sm['row'] : ''
            if (updateOn) {
              const r = await $.agent.spawn({ subagentType: 'pa-expert', prompt: fixBrief(row), description: `FIX ${row}`.trim() })
              await journal('mod', 'hold', {}, r.deny === undefined
                ? `Health FIX: pa-expert ${r.agentId ?? '?'} spawned for the savings repair (${row}).`
                : `Health FIX: the spawn was refused (${clip(r.deny, 200)}).`)
            } else {
              await notice(`‹pa› the hooks' savings repair is flagged (${row}); a FIX task promotes PA3's machine root, which waits for the release.`)
              await journal('mod', 'hold', {}, `Health FIX noted to Claude (${row}); the FIX spawn is off until the release.`)
            }
            const kept = Object.fromEntries(Object.entries(health).filter(([k]) => k !== 'savings_missing'))
            await $.fs.write(healthPath, `${JSON.stringify(kept, null, 2)}\n`)
          }
          // A WAIT-FOR being polled (T5d2a): no pick, so no other task starts, until its next poll.
          if (s.waitFor !== null && now - waitPolledAt < WAIT_POLL_MS) {
            return
          }
          const nx =await py('tools/plan_edit.py', 'next', '--brief', '--json')
          const pick = nx.exitCode === 0 ? jsonOf(nx.stdout) : null
          if (pick === null) {
            await block(s, `plan_edit next: exit ${String(nx.exitCode)} ${clip(nx.stderr || nx.stdout, 200)}`, 'mod', 'hold', '')
            return
          }
          if (pick['kind'] !== 'task') {
            if (pick['reason'] === 'done') {
              // T6b: `held(close)` and the close question to Claude (once a plan end), with the tasks' summaries (T13b); its
              // stop toast (`input`) is raised by Claude's presentation.
              const asked = s.closeAsked === true
              const inbox = (await $.fs.read(`${root}/${INBOX_MD}`).catch(() => null)) ?? ''
              await journal('mod', 'plan', {}, 'Plan end: every task is done; the close follows.')
              await save(pushHold({ ...s, closeAsked: true, closeInbox: inbox }, 'close'), 'close')
              if (!asked) {
                // 3.25 T2: at a generation end the notice says the report covers the whole generation.
                const pp = planPhase((await $.fs.read(`${root}/${PLAN}`).catch(() => null)) ?? '')
                const genEnd = genEndOf((await $.fs.read(`${root}/${GEN_PLAN}`).catch(() => null)) ?? '', pp.gen)
                await notice(closeNotice(pp.phase || '?', summariesOf(s), genEnd ? (pp.gen.split('.')[0] ?? '') : ''), 'input')
              }
            } else {
              const waiting = Array.isArray(pick['waiting_on']) ? (pick['waiting_on'] as unknown[]).map(String).join(', ') : ''
              await block(s, `nothing runnable; waiting on: ${waiting || 'none listed'}`, 'mod', 'hold', '')
            }
            return
          }
          const plan = pick['task'] as PlanTask
          const promoted = pick['promoted']
          if (promoted !== null && promoted !== undefined && promoted !== false && promoted !== '') {
            const why = await commit(`${typeof promoted === 'string' ? promoted : plan.id} promoted`, [PLAN])
            if (why !== '') {
              await block(s, why, 'mod', 'hold', '')
              return
            }
          }
          // WAIT-FOR (T5d2a, pa-session step 2): the file polled every 30 s; met, the task runs; 50 min, the review stop.
          const wf = plan.wait_for ?? ''
          if (wf === '' && s.waitFor !== null) {
            await save({ ...s, waitFor: null }, 'waitfor.clear')
          }
          if (wf !== '') {
            const spec = waitSpec(wf)
            if (spec === null) {
              await block(s, `wait-for of ${plan.id} is not "<file> contains <literal>": ${clip(wf, 200)}`, 'mod', 'hold', '')
              return
            }
            waitPolledAt = now
            const text = await $.fs.read(`${root}/${spec.file}`).catch(() => null)
            const prior = s.waitFor
            if (text !== null && text.includes(spec.literal)) {
              await journal('mod', 'hold', { task: plan.id }, `WAIT-FOR met: ${spec.file} contains ${spec.literal}; ${plan.id} runs.`)
              await save({ ...s, waitFor: null }, 'waitfor.met')
            } else if (prior === null || prior.task !== plan.id || prior.file !== spec.file || prior.literal !== spec.literal) {
              await save({ ...s, waitFor: { task: plan.id, file: spec.file, literal: spec.literal, since: now } }, 'waitfor')
              await journal('mod', 'hold', { task: plan.id },
                `WAIT-FOR: ${plan.id} waits for ${spec.file} to contain ${spec.literal} (polled every 30 s, up to 50 min); no task starts meanwhile.`)
              return
            } else if (now - prior.since < WAIT_MAX_MS) {
              return
            } else {
              const why = `${plan.id} waited 50 min for ${spec.file} to contain ${spec.literal}`
              if (!(await $.fs.exists(`${root}/${REVIEW}`))) {
                const rv = await py('tools/task_log.py', 'review', plan.id)
                if (rv.exitCode !== 0) {
                  await block({ ...s, waitFor: null }, `${why}; task_log review ${plan.id} refused: ${clip((rv.stdout || rv.stderr).trim(), 200)}`, 'mod', 'hold', '')
                  return
                }
              }
              await py('tools/status.py', 'wait', 'REVIEW.md')
              await journal('mod', 'hold', { task: plan.id }, `${why}: REVIEW.md written; held for review.`)
              await save(pushHold({ ...s, waitFor: null }, 'review'), 'waitfor.timeout')
              await notice(waitReviewNotice(plan.id, spec.file, spec.literal), 'review')
              return
            }
          }
          const gate = await py('tools/status.py', 'window-gate')
          if (gate.exitCode === 3) {
            const status = jsonOf((await $.fs.read(`${root}/.run/status.json`).catch(() => null)) ?? '')
            const at = Date.parse(typeof status?.['resume_at'] === 'string' ? status['resume_at'] : '')
            const resumeAt = Number.isFinite(at) ? at : now + 5 * 60_000
            await journal('mod', 'hold', { task: plan.id }, `Window gate: held before ${plan.id} until ${new Date(resumeAt).toISOString()}.`)
            await save({ ...pushHold(s, 'window'), resumeAt }, 'window')
            return
          }
          if (gate.exitCode !== 0) {
            await block(s, `window-gate exit ${String(gate.exitCode)}: ${clip(gate.stderr || gate.stdout, 200)}`, 'mod', 'hold', '')
            return
          }
          if (await dialogIsOpen()) {
            return // a dialog opened during this tick: the pick runs again after it
          }
          const agent = agentFor(plan.agent, plan.effort)
          await py('tools/status.py', 'set', '--task', plan.id, '--agent', agent, '--coder', plan.coder ?? 'none', '--kind', 'task',
            '--attempt', '1', '--title', plan.title ?? '', '--session', sidNow)
          const opts: ExpertOpts = {}
          if (knob((await $.env.get('PA_MOD_TRIAL_HANDOFF')) ?? '', plan.id) !== null) {
            opts.trialHandoff = true
          }
          const q = knob((await $.env.get('PA_MOD_TRIAL_QUESTION')) ?? '', plan.id)
          if (q !== null) {
            // T5d2b: PA_MOD_TRIAL_QUESTION_TEXT replaces the question's text (one Claude must take to the developer).
            const qt = ((await $.env.get('PA_MOD_TRIAL_QUESTION_TEXT')) ?? '').trim()
            opts.trialQuestion = qt !== '' ? qt : q === '' ? 'proceed as planned' : q
          }
          if (knob((await $.env.get('PA_MOD_TRIAL_CODER')) ?? '', plan.id) !== null) {
            opts.trialCoder = true
          }
          await spawnTask(plan, 1, 'task', opts)
          return
        }

        const t = s.task
        if (t === null) {
          await save({ ...s, state: 'boundary' }, 'no task')
          return
        }
        if (s.state === 'running') {
          if (t.agentId === '') {
            if (now - t.spawnedAt >= SPAWN_LOST_MS) {
              await block(s, `spawn of ${t.id} lost (no agentId recorded)`, 'mod', 'hold', '')
            }
            return
          }
          // The contract (T5c.c3): the expert's last STATUS block, interim (pendingContract, T<n>/interim) until the run
          // has ended; at runEnded the latest is routed, else the transcript. Nothing routes or respawns before.
          const f = facts[t.agentId]
          let cur = t
          if (f !== undefined && f.status !== null && f.status !== (t.pendingContract ?? null)) {
            cur = { ...t, pendingContract: f.status }
            await save({ ...s, task: cur }, 'interim')
            await journal(t.id, 'interim', { agentId: t.agentId, role: t.agent, task: t.id }, f.status)
          }
          // T11-fix2: the agent's own status, every tick. pending, running, waiting: busy. killed, failed, or absent
          // ABSENT_MS after its spawn: with a STATUS, routed at once; with none, the `stopped` hold. A release(stopped)
          // resume gets ABSENT_MS before a stale `killed` counts again. 3.28.1 T9c: killed or absent and never seen at work
          // by this process: a session end cut it off; recovered (continue:, else a respawn), no hold.
          const list = await $.agent.list()
          const me = list.find(a => a.id === t.agentId)
          // 3.28.1 T15: a background shell its run waits on (its own or a child's, pa-watch's shells.json) keeps it busy;
          // dropped from the list meanwhile, it waits (the shell's completion notice wakes it), never stopped; so it does
          // for FORWARD_TTL_MS after a send to it (`forwardAt`) until its next turn ends.
          const shellsLive = shellsOut(shellsOf(await $.fs.read(`${root}/.run/mod/${sidNow}/${SHELLS_FILE}`).catch(() => null)),
            [t.agentId, ...list.filter(a => a.parentId === t.agentId).map(a => a.id)]).length
          const sentTo = (t.forwardAt ?? 0) > t.lastCompleteAt && now - (t.forwardAt ?? 0) < FORWARD_TTL_MS
          // 3.28.8: so does a child still at work (TerrainDiffusion T12: its expert, dropped from the list while its
          // coder ran the census, was taken for killed and the loop held).
          const busy = list.filter(a => a.parentId === t.agentId && BUSY.has(a.status)).length
          const live = me === undefined && (shellsLive > 0 || sentTo || busy > 0) ? 'idle' : liveness(me?.status, now - t.spawnedAt)
          if (me !== undefined && live !== 'stopped') {
            seenLive.add(t.agentId)
          }
          if (live === 'stopped') {
            if (cur.stopped?.status === 'resumed' && now - cur.stopped.at < ABSENT_MS) {
              return
            }
            if ((cur.pendingContract ?? null) === null && !seenLive.has(t.agentId) && me?.status !== 'failed') {
              await save({ ...s, task: { ...cur, recovery: { phase: 'start', at: now } } }, 'recover')
              await journal('mod', 'recover', { task: t.id, agentId: t.agentId },
                `recovery: ${t.agentId} ${me?.status ?? 'absent'}, never seen at work by this process: a session end, not a stop.`)
              return
            }
            if ((cur.pendingContract ?? null) === null) {
              await stopHold(cur, me?.status ?? 'absent')
              return
            }
            await save({ ...keepSummary(s, cur, cur.pendingContract ?? null), state: 'routing',
              task: { ...cur, contract: cur.pendingContract ?? '', lastRouted: cur.pendingContract ?? '' } }, 'captured:stopped')
            return
          }
          // T5d2a.c2: busy → idle is recorded (childIdleAt), so runEnded waits for the hand-back's forward request.
          if ((busy > 0) !== (cur.childBusy ?? false)) {
            cur = { ...cur, childBusy: busy > 0, ...(busy > 0 ? {} : { childIdleAt: now }) }
            await save({ ...s, task: cur }, busy > 0 ? 'child.busy' : 'child.idle')
          }
          if (live === 'busy') {
            return
          }
          if (!runEnded(cur, busy + shellsLive, queuedTo(cur, now), now, (cur.pendingContract ?? null) === null ? await activityAt(t.agentId) : 0,
            f?.handbackAt ?? 0)) {
            return
          }
          let contract: string | null = cur.pendingContract ?? null
          let via = contract === null ? '' : 'status'
          if (contract === null) {
            const tp = transcriptSeen.sid === sidNow ? transcriptSeen.path : ''
            const text = tp === '' ? null : await $.fs.read(subagentTranscript(tp, sidNow, t.agentId)).catch(() => null)
            const last = text === null ? null : lastAssistantText(text)
            // 3.25 T3: the last text whether or not it parses (no status word routes as a block carrying it, for Claude).
            contract = last !== null && last.trim() !== '' && last.trim() !== '.' ? last : ''
            via = contract === '' ? 'none' : 'transcript'
          }
          await save({ ...keepSummary(s, cur, contract), state: 'routing', task: { ...cur, contract, lastRouted: contract } }, `captured:${via}`)
          return
        }
        if (s.state !== 'routing' || routedOf(t) !== '') {
          return
        }
        const contract = t.contract ?? ''
        const c = parseExpertContract(contract)
        const body = contract === '' ? 'no contract' : contract
        const meta: Meta = { agentId: t.agentId, role: t.agent, task: t.id }
        const route = routeStatus(t.contract)
        if (route === 'done') {
          // 3.25 T3: the task's commit from git (the newest since its start), never the report; a done with none is done.
          const sha = t.startSha === '' ? '' : (await run(['git', 'rev-list', '--max-count=1', `${t.startSha}..HEAD`])).stdout.trim()
          const set = await py('tools/plan_edit.py', 'set-status', t.id, 'done', '--by', 'router')
          if (set.exitCode !== 0) {
            await block({ ...s, task: { ...t, routed: 'block' } as LoopTask },
              `set-status ${t.id} done refused: ${clip(set.stderr || set.stdout, 200)}`, t.id, 'end', body)
            return
          }
          const why = await commit(`${t.id} done`, [PLAN])
          if (why !== '') {
            await block({ ...s, task: { ...t, routed: 'block' } as LoopTask }, why, t.id, 'end', body)
            return
          }
          await journal(t.id, 'end', meta, sha === '' ? body : `${body}\n\n(commit ${sha.slice(0, 12)})`)
          delete facts[t.agentId]
          const dm = doneMarkOf(s, t.id) // 3.29.9: the display is trimmed to the task's start when this session marked it
          await save({ ...s, state: 'boundary', task: null, lastDone: { id: t.id, at: await $.clock.now() }, ...dm } as LoopState, 'done') // 3.24 T17p
          if (dm.doneMark !== null) {
            trimSoon('done')
          }
          return
        }
        if (route === 'handoff') {
          const plan = planOf(t)
          await journal(t.id, 'end', meta, body)
          if (plan === null) {
            await block({ ...s, task: { ...t, routed: 'block' } as LoopTask }, `handoff of ${t.id} without its plan entry`, 'mod', 'hold', '')
            return
          }
          const k = t.attempt + 1
          await py('tools/status.py', 'set', '--task', t.id, '--agent', t.agent, '--kind', 'handoff', '--attempt', String(k), '--session', sidNow)
          delete facts[t.agentId]
          await spawnTask(plan, k, 'handoff', { progress: PROGRESS })
          return
        }
        if (route === 'review') {
          // 3.24 T8: REVIEW.md is (re)built unless it is newer than the task's summary (one left from an earlier stop is stale).
          const summary = `phase-ends/current/tasks/${t.id}.md`
          const rs = await $.fs.stat(`${root}/${REVIEW}`).catch(() => null)
          const ss = await $.fs.stat(`${root}/${summary}`).catch(() => null)
          const fresh = rs !== null && rs.kind === 'file' && (ss === null || rs.mtimeMs >= ss.mtimeMs)
          if (!fresh) {
            const rv = await py('tools/task_log.py', 'review', t.id)
            if (rv.exitCode !== 0) {
              const why = clip((rv.stdout || rv.stderr).trim(), 200)
              // A summary without a Review: block: its expert is asked for it once by message (it resumes, nothing is
              // killed) and the route runs again on its next contract; still missing, a review hold presented from the summary.
              if (t.reviewAsked !== true && t.agentId !== '' && !(await expertCold(t))) {
                const sent = await $.session.send({ to: { agentId: t.agentId }, text: reviewAskText(t.id, summary, why) })
                await log('review.ask', { agentId: t.agentId, delivered: sent.isDelivered })
                if (sent.isDelivered) {
                  delete facts[t.agentId]
                  // lastCompleteAt 0: the run counts as ended only after its next turn.complete; lastRouted '': the same
                  // contract again is routed, not ignored as a repeat.
                  await save({ ...s, state: 'running', task: { ...t, reviewAsked: true, contract: null, pendingContract: null, lastCompleteAt: 0,
                    lastRouted: '', routed: '' } as LoopTask }, 'review.ask')
                  await journal('mod', 'hold', { task: t.id, agentId: t.agentId },
                    `${t.id} returned review without a Review: block (${why}); ${t.agentId} asked for it.`)
                  return
                }
              }
              await py('tools/status.py', 'wait', 'question', '--note', clip(`${t.id} review: no Review: block`, 200))
              await journal(t.id, 'end', meta, `${body}\n\n(task_log review ${t.id} refused: ${why})`)
              await save(pushHold({ ...s, task: { ...t, routed: 'review' } as LoopTask }, 'review'), 'review: from the summary')
              await notice(reviewSummaryNotice(t.id, summary, why), 'review')
              return
            }
          }
          await py('tools/status.py', 'wait', 'REVIEW.md')
          await journal(t.id, 'end', meta, body)
          await save(pushHold({ ...s, task: { ...t, routed: 'review' } as LoopTask }, 'review'), 'review')
          await notice(reviewNotice(t.id), 'review')
          return
        }
        // question, blocked or no contract: Claude's notice carries a plain question's text as it is (3.25 T3), or a legacy
        // one's QUESTION and RECOMMENDED, or the contract, or "no contract".
        const legacy = /STATUS\s*[:=]/i.test(contract.slice(0, 2000))
        const what = route === 'question'
          ? legacy
            ? `${t.id} asks a question.\nQUESTION: ${c?.question || '(none given)'}\nRECOMMENDED: ${c?.recommended || '(none given)'}`
            : `${t.id} asks a question:\n${clip(contract.trim(), 800)}`
          : contract === '' ? `${t.id}: no contract` : `${t.id} blocked:\n${clip(contract, 800)}`
        await block({ ...s, task: { ...t, routed: route } as LoopTask }, route === 'question' ? `${t.id} asks a question` : `${t.id} blocked`, t.id, 'end', body,
          what)
      }

      const tick = $.clock.every(TICK_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            tick.cancel()
            return
          }
          if (ticking) {
            return
          }
          ticking = true
          try {
            await step()
          } catch (err) {
            await log('error', { err: clip(String(err), 300) })
          }
          try {
            await bandTick()
          } catch (err) {
            await log('band.error', { err: clip(String(err), 300) })
          } finally {
            ticking = false
          }
        })()
      })
      await log('loop.armed', { gen })
    }
    // A new process (or a test): nothing carried from a previous session's module state.
    facts = {}
    loopAgent = ''
    ticking = false
    offCheckedAt = 0
    devAt = 0
    devSid = ''
    partnerSaid = { at: 0, asked: false, notice: false }
    seenLive.clear()
    shellQ = []
    waitPolledAt = 0
    genMode = { sid: '', at: 0, doc: null }
    sideAgent = ''
    routedText = ''
    modSeen = null
    modStale = false
    updWait = ''
    releaseCheckedAt = 0
    apRearm = arm
    apTrim = null
    trimAgain = ''
    startAt = await $.clock.now() // 3.29.1 T8: the update check's note must be this start's
    pausedAskedSid = ''
    // 3.28.2 T17: the release this process loads (the tick's update report and release record compare with it).
    const pa3 = ((await $.env.get('PA_MOD_PA3_DIR')) ?? '') ||
      `${((await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '').replace(/\\/g, '/')}/.claude/pa3`
    const installed = ((await $.fs.read(`${pa3}/VERSION`).catch(() => null)) ?? '').split(/\r?\n/)[0]?.replace(/^version:/, '').trim() ?? ''
    modLoaded = installed
    if (await $.fs.exists(`${root}/${RELEASE_MARKER}`)) {
      // 3.28.2 T16: the release the marker names is the installed VERSION: this start loaded it; Claude says so first,
      // so the resumed conversation's last line (the restart) doesn't read as a loop.
      const relm = objectOf((await $.fs.read(`${root}/${RELEASE_MARKER}`).catch(() => null)) ?? '')
      const v = typeof relm?.['version'] === 'string' ? relm['version'] : ''
      if (v !== '' && v === installed) {
        nseq += 1
        const r = noticeRequest(await $.clock.now(), nseq, loadedNotice(v))
        await $.fs.write(`${root}/.run/mod/${sid}/inbox/${r.name}`, r.body).catch(quiet)
      }
      // 3.28.1 T14: the restart loaded it. 3.29.2: but a release still installing (the start hook's run) stays.
      if (v === installed || relm?.['state'] !== 'running') {
        await $.fs.write(`${root}/${RELEASE_MARKER}`, '{}\n').catch(quiet)
      }
    }
    // 3.29 T6: the one-time `.run/` cleanup, offered once per project when `.run/` holds work from before 3.29.
    // 3.29.5: a marker counts once the picker was shown (`asked`, '' when there was nothing to offer); an older one
    // (3.29-3.29.2's plain-text offer, easy to miss) gets the picker at the next start
    const rvm = objectOf((await $.fs.read(`${root}/${RUN_REVIEW_MARKER}`).catch(() => null)) ?? '')
    if (rvm === null || typeof rvm['asked'] !== 'string') {
      const names = (await $.fs.list(`${root}/.run`).catch(() => [])).map(f => f.name)
      const logs = (await $.fs.list(`${root}/.run/logs`).catch(() => [])).length
      // 3.29.2: offered once the update gate opens (the tick), never before an update and its restart; marked then
      runReviewDue = olderRunWork(names, logs)
      if (!runReviewDue) {
        await $.fs.write(`${root}/${RUN_REVIEW_MARKER}`, `${JSON.stringify({ offered: '', asked: '', at: await $.clock.now() })}\n`).catch(quiet)
      }
    }
    await arm(sid, true)
    // 3.29.9: an interactive start (a --continue resumes its rows): a completion trim that never ran runs now.
    const tr = apTrim
    if (e.isInteractive && tr !== null) {
      $.clock.after(TRIM_START_MS, () => tr('start'))
    }
    return out
  })

  // The session's transcript (its project folder holds the subagents' transcripts); a resume or /clear re-arms after
  // the router's own re-arm moved the generation on.
  on('classic.SessionStart', {}, async ($, e, next) => {
    if (typeof e.transcript_path === 'string' && e.transcript_path !== '') {
      transcriptSeen = { sid: e.session_id, path: e.transcript_path }
    }
    if (e.source === 'startup' || e.source === 'resume' || e.source === 'clear') {
      startSid = e.session_id // 3.23 T13d.c11: every session start waits for the developer
    }
    if (e.source === 'clear') {
      clearSid = e.session_id
    }
    const f = apRearm
    if ((e.source === 'resume' || e.source === 'clear') && f !== null) {
      const sidNow = e.session_id
      $.clock.after(1_000, () => {
        // 3.29.9: a resume's rows may hold a completion trim that never ran (the start's catch-up)
        void f(sidNow).then(() => (e.source === 'resume' ? apTrim?.('start') : undefined)).catch(quiet)
      })
    }
    return next(e)
  })
  on('session.end', {}, async ($, e, next) => {
    const f = apRearm
    if (e.reason === 'clear' && f !== null) {
      $.clock.after(1_500, () => {
        void (async () => {
          await f(await $.session.id())
        })().catch(quiet)
      })
    }
    return next(e)
  })

  // T6a2: the SessionStart hook's lines (door `hook-context`, 3.21-probe.md P17): the update and renewal lines, each
  // queued once a session for the tick (it may arrive before session.start, so the queue is keyed by session).
  on('session.append', { door: 'hook-context' }, async ($, e, next) => {
    const out = await next(e)
    const sid = await $.session.id()
    const text = e.message.content.map(b => (b.type === 'text' ? b.text : '')).join('\n')
    for (const c of contextLines(text)) {
      if (!contextSeen.has(`${sid}\n${c.line}`)) {
        contextSeen.add(`${sid}\n${c.line}`)
        contextQ = [...contextQ, { sid, ...c }]
      }
    }
    return out
  })

  // Capture facts: the loop agent's last STATUS-bearing hand-back report or turn.complete answer (text without STATUS,
  // such as `WAITING: <coder> running`, is ignored) and its last turn end (a hand-back counts as one); the
  // developer's prompt (the tick pushes `talk`) and Claude's answers (whether one asked something).
  on('prompt.submit', {}, async ($, e, next) => {
    const out = await next(e)
    if (out.drop === undefined && (await read($, routeAtom)) && classify(e.origin, e.text, await read($, sdkAtom)) === 'developer') {
      devAt = await $.clock.now()
      devSid = await $.session.id()
    }
    const child = HANDBACK_KINDS.has(e.origin.kind) ? handbackChild(e.text) : null
    if (child !== null && child !== '' && child === loopAgent) {
      // 3.28.1 T15: an expert woken by its shell's notice reports with SendMessage (its hand-back was spent): a note
      // whose body is a contract is its report.
      const report = partnerReport(e.text) ?? (isNote(e.text) ? noteBody(e.text) : null)
      const f = facts[child] ?? { status: null, lastCompleteAt: 0 }
      if (report !== null && routedText.trim() !== '' && report.trim() === routedText.trim()) {
        // T6a2: a repeat of the routed contract, ignored (3.25 T3: without a journal line)
      } else if (report !== null && parseExpertContract(report) !== null) {
        const at = await $.clock.now()
        facts = { ...facts, [child]: { ...f, status: report, lastCompleteAt: Math.max(f.lastCompleteAt, at), handbackAt: at } }
      }
    } else if (child !== null && child !== '' && child === sideAgent) {
      // T6a2: the generation start's agent: its last answer of any form; a hand-back is a turn end. 3.24 T13A: a hand-back's
      // report is the answer (the curator's recap) and is kept against later turns.
      const report = (partnerReport(e.text) ?? '').trim()
      const f = facts[child] ?? { status: null, lastCompleteAt: 0 }
      const at = await $.clock.now()
      facts = { ...facts, [child]: { ...f, lastCompleteAt: Math.max(f.lastCompleteAt, at), ...(report === '' || report === '.' ? {} : { answer: report, handbackAt: at }) } }
    }
    return out
  })
  // 3.28.1 T15: an agent's `WAITING: shell <what>` hand-back: the tick tells Claude.
  on('tool.check', { tool: 'SubagentHandback' }, async ($, e, next) => {
    const m = (e.input as { message?: unknown } | null)?.message
    waitsOnShell(e.agentId, typeof m === 'string' ? m : '', await $.clock.now())
    return next(e)
  }).catch(($, e, next) => next(e)) // a failure leaves the verdict beneath
  on('turn.complete', {}, async ($, e, next) => {
    const id = e.agentId
    if (id !== undefined && id !== '' && id === loopAgent && routedText.trim() !== '' && e.answer.trim() === routedText.trim()) {
      // T6a2: a repeat of the routed contract, ignored (no fact update; 3.25 T3: without a journal line)
    } else if (id !== undefined && id !== '' && id === loopAgent) {
      const now = await $.clock.now()
      const f = facts[id] ?? { status: null, lastCompleteAt: 0 }
      const status = parseExpertContract(e.answer) !== null ? e.answer : f.status
      facts = { ...facts, [id]: { ...f, status, lastCompleteAt: now } }
    } else if (id !== undefined && id !== '' && id === sideAgent) {
      const f = facts[id] ?? { status: null, lastCompleteAt: 0 }
      const said = e.answer.trim()
      // 3.24 T13A: after a hand-back its report stays the answer (round 2: the curator's after-hand-back line was relayed).
      const keep = said === '' || said === '.' || (f.handbackAt ?? 0) > 0
      facts = { ...facts, [id]: { ...f, lastCompleteAt: await $.clock.now(), ...(keep ? {} : { answer: e.answer }) } }
    }
    const said = e.answer.trim()
    const rs = await read($, stateAtom)
    if (id !== undefined && id !== '' && said !== '' && said !== '.' && rs?.partner?.agentId === id) {
      // 3.28.1 T9b: an answer after the mod's own notice (the last message sent to Claude was `other`) does not keep `talk`.
      partnerSaid = { at: await $.clock.now(), asked: asksQuestion(said), notice: rs.sentKind === 'other' }
    }
    return next(e)
  })
}
