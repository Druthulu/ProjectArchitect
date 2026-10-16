// The partner's six tools `mcp__pa-watch__<name>` (agents/pa-claude.md, "The mod's tools"): their specs, input checks,
// and the inbox request each good call becomes. `requestName` is a copy of the router's mod/pa/hooks/inbox.ts naming,
// kept identical (plugins never import across each other); the same test vectors pin both. Pure.

export type ToolName = 'plan' | 'ask' | 'topic' | 'release' | 'plan_edit' | 'steer'

export const TOOL_NAMES: readonly ToolName[] = ['plan', 'ask', 'topic', 'release', 'plan_edit', 'steer']

const LATER = 'Returns at once; the answer arrives later as a message, so end your turn after calling it.'
/** 3.23 T13d.c3: release, plan_edit and steer send no confirmation that only says done. */
const QUIET = 'Returns at once and is taken; a message follows only when it is refused or changes the outcome, so end your turn after calling it.'
const QUIET_KINDS: readonly string[] = ['release', 'plan_edit', 'steer']
const NO_REPLY = 'Returns at once; no reply follows.'

/** What `$.tool.register` takes for each tool. */
export const TOOL_SPECS: readonly { name: ToolName; description: string; inputSchema: Record<string, unknown> }[] = [
  {
    name: 'plan',
    description: `Presents the plan file at \`path\` to the developer in the plan-mode view. \`kind\` generation or phase: on approval the mod installs and commits it (a phase plan's \`rules\` and \`candidates\` lines are put to the developer one by one); a phase plan sent while REPLAN.md sits beside the approved plan is not shown: it is installed at the next task boundary; adhoc (the default): any other plan. ${LATER}`,
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string' }, kind: { type: 'string', enum: ['generation', 'phase', 'adhoc'] }, title: { type: 'string' },
        rules: { type: 'array', items: { type: 'string' } }, candidates: { type: 'array', items: { type: 'string' } },
      },
      required: ['path'],
    },
  },
  {
    name: 'ask',
    description: `Puts 2-4 short options to the developer as a picker; the choice is their option, their own words, or dismissed. ${LATER}`,
    inputSchema: {
      type: 'object',
      properties: { question: { type: 'string' }, options: { type: 'array', items: { type: 'string' }, minItems: 2, maxItems: 4 } },
      required: ['question', 'options'],
    },
  },
  {
    name: 'topic',
    description: `The conversation has turned to a new subject; the next message goes to a fresh Claude seeded from the journal. ${NO_REPLY}`,
    inputSchema: { type: 'object', properties: { summary: { type: 'string' } } },
  },
  {
    name: 'release',
    description: `Ends the hold you name (talk, review, question with a note for the expert, close with your phase report as the note, which the mod keeps as the phase's recap, stopped: the developer's stopped task resumed with the note, or started over with fresh: true; generation: the next generation's start, when the developer asks for it in this session; session: the loop waiting since this session began, when the developer wants it to go on; update: the PA3 update the mod found, installed after the current task, when the developer says in any words that they want it), so plan work may continue. ${QUIET}`,
    inputSchema: {
      type: 'object',
      properties: { hold: { type: 'string', enum: ['talk', 'review', 'question', 'close', 'stopped', 'generation', 'session', 'update'] }, note: { type: 'string' }, fresh: { type: 'boolean' } },
      required: ['hold'],
    },
  },
  {
    name: 'plan_edit',
    description: `A plan change, as the exact arguments of a tools/plan_edit.py call: during a review or question stop it is a decision, applied at once; otherwise it lands at the next task boundary. Refused, or applied but not committed: a refused: or applied: line. ${QUIET}`,
    inputSchema: { type: 'object', properties: { command: { type: 'string' }, why: { type: 'string' } }, required: ['command'] },
  },
  {
    name: 'steer',
    description: `A developer's correction for the running plan task \`task\` (T<n>), relayed to its expert, which tells its own coder what to stop, keep or undo; nothing is stopped. Refused: a refused: line. ${QUIET}`,
    inputSchema: { type: 'object', properties: { task: { type: 'string' }, text: { type: 'string' } }, required: ['task', 'text'] },
  },
]

type Obj = Record<string, unknown>

function field(input: Obj, key: string, required: boolean): string | null {
  const v = input[key]
  if (v === undefined) {
    return required ? `${key} is required` : null
  }
  if (typeof v !== 'string') {
    return `${key} must be a string`
  }
  return required && v.trim() === '' ? `${key} is empty` : null
}

function lines(input: Obj, key: string): string | null {
  const v = input[key]
  return v === undefined || (Array.isArray(v) && v.every(x => typeof x === 'string')) ? null : `${key} must be an array of strings`
}

/** Why the input is bad, or null when it is good. */
export function validateInput(name: string, input: unknown): string | null {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) {
    return 'input must be an object'
  }
  const o = input as Obj
  switch (name) {
    case 'plan': {
      const k = o['kind']
      if (k !== undefined && !['generation', 'phase', 'adhoc'].includes(String(k))) {
        return 'kind must be generation, phase or adhoc'
      }
      return field(o, 'path', true) ?? field(o, 'title', false) ?? lines(o, 'rules') ?? lines(o, 'candidates')
    }
    case 'ask': {
      const q = field(o, 'question', true)
      if (q !== null) {
        return q
      }
      const opts = o['options']
      if (!Array.isArray(opts) || opts.length < 2 || opts.length > 4) {
        return 'options must be 2-4 strings'
      }
      return opts.every(x => typeof x === 'string' && x.trim() !== '') ? null : 'options must be non-empty strings'
    }
    case 'topic':
      return field(o, 'summary', false)
    case 'release': {
      const h = field(o, 'hold', true)
      if (h !== null) {
        return h
      }
      if (o['fresh'] !== undefined && typeof o['fresh'] !== 'boolean') {
        return 'fresh must be a boolean'
      }
      return ['talk', 'review', 'question', 'close', 'stopped', 'generation', 'session', 'update'].includes(String(o['hold'])) ? field(o, 'note', false) : 'hold must be talk, review, question, close, stopped, generation, session or update'
    }
    case 'plan_edit':
      return field(o, 'command', true) ?? field(o, 'why', false)
    case 'steer':
      return field(o, 'task', true) ?? field(o, 'text', true)
    default:
      return `unknown tool ${name}`
  }
}

/** The tool's own arguments of a `tool.call` envelope (they are spread beside `tool`, `tool_use_id`, `agentId`). */
export function argsOf(e: Readonly<Obj>): Obj {
  const out: Obj = {}
  for (const [k, v] of Object.entries(e)) {
    if (k !== 'tool' && k !== 'tool_use_id' && k !== 'agentId' && k !== 'consent') {
      out[k] = v
    }
  }
  return out
}

const KIND = /^[a-z][a-z0-9_]*$/

/** The file name of a request; its id is the name without `.json` (copy of the router's). */
export function requestName(ms: number, seq: number, kind: string): string {
  if (!KIND.test(kind)) {
    throw new Error(`bad request kind: ${kind}`)
  }
  return `${String(ms)}-${String(seq)}-${kind}.json`
}

/** The request body the router's drain parses: `{id, kind, caller, input, at}`. */
export function requestBody(id: string, kind: string, caller: string, input: unknown, at: number): string {
  return JSON.stringify({ id, kind, caller, input, at }, null, 1)
}

/** The tool's answer to a queued request. */
export function queuedReply(kind: string, id: string): string {
  return kind === 'topic'
    ? `queued ${kind} ${id}; no reply will follow`
    : QUIET_KINDS.includes(kind)
      ? `queued ${kind} ${id}; a message follows only if it is refused or changes the outcome — end your turn now`
      : `queued ${kind} ${id}; the answer arrives later as a message — end your turn now`
}

/** A tool call's arguments in at most `n` characters, for the stream facts. */
export function argSummary(args: unknown, n = 120): string {
  let text: string
  try {
    text = JSON.stringify(args) ?? ''
  } catch {
    text = String(args)
  }
  return text.length > n ? `${text.slice(0, n - 1)}…` : text
}
