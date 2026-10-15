// 3.24 T17b: each agent's model and effort from the ladder. T17a's resolver (`pa_ledger.py ladder-row`) gives an agent
// type's row for the active account's tier; the spawn hook sets a managed agent's `model`, its `turn.step` sets the
// effort of its requests, and the agent's first step is journalled as a `spawn` entry with what it actually got (doctor
// compares it with the row). Claude (pa-claude) is never set: its model and effort stay the developer's. Pure;
// register.ts makes the `$` calls.
import type { LadderRow, LadderRows } from '../types'

export type { LadderRow, LadderRows } from '../types'

const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']

/** The agent type whose spawn re-checks the tier (the account may have changed since the rows were read). */
export const PLANNER = 'pa-planner'

/** Types the ladder never sets: Claude, whose model and effort follow /model and /effort (until 3.25's auto effort). */
export const UNSET: readonly string[] = ['pa-claude']

/** 3.25 T6b: a spawn description's trailing `[effort <level>]` hint (level an effort or `deep`): the level, '' when none,
 * and the description without it (what is shown). */
const HINT = /\s*\[effort (low|medium|high|xhigh|max|deep)\]\s*$/
export function hintOf(description: string): { level: string; desc: string } {
  const m = HINT.exec(description)
  return m === null ? { level: '', desc: description } : { level: m[1] ?? '', desc: description.slice(0, m.index) }
}

/** The ladder key of a spawn: `<type>@<level>` with a hint (the resolver echoes it as the row's `agent`), else the type. */
export const rowKey = (type: string, level: string): string => (level === '' ? type : `${type}@${level}`)

/** The resolver's argv, or null when pa.json names no interpreter or no ledger path is known; `effort`, a hint's level. */
export function ladderArgv(python: string, ledger: string, agent: string, project: string, effort = ''): string[] | null {
  if (python === '' || ledger === '' || agent === '') {
    return null
  }
  return [python, ledger, 'ladder-row', '--agent', agent, ...(effort === '' ? [] : ['--effort', effort]), '--project', project, '--json']
}

/** The row in the resolver's output (its last non-blank line), or null when it is not `agent`'s well-formed row. */
export function rowOf(stdout: string, agent: string): LadderRow | null {
  const line = stdout.split(/\r?\n/).map(l => l.trim()).filter(l => l !== '').at(-1) ?? ''
  let o: unknown
  try {
    o = JSON.parse(line)
  } catch {
    return null
  }
  if (typeof o !== 'object' || o === null || Array.isArray(o)) {
    return null
  }
  const r = o as Record<string, unknown>
  const str = (k: string): string | null => {
    const v = r[k]
    return typeof v === 'string' ? v : null
  }
  const [model, effort, tier, source] = [str('model'), str('effort'), str('tier'), str('source')]
  const managed = r['managed']
  if (r['agent'] !== agent || typeof managed !== 'boolean' || model === null || effort === null || tier === null || source === null) {
    return null
  }
  return { agent, managed, model, effort: EFFORTS.includes(effort) ? effort as LadderRow['effort'] : '', tier, source }
}

/** The rows with `row` kept: a row of another tier is dropped (the tier changed), so no stale row outlives it. */
export function keepRow(rows: Readonly<LadderRows>, row: LadderRow): LadderRows {
  const out: LadderRows = {}
  for (const [k, v] of Object.entries(rows)) {
    if (v.tier === row.tier) {
      out[k] = v
    }
  }
  out[row.agent] = row
  return out
}

/** The model a spawn is given: a managed row's, unless the spawn is Claude, a fork (it inherits) or a workflow's agent
 * (its content is not rewritable); else null (the spawn is left as it is). */
export function spawnModel(row: LadderRow | null, e: { subagentType: string; fork: boolean; workflow?: unknown }): string | null {
  if (row === null || !row.managed || row.model === '' || e.fork || e.workflow !== undefined || UNSET.includes(e.subagentType)) {
    return null
  }
  return row.model
}

/** The effort an agent's requests are given: a managed row's for its type, never Claude's; else null (left alone). */
export function stepEffort(row: LadderRow | null | undefined, type: string): Exclude<LadderRow['effort'], ''> | null {
  if (row === null || row === undefined || !row.managed || row.effort === '' || type === '' || UNSET.includes(type)) {
    return null
  }
  return row.effort
}

/** The `spawn` journal entry's fields (T17a's doctor reads `agentType`, `model`, `effort`, newest per type): the agent's
 * type and what its first request ran on (`model`, `effort`),
 * and its row (`managed` yes, no or unknown when the resolver gave none; `row_model`, `row_effort`, `tier`, `source`). */
export function spawnFields(o: { ts: string; session: string; agentId: string; type: string; model: string; effort: string; row: LadderRow | null }): Record<string, string> {
  const r = o.row
  return {
    ts: o.ts, session: o.session, agentId: o.agentId, agentType: o.type, model: o.model, effort: o.effort,
    managed: r === null ? 'unknown' : r.managed ? 'yes' : 'no',
    row_model: r?.model ?? '', row_effort: r?.effort ?? '', tier: r?.tier ?? '', source: r?.source ?? '',
  }
}
