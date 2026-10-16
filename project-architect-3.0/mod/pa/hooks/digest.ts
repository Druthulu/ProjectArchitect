// The running-now note (3.23-autopilot.md, "Visible and steerable"): while work runs, each developer message reaches
// Claude with the running task, its expert and coders, and each one's latest approach note, from the watcher's facts
// files (`.run/mod/<sid>/agents/<id>.json`, extended). Pure, no `$`. 3.23 T13d.c2: no tool steps (the feed block on the
// message carries what the agents said).
import type { AgentFacts, RunningAgent } from '../types'

export const DIGEST_CAP = 800
const NOTE_LINES = 2

const oneLine = (s: string, n: number): string => {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

/** The `‹pa› running now:` block: the task, then the expert and each coder (id, status, note ≤ 2 lines); ≤ ~800 chars. */
export function runningNow(task: { id: string; title: string }, agents: RunningAgent[]): string {
  const out = [`‹pa› running now: ${task.id} ${task.title}`.trim()]
  const ordered = [...agents.filter(a => a.role === 'expert'), ...agents.filter(a => a.role !== 'expert')]
  for (const a of ordered) {
    out.push(`- ${a.role} ${a.id}${a.model === '' ? '' : ` (${a.model})`}${(a.state ?? '') === '' ? '' : `: ${a.state ?? ''}`}`)
    const note = (a.note ?? '').split(/\r?\n/).map(l => l.trim()).filter(l => l !== '').slice(0, NOTE_LINES)
    for (const l of note) {
      out.push(`  note: ${oneLine(l, 200)}`)
    }
  }
  const text = out.join('\n')
  return text.length > DIGEST_CAP ? `${text.slice(0, DIGEST_CAP - 1)}…` : text
}

/** 3.24 T17p: the note while the loop is on and no task runs: no task, the last one done and when, the next, the holds. */
export function idleNow(last: { id: string; hm: string } | null, next: string, holds: readonly string[]): string {
  const parts = ['‹pa› running now: no task']
  if (last !== null) {
    parts.push(`${last.id} done${last.hm === '' ? '' : ` at ${last.hm}`}`)
  }
  parts.push(`next: ${next === '' ? 'none listed' : next}`)
  if (holds.length > 0) {
    parts.push(`waiting on: ${holds.join(', ')}`)
  }
  return parts.join(' · ')
}

/** One facts file's text as AgentFacts, or null when it does not parse. */
function factsOf(text: string): AgentFacts | null {
  try {
    const v: unknown = JSON.parse(text)
    return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as AgentFacts) : null
  } catch {
    return null
  }
}

/** The running task's agents (T5c): its expert, then its descendants by the facts' `parentId`, from `[agentId, file text]`. */
export function taskAgents(expertId: string, files: readonly [string, string][]): RunningAgent[] {
  const all = new Map<string, AgentFacts>()
  for (const [id, text] of files) {
    const f = factsOf(text)
    if (f !== null) {
      all.set(id, f)
    }
  }
  const out: RunningAgent[] = []
  const ex = all.get(expertId)
  out.push({ model: '', effort: '', ctx: 0, at: 0, ...ex, id: expertId, role: 'expert' })
  const ids = new Set([expertId])
  for (let grew = true; grew;) {
    grew = false
    for (const [id, f] of [...all].sort((a, b) => a[0].localeCompare(b[0]))) {
      if (!ids.has(id) && f.parentId !== undefined && ids.has(f.parentId)) {
        ids.add(id)
        out.push({ ...f, id, role: 'coder' })
        grew = true
      }
    }
  }
  return out
}

/** A journal entry the recovery digest keeps (T5d1): a `note` or `thinking` entry of the task's agents, not Claude's or a thinker's;
 * 3.28.1 T12: and the expert's earlier reports (`T<n>-interim`). */
export const isProgressEntry = (name: string): boolean =>
  /-(note|thinking|interim)\.md$/.test(name) && !/-(claude|thinker)-(note|thinking)\.md$/.test(name)

const ENTRY_CHARS = 1_500
const ENTRIES = 30
const THINKING = 10 // 3.28.1 T12: reasoning kept apart (TerrainDiffusion: 281 coder-thinking a day crowded out the notes)

/**
 * TASK_PROGRESS.md written by the mod at recovery (3.23 T5d1) when the hook's kill digest is absent: `git status
 * --short`, `git log --oneline <startSha>..HEAD`, what it waits on (3.28.1 T12), and the task's note and report entries
 * (`[name, text]`, their frontmatter `ts` at or after `since`), oldest first, the last 30, then its thinking, the last 10,
 * each at most 1,500 chars.
 */
export function recoveryDigest(o: {
  id: string; attempt: number; startSha: string; since: number; status: string; log: string; entries: readonly [string, string][]
  waiting?: readonly string[]
}): string {
  const kept: string[] = []
  const thought: string[] = []
  for (const [name, text] of [...o.entries].sort((a, b) => a[0].localeCompare(b[0]))) {
    const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text)
    const ts = Date.parse(/^ts: *(.+)$/m.exec(m?.[1] ?? '')?.[1] ?? '')
    if (!isProgressEntry(name) || !Number.isFinite(ts) || ts < o.since) {
      continue
    }
    const body = (m?.[2] ?? '').trim()
    const to = name.endsWith('-thinking.md') ? thought : kept
    to.push(`### ${name}\n${body.length > ENTRY_CHARS ? `${body.slice(0, ENTRY_CHARS - 1)}…` : body}`)
  }
  const block = (s: string): string => `\`\`\`\n${s.trim() === '' ? '(none)' : s.replace(/^(\s*\n)+/, '').trimEnd()}\n\`\`\``
  return [
    `# TASK_PROGRESS: recovery digest for ${o.id} (attempt ${String(o.attempt)})`,
    '',
    'Written by the mod at a session restart: the expert died with its session and left no progress file. ' +
      'This is the state it left; check the tree, then continue from its last step.',
    '',
    '## git status --short',
    block(o.status),
    '',
    `## git log --oneline ${o.startSha || '?'}..HEAD`,
    block(o.log),
    '',
    '## Waiting on',
    (o.waiting ?? []).length === 0 ? '(nothing recorded)' : (o.waiting ?? []).map(x => `- ${x}`).join('\n'),
    '',
    `## Journal notes and reports since ${new Date(o.since).toISOString()}`,
    kept.length === 0 ? '(none)' : kept.slice(-ENTRIES).join('\n\n'),
    '',
    `## Reasoning, the last ${String(THINKING)}`,
    thought.length === 0 ? '(none)' : thought.slice(-THINKING).join('\n\n'),
    '',
  ].join('\n')
}
