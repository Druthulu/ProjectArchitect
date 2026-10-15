// 3.24 T8b: file edits on screen. A completed Edit, MultiEdit, Write or NotebookEdit by Claude, an expert or a coder is
// one feed record (`kind` "edit") whose `edit` holds the native look's parts: the path, created or updated, the +/-
// counts, the unified hunks (3 lines of context, at most EDIT_CAP rows in all) and the rows left out. The router draws it
// as a note in the agent's role color and stores it as a ```diff block for the phone. Pure helpers; register.ts makes
// the `$` calls.

/** The tools whose successful calls draw an edit card. */
export const EDIT_TOOLS: readonly string[] = ['Edit', 'MultiEdit', 'Write', 'NotebookEdit']

/** Context lines around each change (native: 3). */
export const CONTEXT = 3
/** The rows an edit card shows at most (hunk lines, or a new file's first lines); the rest is `… +N lines`. */
export const EDIT_CAP = 30
/** The middle (after the common head and tail) a line diff takes at most, in old × new lines; past it, all replaced. */
const LCS_MAX = 4_000_000

/** Whose edits draw: Claude (pa-claude, or the partner by id), experts and coders; 3.24 T17b: the planner (its draft's
 * one labelled line). */
export function editsShown(type: string, isPartner: boolean): boolean {
  return type === 'pa-claude' || type === 'pa-planner' || /^(pa-)?(expert|coder)(-|$)/.test(type) || (type === '' && isPartner)
}

/** An edit card's data, the feed record's `edit` (the router's FeedEdit). `hunks`: unified hunks with `@@` headers, or
 * for a created file one block of its first lines; `more`: rows not shown. 3.24 T13C: `label`, when set, replaces the
 * header's `Created <path>` / `Updated <path>` (a plan draft: "Plan drafted", no hunks). */
export type FeedEdit = { path: string; created: boolean; add: number; del: number; hunks: string[]; more: number; label?: string }

/** The plan drafts (project-relative, either separator): their edit record is one header line, no hunks (3.24 T13C). */
const PLAN_DRAFTS = ['.run/PHASE_PLAN.draft.md', '.run/GENERATION_PLAN.draft.md']

/** The header line (the record's `text`, the band's line, the phone's line above the fence). */
export function editHeader(e: FeedEdit): string {
  const verb = e.label ?? `${e.created ? 'Created' : 'Updated'} ${e.path}`
  return e.created ? `${verb} (${e.add} ${e.add === 1 ? 'line' : 'lines'})` : `${verb} (+${e.add} -${e.del})`
}

/** A text's lines: CRLF and CR as LF, other control characters but tab as spaces, no empty last line after a final newline. */
export function linesOf(text: string): string[] {
  if (text === '') {
    return []
  }
  const ls = text.replace(/\r\n?/g, '\n').replace(/[\x00-\x08\x0b-\x1f\x7f]/g, ' ').split('\n')
  if (ls[ls.length - 1] === '') {
    ls.pop()
  }
  return ls
}

/** One diff row: kept, removed or added; `o`/`n` the old/new lines before it (0-based positions). */
type Row = { sign: ' ' | '-' | '+'; o: number; n: number; text: string }

/** Every row of the line diff of `a` to `b` (common head and tail kept, an LCS over the middle; removals first). */
export function diffRows(a: readonly string[], b: readonly string[]): Row[] {
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) {
    head += 1
  }
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) {
    tail += 1
  }
  const rows: Row[] = []
  for (let i = 0; i < head; i += 1) {
    rows.push({ sign: ' ', o: i, n: i, text: a[i] ?? '' })
  }
  const am = a.slice(head, a.length - tail)
  const bm = b.slice(head, b.length - tail)
  const M = am.length
  const N = bm.length
  let o = head
  let n = head
  if (M * N > 0 && M * N <= LCS_MAX) {
    // dp[i][j]: the LCS length of am[i..] and bm[j..]
    const w = N + 1
    const dp = new Uint32Array((M + 1) * w)
    for (let i = M - 1; i >= 0; i -= 1) {
      for (let j = N - 1; j >= 0; j -= 1) {
        dp[i * w + j] = am[i] === bm[j] ? (dp[(i + 1) * w + j + 1] ?? 0) + 1 : Math.max(dp[(i + 1) * w + j] ?? 0, dp[i * w + j + 1] ?? 0)
      }
    }
    let i = 0
    let j = 0
    while (i < M || j < N) {
      if (i < M && j < N && am[i] === bm[j]) {
        rows.push({ sign: ' ', o, n, text: am[i] ?? '' })
        i += 1; j += 1; o += 1; n += 1
      } else if (i < M && (j >= N || (dp[(i + 1) * w + j] ?? 0) >= (dp[i * w + j + 1] ?? 0))) {
        rows.push({ sign: '-', o, n, text: am[i] ?? '' })
        i += 1; o += 1
      } else {
        rows.push({ sign: '+', o, n, text: bm[j] ?? '' })
        j += 1; n += 1
      }
    }
  } else {
    for (const t of am) {
      rows.push({ sign: '-', o, n, text: t })
      o += 1
    }
    for (const t of bm) {
      rows.push({ sign: '+', o, n, text: t })
      n += 1
    }
  }
  for (let k = 0; k < tail; k += 1) {
    rows.push({ sign: ' ', o: o + k, n: n + k, text: a[a.length - tail + k] ?? '' })
  }
  return rows
}

/** The changed rows with CONTEXT kept rows around each change; hunks closer than 2 × CONTEXT merge. */
export function hunksOf(rows: readonly Row[], context = CONTEXT): Row[][] {
  const changed = rows.map((r, k) => (r.sign === ' ' ? -1 : k)).filter(k => k >= 0)
  const out: Row[][] = []
  let from = -1
  let to = -1
  for (const k of changed) {
    if (from >= 0 && k - context <= to + context + 1) {
      to = k
      continue
    }
    if (from >= 0) {
      out.push(rows.slice(Math.max(0, from - context), Math.min(rows.length, to + context + 1)))
    }
    from = k
    to = k
  }
  if (from >= 0) {
    out.push(rows.slice(Math.max(0, from - context), Math.min(rows.length, to + context + 1)))
  }
  return out
}

/** One hunk as unified-diff text: `@@ -a,b +c,d @@`, then each row with its sign. */
export function hunkText(rows: readonly Row[]): string {
  const first = rows[0]
  if (first === undefined) {
    return ''
  }
  const ol = rows.filter(r => r.sign !== '+').length
  const nl = rows.filter(r => r.sign !== '-').length
  const start = (pos: number, count: number): number => (count === 0 ? pos : pos + 1)
  return [`@@ -${start(first.o, ol)},${ol} +${start(first.n, nl)},${nl} @@`, ...rows.map(r => `${r.sign}${r.text}`)].join('\n')
}

/** The hunks cut to `cap` rows in all (a hunk cut short gets its own header); the rows left out. */
function capped(hunks: readonly Row[][], cap: number): { hunks: string[]; more: number } {
  const out: string[] = []
  let room = cap
  let more = 0
  for (const h of hunks) {
    if (room <= 0) {
      more += h.length
      continue
    }
    const kept = h.slice(0, room)
    room -= kept.length
    more += h.length - kept.length
    out.push(hunkText(kept))
  }
  return { hunks: out, more }
}

/** An edit card of `before` to `after` at `path`; `before` null: a created file (its first EDIT_CAP lines). */
export function editView(path: string, before: string | null, after: string): FeedEdit {
  const b = linesOf(after)
  if (before === null) {
    return { path, created: true, add: b.length, del: 0, hunks: b.length === 0 ? [] : [b.slice(0, EDIT_CAP).join('\n')], more: Math.max(0, b.length - EDIT_CAP) }
  }
  const rows = diffRows(linesOf(before), b)
  const c = capped(hunksOf(rows), EDIT_CAP)
  return { path, created: false, add: rows.filter(r => r.sign === '+').length, del: rows.filter(r => r.sign === '-').length, ...c }
}

type Patch = { oldStart: number; newStart: number; lines: string[] }

/** An edit card of the tool's own structured patch (when the old text is not in the result). */
export function patchView(path: string, patch: readonly Patch[]): FeedEdit {
  const hunks: Row[][] = []
  for (const p of patch) {
    let o = p.oldStart - 1
    let n = p.newStart - 1
    const rows: Row[] = []
    for (const l of p.lines) {
      const sign = l[0] === '-' || l[0] === '+' ? l[0] : ' '
      rows.push({ sign, o, n, text: linesOf(l.slice(1))[0] ?? '' })
      o += sign === '+' ? 0 : 1
      n += sign === '-' ? 0 : 1
    }
    hunks.push(rows)
  }
  const all = hunks.flat()
  return { path, created: false, add: all.filter(r => r.sign === '+').length, del: all.filter(r => r.sign === '-').length, ...capped(hunks, EDIT_CAP) }
}

const lf = (s: string): string => s.replace(/\r\n?/g, '\n')

/** `text` with one Edit applied (LF throughout); null when the old string is not found. */
function applied(text: string, oldS: string, newS: string, all: boolean): string | null {
  if (oldS === '') {
    return text === '' ? newS : null
  }
  const i = text.indexOf(oldS)
  if (i < 0) {
    return null
  }
  return all ? text.split(oldS).join(newS) : text.slice(0, i) + newS + text.slice(i + oldS.length)
}

/** `path` shown project-relative (its own separators) when under `root`, else whole. */
export function shownPath(path: string, root: string): string {
  const f = (s: string): string => s.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  const r = f(root)
  return r !== '' && f(path).startsWith(`${r}/`) ? path.slice(root.replace(/[\\/]+$/, '').length + 1) : path
}

type Obj = Record<string, unknown>
const str = (x: unknown): string => (typeof x === 'string' ? x : '')
const isObj = (x: unknown): x is Obj => typeof x === 'object' && x !== null && !Array.isArray(x)

/**
 * The edit card of one tool call, or null: not an edit tool, the call denied or failed (`deny`, `isError`, no `result`),
 * held for review (`staged`), or nothing to show. `input` is the call's arguments, `r` what `next(e)` resolved to.
 */
export function editOf(tool: string, input: Obj, r: unknown, root: string): FeedEdit | null {
  if (!EDIT_TOOLS.includes(tool) || !isObj(r) || r['deny'] !== undefined || r['isError'] === true || !isObj(r['result'])) {
    return null
  }
  const res = r['result']
  if (res['staged'] === true || str(res['error']) !== '') {
    return null
  }
  const path = shownPath(str(res['filePath']) || str(res['notebook_path']) || str(input['file_path']) || str(input['notebook_path']), root)
  if (path === '') {
    return null
  }
  const patch = Array.isArray(res['structuredPatch']) ? (res['structuredPatch'] as Patch[]) : []
  const orig = typeof res['originalFile'] === 'string' ? lf(res['originalFile']) : null
  let view: FeedEdit | null = null
  if (tool === 'Write') {
    const content = str(res['content']) || str(input['content'])
    view = res['type'] === 'create' ? editView(path, null, content) : orig !== null ? editView(path, orig, content) : patchView(path, patch)
  } else if (tool === 'NotebookEdit') {
    view = editView(path, str(res['old_source']), str(res['new_source']))
  } else {
    const edits: Obj[] = tool === 'MultiEdit' ? (Array.isArray(input['edits']) ? input['edits'].filter(isObj) : []) : [input]
    let after: string | null = orig ?? (edits.length > 0 && str(edits[0]?.['old_string']) === '' ? '' : null)
    for (const ed of edits) {
      after = after === null ? null : applied(after, lf(str(ed['old_string'])), lf(str(ed['new_string'])), ed['replace_all'] === true)
    }
    if (after !== null && edits.length > 0) {
      view = editView(path, orig, after) // no original: an Edit that created the file
    } else if (patch.length > 0) {
      view = patchView(path, patch)
    }
  }
  if (view !== null && (view.created || view.hunks.length > 0) && PLAN_DRAFTS.includes(path.replace(/\\/g, '/'))) {
    // 3.24 T13C: a plan draft is one line; the plan-mode view shows the plan itself.
    return { ...view, hunks: [], more: 0, label: view.created ? 'Plan drafted' : 'Plan draft updated' }
  }
  return view === null || (view.hunks.length === 0 && !view.created) ? null : view
}
