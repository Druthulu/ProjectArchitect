// 3.23 T13d: the watcher's side of the live feed. Each visible-text segment of an agent's step is one record file in
// the feed folder's `in/` (`kind` "text"; 3.23 T13d.c7: statements only, the c5 step-summary records are gone), which the router plugin's monitor (`mod/pa/feed/pa_feed.py`) numbers and prints. Pure helpers;
// register.ts makes the `$` calls.
import type { FeedEdit } from './edit'

/** Agents whose text never feeds: retrievers. 3.24 T13C: the memory curator and the auditor feed (was: never). */
export function feeds(type: string): boolean {
  return !type.startsWith('retriever-')
}

/** 3.24 T17g: agents the watcher announces with a start line at their first request, so the developer sees their model
 * and effort even if they never feed (round 4: the thinker at max thought 15 minutes and fed nothing): the thinkers, the
 * planners, coders, the curator and the auditor. Not experts: the router writes their start lines. */
export function startsLine(type: string): boolean {
  return ['pa-thinker', 'thinker', 'deep-thinker', 'pa-planner', 'planner-gen', 'planner-phase', 'memory-curator', 'auditor'].includes(type) || /^(pa-)?coder/.test(type) // 3.25 T6b: pa-thinker, pa-coder
}

/** 3.24 T17g: a start line's text: `Starting: <the spawn's description>` (`Starting` when it has none). */
export const startText = (desc: string): string => (desc.trim() === '' ? 'Starting' : `Starting: ${desc.trim()}`)

/**
 * The feed folder, absolute: state.json's `feed.dir` (project-relative, or absolute) under `root`, else
 * `<root>/.run/mod/<sid>/feed`.
 */
export function feedDirOf(stateText: string | null, root: string, sid: string): string {
  let dir = ''
  try {
    const doc = stateText === null ? null : (JSON.parse(stateText) as { feed?: { dir?: unknown } | null })
    dir = typeof doc?.feed?.dir === 'string' ? doc.feed.dir : ''
  } catch {
    dir = ''
  }
  if (dir === '') {
    return `${root}/.run/mod/${sid}/feed`
  }
  return /^([A-Za-z]:)?[\\/]/.test(dir) ? dir.replace(/\\/g, '/') : `${root}/${dir.replace(/\\/g, '/')}`
}

/** The band's working line of a feed segment (3.23 T13d.c2): its last non-empty line, whitespace collapsed. */
export function lastLine(text: string): string {
  const lines = text.split(/\r?\n/).map(l => l.replace(/\s+/g, ' ').trim()).filter(l => l !== '')
  return lines[lines.length - 1] ?? ''
}

/** A record file's name: 13-digit ms and a zero-padded sequence, so names sort in order. */
export function feedName(ms: number, seq: number): string {
  return `${String(ms).padStart(13, '0')}-${String(seq).padStart(6, '0')}.json`
}

export type FeedKind = 'summary' | 'text' | 'edit'

/** One record file's text (the feed contract): one JSON object and a final newline; `kind` is "text" (the agent's
 * own text). 3.23 T13d.c5 also wrote "summary" (a step's `thinking`); c7 stopped it, the router still reads old ones.
 * 3.24 T8b: "edit", a file edit's card (edit.ts): `text` its header line, `edit` its parts. */
export function feedRecord(r: { agent: string; type: string; desc: string; planning: boolean; text: string; at: number; kind: FeedKind; edit?: FeedEdit }): string {
  const edit = r.edit === undefined ? {} : { edit: r.edit }
  return `${JSON.stringify({ agent: r.agent, type: r.type, desc: r.desc, planning: r.planning, text: r.text, at: r.at, kind: r.kind, ...edit })}\n`
}

// 3.24 T13n: Opus 5.5 "narration" blocks are `thinking` blocks Claude Code draws white, as statements; they feed like
// text. Claude Code 2.1.296 tells them apart by the signature (`Nsn`): protobuf field 2, then 1, then string field 8 is
// "narration". A signature that cannot be read is no narration (fail-safe: the block stays journal-only).

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/** Standard base64 to bytes; null on a character outside the alphabet. */
function base64Bytes(s: string): number[] | null {
  const out: number[] = []
  let acc = 0
  let bits = 0
  for (const c of s.replace(/=+$/, '')) {
    const v = B64.indexOf(c)
    if (v < 0) {
      return null
    }
    acc = ((acc << 6) | v) & 0xffffff
    bits += 6
    if (bits >= 8) {
      bits -= 8
      out.push((acc >> bits) & 0xff)
    }
  }
  return out
}

/** A protobuf varint at `i`: [value, next index], or null past the end. */
function varint(b: number[], i: number): [number, number] | null {
  let r = 0
  let s = 0
  while (i < b.length && s < 64) {
    const x = b[i] ?? 0
    i += 1
    r += (x & 0x7f) * 2 ** s
    s += 7
    if (x < 0x80) {
      return [r, i]
    }
  }
  return null
}

/** The bytes of the first length-delimited field `num` of a protobuf message, or null. */
function protoField(b: number[], num: number): number[] | null {
  let i = 0
  while (i < b.length) {
    const k = varint(b, i)
    if (k === null) {
      return null
    }
    i = k[1]
    const f = Math.floor(k[0] / 8)
    const w = k[0] % 8
    if (w === 0) {
      const v = varint(b, i)
      if (v === null) {
        return null
      }
      i = v[1]
    } else if (w === 1) {
      i += 8
    } else if (w === 5) {
      i += 4
    } else if (w === 2) {
      const n = varint(b, i)
      if (n === null || n[1] + n[0] > b.length) {
        return null
      }
      if (f === num) {
        return b.slice(n[1], n[1] + n[0])
      }
      i = n[1] + n[0]
    } else {
      return null
    }
  }
  return null
}

/** Whether a thinking block's signature marks it as narration (Claude Code's `Nsn`); false when it cannot be read. */
export function isNarration(sig: unknown): boolean {
  if (typeof sig !== 'string' || sig === '') {
    return false
  }
  const b = base64Bytes(sig)
  const s = b === null ? null : protoField(b, 2)
  const h = s === null || s.length === 0 ? null : protoField(s, 1)
  const t = h === null || h.length === 0 ? null : protoField(h, 8)
  return t !== null && String.fromCharCode(...t) === 'narration'
}

/** One content block of a step as it streamed (text and thinking joined per block, a tool call by its id). */
export type StepBlock = { kind: 'text' | 'thinking'; index: number; text: string } | { kind: 'tool'; index: number; id: string; handback: boolean }

const API_TYPE = { text: 'text', thinking: 'thinking', tool: 'tool_use' } as const

/**
 * The block indexes of the step's narration blocks, from `$.session.messages({ agentId, as: 'api' })` read at the step's
 * end (the probe: it then holds the step's own message, mostly last; a quick tool's result may follow it). The step's
 * message is the newest assistant one that lines up with the streamed blocks (each block's type, and each tool call's
 * id, at its index; one tool call at least); none lines up, no narration.
 */
export function narrationIndexes(found: unknown, blocks: readonly StepBlock[]): Set<number> {
  const none = new Set<number>()
  if (!Array.isArray(found) || !blocks.some(b => b.kind === 'tool')) {
    return none
  }
  const linesUp = (m: { role?: unknown; content?: unknown } | null | undefined): m is { content: Record<string, unknown>[] } =>
    m?.role === 'assistant' && Array.isArray(m.content) && blocks.every(b => {
      const c = (m.content as Record<string, unknown>[])[b.index]
      return c !== undefined && c !== null && c['type'] === API_TYPE[b.kind] && (b.kind !== 'tool' || c['id'] === b.id)
    })
  const msg = [...(found as ({ role?: unknown; content?: unknown } | null)[])].reverse().find(linesUp)
  if (msg === undefined || !linesUp(msg)) {
    return none
  }
  const content = msg.content
  const out = new Set<number>()
  content.forEach((c, j) => {
    if (c['type'] === 'thinking' && isNarration(c['signature'])) {
      out.add(j)
    }
  })
  return out
}

/**
 * A step's feed lines in block order: the text since the last tool call before each tool call but the hand-back (text
 * blocks joined, as 3.23 T13d), each narration block on its own; `tail` adds those after the last tool call.
 */
export function feedPieces(blocks: readonly StepBlock[], narration: ReadonlySet<number>, tail: boolean): string[] {
  const out: string[] = []
  let seg: string[] = []
  let text = ''
  const close = (): void => {
    seg.push(text)
    text = ''
  }
  for (const b of blocks) {
    if (b.kind === 'text') {
      text += b.text
    } else if (b.kind === 'thinking') {
      if (narration.has(b.index)) {
        close()
        seg.push(b.text)
      }
    } else {
      close()
      if (!b.handback) {
        out.push(...seg)
      }
      seg = []
    }
  }
  close()
  if (tail) {
    out.push(...seg)
  }
  return out.map(t => t.trim()).filter(t => t !== '')
}
