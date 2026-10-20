// 3.29.9: the watcher's half of the router's display trim. The router (pa) calls `$.session.compact` with instructions
// `pa-trim {"k":…,"tail":[…]}`; the call skips the calling plugin's hooks, so this plugin answers it with the rows from
// `k` on, after checking that the rows before `k` still carry the fingerprints the router saw. `fingerprint` and
// `tailOf` are a copy of the router's mod/pa/hooks/trim.ts, kept identical.

type Row = { role: string; text: string; toolUses?: readonly unknown[]; toolResults?: readonly unknown[] }

export const TRIM_PREFIX = 'pa-trim '
const TAIL = 3

function fnv(text: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

/** One row's fingerprint: role, text length, tool block counts and a hash of the text (copy of the router's). */
export function fingerprint(r: Row): string {
  return `${r.role.slice(0, 1)}${String(r.text.length)}.${String(r.toolUses?.length ?? 0)}.${String(r.toolResults?.length ?? 0)}.${fnv(r.text)}`
}

/** The fingerprints of up to TAIL rows before index `end`, oldest first (copy of the router's). */
export function tailOf(rows: readonly Row[], end: number): string[] {
  return rows.slice(Math.max(0, end - TAIL), Math.max(0, end)).map(fingerprint)
}

/**
 * The cut point a `pa-trim` request names, checked against `rows`: `k` when 0 < k < rows.length, a non-empty tail
 * matches the rows just before it and rows[k] is a user row; -1 when it does not validate; null when `instructions` is
 * not a `pa-trim` request.
 */
export function trimCut(instructions: string | undefined, rows: readonly Row[]): number | null {
  if (instructions === undefined || !instructions.startsWith(TRIM_PREFIX)) {
    return null
  }
  let o: unknown
  try {
    o = JSON.parse(instructions.slice(TRIM_PREFIX.length))
  } catch {
    return -1
  }
  const d = typeof o === 'object' && o !== null ? (o as Record<string, unknown>) : {}
  const k = d['k']
  const tail = Array.isArray(d['tail']) ? (d['tail'] as unknown[]) : []
  if (typeof k !== 'number' || !Number.isInteger(k) || k <= 0 || k >= rows.length || tail.length === 0 || rows[k]?.role !== 'user') {
    return -1
  }
  return tailOf(rows, k).join('|') === tail.join('|') && tail.every(t => typeof t === 'string') ? k : -1
}
