// The fresh partner's first message, a pure function of the journal's entries (design.md "The journal",
// 3.22-partner.md): the recent conversation, the running work, the plan pointer and the open question, ~28k real tokens.
// 3.22 T8g: compact headers: a `## <date>` line where the local day changes, `HH:MM dev:` / `HH:MM Claude:` per
// message (was `### <ISO ts> dev`), and a `— session <id8> ended; its agents are gone —` line at each session change.

// 3.23 T7: entries may come from rolled day files (`<date>.md#<stem>` paths, same order); two more sections before the
// open question, each omitted when its source is absent: `## LOOP` (loop.json) and `## MEMORY` (MEMORY.md's index lines).
// 3.24 T7b: `## CARD` (`card.py slice claude`) before `## MEMORY`, so the card reaches every fresh Claude.
// 3.31 T3: with records (`opts.records`), the conversation is `## Since the last filing (word for word)`: only the pairs
// newer than the newest record's `through`, in RECORDS_CONV_TOKENS, then `records.py seed`'s sections as given.
// 3.32 B4: a fresh Claude starts at ~60k real tokens (a fixed base of ~32k plus this seed). The last exchanges are always
// there word for word, filed or not: `## Recent exchanges`, a line marking where the filed ones begin, in RECENT_EXCHANGES_TOKENS.

import type { LoopState } from '../types'
import { type Entry, firstLine, parseEntryPath } from './journal'

// Budgets are real tokens; tokenChars is the only conversion (the mod hands `records.py seed` its budget in characters).
export const CHARS_PER_TOKEN = 2.85 // 3.32: measured over 14 fresh starts; was chars / 4, which let a start reach ~100k
export const tokenChars = (tokens: number): number => Math.round(tokens * CHARS_PER_TOKEN)
export const charTokens = (chars: number): number => Math.round(chars / CHARS_PER_TOKEN)

export const SEED_BUDGET_TOKENS = 28_000 // was 40_000 at chars / 4; card, memory and plan ~3k, records ~10k, exchanges ~12k, the rest spare
export const RECENT_EXCHANGES_TOKENS = 12_000 // 3.32 B4: was RECORDS_CONV_TOKENS = 20_000, unfiled only, at chars / 4
export const RECORDS_TOKENS = 10_000 // 3.32 B4: `records.py seed`'s budget; was 30_000 at chars / 4
export const GENERATION_TOKENS = 3_000 // 3.32 B5: `phaseend_index.py history-seed`'s budget
export const STATE_TOKENS = 3_000 // 3.32 B6: `records.py state`'s budget
export const MEMORY_CAP_TOKENS = 800 // 3.32 B4: with the card, ~3k; was MEMORY_CAP_CHARS = 6_000
export const CARD_CAP_CHARS = 6_000 // 3.24 T7b: Claude's slice of the card (10k chars, 3.32 B7), ~2.1k real tokens; card.py SEED_CARD_CAP mirrors it

export type Child = { who: string; agentId?: string; status?: string; summary?: string }

export type SeedOptions = {
  children?: Child[]
  planPaths?: string[]
  budgetTokens?: number // default SEED_BUDGET_TOKENS, in real tokens
  session?: string // the session the seed is built for; default the newest pair's (3.22 T8g)
  loop?: { state: LoopState; phase: string } // loop.json and the plan's phase; absent: no LOOP section (3.23 T7)
  memory?: string // .claude-state/memory/MEMORY.md; absent: no MEMORY section (3.23 T7)
  card?: string // `card.py slice claude`; absent: no CARD section (3.24 T7b)
  intake?: string // the PA3 intake's line while it is due (loop.ts INTAKE_WAITS), under `## Plan`; absent: none (3.32 B9)
  records?: { through: string; text: string } // 3.31 T3: the newest record's `through` and `records.py seed`'s output; absent: no record yet
  generation?: string // 3.32 B5: `phaseend_index.py history-seed`'s output, after the records; absent: no phase history
  state?: string // 3.32 B6: `records.py state`'s output, before the records; absent: no pre-PA3 state page in use
}

const STOPS = ['review', 'question', 'close', 'stopped'] as const
const FILED_LINE = '— filed: these and older exchanges are on record —'

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

/** The MEMORY section: MEMORY.md's non-blank lines while they fit `MEMORY_CAP_TOKENS`; '' when it has none. */
export function memorySection(text: string): string {
  return cappedSection('MEMORY', text, tokenChars(MEMORY_CAP_TOKENS), '.claude-state/memory/MEMORY.md')
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
  const budgetChars = tokenChars(opts.budgetTokens ?? SEED_BUDGET_TOKENS)
  const children = opts.children ?? []
  const work = children.length
    ? children
        .map((c) => `- ${c.who}${c.agentId ? ` (${c.agentId})` : ''}${c.status ? ` ${c.status}` : ''}${c.summary ? `: ${c.summary}` : ''}`)
        .join('\n')
    : '(none)'
  const plans = (opts.planPaths?.length ? opts.planPaths.map((p) => `- ${p}`).join('\n') : '(none)') +
    (opts.intake ? `\n\n${opts.intake}` : '')
  const question = openQuestion(entries) ?? '(none)'
  const rec = opts.records
  const head = rec === undefined
    ? 'JOURNAL SEED\n\n## Recent conversation (newest first)\n\n'
    : 'JOURNAL SEED\n\n## Recent exchanges (word for word, newest first)\n\n'
  const records = rec === undefined || rec.text.trim() === '' ? '' : `${rec.text.trim()}\n\n`
  const generation = (opts.generation ?? '').trim() === '' ? '' : `${(opts.generation ?? '').trim()}\n\n`
  const state = (opts.state ?? '').trim() === '' ? '' : `${(opts.state ?? '').trim()}\n\n`
  const loop = opts.loop === undefined ? '' : loopSection(opts.loop)
  const memory = opts.memory === undefined ? '' : memorySection(opts.memory)
  const card = opts.card === undefined ? '' : cardSection(opts.card)
  const tail = `## Running work\n\n${work}\n\n${loop}## Plan\n\n${plans}\n\n${card}${memory}## Open question\n\n${question}\n`

  // Newest first: whole pairs while they fit, then one line each while those fit; the newest pair is never cut.
  // Before a pair: the filed line at the first filed pair, the ended line when its session differs from the newer pair's,
  // the date line when its day does. The exchanges keep their budget unless the seed as a whole runs out.
  const room = Math.min(tokenChars(RECENT_EXCHANGES_TOKENS), budgetChars - head.length - tail.length - records.length - generation.length - state.length)
  const unfiled = (p: Pair): boolean => rec === undefined || [p.msg, p.answer].some((e) => e !== undefined && (e.meta.ts ?? '') > rec.through)
  const ps = pairs(entries).reverse()
  let conv = ''
  let short = false
  let dropped = 0
  let filedMarked = false
  let newerSession = opts.session ?? (ps.length ? sessionOf(ps[0]) : '')
  let newerDate = ''
  for (let i = 0; i < ps.length; i++) {
    const sid = sessionOf(ps[i])
    const date = localStamp(first(ps[i])?.meta.ts).date
    const marks: string[] = []
    if (!filedMarked && !unfiled(ps[i])) {
      filedMarked = true
      marks.push(FILED_LINE)
    }
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
  return head + conv + state + records + generation + tail
}
