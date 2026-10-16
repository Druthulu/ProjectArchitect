#!/usr/bin/env python
"""``pa-ledger`` entry point (design doc A.1/A.3).

Run directly (``python pa_ledger.py recalc --dry-run``) or through the shims the
installer writes (``~/.local/bin/pa-ledger.cmd`` / ``pa-ledger``).  The file
inserts its own directory into ``sys.path`` so ``-I`` (no PYTHONPATH, no user
site) still finds the ``pa`` package next to it, and forces UTF-8 on stdout /
stderr so the output is identical with and without ``-X utf8`` (recon V7).
"""

import os
import sys


def _bootstrap():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv=None):
    _bootstrap()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["ladder-row"]:      # 3.24 T17: the spawn-time resolver, without ledger_cli's imports
        from pa.ladder_row import main as row_main
        return row_main(args[1:])
    if args[:1] == ["update-check"]:    # 3.28.1 T14: the mod's 2-hour release check (the session-start one)
        from pa.hooks.session_start import check_main
        return check_main(args[1:])
    from pa.ledger_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
