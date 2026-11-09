// Pure helpers of the journal (design.md "The journal"): entry paths, entry frontmatter and body, and the output of
// its reader (`tools/journal_rollup.py entries`, 3.32.15). One Markdown file per entry under
// `.claude-state/journal/<YYYY-MM-DD>/`, a finished day packed into `<YYYY-MM-DD>.md`; the watcher (`mod/pa-watch`)
// copies the naming and pins it with the same test vectors. 3.32.15: no month index (GitHub #15).

export const JOURNAL_DIR = '.claude-state/journal'

export const WRITERS = ['r', 'w'] as const
export const WHOS = ['dev', 'claude', 'partner', 'thinker', 'coder', 'critic', 'mod'] as const // plus T<n>; `partner`: entries before 3.22 T8f
export const KINDS = ['msg', 'answer', 'thinking', 'start', 'end', 'prompt', 'hold', 'release', 'plan', 'decision', 'note', 'steer', 'interim', 'recover', 'repeat', 'context', 'close', 'feed', 'card', 'shell', 'record'] as const // note: the watcher's approach notes, steer: mod/steer (3.23 T5c), interim: T<n>/interim (T5c.c3), recover: mod/recover (T5d1), repeat: T<n>/repeat (3.23 T6a2), context: mod/context (T6a2), close: mod/close (3.23 T6b), feed: mod/feed (3.23 T13d.c2: the feed could not start), card: mod/card (3.24 T13E: a card the loop drew, the review release's accepted card), shell: T<n>/shell (3.28.1 T15: a task's agent handed back to wait on its background shell), record: claude/record, a filing's records (3.31)

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

/** 3.32.9: a read entry's writer may also be `x`, an external coder's (tools/pa_ext.py); only r and w write here.
 * 3.32.11 T1: any who and kind of the name's shape (the expert's, planner's, …, `spawn`), as journal_rollup.py. */
export type EntryName = { date: string; time: string; seq: number; writer: Writer | 'x'; who: string; kind: string }

/**
 * The parts of an entry path (or its tail from the date folder on), or null when it is not one. Both forms: the live
 * `<date>/<stem>.md` and the rolled `<date>.md#<stem>` (tools/journal_rollup.py, 3.23 T7).
 */
export function parseEntryPath(path: string): EntryName | null {
  // 3.32.9: an external coder's entries (x) and a watcher's past its 999th (four digits) parse too
  const m = /(?:^|\/)([0-9]{4}-[0-9]{2}-[0-9]{2})(?:\/|\.md#)([0-9]{6})-([0-9]{3,})([rwx])-(T[0-9]+|[a-z]+)-([a-z]+)(\.md)?$/.exec(path)
  // 3.32.11 T1: any writer and kind of the shape (was: isWho/isKind, which left other agents' entries out of the index)
  if (!m || (m[7] === undefined) !== path.includes('.md#')) return null
  return { date: m[1], time: m[2], seq: Number(m[3]), writer: m[4] as Writer | 'x', who: m[5], kind: m[6] }
}

/**
 * 3.32.15: the journal's reader, `tools/journal_rollup.py entries` (its docstring). The mod reads no packed day file
 * itself: `$.fs` refuses a file over 4 MiB, a busy day's passes that (GitHub #15: 7.4 MB; bfm-decomp: 13.4 MB), and
 * splitting one in the hooks worker is its time.
 */
export const ENTRIES_TOOL = 'tools/journal_rollup.py'

/** One line of `journal_rollup.py entries`: the entry's path (live `<J>/<date>/<stem>.md` or packed
 * `<J>/<date>.md#<stem>`), its name `<date>/<stem>.md` and its text unchanged. */
export type EntryLine = { path: string; name: string; text: string }

/** 3.32.15: the lines of `journal_rollup.py entries`; a line that is not one is skipped. */
export function entryLinesOf(stdout: string): EntryLine[] {
  const out: EntryLine[] = []
  for (const line of stdout.split(/\r?\n/)) {
    if (line.trim() === '') continue
    try {
      const o = JSON.parse(line) as Partial<Record<keyof EntryLine, unknown>>
      if (typeof o.path === 'string' && typeof o.name === 'string' && typeof o.text === 'string') {
        out.push({ path: o.path, name: o.name, text: o.text })
      }
    } catch {
      // not a line of the reader's
    }
  }
  return out
}

/** 3.32.15: those lines as entries (frontmatter and body). */
export function entriesOf(stdout: string): Entry[] {
  return entryLinesOf(stdout).map(l => ({ path: l.path, ...(parseEntry(l.text) ?? { meta: {}, body: l.text }) }))
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
  through?: string // 3.31: a record's newest covered exchange (ISO)
  usage?: Usage
}

export type Entry = { path: string; meta: Meta; body: string }

const STRING_KEYS = ['ts', 'session', 'agentId', 'parentId', 'role', 'model', 'effort', 'turnId', 'task', 'agentType', 'through'] as const // agentType: the watcher's `spawn` entries (3.24 T17b; doctor reads them); through: record entries (3.31)
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

const two = (n: number): string => String(n).padStart(2, '0')

/** `[YYYY-MM-DD, HHMMSS]` of `ms` in local time, as the watcher stamps its entries. */
export function stamp(ms: number): [string, string] {
  const d = new Date(ms)
  return [
    `${String(d.getFullYear())}-${two(d.getMonth() + 1)}-${two(d.getDate())}`,
    `${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`,
  ]
}

/** 3.31 T2b: `records.py index`'s TOPICS.md, the path of. */
export const TOPICS_PATH = `${JOURNAL_DIR}/TOPICS.md`

/** 3.31 T2b: the `# filed through: <ISO>` header of TOPICS.md (the newest record's `through`); '' when absent or `(none)`. */
export function filedThroughOf(topics: string | null): string {
  const m = /^# filed through: (\d{4}-\S+)$/m.exec(topics ?? '')
  return m?.[1] ?? ''
}
