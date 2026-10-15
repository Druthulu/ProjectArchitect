// The watcher's contract: its $.state values (they survive a hot reload, not /clear). Self-contained: validate refuses
// imports here. No noun on $: the watcher writes requests to the router's inbox as files.

/** Whether pa-watch is active this session. Also keeps this file a module (3.23 T13d.c11: `WatchLevel` retired;
 * 3.24 T13C: `Prices` and the `prices` state retired with the usage line). */
export type Active = boolean

/** 3.24 T17b: one ladder row, as T17a's resolver prints it (`pa_ledger.py ladder-row --agent <type> --project <dir>
 * --json`): `managed` false for an agent outside the ladder; `source` `credentials`, `preset` or `default`. */
export type LadderRow = {
  agent: string; managed: boolean; model: string; effort: 'low' | 'medium' | 'high' | 'xhigh' | 'max' | ''; tier: string; source: string
}

/** 3.24 T17b: the rows resolved this session, by agent type (one tier: a planner spawn's new tier drops the others). */
export type LadderRows = Record<string, LadderRow>

declare module 'claude-code' {
  interface PluginState {
    'pa-watch': {
      active: Active
      ladder: LadderRows
    }
  }
}
