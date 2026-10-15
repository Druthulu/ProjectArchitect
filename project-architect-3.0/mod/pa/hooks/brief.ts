// The autopilot's briefs (3.23-autopilot.md, "The loop (router `pa`)"): the expert's (project-architect §1, filled as
// agents/pa-session.md step 3 fills it), the memory-curator's, the auditor's (agents/auditor.md) and the
// health FIX expert's. One `KEY: value` line per field. Pure, no `$`.

/** One task as `PY tools/plan_edit.py show --task T<n> --json` (and `next --brief --json`) prints it. */
export type PlanTask = {
  id: string
  status: string
  agent: string
  coder: string | null
  effort: string | null
  review: boolean
  wait_for: string | null
  title: string | null
  done_when: string | null
  verify: string | null
  reads: string[]
  files: string[]
  deps: string[]
  est_ctx: string | null
}

export type ExpertOpts = {
  /** A respawn's progress file (`phase-ends/current/TASK_PROGRESS.md`). */
  progress?: string
  /** A release note or a steer carried into a respawn. */
  note?: string
  /** Trial knob: hand off after the first coder report. */
  trialHandoff?: boolean
  /** Trial knob: ask a question, "whether to <this>". */
  trialQuestion?: string
  /** Trial knob (3.23 T5c): delegate even a small change, so a coder exists to be steered. */
  trialCoder?: boolean
  /** 3.24 T7b: `PY tools/card.py slice expert`'s output, carried after the fields; absent or '': none. */
  card?: string
}

export const EXPERT_RETURN = 'your report: one plain first word, done, question, blocked, review or handoff, then free text'

const lines = (fields: [string, string | undefined][]): string =>
  fields.filter((f): f is [string, string] => f[1] !== undefined && f[1] !== '').map(([k, v]) => `${k}: ${v}`).join('\n')

/** The plan through the tool, never the file: the four sections the expert reads, then its entry. */
export function planCommands(id: string): string {
  return `PY tools/plan_edit.py show --section Context; show --section Interfaces; show --section Cookbook; show --section Research; show --task ${id} (one Bash call; never the file)`
}

/** A `reads:` item as the summary path: `T3` and `tasks/T3.md` are this phase's; another path stays as written. */
export function readsPath(item: string): string {
  if (/^T[0-9][0-9.]*$/.test(item)) {
    return `phase-ends/current/tasks/${item}.md`
  }
  if (item.startsWith('tasks/')) {
    return `phase-ends/current/${item}`
  }
  return item
}

export function expertBrief(task: PlanTask, opts: ExpertOpts = {}): string {
  const notes: string[] = []
  if (opts.note !== undefined && opts.note !== '') {
    notes.push(opts.note)
  }
  if (opts.trialHandoff === true) {
    notes.push("hand back `handoff` after your first coder's report, with TASK_PROGRESS.md written")
  }
  if (opts.trialQuestion !== undefined && opts.trialQuestion !== '') {
    notes.push(`hand back \`question\` asking whether to ${opts.trialQuestion}`)
  }
  if (opts.trialCoder === true) {
    notes.push('trial: delegate the change to pa-coder even though it is small')
  }
  if (task.review) {
    notes.push("this task ends at a developer review: fill the summary's Review: block and hand back `review`")
  }
  const done = [task.done_when ?? '', task.verify === null ? '' : `verify: ${task.verify}`].filter(x => x !== '').join(' · ')
  const card = (opts.card ?? '').trim()
  return lines([
    ['TASK', `${task.id} ${task.title ?? ''}`.trim()],
    ['PLAN', planCommands(task.id)],
    ['HOW', task.files.length === 0 ? 'none named' : task.files.join(', ')],
    ['LOGS TO READ', task.reads.length === 0 ? 'none' : task.reads.map(readsPath).join(', ')],
    ['CODER', task.coder ?? 'none'],
    ['EFFORT', task.effort ?? 'medium'],
    ['PROGRESS', opts.progress],
    ['NOTE', notes.join(' · ')],
    ['DONE WHEN', done],
    ['RETURN', EXPERT_RETURN],
  ]) + (card === '' ? '' : `\nCARD (PY tools/card.py slice expert; the card's expert sections, already read):\n${card}`)
}

/** The memory-curator's brief from the seed's `curate:` line (3.24 T8b: no second MEMORY field when the line names it). */
export function curatorBrief(curateLine: string, memoryDir: string): string {
  return lines([['CURATE', curateLine], ['MEMORY', / MEMORY:/.test(curateLine) ? undefined : memoryDir], ['RETURN', 'recap']])
}

/** The auditor's brief (agents/auditor.md:19): `AUDIT · PHASE · TABLES · PREVIOUS · RETURN`. */
export function auditorBrief(o: { flag: string; phase: string; tables: string; previous: string }): string {
  return lines([
    ['AUDIT', o.flag === '' ? 'generation start' : o.flag],
    ['PHASE', o.phase],
    ['TABLES', o.tables],
    ['PREVIOUS', o.previous === '' ? '—' : o.previous],
    ['RETURN', 'contract'],
  ])
}

/** 3.24 T6: the one-off expert task for a PA3 update's conflicts; the mod runs the project step again on its return. */
export function upgradeBrief(file: string): string {
  return lines([
    ['TASK', 'UPGRADE'],
    ['FILE', `${file}: one line per managed file the developer edited that PA3 also changed; its upstream copy is staged beside it as <file>.upstream`],
    ['HOW', 'per line, compare the developer\'s file with the upstream copy and end the line with `| resolve: keep`, `| resolve: upstream` or `| resolve: merged: <path>` (the merged file written at <path>); never run the installer'],
    ['DONE WHEN', 'every line ends in a resolve: verdict (the mod then runs the project step once more to apply them)'],
    ['RETURN', EXPERT_RETURN],
  ])
}

/** The health FIX expert's brief for `.run/health.json` `savings_missing.row`. */
export function fixBrief(row: string): string {
  return lines([
    ['TASK', 'FIX'],
    ['ROW', row],
    ['DONE WHEN', 'doctor 0 FAIL and the statusline shows the value'],
    ['RETURN', EXPERT_RETURN],
  ])
}
