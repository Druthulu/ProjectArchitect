// PA 3.2 watcher plugin `pa-watch` (phase 3.22): activation, the debug log (T5a); the tools as inbox requests,
// `thinking` journal entries and the per-agent stream facts (T7); the activity lines and the usage line (T8b).
// 3.23 T5c: approach notes become `note` journal entries (experts `T<n>`, coders); the facts file carries type, description,
// parent, the latest note and the last three steps, for the router's running-now note; the sixth tool, `steer`.
// 3.23 T8: activity lines by role (the `colors` userConfig, PA_MOD_COLORS over it): glyphs, ANSI colors or a render rule.
// 3.23 T13d: each visible-text segment of an agent's step is a record in the live feed's folder (feed.ts). T13d.c3: the
// feed replaces the activity lines: the watcher draws nothing in the transcript (no `$.ui.log`), and `colors` retired.
// The router's own hooks never see the steps and tool calls of the agents it spawned (3.21 T4); this plugin does.
// The engine allows `$` only as `$.noun.method(...)` at the call site, so helpers are pure (activation.ts, debug.ts)
// and every `$` call is spelled inline in a hook (design.md, "Execution model").
// 3.24 T6: an active activation writes `.run/mod/heartbeat.json`; an activation hook that throws writes
// `.run/mod/error.json` (pa/health.py reads both; an inert session writes neither).
// 3.24 T8b: a successful Edit, MultiEdit, Write or NotebookEdit by Claude, an expert or a coder is an `edit` feed record
// (edit.ts): the router draws it in the native edit look as a note; no other tool call is recorded for display.
// 3.24 T13n: an Opus 5.5 narration block (a `thinking` block Claude Code draws white, told by its signature) feeds like
// text; the step's records are written at its end, in block order, once the api messages hold its signatures.
// 3.24 T13C: the usage line (`$.ui.status`) and the prices and spend it alone used are gone; the memory curator and the
// auditor feed like the experts (retrievers still never do); a plan draft's edit record is one labelled line (edit.ts).
// 3.24 T17b: the ladder (ladder.ts): a spawn asks T17a's resolver for its type's row (kept per session in `ladder`) and
// a managed agent is spawned on the row's model; its requests get the row's effort (Claude's never); the spawn's model
// and effort go to its facts file at once (the router shows them on its first feed line), and its first request's
// to a `spawn` journal entry. A resolver failure leaves the spawn as it is.
// 3.25 T6b: a spawn description's trailing `[effort <level>]` hint picks the row (`ladder-row --effort`, kept as
// `<type>@<level>`); that agent's requests keep its own row's effort; the hint is never shown (facts, feed, journal).
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { decide, ERROR_FILE, errorText, HEARTBEAT_FILE, heartbeatText, ledgerPath, paSetting, releaseOf, versionOf } from './activation'
import { eventFile } from './debug'
import { EDIT_TOOLS, editHeader, editOf, editsShown } from './edit'
import { feedDirOf, feedName, feedPieces, feedRecord, feeds, lastLine, narrationIndexes, startsLine, startText } from './feed'
import type { StepBlock } from './feed'
import { displayWho, entryPath, entryText, noteLines, noteWho, partnerIdOf, stamp, stepFactsText, whoOf } from './journal'
import { hintOf, keepRow, ladderArgv, PLANNER, rowKey, rowOf, spawnFields, spawnModel, stepEffort, UNSET } from './ladder'
import type { LadderRow } from './ladder'
import { argLine, depthOf, HANDBACK, isPingStep, planningAfter, ctxOf } from './stream'
import type { StepUsage } from './stream'
import { argsOf, argSummary, queuedReply, requestBody, requestName, TOOL_NAMES, TOOL_SPECS, validateInput } from './tools'

const activeAtom = atom({ plugin: 'pa-watch', key: 'active' } as const, false)
const ladderAtom = atom({ plugin: 'pa-watch', key: 'ladder' } as const, {}) // 3.24 T17b: rows by agent type (3.25 T6b: `<type>@<level>` for a hint)

// Module variables are lost on a hot reload; the next session.start or re-arm sets them again.
let debugDir = '' // `<root>/.run/mod/<sid>/debug` under PA_MOD_DEBUG=1 in an active session, else '' (no debug log)
let debugWanted = false // PA_MOD_DEBUG=1; 3.24 T8: an inert session writes nothing, so debugDir opens only when active
let counter = 0
let reqSeq = 0 // per-load sequence of inbox requests
let journalSeq = 0 // per-load sequence of journal entries
let feedSeq = 0 // per-load sequence of feed records (3.23 T13d)

/** Stream facts per agent (T8b draws the stream and usage lines from them); in-memory, lost on reload. */
type Facts = {
  model: string; effort: string; usage: unknown; tool: string; args: string; at: number
  type: string; description: string; parentId: string; note: string; steps: string[] // 3.23 T5c
  line: string // 3.23 T13d.c2: the last line of the latest feed segment
}
const facts = new Map<string, Facts>()
const factsOf = (id: string): Facts => facts.get(id) ??
  { model: '', effort: '', usage: null, tool: '', args: '', at: 0, type: '', description: '', parentId: '', note: '', steps: [], line: '' }
/** The facts file text of `f` written at `now` (the last step's `at` kept). */
const factsText = (f: Facts, ctx: number, now: number): string =>
  stepFactsText(f.model, f.effort, ctx, f.at, { type: f.type, description: f.description, parentId: f.parentId, note: f.note, steps: f.steps, updatedAt: now, line: f.line })

/** One debug event file: [path, text] for `$.fs.write`. */
function ev(kind: string, data: Record<string, unknown>): [string, string] {
  const ms = Date.now()
  counter += 1
  return [eventFile(debugDir, ms, counter, `watch.${kind}`), JSON.stringify({ ms, ev: `watch.${kind}`, ...data }, null, 1)]
}

const quiet = (): undefined => undefined

/** 3.28.1 T15: background shells (Bash `run_in_background`) per agent, `.run/mod/<sid>/shells.json` (pa loop.ts
 * SHELLS_FILE, shellsOf): `{ <agentId>: [{ id, name, at, type, exit? }] }`, from the call to its completion notice. */
type ShellRec = { id: string; name: string; at: number; type: string; exit?: number }
let shellsChain: Promise<unknown> = Promise.resolve() // one read-modify-write at a time
/** A shell's name: the last script-like token's basename (`bake_rest.sh`), else the last command's first word. */
const shellName = (command: string): string => {
  const words = command.split(/\s+/).map(w => w.replace(/^["']|["';]$/g, '')).filter(w => w !== '')
  const script = [...words].reverse().find(w => /\.(sh|py|ps1|js|ts|cmd|bat)$/i.test(w))
  const last = command.split(/&&|\|\||;/).pop()?.trim().split(/\s+/)[0] ?? ''
  return (script ?? last).split(/[\/]/).pop() ?? ''
}
/** The completion notice's task id and outcome: removed (`exit` undefined) on a clean end, else its exit code (-1 unknown). */
const shellEnd = (text: string): { id: string; exit?: number } | null => {
  const id = /<task-id>([^<]+)<\/task-id>/.exec(text)?.[1]?.trim() ?? ''
  if (!text.includes('<task-notification>') || id === '') {
    return null
  }
  const status = /<status>([^<]*)<\/status>/.exec(text)?.[1]?.trim() ?? ''
  const code = /exit code:?\s*(-?\d+)/i.exec(text)?.[1]
  const exit = code === undefined ? undefined : Number(code)
  return status === 'completed' && (exit === undefined || exit === 0) ? { id } : { id, exit: exit ?? -1 }
}
const shellsEdit = async ($: Pick<EngineInterface, 'fs' | 'session'>, f: (s: Record<string, ShellRec[]>) => Record<string, ShellRec[]> | null): Promise<void> => {
  const run = shellsChain.then(async () => {
    const path = `${await $.session.root()}/.run/mod/${await $.session.id()}/shells.json`
    let cur: Record<string, ShellRec[]> = {}
    try {
      const v = JSON.parse((await $.fs.read(path).catch(() => null)) ?? '{}') as unknown
      cur = typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, ShellRec[]>) : {}
    } catch {
      cur = {}
    }
    const next = f(cur)
    if (next !== null) {
      await $.fs.write(path, JSON.stringify(Object.fromEntries(Object.entries(next).filter(([, l]) => l.length > 0))))
    }
  })
  shellsChain = run.catch(quiet)
  await run.catch(quiet)
}

/** Each agent seen: who it is, how deep, its last context (the facts file's `ctx`). */
type Who = { who: string; depth: number; ctx: number; noted?: string; planning?: boolean } // noted: the turn whose approach note was taken; planning: after a pa-planning Skill call, until `plan` (3.23 T8)
const agents = new Map<string, Who>()

/** 3.24 T17b: agents spawned in this load whose first request is not journalled yet: their type and row. */
const spawned = new Map<string, { type: string; row: LadderRow | null }>()
/** 3.25 T6b: a hinted agent's own spawn row (`<type>@<level>`): its requests get this row's effort, not its type's. */
const ownRows = new Map<string, LadderRow>()

/** 3.24 T6: the error record pa/health.py reads, for an activation hook that threw; only where the mod would be active
 * (pa.json mod: on), so an inert session still writes nothing. Never throws. */
async function errorRecord($: Pick<EngineInterface, 'fs' | 'session' | 'env' | 'clock'>, sid: string, hook: string, error: string): Promise<void> {
  try {
    const root = await $.session.root()
    const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
    if (decide(paText, await $.env.get('PA_MOD_OFF'), versionOf(await $.session.version())).active) {
      await $.fs.write(`${root}/${ERROR_FILE}`, errorText(sid === '' ? await $.session.id() : sid, 'pa-watch', hook, error, await $.clock.now()))
    }
  } catch {
    // best effort: the record must never fail the handler that writes it
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const root = await $.session.root()
    const sid = await $.session.id()
    debugWanted = (await $.env.get('PA_MOD_DEBUG')) === '1'
    const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
    const version = versionOf(await $.session.version())
    const decision = decide(paText, await $.env.get('PA_MOD_OFF'), version)
    await update($, activeAtom, () => decision.active)
    debugDir = debugWanted && decision.active ? `${root}/.run/mod/${sid}/debug` : ''
    if (decision.active) {
      // 3.24 T6: the heartbeat pa/health.py checks (the release from the plugin's root, two up).
      const release = releaseOf(await $.fs.read(`${$.plugin.root}/../../VERSION`).catch(() => null))
      await $.fs.write(`${root}/${HEARTBEAT_FILE}`, heartbeatText(sid, release, version, await $.clock.now(), 'pa-watch')).catch(quiet)
      for (const spec of TOOL_SPECS) {
        await $.tool.register(spec)
      }
    }
    if (debugDir !== '') {
      await $.fs.write(...ev('activation', { why: 'start', sid, version, decision })).catch(quiet)
    }
    return next(e)
  }).catch(async ($, e, next) => {
    if (next.error.kind === 'throw') {
      await errorRecord($, '', 'session.start', next.error.message ?? '')
    }
    return next(e)
  })

  // A /clear or a resume resets $.state (or starts a new process): decide again under the session id it goes on with.
  on('classic.SessionStart', async ($, e, next) => {
    const source = e.source
    if (source === 'resume' || source === 'clear') {
      const root = await $.session.root()
      const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
      const version = versionOf(await $.session.version())
      const decision = decide(paText, await $.env.get('PA_MOD_OFF'), version)
      await update($, activeAtom, () => decision.active)
      debugDir = debugWanted && decision.active ? `${root}/.run/mod/${e.session_id}/debug` : ''
      if (decision.active) { // 3.24 T6: the heartbeat under the session id it goes on with
        const release = releaseOf(await $.fs.read(`${$.plugin.root}/../../VERSION`).catch(() => null))
        await $.fs.write(`${root}/${HEARTBEAT_FILE}`, heartbeatText(e.session_id, release, version, await $.clock.now(), 'pa-watch')).catch(quiet)
      }
      if (debugDir !== '') {
        await $.fs.write(...ev('activation', { why: `classic.SessionStart:${source}`, sid: e.session_id, version, decision })).catch(quiet)
      }
    }
    return next(e)
  }).catch(async ($, e, next) => {
    if (next.error.kind === 'throw') {
      await errorRecord($, e.session_id, `classic.SessionStart:${e.source}`, next.error.message ?? '')
    }
    return next(e)
  })

  // 3.24 T17b: every spawn asks the ladder for its type's row (once per session and tier; a planner spawn re-checks the
  // tier), sets a managed agent's model, and writes the spawn's model and effort to its facts file. Never blocks or
  // fails a spawn: no row leaves it as it is.
  on('agent.spawn', async ($, e, next) => {
    const type = e.subagentType
    if (!(await read($, activeAtom).catch(() => false)) || e.fork || UNSET.includes(type)) {
      return next(e)
    }
    let row: LadderRow | null = null
    const { level, desc } = hintOf(e.description) // 3.25 T6b: an `[effort <level>]` hint picks the row
    const key = rowKey(type, level)
    try {
      row = (await read($, ladderAtom))[key] ?? null
      if (row === null || type === PLANNER) {
        const root = await $.session.root()
        const paText = await $.fs.read(`${root}/.claude/pa.json`).catch(() => null)
        const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
        const argv = ladderArgv(paSetting(paText, 'python'), ledgerPath(await $.env.get('PA_MOD_PA3_DIR'), home), type, root, level)
        const ran = argv === null ? null : await $.process.run(argv, { cwd: root, timeoutMs: 10_000 }).catch(() => null)
        const got = ran !== null && ran.exitCode === 0 ? rowOf(ran.stdout, key) : null
        if (got !== null) {
          await update($, ladderAtom, rs => keepRow(rs, got))
          row = got
        }
        if (debugDir !== '') {
          await $.fs.write(...ev('ladder', { type: key, row: got, exitCode: ran?.exitCode ?? null })).catch(quiet)
        }
      }
    } catch {
      row = null
    }
    const model = spawnModel(row, e)
    const r = await next(model === null ? e : { ...e, model })
    const id = r.agentId
    if (id !== undefined && r.deny === undefined) {
      spawned.set(id, { type, row })
      if (level !== '' && row !== null) {
        ownRows.set(id, row) // 3.25 T6b: a hinted agent keeps its own row's effort on every request
      }
      const effort = row?.effort ?? ''
      const f = { ...factsOf(id), model: r.model, effort, type, description: desc, parentId: e.parentAgentId ?? '' }
      facts.set(id, f)
      const write = async (): Promise<void> => {
        await $.fs.write(`${await $.session.root()}/.run/mod/${await $.session.id()}/agents/${id}.json`, factsText(f, 0, Date.now()))
      }
      await write().catch(quiet)
    }
    return r
  }).catch(async ($, e, next) => next(e)) // a failed hook leaves the spawn as it is

  // Every agent's steps: chunks pass through unchanged; the feed records, the facts file, and a step that had thinking
  // becomes a journal `thinking` entry. A `.` ping turn writes no record. The text before a SubagentHandback call is no note.
  // 3.24 T17b: a managed agent's request gets its row's effort (`stream` is made once its type is known).
  on('turn.step', async function* ($, e, next) {
    const agentId = e.agentId
    const active = await read($, activeAtom).catch(() => false)
    let me: Who | undefined
    if (active && agentId !== undefined) {
      me = agents.get(agentId)
      if (me === undefined) {
        const list = await $.agent.list().catch(() => [])
        const row = list.find(a => a.id === agentId)
        const f0 = factsOf(agentId) // 3.24 T17b: the spawn's facts when the list has no row
        let who = displayWho(row?.type ?? f0.type)
        facts.set(agentId, { ...f0, type: row?.type ?? f0.type, description: row === undefined ? f0.description : hintOf(row.description).desc, parentId: row?.parentId ?? f0.parentId })
        if (who === '') {
          const root = await $.session.root()
          const sid = await $.session.id()
          const partnerId = partnerIdOf(await $.fs.read(`${root}/.run/mod/${sid}/state.json`).catch(() => null))
          who = partnerId !== '' && partnerId === agentId ? 'Claude' : 'agent'
        }
        me = { who, depth: depthOf(agentId, list), ctx: 0 }
        agents.set(agentId, me)
      }
    }
    const type = agentId === undefined ? '' : factsOf(agentId).type
    const own = agentId === undefined ? undefined : ownRows.get(agentId)
    const want = me === undefined ? null : stepEffort(own ?? (await read($, ladderAtom).catch(() => ({})) as Record<string, LadderRow | undefined>)[type], type)
    const stream = next(want === null ? e : { ...e, effort: want })
    const effort = String(want ?? e.effort ?? '')
    // 3.24 T17b: an agent spawned in this load: its first request's model and effort, as a `spawn` journal entry.
    const first = agentId === undefined ? undefined : spawned.get(agentId)
    if (agentId !== undefined && first !== undefined) {
      spawned.delete(agentId)
      const ms = Date.now()
      const [date, time] = stamp(ms)
      journalSeq += 1
      const fields = spawnFields({ ts: new Date(ms).toISOString(), session: await $.session.id(), agentId, type: first.type, model: e.model, effort, row: first.row })
      const rel = entryPath(date, time, journalSeq, 'w', whoOf(first.type, agentId, '', factsOf(agentId).description), 'spawn')
      const body = `${first.type} ran on ${e.model} at ${effort === '' ? 'no effort' : effort}.`
      await $.fs.write(`${await $.session.root()}/${rel}`, entryText(fields, body)).catch(quiet)
    }
    // 3.23 T13d: the feed. Text before each tool chunk but the hand-back, and after the last tool chunk of a step that
    // made tool calls, is one record each (`kind` "text"); the final report and the text before the hand-back are not.
    // 3.23 T13d.c7: statements only (the developer's rule: what is white in a subagent's view); the step summaries
    // (`thinking` chunks) and tool calls never feed. c5's `kind` "summary" records are gone; `kind` stays "text".
    // 3.24 T13n: but a narration block does (white in the view); all the step's records are written at its end, which
    // comes before its tools run.
    const feedOn = me !== undefined && agentId !== undefined && feeds(factsOf(agentId).type)
    let feedDir = ''
    const feedOut = async (raw: string): Promise<void> => {
      const t = raw.trim()
      if (!feedOn || t === '' || agentId === undefined || me === undefined) {
        return
      }
      if (feedDir === '') {
        const root = await $.session.root()
        const sid = await $.session.id()
        feedDir = feedDirOf(await $.fs.read(`${root}/.run/mod/${sid}/state.json`).catch(() => null), root, sid)
      }
      const f = { ...factsOf(agentId), line: lastLine(t) }
      const at = Date.now()
      feedSeq += 1
      const body = feedRecord({ agent: agentId, type: f.type, desc: f.description, planning: me.planning === true, text: t, at, kind: 'text' })
      await $.fs.write(`${feedDir}/in/${feedName(at, feedSeq)}`, body).catch(quiet)
      // 3.23 T13d.c2: the facts' `line` (the band's step), written with the record.
      facts.set(agentId, f)
      await $.fs.write(`${await $.session.root()}/.run/mod/${await $.session.id()}/agents/${agentId}.json`, factsText(f, me.ctx, at)).catch(quiet)
    }
    // 3.24 T17g: a shown agent's start line at its first request, its facts carrying this request's model and effort, so
    // its first titled run names them even if it never feeds (feed.ts startsLine).
    if (first !== undefined && agentId !== undefined && startsLine(first.type)) {
      facts.set(agentId, { ...factsOf(agentId), model: e.model, effort })
      await feedOut(startText(factsOf(agentId).description))
    }
    const blocks: StepBlock[] = [] // the step's blocks in order, for the feed (3.24 T13n)
    let sawTool = false
    let thinking = ''
    let text = '' // text since the last tool chunk: an approach note when a tool chunk follows
    let approach = '' // the text before the turn's first tool call (T5c), '' when this step has none
    for await (const chunk of stream) {
      if (me !== undefined) {
        const lastBlock = blocks[blocks.length - 1]
        if ((chunk.kind === 'thinking' || chunk.kind === 'text') && lastBlock !== undefined && lastBlock.kind === chunk.kind && lastBlock.index === chunk.index) {
          lastBlock.text += chunk.text
        } else if (chunk.kind === 'thinking' || chunk.kind === 'text') {
          blocks.push({ kind: chunk.kind, index: chunk.index, text: chunk.text })
        } else if (chunk.kind === 'tool') {
          blocks.push({ kind: 'tool', index: chunk.index, id: chunk.id, handback: chunk.name === HANDBACK })
        }
        if (chunk.kind === 'thinking') {
          thinking += chunk.text
        } else if (chunk.kind === 'text') {
          text += chunk.text
        } else if (chunk.kind === 'tool' && chunk.name !== HANDBACK) {
          sawTool = true
          if (me.noted !== e.turnId) {
            me.noted = e.turnId
            approach = text.trim()
          }
          text = ''
        } else if (chunk.kind === 'tool') {
          sawTool = true // the text before the hand-back is no feed line (feedPieces)
        }
      }
      yield chunk
    }
    const result = await stream.result
    const usage = result.usage as StepUsage | null
    if (agentId === undefined || me === undefined) {
      return result
    }
    const ping = e.index === 0 && isPingStep(result.answer, result.toolUses)
    // 3.24 T13n: the narration blocks by their signatures (the probe: the api messages end on this step's message here).
    const narration = feedOn && sawTool ? narrationIndexes(await $.session.messages({ agentId, as: 'api' }).catch(() => null), blocks) : new Set<number>()
    for (const piece of sawTool ? feedPieces(blocks, narration, !ping) : []) {
      await feedOut(piece)
    }
    me.ctx = usage === null ? me.ctx : ctxOf(usage)
    const now = Date.now()
    const f0 = factsOf(agentId)
    const f = { ...f0, model: e.model, effort, usage: result.usage, at: now, note: approach === '' ? f0.note : noteLines(approach) }
    facts.set(agentId, f)
    const root = await $.session.root()
    const sid = await $.session.id()
    // 3.22 T8f: the agent's last-step facts; the router stamps model and effort on its answer from them (T5c: and more).
    await $.fs.write(`${root}/.run/mod/${sid}/agents/${agentId}.json`, factsText(f, me.ctx, now)).catch(quiet)
    // T5c: an expert's or a coder's approach note is a `note` journal entry (the partner's text is its answer).
    const noter = approach === '' ? '' : noteWho(f.type, f.description)
    if (noter !== '') {
      const [date, time] = stamp(now)
      journalSeq += 1
      const body = entryText({ ts: new Date(now).toISOString(), session: sid, agentId, model: e.model, turnId: e.turnId }, approach)
      await $.fs.write(`${root}/${entryPath(date, time, journalSeq, 'w', noter, 'note')}`, body).catch(quiet)
    }
    if (debugDir !== '') {
      await $.fs.write(...ev('step', { agentId, model: f.model, effort: f.effort, usage: f.usage, thinking: thinking.length, narration: narration.size })).catch(quiet)
    }
    if (thinking.trim() !== '') {
      const row = (await $.agent.list().catch(() => [])).find(a => a.id === agentId)
      const type = row?.type ?? ''
      const partnerId = type === '' ? partnerIdOf(await $.fs.read(`${root}/.run/mod/${sid}/state.json`).catch(() => null)) : ''
      const ms = Date.now()
      const [date, time] = stamp(ms)
      journalSeq += 1
      const rel = entryPath(date, time, journalSeq, 'w', whoOf(type, agentId, partnerId, row === undefined ? f.description : hintOf(row.description).desc), 'thinking')
      const text = entryText({ ts: new Date(ms).toISOString(), session: sid, agentId, model: e.model, effort: f.effort, turnId: e.turnId }, thinking.trim())
      await $.fs.write(`${root}/${rel}`, text).catch(quiet)
    }
    return result
  })

  // Every agent's tool calls: the last one per agent goes to the stream facts (3.23 T13d.c3: no `↳` line).
  on('tool.call', async ($, e, next) => {
    if (e.agentId !== undefined && (await read($, activeAtom).catch(() => false))) {
      const args = argsOf(e as unknown as Record<string, unknown>)
      const me = agents.get(e.agentId)
      if (me !== undefined) {
        me.planning = planningAfter(me.planning === true, e.tool, args) // the Skill line before, the plan line in planning
      }
      const f0 = factsOf(e.agentId)
      // T5c: the last three steps, `Tool short-arg`, into the facts file at once (the step's own write came before its calls).
      const steps = e.tool === HANDBACK ? f0.steps : [...f0.steps, `${e.tool} ${argLine(args, 60)}`.trim()].slice(-3)
      const f = { ...f0, tool: e.tool, args: argSummary(args), steps }
      facts.set(e.agentId, f)
      if (steps !== f0.steps) {
        const root = await $.session.root()
        const sid = await $.session.id()
        await $.fs.write(`${root}/.run/mod/${sid}/agents/${e.agentId}.json`, factsText(f, me?.ctx ?? 0, Date.now())).catch(quiet)
      }
      if (debugDir !== '') {
        await $.fs.write(...ev('tool', { agentId: e.agentId, tool: f.tool, args: f.args })).catch(quiet)
      }
      if (e.tool === 'Bash' && args['run_in_background'] === true) {
        // 3.28.1 T15: a background shell is recorded under its agent until its completion notice (session.append).
        const r = await next(e)
        const res = r.result as { backgroundTaskId?: unknown } | undefined
        const id = typeof res?.backgroundTaskId === 'string' ? res.backgroundTaskId : (/ID: (\w+)/.exec(r.text ?? '')?.[1] ?? '')
        if (id !== '') {
          const agent = e.agentId
          const type = f.type !== '' ? f.type : ((await $.agent.list().catch(() => [])).find(a => a.id === agent)?.type ?? '')
          const rec: ShellRec = { id, name: shellName(String(args['command'] ?? '')), at: Date.now(), type }
          await shellsEdit($, s => ({ ...s, [agent]: [...(s[agent] ?? []).filter(x => x.id !== id), rec] }))
        }
        return r
      }
      if (e.tool === 'SendMessage' || e.tool === HANDBACK) {
        // 3.28.1 T15: the agent's report: its dead shells are cleared; a contract (`STATUS:`) clears all of them.
        const msg = String(args['message'] ?? '')
        const agent = e.agentId
        await shellsEdit($, s => (s[agent] === undefined ? null
          : { ...s, [agent]: /^\s*STATUS:/.test(msg) ? [] : (s[agent] ?? []).filter(x => x.exit === undefined) }))
      }
      if (EDIT_TOOLS.includes(e.tool)) {
        // 3.24 T8b: a successful file edit by Claude, an expert or a coder is one `edit` feed record; the band's line names it.
        const r = await next(e)
        const root = await $.session.root()
        const sid = await $.session.id()
        const stateText = await $.fs.read(`${root}/.run/mod/${sid}/state.json`).catch(() => null)
        const row = f.type !== '' ? null : (await $.agent.list().catch(() => [])).find(a => a.id === e.agentId)
        const type = row === null ? f.type : (row?.type ?? '')
        const desc = row === null ? f.description : hintOf(row?.description ?? '').desc
        const edit = editsShown(type, partnerIdOf(stateText) === e.agentId) ? editOf(e.tool, args, r, root) : null
        if (edit !== null) {
          const at = Date.now()
          const text = editHeader(edit)
          feedSeq += 1
          const body = feedRecord({ agent: e.agentId, type, desc, planning: me?.planning === true, text, at, kind: 'edit', edit })
          await $.fs.write(`${feedDirOf(stateText, root, sid)}/in/${feedName(at, feedSeq)}`, body).catch(quiet)
          const f2 = { ...factsOf(e.agentId), line: text }
          facts.set(e.agentId, f2)
          await $.fs.write(`${root}/.run/mod/${sid}/agents/${e.agentId}.json`, factsText(f2, me?.ctx ?? 0, at)).catch(quiet)
        }
        return r
      }
    }
    return next(e)
  })

  // 3.28.1 T15: a background shell's completion notice (a row in its agent's conversation): a clean end removes its
  // record; a failed one keeps it with its exit code (shown dead) until the agent reports.
  on('session.append', async ($, e, next) => {
    const r = await next(e)
    try {
      const content = (e.message as { content?: unknown }).content
      const text = typeof content === 'string' ? content
        : (Array.isArray(content) ? content : []).map(b => (typeof b === 'object' && b !== null && (b as { type?: unknown }).type === 'text' ? String((b as { text?: unknown }).text ?? '') : '')).join('\n')
      const end = text.includes('<task-notification>') ? shellEnd(text) : null
      if (end !== null && (await read($, activeAtom).catch(() => false))) {
        await shellsEdit($, s => {
          if (!Object.values(s).some(l => l.some(x => x.id === end.id))) {
            return null
          }
          return Object.fromEntries(Object.entries(s).map(([a, l]) => [a, end.exit === undefined
            ? l.filter(x => x.id !== end.id) : l.map(x => (x.id === end.id ? { ...x, exit: end.exit } : x))]))
        })
      }
    } catch {
      // best effort: the row is kept whatever the record does
    }
    return r
  })

  // The six tools: a good call becomes one request file in the router's inbox, which its one-second tick drains.
  for (const name of TOOL_NAMES) {
    on('tool.call', { tool: `mcp__pa-watch__${name}` }, async ($, e) => {
      const input = argsOf(e as unknown as Record<string, unknown>)
      const bad = validateInput(name, input)
      if (bad !== null) {
        return { result: `error: ${bad}` }
      }
      const ms = Date.now()
      reqSeq += 1
      const file = requestName(ms, reqSeq, name)
      const id = file.slice(0, -'.json'.length)
      const failed = await (async (): Promise<string> => {
        const root = await $.session.root()
        const sid = await $.session.id()
        await $.fs.write(`${root}/.run/mod/${sid}/inbox/${file}`, requestBody(id, name, e.agentId ?? '', input, ms))
        return ''
      })().catch((err: unknown) => String(err).slice(0, 200))
      if (failed !== '') {
        return { result: `error: the request was not queued (${failed})` }
      }
      if (debugDir !== '') {
        await $.fs.write(...ev('request', { id, kind: name, caller: e.agentId ?? '' })).catch(quiet)
      }
      return { result: queuedReply(name, id) }
    })
  }
}
