// Pure helpers of the watcher's debug log (one JSON file per event under PA_MOD_DEBUG=1, since `$.fs` has no append):
// copies of the router's mod/pa/hooks/debug.ts `eventFile` and `clip`, kept identical. 3.23 T13d.c11: `levelOf` and
// the `level` option retired (since c3 it only reached the activation event).

/** One file per event, sortable by time then by a per-load counter. */
export function eventFile(dir: string, ms: number, n: number, kind: string): string {
  const safe = kind.replace(/[^a-z0-9.]+/gi, '_')
  return `${dir}/${String(ms)}-${String(n).padStart(5, '0')}-${safe}.json`
}

/** A value as text, cut to `n` characters, so event files stay small. */
export function clip(value: unknown, n: number): string {
  let text: string
  if (typeof value === 'string') {
    text = value
  } else {
    try {
      text = JSON.stringify(value) ?? String(value)
    } catch {
      text = String(value)
    }
  }
  return text.length > n ? `${text.slice(0, n)}…` : text
}
