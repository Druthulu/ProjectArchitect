#!/usr/bin/env python
"""Claude Code ``statusLine`` entry point for Project Architect 3.0.

Invoked as ``"<python>" -I -X utf8 <pa3>/pa_statusline.py`` with the statusline
JSON on stdin (design §A.3).  It prints the rendered lines first and samples the
ledger afterwards, so the developer never waits on the sampler.  It always exits
0 and always prints at least line 1: an empty statusline would hide the context
gauge the whole workflow leans on.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _bare_fallback():
    """Print something useful even when the package itself cannot be imported."""
    try:
        import json

        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
        model = ((data.get("model") or {}).get("display_name")
                 or (data.get("model") or {}).get("id") or "claude")
        window = data.get("context_window") or {}
        used = window.get("total_input_tokens")
        size = window.get("context_window_size")
        line = str(model)
        if used is not None and size:
            line += " · %d/%dk" % (int(used) // 1000, int(size) // 1000)
    except Exception:
        line = "claude"
    try:
        sys.stdout.write(line + "\n")
    except Exception:
        pass
    return 0


def main():
    try:
        from pa import statusline
    except Exception:
        return _bare_fallback()
    try:
        return statusline.main()
    except Exception:
        try:
            from pa import log

            log.log("statusline.entry_failed")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
