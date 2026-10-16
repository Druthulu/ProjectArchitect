// T8c: plans in the plan-mode view (design.md "Plans in the plan-mode view (I63)") and the `ask` picker. Pure: the
// queue's items from inbox requests, the deferral, the plan file's path, the main plan file an ExitPlanMode result
// names, the decision line parsed from ExitPlanMode's raw result, and the fallback's and the ask's answer lines.
// register.tsx's presentation tick makes the `$` calls (path 0, the fallback pane and `$.ui.ask`).
// 3.23 T6a1: plan kinds (generation and phase plans are installed by the loop on approval), the dialog guard (no agent
// busy, no draft; the router's `dialog.json` flag, which the loop reads), the trial approve.
import type { Present } from '../types'

/** The fallback's Markdown pane (drawn by register.tsx's `ui.render` hook on `Pane`). */
export const PLAN_PANE = 'pa-plan'
export const FALLBACK_QUESTION = 'Approve this plan?'
export const FALLBACK_OPTIONS = ['Approve', 'Ask for changes', 'Reject'] as const

export const APPROVED = 'Plan decision: approved'
export const REJECTED = 'Plan decision: rejected'
export const changesAsked = (words: string): string => `Plan decision: changes asked: ${words}`
/** "Ask for changes" picked with no words typed under Other. */
export const NO_WORDS = '(no words given)'

type Obj = Record<string, unknown>
const isObj = (x: unknown): x is Obj => typeof x === 'object' && x !== null && !Array.isArray(x)
const str = (o: unknown, key: string): string => (isObj(o) && typeof o[key] === 'string' ? o[key] : '')

const strings = (o: unknown, key: string): string[] => {
  const v = isObj(o) ? o[key] : undefined
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.trim() !== '').map(x => x.trim()) : []
}

/**
 * The queue item of an inbox `plan` (`{path, kind?, title, rules?, candidates?}`) or `ask` (`{question, options}`, 2-4
 * options); else null. A plan's kind defaults to adhoc.
 */
export function presentOf(req: { id: string; kind: string; caller: string; input: unknown; at: number }): Present | null {
  const base = { id: req.id, caller: req.caller, at: req.at, path: '', title: '', question: '', options: [] as string[] }
  if (req.kind === 'plan') {
    const path = str(req.input, 'path').trim()
    const k = str(req.input, 'kind')
    const planKind = k === 'generation' || k === 'phase' ? k : 'adhoc'
    return path === '' ? null : {
      ...base, kind: 'plan', path, title: str(req.input, 'title').trim() || path, planKind,
      rules: strings(req.input, 'rules'), candidates: strings(req.input, 'candidates'),
    }
  }
  if (req.kind === 'ask') {
    const question = str(req.input, 'question').trim()
    const raw = isObj(req.input) && Array.isArray(req.input['options']) ? req.input['options'] : []
    const options = raw.filter((o): o is string => typeof o === 'string' && o.trim() !== '').map(o => o.trim()).slice(0, 4)
    return question === '' ? null : { ...base, kind: 'ask', question, options }
  }
  return null
}

/**
 * Why a dialog must wait, or '' when it may show: an agent `running|pending|waiting` (a permission-mode switch reaches
 * running agents), a draft in the prompt box (the dialog would take its focus). One dialog at a time is the tick's flag.
 * 3.28.1 T7: whatever the loop is doing, a running task included (developer 2026-10-14).
 */
export function presentHold(o: { busy: boolean; draft: string }): string {
  if (o.busy) {
    return 'agent busy'
  }
  return o.draft.trim() !== '' ? 'draft' : ''
}

/**
 * What holds a dialog (presentHold's `busy`), or null when nothing does: `{id, status}` of the holding agent, or
 * `{id: <dialog key>, status: 'dialog'}` for an open permission dialog. 3.23 T13d.c7: a plan waits while any agent is
 * `running|pending|waiting` (its permission-mode switch reaches running agents). 3.24 T8 (L18): an ask waits only while
 * its caller's own turn is `running|pending` (its presentation lands first) or a permission dialog is open (`dialogs`,
 * dialogKey's keys); a loop ask (`caller: 'loop'`) also waits while a pa-claude turn is `running`. `waiting` never holds
 * an ask: by the engine's type doc it is background work, a plan's approval or an Agent call, never a dialog.
 */
export function holdingAgent(p: { kind: string; caller: string }, agents: readonly { id: string; type?: string; status?: string }[],
  dialogs: readonly string[] = []): { id: string; status: string } | null {
  const live = (st: string | undefined): boolean => st === 'running' || st === 'pending'
  const hit = (pick: (a: { id: string; type?: string; status?: string }) => boolean): { id: string; status: string } | null => {
    const a = agents.find(pick)
    return a === undefined ? null : { id: a.id, status: a.status ?? '' }
  }
  if (dialogs.length > 0) {
    return { id: dialogs[0] ?? '', status: 'dialog' }
  }
  if (p.kind === 'plan') {
    return hit(a => live(a.status) || a.status === 'waiting')
  }
  return hit(a => (a.id === p.caller && live(a.status)) || (p.caller === 'loop' && a.type === PARTNER && a.status === 'running'))
}

/** Whether the agents hold a dialog: holdingAgent found a holder. */
export function agentsHold(p: { kind: string; caller: string }, agents: readonly { id: string; type?: string; status?: string }[],
  dialogs: readonly string[] = []): boolean {
  return holdingAgent(p, agents, dialogs) !== null
}

/** Claude's agent type (route.ts PARTNER_TYPE; repeated so this pure module imports no runtime code). */
const PARTNER = 'pa-claude'

/**
 * One permission dialog's key (3.24 T8): its agent (`main` for the main loop), tool and input. classic.PermissionRequest
 * carries no tool_use_id, so its close (PostToolUse, PostToolUseFailure, PermissionDenied) is matched on these three.
 */
export const dialogKey = (agentId: string | undefined, tool: string, input: unknown): string =>
  `${agentId ?? 'main'}|${tool}|${JSON.stringify(input ?? null)}`

/** The open dialogs less those of one agent (`main` for the main loop): its turn ended, so none of its dialogs is up. */
export const dropAgentDialogs = (keys: readonly string[], agentId: string | undefined): string[] =>
  keys.filter(k => !k.startsWith(`${agentId ?? 'main'}|`))

/** The open-dialog flag (3.23 T6a1): `.run/mod/<sid>/dialog.json`, written only by the router, read by the loop. */
export const DIALOG_FILE = 'dialog.json'
export const dialogJson = (open: boolean, id: string, at: number): string => `${JSON.stringify({ open, id, at })}
`
/** The flag file's text says a dialog is open. */
export function dialogOpen(text: string | null): boolean {
  try {
    const v: unknown = JSON.parse(text ?? '')
    return isObj(v) && v['open'] === true
  } catch {
    return false
  }
}

/** PA_MOD_TRIAL_APPROVE=1 (under `-p` neither ExitPlanMode nor `$.ui.ask` exists): a plan approved, an ask its first option. */
export const trialLine = (p: Present): string => (p.kind === 'plan' ? APPROVED : answerLine(p.options[0] ?? null))

/** A plan the loop installs on approval (generation or phase). */
export const installs = (p: Present): boolean => p.kind === 'plan' && (p.planKind === 'generation' || p.planKind === 'phase')

/** 3.28.1 T6: a redraft of an approved phase (REPLAN.md beside an approved PHASE_PLAN.md) goes to the loop to install at
 * the next task boundary, with no plan view: the conversation that asked for it is the approval. */
export const isReplan = (p: Present, replanMd: boolean, planText: string): boolean =>
  p.kind === 'plan' && p.planKind === 'phase' && replanMd && /^Approved:\s*\d/m.test(planText)

/** The partner's plan file: an absolute path as given, a relative one against the repo root. */
export function planFilePath(root: string, path: string): string {
  return /^([A-Za-z]:[\\/]|[\\/])/.test(path) ? path : `${root.replace(/[\\/]+$/, '')}/${path.replace(/^\.[\\/]/, '')}`
}

/** The main plan file an ExitPlanMode result names (`result.filePath`, in plan mode with no plan written), or ''. */
export function learnedFile(result: unknown): string {
  return isObj(result) ? str(result['result'], 'filePath') : ''
}

// The transcript's last `"slug":"<words>"` in its final 256 KB, printed as the main plan file's path (the plans
// directory under CLAUDE_CONFIG_DIR, else ~/.claude); '' when none. Stdlib only, so no `$.fs` 4 MiB cap applies; no
// double quote in the argv (re's `\x22`), so Windows command-line quoting cannot bend it.
const SLUG_PY = "import os,re,sys;p=sys.argv[1];f=open(p,'rb');f.seek(max(0,os.path.getsize(p)-262144));"
  + "m=re.findall(rb'\\x22slug\\x22:\\x22([A-Za-z0-9-]+)\\x22',f.read());"
  + "print(os.path.normpath(os.path.join(os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude'),'plans',m[-1].decode()+'.md')) if m else '')"

/** The argv that prints this session's main plan file from its transcript's slug; null: no python or transcript. */
export function slugArgv(python: string, transcript: string): string[] | null {
  return python === '' || transcript === '' ? null : [python, '-c', SLUG_PY, transcript]
}

/** The plan file the slug run printed, or ''. */
export function slugFile(ran: { exitCode: number; stdout: string } | null): string {
  return ran !== null && ran.exitCode === 0 ? ran.stdout.trim() : ''
}

/** The fallback's transcript lines when its pane is not placed: a heading, then the plan line by line. */
export const planLogLines = (title: string, text: string): string[] =>
  [`▣ plan: ${title}`, ...text.replace(/\r\n/g, '\n').replace(/\n+$/, '').split('\n')]

// 3.24 T13F: the "Ready to code?" dialog's own limits (claude.exe; .run/3.24/r3/planview.md). Past any of them the
// dialog withholds approval of the plan, whoever presents it: a line over 4096 UTF-16 units, a sum of squared line
// lengths over 4·4096², a run of more than 8 zero-width or combining characters, a plan over 200,000 chars.
export const DIALOG_CHARS = 200_000
export const DIALOG_LINE = 4096
export const DIALOG_SQUARES = 4 * 4096 * 4096
const WRAP_AT = 4000
const ZERO_RUN = /[\p{M}\p{Cf}]{9,}/u
// A continuation line starting so would open a new Markdown block instead of joining its paragraph.
const OPENS_BLOCK = /^(?:[-*+>#|=]|\d+[.)](?:\s|$)|```|~~~)/

/** Why the dialog would withhold approval of `text`; '' when it would not. */
export function dialogFault(text: string): string {
  if (text.length > DIALOG_CHARS) {
    return `plan is ${String(text.length)} chars (over ${String(DIALOG_CHARS)})`
  }
  let squares = 0
  for (const [i, l] of text.split('\n').entries()) {
    if (l.length > DIALOG_LINE) {
      return `line ${String(i + 1)} is ${String(l.length)} chars (over ${String(DIALOG_LINE)})`
    }
    if (ZERO_RUN.test(l)) {
      return `line ${String(i + 1)} has a run of over 8 zero-width characters`
    }
    squares += l.length * l.length
  }
  return squares > DIALOG_SQUARES ? `squared line lengths ${String(squares)} (over ${String(DIALOG_SQUARES)})` : ''
}

// A paragraph line over the limit as pieces of at most WRAP_AT, cut after `;` or a sentence's end; null: no such cut.
function wrapLine(line: string): string[] | null {
  if (/^(?: {4}|\t|#|\|)/.test(line)) {
    return null // code, a heading or a table row: a newline would change it
  }
  const out: string[] = []
  let rest = line
  while (rest.length > WRAP_AT) {
    let cut = -1
    let next = -1
    for (const m of rest.slice(0, WRAP_AT + 1).matchAll(/[;.!?] +(?=\S)/g)) {
      const after = m.index + m[0].length
      if (!OPENS_BLOCK.test(rest.slice(after))) {
        cut = m.index + 1
        next = after
      }
    }
    if (cut < 0) {
      return null
    }
    out.push(rest.slice(0, cut))
    rest = rest.slice(next)
  }
  return [...out, rest]
}

/**
 * The copy of a plan the dialog is shown: each line over its limit soft-wrapped (Markdown joins the pieces back, so
 * it reads the same), outside code fences. `why`: the copy still fails a limit ('' when it passes).
 */
export function dialogCopy(text: string): { copy: string; why: string } {
  let fence = false
  const lines: string[] = []
  for (const l of text.split('\n')) {
    if (/^ {0,3}(?:```|~~~)/.test(l)) {
      fence = !fence
    }
    lines.push(...(l.length > DIALOG_LINE && !fence ? wrapLine(l) ?? [l] : [l]))
  }
  const copy = lines.join('\n')
  return { copy, why: dialogFault(copy) }
}

export type Decision = { line: string; known: boolean }

// A refusal's text carries the developer's words after "the user said:" (core's rejection message).
const USER_SAID = /the user said:\s*([\s\S]*)$/i

/**
 * The partner's decision line from ExitPlanMode's raw `$.tool.call` result. Approved: `{result: {plan, filePath},
 * text: "User has approved your plan..."}` (3.21 6d7709f6). A refusal: `{deny}` or an error result (`isError`), whose
 * text after "the user said:" is the feedback. `known` false: a shape not recognized (the line is then `rejected`).
 */
export function decisionLine(result: unknown): Decision {
  if (!isObj(result)) {
    return { line: REJECTED, known: false }
  }
  const deny = typeof result['deny'] === 'string' ? result['deny'] : null
  const text = typeof result['text'] === 'string' ? result['text'] : ''
  const r = result['result']
  if (deny !== null || result['isError'] === true) {
    const msg = deny ?? (text !== '' ? text : typeof r === 'string' ? r : JSON.stringify(r ?? ''))
    const words = (USER_SAID.exec(msg)?.[1] ?? '').trim()
    return { line: words === '' ? REJECTED : changesAsked(words), known: true }
  }
  if ((isObj(r) && typeof r['plan'] === 'string') || /^User has approved/i.test(text)) {
    return { line: APPROVED, known: true }
  }
  return { line: REJECTED, known: false }
}

/** The fallback picker's answer as a decision line; null: dismissed (a rejection). Other's free text is feedback. */
export function fallbackLine(answer: string | null): string {
  if (answer === null || answer === 'Reject') {
    return REJECTED
  }
  if (answer === 'Approve') {
    return APPROVED
  }
  const words = answer === 'Ask for changes' ? '' : answer.trim()
  return changesAsked(words === '' ? NO_WORDS : words)
}

/** An ask's answer for the caller; null: dismissed. */
export const answerLine = (answer: string | null): string => `Answer: ${answer === null ? 'dismissed' : answer}`

/** The `mod/plan` journal body: the title, the path, then the plan text. */
export const planBody = (p: Present, text: string): string => `${p.title}\n${p.path}\n\n${text}`

/** The `mod/prompt` journal body of an ask: the question, then its options. */
export const askBody = (p: Present): string => [p.question, ...p.options.map(o => `- ${o}`)].join('\n')
