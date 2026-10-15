// The router's live feed (3.23 T13d.c1b; brief .run/t13d-c1.md, "The feed contract"): pure parts. The watcher drops each
// agent utterance into `<feed.dir>/in/`; the feed script (mod/pa/feed/pa_feed.py) numbers it into `log.jsonl` and prints
// it; Claude Code delivers each printed batch as a `task-notification` turn ("pa feed"). The router collects the records
// at prompt.submit (each queued notification passes there on its own), fills any gap in the numbering from the log, and
// answers the next feed turn with every pending record: a bold title with the role's square when the speaker changes,
// then each line after an em space. register.tsx spells the `$` calls.
import { borderOf, type CardRole, type CardStyle, claudeCard, parseCard, ROLE_HEX, type Sender, SQUARE } from './card'
import { parentOf, xmlText } from './route'
import type { Child, FeedEdit, FeedLife, FeedPending, FeedRecord, FeedRun, LoopTask } from '../types'

export type { FeedDrawn, FeedEdit, FeedLife, FeedPending, FeedRecord, FeedRun } from '../types'

/** What one `<event>` line holds: a record, a nudge, the launcher's error (3.23 T13d.c2: the feed cannot start), a
 * record's line Claude Code cut ("…(truncated)", 3.23 T13d.c6a: its number, read from the log at once), or Claude Code's
 * own bracketed line (housekeeping). */
export type FeedEvent =
  | { kind: 'record'; rec: FeedRecord }
  | { kind: 'nudge' }
  | { kind: 'error'; error: string }
  | { kind: 'cut'; n: number }
  | { kind: 'note'; line: string }

export const emptyFeed = (): FeedPending => ({ pending: [], drawn: null, speaker: '' })

/** A nudge file's body: the feed script prints it as one event, so a feed turn follows and draws what is pending. */
export const NUDGE = '{"nudge":true}\n'

/** A nudge file's name in `in/`: `<13-digit ms>-9<5-digit seq>.json` (the 9 keeps the router's names apart from the
 * watcher's at the same ms; the names still sort by time). */
export function nudgeName(ms: number, seq: number, lead = '9'): string {
  return `${String(Math.max(0, Math.floor(ms))).padStart(13, '0')}-${lead}${String(seq % 100_000).padStart(5, '0')}.json`
}

/** 3.23 T13d.c3: the start announcement, one feed record the router writes when the loop spawns a task's expert (its id
 * and type, so the label is `Expert · T<n>`): `Starting <id>: <title>`. Zero tokens. Named `nudgeName(ms, seq, '8')`. */
export function startRecord(o: { agent: string; type: string; id: string; title: string; at: number }): string {
  const text = `Starting ${o.id}${o.title === '' ? '' : `: ${o.title}`}`
  return `${JSON.stringify({ agent: o.agent, type: o.type, desc: `${o.id} ${o.title}`.trim(), planning: false, text, at: o.at })}\n`
}

/** 3.25 T2: the close's closed line as the mod's own record (agent `mod`, type `pa`, so the label is `pa`); no agent closes. */
export function modRecord(o: { text: string; at: number }): string {
  return `${JSON.stringify({ agent: 'mod', type: 'pa', desc: '', planning: false, text: o.text, at: o.at })}\n`
}

/** 3.24 T13B (decision 3): a reply of Claude's that came with no hand-back, as a feed record the router writes (named
 * `nudgeName(ms, seq, '7')`) instead of the `‹pa reply <n>›` prompt, whose "Prompt from the pa plugin" row no render hook
 * hides; a feed notification's row is hidden, as every feed turn's. `reply` is the marker's number (`state.markers`: the
 * reply's journal file); type '' (the partner's: never in a developer message's feed block). Zero tokens.
 * 3.24 T17g: `shown`, the reply's model and effort (shownOf), for its card's title; omitted when ''. */
export function replyRecord(o: { agent: string; n: number; text: string; at: number; shown?: string }): string {
  const shown = o.shown === undefined || o.shown === '' ? {} : { shown: o.shown }
  return `${JSON.stringify({ agent: o.agent, type: '', desc: 'Claude', planning: false, text: o.text, at: o.at, reply: o.n, ...shown })}\n`
}

const str = (x: unknown): string => (typeof x === 'string' ? x : '')
const count = (x: unknown): number => (typeof x === 'number' && Number.isInteger(x) && x >= 0 ? x : 0)

/** 3.24 T8b: a record's `edit` (the watcher's edit card, mod/pa-watch/hooks/edit.ts), undefined when absent or not one. */
export function editOfRecord(x: unknown): FeedEdit | undefined {
  if (typeof x !== 'object' || x === null || Array.isArray(x)) {
    return undefined
  }
  const e = x as Record<string, unknown>
  if (str(e['path']) === '') {
    return undefined
  }
  const hunks = Array.isArray(e['hunks']) ? e['hunks'].filter((h): h is string => typeof h === 'string') : []
  const label = str(e['label'])
  return { path: str(e['path']), created: e['created'] === true, add: count(e['add']), del: count(e['del']), hunks, more: count(e['more']), ...(label === '' ? {} : { label }) }
}

/** 3.24 T8b: an edit card's header, native: `Updated <path>` and ` (+N -M)`, or `Created <path>` and ` (N lines)`; 3.24
 * T13B: the record's `label` in place of the verb and path (a plan draft: `Plan drafted (58 lines)`). */
export function editHead(e: FeedEdit): { verb: string; counts: string } {
  return e.created
    ? { verb: e.label ?? `Created ${e.path}`, counts: ` (${e.add} ${e.add === 1 ? 'line' : 'lines'})` }
    : { verb: e.label ?? `Updated ${e.path}`, counts: ` (+${e.add} -${e.del})` }
}

/** 3.24 T8b: the stored text of an edit card (the phone): the header line, then a ```diff fence of the hunks (a created
 * file's lines as `+` lines), then `… +N lines` when rows were left out. 3.24 T13p: every line quoted (`quote`), the
 * fence inside the quote. */
export function editText(e: FeedEdit): string {
  const h = editHead(e)
  const body = e.hunks.flatMap(b => b.split('\n').map(l => (e.created ? `+${l}` : l)))
  const out = [`${h.verb}${h.counts}`]
  if (body.length > 0) {
    out.push('```diff', ...body, '```')
  }
  if (e.more > 0) {
    out.push(`… +${e.more} lines`)
  }
  return out.map(quote).join('\n')
}

/** A record of one JSON line (an event line or a `log.jsonl` line), null when it is not one. */
export function parseFeedLine(line: string): FeedRecord | null {
  let o: unknown
  try {
    o = JSON.parse(line)
  } catch {
    return null
  }
  if (typeof o !== 'object' || o === null || Array.isArray(o)) {
    return null
  }
  const r = o as Record<string, unknown>
  const n = r['n']
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 1 || typeof r['text'] !== 'string') {
    return null
  }
  const edit = editOfRecord(r['edit'])
  const reply = r['reply']
  return {
    n, agent: str(r['agent']), type: str(r['type']), desc: str(r['desc']), planning: r['planning'] === true, text: r['text'],
    at: typeof r['at'] === 'number' ? r['at'] : 0, ...(edit === undefined ? {} : { edit }),
    ...(typeof reply === 'number' && Number.isInteger(reply) && reply >= 1 ? { reply } : {}),
    ...(typeof r['shown'] === 'string' && r['shown'] !== '' ? { shown: r['shown'] } : {}), // 3.24 T17g
  }
}

/** The events of a feed notification: every line inside its `<event>` blocks, blank lines skipped; 3.24 T13r: the block
 * unescaped once (route.ts xmlText: Claude Code escapes `&`, `<`, `>` in it), so a record's text is the agent's own. */
export function feedEvents(text: string): FeedEvent[] {
  const out: FeedEvent[] = []
  for (const m of text.matchAll(/<event>([\s\S]*?)<\/event>/g)) {
    for (const raw of xmlText(m[1] ?? '').split('\n')) {
      const line = raw.trim()
      if (line === '') {
        continue
      }
      if (line.startsWith('{')) {
        let o: unknown = null
        try {
          o = JSON.parse(line)
        } catch {
          o = null
        }
        const r = typeof o === 'object' && o !== null ? (o as Record<string, unknown>) : null
        if (r !== null && r['pa'] === 'feed' && r['nudge'] === true) {
          out.push({ kind: 'nudge' })
          continue
        }
        if (r !== null && r['pa'] === 'feed' && typeof r['error'] === 'string') {
          out.push({ kind: 'error', error: r['error'] })
          continue
        }
        const rec = r !== null && r['pa'] === 'feed' ? parseFeedLine(line) : null
        if (rec !== null) {
          out.push({ kind: 'record', rec })
          continue
        }
        const cut = o === null ? /^\{\s*"pa"\s*:\s*"feed"\s*,\s*"n"\s*:\s*([0-9]+)/.exec(line) : null
        if (cut !== null && Number(cut[1]) >= 1) {
          out.push({ kind: 'cut', n: Number(cut[1]) })
          continue
        }
      }
      out.push({ kind: 'note', line })
    }
  }
  return out
}

/** The atom with `recs` added: a number drawn already or pending already is dropped; pending stays in `n` order. */
export function addRecords(f: FeedPending, recs: readonly FeedRecord[]): FeedPending {
  const have = new Set(f.pending.map(r => r.n))
  const add: FeedRecord[] = []
  for (const r of recs) {
    if ((f.drawn !== null && r.n <= f.drawn) || have.has(r.n)) {
      continue
    }
    have.add(r.n)
    add.push(r)
  }
  return add.length === 0 ? f : { ...f, pending: [...f.pending, ...add].sort((a, b) => a.n - b.n) }
}

/** Numbers skipped below the newest pending one: from the last drawn + 1 (before any draw this load, from the oldest
 * pending, so a reload never redraws the log), each not pending. */
export function missingNs(f: FeedPending): number[] {
  const top = f.pending[f.pending.length - 1]?.n
  if (top === undefined) {
    return []
  }
  const from = f.drawn === null ? (f.pending[0]?.n ?? top) : f.drawn + 1
  const have = new Set(f.pending.map(r => r.n))
  const out: number[] = []
  for (let n = from; n < top; n += 1) {
    if (!have.has(n)) {
      out.push(n)
    }
  }
  return out
}

/** The records of `log.jsonl` whose numbers are in `ns`. */
export function fromLog(text: string, ns: readonly number[]): FeedRecord[] {
  const want = new Set(ns)
  const out: FeedRecord[] = []
  for (const line of text.split('\n')) {
    const r = line.trim() === '' ? null : parseFeedLine(line.trim())
    if (r !== null && want.has(r.n)) {
      want.delete(r.n)
      out.push(r)
    }
  }
  return out
}

/** The agents `$.agent.list()` gives, as the sender logic reads them. */
type Listed = { id: string; type?: string; parentId?: string }

/**
 * Who an agent is to the loop (the cards' sender logic, shared with the feed): the loop task's expert, a coder under it
 * (any depth, a `coder*` or `pa-coder` type), a thinker (`pa-thinker`, or an old `thinker`/`deep-thinker`), else other. `coder` is the coder's
 * `c<k>` the card text names ('' none).
 */
export function senderOf(id: string, lt: LoopTask | null, children: Record<string, Child>, list: readonly Listed[], coder = ''): Sender {
  const typeOf = (x: string): string => children[x]?.type ?? list.find(a => a.id === x)?.type ?? ''
  const sender: Sender = { role: 'other', task: lt?.id ?? '', coder }
  if (lt !== null && lt.agentId !== '' && id === lt.agentId) {
    sender.role = 'expert'
    sender.task = lt.id
  } else if (lt !== null && lt.agentId !== '') {
    let p = parentOf(children, id, list)
    for (let hop = 0; p !== null && p !== lt.agentId && hop < 8; hop += 1) {
      p = parentOf(children, p, list)
    }
    sender.role = p === lt.agentId && /^(pa-)?coder/.test(typeOf(id)) ? 'coder' : 'other'
  }
  if (sender.role === 'other' && ['pa-thinker', 'thinker', 'deep-thinker'].includes(typeOf(id))) { // 3.25 T6b: one agent per role
    sender.role = 'thinker'
  }
  return sender
}

/** A record's speaker: its card role (null: no square) and label. */
export type Speaker = { role: CardRole | null; label: string }

const titled = (name: string, task: string): string => (task === '' ? name : `${name} · ${task}`)

/** The label of a record's agent: Claude or Planning (the partner, by id or by the watcher's '' type), Planning (the
 * planners, `pa-planner` included), the loop's expert or coder, Thinker, Curator or Auditor (3.24 T13B), else the agent's type. */
export function speakerOf(rec: FeedRecord, partnerId: string, sender: Sender): Speaker {
  if ((partnerId !== '' && rec.agent === partnerId) || rec.type === '') {
    return rec.planning ? { role: 'planning', label: 'Planning' } : { role: 'claude', label: 'Claude' }
  }
  if (rec.type === 'planner-gen' || rec.type === 'planner-phase' || rec.type === 'pa-planner') { // 3.24 T17b: pa-planner
    return { role: 'planning', label: 'Planning' }
  }
  if (sender.role === 'expert') {
    return { role: 'expert', label: titled('Expert', sender.task) }
  }
  if (sender.role === 'coder') {
    return { role: 'coder', label: titled('Coder', sender.task) }
  }
  if (sender.role === 'thinker' || ['pa-thinker', 'thinker', 'deep-thinker'].includes(rec.type)) {
    return { role: 'thinker', label: 'Thinker' }
  }
  if (rec.type === 'memory-curator') {
    return { role: 'curator', label: 'Curator' }
  }
  if (rec.type === 'auditor') {
    return { role: 'auditor', label: 'Auditor' }
  }
  return { role: null, label: rec.type }
}

/** 3.24 T17b: a model id as the developer reads it: `claude-opus-5-5` → `Opus 5.5` (a date or a `[1m]` tail dropped);
 * anything else as given. */
export function modelName(id: string): string {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(?:\[[^\]]*\])?$/.exec(id.trim())
  const fam = m?.[1] ?? ''
  if (m === null || fam === '') {
    return id.trim()
  }
  return `${fam.charAt(0).toUpperCase()}${fam.slice(1)} ${m[2] ?? ''}${m[3] === undefined ? '' : `.${m[3]}`}`
}

/** 3.24 T17b: an agent's model and effort as its first titled run shows them (`Opus 5.5 · max`); '' with no model. */
export function shownOf(model: string, effort: string): string {
  return model === '' ? '' : effort === '' ? modelName(model) : `${modelName(model)} · ${effort}`
}

/** 3.24 T17b: whether a record's agent is announced (its model and effort shown once): any agent but Claude (the
 * partner by id or the watcher's '' type) and Claude's replies. */
export const announces = (rec: FeedRecord, partnerId: string): boolean =>
  rec.reply === undefined && rec.type !== '' && rec.agent !== '' && !(partnerId !== '' && rec.agent === partnerId)

/** 3.24 T13p: a stored feed line as a blockquote line (`> <line>`, a bare `>` for a blank line), so the phone renders the
 * run's markdown. Was `INDENT`, an em space (U+2003) before each line: CommonMark does not take it as indentation, so
 * headings, lists and fences stayed literal on the phone. */
export const quote = (line: string): string => (line === '' ? '>' : `> ${line}`)

/**
 * The stored text of a feed turn (what the phone shows): per run of one speaker, the title `**<square> <label>**` only
 * when the speaker differs from `last`, then the run as a blockquote (3.24 T13p: `quote` on every line, a bare `>`
 * between two records, a blank line before each run so the last quote ends). Returns the text, the
 * last speaker's key and the runs (3.23 T13d.c2: what the terminal draws; a first run continuing `last` is untitled).
 * 3.24 T8b: an edit record is a run of its own (untitled when it continues the speaker), stored as editText.
 * 3.24 T13B: a reply record is a run of its own, always titled, `card` set, stored as its Claude card (claudeCard); the
 * record after it is titled again. 3.24 T13E: a reply record whose text is a card already (the loop's accepted card) is
 * stored and drawn as that card, not wrapped in Claude's.
 * 3.24 T17b: `shown` (by agent id) is added to an agent's title (`Expert · T1 · Opus 5.5 · medium`); `announced` lists
 * the agents whose title carried it.
 */
export function feedText(items: readonly { rec: FeedRecord; who: Speaker }[], last: string, shown: Readonly<Record<string, string>> = {}): { text: string; speaker: string; runs: FeedRun[]; announced: string[] } {
  const out: string[] = []
  const runs: FeedRun[] = []
  const announced: string[] = []
  let speaker = last
  for (const { rec, who } of items) {
    if (rec.reply !== undefined) {
      if (out.length > 0) {
        out.push('')
      }
      const own = parseCard(rec.text)
      const mine = rec.shown ?? '' // 3.24 T17g: the reply's model and effort in its title
      out.push(own === null ? claudeCard(rec.text, mine) : rec.text)
      const body = own === null ? rec.text : own.body
      runs.push({ label: own === null ? (mine === '' ? 'Claude' : `Claude · ${mine}`) : own.title, role: own === null ? 'claude' : own.role, titled: true, lines: body === '' ? [] : body.split('\n').map(l => l.replace(/\s+$/, '')), card: true })
      speaker = `${rec.agent}\treply`
      continue
    }
    const key = `${rec.agent}\t${who.label}`
    if (key !== speaker) {
      if (out.length > 0) {
        out.push('') // 3.24 T13p: else the title is the last quote's lazy continuation
      }
      const extra = shown[rec.agent] ?? ''
      const label = extra === '' || announced.includes(rec.agent) ? who.label : `${who.label} · ${extra}`
      if (label !== who.label) {
        announced.push(rec.agent)
      }
      out.push(`**${who.role === null ? '' : `${SQUARE[who.role]} `}${label}**`)
      speaker = key
      runs.push({ label, role: who.role, titled: true, lines: [] })
    } else if (runs.length === 0 || rec.edit !== undefined || runs[runs.length - 1]?.edit !== undefined || runs[runs.length - 1]?.card === true) {
      if (out.length > 0) {
        out.push('')
      }
      runs.push({ label: who.label, role: who.role, titled: false, lines: [] })
    }
    if (rec.edit !== undefined) {
      const run = runs[runs.length - 1]
      if (run !== undefined) {
        run.edit = rec.edit
      }
      out.push(editText(rec.edit))
      continue
    }
    // 3.24 T13r: a run's records are drawn as one Markdown block, so a blank line between two keeps each its own paragraph.
    const cur = runs[runs.length - 1]
    if (cur !== undefined && cur.lines.length > 0) {
      cur.lines.push('')
      out.push(quote(''))
    }
    for (const line of rec.text.split('\n')) {
      out.push(quote(line.replace(/\s+$/, '')))
      cur?.lines.push(line.replace(/\s+$/, ''))
    }
  }
  return { text: out.join('\n'), speaker, runs, announced }
}

/** How the terminal draws one feed run (3.23 T13d.c2), the cards' look: a note card's border under `style` (null:
 * plain), the role's hex (none for an unknown type), the title (the bold label, `● <label>` under plain; '' on a
 * continuation run), the lines. */
export function feedLook(run: FeedRun, style: CardStyle): { border: 'quote' | 'round' | null; color: string | undefined; title: string; lines: string[]; edit?: FeedEdit; card?: boolean } {
  const border = borderOf(style, false)
  const title = !run.titled ? '' : border === null ? `● ${run.label}` : run.label
  return {
    border, color: run.role === null ? undefined : ROLE_HEX[run.role], title, lines: run.lines, ...(run.edit === undefined ? {} : { edit: run.edit }),
    ...(run.card === true ? { card: true } : {}),
  }
}

/** The feed records a developer message carries at most (3.23 T13d.c2). */
export const FEED_BLOCK_MAX = 40

/** The records of `log.jsonl` the developer saw and Claude did not (3.23 T13d.c2): drawn (`n` <= `drawn`; none before
 * the first draw of this load), after Claude's last answer (`at` > `since`), not the partner's own (by id, or the
 * watcher's '' type), the latest FEED_BLOCK_MAX in `n` order. */
export function unseenFeed(logText: string, drawn: number | null, since: number, partnerId: string): FeedRecord[] {
  if (drawn === null) {
    return []
  }
  const out: FeedRecord[] = []
  for (const line of logText.split('\n')) {
    const r = line.trim() === '' ? null : parseFeedLine(line.trim())
    if (r !== null && r.n <= drawn && r.at > since && r.type !== '' && !(partnerId !== '' && r.agent === partnerId)) {
      out.push(r)
    }
  }
  return out.sort((a, b) => a.n - b.n).slice(-FEED_BLOCK_MAX)
}

/** The feed block's cap on one record's text, in characters (3.23 T13d.c7). */
export const FEED_BLOCK_CAP = 400

/** The feed block on a developer message (3.23 T13d.c2): a first line naming the log, then `<label>: <line>` per
 * non-blank text line; '' when there is nothing. */
export function feedBlock(items: readonly { rec: FeedRecord; who: Speaker }[], dir: string): string {
  // 3.23 T13d.c7: a size guard for Claude's input: each record's text cut at FEED_BLOCK_CAP chars (`…` when cut); the
  // screen shows statements whole.
  const cut = (t: string): string => (t.length > FEED_BLOCK_CAP ? `${t.slice(0, FEED_BLOCK_CAP)}…` : t)
  const lines = items.flatMap(({ rec, who }) =>
    cut(rec.text).split('\n').map(l => l.trim()).filter(l => l !== '').map(l => `${who.label}: ${l}`))
  if (lines.length === 0) {
    return ''
  }
  return [`‹pa› the feed you have not seen (since your last answer; older lines: ${dir}/log.jsonl):`, ...lines].join('\n')
}

/** The plain line the developer gets when the launcher reports the feed cannot start (3.23 T13d.c2). */
export const feedErrorLine = (error: string): string =>
  error === 'no python' ? "The live feed could not start: no Python found (pa.json's python key)." : `The live feed could not start: ${error}.`

// 3.23 T13d.c1c: the feed never stays dead. Claude Code never restarts a monitor (not on a reload, not after it dies), so
// the router re-arms it through the Monitor tool at its end notice, an expiry or stop line, or a heartbeat (`alive`'s
// `beat`, rewritten every 5 s by the feed script) older than FEED_STALE_MS; only in a session that had a feed (a fresh
// heartbeat or a feed notification seen) or FEED_WAIT_MS after the first developer prompt from the composer or the
// bridge with no heartbeat at all (headless `-p` sessions never arm); at most RESTART_MAX in RESTART_WINDOW_MS.
export const FEED_DESC = 'pa feed'
export const FEED_TIMEOUT_MS = 1_800_000
export const FEED_BEAT_MS = 5_000
export const FEED_STALE_MS = 20_000
export const FEED_WAIT_MS = 30_000
export const RESTART_MAX = 6
export const RESTART_WINDOW_MS = 600_000

export const emptyLife = (): FeedLife => ({ seen: false, devAt: null, restarts: [], calling: false, waiting: false, error: '' })

/** `alive`'s `beat` (ms), null when the file is absent or not the heartbeat. */
export function beatOf(text: string | null): number | null {
  if (text === null) {
    return null
  }
  try {
    const o = JSON.parse(text) as unknown
    const beat = typeof o === 'object' && o !== null ? (o as Record<string, unknown>)['beat'] : null
    return typeof beat === 'number' ? beat : null
  } catch {
    return null
  }
}

/** The life with a fresh heartbeat counted as a feed seen. */
export const seenBeat = (life: FeedLife, beat: number | null, now: number): FeedLife =>
  !life.seen && beat !== null && now - beat <= FEED_STALE_MS ? { ...life, seen: true } : life

/** Why a feed notification says its monitor is dead: `end` (the end notice: a `<status>` and `Monitor "pa feed" …`),
 * `expired` or `stopped` (Claude Code's bracketed line in an event); '' when it does not. */
export function deathOf(text: string): string {
  if (/<status>[^<]*<\/status>/.test(text) && /<summary>Monitor "pa feed"/.test(text)) {
    return 'end'
  }
  for (const x of feedEvents(text)) {
    if (x.kind === 'note' && /^\[Monitor expired/.test(x.line)) {
      return 'expired'
    }
    if (x.kind === 'note' && /^\[Monitor stopped/.test(x.line)) {
      return 'stopped'
    }
  }
  return ''
}

/** Why the heartbeat says the feed is dead at `now`: `stale` (a feed seen, no beat for FEED_STALE_MS), `none` (no feed
 * ever, FEED_WAIT_MS after the first developer prompt); '' otherwise. A restart counts as a beat, so it gets its time.
 * 3.23 T13d.c2: never after the feed's error; `none` never while a restart's Monitor has not ended (its end notice or
 * expiry clears `waiting`), so a feed that cannot start does not arm one idle Monitor after another. */
export function staleOf(life: FeedLife, beat: number | null, now: number): string {
  const last = Math.max(beat ?? 0, life.restarts[life.restarts.length - 1] ?? 0)
  if (life.error !== '') {
    return ''
  }
  if (life.seen) {
    return now - last > FEED_STALE_MS ? 'stale' : ''
  }
  return !life.waiting && life.devAt !== null && now - Math.max(life.devAt, last) >= FEED_WAIT_MS ? 'none' : ''
}

/** Whether a restart may run at `now`: not while one runs, not within FEED_STALE_MS of the last (the old monitor's late
 * notices), fewer than RESTART_MAX in RESTART_WINDOW_MS. `recent` is the window's restarts. */
export function restartGate(life: FeedLife, now: number): { ok: boolean; why: string; recent: number[] } {
  const recent = life.restarts.filter(t => now - t < RESTART_WINDOW_MS)
  const last = recent[recent.length - 1]
  const why = life.calling ? 'calling' : last !== undefined && now - last < FEED_STALE_MS ? 'grace' : recent.length >= RESTART_MAX ? 'limit' : ''
  return { ok: why === '', why, recent }
}

const fwd = (p: string): string => p.split(String.fromCharCode(92)).join('/').replace(/[/]+$/, '') // the loader's parser trips on a backslash regex

/** The restart's Monitor command: the launcher with the project and the feed folder as an absolute path, forward
 * slashes. `pluginRoot` may name `.claude-plugin` (the folder holding plugin.json); the launcher is beside it. */
export function feedCommand(pluginRoot: string, root: string, dir: string): string {
  const plug = fwd(pluginRoot).replace(/\/\.claude-plugin$/, '')
  const d = fwd(dir)
  const abs = /^([A-Za-z]:)?\//.test(d) ? d : `${fwd(root)}/${d}`
  return `bash "${plug}/feed/pa_feed.sh" "${fwd(root)}" --dir "${abs}"`
}
