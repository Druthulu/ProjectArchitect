// The watcher's journal entries (design.md, "Journal"): the watcher writes its own `thinking` entries, so reasoning
// summaries survive a reload. `entryPath` is a copy of the router's mod/pa/hooks/journal.ts naming, kept identical
// (plugins never import across each other); the same test vectors pin both. Pure.

/** `.claude-state/journal/<YYYY-MM-DD>/<HHMMSS>-<seq><writer>-<who>-<kind>.md`, seq padded to 3 digits. */
export function entryPath(date: string, time: string, seq: number, writer: string, who: string, kind: string): string {
  return `.claude-state/journal/${date}/${time}-${String(seq).padStart(3, '0')}${writer}-${who}-${kind}.md`
}

const two = (n: number): string => String(n).padStart(2, '0')

/** `[YYYY-MM-DD, HHMMSS]` of `ms` in local time. */
export function stamp(ms: number): [string, string] {
  const d = new Date(ms)
  return [`${String(d.getFullYear())}-${two(d.getMonth() + 1)}-${two(d.getDate())}`,
    `${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`]
}

/** An entry: one `key: value` line per field between `---` lines, then the body. */
export function entryText(fields: Readonly<Record<string, string | number>>, body: string): string {
  const lines = Object.entries(fields).map(([k, v]) => `${k}: ${String(v).replace(/[\r\n]+/g, ' ')}`)
  return `---\n${lines.join('\n')}\n---\n${body}\n`
}

/**
 * The journal's `who` for an agent: type `pa-claude` (3.22 T8g; `claude` at T8f, `partner` before) `claude`, `thinker` or `deep-thinker`
 * `thinker` (a who is one `[a-z]+` word); 3.24 T13f, by role (the card's names): `expert-*` `expert`; `memory-curator` `curator`; `retriever-*`
 * `retriever`; `coder*` `coder`; `auditor`, `planner-*` and `pa-planner` (3.24 T17b) `planner`, `critic`, `review` as their role; any other type
 * `coder` (was: every type but these, so expert and curator thinking filed as `coder`); unknown type (`''`): `claude`
 * when the agentId is the router's partner (state.json), else `coder`.
 */
export function whoOf(type: string, agentId: string, partnerId: string, description = ''): string {
  if (type === 'pa-claude' || type === 'partner') { // never the built-in `claude` catch-all (3.22 T8g)
    return 'claude'
  }
  if (type === 'pa-thinker' || type === 'thinker' || type === 'deep-thinker') { // 3.25 T6b: one agent per role
    return 'thinker'
  }
  if (type === 'pa-expert' || type.startsWith('expert-')) {
    return 'expert'
  }
  if (type === 'memory-curator') {
    return 'curator'
  }
  if (type === 'pa-planner') { // 3.24 T17b
    return 'planner'
  }
  if (type === 'pa-coder') { // 3.25 T6b
    return 'coder'
  }
  for (const role of ['retriever', 'coder', 'planner']) {
    if (type === role || type.startsWith(`${role}-`)) {
      return role
    }
  }
  if (type === 'auditor' || type === 'critic' || type === 'review') {
    return type
  }
  if (type === '' && partnerId !== '' && agentId === partnerId) {
    return 'claude'
  }
  return 'coder'
}

/** The display name of an agent type in the stream and the usage line: `pa-claude` → `Claude`, others as is (3.22 T8g). */
export function displayWho(type: string): string {
  return type === 'pa-claude' ? 'Claude' : type
}

/** The facts file's 3.23 T5c fields: who the agent is, its latest approach note, its last three steps, when written;
 * 3.23 T13d.c2: `line`, the last line of its latest feed segment ('' none yet; the band shows it). */
export type MoreFacts = { type: string; description: string; parentId: string; note: string; steps: string[]; updatedAt: number; line: string }

/**
 * The watcher's last-step facts of one agent, `.run/mod/<sid>/agents/<agentId>.json` (3.22 T8f); the router stamps them on
 * answers and builds the running-now note from them (3.23 T5c: `more`).
 */
export function stepFactsText(model: string, effort: string, ctx: number, at: number, more?: MoreFacts): string {
  return JSON.stringify({ model, effort, ctx, at, ...more })
}

/** An approach note as the facts keep it: its first two non-blank lines. */
export function noteLines(text: string): string {
  return text.split(/\r?\n/).map(l => l.trim()).filter(l => l !== '').slice(0, 2).join('\n')
}

/**
 * The journal's `who` of an approach note (3.23 T5c): an expert (`expert-*`) is `T<n>` from its spawn description
 * "T<n> <title>", a coder (`coder*`) `coder`; '' for any other agent (the partner's text is its answer), and for an
 * expert whose description names no task.
 */
export function noteWho(type: string, description: string): string {
  if (type === 'pa-expert' || type.startsWith('expert-')) { // 3.25 T6b: pa-expert, pa-coder
    return /^(T[0-9]+)\b/.exec(description.trim())?.[1] ?? ''
  }
  return /^(pa-)?coder/.test(type) ? 'coder' : ''
}

/** `partner.agentId` of the router's state file text, or '' when absent or unreadable. */
export function partnerIdOf(stateText: string | null): string {
  if (stateText === null) {
    return ''
  }
  try {
    const doc = JSON.parse(stateText) as { partner?: { agentId?: unknown } | null }
    const id = doc.partner?.agentId
    return typeof id === 'string' ? id : ''
  } catch {
    return ''
  }
}
