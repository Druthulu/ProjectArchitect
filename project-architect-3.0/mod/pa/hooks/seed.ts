// The fresh partner's first message, a pure function of the journal's entries (design.md "The journal",
// 3.22-partner.md): the recent conversation, the running work, the plan pointer and the open question, ~40k tokens.
// 3.22 T8g: compact headers: a `## <date>` line where the local day changes, `HH:MM dev:` / `HH:MM Claude:` per
// message (was `### <ISO ts> dev`), and a `— session <id8> ended; its agents are gone —` line at each session change.

// 3.23 T7: entries may come from rolled day files (`<date>.md#<stem>` paths, same order); two more sections before the
// open question, each omitted when its source is absent: `## LOOP` (loop.json) and `## MEMORY` (MEMORY.md's index lines).
// 3.24 T7b: `## CARD` (`card.py slice claude`) before `## MEMORY`, so the card reaches every fresh Claude.

import type { LoopState } from '../types'
import { type Entry, firstLine, parseEntryPath } from './journal'

export const SEED_BUDGET_TOKENS = 40_000
export const MEMORY_CAP_CHARS = 6_000 // ~1.5k tokens
export const CARD_CAP_CHARS = 6_000 // 3.24 T7b: the card's cap is 7k chars; Claude's slice is part of it

export type Child = { who: string; agentId?: string; status?: string; summary?: string }

export type SeedOptions = {
  children?: Child[]
  planPaths?: string[]
  budgetTokens?: number // default SEED_BUDGET_TOKENS; estimated as chars / 4
  session?: string // the session the seed is built for; default the newest pair's (3.22 T8g)
  loop?: { state: LoopState; phase: string } // loop.json and the plan's phase; absent: no LOOP section (3.23 T7)
  memory?: string // .claude-state/memory/MEMORY.md; absent: no MEMORY section (3.23 T7)
  card?: string // `card.py slice claude`; absent: no CARD section (3.24 T7b)
  firstRun?: string // a pending first run's line (loop.ts FIRST_RUN_WAITS), under `## Plan`; absent: none (3.24 T7b-2c)
}

const STOPS = ['review', 'question', 'close', 'stopped'] as const

/** The LOOP section: the phase, the task in flight, the hold stack and the open stop (the topmost review/question/close). */
export function loopSection(l: { state: LoopState; phase: string }): string {
  const s = l.state
  const t = s.task
  const stop = [...s.holds].reverse().find((h) => (STOPS as readonly string[]).includes(h))
  const lines = [
    `- phase: ${l.phase === '' ? '(unknown)' : l.phase}; state: ${s.state}`,
    `- task: ${t === null ? '(none)' : `${t.id} ${t.title} (attempt ${String(t.attempt)})`}`,
    `- holds: ${s.holds.length ? s.holds.join(', ') : '(none)'}`,
    `- open stop: ${stop === undefined ? '(none)' : `${stop}${t === null ? '' : ` ${t.id}`}`}`,
  ]
  return `## LOOP\n\n${lines.join('\n')}\n\n`
}

/** `## <name>` with the text's non-blank lines while they fit `cap` chars, then how many more and where; '' none. */
function cappedSection(name: string, text: string, cap: number, where: string): string {
  const lines = text.replace(/\r\n/g, '\n').split('\n').filter((l) => l.trim() !== '')
  if (!lines.length) return ''
  const kept: string[] = []
  let size = 0
  for (const l of lines) {
    if (size + l.length + 1 > cap) break
    kept.push(l)
    size += l.length + 1
  }
  const more = lines.length - kept.length
  return `## ${name}\n\n${kept.join('\n')}${more ? `\n(${String(more)} more lines; see ${where})` : ''}\n\n`
}

/** The MEMORY section: MEMORY.md's non-blank lines while they fit `MEMORY_CAP_CHARS`; '' when it has none. */
export function memorySection(text: string): string {
  return cappedSection('MEMORY', text, MEMORY_CAP_CHARS, '.claude-state/memory/MEMORY.md')
}

/** 3.24 T7b: the CARD section, `card.py slice claude`'s output (the card's sections tagged for Claude, `## Developer`
 * among them), capped like MEMORY; '' when empty. */
export function cardSection(text: string): string {
  return cappedSection('CARD', text, CARD_CAP_CHARS, 'HOW_WE_WORK.md (PY tools/card.py slice claude)')
}

type Pair = { msg?: Entry; answer?: Entry }

function role(e: Entry): 'msg' | 'answer' | null {
  const n = parseEntryPath(e.path)
  if (n?.who === 'dev' && n.kind === 'msg') return 'msg'
  if ((n?.who === 'claude' || n?.who === 'partner') && n.kind === 'answer') return 'answer' // partner: before 3.22 T8f
  return null
}

function chronological(entries: Entry[]): Entry[] {
  return [...entries].sort((a, b) => {
    const ta = a.meta.ts ?? ''
    const tb = b.meta.ts ?? ''
    if (ta !== tb) return ta < tb ? -1 : 1
    return a.path < b.path ? -1 : a.path > b.path ? 1 : 0
  })
}

/** Dev msg / partner answer pairs, oldest first; a msg with no answer yet or an answer with no msg is a half pair. */
export function pairs(entries: Entry[]): Pair[] {
  const out: Pair[] = []
  for (const e of chronological(entries)) {
    const r = role(e)
    if (r === 'msg') out.push({ msg: e })
    else if (r === 'answer') {
      const last = out[out.length - 1]
      if (last && last.msg && !last.answer) last.answer = e
      else out.push({ answer: e })
    }
  }
  return out
}

/** `{ date: 'YYYY-MM-DD', hm: 'HH:MM' }` of an ISO ts in the machine's local zone (3.22 T8g); empty when unparsable. */
export function localStamp(ts: string | undefined): { date: string; hm: string } {
  const d = new Date(ts ?? '')
  if (Number.isNaN(d.getTime())) return { date: '', hm: '' }
  const p2 = (n: number): string => String(n).padStart(2, '0')
  return { date: `${String(d.getFullYear())}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`, hm: `${p2(d.getHours())}:${p2(d.getMinutes())}` }
}

const hm = (e: Entry): string => localStamp(e.meta.ts).hm
const first = (p: Pair): Entry | undefined => p.msg ?? p.answer
const sessionOf = (p: Pair): string => p.msg?.meta.session ?? p.answer?.meta.session ?? ''

function whole(p: Pair): string {
  const parts: string[] = []
  if (p.msg) parts.push(`${hm(p.msg)} dev:\n${p.msg.body.trim()}`)
  if (p.answer) parts.push(`${hm(p.answer)} Claude:\n${p.answer.body.trim()}`)
  return `${parts.join('\n\n')}\n\n`
}

function oneLine(p: Pair): string {
  const f = first(p)
  const dev = p.msg ? ` dev: ${firstLine(p.msg.body)}` : ''
  const partner = p.answer ? ` Claude: ${firstLine(p.answer.body)}` : ''
  return `- ${f ? hm(f) : ''}${dev}${dev && partner ? ' /' : ''}${partner}\n`
}

/** The last paragraph of the partner's last answer when it ends in a question and no dev msg followed; else null. */
export function openQuestion(entries: Entry[]): string | null {
  const conv = chronological(entries).filter((e) => role(e) !== null)
  const last = conv[conv.length - 1]
  if (!last || role(last) !== 'answer') return null
  const body = last.body.trim()
  if (!body.endsWith('?')) return null
  const paras = body.split(/\n\s*\n/)
  return paras[paras.length - 1].trim()
}

export function buildSeed(entries: Entry[], opts: SeedOptions = {}): string {
  const budgetChars = (opts.budgetTokens ?? SEED_BUDGET_TOKENS) * 4
  const children = opts.children ?? []
  const work = children.length
    ? children
        .map((c) => `- ${c.who}${c.agentId ? ` (${c.agentId})` : ''}${c.status ? ` ${c.status}` : ''}${c.summary ? `: ${c.summary}` : ''}`)
        .join('\n')
    : '(none)'
  const plans = (opts.planPaths?.length ? opts.planPaths.map((p) => `- ${p}`).join('\n') : '(none)') +
    (opts.firstRun ? `\n\n${opts.firstRun}` : '')
  const question = openQuestion(entries) ?? '(none)'
  const head = 'JOURNAL SEED\n\n## Recent conversation (newest first)\n\n'
  const loop = opts.loop === undefined ? '' : loopSection(opts.loop)
  const memory = opts.memory === undefined ? '' : memorySection(opts.memory)
  const card = opts.card === undefined ? '' : cardSection(opts.card)
  const tail = `## Running work\n\n${work}\n\n${loop}## Plan\n\n${plans}\n\n${card}${memory}## Open question\n\n${question}\n`

  // Newest first: whole pairs while they fit, then one line each while those fit; the newest pair is never cut.
  // Before a pair: the ended line when its session differs from the newer pair's, the date line when its day does.
  const room = budgetChars - head.length - tail.length
  const ps = pairs(entries).reverse()
  let conv = ''
  let short = false
  let dropped = 0
  let newerSession = opts.session ?? (ps.length ? sessionOf(ps[0]) : '')
  let newerDate = ''
  for (let i = 0; i < ps.length; i++) {
    const sid = sessionOf(ps[i])
    const date = localStamp(first(ps[i])?.meta.ts).date
    const marks: string[] = []
    if (sid !== '' && sid !== newerSession) marks.push(`— session ${sid.slice(0, 8)} ended; its agents are gone —`)
    if (date !== '' && date !== newerDate) marks.push(`## ${date}`)
    const pre = marks.map((m, k) => `${k === 0 && conv !== '' && !conv.endsWith('\n\n') ? '\n' : ''}${m}\n\n`).join('')
    if (sid !== '') newerSession = sid
    if (date !== '') newerDate = date
    const w = whole(ps[i])
    if (!short && (i === 0 || conv.length + pre.length + w.length <= room)) {
      conv += pre + w
      continue
    }
    short = true
    const line = oneLine(ps[i])
    if (conv.length + pre.length + line.length <= room) {
      conv += pre + line
      continue
    }
    dropped = ps.length - i
    break
  }
  if (short && !conv.endsWith('\n\n')) conv += '\n'
  if (dropped) conv += `(${dropped} older exchanges dropped; see the journal)\n\n`
  if (!ps.length) conv = '(none)\n\n'
  return head + conv + tail
}
