// The router's own activity-stream lines (design.md "Activity stream", I49): the lines it writes at every level.
// The watcher formats its own lines (mod/pa-watch).

export const LINE_MAX = 160

export type LineInfo = {
  depth?: number // two spaces per level
  who?: string // default `Claude`
  model?: string // a label (`Opus 5.5`) or an id (`claude-opus-5-5[1m]`)
  ctx?: number
  usd?: number
  title?: string
}

/** `claude-opus-5-5[1m]` → `Opus 5.5`; anything else unchanged. */
export function modelLabel(model: string): string {
  const m = /(opus|sonnet|haiku|fable)-([0-9]+)-([0-9]+)/i.exec(model)
  if (!m) return model
  return `${m[1][0].toUpperCase()}${m[1].slice(1).toLowerCase()} ${m[2]}.${m[3]}`
}

/** 950 → `950`, 12_345 → `12.3k`, 1_250_000 → `1.3M`. */
export function tokens(n: number): string {
  if (n < 1000) return String(Math.round(n))
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** One line: `start` `▸ Claude started (Opus 5.5)`, `answer` `◂ Claude answered · 12.3k ctx · $0.04`, `plan` `▣ plan presented: <title>`. */
export function routerLine(kind: 'start' | 'answer' | 'plan', info: LineInfo = {}): string {
  const who = info.who ?? 'Claude'
  let text: string
  if (kind === 'start') {
    text = `▸ ${who} started${info.model ? ` (${modelLabel(info.model)})` : ''}`
  } else if (kind === 'answer') {
    const ctx = typeof info.ctx === 'number' ? ` · ${tokens(info.ctx)} ctx` : ''
    const usd = typeof info.usd === 'number' ? ` · $${info.usd.toFixed(2)}` : ''
    text = `◂ ${who} answered${ctx}${usd}`
  } else if (kind === 'plan') {
    text = `▣ plan presented: ${info.title ?? ''}`
  } else {
    throw new Error(`stream: unknown kind ${JSON.stringify(kind)}`)
  }
  const line = '  '.repeat(Math.max(0, info.depth ?? 0)) + text.replace(/\s*\r?\n\s*/g, ' ')
  return line.length > LINE_MAX ? `${line.slice(0, LINE_MAX - 1)}…` : line
}
