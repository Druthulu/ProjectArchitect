#!/usr/bin/env python
"""PA3 hook entry point -- ``pa_hook.py <event>`` (design A section A.3).

Installed as::

    "<python>" -I -X utf8 <pa3>/pa_hook.py <event>

``-I`` ignores ``PYTHONPATH`` and the user site directory, so this file puts its
own directory on ``sys.path`` before importing ``pa``; ``-X utf8`` fixes the
cp1252 decode the probe caught (recon V7).

Contract, in order:

1. **Kill switches first** -- ``PA_LEDGER_OFF=1``, ``PA_HOOKS_OFF=1`` or
   ``config.enabled=false`` exit 0 before any work.
2. stdin is read as UTF-8 JSON.
3. ``log.watchdog(budget_ms)`` arms a daemon timer that hard-exits 0 if the hook
   wedges (each budget sits under the matching ``settings.json`` timeout).
4. ``pa.hooks.<event>.run(inp, cfg)`` is dispatched; a non-empty returned dict is
   printed as JSON -- only PreToolUse (deny) and PostToolUse (handoff) ever
   return one.
5. **Always exit 0.**  Every exception is logged to ``hooks.log`` and swallowed:
   a broken ledger must never break the developer's session.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _off(name):
    return os.environ.get(name, "").strip() not in ("", "0", "false", "False", "no")


def _read_stdin():
    data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else (sys.stdin.read() or "")
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return data


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if _off("PA_LEDGER_OFF") or _off("PA_HOOKS_OFF"):
        return 0
    event = (argv[0] if argv else "").strip().lower().replace("-", "_")

    try:
        from pa import config, log
    except Exception:                                     # noqa: BLE001 - never break a session
        return 0

    timer = None
    try:
        cfg = config.load()
        if not config.enabled(cfg):
            return 0
        from pa import hooks

        if event not in hooks.EVENTS:
            log.log("hook_unknown_event", hook=event)
            return 0

        raw = _read_stdin()
        try:
            inp = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            log.log("hook_bad_stdin", hook=event, err=str(exc)[:200], head=raw[:200])
            return 0
        if not isinstance(inp, dict):
            log.log("hook_bad_stdin", hook=event, err="not an object", head=raw[:200])
            return 0

        timer = log.watchdog(hooks.BUDGET_MS.get(event, 5000), note=event)
        module = __import__("pa.hooks." + event, fromlist=["run"])
        out = module.run(inp, cfg)
        if isinstance(out, dict) and out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            sys.stdout.write("\n")
            sys.stdout.flush()
    except BaseException as exc:                          # noqa: BLE001 - exit 0 always
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return 0
        try:
            import traceback

            log.log("hook_error", hook=event, err=repr(exc)[:300],
                    tb=traceback.format_exc(limit=8).replace("\n", " | ")[:2000])
        except Exception:
            pass
    finally:
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
