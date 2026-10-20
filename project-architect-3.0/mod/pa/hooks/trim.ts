// 3.29.9: the router's trim of the main conversation (the display; no model reads it). A task's first spawn marks where
// it began (`trimMark`: the row count and the fingerprints of up to TAIL rows before it); at its done, and at a resumed
// session's start, the router finds that place again in `$.session.messages()` and asks the watcher (pa-watch, its
// `session.compact` hook) to keep the rows from there on. `fingerprint` is copied in mod/pa-watch/hooks/trim.ts,
// kept identical: the watcher re-validates the cut point against the rows it is handed.

/** A main-conversation row as `$.session.messages()` gives it (the fields a fingerprint reads). */
export type Row = { role: string; text: string; toolUses?: readonly unknown[]; toolResults?: readonly unknown[] }

/**
 * Where a task began: its session, its id, the row count then and the fingerprints of the rows just before; H1.c2: and
 * its `anchor` (anchorOf), absent on marks written before it.
 */
export type TrimMark = { sid: string; task: string; len: number; tail: string[]; anchor?: string }

/** An agent's hand-back delivered to the main conversation: never an anchor (a reload shows rows the live list lacks). */
const HANDBACK_ROW = 'Another Claude session sent a message:'
/** An anchor's cap, in normalized chars. */
const ANCHOR_CAP = 4_000

/** A row's text for anchor comparison: every whitespace removed (a reload's rows differ in it), capped. */
export const anchorText = (text: string): string => text.replace(/\s+/g, '').slice(0, ANCHOR_CAP)

/**
 * The reload-proof anchor of a place `end` (H1.c2): the normalized text of the last user row before it that is not a
 * hand-back delivery (normally a feed event's `<task-notification>` or the developer's message); '' when none.
 */
export function anchorOf(rows: readonly Row[], end: number): string {
  for (let i = Math.min(end, rows.length) - 1; i >= 0; i -= 1) {
    const r = rows[i]
    if (r !== undefined && r.role === 'user' && !r.text.startsWith(HANDBACK_ROW) && anchorText(r.text) !== '') {
      return anchorText(r.text)
    }
  }
  return ''
}

/** The instructions' prefix the watcher answers; the rest is `trimPayload`'s JSON. */
export const TRIM_PREFIX = 'pa-trim '
/** How many rows before a place identify it. */
export const TAIL = 3

/** FNV-1a, 32 bits, over the UTF-16 code units, as 8 hex digits. */
function fnv(text: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

/** One row's fingerprint: role, text length, tool block counts and a hash of the text. */
export function fingerprint(r: Row): string {
  return `${r.role.slice(0, 1)}${String(r.text.length)}.${String(r.toolUses?.length ?? 0)}.${String(r.toolResults?.length ?? 0)}.${fnv(r.text)}`
}

/** The fingerprints of up to TAIL rows before index `end`, oldest first. */
export function tailOf(rows: readonly Row[], end: number): string[] {
  return rows.slice(Math.max(0, end - TAIL), Math.max(0, end)).map(fingerprint)
}

const tailAt = (rows: readonly Row[], end: number, tail: readonly string[]): boolean =>
  end >= tail.length && end <= rows.length && tailOf(rows, end).join('|') === tail.join('|')

/**
 * The cut point for a mark: its exact place (the rows before `len` match `tail`), else the latest place at or before
 * `len` whose rows before it match, moved past assistant rows to the next user row; -1 when none, when it is 0 (nothing
 * to drop) or when no row would be kept. An empty tail matches only at its exact place. H1.c2: neither matching (the
 * rows a reload gives differ: shorter texts, hand-back rows the live list lacked), just after the last user row whose
 * normalized text is the mark's `anchor`.
 */
export function cutPoint(rows: readonly Row[], mark: Pick<TrimMark, 'len' | 'tail' | 'anchor'>): number {
  let k = -1
  if (tailAt(rows, mark.len, mark.tail)) {
    k = mark.len
  } else if (mark.tail.length > 0) {
    for (let j = Math.min(mark.len, rows.length); j >= mark.tail.length; j -= 1) {
      if (tailAt(rows, j, mark.tail)) {
        k = j
        break
      }
    }
  }
  if (k < 0 && (mark.anchor ?? '') !== '') {
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const r = rows[i]
      if (r !== undefined && r.role === 'user' && anchorText(r.text) === mark.anchor) {
        k = i + 1
        break
      }
    }
  }
  while (k >= 0 && k < rows.length && rows[k]?.role === 'assistant') {
    k += 1
  }
  return k > 0 && k < rows.length ? k : -1
}

/** The compaction's instructions for a cut at `k`: the prefix, then `{k, tail}` (the rows just before `k`). */
export function trimInstructions(rows: readonly Row[], k: number): string {
  return `${TRIM_PREFIX}${JSON.stringify({ k, tail: tailOf(rows, k) })}`
}

/** A loop state's mark (`trimMark` / `doneMark`), loosely typed; null when absent or malformed. */
export function markOf(x: unknown): TrimMark | null {
  if (typeof x !== 'object' || x === null) {
    return null
  }
  const o = x as Record<string, unknown>
  const tail = Array.isArray(o['tail']) ? (o['tail'] as unknown[]).filter((t): t is string => typeof t === 'string') : []
  return typeof o['sid'] === 'string' && typeof o['task'] === 'string' && typeof o['len'] === 'number'
    ? { sid: o['sid'], task: o['task'], len: o['len'], tail, ...(typeof o['anchor'] === 'string' && o['anchor'] !== '' ? { anchor: o['anchor'] } : {}) }
    : null
}

/**
 * What a rejected `$.session.compact` leads to at attempt `tries` (1-based) of `max`: the headless engine's refusal (a -p
 * or SDK run) is skipped, never retried; any other (a turn runs) is retried until the last attempt, then given up.
 */
export function trimFailure(err: unknown, tries: number, max: number): 'headless' | 'retry' | 'giveup' {
  return String(err).includes('not available in a headless') ? 'headless' : tries >= max ? 'giveup' : 'retry'
}
