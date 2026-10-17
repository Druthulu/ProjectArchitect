// PA 3.2 router plugin `pa` (phase 3.22 T5a skeleton, T5b route): activation, the state file and its generation token,
// recovery after /clear or resume, the inbox drain, the debug log, the compaction veto, the prune of old session
// folders, and the conversation route (route.ts): local main-loop answers, prompt routing, the partner, hand-backs,
// replies as marker prompts, and the render rules that hide the route's rows. A .tsx module for the render rules' JSX.
// The engine allows `$` only as `$.noun.method(...)` at the call site (never stored, passed, spread or returned), so
// helpers are pure (activation.ts, state.ts, inbox.ts, debug.ts) and every `$` call is spelled inline in a hook or a
// closure inside one (design.md, "Execution model"). Hooks record and return; steps run from ticks.
// T8a: journal entries are queued in the state by the hooks and written by the route tick (which rewrites the month's
// index); a fresh partner is seeded from the journal; a warming tick pings the idle partner; the route tick raises the
// waiting toast.
// T8c: inbox `plan` and `ask` requests queue a dialog; the presentation tick shows one at a time when no agent runs and
// the prompt box holds no draft: a plan in the plan-mode view (path 0), else a Markdown pane and `$.ui.ask` (fallback);
// an ask in `$.ui.ask`. The answer goes to the caller through the outbox (planview.ts).
// 3.23 T6a1: a dialog opens only while the loop is `off` or held (loop.json, read only), with `.run/mod/<sid>/dialog.json`
// (this router its one writer) open meanwhile, so the loop's tick does nothing; the decision goes to the request's caller
// (a live agent, else the partner); an approved generation or phase plan becomes the loop's `install` request, and the
// answer to the loop's own ask (caller `loop`) its `answer` request. PA_MOD_TRIAL_APPROVE=1 answers without a dialog.
// 3.23 T13: the cards (AssistantMessage) and the band (AbovePrompt): the autopilot's tick writes `pa.band`, the rule draws it.
// 3.24 T6: an active arm writes `.run/mod/heartbeat.json`; an activation hook that throws writes `.run/mod/error.json`
// (pa/health.py reads both; an inert session writes neither).
// 3.24 T13B: every Claude reply is an orange Claude card; a reply with no hand-back goes through the feed while it lives
// (no "Prompt from the pa plugin" row), the marker prompt otherwise; the band gains Claude's rows, the conversation-only
// note and `‹pa› working` with cycling dots (the work tick).
import { atom, read, update } from 'claude-code'
import type { Elements, EngineInterface, Register, RenderElement } from 'claude-code'

import type { Band, BandRow, FeedDrawn, FeedEdit, FeedRun, Work } from '../types'

import { decide, ERROR_FILE, errorText, HEARTBEAT_FILE, heartbeatText, ledgerPath, paSetting, releaseOf, versionOf } from './activation'
import { armAutopilot, sessionStartPending } from './autopilot'
import {
  type BandAgent, bandCells, bandHex, bandIds, bandStepOf, bandView, borderOf, type Card, type CardStyle, cardStyleOf, claudeCard, claudeRows, coderIdOf, finishCardOf,
  loopNoteOf, noteCardOf, numstatOf, parseCard, ROLE_HEX, type Sender,
} from './card'
import { parseCoderReturn } from './contract'
import { clip, eventFile, isStale, PRUNE_DAYS, pruneArgv } from './debug'
import { forwardRequest, handle, isLoopKind, parseRequest, pendingRequests, requestFile } from './inbox'
import { dayFilePath, type Entry, indexAgrees, indexPath, JOURNAL_DIR, mergeIndex, parseEntry, parseEntryPath, splitDay, stamp, unindexed } from './journal'
import { idleNow, runningNow, taskAgents } from './digest'
import {
  addRecords, beatOf, deathOf, emptyFeed, emptyLife, FEED_BEAT_MS, FEED_DESC, FEED_STALE_MS, FEED_TIMEOUT_MS, feedBlock, feedCommand, feedErrorLine, feedEvents,
  announces, editHead, feedLook, type FeedRecord, feedText, fromLog, missingNs, NUDGE, nudgeName, replyRecord, restartGate, seenBeat, senderOf, shownOf, speakerOf, staleOf, unseenFeed,
} from './feed'
import { FIRST_RUN_WAITS, firstRunLine, LOOP_FILE, loopAgentIds, nextIdOf, ownsLoop, parseLoop, planPhase, projectName, queuedTo, RESTART_LINE, sessionTitle, SHELLS_FILE, shellsOf, shellsOut, statusWord, talkLine } from './loop'
import {
  answerLine, APPROVED, askBody, decisionLine, dialogCopy, DIALOG_FILE, dialogJson, FALLBACK_OPTIONS, FALLBACK_QUESTION, fallbackLine, installs, isReplan,
  dialogKey, dropAgentDialogs, holdingAgent, learnedFile, PLAN_PANE, planBody, planFilePath, planLogLines, presentHold, slugArgv, slugFile, trialLine,
} from './planview'
import { cacheTtlOf, expertStatus, isCold, partnerStatus, pingCount, pingDecision, pingUsd, type Rates, ratesFrom, tierRates } from './policy'
import {
  addAttach, addReply, applyReply, childOut, classify, clearTurn, dueMarkers, emptyTurns, enqueue, entryUsage, fileEntry,
  framedOther, handbackChild, hideAssistant, hideSpinner, hideUser, isBusy, isLive, isSeedName, journalOut, localAnswer, markerReply,
  markerText, newestPartner, newPartner, otherTarget, PARTNER_TYPE, stepFactsOf, nextMarker, outboxHeld, parentOf, partnerReport, partnerRoute, partnerTurnEnded, priceCacheOf,
  registerTurn, replyKey, RESUME_CHECK_MS, seedEntries, takeHandback, takeReply, toastArgv, toastOut, usageCtx,
  handbackReport, isNote, noteBody, isFeedText, armYourTurn, yourTurnStep, loopPending, STUB, dueToast, heardDeveloper,
  isPointer, lateReport, markerFacts, workingIds, noReportHandback, NO_REPORT_REASON, imageSourceLines, imagesPending,
  withImageSources,
} from './route'
import { buildSeed, type Child as SeedChild, localStamp, SEED_BUDGET_TOKENS } from './seed'
import {
  defaultState, drawnPath, feedDirOf, lastJson, lastPath, markDone, mergeDrawn, parseDrawn, parseLast, parseState, pruneDone, serializeDrawn,
  serializeState, statePath,
} from './state'
import type { Drawn, Present, PriceCache, RouterState, ToastOut, WarmAgent } from './state'

const genAtom = atom({ plugin: 'pa', key: 'gen' } as const, 0)
const activeAtom = atom({ plugin: 'pa', key: 'active' } as const, false)
const stateAtom = atom({ plugin: 'pa', key: 'state' } as const, null)
const routeAtom = atom({ plugin: 'pa', key: 'route' } as const, false)
const sdkAtom = atom({ plugin: 'pa', key: 'sdkAsUser' } as const, false)
const turnsAtom = atom({ plugin: 'pa', key: 'turns' } as const, emptyTurns())
const ratesAtom = atom({ plugin: 'pa', key: 'rates' } as const, {} as Record<string, PriceCache | null>)
const paneAtom = atom({ plugin: 'pa', key: 'pane' } as const, '')
const cardsAtom = atom({ plugin: 'pa', key: 'cards' } as const, [] as string[])
const bandAtom = atom({ plugin: 'pa', key: 'band' } as const, { line: '', rows: [] } as Band)
// 3.24 T13B: the router's own band part, written by its work tick (Claude's rows, the conversation-only note, the dots).
const workAtom = atom({ plugin: 'pa', key: 'work' } as const, { rows: [], note: '', working: false, dots: 1 } as Work)
// 3.23 T13d: the feed's records collected at prompt.submit and not yet drawn, the last drawn number and speaker.
const feedAtom = atom({ plugin: 'pa', key: 'feed' } as const, emptyFeed())
// 3.23 T13d.c1c: the feed's life (a feed seen, the first developer prompt, the restarts, the router's Monitor call running).
const lifeAtom = atom({ plugin: 'pa', key: 'feedLife' } as const, emptyLife())
// 3.23 T13d.c2: the feed answers' stored texts and runs (capped by MAX_CARDS): the AssistantMessage rule draws these.
const feedRunsAtom = atom({ plugin: 'pa', key: 'feedRuns' } as const, [] as FeedDrawn[])
// 3.24 T8 (L18): the open permission dialogs (planview.ts dialogKey), from classic.PermissionRequest to that call's
// PostToolUse, PostToolUseFailure or PermissionDenied (or its agent's turn end); an open one holds every dialog of ours.
const dialogsAtom = atom({ plugin: 'pa', key: 'dialogs' } as const, [] as string[])

export const COMPACT_SKIP = 'PA 3.2: the journal replaces compaction'
const DRAIN_MS = 1_000
/** 3.24 T13B: the router's band part's tick (its dots cycle at this rate while `‹pa› working` shows). */
const WORK_MS = 500
const WARM_MS = 30_000 // was 60_000; 3.28.1 T12: a 5-minute agent's ping window (3.28.8: 4-4.5 min) is 30 s wide
const SEED_DAYS = 14
/** Card texts kept in the registry at most (3.23 T13); an older card's row draws as its plain markdown. */
const MAX_CARDS = 100

// Module variables are lost on a hot reload or a lost hooks worker (design.md); everything here is re-derivable.
let debugBase = '' // `<root>/.run/mod` under PA_MOD_DEBUG=1, else '' (no debug log)
let debugSid = 'unknown'
let counter = 0
let rendered = new Set<string>()
/** 3.23 T13d.c11, debug only: each main-loop answer's text (trimmed) → its kind and wall time, for `pa.draw`'s delay. */
const answered = new Map<string, { kind: DrawKind; ms: number }>()
let armed = { sid: '', gen: 0 }
let draining = false
let routing = false
let warming = false
let jseq = 0 // per-load sequence of the router's journal entries
let fseq = 0 // the loop `forward` requests' sequence in the inbox (3.23 T5d2a)
let nseq = 0 // the feed's nudge files' sequence (3.23 T13d)
let expertSkip = '' // 3.23 T13b.c4: warmExpert's last skip reason, logged once per change
let noToastLogged = false
/** 3.28.8: the developer's latest message with attachments (when, how many) and Claude Code's source lines for them. */
let imageWait: { at: number; count: number; lines: string[] } | null = null
let presenting = false // the presentation tick's one dialog at a time
let lastHold = '' // the last deferral logged, so a long wait logs once
let turnWaitLogged = '' // 3.24 T17j: the your-turn toast's last loop wait logged (turnId + why), once per change
let modeSeen = '' // the permission mode the last classic.PostToolUse carried ('' unread)
// T8e: this session's transcript (classic.SessionStart's `transcript_path`): its slug names the main plan file.
let transcriptSeen = { sid: '', path: '' }
// The arming closure session.start builds; the re-arm hooks call it. Holds no `$` of its own: it is a closure inside
// the session.start hook, so its `$` calls stay inline there.
let rearm: ((why: string, sid: string) => Promise<void>) | null = null
let feedRestart: ((why: string) => Promise<void>) | null = null // 3.23 T13d.c1c: set by arm, run by a death signal

/** 3.23 T13d.c6a: drawn.json rewritten with an entry added (read, change, write: the file keeps every session's entries). */
async function keepDrawn($: Pick<EngineInterface, 'fs' | 'session'>, change: (d: Drawn) => Drawn): Promise<void> {
  const path = drawnPath(`${await $.session.root()}/.run/mod`)
  const d = parseDrawn(await $.fs.read(path).catch(() => null))
  await $.fs.write(path, serializeDrawn(change(d))).catch(quiet)
}

/** 3.24 T6: the error record pa/health.py reads, for an activation hook that threw (`sid` '' for this session's); only
 * where the mod would be active (pa.json mod: on), so an inert session still writes nothing. Never throws. */
async function errorRecord($: Pick<EngineInterface, 'fs' | 'session' | 'env' | 'clock'>, sid: string, hook: string, error: string): Promise<void> {
  try {
    const root = await $.session.root()
    const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
    if (decide(paText, await $.env.get('PA_MOD_OFF'), versionOf(await $.session.version())).active) {
      await $.fs.write(`${root}/${ERROR_FILE}`, errorText(sid === '' ? await $.session.id() : sid, 'pa', hook, error, await $.clock.now()))
    }
  } catch {
    // best effort: the record must never fail the handler that writes it
  }
}

/** One debug event file: [path, text] for `$.fs.write`. */
function ev(kind: string, data: Record<string, unknown>): [string, string] {
  const ms = Date.now()
  counter += 1
  return [eventFile(`${debugBase}/${debugSid}/debug`, ms, counter, `pa.${kind}`), JSON.stringify({ ms, ev: `pa.${kind}`, ...data }, null, 1)]
}

const quiet = (): undefined => undefined

/** 3.24 T8: under PA_MOD_DEBUG a dialog's open or close logs `$.agent.list()`'s statuses: the live record of which
 * status a subagent shows during its own permission dialog. */
async function dialogLog($: EngineInterface, kind: string, key: string, open: number): Promise<void> {
  if (debugBase !== '') {
    const agents = (await $.agent.list().catch(() => [])).map(a => ({ id: a.id, type: a.type, status: a.status }))
    await $.fs.write(...ev(kind, { key: key.slice(0, 200), open, agents })).catch(quiet)
  }
}

/** 3.24 T8: one open permission dialog closes (`why`: done, failure, denied); a key not open is a no-op. */
async function dialogClose($: EngineInterface, key: string, why: string): Promise<void> {
  if (!(await read($, activeAtom)) || !(await read($, dialogsAtom)).includes(key)) {
    return
  }
  const left = await update($, dialogsAtom, ks => ks.filter(k => k !== key))
  await dialogLog($, `dialog.close.${why}`, key, left.length)
}

/** What a main-loop answer or a drawn assistant row is, for the draw log (3.23 T13d.c11). */
type DrawKind = 'feed' | 'card' | 'reply' | 'other'
const drawKindOf = (kind: string): DrawKind =>
  kind === 'feed' || kind === 'card' ? kind : kind === 'marker' || kind === 'reply' ? 'reply' : 'other'

/** A card (3.23 T13, card.ts) in its role's color under a `card_style`: a Box border (`quote` a side bar, `round`) with a
 * bold title, the Markdown body and the diff as Code; plain draws no border, `● ` and the title, the rest indented 2. */
function drawCard(el: Pick<Elements['terminal'], 'Box' | 'Text' | 'Markdown' | 'Code'>, card: Card, style: CardStyle): RenderElement {
  const { Box, Text, Markdown, Code } = el
  const hex = ROLE_HEX[card.role]
  const border = borderOf(style, card.done)
  // 3.28.2 T16: Claude's installed-update card draws the mod's restart line (loop.ts RESTART_LINE) bold green with
  // "(not /clear)" red; the stored text stays markdown, so Remote Control shows it as is.
  const at = card.role === 'claude' ? card.body.indexOf(RESTART_LINE) : -1
  const body = at < 0
    ? (card.body === '' ? null : <Markdown text={card.body} />)
    : [
        card.body.slice(0, at).trim() === '' ? null : <Markdown text={card.body.slice(0, at).trimEnd()} />,
        card.body.slice(0, at).trim() === '' ? null : <Text> </Text>, // 3.28.2 T17: the empty line before it, always
        <Text><Text bold color="#00af00">To load it: /exit, then claude --continue</Text> <Text color="#d70000">(not /clear)</Text></Text>,
        card.body.slice(at + RESTART_LINE.length).trim() === '' ? null : <Markdown text={card.body.slice(at + RESTART_LINE.length).trimStart()} />,
      ]
  const rest = (
    <Box flexDirection="column" paddingLeft={border === null ? 2 : 0}>
      {body}
      {card.diff === '' ? null : <Code source={card.diff} format="diff" />}
    </Box>
  )
  if (border === null) {
    return (
      <Box flexDirection="column">
        <Text bold color={hex}>{`● ${card.title}`}</Text>
        {rest}
      </Box>
    )
  }
  return (
    <Box flexDirection="column" borderStyle={border} borderColor={hex} paddingLeft={1} paddingRight={1}>
      <Text bold color={hex}>{card.title}</Text>
      {rest}
    </Box>
  )
}

/** 3.24 T8b: a file edit in native Claude Code's look (docs/pa3-build/3.2/trial/look/native-edit-view.png): the bold
 * `Updated <path>` (or `Created <path>`) and its counts, then each hunk as a numbered diff (a created file: its first lines,
 * numbered from 1) with a dim `...` between hunks, then a dim `… +N lines` when rows were left out. */
function drawEdit(el: Pick<Elements['terminal'], 'Box' | 'Text' | 'Code'>, edit: FeedEdit): RenderElement[] {
  const { Text, Code } = el
  const head = editHead(edit) // not `h`: the JSX factory
  const out: RenderElement[] = [<Text color="text"><Text bold>{head.verb}</Text>{head.counts}</Text>]
  edit.hunks.forEach((hunk, i) => {
    if (i > 0) {
      out.push(<Text dimColor>...</Text>)
    }
    out.push(edit.created ? <Code source={hunk} path={edit.path} startLine={1} /> : <Code source={hunk} path={edit.path} format="diff" />)
  })
  if (edit.more > 0) {
    out.push(<Text dimColor>{`… +${edit.more} lines`}</Text>)
  }
  return out
}

/** A feed answer (3.23 T13d.c2, feed.ts feedLook) in the cards' look: per run a note card's border in the role's hex
 * (plain: none, the lines indented 2), the bold label in the role's hex on a titled run only, each line as Text in the
 * theme's primary text color (3.23 T13d.c7: `color="text"`, white on dark themes, readable on light ones).
 * 3.24 T13r: a run's lines are one `Markdown` block, drawn as an assistant reply is (was a `Text` per line: raw markers). */
function drawFeed(el: Pick<Elements['terminal'], 'Box' | 'Text' | 'Markdown' | 'Code'>, runs: readonly FeedRun[], style: CardStyle): RenderElement {
  const { Box, Text, Markdown } = el
  return (
    <Box flexDirection="column">
      {runs.map(run => {
        const look = feedLook(run, style)
        const body = (
          <Box flexDirection="column" paddingLeft={look.border === null ? 2 : 0}>
            {look.edit === undefined ? <Markdown text={look.lines.join('\n')} /> : drawEdit(el, look.edit)}
          </Box>
        )
        const title = look.title === '' ? null : <Text bold color={look.color}>{look.title}</Text>
        if (look.border === null) {
          return (
            <Box flexDirection="column">
              {title}
              {body}
            </Box>
          )
        }
        return (
          <Box flexDirection="column" borderStyle={look.border} borderColor={look.color} paddingLeft={1}>
            {title}
            {body}
          </Box>
        )
      })}
    </Box>
  )
}

/** The band (3.23 T13): the loop's line, then a row per running agent (the name bold in its role's hex, the task plain,
 * the step and the time dim), each one row cut to `cols`, never wrapped. 3.24 T13B: its lines (bandView). */
function drawBand(el: Pick<Elements['terminal'], 'Box' | 'Text'>, band: { lines: string[]; rows: BandRow[] }, cols: number): RenderElement {
  const { Box, Text } = el
  return (
    <Box flexDirection="column">
      {band.lines.map(line => <Text wrap="truncate-end">{line.length > cols ? `${line.slice(0, Math.max(0, cols - 1))}…` : line}</Text>)}
      {band.rows.map(r => {
        const c = bandCells(r, cols)
        return (
          <Box flexDirection="row">
            <Text bold color={bandHex(r.role)} wrap="truncate-end">{c.name}</Text>
            <Text wrap="truncate-end">{c.task}</Text>
            <Text dimColor wrap="truncate-end">{c.dim}</Text>
          </Box>
        )
      })}
    </Box>
  )
}

export const register: Register = (on, options) => {
  // 3.23 T13: the `/config` row pa.card_style: how the cards draw (mixed, boxed, bar, plain).
  const cardStyle = cardStyleOf(options)
  on('session.start', async ($, e, next) => {
    const root = await $.session.root()
    const sid = await $.session.id()
    const base = `${root}/.run/mod`
    // 3.24 T8: the debug log opens only once arm() finds the mod active, so an inert session writes nothing.
    const debugWanted = (await $.env.get('PA_MOD_DEBUG')) === '1'
    debugBase = ''
    debugSid = sid
    // The route runs only in an interactive session, or under the trial flag that makes a `-p` prompt the developer's.
    const interactive = e.isInteractive
    const sdkAsUser = (await $.env.get('PA_MOD_SDK_AS_USER')) === '1'

    // Decide, restore the state under a new generation, save it, and start this generation's ticks. `why` is
    // `start` (session.start: the session's own state file, if a resume left one) or a re-arm (also through last.json).
    const arm = async (why: string, sidNow: string): Promise<void> => {
      if (why !== 'start' && armed.sid === sidNow && armed.gen !== 0 && (await read($, genAtom)) === armed.gen) {
        return // already armed for this session and $.state was not reset: never a second set of ticks
      }
      debugSid = sidNow
      const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
      const version = versionOf(await $.session.version())
      const decision = decide(paText, await $.env.get('PA_MOD_OFF'), version)
      await update($, activeAtom, () => decision.active)
      const routeOn = decision.active && (interactive || sdkAsUser)
      await update($, routeAtom, () => routeOn)
      await update($, sdkAtom, () => sdkAsUser)
      debugBase = debugWanted && decision.active ? base : ''
      if (!decision.active) {
        return
      }
      // 3.24 T6: the heartbeat pa/health.py checks: this session's mod loaded (the release from the plugin's root, two up).
      const release = releaseOf(await $.fs.read(`${$.plugin.root}/../../VERSION`).catch(() => null))
      await $.fs.write(`${root}/${HEARTBEAT_FILE}`, heartbeatText(sidNow, release, version, await $.clock.now(), 'pa')).catch(quiet)
      if (debugBase !== '') {
        await $.fs.write(...ev('activation', { why, sid: sidNow, version, decision, route: routeOn, interactive, sdkAsUser })).catch(quiet)
      }
      // 3.23 T13d.c6a: the drawn registries come back from drawn.json (a resumed or restarted process starts without them).
      const kept = parseDrawn(await $.fs.read(drawnPath(base)).catch(() => null))
      await update($, cardsAtom, cs => mergeDrawn(kept.cards, cs, c => c, MAX_CARDS))
      await update($, feedRunsAtom, xs => mergeDrawn(kept.feedRuns, xs, x => x.text, MAX_CARDS))
      // 3.23 T13d.c6c: what the load brought back, so a resumed session's reload can be checked.
      if (debugBase !== '') {
        const reason = why === 'start' ? 'start' : why.includes('resume') ? 'resume' : 'clear'
        await $.fs.write(...ev('drawn.load', { reason, cards: kept.cards.length, feedTexts: kept.feedRuns.length })).catch(quiet)
      }
      const now = await $.clock.now()
      const lastSid = parseLast(await $.fs.read(lastPath(base)).catch(() => null))
      let prev: RouterState | null = null
      let from = ''
      const own = await $.fs.read(statePath(base, sidNow)).catch(() => null)
      if (own !== null) {
        prev = parseState(own, sidNow, now)
        from = sidNow
      }
      if (prev === null && why !== 'start' && lastSid !== null && lastSid !== sidNow) {
        const theirs = await $.fs.read(statePath(base, lastSid)).catch(() => null)
        prev = theirs === null ? null : parseState(theirs, lastSid, now)
        from = prev === null ? '' : lastSid
      }
      const gen = Math.max(await read($, genAtom), armed.gen, prev?.gen ?? 0) + 1
      const s: RouterState = { ...(prev ?? defaultState(sidNow, gen)), sid: sidNow, gen }
      await update($, stateAtom, () => s)
      await update($, genAtom, () => gen)
      await $.fs.write(statePath(base, sidNow), serializeState(s))
      await $.fs.write(lastPath(base), lastJson(sidNow, now))
      if (armed.sid !== sidNow) {
        rendered = new Set<string>()
      }
      armed = { sid: sidNow, gen }
      if (debugBase !== '') {
        await $.fs.write(...ev('gen', { why, gen, from })).catch(quiet)
      }
      const log = async (kind: string, data: Record<string, unknown>): Promise<void> => {
        if (debugBase !== '') {
          await $.fs.write(...ev(kind, data)).catch(quiet)
        }
      }

      // The journal (journal.ts): entries under `<root>/.claude-state/journal/<date>/`, the month's index beside them.
      const python = paSetting(paText, 'python')
      const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
      const ledger = ledgerPath(await $.env.get('PA_MOD_PA3_DIR'), home)
      const rotateAt = Number((await $.env.get('PA_MOD_ROTATE_AT')) ?? '') || 0
      // Trial knob (3.23 T5d2b): a ping falls due this long after the last request instead of policy.PING_AFTER_MS.
      const pingAfterMs = Number((await $.env.get('PA_MOD_PING_AFTER_MS')) ?? '') || undefined
      const jdir = `${root}/${JOURNAL_DIR}`
      // Both forms (3.23 T7): a live day folder `<date>/` and a rolled day file `<date>.md` (tools/journal_rollup.py).
      const days = async (): Promise<string[]> => {
        if (!(await $.fs.exists(jdir))) {
          return []
        }
        const names = (await $.fs.list(jdir)).map(f => f.name.replace(/\.md$/, '')).filter(n => /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(n))
        return [...new Set(names)].sort()
      }
      const rolled = async (day: string): Promise<Entry[]> => {
        const text = await $.fs.read(`${root}/${dayFilePath(day)}`).catch(() => null)
        return text === null ? [] : splitDay(day, text)
      }
      const dayFiles = async (day: string): Promise<string[]> => {
        const live = (await $.fs.exists(`${jdir}/${day}`))
          ? (await $.fs.list(`${jdir}/${day}`)).filter(f => f.kind === 'file').map(f => `${JOURNAL_DIR}/${day}/${f.name}`)
          : []
        return [...live, ...(await rolled(day)).map(e => e.path)].filter(rel => parseEntryPath(rel) !== null).sort()
      }
      const readEntry = async (rel: string): Promise<Entry | null> => {
        const hash = rel.indexOf('.md#')
        if (hash >= 0) {
          return (await rolled(parseEntryPath(rel)?.date ?? '')).find(e => e.path === rel) ?? null
        }
        const text = await $.fs.read(`${root}/${rel}`).catch(() => null)
        return text === null ? null : { path: rel, ...(parseEntry(text) ?? { meta: {}, body: text }) }
      }
      // The month's index, rewritten whole when it disagrees with the month's entries (its lines kept, new entries read).
      const syncIndex = async (month: string): Promise<void> => {
        const paths: string[] = []
        for (const d of (await days()).filter(n => n.startsWith(`${month}-`))) {
          paths.push(...(await dayFiles(d)))
        }
        const ip = `${root}/${indexPath(month)}`
        const text = (await $.fs.read(ip).catch(() => null)) ?? ''
        if ((paths.length === 0 && text === '') || indexAgrees(text, paths)) {
          return
        }
        const fresh: Entry[] = []
        for (const rel of unindexed(text, paths)) {
          const e = await readEntry(rel)
          if (e !== null) {
            fresh.push(e)
          }
        }
        await $.fs.write(ip, mergeIndex(text, paths, fresh))
        await log('journal.index', { month, entries: paths.length, read: fresh.length })
      }
      $.clock.after(0, () => {
        void (async () => {
          await syncIndex(stamp(await $.clock.now())[0].slice(0, 7))
        })().catch(async err => log('journal.error', { err: clip(String(err), 300) }))
      })

      if (why === 'start') {
        const argv = pruneArgv(paSetting(paText, 'python'), base, [sidNow, lastSid ?? ''], PRUNE_DAYS)
        if (argv !== null) {
          $.clock.after(0, () => {
            void (async () => {
              try {
                const ran = await $.process.run(argv, { cwd: root, timeoutMs: 15_000 })
                if (debugBase !== '') {
                  await $.fs.write(...ev('prune', { exitCode: ran.exitCode, stdout: clip(ran.stdout, 120), stderr: clip(ran.stderr, 300) })).catch(quiet)
                }
              } catch (err) {
                if (debugBase !== '') {
                  await $.fs.write(...ev('prune.error', { err: clip(String(err), 300) })).catch(quiet)
                }
              }
              // The journal's daily roll-up (3.23 T7): finished days only, so safe in any session; then the month's index.
              try {
                const ran = await $.process.run([python, 'tools/journal_rollup.py'], { cwd: root, timeoutMs: 60_000 })
                await log('rollup', { exitCode: ran.exitCode, stdout: clip(ran.stdout, 600), stderr: clip(ran.stderr, 300) })
                await syncIndex(stamp(await $.clock.now())[0].slice(0, 7))
              } catch (err) {
                await log('rollup.error', { err: clip(String(err), 300) })
              }
            })()
          })
        }
      }

      // The inbox drain: this generation's one tick. Stale (a /clear reset $.state, or a re-arm moved on): it stops.
      const inbox = `${base}/${sidNow}/inbox`
      const timer = $.clock.every(DRAIN_MS, () => {
        void (async () => {
          const current = await read($, genAtom)
          if (isStale(gen, current)) {
            timer.cancel()
            if (debugBase !== '') {
              await $.fs.write(...ev('gen.stale', { tickGen: gen, current })).catch(quiet)
            }
            return
          }
          if (draining) {
            return
          }
          draining = true
          try {
            if (!(await $.fs.exists(inbox))) {
              return
            }
            const names = (await $.fs.list(inbox)).filter(f => f.kind === 'file').map(f => f.name)
            const at = await $.clock.now()
            const st = await read($, stateAtom)
            if (st === null) {
              return
            }
            // The loop's own kinds (`release`, `plan_edit`) are drained by its tick (autopilot.tsx), never here.
            for (const name of pendingRequests(names, st.inboxDone, at).filter(n => !isLoopKind(n))) {
              if (isStale(gen, await read($, genAtom))) {
                return
              }
              const text = await $.fs.read(`${inbox}/${name}`).catch(() => '')
              const req = parseRequest(text, name)
              // Done before acting: a crash mid-act never replays a request. A functional update: the route's hooks
              // change the state between ticks.
              const id = name.slice(0, -'.json'.length)
              const saved = await update($, stateAtom, s => (s === null ? s : markDone(pruneDone(s, at), id, at)))
              if (saved === null) {
                return
              }
              await $.fs.write(statePath(base, sidNow), serializeState(saved))
              await $.fs.write(lastPath(base), lastJson(sidNow, at))
              if (req === null) {
                if (debugBase !== '') {
                  await $.fs.write(...ev('inbox.bad', { name, text: clip(text, 120) })).catch(quiet)
                }
                continue
              }
              for (const fx of handle(req)) {
                if (fx.do === 'log' && debugBase !== '') {
                  await $.fs.write(...ev(fx.kind, fx.data)).catch(quiet)
                } else if (fx.do === 'present' && isReplan(fx.item, await $.fs.exists(`${root}/phase-ends/current/REPLAN.md`),
                  (await $.fs.read(`${root}/phase-ends/current/PHASE_PLAN.md`).catch(() => null)) ?? '')) {
                  // 3.28.1 T6: a replan's draft goes straight to the loop, which installs it at the next task boundary.
                  const item = fx.item
                  fseq += 1
                  const r = requestFile(at, fseq, 'install', item.caller, {
                    kind: 'phase', path: item.path, title: item.title, rules: item.rules ?? [], candidates: item.candidates ?? [],
                  })
                  await $.fs.write(`${inbox}/${r.name}`, r.body)
                  await log('present.replan', { id, caller: item.caller })
                } else if (fx.do === 'present') {
                  // A dialog queued for the presentation tick (once per request id).
                  const item = fx.item
                  const after = await update($, stateAtom, s =>
                    s === null || s.present.some(p => p.id === item.id) ? s : { ...s, present: [...s.present, item] })
                  if (after !== null) {
                    await $.fs.write(statePath(base, sidNow), serializeState(after))
                  }
                  await log('present.queued', { id, kind: item.kind, caller: item.caller })
                } else if (fx.do === 'notice') {
                  // A loop notice (3.23 T5b): a framed `other` message, filed like any other prompt; `child` names its
                  // addressee (a caller) or ''.
                  const f = fx
                  jseq += 1
                  const seq = jseq
                  const after = await update($, stateAtom, s => (s === null ? s : fileEntry(
                    enqueue(s, 'other', framedOther(req.caller || 'pa', f.text), f.to, at),
                    journalOut(at, seq, 'mod', 'prompt', { session: s.sid, role: req.caller || 'pa' }, f.text))))
                  if (after !== null) {
                    await $.fs.write(statePath(base, sidNow), serializeState(after))
                  }
                  await log('inbox.notice', { id, to: f.to, text: clip(f.text, 200) })
                } else if (fx.do === 'topic' || fx.do === 'toast' || fx.do === 'stop') {
                  // The topic flag on the calling partner (rotate at the next message); an open ask's toast; T13b: a
                  // loop stop's toast cause, owed to Claude's next reply (null: Claude's ask took its place). T13b.c2: a
                  // loop ask while a stop's toast is owed adds none (its picker waits out Claude's turn; that toast covers it).
                  const f = fx
                  const after = await update($, stateAtom, s => {
                    if (s === null) {
                      return s
                    }
                    if (f.do === 'stop') {
                      return { ...s, stopToast: f.cause }
                    }
                    if (f.do === 'toast') {
                      // 3.24 T13E: a loop toast (the generation end's) drops an armed your-turn toast: one toast, not two.
                      const t = req.caller === 'loop' ? { ...s, yourTurn: null } : s
                      return req.caller === 'loop' && s.stopToast !== null ? t : { ...t, toasts: [...t.toasts, toastOut(f.cause, f.body, at)] }
                    }
                    const p = s.partner
                    return p === null || (f.agentId !== '' && f.agentId !== p.agentId) ? s : { ...s, partner: { ...p, topic: true } }
                  })
                  if (after !== null) {
                    await $.fs.write(statePath(base, sidNow), serializeState(after))
                  }
                  await log(`inbox.${fx.do}`, { id, agentId: f.do === 'topic' ? f.agentId : '', ...(f.do === 'stop' ? { cause: f.cause } : {}) })
                } else if (fx.do === 'card') {
                  // 3.24 T13E: a card of the loop's (the review release's accepted card): registered so the AssistantMessage
                  // rule draws it, journaled, and queued as a reply marker (the route tick sends it as a feed reply
                  // record or the marker prompt; its local turn answers the card as it is). Zero tokens.
                  const c = fx.text
                  await update($, cardsAtom, cs => (cs.includes(c) ? cs : [...cs, c].slice(-MAX_CARDS)))
                  await keepDrawn($, d => ({ ...d, cards: mergeDrawn(d.cards, [c], x => x, MAX_CARDS) }))
                  jseq += 1
                  const seq = jseq
                  const n = { v: 0 }
                  const after = await update($, stateAtom, s => {
                    if (s === null) {
                      return s
                    }
                    const out = journalOut(at, seq, 'mod', 'card', { session: s.sid }, c)
                    n.v = nextMarker(s)
                    return { ...fileEntry(s, out), markers: { ...s.markers, [String(n.v)]: `${root}/${out.rel}` }, pendingMarkers: [...s.pendingMarkers, n.v] }
                  })
                  if (after !== null) {
                    await $.fs.write(statePath(base, sidNow), serializeState(after))
                  }
                  await log('inbox.card', { id, n: n.v, text: clip(c, 120) })
                }
              }
            }
          } catch (err) {
            if (debugBase !== '') {
              await $.fs.write(...ev('inbox.error', { err: clip(String(err), 300) })).catch(quiet)
            }
          } finally {
            draining = false
          }
        })()
      })

      // 3.28.1 T12: the partner's own turn has ended (a turn end after the last message delivered to it); its idle
      // stretch still has warming pings; it is cold (policy.isCold), idle and not working on a message.
      const partnerIdle = (x: RouterState): boolean =>
        x.partner !== null && x.partner.lastTurnAt > 0 && x.partner.lastTurnAt >= x.sentAt && !x.partner.pingInFlight
      const partnerPingsLeft = (x: RouterState): boolean =>
        x.warm.waitPings < (x.warm.waitCount > 0 ? x.warm.waitCount : pingCount(x.partner?.ctx ?? 0, 'claude'))
      const partnerCold = (x: RouterState, list: readonly { id: string; status: string }[], now: number): boolean => {
        const p = x.partner
        if (p === null || !partnerIdle(x)) {
          return false
        }
        const status = partnerStatus(list.find(a => a.id === p.agentId)?.status, p.lastTurnAt, true)
        return (status === 'idle' || status === 'completed') &&
          isCold(Math.max(p.lastTurnAt, p.spawnedAt, x.warm.lastPingAt), now, '1h', partnerPingsLeft(x), pingAfterMs)
      }
      // The loop's expert `id`, cold (autopilot's coldOf): nothing is sent to it; the loop's `forward` request with the
      // text has the autopilot start a fresh expert with PROGRESS and the text.
      const loopExpertCold = async (x: RouterState, id: string, now: number): Promise<boolean> => {
        const t = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null)).task
        if (t === null || t.agentId !== id || id === '' || t.lastCompleteAt <= 0) {
          return false
        }
        const w = x.warmAgents[id]
        const ttl = cacheTtlOf(await $.fs.read(`${root}/.claude/agents/${t.agent}.md`).catch(() => null))
        const pingsLeft = w === undefined || w.waitCount === 0 || w.waitPings < w.waitCount
        return isCold(Math.max(t.lastCompleteAt, t.spawnedAt, w?.lastPingAt ?? 0), now, ttl, pingsLeft)
      }
      const toColdExpert = async (id: string, child: string, text: string): Promise<void> => {
        fseq += 1
        const now = await $.clock.now()
        const r = requestFile(now, fseq, 'forward', 'router', { agentId: id, child, text })
        await $.fs.write(`${base}/${sidNow}/inbox/${r.name}`, r.body).catch(quiet)
        await log('route.cold', { agentId: id, child })
      }
      // The route tick: the journal's queued entries, the partner's resume check, the outbox, then the reply markers
      // and the waiting toasts, each transition saved. Steps run here: `$.prompt.submit` threw "no session is bound"
      // from a timer made inside an agent's dispatch.
      const stPath = statePath(base, sidNow)
      const routeTimer = $.clock.every(DRAIN_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            routeTimer.cancel()
            return
          }
          if (routing || !(await read($, routeAtom))) {
            return
          }
          routing = true
          const step = async (f: (x: RouterState) => RouterState): Promise<RouterState | null> => {
            const after = await update($, stateAtom, x => (x === null ? x : f(x)))
            if (after !== null) {
              await $.fs.write(stPath, serializeState(after))
            }
            return after
          }
          // Each queued entry written, then dropped from the queue (a rewrite is the same file), then the month's index.
          const flushJournal = async (): Promise<void> => {
            const months = new Set<string>()
            let cur = await read($, stateAtom)
            while (cur !== null && cur.journal.length > 0) {
              const out = cur.journal[0]
              if (out === undefined) {
                break
              }
              await $.fs.write(`${root}/${out.rel}`, out.text)
              months.add(parseEntryPath(out.rel)?.date.slice(0, 7) ?? '')
              cur = await step(x => ({ ...x, journal: x.journal.filter(j => j.rel !== out.rel) }))
            }
            for (const m of months) {
              if (m !== '') {
                await syncIndex(m)
              }
            }
          }
          // A fresh partner's seed (seed.ts): the last 14 days' conversation, the running work, the plans.
          const seedFor = async (sending: string): Promise<string> => {
            const now = await $.clock.now()
            const since = stamp(now - SEED_DAYS * 86_400_000)[0]
            const entries: Entry[] = []
            for (const d of (await days()).filter(n => n >= since)) {
              const day = await rolled(d)
              entries.push(...day.filter(e => isSeedName(e.path)))
              for (const rel of (await dayFiles(d)).filter(r => isSeedName(r) && !r.includes('.md#'))) {
                const e = await readEntry(rel)
                if (e !== null) {
                  entries.push(e)
                }
              }
            }
            const cur = await read($, stateAtom)
            const old = cur?.partner?.agentId ?? ''
            const children: SeedChild[] = (await $.agent.list())
              .filter(a => old !== '' && a.parentId === old && isBusy(a.status))
              .map(a => ({ who: a.type, agentId: a.id, status: a.status }))
            const planPaths: string[] = []
            for (const rel of ['GENERATION_PLAN.md', 'phase-ends/current/PHASE_PLAN.md']) {
              if (await $.fs.exists(`${root}/${rel}`)) {
                planPaths.push(rel)
              }
            }
            if (await $.fs.exists(`${root}/.run/plans`)) {
              for (const f of await $.fs.list(`${root}/.run/plans`)) {
                if (f.kind === 'file' && f.name.endsWith('.md')) {
                  planPaths.push(`.run/plans/${f.name}`)
                }
              }
            }
            // 3.23 T7: the loop (loop.json, read only; its phase from the plan's first line) and the memory index, when present.
            const loopText = await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null)
            const planText = await $.fs.read(`${root}/phase-ends/current/PHASE_PLAN.md`).catch(() => null)
            const loop = loopText === null ? undefined : { state: parseLoop(loopText), phase: planText === null ? '' : planPhase(planText).phase }
            const memory = (await $.fs.read(`${root}/.claude-state/memory/MEMORY.md`).catch(() => null)) ?? undefined
            // 3.24 T7b: Claude's card slice (`card.py slice claude`), so the card reaches every fresh Claude; none on a failure.
            const sl = python === '' ? null : await $.process.run([python, 'tools/card.py', 'slice', 'claude'], { cwd: root, timeoutMs: 15_000 }).catch(() => null)
            const card = sl !== null && sl.exitCode === 0 && sl.stdout.trim() !== '' ? sl.stdout : undefined
            // 3.24 T7b-2c: a pending first run (pa.json) says plans wait for the memory curator.
            const firstRun = firstRunLine(await $.fs.read(`${root}/.claude/pa.json`).catch(() => null), '') === '' ? undefined : FIRST_RUN_WAITS
            return buildSeed(seedEntries(entries, sending), { children, planPaths, budgetTokens: SEED_BUDGET_TOKENS, session: cur?.sid || undefined, loop, memory, card, firstRun })
          }
          const spawnPartner = async (text: string, why: string): Promise<void> => {
            const before = (await read($, stateAtom))?.partner?.agentId ?? ''
            const seed = await seedFor(text)
            const spawned = await $.agent.spawn({ subagentType: PARTNER_TYPE, prompt: `${seed}\n\n${text}`, description: 'Claude' })
            // agentId is optional in the result: absent, the newest live partner the list shows is the one started.
            const id = spawned.deny !== undefined
              ? undefined
              : spawned.agentId ?? newestPartner(await $.agent.list(), before)
            if (typeof id === 'string' && id !== '') {
              const at = await $.clock.now()
              const model = spawned.model ?? ''
              const seedTokens = Math.round(seed.length / 4)
              jseq += 1
              const out = journalOut(at, jseq, 'mod', 'start', { session: sidNow, agentId: id, model },
                `Fresh partner ${id} (${why}); seed ~${String(seedTokens)} tokens.${before === '' ? '' : ` Replaces ${before}.`}`)
              await step(x => fileEntry({ ...x, partner: newPartner(id, model, at), awaiting: null }, out))
              await log('route.spawn', { why, agentId: id, model, seedTokens, from: before })
              await flushJournal()
            } else {
              await log('route.spawn.deny', { why, result: clip(spawned, 300) })
            }
          }
          try {
            await flushJournal()
            let st = await read($, stateAtom)
            if (st === null) {
              return
            }
            const now = await $.clock.now()
            const aw = st.awaiting
            if (aw !== null) {
              const listed = (await $.agent.list()).find(a => a.id === aw.agentId)
              if (isLive(listed?.status)) {
                st = await step(x => ({ ...x, awaiting: null }))
                await log('route.seen', { agentId: aw.agentId, status: listed?.status })
              } else if (now - aw.at >= RESUME_CHECK_MS) {
                // After --resume the old partner is gone: the message seeds a fresh one.
                await log('route.resume.fail', { agentId: aw.agentId, status: listed?.status ?? 'unlisted' })
                await step(x => ({ ...x, awaiting: null }))
                await spawnPartner(aw.text, 'resume-check')
                st = await read($, stateAtom)
              }
            }
            while (st !== null && !outboxHeld(st) && st.outbox.length > 0) {
              if (isStale(gen, await read($, genAtom))) {
                return
              }
              const cur: RouterState = st
              const msg = cur.outbox[0]
              // 3.28.8: a developer message with attachments waits for Claude Code's source lines (IMAGE_WAIT_MS at most).
              if (msg?.kind === 'dev' && imagesPending(imageWait, await $.clock.now())) {
                break
              }
              // Taken before acting: a crash mid-send never sends twice.
              st = await step(x => ({ ...x, outbox: x.outbox.slice(1) }))
              if (msg === undefined) {
                continue
              }
              if (msg.kind === 'forward') {
                const list = await $.agent.list()
                const parent = parentOf(cur.children, msg.child, list)
                const status = parent === null ? undefined : list.find(a => a.id === parent)?.status
                if (parent === null || isLive(status)) {
                  await log('forward.skip', { child: msg.child, parent, status })
                  continue
                }
                // 3.28.1 T12: never to a cold Claude; a fresh one, seeded from the journal, takes the hand-back.
                if (parent === cur.partner?.agentId && partnerCold(cur, list, await $.clock.now())) {
                  await spawnPartner(msg.text, 'cold')
                  st = await step(x => ({ ...x, sentKind: 'forward', sentAt: x.partner?.spawnedAt ?? 0 }))
                  continue
                }
                if (await loopExpertCold(cur, parent, await $.clock.now())) {
                  await toColdExpert(parent, msg.child, msg.text)
                  continue
                }
                const sent = await $.session.send({ to: { agentId: parent }, text: msg.text })
                await log('forward.sent', { child: msg.child, parent, status: status ?? 'unlisted', sent })
                if (sent.isDelivered && parent === cur.partner?.agentId) {
                  const at = await $.clock.now()
                  st = await step(x => ({ ...x, sentKind: 'forward', sentAt: at }))
                }
                continue
              }
              const p = cur.partner
              // A loop notice's reply goes to its caller when that is a listed agent other than the partner.
              const direct = msg.kind === 'other' && msg.child !== '' ? otherTarget(msg.child, p?.agentId ?? '', await $.agent.list()) : ''
              if (direct !== '' && (await loopExpertCold(cur, direct, await $.clock.now()))) {
                await toColdExpert(direct, '', msg.text)
                continue
              }
              if (direct !== '') {
                const sent = await $.session.send({ to: { agentId: direct }, text: msg.text })
                await log('route.direct', { agentId: direct, sent })
                continue
              }
              let route: 'resume' | 'fresh' = 'fresh'
              let cold = false
              if (p !== null) {
                const list = await $.agent.list()
                const out = childOut(p.agentId, cur.children, list)
                // 3.28.1 T12: busy by its warming status (a turn ended, listed `waiting` on its own work, is not busy).
                const busy = isBusy(partnerStatus(list.find(a => a.id === p.agentId)?.status, p.lastTurnAt, partnerIdle(cur)))
                const now = await $.clock.now()
                route = partnerRoute(p, now, { childOut: out, busy, rotateAt, pingsLeft: partnerPingsLeft(cur) })
                cold = route === 'fresh' && partnerCold(cur, list, now)
                if (route === 'fresh') {
                  await log('route.rotate', { agentId: p.agentId, ctx: p.ctx, topic: p.topic, lastTurnAt: p.lastTurnAt, rotateAt, busy, cold })
                }
              }
              if (p !== null && route === 'resume') {
                const sent = await $.session.send({ to: { agentId: p.agentId }, text: msg.text })
                await log('route.send', { agentId: p.agentId, kind: msg.kind, sent })
                if (sent.isDelivered) {
                  const at = await $.clock.now()
                  st = await step(x => ({ ...x, awaiting: { agentId: p.agentId, text: msg.text, at }, sentKind: msg.kind, sentAt: at }))
                  continue
                }
              }
              await spawnPartner(msg.text, p === null ? 'none' : cold ? 'cold' : route === 'fresh' ? 'rotate' : 'undelivered')
              st = await step(x => ({ ...x, sentKind: msg.kind, sentAt: x.partner?.spawnedAt ?? 0 }))
            }
            // Markers only for entries on disk: those queued before this flush (a later answer waits a tick). An answer
            // whose hand-back did not come within HANDBACK_WAIT_MS gets its marker (T8d's fallback).
            await flushJournal()
            st = await read($, stateAtom)
            const tDue = await $.clock.now()
            if (st !== null && dueMarkers(st, tDue) !== st) {
              const before = st.pendingMarkers.length
              st = await step(x => dueMarkers(x, tDue))
              await log('route.fallback', { markers: st?.pendingMarkers.slice(before) })
            }
            const ready = st?.pendingMarkers ?? []
            for (const n of ready) {
              if (isStale(gen, await read($, genAtom))) {
                return
              }
              st = await step(x => ({ ...x, pendingMarkers: x.pendingMarkers.filter(k => k !== n) }))
              // 3.24 T13B (decision 3): while the feed lives (a fresh heartbeat, no launcher error), the reply goes as a
              // feed record (feed.ts replyRecord): a feed turn draws it as a Claude card and its notification row is
              // hidden; the marker prompt's "Prompt from the pa plugin" row is not. Its number and file stay the marker's.
              const fdir = `${root}/${st?.feed.dir ?? feedDirOf(sidNow)}`
              const tNow = await $.clock.now()
              const beat = beatOf(await $.fs.read(`${fdir}/alive`).catch(() => null))
              const path = st?.markers[String(n)]
              if (beat !== null && tNow - beat < FEED_STALE_MS && (await read($, lifeAtom)).error === '' && path !== undefined) {
                const entry = await $.fs.read(path).catch(() => null)
                const reply = entry === null ? null : markerReply(entry)
                if (entry !== null && reply !== null && reply.trim() !== '') {
                  nseq += 1
                  const mf = markerFacts(entry) // 3.24 T17g: the card's title names the answer's model and effort
                  await $.fs.write(`${fdir}/in/${nudgeName(tNow, nseq, '7')}`, replyRecord({ agent: st?.partner?.agentId ?? '', n, text: reply, at: tNow, shown: shownOf(mf.model, mf.effort) }))
                  await log('route.marker', { n, mode: 'feed' })
                  continue
                }
              }
              // asUser: a plugin-framed prompt row escapes every render hook (T11 recheck, no UserMessage call for
              // it); a prompt submitted as the user's is drawn as a user row, which the UserMessage rule hides
              await $.prompt.submit({ text: markerText(n), asUser: true })
              await log('route.marker', { n, mode: 'marker' })
            }
            // 3.23 T13d.c10: the your-turn toast (route.yourTurnStep): Claude's reply landed, no agent live, YOUR_TURN_MS
            // without a developer message; once per reply; dropped under a dialog or a stop's owed toast, or an agent started.
            const yt = st?.yourTurn ?? null
            if (st !== null && yt !== null && !yt.fired) {
              const live = workingIds(await $.agent.list().catch(() => [])) // 3.24 T17g: a waiting agent works
              const held = presenting || st.present.length > 0 ? 'dialog' : st.stopToast !== null ? 'stop' : ''
              const at = await $.clock.now()
              // 3.24 T17j: a hand-back the loop has yet to act on (its review, close question or park brings its own toast)
              const lp = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
              const go = yourTurnStep(yt, at, live, st.partner?.agentId ?? '', held, ownsLoop(lp, sidNow, at) ? loopPending(lp) : '')
              const mine = (x: RouterState): boolean => x.yourTurn?.turnId === yt.turnId
              if (go.do === 'wait' && go.why !== undefined && turnWaitLogged !== `${yt.turnId} ${go.why}`) {
                turnWaitLogged = `${yt.turnId} ${go.why}`
                await log('yourturn.wait', { turnId: yt.turnId, why: go.why })
              }
              if (go.do === 'fire') {
                const t: ToastOut = { cause: 'input', body: yt.body, at, turn: true }
                st = await step(x => (mine(x) ? { ...x, yourTurn: { ...yt, fired: true }, toasts: [...x.toasts, t] } : x))
                await log('yourturn.fire', { turnId: yt.turnId, waitedMs: at - yt.at })
              } else if (go.do === 'drop') {
                st = await step(x => (mine(x) ? { ...x, yourTurn: null } : x))
                await log('yourturn.drop', { turnId: yt.turnId, why: go.why, agents: go.agents })
              }
            }
            // The waiting toast, once per answer that asks (or open ask), through PA3's own toast().
            // 3.24 T17t: a stop's toast only once due (route.dueToast: 60 s of developer quiet); it waits in `toasts`.
            const tDrain = st !== null && st.toasts.length > 0 ? await $.clock.now() : 0
            while (st !== null && st.toasts.some(x => dueToast(x, tDrain))) {
              const t = st.toasts.find(x => dueToast(x, tDrain))
              st = await step(x => {
                const i = x.toasts.findIndex(y => dueToast(y, tDrain))
                return i < 0 ? x : { ...x, toasts: x.toasts.filter((_, j) => j !== i) }
              })
              if (t === undefined) {
                continue
              }
              let why = python === '' || ledger === '' ? 'no python or home' : ''
              if (why === '') {
                try {
                  const ran = await $.process.run(toastArgv(python, ledger, sidNow, root, t), { cwd: root, timeoutMs: 10_000 })
                  why = ran.exitCode === 0 ? '' : `exit ${String(ran.exitCode)}: ${clip(ran.stderr, 200)}`
                } catch (err) {
                  why = clip(String(err), 200)
                }
              }
              if (why === '') {
                await log('toast', { cause: t.cause })
              } else if (!noToastLogged) {
                noToastLogged = true
                await log('toast.unavailable', { why })
              }
            }
          } catch (err) {
            await log('route.error', { err: clip(String(err), 300) })
          } finally {
            routing = false
          }
        })()
      })

      // T8c: a state transition saved (the presentation tick's).
      const save = async (f: (x: RouterState) => RouterState): Promise<void> => {
        const after = await update($, stateAtom, x => (x === null ? x : f(x)))
        if (after !== null) {
          await $.fs.write(statePath(base, sidNow), serializeState(after))
        }
      }
      // Path 0 (3.21 P7, its verified order; T8e): EnterPlanMode; the main plan file known for this session, else this
      // session's transcript slug names it, else an ExitPlanMode with no plan written answers its `filePath` (one extra
      // "Exit plan mode?" dialog per session) and EnterPlanMode again; write the plan there; ExitPlanMode: its result is
      // the decision. (Outside plan mode ExitPlanMode errors on 2.1.293: T11.) Any step failing: the fallback, a Markdown
      // pane and `$.ui.ask`. Zero model tokens either way; never the main model.
      const presentPlan = async (p: Present): Promise<string> => {
        const src = planFilePath(root, p.path)
        const text = await $.fs.read(src).catch(() => null)
        // 3.23 T13d.c3: no `▣ plan presented` line; the plan itself is the screen's (the dialog, the pane or the fallback lines).
        // 3.24 T13F: the dialog is shown a copy within its limits (a long line soft-wrapped); the draft stays as it is,
        // and a copy still past a limit goes to the fallback, before plan mode is entered.
        const shown = dialogCopy(text ?? '')
        let why = text === null ? `plan file unreadable: ${src}` : shown.why === '' ? '' : `the dialog would withhold it: ${shown.why}`
        let line = ''
        // 3.24 T13F: in plan mode by the router's EnterPlanMode and not yet out by an approval or the learning exit.
        let inPlan = false
        const enterPlan = async (step: string): Promise<void> => {
          try {
            const enter = await $.tool.call({ tool: 'EnterPlanMode' })
            await log(step, { result: clip(enter, 300), mode: modeSeen })
            if (enter.deny !== undefined) {
              why = `enter denied: ${clip(enter.deny, 200)}`
            } else {
              inPlan = true
            }
          } catch (err) {
            why = `enter: ${clip(String(err), 200)}`
          }
        }
        if (why === '') {
          await enterPlan('plan.enter')
        }
        const known = (await read($, stateAtom))?.planFile
        let file = known?.sid === sidNow ? known.path : ''
        if (why === '' && file === '') {
          const transcript = transcriptSeen.sid === sidNow ? transcriptSeen.path : ''
          const argv = slugArgv(python, transcript)
          const ran = argv === null ? null : await $.process.run(argv, { cwd: root, timeoutMs: 10_000 }).catch(() => null)
          file = slugFile(ran)
          await log('plan.slug', { transcript, file, exitCode: ran?.exitCode ?? null, stderr: clip(ran?.stderr ?? '', 200) })
        }
        if (why === '' && file === '') {
          // 3.23 T13b: `learning` set just around this call; the `tool.check` hook allows it without the dialog.
          await save(x => ({ ...x, learning: true }))
          try {
            const learned = await $.tool.call({ tool: 'ExitPlanMode' })
            file = learnedFile(learned)
            inPlan = file === ''
            await log('plan.learn', { result: clip(learned, 600), file, mode: modeSeen })
            if (file === '') {
              why = 'no plan file learned'
            }
          } catch (err) {
            why = `learn: ${clip(String(err), 200)}`
          } finally {
            await save(x => ({ ...x, learning: false }))
          }
          if (why === '') {
            await enterPlan('plan.enter.again')
          }
        }
        if (why === '' && known?.path !== file) {
          const pf = { sid: sidNow, path: file }
          await save(x => ({ ...x, planFile: pf }))
        }
        if (why === '') {
          try {
            await $.fs.write(file, shown.copy)
            if (shown.copy !== text) {
              await log('plan.wrap', { lines: (text ?? '').split('\n').length, shown: shown.copy.split('\n').length })
            }
          } catch (err) {
            why = `write: ${clip(String(err), 200)}`
          }
        }
        if (why === '') {
          try {
            const exit = await $.tool.call({ tool: 'ExitPlanMode' })
            const d = decisionLine(exit)
            await log('plan.exit', { result: clip(exit, 3000), mode: modeSeen, line: clip(d.line, 300) })
            if (!d.known) {
              await log('decision.unknown', { result: clip(exit, 3000) })
            }
            line = d.line
            inPlan = line !== APPROVED
          } catch (err) {
            why = `exit: ${clip(String(err), 200)}`
          }
        }
        if (inPlan) {
          // 3.24 T13F: a decision that is not an approval (changes asked, rejected, withheld then Esc) or a failed step
          // leaves the engine in plan mode; round 3's partner held off on its reminder, idle. One more exit with the
          // allow flag up leaves it; `mode` after it is the mode the engine restored.
          await save(x => ({ ...x, learning: true }))
          try {
            const left = await $.tool.call({ tool: 'ExitPlanMode' })
            await log('plan.leave', { result: clip(left, 600), mode: modeSeen })
          } catch (err) {
            await log('plan.leave', { err: clip(String(err), 200), mode: modeSeen })
          } finally {
            await save(x => ({ ...x, learning: false }))
          }
        }
        if (why !== '') {
          await log('plan.fallback', { id: p.id, why })
          const shown = text ?? `(The plan file ${p.path} could not be read.)`
          await update($, paneAtom, () => `# ${p.title}\n\n${shown}`)
          // `focus: true` tried (T8e): whether a focused open is placed below 144 columns is logged with the width's reason.
          const opened = await $.ui.open({ id: PLAN_PANE, title: `Plan: ${p.title}`, focus: true }).catch((err: unknown) => ({ isPlaced: false, reason: String(err) }))
          await log('plan.pane', { opened: clip(opened, 200), focus: true })
          if (!opened.isPlaced) {
            // The pane waits undrawn: the plan goes into the transcript, line by line, before the picker.
            for (const l of planLogLines(p.title, shown)) {
              $.ui.log(l, { to: 'transcript' })
            }
          }
          let answer: string | null = null
          try {
            answer = await $.ui.ask(FALLBACK_QUESTION, [...FALLBACK_OPTIONS])
          } catch (err) {
            await log('plan.ask.dismissed', { err: clip(String(err), 200) })
          }
          await $.ui.close({ id: PLAN_PANE }).catch(quiet)
          await update($, paneAtom, () => '')
          line = fallbackLine(answer)
        }
        const at = await $.clock.now()
        jseq += 1
        const planOut = journalOut(at, jseq, 'mod', 'plan', { session: sidNow, agentId: p.caller }, planBody(p, text ?? ''))
        jseq += 1
        const decOut = journalOut(at, jseq, 'dev', 'decision', { session: sidNow, agentId: p.caller }, line)
        await save(x => fileEntry(fileEntry(x, planOut), decOut))
        return line
      }
      // An ask: `$.ui.ask` under the same deferral; a dismissal (a rejection) answers `dismissed`.
      const presentAsk = async (p: Present): Promise<string> => {
        let answer: string | null = null
        try {
          answer = await $.ui.ask(p.question, p.options)
        } catch (err) {
          await log('ask.dismissed', { id: p.id, err: clip(String(err), 200) })
        }
        const line = answerLine(answer)
        const at = await $.clock.now()
        jseq += 1
        const askOut = journalOut(at, jseq, 'mod', 'prompt', { session: sidNow, agentId: p.caller, role: 'ask' }, askBody(p))
        jseq += 1
        const decOut = journalOut(at, jseq, 'dev', 'decision', { session: sidNow, agentId: p.caller }, line)
        await save(x => fileEntry(fileEntry(x, askOut), decOut))
        return line
      }
      // 3.23 T6a1, PA_MOD_TRIAL_APPROVE=1: no dialog; a plan approved, an ask its first option; logged and journaled.
      const presentTrial = async (p: Present): Promise<string> => {
        const line = trialLine(p)
        await log('present.trial', { id: p.id, kind: p.kind, line })
        const text = p.kind === 'plan' ? await $.fs.read(planFilePath(root, p.path)).catch(() => '') : ''
        const at = await $.clock.now()
        jseq += 1
        const shown = p.kind === 'plan'
          ? journalOut(at, jseq, 'mod', 'plan', { session: sidNow, agentId: p.caller }, planBody(p, text))
          : journalOut(at, jseq, 'mod', 'prompt', { session: sidNow, agentId: p.caller, role: 'ask' }, askBody(p))
        jseq += 1
        const decOut = journalOut(at, jseq, 'dev', 'decision', { session: sidNow, agentId: p.caller }, line)
        await save(x => fileEntry(fileEntry(x, shown), decOut))
        return line
      }
      const dialogPath = `${base}/${sidNow}/${DIALOG_FILE}`

      // The presentation tick (T8c, planview.ts): the head of `present`, one dialog at a time, never while an agent is
      // running, pending or waiting (a mode switch reaches it) nor over a draft. 3.23 T13d.c7: that agent rule is the
      // plans'; an ask waits only for its caller running or pending, or an open permission dialog (3.24 T8, planview.ts
      // holdingAgent; a loop ask also for Claude's running turn). Plan mode and `$.ui.ask` work only from
      // a tick made here: from a timer made in an agent's dispatch they run in that agent's loop (design.md).
      const presentTimer = $.clock.every(DRAIN_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            presentTimer.cancel()
            return
          }
          if (presenting || !(await read($, activeAtom))) {
            return
          }
          const head = (await read($, stateAtom))?.present[0]
          if (head === undefined) {
            return
          }
          presenting = true
          try {
            const holder = holdingAgent(head, await $.agent.list(), await read($, dialogsAtom)) // an ask waits only for real conflicts
            const hold = presentHold({ busy: holder !== null, draft: (await $.prompt.read()).text })
            if (hold !== '') {
              const seen = `${hold}|${holder?.id ?? ''}|${holder?.status ?? ''}`
              if (seen !== lastHold) {
                lastHold = seen
                await log('present.defer', { id: head.id, why: hold, agent: holder?.id ?? '', status: holder?.status ?? '' })
              }
              return
            }
            lastHold = ''
            await $.fs.write(dialogPath, dialogJson(true, head.id, await $.clock.now()))
            // Taken before acting: a crash mid-dialog never shows it twice.
            await save(x => ({ ...x, present: x.present.filter(p => p.id !== head.id) }))
            // 3.24 T7b-2c: while the project's first run is pending (pa.json), no plan is presented; Claude is told why.
            if (head.kind === 'plan' && firstRunLine(await $.fs.read(`${root}/.claude/pa.json`).catch(() => null), '') !== '') {
              const at = await $.clock.now()
              await $.fs.write(dialogPath, dialogJson(false, head.id, at))
              await save(x => enqueue(x, 'other', `Not presented: ${FIRST_RUN_WAITS}`, head.caller, at))
              await log('present.firstrun', { id: head.id, caller: head.caller })
              return
            }
            let line = ''
            try {
              line = (await $.env.get('PA_MOD_TRIAL_APPROVE')) === '1'
                ? await presentTrial(head)
                : head.kind === 'plan' ? await presentPlan(head) : await presentAsk(head)
            } finally {
              await $.fs.write(dialogPath, dialogJson(false, head.id, await $.clock.now()))
            }
            const at = await $.clock.now()
            // 3.24 T17t: the developer answered: a stop's toast still waiting for his quiet is dropped.
            await save(heardDeveloper)
            if (head.caller === 'loop' || (installs(head) && line === APPROVED)) {
              // The loop's own ask answered, or an approved plan for the loop to install: a request in its inbox.
              fseq += 1
              const r = head.caller === 'loop'
                ? requestFile(at, fseq, 'answer', 'router', { id: head.id, line })
                : requestFile(at, fseq, 'install', head.caller, {
                  kind: head.planKind, path: head.path, title: head.title, rules: head.rules ?? [], candidates: head.candidates ?? [],
                })
              await $.fs.write(`${base}/${sidNow}/inbox/${r.name}`, r.body)
            } else {
              await save(x => enqueue(x, 'other', line, head.caller, at))
            }
            await log('present.answer', { id: head.id, kind: head.kind, caller: head.caller, line: clip(line, 300) })
          } catch (err) {
            await log('present.error', { id: head.id, err: clip(String(err), 300) })
          } finally {
            presenting = false
          }
        })()
      })
      // The model's rates, once per model per session: Python's own matcher, else the table and modelKey.
      const priceOf = async (model: string): Promise<PriceCache | null> => {
        if (model === '') {
          return null
        }
        const cached = (await read($, ratesAtom))[model]
        if (cached !== undefined) {
          return cached
        }
        let pc: PriceCache | null = null
        if (python !== '' && ledger !== '') {
          for (const argv of [[python, ledger, 'prices', '--json', '--model', model], [python, ledger, 'prices', '--json']]) {
            const ran = await $.process.run(argv, { cwd: root, timeoutMs: 15_000 }).catch(() => null)
            pc = ran !== null && ran.exitCode === 0 ? priceCacheOf(ran.stdout, model) : null
            if (pc !== null) {
              break
            }
          }
        }
        const value = pc
        await update($, ratesAtom, c => ({ ...c, [model]: value }))
        return pc
      }
      // 3.23 T5d2b: the loop's expert (loop.json, read only: the loop is its one writer), warmed as the partner is while
      // it waits (a child out, `childBusy`; a background shell; a question to the developer), the stretch's pings by role
      // (3.28.8, pingCount); never while it works (pending, running, waiting). 3.28.8: its coders waiting on a background
      // shell of their own are warmed too (warmCoders); the critic never is. Its state is
      // `warmAgents[agentId]`; the expert's own turn.complete lifts its pingInFlight. The loop's capture ignores its `.`.
      const saveWarm = async (id: string, f: (a: WarmAgent) => WarmAgent, blank: WarmAgent): Promise<void> => {
        const saved = await update($, stateAtom, x => (x === null ? x : { ...x, warmAgents: { ...x.warmAgents, [id]: f(x.warmAgents[id] ?? blank) } }))
        if (saved !== null) {
          await $.fs.write(statePath(base, sidNow), serializeState(saved))
        }
      }
      const warmExpert = async (): Promise<void> => {
        const loop = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
        const t = loop.task
        const st = await read($, stateAtom)
        // T13b.c4: why the expert was not pinged, once per change of reason (warm.expert.skip).
        const skip = async (why: string, data: Record<string, unknown> = {}): Promise<void> => {
          if (why !== expertSkip) {
            expertSkip = why
            await log('warm.expert.skip', { why, agentId: t?.agentId ?? '', holds: loop.holds, ...data })
          }
        }
        if (t === null || t.agentId === '' || st === null) {
          return skip('no task')
        }
        const id = t.agentId
        const blank: WarmAgent = {
          role: t.agent, lastPingAt: 0, pingInFlight: false, waitKind: '', waitStart: 0, waitUsd: 0, waitPings: 0, waitCount: 0, pings: 0, pingUsd: 0,
          avoidedUsd: 0,
        }
        const w0 = st.warmAgents[id] ?? blank
        if (w0.pingInFlight) {
          return skip('in flight')
        }
        // 3.28.1 T15: `shell`, waiting on a background shell of its own or its children's (pa-watch's shells.json).
        const kids = (await $.agent.list()).filter(a => a.parentId === id).map(a => a.id)
        const shellOut = shellsOut(shellsOf(await $.fs.read(`${base}/${sidNow}/${SHELLS_FILE}`).catch(() => null)), [id, ...kids]).length > 0
        const kind = t.childBusy === true ? 'child' : shellOut ? 'shell' : loop.holds.includes('question') ? 'question' : ''
        // T13b.c4: an expert dropped from the list while it waits is still pinged (expertStatus; run 8746b004 dropped it).
        // 3.28.1 T8: its own turn ended (a turn.complete, nothing queued to it since) with a child out: waiting, whatever
        // the list says (TerrainDiffusion a3dff244: listed `waiting` on its background coder, never pinged).
        const now = await $.clock.now()
        const turnEnded = t.lastCompleteAt > 0 && queuedTo(t, now) === 0
        const listed = (await $.agent.list()).find(a => a.id === id)?.status
        const status = expertStatus(listed, kind, t.lastCompleteAt, loop.holds.includes('stopped'), turnEnded)
        if (status === undefined) {
          return skip('not listed')
        }
        // A wait starts when the expert is first seen idle in it; seen working, or out of it, the wait ends.
        const idle = status === 'idle' || status === 'completed'
        const waitKind = idle ? kind : ''
        if (waitKind !== w0.waitKind) {
          const waitStart = waitKind === '' ? 0 : Math.max(t.lastCompleteAt, t.spawnedAt, w0.lastPingAt)
          await saveWarm(id, a => ({ ...a, waitKind, waitStart, waitUsd: 0, waitPings: 0, waitCount: 0 }), blank)
        }
        if (waitKind === '') {
          return skip(`no wait (${status})`, { childBusy: t.childBusy === true })
        }
        const step = stepFactsOf(await $.fs.read(`${base}/${sidNow}/agents/${id}.json`).catch(() => null))
        const ttl = cacheTtlOf(await $.fs.read(`${root}/.claude/agents/${t.agent}.md`).catch(() => null))
        // 3.28.1 T12: the stretch's ping count, from its context at its start (its first tick).
        if (((await read($, stateAtom))?.warmAgents[id]?.waitCount ?? 0) === 0) {
          await saveWarm(id, a => ({ ...a, waitCount: pingCount(step.ctx, 'expert') }), blank)
        }
        const w = (await read($, stateAtom))?.warmAgents[id] ?? blank
        const pc = await priceOf(step.model)
        const rates0 = pc === null ? null : ratesFrom(pc.table, pc.lookup, step.ctx)
        const rates = rates0 === null ? null : tierRates(rates0, ttl)
        const lastRequestAt = Math.max(t.lastCompleteAt, t.spawnedAt, w.lastPingAt)
        const d = pingDecision({ status, lastRequestAt, ctx: step.ctx }, now, { ttl, pings: w.waitPings, count: w.waitCount, pingAfterMs })
        if (!d.ping) {
          return skip(d.why, { status, waitKind, lastRequestAt, at: d.at, now })
        }
        expertSkip = ''
        await sendPing(id, { role: t.agent, task: t.id, ttl, model: step.model, ctx: step.ctx, rates, lastRequestAt, now, why: d.why, n: w.pings + 1 }, blank)
      }
      // One warming ping to the loop's expert: the `warm_ping` event, the hold, the `.`.
      const sendPing = async (id: string, p: {
        role: string; task: string; ttl: string; model: string; ctx: number; rates: Rates | null; lastRequestAt: number; now: number; why: string; n: number
      }, blank: WarmAgent): Promise<void> => {
        const { rates, now, n } = p
        const usd = rates === null ? 0 : pingUsd(p.ctx, rates)
        const rewrite = rates === null ? 0 : p.ctx * (rates.write_1h - rates.read)
        const detail = {
          run_id: id, session_id: sidNow, role: p.role, task: p.task, ttl: p.ttl, idle_s: Math.round((now - p.lastRequestAt) / 1000), n,
          via: 'mod', cap: null, cap_inputs: { ctx: p.ctx, model: p.model, rewrite_usd: rewrite, ping_usd: usd },
        }
        if (python !== '' && ledger !== '') {
          const argv = [python, ledger, 'event', 'warm_ping', '--session', sidNow, '--run', id, '--detail', JSON.stringify(detail)]
          const ran = await $.process.run(argv, { cwd: root, timeoutMs: 15_000 }).catch(err => ({ exitCode: -1, stdout: '', stderr: String(err) }))
          if (ran.exitCode !== 0) {
            await log('warm.event.fail', { agentId: id, exitCode: ran.exitCode, stderr: clip(ran.stderr, 200) })
          }
        }
        await saveWarm(id, a => ({ ...a, pingInFlight: true }), blank)
        const sent = await $.session.send({ to: { agentId: id }, text: '.' })
        await saveWarm(id, a => (sent.isDelivered
          ? { ...a, pings: n, pingUsd: a.pingUsd + usd, avoidedUsd: a.avoidedUsd + rewrite, lastPingAt: now, waitUsd: a.waitUsd + usd, waitPings: a.waitPings + 1 }
          : { ...a, pingInFlight: false }), blank)
        await log('warm.ping', { agentId: id, role: p.role, task: p.task, why: p.why, ctx: p.ctx, usd, rewrite, n, delivered: sent.isDelivered })
      }
      // 3.28.8: the loop expert's coders, each waiting on a background shell of its own (pa-watch's shells.json) with its
      // turn ended (listed `waiting`, `idle` or `completed`), get a `.` 4 min after their last request (~4.5 min apart), up to 2.5 h a
      // stretch (policy.pingCount); 5-minute agents only. Its `.` turn lifts its pingInFlight; a real turn end starts its
      // stretch over (turn.complete).
      const warmCoders = async (): Promise<void> => {
        const loop = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
        const t = loop.task
        if (t === null || t.agentId === '' || loop.holds.includes('stopped')) {
          return
        }
        const list = await $.agent.list()
        const shells = shellsOf(await $.fs.read(`${base}/${sidNow}/${SHELLS_FILE}`).catch(() => null))
        for (const a of list.filter(x => x.parentId === t.agentId)) {
          if (!['waiting', 'idle', 'completed'].includes(a.status) || shellsOut(shells, [a.id]).length === 0) {
            continue
          }
          const facts = await $.fs.read(`${base}/${sidNow}/agents/${a.id}.json`).catch(() => null)
          let at = 0
          let type = ''
          try {
            const d = JSON.parse(facts ?? '') as Record<string, unknown>
            at = typeof d['at'] === 'number' ? d['at'] : 0
            type = typeof d['type'] === 'string' ? d['type'] : ''
          } catch {
            continue // no facts yet: nothing to time a ping by
          }
          const role = type || a.type || ''
          const ttl = cacheTtlOf(role === '' ? null : await $.fs.read(`${root}/.claude/agents/${role}.md`).catch(() => null))
          if (ttl !== '5m') {
            continue
          }
          const step = stepFactsOf(facts)
          const blank: WarmAgent = {
            role, lastPingAt: 0, pingInFlight: false, waitKind: '', waitStart: 0, waitUsd: 0, waitPings: 0, waitCount: 0, pings: 0,
            pingUsd: 0, avoidedUsd: 0,
          }
          const w0 = (await read($, stateAtom))?.warmAgents[a.id] ?? blank
          if (w0.pingInFlight) {
            continue
          }
          if (w0.waitKind !== 'shell') {
            await saveWarm(a.id, x => ({ ...x, role, waitKind: 'shell', waitStart: at, waitUsd: 0, waitPings: 0, waitCount: pingCount(step.ctx, 'coder') }), blank)
          }
          const w = (await read($, stateAtom))?.warmAgents[a.id] ?? blank
          const now = await $.clock.now()
          const lastRequestAt = Math.max(at, w.lastPingAt)
          const d = pingDecision({ status: 'idle', lastRequestAt, ctx: step.ctx }, now, { ttl, pings: w.waitPings, count: w.waitCount })
          if (!d.ping) {
            continue
          }
          const pc = await priceOf(step.model)
          const rates0 = pc === null ? null : ratesFrom(pc.table, pc.lookup, step.ctx)
          const rates = rates0 === null ? null : tierRates(rates0, ttl)
          await sendPing(a.id, { role, task: t.id, ttl, model: step.model, ctx: step.ctx, rates, lastRequestAt, now, why: d.why, n: w.pings + 1 }, blank)
        }
      }
      // The warming tick (design.md "Warming", policy.pingDecision): the idle partner (the router's only spawn) gets a
      // `.` before its 1h cache lapses, the idle stretch's pings set by its size (3.28.1 T12, policy.pingCount).
      // The `warm_ping` event is spooled first, so the audit's 60-second match holds; its `.` answer lifts the hold.
      // 3.23 T5d2b: the loop's expert first (warmExpert).
      const warmTimer = $.clock.every(WARM_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            warmTimer.cancel()
            return
          }
          if (warming || !(await read($, routeAtom))) {
            return
          }
          warming = true
          try {
            await warmExpert().catch(async err => log('warm.expert.error', { err: clip(String(err), 300) }))
            await warmCoders().catch(async err => log('warm.coders.error', { err: clip(String(err), 300) }))
            const st = await read($, stateAtom)
            const p = st?.partner ?? null
            if (st === null || p === null || p.pingInFlight || st.awaiting !== null || st.outbox.length > 0) {
              return
            }
            const list = await $.agent.list()
            // A partner dropped from the list after a turn end is still pinged (partnerStatus, as the expert's in T13b.c4);
            // 3.28.1 T12: so is one listed `waiting` on its own background work after its turn ended.
            const status = partnerStatus(list.find(a => a.id === p.agentId)?.status, p.lastTurnAt, partnerIdle(st))
            if (status === undefined) {
              return
            }
            const now = await $.clock.now()
            // 3.28.1 T12: the stretch's ping count, from its context at its start (its first tick; applyReply resets it).
            const count = st.warm.waitCount > 0 ? st.warm.waitCount : pingCount(p.ctx, 'claude')
            if (st.warm.waitCount === 0 && (status === 'idle' || status === 'completed')) {
              const counted = await update($, stateAtom, x => (x === null || x.warm.waitCount > 0 ? x : { ...x, warm: { ...x.warm, waitCount: count } }))
              if (counted !== null) {
                await $.fs.write(statePath(base, sidNow), serializeState(counted))
              }
            }
            const lastRequestAt = Math.max(p.lastTurnAt, p.spawnedAt, st.warm.lastPingAt)
            const d = pingDecision({ status, lastRequestAt, ctx: p.ctx }, now, { ttl: '1h', pings: st.warm.waitPings, count, pingAfterMs })
            if (!d.ping) {
              return
            }
            const pc = await priceOf(p.model)
            const rates = pc === null ? null : ratesFrom(pc.table, pc.lookup, p.ctx)
            const usd = rates === null ? 0 : pingUsd(p.ctx, rates)
            const rewrite = rates === null ? 0 : p.ctx * (rates.write_1h - rates.read)
            const n = st.warm.waitPings + 1
            const detail = {
              run_id: p.agentId, session_id: sidNow, ttl: '1h', idle_s: Math.round((now - lastRequestAt) / 1000), n, via: 'mod',
              cap: null, cap_inputs: { ctx: p.ctx, model: p.model, rewrite_usd: rewrite, ping_usd: usd },
            }
            if (python !== '' && ledger !== '') {
              const argv = [python, ledger, 'event', 'warm_ping', '--session', sidNow, '--run', p.agentId, '--detail', JSON.stringify(detail)]
              const ran = await $.process.run(argv, { cwd: root, timeoutMs: 15_000 }).catch(err => ({ exitCode: -1, stdout: '', stderr: String(err) }))
              if (ran.exitCode !== 0) {
                await log('warm.event.fail', { exitCode: ran.exitCode, stderr: clip(ran.stderr, 200) })
              }
            }
            // The hold is set before the send: a message routed meanwhile waits for the `.` turn's end.
            const held = await update($, stateAtom, x =>
              x === null || x.partner?.agentId !== p.agentId ? x : { ...x, partner: { ...x.partner, pingInFlight: true } })
            if (held !== null) {
              await $.fs.write(statePath(base, sidNow), serializeState(held))
            }
            const sent = await $.session.send({ to: { agentId: p.agentId }, text: '.' })
            const saved = await update($, stateAtom, x => {
              if (x === null || x.partner?.agentId !== p.agentId) {
                return x
              }
              if (!sent.isDelivered) {
                return { ...x, partner: { ...x.partner, pingInFlight: false } }
              }
              const w = x.warm
              return {
                ...x,
                warm: {
                  ...w, pings: w.pings + 1, pingUsd: w.pingUsd + usd, avoidedUsd: w.avoidedUsd + rewrite, lastPingAt: now,
                  waitUsd: w.waitUsd + usd, waitPings: n,
                },
              }
            })
            if (saved !== null) {
              await $.fs.write(statePath(base, sidNow), serializeState(saved))
            }
            await log('warm.ping', { agentId: p.agentId, why: d.why, ctx: p.ctx, usd, rewrite, n, delivered: sent.isDelivered })
          } catch (err) {
            await log('warm.error', { err: clip(String(err), 300) })
          } finally {
            warming = false
          }
        })()
      })

      // 3.23 T13d.c1c: the feed never stays dead (feed.ts, "the feed's life"). The restart is the Monitor tool with the
      // launcher and `--dir` (the folder the writers use); `calling` is up just around the call, so the router's own
      // `tool.check` allows it without a dialog. The tick reads the heartbeat; prompt.submit acts on the notices.
      const feedDir = async (): Promise<string> => `${root}/${(await read($, stateAtom))?.feed.dir ?? feedDirOf(sidNow)}`
      const restart = async (why: string): Promise<void> => {
        const now = await $.clock.now()
        const life = await read($, lifeAtom)
        if (life.error !== '') {
          return // 3.23 T13d.c2: the feed cannot start (the launcher's error): final for the session, logged once
        }
        const gate = restartGate(life, now)
        if (!gate.ok) {
          await log('feed.refuse', { why, gate: gate.why, recent: gate.recent.length })
          return
        }
        const command = feedCommand($.plugin.root, root, await feedDir())
        await update($, lifeAtom, x => ({ ...x, calling: true, restarts: [...gate.recent, now] }))
        try {
          const r = await $.tool.call({ tool: 'Monitor', description: FEED_DESC, timeout_ms: FEED_TIMEOUT_MS, command })
          await update($, lifeAtom, x => ({ ...x, waiting: true })) // 3.23 T13d.c2: its end notice or expiry ends the wait
          await log('feed.restart', { why, command, n: gate.recent.length + 1, result: clip(JSON.stringify(r ?? null), 300) })
        } catch (err) {
          await log('feed.restart', { why, command, n: gate.recent.length + 1, err: clip(String(err), 300) })
        } finally {
          await update($, lifeAtom, x => ({ ...x, calling: false }))
        }
      }
      feedRestart = restart
      const feedTimer = $.clock.every(FEED_BEAT_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            feedTimer.cancel()
            return
          }
          if (!(await read($, routeAtom))) {
            return
          }
          const now = await $.clock.now()
          const beat = beatOf(await $.fs.read(`${await feedDir()}/alive`).catch(() => null))
          const why = staleOf(await update($, lifeAtom, x => seenBeat(x, beat, now)), beat, now)
          if (why !== '') {
            await restart(why)
          }
        })().catch(async err => log('feed.error', { err: clip(String(err), 300) }))
      })

      // 3.24 T13B (decision 2): the router's band part, every WORK_MS. Claude's rows: the partner and its live children
      // (an agent on a warming ping left out), each with its latest step (the watcher's facts); the conversation-only note
      // (loop.json, read only; moved from pa-watch's usage line); whether anything works (a live agent, the loop at work
      // in this session, a send or a reply owed); the dots, cycled only while `‹pa› working` shows. Written when changed.
      const workSince = new Map<string, number>()
      const workTimer = $.clock.every(WORK_MS, () => {
        void (async () => {
          if (isStale(gen, await read($, genAtom))) {
            workTimer.cancel()
            return
          }
          if (!(await read($, routeAtom))) {
            return
          }
          const now = await $.clock.now()
          const st = await read($, stateAtom)
          const pinging = new Set(Object.entries(st?.warmAgents ?? {}).filter(([, a]) => a.pingInFlight).map(([id]) => id))
          if (st?.partner?.pingInFlight === true) {
            pinging.add(st.partner.agentId)
          }
          const listed = (await $.agent.list().catch(() => [])).filter(a => !pinging.has(a.id))
          for (const id of [...workSince.keys()]) {
            if (!listed.some(a => a.id === id && isBusy(a.status))) {
              workSince.delete(id)
            }
          }
          const agents: BandAgent[] = listed.map(a => {
            if (isBusy(a.status) && !workSince.has(a.id)) {
              workSince.set(a.id, now)
            }
            return { id: a.id, type: a.type, name: a.name, status: a.status, parentId: a.parentId, description: a.description, since: workSince.get(a.id) ?? now }
          })
          const pid = st?.partner?.agentId ?? ''
          const tree = new Set(pid === '' ? [] : bandIds(agents, [{ id: pid, task: '' }]).map(x => x.id))
          for (const a of agents) {
            if (tree.has(a.id)) {
              const facts = await $.fs.read(`${root}/.run/mod/${sidNow}/agents/${a.id}.json`).then(t => JSON.parse(t) as Record<string, unknown>).catch(() => null)
              const s = bandStepOf(typeof facts === 'object' && facts !== null && !Array.isArray(facts) ? facts : null)
              if (s !== undefined) {
                a.step = s
              }
            }
          }
          const rows = claudeRows(agents, pid, now)
          const loop = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
          const owns = ownsLoop(loop, sidNow, now)
          const loopWorks = owns && (loop.state === 'running' || loop.state === 'routing' || loop.state === 'closing' ||
            (loop.state === 'boundary' && loop.holds.length === 0))
          const working = rows.length > 0 || listed.some(a => isLive(a.status)) || loopWorks ||
            (st !== null && (st.outbox.length > 0 || st.awaiting !== null || st.pendingMarkers.length > 0))
          const prev = await read($, workAtom)
          const shown = bandView(await read($, bandAtom), { ...prev, rows, note: loopNoteOf(owns, loop.ownerSid), working })
          const dots = working && shown.lines[0]?.startsWith('‹pa› working') === true ? (prev.dots % 3) + 1 : prev.dots
          const work: Work = { rows, note: loopNoteOf(owns, loop.ownerSid), working, dots }
          if (JSON.stringify(work) !== JSON.stringify(prev)) {
            await update($, workAtom, () => work)
          }
        })().catch(async err => log('work.error', { err: clip(String(err), 300) }))
      })
    }

    rearm = arm
    await arm('start', sid)
    return next(e)
  }).catch(async ($, e, next) => {
    // 3.24 T6: an activation that threw leaves the error record (pa/health.py: the mod did not load cleanly).
    if (next.error.kind === 'throw') {
      await errorRecord($, '', 'session.start', next.error.message ?? '')
    }
    return next(e)
  })

  // Re-arm after a resume or a /clear: $.state was reset (or the process is new), the ticks of the old generation stop.
  on('classic.SessionStart', async ($, e, next) => {
    const source = e.source
    if (typeof e.transcript_path === 'string' && e.transcript_path !== '') {
      transcriptSeen = { sid: e.session_id, path: e.transcript_path }
    }
    const f = rearm
    if ((source === 'resume' || source === 'clear') && f !== null) {
      const sidNow = e.session_id
      $.clock.after(0, () => {
        void f(`classic.SessionStart:${source}`, sidNow).catch(async (err: unknown) => errorRecord($, sidNow, `classic.SessionStart:${source}`, String(err)))
      })
    }
    const out = await next(e)
    if (!(await read($, routeAtom))) {
      return out
    }
    // 3.23 T13d.c2: the session title (the terminal tab, Remote Control's list), the status line header's form.
    const root = await $.session.root()
    const title = sessionTitle(projectName(await $.fs.read(`${root}/.claude/pa.json`).catch(() => null), root),
      (await $.fs.read(`${root}/phase-ends/current/PHASE_PLAN.md`).catch(() => null)) ?? '',
      (await $.fs.read(`${root}/GENERATION_PLAN.md`).catch(() => null)) ?? '')
    return { ...out, sessionTitle: title }
  }).catch(async ($, e, next) => {
    if (next.error.kind === 'throw') { // 3.24 T6: the error record, as session.start's
      await errorRecord($, e.session_id, `classic.SessionStart:${e.source}`, next.error.message ?? '')
    }
    return next(e)
  })

  // 3.23 T13d.c2: the session title refreshed on every prompt while the mod is active (one debug event per call: whether
  // it fires for notification turns too, so the count follows the tasks).
  on('classic.UserPromptSubmit', async ($, e, next) => {
    const out = await next(e)
    if (!(await read($, routeAtom))) {
      return out
    }
    const root = await $.session.root()
    const title = sessionTitle(projectName(await $.fs.read(`${root}/.claude/pa.json`).catch(() => null), root),
      (await $.fs.read(`${root}/phase-ends/current/PHASE_PLAN.md`).catch(() => null)) ?? '',
      (await $.fs.read(`${root}/GENERATION_PLAN.md`).catch(() => null)) ?? '')
    if (debugBase !== '') {
      await $.fs.write(...ev('title', { source: e.source ?? '', title, prompt: clip(e.prompt, 80) })).catch(quiet)
    }
    return { ...out, sessionTitle: title }
  })

  // A /clear ends the session; the process goes on under a new id and no session.start fires (typings, SessionEndInput).
  on('session.end', async ($, e, next) => {
    const f = rearm
    if (e.reason === 'clear' && f !== null) {
      $.clock.after(500, () => {
        void (async () => {
          await f('session.end:clear', await $.session.id())
        })().catch(quiet)
      })
    }
    return next(e)
  })

  // The route (route.ts). Every prompt is accepted; its routing is recorded in the state and the tick acts on it.
  on('prompt.submit', async ($, e, next) => {
    if (!(await read($, routeAtom))) {
      return next(e)
    }
    const out = await next(e)
    if (out.drop !== undefined) {
      return out
    }
    try {
      const route = classify(e.origin, e.text, await read($, sdkAtom))
      const text = out.text
      const now = await $.clock.now()
      let kind = `route.${route}`
      if (route === 'developer' && (e.origin.kind === 'composer' || e.origin.kind === 'bridge')) {
        // 3.23 T13d.c1c: the first interactive developer prompt starts the no-heartbeat wait (never under `-p`).
        await update($, lifeAtom, x => (x.devAt === null ? { ...x, devAt: now } : x))
      }
      let data: Record<string, unknown> = { origin: e.origin, text: clip(text, 200) }
      let change: ((s: RouterState) => RouterState) | null = null
      if (route === 'developer') {
        // 3.28.8: its attachments' files reach Claude through Claude Code's own source lines (the session.append below).
        imageWait = (e.attachments?.length ?? 0) > 0 ? { at: now, count: e.attachments?.length ?? 0, lines: [] } : null
        if ((e.attachments?.length ?? 0) > 0) {
          await update($, turnsAtom, r => addAttach(r, text))
          data = { ...data, attachments: e.attachments?.map(a => a.type) }
        }
        // 3.23 T5b2: the first message held by `talk` reaches Claude after the loop's talk line (loop.json read only; the
        // autopilot's tick pushes `talk`).
        const root = await $.session.root()
        const loop = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null))
        // 3.24 T12c: before this session start's first owning tick, a loop that would act gets the `session` line now.
        const pending = sessionStartPending(await $.session.id()) && loop.state !== 'off' &&
          !loop.holds.some(h => h !== 'talk' && h !== 'window')
        const held = talkLine(pending ? { ...loop, holds: [...loop.holds, 'session'] } : loop)
        // 3.23 T5c: while a task runs, the running-now note (digest.runningNow) from the watcher's facts files.
        let running = ''
        const lt = loop.task
        if ((loop.state === 'running' || loop.state === 'routing') && lt !== null && lt.agentId !== '') { // 3.26 T2: routing too
          const dir = `${root}/.run/mod/${await $.session.id()}/agents`
          const names = (await $.fs.list(dir).catch(() => [])).filter(f => f.kind === 'file' && f.name.endsWith('.json')).map(f => f.name)
          const files: [string, string][] = []
          for (const name of names) {
            files.push([name.slice(0, -'.json'.length), await $.fs.read(`${dir}/${name}`).catch(() => '')])
          }
          // T11-fix2: each agent's real status (`stopped`: killed, failed, or the expert gone from the list; `waiting`).
          const listed = new Map((await $.agent.list()).map(a => [a.id, a.status as string]))
          running = runningNow({ id: lt.id, title: lt.title }, taskAgents(lt.agentId, files)
            .map(a => ({ ...a, state: a.role === 'expert' || listed.has(a.id) ? statusWord(listed.get(a.id)) : '' })))
        } else if (loop.state !== 'off' && lt === null) {
          // 3.24 T17p: no task runs: say so (the last done task and when), so a stale "running" cannot stand.
          const ld = (loop as { lastDone?: { id?: unknown; at?: unknown } }).lastDone
          const last = typeof ld?.id === 'string' && ld.id !== ''
            ? { id: ld.id, hm: typeof ld.at === 'number' ? localStamp(new Date(ld.at).toISOString()).hm : '' } : null
          running = idleNow(last, nextIdOf(loop), loop.holds)
        }
        // 3.23 T13d.c2: what the developer saw, Claude sees: the feed lines drawn since Claude's last answer (loop.json's
        // lastAnswerAt), not its own, the latest 40, read from the feed log.
        const st0 = await read($, stateAtom)
        const fdir = st0?.feed.dir ?? feedDirOf(await $.session.id())
        const fp = await read($, feedAtom)
        const pid = st0?.partner?.agentId ?? ''
        const unseen = fp.drawn === null ? [] : unseenFeed((await $.fs.read(`${root}/${fdir}/log.jsonl`).catch(() => null)) ?? '', fp.drawn, loop.lastAnswerAt, pid)
        let fed = ''
        if (unseen.length > 0) {
          const listed = await $.agent.list().catch(() => [])
          fed = feedBlock(unseen.map(rec => ({ rec, who: speakerOf(rec, pid, senderOf(rec.agent, lt, st0?.children ?? {}, listed)) })), fdir)
          data = { ...data, fed: unseen.length }
        }
        const sent = [held ?? '', running, fed].filter(p => p !== '').concat(text).join('\n\n')
        jseq += 1
        const seq = jseq
        // 3.23 T13d.c10: the developer's message drops the your-turn toast still owed.
        const owed = st0?.yourTurn ?? null
        if (owed !== null && !owed.fired && debugBase !== '') {
          await $.fs.write(...ev('yourturn.drop', { turnId: owed.turnId, why: 'typed' })).catch(quiet)
        }
        // 3.24 T17t: and a stop's toast still waiting for his quiet.
        change = s => fileEntry(enqueue(heardDeveloper({ ...s, yourTurn: null }), 'dev', sent, '', now), journalOut(now, seq, 'dev', 'msg', { session: s.sid }, text))
      } else if (route === 'feed') {
        // 3.23 T13d: the feed's records wait in feedAtom for the next feed turn; a skipped number is read from the log;
        // records delivered into a running turn get a nudge, so a feed turn follows that draws them.
        kind = 'feed.in'
        const evs = feedEvents(e.text)
        const recs = evs.flatMap(x => (x.kind === 'record' ? [x.rec] : []))
        const root = await $.session.root()
        const dir = `${root}/${(await read($, stateAtom))?.feed.dir ?? feedDirOf(await $.session.id())}`
        let f = await update($, feedAtom, x => addRecords(x, recs))
        // 3.23 T13d.c6a: a record's line Claude Code cut is read from the log at once, as a gap is.
        const cuts = evs.flatMap(x => (x.kind === 'cut' && (f.drawn === null || x.n > f.drawn) ? [x.n] : []))
        const miss = [...new Set([...missingNs(f), ...cuts])].sort((a, b) => a - b)
        let filled: number[] = []
        if (miss.length > 0) {
          const got = fromLog((await $.fs.read(`${dir}/log.jsonl`).catch(() => null)) ?? '', miss)
          filled = got.map(r => r.n)
          f = await update($, feedAtom, x => addRecords(x, got))
        }
        const nudge = e.turnId !== undefined && recs.length + cuts.length > 0 && f.pending.length > 0
        if (nudge) {
          nseq += 1
          await $.fs.write(`${dir}/in/${nudgeName(now, nseq)}`, NUDGE).catch(quiet)
        }
        // 3.23 T13d.c1c: a feed notification proves a feed; its end notice or an expiry or stop line restarts it.
        // 3.23 T13d.c2: only a record or a nudge proves a working feed (an end notice or an error does not); an end ends
        // the restart's wait; the launcher's error (no Python) is final for the session: no more restarts, one plain line
        // to the developer, journaled `mod/feed`.
        const dead = deathOf(e.text)
        const works = evs.some(x => x.kind === 'record' || x.kind === 'cut' || x.kind === 'nudge')
        await update($, lifeAtom, x => ({ ...x, seen: x.seen || works, waiting: dead === '' ? x.waiting : false }))
        const error = evs.flatMap(x => (x.kind === 'error' ? [x.error] : []))[0] ?? ''
        if (error !== '' && (await read($, lifeAtom)).error === '') {
          await update($, lifeAtom, x => ({ ...x, error }))
          kind = 'feed.fail'
          const line = feedErrorLine(error)
          $.ui.log(line, { to: 'transcript' })
          jseq += 1
          const seq = jseq
          change = s => fileEntry(s, journalOut(now, seq, 'mod', 'feed', { session: s.sid }, line))
        }
        const fr = feedRestart
        if (dead !== '' && fr !== null) {
          $.clock.after(0, () => {
            void fr(dead).catch(quiet)
          })
        }
        data = {
          origin: e.origin, dead, error, ns: recs.map(r => r.n), cuts, nudges: evs.filter(x => x.kind === 'nudge').length,
          notes: evs.flatMap(x => (x.kind === 'note' ? [clip(x.line, 120)] : [])), missing: miss, filled, nudge, pending: f.pending.length,
        }
      } else if (route === 'note') {
        // 3.23 T13: a note (an agent's SendMessage to main) is never filed, forwarded or read as a contract; turn.start
        // answers its turn with a note card when a loop agent sent it.
        data = { origin: e.origin, child: handbackChild(e.text) ?? '' }
      } else if (route === 'handback') {
        const child = handbackChild(e.text) ?? ''
        const handText = e.text
        const st = await read($, stateAtom)
        data = { origin: e.origin, child }
        const loopNowS = parseLoop(await $.fs.read(`${await $.session.root()}/${LOOP_FILE}`).catch(() => null))
        const loopTask = loopNowS.task
        if (st?.partner?.agentId === child) {
          kind = 'handback.partner' // its answer is filed at turn.complete; its turn shows the report (turn.start, T8d)
        } else if (loopAgentIds(loopNowS).includes(child)) {
          kind = 'handback.loop' // 3.23 T5a: a loop agent's hand-back goes to the autopilot's capture, never filed or forwarded (T6a2: the curator and the auditor too)
        } else if (st !== null && st.forwarded[child] !== undefined) {
          kind = 'handback.dup'
        } else {
          // Filed as the child's end: `thinker/end` for a thinker, else `coder/end`.
          const listed = (await $.agent.list().catch(() => [])).find(a => a.id === child)
          const type = st?.children[child]?.type ?? listed?.type ?? ''
          const parentId = st?.children[child]?.parent ?? listed?.parentId ?? ''
          jseq += 1
          const out = journalOut(now, jseq, ['pa-thinker', 'thinker', 'deep-thinker'].includes(type) ? 'thinker' : 'coder', 'end',
            { session: st?.sid ?? '', agentId: child, ...(parentId === '' ? {} : { parentId }), ...(type === '' ? {} : { role: type }) }, handText)
          change = s => (s.forwarded[child] !== undefined ? s : fileEntry(takeHandback(s, child, handText, now), out))
          // 3.23 T5d2a: a forward to the loop's expert is queued to it: the loop's `forward` request (its inbox, a file
          // of its own) keeps that run from reading as ended before the expert's next turn.complete.
          if (parentId !== '' && loopTask?.agentId === parentId) {
            fseq += 1
            const r = forwardRequest(now, fseq, parentId, child)
            await $.fs.write(`${await $.session.root()}/.run/mod/${await $.session.id()}/inbox/${r.name}`, r.body).catch(quiet)
            data = { ...data, loopForward: parentId }
          }
        }
      } else if (route === 'other') {
        kind = 'prompt.other'
        const framed = framedOther(e.origin.kind, text)
        const origin = e.origin.kind
        jseq += 1
        const seq = jseq
        change = s => fileEntry(enqueue(s, 'other', framed, '', now),
          journalOut(now, seq, 'mod', 'prompt', { session: s.sid, role: origin }, text))
      }
      if (change !== null) {
        const f = change
        const saved = await update($, stateAtom, x => (x === null ? x : f(x)))
        if (saved !== null) {
          await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, saved.sid), serializeState(saved))
        }
      }
      if (debugBase !== '') {
        await $.fs.write(...ev(kind, data)).catch(quiet)
      }
    } catch (err) {
      // The prompt stays accepted whatever the routing did.
      if (debugBase !== '') {
        await $.fs.write(...ev('route.error', { err: clip(String(err), 300) })).catch(quiet)
      }
    }
    return out
  })

  // 3.28.8: the developer's pasted images: Claude Code names each one's file in a hidden row after the prompt
  // (`[Image: source: <path>]`); the lines go to Claude with the message, and Claude opens the files with Read.
  on('session.append', async ($, e, next) => {
    const out = await next(e)
    const w = imageWait
    try {
      if (e.agentId === undefined && e.message.isMeta === true && w !== null) {
        const lines = imageSourceLines(e.message.content)
        if (lines.length > 0 && (await read($, routeAtom))) {
          imageWait = { ...w, lines: [...w.lines, ...lines] }
          const saved = await update($, stateAtom, x => (x === null ? x : withImageSources(x, lines)))
          if (saved !== null) {
            await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, saved.sid), serializeState(saved))
          }
        }
      }
    } catch (err) {
      // The row stays as it came; the message goes without the lines.
      if (debugBase !== '') {
        await $.fs.write(...ev('image.error', { err: clip(String(err), 300) })).catch(quiet)
      }
    }
    return out
  })

  // Every turn is registered (turn.start carries no agentId, so agents' turns too; each is cleared at its turn.complete).
  on('turn.start', async ($, e, next) => {
    if ((await read($, routeAtom)) && isFeedText(e.text)) {
      // 3.23 T13d: a feed turn draws the pending feed records (turn.step); never a hand-back or a card.
      const turnId = e.turnId
      await update($, turnsAtom, r => {
        const x = registerTurn(r, turnId, e.text)
        return { ...x, kinds: { ...x.kinds, [turnId]: { kind: 'feed' } } }
      })
    } else if (await read($, routeAtom)) {
      const turnId = e.turnId
      const text = e.text
      // T8d: a partner hand-back's turn shows its report, unless a `.` ping's or its answer's marker went already (its
      // answer is filed at turn.complete, whose marker it replaces). Taken here, not at prompt.submit: a notification's
      // turn started before that hook's work was done (T8d headless trial).
      const child = handbackChild(text)
      const report = child !== null && (await read($, stateAtom))?.partner?.agentId === child ? partnerReport(text) : null
      if (child !== null && report !== null && report.trim() !== '.' && report.trim() !== '') {
        const key = replyKey(child, report)
        const now = await $.clock.now()
        const took = { show: false }
        const saved = await update($, stateAtom, x => {
          if (x === null) {
            return x
          }
          const t = takeReply(x, key, now)
          took.show = t.show
          return t.state
        })
        if (saved !== null) {
          await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, saved.sid), serializeState(saved))
        }
        if (took.show) {
          await update($, turnsAtom, r => addReply(r, key, text, report))
        }
        if (debugBase !== '') {
          await $.fs.write(...ev('handback.reply', { child, key, show: took.show, chars: report.length })).catch(quiet)
        }
      }
      // 3.23 T13: a note or finish of the loop's expert, a coder under it, or a thinker answers as a card;
      // retrievers, the curator, the auditor, `WAITING:` hand-backs and `.` pings stay the stub.
      let card: string | null = null
      const st = await read($, stateAtom)
      if (child !== null && st?.partner?.agentId !== child) {
        const root = await $.session.root()
        const lt = parseLoop(await $.fs.read(`${root}/${LOOP_FILE}`).catch(() => null)).task
        const list = await $.agent.list().catch(() => [])
        const sender: Sender = senderOf(child, lt, st?.children ?? {}, list, coderIdOf(text)) // shared with the feed (T13d)
        if (isNote(text)) {
          card = noteCardOf(sender, noteBody(text))
        } else if (sender.role !== 'other') {
          // 3.24 T17g: a notification that only points at a hand-back already spent (the agent's SubagentHandback was
          // refused, its report came as its last text): that text is the report.
          const hb = handbackReport(text)
          const late = hb !== null && isPointer(hb) && sender.role !== 'thinker'
            ? lateReport(await $.session.messages({ agentId: child }).catch(() => null))
            : null
          // 3.24 T8b: a coder's lists its commit's files (numstat).
          const commit = sender.role === 'coder' ? (parseCoderReturn(late ?? hb ?? '')?.commit.trim() ?? '') : ''
          const stat = /^[0-9a-f]{7,40}$/i.test(commit)
            ? await $.process.run(['git', 'show', '--numstat', '--format=', commit], { cwd: root, timeoutMs: 10_000 }).catch(() => null)
            : null
          card = finishCardOf(sender, text, stat !== null && stat.exitCode === 0 ? numstatOf(stat.stdout) : [], late)
        }
        // A second delivery of the same card stays the stub.
        if (card !== null && (await read($, cardsAtom)).includes(card)) {
          card = null
        }
        if (debugBase !== '') {
          await $.fs.write(...ev('card', { child, role: sender.role, task: sender.task, card: card !== null })).catch(quiet)
        }
      }
      await update($, turnsAtom, r => registerTurn(r, turnId, text))
      if (card !== null) {
        const c = card
        await update($, cardsAtom, cs => [...cs, c].slice(-MAX_CARDS))
        await keepDrawn($, d => ({ ...d, cards: mergeDrawn(d.cards, [c], x => x, MAX_CARDS) }))
        await update($, turnsAtom, r => ({ ...r, kinds: { ...r.kinds, [turnId]: { kind: 'card', text: c } } }))
      }
    }
    return next(e)
  })

  // Every main-loop turn is answered here, by construction: `next` is never called, so the main loop makes no API turn.
  on('turn.step', async function* ($, e, next) {
    if (e.agentId !== undefined || !(await read($, routeAtom))) {
      return yield* next(e)
    }
    const registered = (await read($, turnsAtom)).kinds[e.turnId]
    const kind = registered ?? { kind: 'stub' as const }
    let reply: string | null = null
    let entry: string | null = null
    if (kind.kind === 'marker') {
      const path = (await read($, stateAtom))?.markers[String(kind.n)]
      entry = path === undefined ? null : await $.fs.read(path).catch(() => null)
      reply = entry === null ? null : markerReply(entry)
    }
    let text = localAnswer(kind, reply)
    // 3.28.8: every attachment's file went to Claude (Claude Code's source lines): no text-only notice.
    if (kind.kind === 'attach' && imageWait !== null && imageWait.lines.length >= imageWait.count) {
      text = STUB
    }
    // 3.24 T13B (decision 8): Claude's reply, on its hand-back's turn or a marker's, answers as an orange Claude card
    // (registered, so the AssistantMessage rule draws it; the stored text is the card's markdown, its title line first).
    // 3.24 T13E: a marker carrying a registered card (the loop's, inbox kind `card`) answers that card as it is.
    if ((kind.kind === 'reply' || kind.kind === 'marker') && text !== STUB && !(kind.kind === 'marker' && (await read($, cardsAtom)).includes(text))) {
      // 3.24 T17g: the title names the reply's model and effort: a marker's from its journal entry, a hand-back's from
      // the partner's last step (the watcher's facts file: the hand-back's own request).
      let facts = entry === null ? { model: '', effort: '' } : markerFacts(entry)
      const pid = (await read($, stateAtom))?.partner?.agentId ?? ''
      if (kind.kind === 'reply' && pid !== '') {
        facts = stepFactsOf(await $.fs.read(`${await $.session.root()}/.run/mod/${await $.session.id()}/agents/${pid}.json`).catch(() => null))
      }
      const c = claudeCard(text, shownOf(facts.model, facts.effort))
      text = c
      if (!(await read($, cardsAtom)).includes(c)) {
        await update($, cardsAtom, cs => [...cs, c].slice(-MAX_CARDS))
        await keepDrawn($, d => ({ ...d, cards: mergeDrawn(d.cards, [c], x => x, MAX_CARDS) }))
      }
    }
    let drew: number[] = []
    if (kind.kind === 'feed') {
      // 3.23 T13d: every pending record, in `n` order, titled when the speaker changes; nothing pending: the stub.
      const f = await read($, feedAtom)
      if (f.pending.length > 0) {
        const recs: FeedRecord[] = f.pending
        const st = await read($, stateAtom)
        const lt = parseLoop(await $.fs.read(`${await $.session.root()}/${LOOP_FILE}`).catch(() => null)).task
        const list = await $.agent.list().catch(() => [])
        const items = recs.map(rec => ({ rec, who: speakerOf(rec, st?.partner?.agentId ?? '', senderOf(rec.agent, lt, st?.children ?? {}, list)) }))
        // 3.24 T17b: an agent's first titled run names its model and effort (the watcher's facts file, written at its spawn).
        const shown: Record<string, string> = {}
        for (const rec of recs) {
          if (shown[rec.agent] === undefined && announces(rec, st?.partner?.agentId ?? '') && !(f.announced ?? []).includes(rec.agent)) {
            const step = stepFactsOf(await $.fs.read(`${await $.session.root()}/.run/mod/${await $.session.id()}/agents/${rec.agent}.json`).catch(() => null))
            shown[rec.agent] = shownOf(step.model, step.effort)
          }
        }
        const drawn = feedText(items, f.speaker, shown)
        drew = recs.map(r => r.n)
        const top = drew[drew.length - 1] ?? 0
        text = drawn.text
        await update($, feedRunsAtom, xs => [...xs, { text: drawn.text, runs: drawn.runs }].slice(-MAX_CARDS))
        await keepDrawn($, d => ({ ...d, feedRuns: mergeDrawn(d.feedRuns, [{ text: drawn.text, runs: drawn.runs }], x => x.text, MAX_CARDS) }))
        await update($, feedAtom, x => ({
          pending: x.pending.filter(r => r.n > top), drawn: Math.max(x.drawn ?? 0, top), speaker: drawn.speaker,
          ...(drawn.announced.length === 0 && x.announced === undefined ? {} : { announced: [...(x.announced ?? []), ...drawn.announced].slice(-200) }),
        }))
      }
    } else {
      await update($, feedAtom, x => (x.speaker === '' ? x : { ...x, speaker: '' })) // the title shows again after other content
    }
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    if (debugBase !== '') {
      const what = registered === undefined ? 'main.unexpected' : 'main.local'
      await $.fs.write(...ev(what, { kind: kind.kind, turnId: e.turnId, index: e.index, chars: text.length, ...(drew.length > 0 ? { drew } : {}) })).catch(quiet)
      // 3.23 T13d.c11: the answer's time, to compare with its first draw (`pa.draw`): live, rows drew minutes late.
      const ms = Date.now()
      answered.set(text.trim(), { kind: drawKindOf(kind.kind), ms })
      if (answered.size > 200) {
        answered.delete(answered.keys().next().value ?? '')
      }
      await $.fs.write(...ev('answer', { kind: drawKindOf(kind.kind), turnId: e.turnId, head: text.slice(0, 60), at: ms })).catch(quiet)
    }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })

  // The partner's reply: its `partner/answer` journal entry (queued; the route tick writes it, then submits the marker
  // naming it) and the waiting toast when it asks; any partner turn's end (a `.` ping, never filed) lifts the ping hold.
  // T8d: no marker when its hand-back showed the answer; else the marker waits HANDBACK_WAIT_MS for one (dueMarkers).
  on('turn.complete', async ($, e, next) => {
    // 3.24 T8: a turn's end closes its agent's open permission dialogs (one refused may raise no close event).
    if ((await read($, activeAtom)) && (await read($, dialogsAtom)).length > 0) {
      await update($, dialogsAtom, ks => dropAgentDialogs(ks, e.agentId))
    }
    if (!(await read($, routeAtom))) {
      return next(e)
    }
    const turnId = e.turnId
    await update($, turnsAtom, r => clearTurn(r, turnId))
    const st = await read($, stateAtom)
    // 3.23 T5d2b: a warmed loop expert's turn end (its `.` answer) lifts its ping hold.
    const warmId = e.agentId
    if (warmId !== undefined && st !== null && st.warmAgents[warmId]?.pingInFlight === true) {
      const lifted = await update($, stateAtom, x => {
        const a = x?.warmAgents[warmId]
        return x === null || a === undefined ? x : { ...x, warmAgents: { ...x.warmAgents, [warmId]: { ...a, pingInFlight: false } } }
      })
      if (lifted !== null) {
        await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, lifted.sid), serializeState(lifted))
      }
    } else if (warmId !== undefined && st !== null && st.warmAgents[warmId] !== undefined) {
      // 3.28.1 T12: any other turn end of a warmed agent is a real turn: its idle stretch, and its ping count, start over.
      const reset = await update($, stateAtom, x => {
        const a = x?.warmAgents[warmId]
        return x === null || a === undefined ? x : { ...x, warmAgents: { ...x.warmAgents, [warmId]: { ...a, waitKind: '', waitPings: 0, waitCount: 0 } } }
      })
      if (reset !== null) {
        await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, reset.sid), serializeState(reset))
      }
    }
    if (e.agentId === undefined || st === null || st.partner?.agentId !== e.agentId) {
      return next(e)
    }
    const now = await $.clock.now()
    const answer = e.answer
    const root = await $.session.root()
    const base = `${root}/.run/mod`
    // 3.22 T8f: model and effort from the watcher's last step of this agent; turn.complete carries no effort.
    const step = stepFactsOf(await $.fs.read(`${base}/${st.sid}/agents/${e.agentId}.json`).catch(() => null))
    // 3.23 T13d.c5: the partner's context is its last step's (the watcher's facts file); turn.complete's usage sums
    // every step of the turn (nine tool steps of ~55k read as 511k and rotated the partner). The usage only when the
    // file is missing.
    const ctx = step.ctx > 0 ? step.ctx : usageCtx(e.usage)
    let saved: RouterState | null
    if (answer.trim() !== '.' && answer.trim() !== '') {
      const n = nextMarker(st)
      const model = step.model || (e.usage?.model ?? st.partner?.model ?? '')
      const effort = step.effort === '' ? undefined : step.effort
      jseq += 1
      const out = journalOut(now, jseq, 'claude', 'answer',
        { session: st.sid, agentId: e.agentId, model, effort, turnId, usage: entryUsage(e.usage) }, answer)
      const path = `${root}/${out.rel}`
      // 3.23 T13b: one toast per loop stop, raised by Claude's first reply after it (its presentation); plain
      // conversation raises the your-turn toast only.
      // 3.23 T13d.c10: a reply that raised none arms the your-turn toast (the route tick fires it); `known`: the agents
      // live now, besides Claude.
      let toast: ToastOut | null = null
      const key = replyKey(e.agentId, answer)
      const partnerId = e.agentId
      const known = workingIds(await $.agent.list().catch(() => []), partnerId)
      saved = await update($, stateAtom, x => {
        if (x === null) {
          return x
        }
        const r = applyReply(fileEntry(x, out), n, path, key, now, ctx, e.usage?.model ?? '')
        toast = r.stopToast === null ? null : toastOut(r.stopToast, answer, now)
        return armYourTurn(toast === null ? r : { ...r, stopToast: null, toasts: [...r.toasts, toast] }, turnId, answer, now, known, toast !== null)
      })
      if (debugBase !== '') {
        const via = saved?.replies[key]?.via ?? ''
        await $.fs.write(...ev('reply', { n, key, via, chars: answer.length, ctx, entry: out.rel, toast: (toast as ToastOut | null)?.cause ?? '' })).catch(quiet)
      }
    } else {
      saved = await update($, stateAtom, x => (x === null ? x : partnerTurnEnded(x, now, ctx)))
    }
    if (saved !== null) {
      await $.fs.write(statePath(base, saved.sid), serializeState(saved))
    }
    return next(e)
  })

  on('classic.PostModelSwitch', async ($, e, next) => {
    if (await read($, activeAtom)) {
      const now = await $.clock.now()
      const saved = await update($, stateAtom, x =>
        x === null || x.partner === null ? x : { ...x, partner: { ...x.partner, modelSwitchAt: now } })
      if (saved !== null) {
        await $.fs.write(statePath(`${await $.session.root()}/.run/mod`, saved.sid), serializeState(saved))
      }
    }
    return next(e)
  })

  // Render rules: the route's rows draw nothing (the API has no hidden prop), so the transcript reads as a chat.
  on('ui.render', { component: 'UserMessage' }, async ($, e, next) => {
    const hidden = (await read($, routeAtom)) && hideUser(e.props.origin, e.props.text)
    // Debug only (T8d): a marker or plugin row, once per drawn state, for why plugin rows escaped the rule (T11).
    if (debugBase !== '' && (e.props.text.includes('‹pa reply') || e.props.origin.kind === 'plugin')) {
      const textHead = e.props.text.slice(0, 120)
      const seen = `UserMessage:${textHead}:${String(e.props.isExpanded)}:${String(hidden)}`
      if (!rendered.has(seen)) {
        rendered.add(seen)
        await $.fs.write(...ev('render.user', { textHead, origin: e.props.origin, isExpanded: e.props.isExpanded, from: e.props.from, hidden }))
          .catch(quiet)
      }
    }
    if (hidden) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    return next(e)
  })

  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    // 3.23 T13d.c11, debug only: the first draw of each text (deduped by it), its kind and its delay from the answer.
    if (debugBase !== '' && !rendered.has(`draw:${e.props.text.trim()}`)) {
      const tt = e.props.text.trim()
      rendered.add(`draw:${tt}`)
      const ans = answered.get(tt)
      const kind: DrawKind = ans?.kind ?? ((await read($, cardsAtom)).some(c => c.trim() === tt) ? 'card'
        : (await read($, feedRunsAtom)).some(x => x.text.trim() === tt) ? 'feed' : 'other')
      const at = Date.now()
      await $.fs.write(...ev('draw', { kind, head: e.props.text.slice(0, 60), at, ...(ans !== undefined ? { delayMs: at - ans.ms } : {}) }))
        .catch(quiet)
    }
    if ((await read($, routeAtom)) && hideAssistant(e.props.text)) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    // 3.23 T13: only a text the router answered as a card draws as one; a look-alike reply of Claude's does not.
    const t = e.props.text.trim()
    const card = (await read($, cardsAtom)).some(c => c.trim() === t) ? parseCard(t) : null
    if (card !== null) {
      return drawCard($.ui.resolve(e), card, cardStyle)
    }
    // 3.23 T13d.c2: a feed answer the router stored draws run by run (the phone, with no render hook, shows the text).
    const fed = (await read($, feedRunsAtom)).find(x => x.text.trim() === t)
    if (fed !== undefined) {
      return drawFeed($.ui.resolve(e), fed.runs, cardStyle)
    }
    return next(e)
  })

  // 3.23 T13: the band above the prompt; it yields to a survey and draws nothing when nothing runs and nothing is held.
  // 3.24 T13B: with the router's part (bandView): Claude's rows, the conversation-only note, `‹pa› working` and its dots.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const band = bandView(await read($, bandAtom), await read($, workAtom))
    if (e.props.hasSurvey || (band.lines.length === 0 && band.rows.length === 0)) {
      return next(e)
    }
    return drawBand($.ui.resolve(e), band, e.props.bodyColumns)
  })

  on('ui.render', { component: 'TurnDuration' }, async ($, e, next) => {
    if (await read($, routeAtom)) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    return next(e)
  })

  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    if ((await read($, routeAtom)) && hideSpinner(e.props.message)) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    return next(e)
  })

  // T8c: the fallback plan pane `pa-plan` (PLAN_PANE; a literal for the loader), its Markdown from the presentation tick.
  on('ui.render', { component: 'Pane', requestId: 'pa-plan' }, async ($, e, next) => {
    const text = await read($, paneAtom)
    if (text === '') {
      return next(e)
    }
    const { Box, Markdown } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Markdown text={text} />
      </Box>
    )
  })

  // 3.24 T8 (L18): the open permission dialogs. Open at classic.PermissionRequest, closed at the call's PostToolUse,
  // PostToolUseFailure or PermissionDenied, and at its agent's turn end (a dialog the developer refused may raise none).
  // Under PA_MOD_DEBUG each open and close logs `$.agent.list()`'s statuses (dialogLog).
  on('classic.PermissionRequest', async ($, e, next) => {
    if (await read($, activeAtom)) {
      const key = dialogKey(e.agent_id, e.tool_name, e.tool_input)
      const open = await update($, dialogsAtom, ks => (ks.includes(key) ? ks : [...ks, key]))
      await dialogLog($, 'dialog.open', key, open.length)
    }
    return next(e)
  }).catch(($, e, next) => next(e)) // a failure leaves the dialog to the engine
  on('classic.PostToolUseFailure', async ($, e, next) => {
    await dialogClose($, dialogKey(e.agent_id, e.tool_name, e.tool_input), 'failure')
    return next(e)
  })
  on('classic.PermissionDenied', async ($, e, next) => {
    await dialogClose($, dialogKey(e.agent_id, e.tool_name, e.tool_input), 'denied')
    return next(e)
  })
  // T8c: the permission mode, as the last tool's classic.PostToolUse carries it (path 0 logs it around its calls).
  on('classic.PostToolUse', async ($, e, next) => {
    await dialogClose($, dialogKey(e.agent_id, e.tool_name, e.tool_input), 'done')
    if (typeof e.permission_mode === 'string') {
      modeSeen = e.permission_mode
      if (debugBase !== '' && /^(Enter|Exit)PlanMode$/.test(e.tool_name)) {
        await $.fs.write(...ev('plan.mode', { tool: e.tool_name, mode: modeSeen })).catch(quiet)
      }
    }
    return next(e)
  })

  // 3.23 T13b: the learning exit. presentPlan's ExitPlanMode with no plan written (only its `filePath` is wanted) is
  // allowed while state's `learning` is set around it; the real exit (flag down) keeps the dialog. No origin guard
  // (T13b.c2): the kit raises no tool.check beneath the router's own `$.tool.call`, so the check seen is the engine's.
  // 3.24 T13F: the same flag is up around the exit that leaves plan mode after a decision that is not an approval.
  on('tool.check', { tool: 'ExitPlanMode' }, async ($, e, next) =>
    (await read($, stateAtom))?.learning === true
      ? { decision: 'allow', reason: 'PA3 router: leaving plan mode with no plan to approve (the path learned, or a decision given)' }
      : next(e)).catch(($, e, next) => next(e)) // a failure leaves the verdict beneath: the dialog

  // 3.23 T13d.c1c: the feed's restart. Only the router's own Monitor call is allowed, while `calling` is up around it;
  // every other Monitor call keeps the verdict beneath.
  on('tool.check', { tool: 'Monitor' }, async ($, e, next) =>
    (await read($, lifeAtom)).calling
      ? { decision: 'allow', reason: 'PA3 router: restarting its feed monitor' }
      : next(e)).catch(($, e, next) => next(e))

  // 3.24 T17h: a `.` (or blank) hand-back is refused unless its agent answers a warming ping (route.noReportHandback):
  // round 5's thinker spent its one hand-back on `.` while its retrievers ran. A query carries no agentId: not pinged.
  on('tool.check', { tool: 'SubagentHandback' }, async ($, e, next) => {
    if (!(await read($, activeAtom))) {
      return next(e)
    }
    const st = await read($, stateAtom)
    const id = e.agentId ?? ''
    // 3.26 T2: the partner's own `.` means "show nothing" (pa-claude.md), never refused.
    if (id !== '' && st?.partner?.agentId === id) {
      return next(e)
    }
    const pinged = id !== '' && st?.warmAgents[id]?.pingInFlight === true
    return noReportHandback(e.input, pinged) ? { decision: 'deny', reason: NO_REPORT_REASON } : next(e)
  }).catch(($, e, next) => next(e)) // a failure leaves the verdict beneath

  // Debug only (3.23 T13b.c2 probe): does the engine make plan_mode rows on the mod's locally answered main-loop turns?
  // If so the plan path is learned from `detail.planFilePath` without the learning exit. Passes the row unchanged.
  on('prompt.attachment', async ($, e, next) => {
    if (debugBase !== '' && (e.type === 'plan_mode' || e.type === 'plan_mode_reentry')) {
      await $.fs.write(...ev('plan.attachment', {
        type: e.type,
        agentId: e.agentId ?? null,
        planFilePath: e.detail?.planFilePath ?? null,
        hasPlan: e.type === 'plan_mode' ? e.detail?.hasPlan ?? null : null,
      })).catch(quiet)
    }
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    if (!(await read($, activeAtom))) {
      return next(e)
    }
    if (debugBase !== '') {
      await $.fs.write(...ev('compact.skip', { trigger: e.trigger })).catch(quiet)
    }
    return { skip: COMPACT_SKIP }
  })

  // Debug only: each component's name once per session, so T5b can name the engine's waiting line. Draws unchanged.
  on('ui.render', async ($, e, next) => {
    const out = await next(e)
    if (debugBase !== '' && armed.gen !== 0 && !rendered.has(e.component)) {
      rendered.add(e.component)
      await $.fs.write(...ev('ui.render', {
        component: e.component,
        requestId: e.requestId,
        props: clip((e as { props?: unknown }).props, 120),
        out: clip(out, 120),
      })).catch(quiet)
    }
    return out
  })

  // The autopilot (autopilot.tsx): registered last, so its hooks nest inside the router's (first outermost). T4's probe:
  // hooks.json takes one module per plugin, so it is an `on`-taking helper; a repeated event needs a matcher (`{}`).
  armAutopilot(on)
}
