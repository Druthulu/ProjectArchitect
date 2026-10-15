// The usage line (design.md, "Activity stream (I49)") and the step helpers the watcher keeps: a tool call's argument
// line for the facts file, ping and hand-back steps, agent depth, planning; (until 3.24 T13C) one live usage line.
// 3.23 T13d.c3: the dim transcript lines (`streamLine`, the `level` kinds) and the `colors` option retired; the live
// feed shows the agents' working lines. 3.24 T13C: the usage line is gone (`usageLine`, `warmOf`, `loopNote`, and the
// step pricing it alone used: `ratesFor`, `stepUsd`); `ctxOf` stays for the facts file. Pure.

/** `text` cut to `n` characters, the last one `…` when cut. */
export function cut(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text
}

/** The first non-blank line of `text`, trimmed, in at most `n` characters. */
export function firstLine(text: string, n: number): string {
  const line = text.split(/\r?\n/).map(l => l.trim()).find(l => l !== '') ?? ''
  return cut(line, n)
}

const PRIMARY = ['command', 'file_path', 'path', 'pattern', 'url', 'query', 'subagent_type', 'description', 'prompt'] as const

/** A tool call's arguments in one line of at most `n`: its main string argument, else the JSON. */
export function argLine(args: unknown, n = 80): string {
  if (typeof args === 'object' && args !== null && !Array.isArray(args)) {
    const o = args as Record<string, unknown>
    for (const k of PRIMARY) {
      const v = o[k]
      if (typeof v === 'string' && v.trim() !== '') {
        return firstLine(v, n)
      }
    }
  }
  return cut(json(args).replace(/\s+/g, ' '), n)
}

function json(v: unknown): string {
  try {
    return JSON.stringify(v) ?? ''
  } catch {
    return String(v)
  }
}

/** A `.` ping turn's answer: `.` alone and no tool calls (it draws no lines at all). */
export function isPing(answer: string, toolUses: number): boolean {
  return answer.trim() === '.' && toolUses === 0
}

/** The hand-back tool: its call is the agent's answer (3.22 T8e), never a feed segment. */
export const HANDBACK = 'SubagentHandback'

/** The `message` of a step's SubagentHandback call, or null when the step makes none. */
export function handbackOf(toolUses: readonly { name: string; input: unknown }[]): string | null {
  const use = toolUses.find(u => u.name === HANDBACK)
  if (use === undefined) {
    return null
  }
  const msg = typeof use.input === 'object' && use.input !== null ? (use.input as Record<string, unknown>)['message'] : undefined
  return typeof msg === 'string' ? msg : ''
}

/** A ping step: `.` alone and no tool calls, or a `.` hand-back as its only call (it draws no lines at all). */
export function isPingStep(answer: string, toolUses: readonly { name: string; input: unknown }[]): boolean {
  if (isPing(answer, toolUses.length)) {
    return true
  }
  const hb = handbackOf(toolUses)
  return hb !== null && toolUses.length === 1 && maybePing(answer) && hb.trim() === '.'
}

/** Text streamed so far that may still be a ping's `.` answer. */
export function maybePing(text: string): boolean {
  const t = text.trim()
  return t === '' || t === '.'
}

/** Planning after a tool call: on at a `Skill` call naming pa-planning, off at the `plan` call, else unchanged. */
export function planningAfter(planning: boolean, tool: string, args: unknown): boolean {
  if (tool === 'Skill' && typeof args === 'object' && args !== null) {
    const o = args as Record<string, unknown>
    const name = [o['skill'], o['name'], o['command']].find(v => typeof v === 'string')
    if (typeof name === 'string' && /(^|[:/])pa-planning$/.test(name.trim())) {
      return true
    }
  }
  return tool === 'mcp__pa-watch__plan' ? false : planning
}

/** An agent's depth: the main loop 0, an agent it spawned (Claude, the partner) 1, theirs 2, by `parentId` chains. */
export function depthOf(id: string, agents: readonly { id: string; parentId?: string }[]): number {
  const byId = new Map(agents.map(a => [a.id, a]))
  let depth = 1
  let cur = byId.get(id)?.parentId
  while (cur !== undefined && depth < 16) {
    depth += 1
    cur = byId.get(cur)?.parentId
  }
  return depth
}

/** The usage fields a step reports (`TurnUsage`; `cache_creation` is the API's TTL split, when present). */
export type StepUsage = {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
  cache_creation?: { ephemeral_1h_input_tokens?: number; ephemeral_5m_input_tokens?: number }
  model?: string
}

const n0 = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/** The context of a step: input + cache read + cache write. */
export function ctxOf(u: StepUsage | null): number {
  return u === null ? 0 : n0(u.input_tokens) + n0(u.cache_read_input_tokens) + n0(u.cache_creation_input_tokens)
}
