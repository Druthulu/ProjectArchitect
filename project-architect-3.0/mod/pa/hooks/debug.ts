// Pure helpers of the router's wiring: debug event files (one JSON file per event under PA_MOD_DEBUG=1, since `$.fs`
// has no append), the generation token, and the argv of the prune of old `.run/mod/<sid>` folders.

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

/**
 * A tick holds the generation it was armed under; `/clear` resets `$.state` (the current generation reads 0) and
 * re-arming bumps it, so a tick whose token differs from the current one is stale and stops.
 */
export function isStale(tickGen: number, currentGen: number): boolean {
  return tickGen !== currentGen
}

/** `.run/mod/<sid>` folders untouched this many days are pruned at session start. */
export const PRUNE_DAYS = 14

// argv: base, days, then the folder names to keep. A folder's age is the newer of its own and its state.json mtime;
// only 36-character names (session ids) are candidates. Prints how many it removed.
const PRUNE_SCRIPT =
  'import os,sys,time,shutil;b=sys.argv[1];c=time.time()-float(sys.argv[2])*86400;k=set(sys.argv[3:]);' +
  "s=lambda p:max(os.path.getmtime(p),os.path.getmtime(os.path.join(p,'state.json')) if os.path.exists(os.path.join(p,'state.json')) else 0);" +
  'o=[os.path.join(b,x) for x in (os.listdir(b) if os.path.isdir(b) else []) if len(x)==36 and x not in k and os.path.isdir(os.path.join(b,x))];' +
  'o=[p for p in o if s(p)<c];[shutil.rmtree(p,ignore_errors=True) for p in o];print(len(o))'

/** The `$.process.run` argv of the prune, or null when pa.json names no interpreter. */
export function pruneArgv(python: string, base: string, keep: readonly string[], days: number): string[] | null {
  if (python === '') {
    return null
  }
  return [python, '-c', PRUNE_SCRIPT, base, String(days), ...keep.filter(k => k !== '')]
}
