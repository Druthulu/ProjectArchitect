// Activation of the PA 3.2 mod (design.md, "Activation"): a copy of the router's mod/pa/hooks/activation.ts, kept
// identical (plugins never import across each other); the same test vectors pin both.

/** The lowest Claude Code release the mod runs on: P3b, path 0 and the file channel were verified there (3.21). */
export const VERSION_FLOOR = '2.1.292'

export type Decision = { active: boolean; reason: string }

/** `[major, minor, patch]` of a version's leading `x.y.z`, or `x.y` as `[x, y, 0]` (3.3 T7: VERSION 3.30), or null
 * when it does not start with one. */
export function parseVersion(version: string): [number, number, number] | null {
  const m = /^\s*(\d+)\.(\d+)(?:\.(\d+))?/.exec(version)
  if (m === null) {
    return null
  }
  return [Number(m[1]), Number(m[2]), Number(m[3] ?? 0)]
}

/** Numeric compare of `version` against `floor`; null when either does not parse. */
export function versionAtLeast(version: string, floor: string): boolean | null {
  const a = parseVersion(version)
  const b = parseVersion(floor)
  if (a === null || b === null) {
    return null
  }
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) {
      return (a[i] ?? 0) > (b[i] ?? 0)
    }
  }
  return true
}

/** The release a `$.session.version()` answer names: its `base` when given, else its full `version`. */
export function versionOf(v: { version: string; base?: string }): string {
  return v.base ?? v.version
}

/** One string field of pa.json, or '' when absent, not a string, or pa.json does not parse. */
export function paSetting(paJsonText: string | null, key: string): string {
  if (paJsonText === null) {
    return ''
  }
  try {
    const doc: unknown = JSON.parse(paJsonText)
    const value = typeof doc === 'object' && doc !== null ? (doc as Record<string, unknown>)[key] : undefined
    return typeof value === 'string' ? value : ''
  } catch {
    return ''
  }
}

/**
 * On only with `.claude/pa.json` `"mod": "on"`. Off without pa.json, with any other value, under PA_MOD_OFF (non-empty
 * and not "0"), or below Claude Code VERSION_FLOOR.
 */
export function decide(paJsonText: string | null, envOff: string | undefined, version: string): Decision {
  if (envOff !== undefined && envOff !== '' && envOff !== '0') {
    return { active: false, reason: 'PA_MOD_OFF is set' }
  }
  const atLeast = versionAtLeast(version, VERSION_FLOOR)
  if (atLeast !== true) {
    return { active: false, reason: `Claude Code ${version} is below ${VERSION_FLOOR} or unreadable` }
  }
  if (paJsonText === null) {
    return { active: false, reason: 'no .claude/pa.json' }
  }
  let doc: unknown
  try {
    doc = JSON.parse(paJsonText)
  } catch {
    return { active: false, reason: 'pa.json does not parse' }
  }
  const mod = typeof doc === 'object' && doc !== null ? (doc as Record<string, unknown>)['mod'] : undefined
  if (mod === 'on') {
    return { active: true, reason: 'pa.json mod: on' }
  }
  return { active: false, reason: `pa.json mod: ${String(mod ?? 'absent')}` }
}

/**
 * The ledger CLI (design.md, "Execution model"): `$PA_MOD_PA3_DIR/pa_ledger.py` when PA_MOD_PA3_DIR is set (trials point
 * it at a snapshot's project-architect-3.0), else `<home>/.claude/pa3/pa_ledger.py`; '' with neither. Forward slashes.
 */
export function ledgerPath(pa3Dir: string | undefined, home: string | undefined): string {
  if (pa3Dir !== undefined && pa3Dir !== '') {
    return `${pa3Dir.replace(/\\/g, '/').replace(/\/+$/, '')}/pa_ledger.py`
  }
  if (home === undefined || home === '') {
    return ''
  }
  return `${home.replace(/\\/g, '/')}/.claude/pa3/pa_ledger.py`
}

// 3.24 T6: the heartbeat and the error record pa/health.py reads under `<root>/.run/mod/` (the session-start and first
// prompt checks); written only by an active session, so an inert one still writes nothing (3.24 T8).
export const HEARTBEAT_FILE = '.run/mod/heartbeat.json'
export const ERROR_FILE = '.run/mod/error.json'

/** The PA3 release a VERSION file names: its `version:` line, else its first line; '' when absent. */
export function releaseOf(text: string | null): string {
  const lines = (text ?? '').split(/\r?\n/)
  const v = lines.find(l => /^\s*version\s*:/.test(l))
  return (v === undefined ? lines[0] ?? '' : v.slice(v.indexOf(':') + 1)).trim()
}

/** The heartbeat `{sid, version, engine, at, plugin}`: the PA3 release, the Claude Code release, `at` in seconds. */
export function heartbeatText(sid: string, version: string, engine: string, ms: number, plugin: string): string {
  return JSON.stringify({ sid, version, engine, at: ms / 1000, plugin }, null, 1)
}

/** The error record `{sid, plugin, hook, error, at}` of an activation hook that threw (`at` in seconds). */
export function errorText(sid: string, plugin: string, hook: string, error: string, ms: number): string {
  return JSON.stringify({ sid, plugin, hook, error: error.slice(0, 500), at: ms / 1000 }, null, 1)
}
