// The autopilot's parsers (3.23-autopilot.md, "The loop (router `pa`)" and "Stops, holds and decisions"): the expert's,
// the closer's and the auditor's returns, and the POSIX-style splitter of Claude's `plan_edit` commands. Pure, no `$`.

/** An expert's (or the closer's) return; '' for a field it does not carry. `status` is lower-case. */
export type ExpertContract = {
  status: string
  log: string
  ctx: string
  commit: string
  milestone: string
  recommended: string
  question: string
  /** 3.23 T13: the plain-English summary of 2-4 sentences (optional; '' when absent). */
  summary: string
}

/** A coder's return, as far as its finish card reads it (3.23 T13). */
export type CoderReturn = { status: string; summary: string; commit: string }

/** The auditor's return (agents/auditor.md, "Return only"). */
export type AuditorReturn = {
  status: string
  verdicts: string
  fixed: string
  candidates: string
  file: string
  recommended: string
}

// As pa/hooks/user_prompt_submit.py:30-31: the first STATUS within the first 2000 chars.
const STATUS_RE = /STATUS\s*[:=]\s*([A-Za-z-]+)/i
const STATUS_WINDOW = 2000
const FENCE_RE = /^\s*(```|~~~)/
const FIELD_RE = /^\s*([A-Za-z_]+)\s*[:=]\s?(.*)$/

/**
 * The fields after the first STATUS (STATUS within the first 2000 chars), by `KEY: value` lines; a line that is no known
 * key continues the field before it (QUESTION and RECOMMENDED may wrap), a blank line or a fence ends it. Null without
 * STATUS.
 */
function fieldsAfterStatus(text: string, keys: readonly string[]): Record<string, string> | null {
  const m = STATUS_RE.exec(text.slice(0, STATUS_WINDOW))
  if (m === null) {
    return null
  }
  const out: Record<string, string> = { status: (m[1] ?? '').toLowerCase() }
  scanFields(text.slice(m.index).split(/\r?\n/).slice(1), keys, out)
  return out
}

/** The `KEY: value` lines of `lines` into `out` (fieldsAfterStatus's reading). */
function scanFields(lines: readonly string[], keys: readonly string[], out: Record<string, string>): void {
  let current = ''
  for (const line of lines) {
    if (FENCE_RE.test(line) || line.trim() === '') {
      current = ''
      continue
    }
    const f = FIELD_RE.exec(line)
    const key = f === null ? '' : (f[1] ?? '').toLowerCase()
    if (f !== null && keys.includes(key)) {
      if (out[key] === undefined) {
        out[key] = (f[2] ?? '').trim()
        current = key
      } else {
        current = '' // a second block's field: never overwrites the first
      }
    } else if (key === 'status') {
      break // the next block
    } else if (current !== '') {
      out[current] = `${out[current] ?? ''}\n${line.trim()}`
    }
  }
}

// 3.25 T3 (3.25-loose.md, decision 2): the plain hand-back. The first non-blank line's first word, markdown and trailing
// punctuation stripped, when it is one of these; everything after it is free text.
const PLAIN_WORDS: readonly string[] = ['done', 'question', 'blocked', 'block', 'review', 'handoff', 'partial']
const PLAIN_RE = /^[\s#>*_-]*([A-Za-z]+)[\s*_:.,—–-]*(.*)$/
// A commit hash in free text: a 7-40 hex token after the word "commit" on the same line.
const COMMIT_IN_TEXT_RE = /\bcommit\w*\b[^\n]*?(?<![0-9a-z])([0-9a-f]{7,40})(?![0-9a-z])/i

/**
 * The legacy STATUS block's fields, else a plain report's: `status` the first word, `text` the free text after it (the
 * rest of the first line and the lines after, trimmed), any legacy `KEY:` lines among those lines as fields. Null with
 * neither (`.`, a blank text and `WAITING:` stay null).
 */
function looseFields(text: string, keys: readonly string[]): Record<string, string> | null {
  const legacy = fieldsAfterStatus(text, keys)
  if (legacy !== null) {
    return legacy
  }
  const lines = text.split(/\r?\n/)
  const i = lines.findIndex(l => l.trim() !== '')
  const m = i < 0 ? null : PLAIN_RE.exec(lines[i] ?? '')
  const word = (m?.[1] ?? '').toLowerCase()
  if (m === null || !PLAIN_WORDS.includes(word)) {
    return null
  }
  const out: Record<string, string> = { status: word }
  scanFields(lines.slice(i + 1), keys, out)
  out['text'] = [m[2] ?? '', ...lines.slice(i + 1)].join('\n').trim()
  return out
}

/** The commit: the `commit` field's leading hash, else the field as written; without one, a hash after "commit" in the text. */
function commitOf(f: Record<string, string>, text: string): string {
  const field = f['commit'] ?? ''
  const hash = /^\s*([0-9a-f]{7,40})(?![0-9a-z])/i.exec(field)?.[1]
  return hash ?? (field !== '' ? field : (COMMIT_IN_TEXT_RE.exec(text)?.[1] ?? ''))
}

const EXPERT_KEYS = ['log', 'ctx', 'commit', 'milestone', 'recommended', 'question', 'summary'] as const

/** An expert's return: the legacy block (`STATUS · LOG · CTX · COMMIT · MILESTONE · RECOMMENDED · QUESTION`, optional
 * `SUMMARY`) or a plain report (3.25 T3: `summary` the free text, and for a question `question` too); null with neither.
 * 3.28.8: a plain report's free text wins over a `Summary: <path>` line inside it (TerrainDiffusion T12's card showed
 * only that pointer line). */
export function parseExpertContract(text: string): ExpertContract | null {
  const f = looseFields(text, EXPERT_KEYS)
  if (f === null) {
    return null
  }
  const g = (k: string): string => f[k] ?? ''
  const free = g('text')
  return {
    status: g('status'), log: g('log'), ctx: g('ctx'), commit: commitOf(f, text), milestone: g('milestone'),
    recommended: g('recommended'), question: g('question') || (g('status') === 'question' ? free : ''),
    summary: free || g('summary'),
  }
}

// Every coder key, so none continues the field before it.
const CODER_KEYS = ['changed', 'verified', 'log', 'ctx', 'commit', 'deviations', 'blocker', 'summary'] as const

/** A coder's return (`STATUS · CHANGED · VERIFIED · … · COMMIT · … · SUMMARY`, or a plain report, 3.25 T3): its status,
 * summary and commit; null with neither.
 * 3.24 T12c: `commit` is the field's leading hash when it has one (`75c1b8d (rename, on top of e45aa75)` → `75c1b8d`), so
 * a coder's note after the hash still gives the finish card its commit and files; otherwise the field as written; without
 * the field, a hash after "commit" in the text. */
export function parseCoderReturn(text: string): CoderReturn | null {
  const f = looseFields(text, CODER_KEYS)
  return f === null ? null : { status: f['status'] ?? '', summary: (f['text'] ?? '') || (f['summary'] ?? ''), commit: commitOf(f, text) }
}

const AUDITOR_KEYS = ['verdicts', 'fixed', 'candidates', 'file', 'recommended'] as const

/** The auditor's return (`STATUS · VERDICTS · FIXED · CANDIDATES · FILE · RECOMMENDED`); null without STATUS. */
export function parseAuditorReturn(text: string): AuditorReturn | null {
  const f = fieldsAfterStatus(text, AUDITOR_KEYS)
  if (f === null) {
    return null
  }
  const g = (k: string): string => f[k] ?? ''
  return { status: g('status'), verdicts: g('verdicts'), fixed: g('fixed'), candidates: g('candidates'), file: g('file'), recommended: g('recommended') }
}

/** The only scripts a `plan_edit` command may run, after `PY`. */
export const ALLOWED_SCRIPTS: readonly string[] = ['tools/plan_edit.py', 'tools/rules_add.py']
/** The phase close's `RUN:` lines (3.23 T6b): plan_edit.py gen-close and genend_index.py assemble. */
export const CHAIN_SCRIPTS: readonly string[] = [...ALLOWED_SCRIPTS, 'tools/genend_index.py']

export type Split = { argv: string[] } | { refused: string }

/**
 * Claude's `plan_edit` command as argv, never through a shell: split POSIX-style (single quotes literal; in double quotes
 * a backslash escapes `\ " $` and the backtick; outside quotes a backslash escapes any character). Refused where a shell
 * would substitute (`$(` or a backtick outside single quotes, unescaped) or chain or redirect (`;`, `&&`, `|`, `<`, `>`
 * outside quotes); an unterminated quote; any script but `tools/plan_edit.py` or `tools/rules_add.py`. The script may
 * follow `PY` or any interpreter-looking token, stand first, or be left out (bare args mean plan_edit.py; rules_add.py
 * only when named). The interpreter becomes `python`, one argv element. `allowed` widens the scripts (CHAIN_SCRIPTS).
 */
export function splitCommand(line: string, python: string, allowed: readonly string[] = ALLOWED_SCRIPTS): Split {
  const argv: string[] = []
  let word = ''
  let inWord = false
  let quote: '' | "'" | '"' = ''
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i] ?? ''
    const next = line[i + 1] ?? ''
    if (quote === "'") {
      if (c === "'") {
        quote = ''
      } else {
        word += c
      }
      continue
    }
    if (c === '\\') {
      if (quote === '"') {
        if (next === '\\' || next === '"' || next === '$' || next === '`') {
          word += next
          i += 1
        } else if (next === '\n') {
          i += 1
        } else {
          word += c
        }
      } else if (next === '\n') {
        i += 1
      } else if (next !== '') {
        word += next
        inWord = true
        i += 1
      } else {
        word += c
        inWord = true
      }
      continue
    }
    if (c === '`') {
      return { refused: 'a backtick outside single quotes' }
    }
    if (c === '$' && next === '(') {
      return { refused: '$( command substitution' }
    }
    if (quote === '"') {
      if (c === '"') {
        quote = ''
      } else {
        word += c
      }
      continue
    }
    if (c === ';' || c === '|' || c === '<' || c === '>') {
      return { refused: `${c} outside quotes` }
    }
    if (c === '&' && next === '&') {
      return { refused: '&& outside quotes' }
    }
    if (c === "'" || c === '"') {
      quote = c
      inWord = true
      continue
    }
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      if (inWord) {
        argv.push(word)
        word = ''
        inWord = false
      }
      continue
    }
    word += c
    inWord = true
  }
  if (quote !== '') {
    return { refused: 'an unterminated quote' }
  }
  if (inWord) {
    argv.push(word)
  }
  if (argv.length === 0) {
    return { refused: 'an empty command' }
  }
  // The forms Claude writes: `PY <script> …`, `<interpreter> <script> …`, `<script> …`, or bare plan_edit.py args.
  const rest = isInterpreter(argv[0] ?? '') ? argv.slice(1) : argv
  const first = rest[0] ?? ''
  const script = scriptOf(first, allowed)
  let args: string[]
  if (script !== null) {
    args = [script, ...rest.slice(1)]
  } else if (rest !== argv || !/^[a-z][a-z-]*$/.test(first)) {
    return { refused: `not an allowed script: ${first === '' ? '(none)' : first}` }
  } else if (SHELLS.includes(first)) {
    return { refused: `${first} runs nothing here: only tools/plan_edit.py or tools/rules_add.py` }
  } else {
    args = ['tools/plan_edit.py', ...rest] // bare args mean plan_edit.py
  }
  if (python === '') {
    return { refused: 'no python in pa.json' }
  }
  return { argv: [python, ...args] }
}

// Bare args go to plan_edit.py, whose argparse refuses an unknown subcommand; a shell's name gets a plain refusal instead.
const SHELLS = ['bash', 'sh', 'zsh', 'cmd', 'powershell', 'pwsh', 'env', 'exec']

/** `PY`, or a token naming a Python interpreter (`python`, `python3.14`, `py`, `…/python.exe`). */
function isInterpreter(tok: string): boolean {
  const base = tok.split(/[\\/]/).pop() ?? ''
  return tok === 'PY' || /^(python[\d.]*|py)(\.exe)?$/i.test(base)
}

/** The allowed script a token names (`tools/plan_edit.py`, `./tools/plan_edit.py`, `plan_edit.py`, backslashes), else null. */
function scriptOf(tok: string, allowed: readonly string[]): string | null {
  const t = tok.replace(/\\/g, '/').replace(/^\.\//, '')
  return allowed.find(s => t === s || t === s.slice('tools/'.length)) ?? null
}
