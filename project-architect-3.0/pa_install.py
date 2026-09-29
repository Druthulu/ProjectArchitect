#!/usr/bin/env python3
"""Project Architect 3.0 installer.

    pa_install.py --root [--config-dir DIR] [--pa3-dir DIR] [--python EXE]
                         [--dry-run] [--yes] [--renewal-day N] [--extra-root DIR]...
                         [--label EMAIL=LABEL]... [--no-statusline] [--hooks off]
    pa_install.py --project [DIR]        (M2 -- prints "not yet implemented")

``--root`` runs once per machine *per config dir*: it copies ``pa/`` and the
entry scripts into ``<home>/.claude/pa3/``, creates ``<home>/.claude/usage-ledger/``
(schema + ``config.json``) and merges ``settings/user.snippet.json`` into
``<config-dir>/settings.json`` after backing that file up.  Every step prints
``SKIP``/``DONE``/``FAIL`` with a one-line reason and the run ends in ``OK`` or
``FAIL``; a second run changes nothing.

Run it with the interpreter that should execute the hooks (or pass ``--python``):
the hook commands are written with that interpreter's absolute path.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def main(argv=None):
    from pa.install import main as _main

    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
