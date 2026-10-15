// Pure helpers of the journal (design.md "The journal"): entry paths, entry frontmatter and body, and the monthly
// index. One Markdown file per entry under `.claude-state/journal/<YYYY-MM-DD>/`; the watcher (`mod/pa-watch`) copies
// the naming and pins it with the same test vectors.

export const JOURNAL_DIR = '.claude-state/journal'

export const WRITERS = ['r', 'w'] as const
export const WHOS = ['dev', 'claude', 'partner', 'thinker', 'coder', 'critic', 'mod'] as const // plus T<n>; `partner`: entries before 3.22 T8f
export const KINDS = ['msg', 'answer', 'thinking', 'start', 'end', 'prompt', 'hold', 'release', 'plan', 'decision', 'note', 'steer', 'interim', 'recover', 'repeat', 'context', 'close', 'feed', 'card'] as const // note: the watcher's approach notes, steer: mod/steer (3.23 T5c), interim: T<n>/interim (T5c.c3), recover: mod/recover (T5d1), repeat: T<n>/repeat (3.23 T6a2), context: mod/context (T6a2), close: mod/close (3.23 T6b), feed: mod/feed (3.23 T13d.c2: the feed could not start), card: mod/card (3.24 T13E: a card the loop drew, the review release's accepted card)

export type Writer = (typeof WRITERS)[number]
export type Kind = (typeof KINDS)[number]

export function isWho(who: string): boolean {
  return (WHOS as readonly string[]).includes(who) || /^T[0-9]+$/.test(who)
}

export function isKind(kind: string): boolean {
  return (KINDS as readonly string[]).includes(kind)
}

/** `.claude-state/journal/<date>/<HHMMSS>-<seq><writer>-<who>-<kind>.md`; throws on any unknown or malformed part. */
export function entryPath(date: string, hhmmss: string, seq: number, writer: string, who: string, kind: string): string {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(date)) throw new Error(`journal: bad date ${JSON.stringify(date)}`)
  if (!/^[0-9]{6}$/.test(hhmmss)) throw new Error(`journal: bad time ${JSON.stringify(hhmmss)}`)
  if (!Number.isInteger(seq) || seq < 0 || seq > 999) throw new Error(`journal: bad seq ${String(seq)}`)
  if (!(WRITERS as readonly string[]).includes(writer)) throw new Error(`journal: bad writer ${JSON.stringify(writer)}`)
  if (!isWho(who)) throw new Error(`journal: bad who ${JSON.stringify(who)}`)
  if (!isKind(kind)) throw new Error(`journal: bad kind ${JSON.stringify(kind)}`)
  return `${JOURNAL_DIR}/${date}/${hhmmss}-${String(seq).padStart(3, '0')}${writer}-${who}-${kind}.md`
}

export type EntryName = { date: string; time: string; seq: number; writer: Writer; who: string; kind: Kind }

/**
 * The parts of an entry path (or its tail from the date folder on), or null when it is not one. Both forms: the live
 * `<date>/<stem>.md` and the rolled `<date>.md#<stem>` (tools/journal_rollup.py, 3.23 T7).
 */
export function parseEntryPath(path: string): EntryName | null {
  const m = /(?:^|\/)([0-9]{4}-[0-9]{2}-[0-9]{2})(?:\/|\.md#)([0-9]{6})-([0-9]{3})([rw])-(T[0-9]+|[a-z]+)-([a-z]+)(\.md)?$/.exec(path)
  if (!m || !isWho(m[5]) || !isKind(m[6]) || (m[7] === undefined) !== path.includes('.md#')) return null
  return { date: m[1], time: m[2], seq: Number(m[3]), writer: m[4] as Writer, who: m[5], kind: m[6] as Kind }
}

/** A rolled day's file `.claude-state/journal/<date>.md` (tools/journal_rollup.py). */
export function dayFilePath(date: string): string {
  return `${JOURNAL_DIR}/${date}.md`
}

const STEM = /^[0-9]{6}-[0-9]{3}[rw]-(?:T[0-9]+|[a-z]+)-[a-z]+$/

/**
 * The entries of a rolled day file (journal_rollup.py split_day): each `## <stem>` heading starts one, its text the
 * entry file unchanged; paths `.claude-state/journal/<date>.md#<stem>`.
 */
export function splitDay(date: string, text: string): Entry[] {
  const out: Entry[] = []
  let stem = ''
  let buf: string[] = []
  const flush = (): void => {
    if (stem === '') return
    const t = buf.join('\n')
    out.push({ path: `${dayFilePath(date)}#${stem}`, ...(parseEntry(t) ?? { meta: {}, body: t }) })
  }
  for (const line of text.replace(/\r\n/g, '\n').split('\n')) {
    const m = /^## (\S+)$/.exec(line)
    if (m && STEM.test(m[1])) {
      flush()
      stem = m[1]
      buf = []
    } else if (stem !== '') buf.push(line)
  }
  flush()
  return out
}

/** `.claude-state/journal/INDEX-<YYYY-MM>.md`. */
export function indexPath(month: string): string {
  if (!/^[0-9]{4}-[0-9]{2}$/.test(month)) throw new Error(`journal: bad month ${JSON.stringify(month)}`)
  return `${JOURNAL_DIR}/INDEX-${month}.md`
}

export type Usage = { input: number; cacheRead: number; cacheWrite: number; output: number }

export type Meta = {
  ts?: string
  session?: string
  agentId?: string
  parentId?: string
  role?: string
  model?: string
  effort?: string
  turnId?: string
  task?: string
  agentType?: string // 3.24 T17b
  usage?: Usage
}

export type Entry = { path: string; meta: Meta; body: string }

const STRING_KEYS = ['ts', 'session', 'agentId', 'parentId', 'role', 'model', 'effort', 'turnId', 'task', 'agentType'] as const // agentType: the watcher's `spawn` entries (3.24 T17b; doctor reads them)
const USAGE_KEYS = ['input', 'cacheRead', 'cacheWrite', 'output'] as const

// Plain when it cannot be misread; otherwise a JSON string, which is also a YAML double-quoted scalar on one line.
function scalar(value: string): string {
  return /^[A-Za-z0-9][A-Za-z0-9_.\-/+@:[\]]*$/.test(value) && !value.endsWith(':') ? value : JSON.stringify(value)
}

function unscalar(raw: string): string | null {
  if (!raw.startsWith('"')) return raw
  try {
    const v: unknown = JSON.parse(raw)
    return typeof v === 'string' ? v : null
  } catch {
    return null
  }
}

/** `---`, one `key: value` line per present key, `---`, a blank line, the body. */
export function renderEntry(meta: Meta, body: string): string {
  const lines = ['---']
  for (const k of STRING_KEYS) {
    const v = meta[k]
    if (typeof v === 'string') lines.push(`${k}: ${scalar(v)}`)
  }
  const u = meta.usage
  if (u) lines.push(`usage: {${USAGE_KEYS.map((k) => `${k}: ${String(Number(u[k]) || 0)}`).join(', ')}}`)
  lines.push('---', '', body.replace(/\r\n/g, '\n').replace(/\n*$/, ''), '')
  return lines.join('\n')
}

/** The frontmatter and body of an entry file, or null when it has no frontmatter. Unknown keys are ignored. */
export function parseEntry(text: string): { meta: Meta; body: string } | null {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  if (lines[0] !== '---') return null
  const end = lines.indexOf('---', 1)
  if (end < 0) return null
  const meta: Meta = {}
  for (const line of lines.slice(1, end)) {
    const m = /^([A-Za-z]+): (.*)$/.exec(line)
    if (!m) continue
    const [, key, raw] = m
    if (key === 'usage') {
      const u: Partial<Usage> = {}
      for (const part of raw.replace(/^\{|\}$/g, '').split(',')) {
        const p = /^\s*([A-Za-z]+): (-?[0-9.]+)\s*$/.exec(part)
        if (p && (USAGE_KEYS as readonly string[]).includes(p[1]) && Number.isFinite(Number(p[2]))) {
          u[p[1] as keyof Usage] = Number(p[2])
        }
      }
      if (USAGE_KEYS.every((k) => typeof u[k] === 'number')) meta.usage = u as Usage
    } else if ((STRING_KEYS as readonly string[]).includes(key)) {
      const v = unscalar(raw)
      if (v !== null) meta[key as (typeof STRING_KEYS)[number]] = v
    }
  }
  let rest = lines.slice(end + 1)
  if (rest[0] === '') rest = rest.slice(1)
  return { meta, body: rest.join('\n').replace(/\n*$/, '') }
}

/** The first non-blank line of a body, trimmed. */
export function firstLine(body: string): string {
  return (
    body
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l !== '') ?? ''
  )
}

export type IndexLine = { ts: string; who: string; kind: string; first: string; path: string }

function escapeCell(text: string): string {
  return text.replace(/\\/g, '\\\\').replace(/\|/g, '\\|')
}

function unescapeCell(text: string): string {
  return text.replace(/\\([\\|])/g, '$1')
}

function byTsThenPath(a: { ts: string; path: string }, b: { ts: string; path: string }): number {
  if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1
  return a.path < b.path ? -1 : a.path > b.path ? 1 : 0
}

/** `ts | who | kind | first line (≤ 80 chars, pipes escaped) | path`, one line per entry, sorted by ts then path. */
export function buildIndex(entries: Entry[]): string {
  const rows = entries.map((e) => {
    const name = parseEntryPath(e.path)
    return {
      ts: e.meta.ts ?? '',
      who: name?.who ?? '?',
      kind: name?.kind ?? '?',
      first: firstLine(e.body).slice(0, 80),
      path: e.path,
    }
  })
  rows.sort(byTsThenPath)
  return rows.map((r) => `${r.ts} | ${r.who} | ${r.kind} | ${escapeCell(r.first)} | ${r.path}\n`).join('')
}

/** The lines of an index; lines that are not index lines are skipped. */
export function parseIndex(text: string): IndexLine[] {
  const out: IndexLine[] = []
  for (const line of text.replace(/\r\n/g, '\n').split('\n')) {
    const m = /^([^|]*) \| ([^|]*) \| ([^|]*) \| (.*) \| ([^|]+)$/.exec(line)
    if (!m) continue
    out.push({ ts: m[1], who: m[2], kind: m[3], first: unescapeCell(m[4]), path: m[5] })
  }
  return out
}

/** True only when the index lists exactly these paths, each once. */
export function indexAgrees(indexText: string, paths: string[]): boolean {
  const listed = parseIndex(indexText).map((l) => l.path)
  const want = new Set(paths)
  if (listed.length !== want.size || new Set(listed).size !== listed.length) return false
  return listed.every((p) => want.has(p))
}

const two = (n: number): string => String(n).padStart(2, '0')

/** `[YYYY-MM-DD, HHMMSS]` of `ms` in local time, as the watcher stamps its entries. */
export function stamp(ms: number): [string, string] {
  const d = new Date(ms)
  return [
    `${String(d.getFullYear())}-${two(d.getMonth() + 1)}-${two(d.getDate())}`,
    `${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`,
  ]
}

/** The paths an index does not list yet: the entries to read before `mergeIndex`. */
export function unindexed(indexText: string, paths: string[]): string[] {
  const listed = new Set(parseIndex(indexText).map((l) => l.path))
  return paths.filter((p) => !listed.has(p))
}

/** The index of exactly `paths`: an old line is kept for a path it lists, `read` supplies the rest. */
export function mergeIndex(indexText: string, paths: string[], read: Entry[]): string {
  const want = new Set(paths)
  const byPath = new Map<string, Entry>()
  for (const l of parseIndex(indexText)) {
    if (want.has(l.path)) byPath.set(l.path, { path: l.path, meta: { ts: l.ts }, body: l.first })
  }
  for (const e of read) {
    if (want.has(e.path)) byPath.set(e.path, e)
  }
  return buildIndex([...byPath.values()])
}
