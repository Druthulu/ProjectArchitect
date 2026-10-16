// The router's contract: its $.state values (they survive a hot reload, not /clear) and the shape of its state file
// .run/mod/<sid>/state.json (hooks/state.ts parses and writes it). Self-contained: validate refuses imports here.
// No noun on $: the plugins talk through files.

export type Partner = {
  agentId: string
  spawnedAt: number
  lastTurnAt: number
  model: string
  ctx: number
  /** The partner raised the topic flag (a topic change: rotate at the next quiet point). */
  topic: boolean
  modelSwitchAt: number
  /** A warming `.` ping turn is running (T8a sets it): messages wait in `outbox` until its turn.complete. */
  pingInFlight: boolean
  /** The context of its first answered turn (seed + fixed prompt): what a fresh start costs to write; 0 unknown. */
  firstCtx: number
}

export type Child = { parent: string; type: string; startedAt: number; status: string }

/** Warm pings: session totals, and the current wait's (since the partner's last real answer). */
export type Warm = {
  pings: number
  pingUsd: number
  avoidedUsd: number
  lastPingAt: number
  waitStart: number
  waitUsd: number
  waitPings: number
  /** 3.28.1 T12: the current stretch's pings (policy.pingCount at its start); 0 until its first warming tick. */
  waitCount: number
}

/** A loop expert the warming tick keeps warm (3.23 T5d2b): its totals, its ping in flight and its current wait
 * (`waitKind` `child` or `question`, since `waitStart`; reset when it is seen working or ends a real turn).
 * 3.28.1 T12: the wait's pings so far (`waitPings`) and its count (`waitCount`, 0 until set). */
export type WarmAgent = {
  role: string
  lastPingAt: number
  pingInFlight: boolean
  waitKind: string
  waitStart: number
  waitUsd: number
  waitPings: number
  waitCount: number
  pings: number
  pingUsd: number
  avoidedUsd: number
}

/** A journal entry owed to disk: its path under the project root and its whole text (the route tick writes it). */
export type JournalOut = { rel: string; text: string }

/** A waiting toast owed (`question`: an answer that asks; `input`: an open `ask`), raised by the route tick. */
export type ToastOut = { cause: 'question' | 'input' | 'review'; body: string; at: number; /** 3.23 T13d.c10: the your-turn toast. */ turn?: true }

/** 3.23 T13d.c10: the your-turn toast owed to Claude's latest reply (its turnId): when it landed, its first line, the
 * agents live then (a later one starting drops it), and whether it fired (once per reply). */
export type YourTurn = { turnId: string; at: number; body: string; known: string[]; fired: boolean }

/** A decision owed to an agent (a plan, an ask), answered later as a message. */
export type Pending = { id: string; agentId: string; kind: string; at: number }

export type PlanEdit = { id: string; agentId: string; at: number }

/** A dialog owed to the developer (T8c, hooks/planview.ts), shown one at a time by the presentation tick: a plan
 * (`path`, `title`: the partner's plan file) or an ask (`question`, 2-4 `options`). `caller` gets the answer.
 * 3.23 T6a1: a plan's `planKind` (generation and phase are installed on approval), its `rules` and `candidates` lines. */
export type Present = {
  id: string
  kind: 'plan' | 'ask'
  caller: string
  path: string
  title: string
  question: string
  options: string[]
  at: number
  planKind?: 'generation' | 'phase' | 'adhoc'
  rules?: string[]
  candidates?: string[]
}

/** The session's main plan file, learned once per session from an ExitPlanMode outside plan mode. */
export type PlanFile = { sid: string; path: string }

export type Hold = { reason: string; at: number }

/** A message owed to an agent, sent by the route tick in order: the developer's (`dev`), a framed other prompt
 * (`other`), both to the partner, or a child's hand-back to forward to its parent (`forward`, `child` set). */
export type OutMsg = { kind: 'dev' | 'other' | 'forward'; text: string; child: string; at: number }

/** A message sent to the recorded partner whose run is not yet seen (`running`/`pending` or a turn.complete). */
export type Awaiting = { agentId: string; text: string; at: number }

/** How the router answers a main-loop turn: the reply of marker `n`, a partner hand-back's report (`reply`), the
 * attachment notice, a card (3.23 T13: a loop agent's note or finish, hooks/card.ts), the feed's pending lines (3.23
 * T13d, hooks/feed.ts), or the stub. */
export type TurnKind =
  | { kind: 'marker'; n: number } | { kind: 'reply'; text: string } | { kind: 'attach' } | { kind: 'card'; text: string } | { kind: 'feed' }
  | { kind: 'stub' }

/** 3.23 T13d: one numbered utterance of the live feed, as the feed script prints and logs it. 3.24 T13B: `reply`, a reply
 * of Claude's the router wrote (its marker number): drawn as a Claude card. */
export type FeedRecord = { n: number; agent: string; type: string; desc: string; planning: boolean; text: string; at: number; edit?: FeedEdit; reply?: number; shown?: string } // 3.24 T17g: shown, a reply's model and effort

/** 3.24 T8b: a file edit's card, the watcher's `edit` record field (mod/pa-watch/hooks/edit.ts): the path shown, created or
 * updated, the +/- counts, unified hunks with `@@` headers (a created file: one block of its first lines), rows not shown.
 * 3.24 T13C/T13B: `label` replaces `Created <path>` / `Updated <path>` (a plan draft: "Plan drafted", no hunks). */
export type FeedEdit = { path: string; created: boolean; add: number; del: number; hunks: string[]; more: number; label?: string }

/** 3.23 T13d: the router's feed atom: records not yet drawn (by `n`), the last `n` drawn (null: none yet this load),
 * and the last drawn speaker's key ('' after any other main turn). */
export type FeedPending = { pending: FeedRecord[]; drawn: number | null; speaker: string; announced?: string[] } // 3.24 T17b: agents whose model and effort were shown

/** 3.23 T13d.c2: one run of one speaker in a feed answer, as the terminal draws it: the label, the card role (null: an
 * unknown type, no color), whether it opens with the title (a continuation of the last speaker does not), its lines;
 * 3.24 T8b: an edit record is a run of its own carrying `edit` (no lines). 3.24 T13B: a reply record is a run of its own
 * carrying `card` (drawn as a Claude card); the roles gained the curator and the auditor. */
export type FeedRun = {
  label: string; role: 'claude' | 'planning' | 'expert' | 'coder' | 'thinker' | 'curator' | 'auditor' | null; titled: boolean; lines: string[]
  edit?: FeedEdit; card?: boolean
}

/** 3.23 T13d.c2: a feed answer's stored text and its runs (the AssistantMessage rule draws only these). */
export type FeedDrawn = { text: string; runs: FeedRun[] }

/** 3.23 T13d.c1c: the feed's life in this load: a feed seen, the first interactive developer prompt's time, the
 * restarts' times, the router's own Monitor call running. */
export type FeedLife = {
  seen: boolean; devAt: number | null; restarts: number[]; calling: boolean
  /** 3.23 T13d.c2: a restart's Monitor not yet ended (no end notice or expiry since); why the feed could not start ('' none). */
  waiting: boolean; error: string
}

/** A partner hand-back to show: its reply key, the prompt that carried it, the report. */
export type ReplyTurn = { key: string; prompt: string; text: string }

/** The main-loop turns registered at turn.start (by turnId), accepted texts that carried attachments, and partner
 * hand-backs whose turn shows the report (absent in a registry from before T8d). */
export type TurnRegistry = { kinds: Record<string, TurnKind>; attach: string[]; replies?: ReplyTurn[] }

/** A partner reply's path (T8d), by `agentId:hash` of its text: `wait` (answer filed, hand-back awaited; marker `n` after
 * the wait), `marker` (the fallback marker queued), `handback` (the hand-back showed it), `done` (both seen). */
export type ReplyMark = { via: 'wait' | 'marker' | 'handback' | 'done'; n: number; at: number }

export type RouterState = {
  v: number
  gen: number
  sid: string
  partner: Partner | null
  /** agentId -> the agent the router spawned and has not seen finish. */
  children: Record<string, Child>
  /** inbox request id -> when it was marked done (ms). */
  inboxDone: Record<string, number>
  /** marker number -> the file holding the reply it carries: its `partner/answer` journal entry (state files from
   * 0193534 name `.run/mod/<sid>/replies/<n>.md`, read whole). */
  markers: Record<string, string>
  /** Marker numbers the route tick still has to deliver, in order: a feed reply record while the feed lives (3.24 T13B),
   * else the `‹pa reply <n>›` prompt. */
  pendingMarkers: number[]
  /** The partner's replies by `agentId:hash` (route.replyKey): which path carried each (T8d). */
  replies: Record<string, ReplyMark>
  outbox: OutMsg[]
  /** child agentId -> when its hand-back was taken (a coder's end can arrive as task-notification and as peer). */
  forwarded: Record<string, number>
  awaiting: Awaiting | null
  /** 3.28.1 T9b: the kind of the last message delivered to the partner ('' none yet); an answer after an `other` (a mod
   * notice, a dialog's answer) does not keep the `talk` hold. */
  sentKind: OutMsg['kind'] | ''
  /** 3.28.1 T12: when the last message was delivered to the partner (0 none yet): its turn has ended once lastTurnAt passes it. */
  sentAt: number
  warm: Warm
  /** agentId -> a loop expert's warming (3.23 T5d2b); the partner's is `warm`. */
  warmAgents: Record<string, WarmAgent>
  hold: Hold | null
  planEdits: PlanEdit[]
  pending: Pending[]
  /** Journal entries the route tick still has to write, in order. */
  journal: JournalOut[]
  toasts: ToastOut[]
  /** Dialogs owed to the developer, oldest first; the head is taken when it is shown. */
  present: Present[]
  planFile: PlanFile | null
  /** 3.23 T13b: a loop stop's toast cause, owed to Claude's next reply (or taken by its `ask`); null none. */
  stopToast: ToastOut['cause'] | null
  /** 3.23 T13d.c10: the your-turn toast of Claude's latest reply; null none (or dropped). */
  yourTurn: YourTurn | null
  /** 3.23 T13b: presentPlan's learning ExitPlanMode is running (the `tool.check` hook allows it). */
  learning: boolean
  /** 3.23 T13d: the live feed's folder, project-relative (`.run/mod/<sid>/feed`, the sid the monitor started under);
   * kept across a `/clear`, so the writers keep using the folder the running monitor reads. */
  feed: { dir: string }
}

/** A model's prices as `pa_ledger.py prices --json` gave them: a table and the key to look up in it. */
export type PriceCache = { table: Record<string, unknown>; lookup: string }

// 3.23: the autopilot's project-level `.run/mod/loop.json` (hooks/loop.ts parses and writes it).
export type LoopStateName = 'off' | 'boundary' | 'running' | 'routing' | 'closing'
// 3.23 T13d.c11: `session`, the hold a session start pushes when the loop would act; only a release ends it.
export type HoldName = 'talk' | 'review' | 'question' | 'plan' | 'close' | 'window' | 'stopped' | 'generation' | 'session'

/** The task in flight: what `status.py set` was given, the spawned expert, and its contract once read. */
export type LoopTask = {
  id: string
  title: string
  agent: string
  coder: string
  effort: string
  attempt: number
  /** `task`, `handoff` or `relaunch` (status.py set --kind). */
  kind: string
  agentId: string
  startSha: string
  spawnedAt: number
  /** The expert's last turn.complete; 0 before the first. */
  lastCompleteAt: number
  /** The contract routed: the last `STATUS:` block at runEnded ('' none), null until then. */
  contract: string | null
  /** The expert's last `STATUS:` block so far (3.23 T5c.c3), interim until runEnded; never routed before. */
  pendingContract?: string | null
  /** The last `steer` sent to the expert (3.23 T5c); queued to it until a turn.complete after it. */
  steerAt?: number
  /** The router's last forward of a child's hand-back to the expert (3.23 T5d2a); queued to it until a turn.complete after it. */
  forwardAt?: number
  /** The expert's children were last seen busy (3.23 T5d2a.c2); `childIdleAt` is when they went idle (runEnded waits after it). */
  childBusy?: boolean
  childIdleAt?: number
  /** The last contract routed for this task (3.23 T6a2): a STATUS block textually identical to it is a repeat, ignored. */
  lastRouted?: string
  /** 3.24 T8: its expert was asked once for a missing `Review:` block; a second miss is a review hold from the summary. */
  reviewAsked?: boolean
  /** Recovery after a takeover (3.23 T5d1): `start` (to act), `sent` (`continue:` sent at `at`); absent when none. */
  recovery?: { phase: 'start' | 'sent'; at: number } | null
  /** A stopped agent sent `continue:` at `at` (3.23 T11-fix2), its status then (`killed`, `failed`, `absent`); absent when none. */
  stopped?: { at: number; status: string } | null
}

/** A picked task's WAIT-FOR being polled: the task, the file (project-relative), the literal, since when (ms). */
export type WaitFor = { task: string; file: string; literal: string; since: number }

/** A conversation `plan_edit` queued for the next boundary; `caller` gets the `applied:` or `refused:` line. */
export type QueuedEdit = { command: string; why: string; at: number; caller: string }

export type LoopState = {
  v: number
  ownerSid: string
  heartbeatAt: number
  state: LoopStateName
  /** The hold stack, last on top; `talk` always on top when present. */
  holds: HoldName[]
  task: LoopTask | null
  queuedEdits: QueuedEdit[]
  /** The phase-close chain's step while `closing`, '' otherwise. */
  chainStep: string
  /** Unused (3.23 T6a1): the open-dialog flag is the router's `.run/mod/<sid>/dialog.json` (one writer), read here. */
  dialog: boolean
  /** The WAIT-FOR the queue waits on (3.23 T5d2a: `<file> contains <literal>` of `task`, waiting since `since` ms); null none. */
  waitFor: WaitFor | null
  /** `held(window)` resumes at this ms; 0 none. */
  resumeAt: number
  /** The developer's last message (ms). */
  lastDevAt: number
  /** Claude's (the partner's) last answer (ms). */
  lastAnswerAt: number
  /** 3.28.1 T9b: Claude's last answer not after a mod notice (ms); silence counts from the later of this and `lastDevAt`. */
  talkAnswerAt: number
  /** Claude's last reply asked the developer something: silence does not end `talk`. */
  lastAnswerAsked: boolean
  /** The phase close (3.23 T6b): `release(close)` ran; the next boundary starts the close at NONE-done (3.25 T2: no agent). */
  closeRequested?: boolean
  /** 3.25 T2: `release(close)`'s note, Claude's phase report: the close writes it to RECAP.md, then drops it. */
  closeRecap?: string
  /** The close question went to Claude (once per plan end). */
  closeAsked?: boolean
  /** INBOX.md as last seen in `held(close)`: a change runs a boundary. */
  closeInbox?: string
  /** The closing phase's id, kept for the chain after the archive moves the plan. */
  chainPhase?: string
  /** archive's `RUN:` lines not yet run, and its `stage:` paths for the archived commit. */
  chainRuns?: string[]
  chainStage?: string[]
  /** The close started at the generation's end (its closed line is a stop). */
  chainGenEnd?: boolean
  /** 3.23 T13b: the open phase's finished tasks' contract SUMMARY lines, by task id; cleared at the close. */
  summaries?: Record<string, { title: string; summary: string }>
  /** 3.24 T10b: every session that owned the open phase, in order (the close's recalc reads them all); reset at the close. */
  phaseSids?: string[]
}

/** 3.28.1 T15: a background shell (Bash `run_in_background`) an agent started (`.run/mod/<sid>/shells.json`): Claude
 * Code's task id, its command's script or first word, its start (ms), the agent's type, and its exit code once it ended
 * badly (it stays listed, shown dead, until the agent reports). */
export type Shell = { id: string; name: string; at: number; type: string; exit?: number }

/** The watcher's facts of one agent (`.run/mod/<sid>/agents/<id>.json`, 3.22 T8f), extended in 3.23 T5c with its type,
 * spawn description, parent, latest approach note (≤ 2 lines), its last three steps (`Tool short-arg`, oldest first)
 * and when the file was last written. */
export type AgentFacts = {
  model: string
  effort: string
  ctx: number
  at: number
  type?: string
  description?: string
  parentId?: string
  note?: string
  steps?: string[]
  updatedAt?: number
  /** 3.23 T13d.c2: the last non-empty line of the latest feed segment (whitespace collapsed); the band's step. */
  line?: string
}

/** One row of the band above the prompt (3.23 T13): a running agent's role (its type's first word), name (its type),
 * task (`T<n>`, `T<n> · c<k>` for a coder), current step (`Tool short-arg`, the facts' last) and elapsed time (`4m`). */
export type BandRow = { role: string; name: string; task: string; step: string; elapsed: string }
/** The band (3.23 T13), written by the autopilot's tick: the loop's line ('' none) and one row per running agent. */
export type Band = { line: string; rows: BandRow[] }
/** 3.24 T13B: the router's own band part (its work tick): Claude's rows (and its live children's), the conversation-only
 * note, whether anything works, and the cycling dots' count (1-3) of `‹pa› working` when nothing else shows. */
export type Work = { rows: BandRow[]; note: string; working: boolean; dots: number }

/** One agent of the running task, for the running-now note (hooks/digest.ts). */
export type RunningAgent = AgentFacts & { id: string; role: 'expert' | 'coder'; /** `stopped` or `waiting` (T11-fix2); '' at work. */ state?: string }

declare module 'claude-code' {
  interface PluginState {
    pa: {
      /** The generation ticks are armed under; 0 after /clear until a re-arm. */
      gen: number
      active: boolean
      /** The working copy of .run/mod/<sid>/state.json. */
      state: RouterState | null
      /** The conversation route is on: active, and an interactive session or PA_MOD_SDK_AS_USER=1. */
      route: boolean
      /** PA_MOD_SDK_AS_USER=1: an `sdk` prompt (`-p`) counts as the developer's (trials). */
      sdkAsUser: boolean
      turns: TurnRegistry
      /** The card texts the router answered (3.23 T13), newest last: the AssistantMessage rule draws only these. */
      cards: string[]
      /** The band above the prompt (3.23 T13): the autopilot's tick writes it, the AbovePrompt rule draws it. */
      band: Band
      /** model -> its prices, or null when `pa_ledger.py prices --json` failed (cached for the session). */
      rates: Record<string, PriceCache | null>
      /** The fallback plan pane's Markdown (`pa-plan`), '' when none is shown. */
      pane: string
      /** 3.23 T13d: the live feed's records not yet drawn, the last drawn number and speaker (hooks/feed.ts). */
      feed: FeedPending
      /** 3.23 T13d.c1c: the feed's life: seen, the first developer prompt, the restarts, the Monitor call running. */
      feedLife: FeedLife
      /** 3.23 T13d.c2: the feed answers' runs, newest last (capped like the cards): the AssistantMessage rule draws these. */
      feedRuns: FeedDrawn[]
      /** 3.24 T8: the open permission dialogs (planview.ts dialogKey); an open one holds the picker. */
      dialogs: string[]
      /** 3.24 T13B: the router's band part (its work tick); the AbovePrompt rule draws it with `band`. */
      work: Work
    }
  }
}
