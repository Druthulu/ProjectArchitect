# Statusline and report definitions

One table for every figure the statusline and `pa_ledger.py report` show. A figure is one of three
kinds: **meter** (Claude Code's own usage percent, read from the statusline payload), **counted**
(a token count the API measured), or **modeled** (a dollar or percent figure that rests on a
stated assumption). Modeled figures are never added to each other (spec D39).

## Terms

| Term | Definition |
|---|---|
| ledger price | `pa/prices.py`, per model, applied to every API turn the ledger records (`turns.cost_usd`). The basis of every dollar on the screen. |
| harness cost-state | Claude Code's own cost figure, which bills a `[1m]` launch at the long-context premium. Report cross-check column only; no screen line uses it. |
| kept-out tokens | Per helper run: the API-measured growth of the helper's context, times the share of that growth that was tool results, minus its answer. Plus the credit rows scripts write (`tools/_credit.py note`). Counted. |
| carry model | The kept-out tokens re-read by the parent on every later request of the session at the cache-read price, written once at the 1h write price and again after a cold gap, inside one 1M-token window that resets when it would overflow; the helper's own seed cost is subtracted and the emission credit added. Modeled; the assumption is the 1M window. |
| replay | Every request of an account replayed as one monolithic session (`pa/replay.py`) under stated assumptions. Modeled; a second, separate model. |
| points per dollar | The share of a weekly (or 5h) allowance one ledger dollar buys: the window's meter percent over the account's ledger cost in that window (method `ratio`), or the pooled per-family fit of instances that started at or after `fit.regime_since`. |
| window instance | One account's seven-day (or five-hour) usage window, from its start to its reset, with its own meter readings and its own rate. |
| era | `meta.created` of the ledger, or `statusline.lifetime_since`. Lifetime figures start here; an export carries it and an import keeps it. |

## Statusline

| Line | Piece | Kind | Definition |
|---|---|---|---|
| Header | `N%` of 5h, of 7d, Fable | meter | Claude Code's usage percent for each window of the signed-in account. |
| Pace | `1.9x` | meter | 7d percent used over the fraction of the window elapsed; 1.0x is on pace. |
| Pace | `N%/day used` | meter | percent used over max(1 day, time since the window started). |
| Pace | `N%/day left` | meter | percent left over max(1 day, time to the reset). |
| Pace | `N% fewer tokens than vanilla` | modeled | saved over (paid plus saved). paid: this account's ledger cost since its 7d window started, all projects, all machines. saved: this account's share of the carry model over the same span. |
| Pace | `lasts Mx longer` | modeled | (paid plus saved) over paid, same inputs. |
| Project | `x/y of 5h`, `x/y of 7d` | modeled | x: this account's meter reading for the window, shared among its projects by each project's part of the account's ledger cost in the window on every machine. y: this account's project savings in its own window, same conversion. Both sides are one account and one window. |
| Project | `month ~Xw/Yw`, `lifetime ~Xw/Yw` | modeled | weeks of allowance used and saved: each window instance's dollars times that instance's own rate, summed; dollars in weeks without a rate stay unconverted and turn `~` into `≈` when over 10%. Never today's rate on an earlier week. |
| Project | `N% fewer`, `lasts Mx longer` | modeled | the project's lifetime saving (per-run rows) against its lifetime ledger cost, all accounts. |
| Project | `· N accounts` | counted | accounts with turns on this project. |
| Account | `x of 5h`, `y of 7d`, weeks, fewer, lasts | as above | the same definitions at account scope, every project of this account. |
| Session | cost, saved | ledger price, modeled | the session's ledger cost; its measured saving from the per-run rows; the window pair converts the saving through points per dollar. |

## `pa_ledger.py report`

| Line | Kind | Definition |
|---|---|---|
| `saved meas.` column | modeled | the per-run savings rows (`savings.measured_saved_usd`) in the window. |
| `harness` column | cross-check | the harness cost-state per session, never a basis. |
| `counted kept out of the parent context` | counted | non-void helper runs' kept-out tokens plus credited tool-call tokens in the window, with both counts. |
| `modeled carry avoided at a 1,000k window` | modeled | the carry model over the window at the configured 1M window, with the same walk at 450k and 200k beside it (bounded windows only). |
| `modeled whole account replayed as one session` | modeled | the replay figure. Not added to the carry figure. |
| `pool max seven_day: fable 0.00107/$` | fitted | pooled points per dollar per model family, instances started at or after the regime boundary, with standard error and sample count. |

Two "week" figures differ by definition: the statusline's is the account's actual reset window; the
report's `--window seven_day` is a plain seven-day span ending now.
