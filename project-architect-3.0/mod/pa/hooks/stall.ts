// 3.32.1 T2: the diagnostic snapshot saved when a reported task is routed past leftovers (loop.doneInName) or has
// waited STALL_NOTICE_MS on what still counts as busy. The pure parts: a helper transcript's tail (its last tool calls, the background work it
// started that never reported back, its last text; the rules of `.run/3.32.1/pa3_stall_evidence.py` `scan` and
// `open_background`), the process list's redaction, and the Markdown, capped at SNAPSHOT_CAP characters. autopilot.tsx
// gathers the inputs and writes `.run/mod/stalls/<task>-<YYYYMMDD-HHMMSS>.md`.

/** The snapshot file's cap, in characters. */
export const SNAPSHOT_CAP = 60_000
/** Only this much of a transcript's end is scanned (a long one's tail). */
export const TAIL_CHARS = 1_000_000
/** The process list's line cap and each line's cut. */
export const PROCESS_LINES = 400
export const PROCESS_CUT = 160
/** The stalls folder, relative to the project root. */
export const STALLS_DIR = '.run/mod/stalls'
/** The process list's command: Windows (`OS` is `Windows_NT`), else ps; run with PROCESS_MS. */
export const PROCESS_MS = 15_000
export const PROCESSES_WIN = ['powershell', '-NoProfile', '-Command',
  "'ProcessId ParentProcessId Name CreationDate CommandLine'; Get-CimInstance Win32_Process | ForEach-Object { " +
  "'{0} {1} {2} {3:yyyy-MM-dd HH:mm:ss} {4}' -f $_.ProcessId, $_.ParentProcessId, $_.Name, $_.CreationDate, $_.CommandLine }"]
export const PROCESSES_POSIX = ['ps', '-eo', 'pid,ppid,etime,args']

/** Whitespace collapsed, cut to `n` characters with an ellipsis. */
export function one(text: unknown, n: number): string {
  const s = String(text ?? '').split(/\s+/).filter(x => x !== '').join(' ')
  return s.length <= n ? s : `${s.slice(0, n - 1)}…`
}

/** `<STALLS_DIR>/<task>-<YYYYMMDD-HHMMSS>.md` from the journal's local `stamp` pair (`YYYY-MM-DD`, `HHMMSS`). */
export function stallFile(task: string, day: string, time: string): string {
  return `${STALLS_DIR}/${task}-${day.replace(/-/g, '')}-${time}.md`
}

type Obj = Record<string, unknown>
const objOf = (v: unknown): Obj => (typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Obj) : {})
const flat = (c: unknown): string =>
  typeof c === 'string' ? c : Array.isArray(c) ? c.map(b => String(objOf(b)['text'] ?? '')).join(' ') : ''

/** A tool call's short argument (pa3_stall_evidence.py `short_arg`). */
function shortArg(name: string, inp: Obj): string {
  const bg = inp['run_in_background'] === true ? '[background] ' : ''
  if (name === 'Bash') {
    return bg + one(inp['command'], 120)
  }
  if (name === 'Agent' || name === 'Task') {
    return `${bg}${String(inp['subagent_type'] ?? '?')}: ${one(inp['description'], 80)}`
  }
  if (name === 'SubagentHandback') {
    return one(inp['message'], 100)
  }
  if (name === 'SendMessage') {
    return `to ${String(inp['to'] ?? '?')}: ${one(inp['summary'] ?? inp['message'], 80)}`
  }
  if (['Read', 'Edit', 'Write', 'NotebookEdit'].includes(name)) {
    return String(inp['file_path'] ?? '').replace(/\\/g, '/').split('/').pop() ?? ''
  }
  if (name === 'Grep' || name === 'Glob') {
    return one(inp['pattern'], 60)
  }
  if (['Monitor', 'TaskStop', 'KillShell', 'BashOutput'].includes(name)) {
    return one(JSON.stringify(inp), 100)
  }
  return ''
}

/** What one helper's transcript tail shows. */
export type Tail = { version: string; tools: string[]; open: string[]; lastText: string }

/**
 * A helper's transcript tail (JSONL): its last `keep` tool calls (`<ts> <name> <arg>`), the background shells and
 * agents it started (a `running in background with ID: <id>`, a timed-out `moved to the background (ID: <id>)` or an
 * Agent's `agentId: <id>` result) that no later
 * `<task-notification>` answered, or whose last notice still says waiting (with that notice's summary), unless an
 * `<agent-message>` from it came after; its last text cut to 300; the newest `version`.
 */
export function transcriptTail(jsonl: string, keep = 15): Tail {
  const text = jsonl.length > TAIL_CHARS ? jsonl.slice(jsonl.indexOf('\n', jsonl.length - TAIL_CHARS) + 1) : jsonl
  const tools: string[] = []
  const uses = new Map<string, { name: string; arg: string }>()
  const started = new Map<string, { what: string; ts: string; arg: string }>()
  const notified = new Map<string, { ts: string; status: string; summary: string }>()
  const from = new Map<string, string>()
  let version = ''
  let lastText = ''
  for (const line of text.split(/\r?\n/)) {
    if (line.trim() === '') {
      continue
    }
    let e: Obj
    try {
      e = objOf(JSON.parse(line))
    } catch {
      continue
    }
    const typ = e['type']
    if (typ !== 'user' && typ !== 'assistant') {
      continue
    }
    const ts = String(e['timestamp'] ?? '')
    if (typeof e['version'] === 'string' && e['version'] !== '') {
      version = e['version']
    }
    const c = objOf(e['message'])['content']
    const blocks: unknown[] = Array.isArray(c) ? c : typeof c === 'string' ? [{ type: 'text', text: c }] : []
    for (const raw of blocks) {
      const b = objOf(raw)
      if (b['type'] === 'tool_use') {
        const name = String(b['name'] ?? '?')
        const arg = shortArg(name, objOf(b['input']))
        uses.set(String(b['id'] ?? ''), { name, arg })
        tools.push(`${ts.slice(0, 19)} ${name} ${arg}`.trim())
      } else if (b['type'] === 'tool_result') {
        const u = uses.get(String(b['tool_use_id'] ?? ''))
        const body = flat(b['content'])
        for (const [rx, what] of [[/(?:running in background with ID|moved to the background \(ID): (\w+)/, 'shell'], [/agentId: (\w+)/, 'agent']] as const) {
          const hit = rx.exec(body)
          if (hit !== null && u !== undefined && (what === 'shell' || u.name === 'Agent' || u.name === 'Task')) {
            started.set(hit[1] ?? '', { what, ts, arg: u.arg })
          }
        }
      } else if (b['type'] === 'text') {
        const t = String(b['text'] ?? '')
        if (typ === 'assistant' && t.trim() !== '') {
          lastText = t.trim()
        }
        if (typ === 'user') {
          for (const n of t.matchAll(/<task-notification>([\s\S]*?)<\/task-notification>/g)) {
            const nb = n[1] ?? ''
            const id = /<task-id>([^<]+)<\/task-id>/.exec(nb)?.[1]?.trim()
            if (id !== undefined) {
              notified.set(id, { ts, status: /<status>([^<]+)<\/status>/.exec(nb)?.[1]?.trim() ?? '?',
                summary: one(/<summary>([^<]+)<\/summary>/.exec(nb)?.[1] ?? '', 100) })
            }
          }
          for (const m of t.matchAll(/<agent-message from="([^"]+)">/g)) {
            from.set(m[1] ?? '', ts)
          }
        }
      }
    }
  }
  const at = (ts: string): number => {
    const v = Date.parse(ts)
    return Number.isNaN(v) ? 0 : v
  }
  const open: string[] = []
  for (const [id, s] of started) {
    const n = notified.get(id)
    const said = from.get(id)
    const waiting = n !== undefined && n.summary.toLowerCase().includes('waiting')
    if (said !== undefined && (n === undefined || at(said) >= at(n.ts) || !waiting)) {
      continue // it reported back by message after its last notice
    }
    if (n === undefined) {
      open.push(`${s.what} ${id} started ${s.ts.slice(0, 19)}, no completion notice: ${s.arg}`)
    } else if (waiting) {
      open.push(`${s.what} ${id} last notice ${n.ts.slice(0, 19)} says still waiting (${n.status}: ${n.summary}): ${s.arg}`)
    }
  }
  return { version, tools: tools.slice(-keep), open, lastText: one(lastText, 300) }
}

/** The value after `key`, `token`, `secret`, `password` or `auth` (any case; `=`, `:` or spaces) masked. */
export function redact(line: string): string {
  return line.replace(/([\w-]*(?:key|token|secret|password|auth)[\w-]*)(\s*[=:]\s*|\s+)("[^"]*"|'[^']*'|\S+)/gi, '$1$2[redacted]')
}

/** The process list's lines: blank ones dropped, each redacted and cut to PROCESS_CUT, at most PROCESS_LINES. */
export function processLines(stdout: string): string[] {
  const all = stdout.split(/\r?\n/).map(l => l.trimEnd()).filter(l => l.trim() !== '')
  const out = all.slice(0, PROCESS_LINES).map(l => {
    const r = redact(l)
    return r.length <= PROCESS_CUT ? r : `${r.slice(0, PROCESS_CUT - 1)}…`
  })
  return all.length > PROCESS_LINES ? [...out, `(${String(all.length - PROCESS_LINES)} more lines)`] : out
}

/** One agent as `$.agent.list()` gives it. */
export type Listed = { id: string; type?: string; name?: string; description?: string; status?: string; parentId?: string }

/** The snapshot's inputs, gathered by autopilot.tsx. */
export type Snapshot = {
  task: string
  attempt: number
  expert: { id: string; type: string }
  route: string
  ageMs: number
  pa3: string
  claudeCode: string
  platform: string
  session: string
  at: string
  list: readonly Listed[]
  shells: string | null
  facts: readonly { id: string; text: string | null }[]
  tails: readonly { id: string; type: string; tail: Tail | null }[]
  processes: string[] | null
  gaps: readonly string[]
}

const fence = (text: string): string => `\`\`\`\n${text.trimEnd()}\n\`\`\``

/** The Markdown, cut to SNAPSHOT_CAP characters. Facts files are cut to 4,000 each so the process list fits. */
export function snapshotText(s: Snapshot): string {
  const w: string[] = [
    `# PA3 stall snapshot: ${s.task}`,
    '',
    `- Task ${s.task}, attempt ${String(s.attempt)}; expert ${s.expert.id} (${s.expert.type})`,
    `- Report: ${s.route}, routed ${String(Math.round(s.ageMs / 1000))} s after the expert's turn ended`,
    `- PA3 ${s.pa3 || '?'}; Claude Code ${s.claudeCode || '?'}; platform ${s.platform || '?'}; session ${s.session}`,
    `- Written ${s.at}`,
    '',
    "## Claude Code's agent list",
    '',
    ...(s.list.length === 0 ? ['(empty)'] : s.list.map(a =>
      `- ${a.id} | ${a.type || '-'} | ${a.name || '-'} | ${one(a.description, 120) || '-'} | ${a.status || '-'} | parent ${a.parentId || '-'}`)),
    '',
    "## pa-watch's shells.json",
    '',
    s.shells === null ? '(none)' : fence(s.shells),
    '',
    '## Facts files',
  ]
  for (const f of s.facts) {
    w.push('', `### ${f.id}`, '', f.text === null ? '(none)' : fence(f.text.length <= 4_000 ? f.text : `${f.text.slice(0, 3_999)}…`))
  }
  w.push('', '## Transcripts')
  for (const t of s.tails) {
    w.push('', `### ${t.type || 'agent'} ${t.id}`, '')
    if (t.tail === null) {
      w.push('(unreadable)')
      continue
    }
    w.push(`Last ${String(t.tail.tools.length)} tool calls:`, ...t.tail.tools.map(x => `- ${x}`),
      '', 'Background work not answered:', ...(t.tail.open.length === 0 ? ['- (none)'] : t.tail.open.map(x => `- ${x}`)),
      '', `Last text: ${t.tail.lastText || '(none)'}`)
  }
  w.push('', '## Processes', '', s.processes === null ? '(not read)' : fence(s.processes.join('\n')))
  if (s.gaps.length > 0) {
    w.push('', '## Gaps', '', ...s.gaps.map(g => `- ${g}`))
  }
  const text = `${w.join('\n')}\n`
  const tail = '\n[cut at 60,000 characters]\n'
  return text.length <= SNAPSHOT_CAP ? text : text.slice(0, SNAPSHOT_CAP - tail.length) + tail
}
