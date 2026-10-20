// The router's cards (3.23-autopilot.md, "T13 display and voice"): the answers of locally answered main-loop turns that
// show a loop agent's note or finish. Pure, no `$`. The stored text is clean markdown, because Remote Control shows the
// stored text (no render hook runs on the phone): a bold title led by the role's colored square, the body, then any diff
// in a ```diff fence. register.tsx keeps a registry of the texts it answered and draws only those, in the role's color.
import { type CoderReturn, type ExpertContract, parseCoderReturn, parseExpertContract } from './contract'
import { handbackReport } from './route'
import type { Band, BandRow, Shell, Work } from '../types'
import { RESTART_LINE, SPINNER } from './loop'

export type CardRole = 'claude' | 'planning' | 'expert' | 'coder' | 'thinker' | 'curator' | 'auditor'

/** The title's square by role (no teal square exists: the thinker takes the expert's blue; no pink one: the curator takes
 * the planners' purple, the steel-blue auditor the expert's blue). */
export const SQUARE: Record<CardRole, string> = {
  claude: '🟧', planning: '🟪', expert: '🟦', coder: '🟩', thinker: '🟦', curator: '🟪', auditor: '🟦',
}

/** The roles' hex colors (the feed's and the cards' palette). 3.24 T13B: the memory curator
 * pink, the auditor steel blue. */
export const ROLE_HEX: Record<CardRole, string> = {
  claude: '#d7875f', planning: '#af5fff', expert: '#0087ff', coder: '#00af00', thinker: '#00afaf',
  curator: '#d75faf', auditor: '#5f87af',
}

const LABEL: Record<CardRole, string> = {
  claude: 'Claude', planning: 'Planning', expert: 'Expert', coder: 'Coder', thinker: 'Thinker',
  curator: 'Curator', auditor: 'Auditor',
}

/** A card's text: `**<square> [✓ ]<Role>[ · <what>]**`, the body, then the diff in a ```diff fence when there is one. */
export function cardText(role: CardRole, what: string, body: string, opts: { done?: boolean; diff?: string } = {}): string {
  const title = `**${SQUARE[role]} ${opts.done === true ? '✓ ' : ''}${LABEL[role]}${what === '' ? '' : ` · ${what}`}**`
  const parts = [title, body.trim()].filter(p => p !== '')
  const diff = (opts.diff ?? '').replace(/\s+$/, '')
  if (diff !== '') {
    parts.push(`\`\`\`diff\n${diff}\n\`\`\``)
  }
  return parts.join('\n\n')
}

export type Card = { role: CardRole; done: boolean; title: string; body: string; diff: string }

const TITLE_RE = /^\*\*(\S+) (✓ )?(Claude|Planning|Expert|Coder|Thinker|Curator|Auditor)((?: · [^*\n]+)?)\*\*(?:\n|$)/

/** A card text split for drawing: its role, done mark, title (without the square), body and diff; null when no card. */
export function parseCard(text: string): Card | null {
  const m = TITLE_RE.exec(text)
  if (m === null) {
    return null
  }
  const role = (Object.keys(LABEL) as CardRole[]).find(r => LABEL[r] === m[3]) ?? 'claude'
  let rest = text.slice(m[0].length)
  let diff = ''
  const f = /(?:^|\n)```diff\n([\s\S]*?)\n```\s*$/.exec(rest)
  if (f !== null) {
    diff = f[1] ?? ''
    rest = rest.slice(0, f.index)
  }
  return { role, done: m[2] !== undefined, title: `${m[2] ?? ''}${m[3] ?? ''}${m[4] ?? ''}`, body: rest.trim(), diff }
}

/** The first meaningful line of a report: not blank, a fence, a heading mark or a `KEY:` contract line. */
export function firstLine(text: string): string {
  for (const raw of text.split(/\r?\n/)) {
    const l = raw.replace(/^\s*#+\s*/, '').trim()
    if (l !== '' && !/^(```|~~~)/.test(l) && !/^[A-Z][A-Z_ ]*\s*[:=]/.test(l)) {
      return l
    }
  }
  return ''
}

/** The expert's finish: its SUMMARY (else the report's first meaningful line) and the done mark (only for STATUS done,
 * so a blocked expert's card draws unfinished); no status, commit or path (3.23 T13d.c3: cards are announcements). */
export function expertFinish(task: string, c: ExpertContract, report: string): string {
  return cardText('expert', task, c.summary.trim() || firstLine(report), { done: c.status === 'done' })
}

/** 3.24 T8b: one file of a commit, from `git show --numstat`: lines added and removed (null, null for a binary file). */
export type FileStat = { path: string; add: number | null; del: number | null }

/** The files of `git show --numstat --format= <commit>` stdout, in its order; lines that are not `add<TAB>del<TAB>path` skipped. */
export function numstatOf(stdout: string): FileStat[] {
  const out: FileStat[] = []
  for (const line of stdout.split(/\r?\n/)) {
    const m = /^(\d+|-)\t(\d+|-)\t(.+)$/.exec(line)
    if (m !== null) {
      out.push({ path: m[3] ?? '', add: m[1] === '-' ? null : Number(m[1]), del: m[2] === '-' ? null : Number(m[2]) })
    }
  }
  return out
}

/** The file list a coder's finish card shows at most; the rest is `… +N files`. */
export const FILES_MAX = 20

/** 3.24 T8b: the coder's finish (round border under `mixed`, as the developer's Style D): `✓ Coder · T3 · c1, committed
 * <hash>`, its SUMMARY (else the report's first line), then its commit's files with +/− counts, so an edit made through
 * Bash or a script shows too. The done mark only for STATUS done. */
export function coderFinish(what: string, c: CoderReturn, report: string, files: readonly FileStat[]): string {
  const commit = /^[0-9a-f]{7,40}$/i.test(c.commit.trim()) ? c.commit.trim().slice(0, 7) : ''
  const list = files.slice(0, FILES_MAX).map(f => `- \`${f.path}\` ${f.add === null ? '(binary)' : `+${f.add} −${f.del ?? 0}`}`)
  if (files.length > FILES_MAX) {
    list.push(`- … +${files.length - FILES_MAX} files`)
  }
  const body = [c.summary.trim() || firstLine(report), list.join('\n')].filter(p => p !== '').join('\n\n')
  return cardText('coder', `${what}${commit === '' ? '' : `, committed ${commit}`}`, body, { done: c.status === 'done' })
}

/** 3.24 T13B (decision 8): every reply of Claude's (a hand-back turn's, a marker turn's, a feed reply record's) is an
 * orange Claude card, `**🟧 Claude**` then the reply. Retires 3.24's "white dot = Claude's replies, drawn natively".
 * 3.24 T17g: `shown`, the model and effort of the request that made the reply (feed.ts shownOf), joins the title
 * (`**🟧 Claude · Opus 5.5 · medium**`), so a /model or /effort change shows in the record. */
export const claudeCard = (reply: string, shown = ''): string => cardText('claude', shown, restartGap(reply))

/** 3.28.2 T17: one empty line before the mod's restart line (loop.ts RESTART_LINE) whenever the line before it isn't
 * empty, whatever Claude's spacing; the stored text carries it, so Remote Control matches the drawing. */
export function restartGap(reply: string): string {
  const at = reply.indexOf(RESTART_LINE)
  const before = at < 0 ? '' : reply.slice(0, at)
  if (before.trim() === '' || /\n[ \t]*\n[ \t]*$/.test(before)) {
    return reply
  }
  return `${before.replace(/[ \t]*\n?[ \t]*$/, '')}\n\n${reply.slice(at)}`
}

/** 3.24 T17g: the review release's accepted card, `✓ Expert · T<n> · accepted`, with the task's SUMMARY as a done card
 * carries it (the contract routed for the review; empty when it has none). Was an empty card. */
export const acceptedCard = (task: string, contract: string): string =>
  cardText('expert', `${task} · accepted`, parseExpertContract(contract)?.summary ?? '', { done: true })

/** The thinker's finish: only that it answered; Claude relays the answer. */
export const thinkerFinish = (): string => cardText('thinker', '', 'Answered; Claude relays it.', { done: true })

/** A note: an agent's explain-before-code paragraph, sent to the main session with SendMessage. */
export const noteCard = (role: CardRole, what: string, body: string): string => cardText(role, what, body)

/** Who sent a hand-back or a note, as the turn.start hook works it out: the loop's expert, a coder under it,
 * a thinker, or anyone else (no card). `task` is the loop task's id; `coder` is `c<k>` when known, else ''. */
export type Sender = { role: 'expert' | 'coder' | 'thinker' | 'other'; task: string; coder: string }

/** The coder id a text names (`T3.c2`, `T13a.c1`): `c<k>`, else ''. */
export const coderIdOf = (text: string): string => /\bT\d+[a-z]?\.(c\d+)\b/.exec(text)?.[1] ?? ''

const what = (s: Sender): string => (s.role === 'coder' && s.coder !== '' ? `${s.task} · ${s.coder}` : s.task)

/** A note's card: from the loop's expert or a coder under it; null for anyone else. */
export function noteCardOf(s: Sender, body: string): string | null {
  if (body.trim() === '' || (s.role !== 'expert' && s.role !== 'coder')) {
    return null
  }
  return noteCard(s.role, what(s), body)
}

/** A hand-back's finish card, or null (stays the stub): a `.` ping, a `WAITING:` hand-back, no contract, or a sender with
 * no card (retrievers, the curator, the auditor, the partner). 3.24 T8b: a coder's finish is a card again (3.23 T13d.c3 had dropped it), with `files`, its commit's numstat.
 * 3.24 T17g: `late`, the agent's final text when the notification only points at a hand-back already spent (route.ts
 * lateReport), stands in for the report. */
export function finishCardOf(s: Sender, text: string, files: readonly FileStat[] = [], late: string | null = null): string | null {
  const report = late ?? handbackReport(text)
  if (report === null || report.trim() === '.' || report.trim() === '' || /^\s*WAITING:/.test(report)) {
    return null
  }
  if (s.role === 'thinker') {
    return thinkerFinish()
  }
  if (s.role === 'expert') {
    const c = parseExpertContract(report)
    if (c === null) {
      return null
    }
    return expertFinish(s.task, c, report)
  }
  if (s.role === 'coder') {
    const c = parseCoderReturn(report)
    return c === null ? null : coderFinish(what(s), c, report, files)
  }
  return null
}

/** The `card_style` userConfig (3.23 T13): `mixed` (default: notes a side bar, finishes a round border), `boxed`, `bar`, `plain`. */
export type CardStyle = 'mixed' | 'boxed' | 'bar' | 'plain'

export function cardStyleOf(options: Readonly<Record<string, unknown>>): CardStyle {
  const v = options['card_style']
  return v === 'boxed' || v === 'bar' || v === 'plain' ? v : 'mixed'
}

/** The Box border a card draws in under a style: `quote` (a side bar), `round`, or null (plain: no border). */
export function borderOf(style: CardStyle, done: boolean): 'quote' | 'round' | null {
  if (style === 'plain') {
    return null
  }
  if (style === 'mixed') {
    return done ? 'round' : 'quote'
  }
  return style === 'boxed' ? 'round' : 'quote'
}

// The band above the prompt (3.23 T13): the autopilot's tick works out the rows, the AbovePrompt rule draws them.

/** An agent as the band sees it: `$.agent.list()`'s id, type, status, parent and description, with when it was first
 * seen (ms) and its last step (`Tool short-arg` from the watcher's facts file, '' none). */
export type BandAgent = { id: string; type: string; name?: string; status: string; parentId?: string; description: string; since: number; step?: string }

/** A band root: the loop task's expert with its task id, or the generation start's curator or auditor (''). */
export type BandRoot = { id: string; task: string }

const LIVE = new Set(['pending', 'running', 'waiting'])

/** The live agents under the roots (the roots included), each root's tree in turn, children by first seen; their root's task. */
export function bandIds(agents: readonly BandAgent[], roots: readonly BandRoot[], keep: ReadonlySet<string> = new Set()): { id: string; task: string }[] {
  const out: { id: string; task: string }[] = []
  const live = agents.filter(a => LIVE.has(a.status) || keep.has(a.id)) // 3.28.1 T15: `keep`, an agent whose shell runs
  for (const r of roots) {
    const ids = new Set([r.id])
    const tree = live.filter(a => a.id === r.id)
    for (let grew = true; grew;) {
      grew = false
      for (const a of [...live].sort((x, y) => x.since - y.since || x.id.localeCompare(y.id))) {
        if (!ids.has(a.id) && a.parentId !== undefined && ids.has(a.parentId)) {
          ids.add(a.id)
          tree.push(a)
          grew = true
        }
      }
    }
    out.push(...tree.filter(a => !out.some(o => o.id === a.id)).map(a => ({ id: a.id, task: r.task })))
  }
  return out
}

/** Elapsed time, short: `42s`, `7m`, `1h05m`. */
export function elapsedText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) {
    return `${String(s)}s`
  }
  const m = Math.floor(s / 60)
  return m < 60 ? `${String(m)}m` : `${String(Math.floor(m / 60))}h${String(m % 60).padStart(2, '0')}m`
}

/** An agent type's role (`expert-opus55` → expert, `coder-opus55` → coder); 3.24 T13B: `memory-curator` → curator (pink
 * in the band); 3.24 T17b: `pa-planner` → planner; 3.25 T6b: `pa-expert`, `pa-coder`, `pa-thinker` (and an old
 * `deep-thinker`) by the name after `pa-`. */
export const bandRole = (type: string): string => {
  const t = type === 'deep-thinker' ? 'thinker' : /^pa-(expert|coder|thinker|planner)$/.test(type) ? type.slice(3) : type
  const r = t === 'memory-curator' ? 'curator' : (t.split('-')[0] ?? '') || 'agent'
  return r === 'planner' ? 'planning' : r   // 3.28.1: ROLE_HEX names the planners' purple `planning`
}

/** The band's rows: one per live agent under the roots, its role, name, task (`T<n>`, `T<n> · c<k>` for a coder), step and
 * elapsed time at `now`; 3.28.1 T15: under each agent a row per background shell of its (`shells`, pa-watch's
 * shells.json): `◐ shell` (the spinner's frame from the clock; `✗` once it ended badly), its task, its name, its time. */
export function bandRows(agents: readonly BandAgent[], roots: readonly BandRoot[], now: number, shells: Record<string, Shell[]> = {}): BandRow[] {
  const byId = new Map(agents.map(a => [a.id, a]))
  return bandIds(agents, roots, new Set(Object.keys(shells))).flatMap(({ id, task }) => {
    const a = byId.get(id)
    if (a === undefined) {
      return []
    }
    const role = bandRole(a.type)
    const k = role === 'coder' ? coderIdOf(a.description) : ''
    const t = /\bT\d+[a-z]?\b/.exec(a.description)?.[0] ?? task
    const rowTask = k === '' ? t : `${t} · ${k}`
    return [{
      role, name: a.name || a.type, task: rowTask, step: (a.step ?? '').replace(/\s+/g, ' ').trim(),
      elapsed: elapsedText(now - a.since),
    }, ...(shells[id] ?? []).map(s => ({
      // the frame steps once a tick (the autopilot's 2 s), so each of the four shows
      role: 'shell', name: `${s.exit !== undefined ? '✗' : SPINNER[Math.floor(now / 2000) % SPINNER.length] ?? ''} shell`, task: rowTask,
      step: s.name, elapsed: elapsedText(now - s.at),
    }))]
  })
}

/** A band row's step from an agent's facts (3.23 T13d.c2): the watcher's `line` (the latest working line), else the last
 * of `steps`; undefined when neither. */
export function bandStepOf(facts: Record<string, unknown> | null): string | undefined {
  const line = facts?.['line']
  if (typeof line === 'string' && line.trim() !== '') {
    return line
  }
  const steps = facts?.['steps']
  const last: unknown = Array.isArray(steps) ? steps[steps.length - 1] : undefined
  return typeof last === 'string' ? last : undefined
}

/** 3.24 T13B: the band's lines and rows: the loop's line and the conversation-only note, the autopilot's rows then the
 * router's (Claude's); none of those while something works: `‹pa› working` with 1-3 cycling dots. */
export function bandView(band: Band, work: Work): { lines: string[]; rows: BandRow[] } {
  const lines = [band.line, work.note].filter(l => l !== '')
  const rows = [...band.rows, ...work.rows]
  if (lines.length === 0 && rows.length === 0 && work.working) {
    lines.push(`‹pa› working${'.'.repeat(Math.min(3, Math.max(1, work.dots)))}`)
  }
  return { lines, rows }
}

/** 3.24 T13B: the router's band rows for Claude: the partner (`Claude`, orange) and its live children (`since` and `step`
 * as the caller set them: first seen live, bandStepOf); '' partner: none. */
export function claudeRows(agents: readonly BandAgent[], partnerId: string, now: number): BandRow[] {
  if (partnerId === '') {
    return []
  }
  const named = agents.map(a => (a.id === partnerId ? { ...a, type: 'claude', name: 'Claude' } : a))
  return bandRows(named, [{ id: partnerId, task: '' }], now)
}

/** 3.29 T3: Claude's row while a developer message waits for Claude's turn to start: `received`, with the time since. */
export const receivedRow = (at: number, now: number): BandRow =>
  ({ role: 'claude', name: 'Claude', task: '', step: 'received', elapsed: elapsedText(now - at) })

/** The conversation-only note (3.23 T5d1, moved from pa-watch's usage line in 3.24 T13B): another session owns the loop
 * with a fresh heartbeat; '' when this session may own it. */
export const loopNoteOf = (owns: boolean, ownerSid: string): string =>
  owns || ownerSid === '' ? '' : `conversation only: the loop runs in ${ownerSid.slice(0, 8)}`

/** A role's hex for the band's names (ROLE_HEX; a retriever or another role: none, the default color). */
export const bandHex = (role: string): string | undefined => (role in ROLE_HEX ? ROLE_HEX[role as CardRole] : undefined)

const cut = (s: string, n: number): string => (n <= 0 ? '' : s.length <= n ? s : n === 1 ? '…' : `${s.slice(0, n - 1)}…`)

/** A row's three cells cut to `cols` columns, never wrapped: the name (bold, the role's hex), ` <task>` (plain), then
 * `  <step> · <elapsed>` (dim); the dim cell gives way first, then the task, then the name. */
export function bandCells(r: BandRow, cols: number): { name: string; task: string; dim: string } {
  const name = cut(r.name, cols)
  const task = cut(r.task === '' ? '' : ` ${r.task}`, cols - name.length)
  const dim = cut(`  ${[r.step, r.elapsed].filter(p => p !== '').join(' · ')}`, cols - name.length - task.length)
  return { name, task, dim }
}
