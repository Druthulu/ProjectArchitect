"""The repository setup offer's memory (3.11 T7, developer 2026-09-28; T7.1).

The session-start hook shows the user a copy-ready `!` line that sets PA3 up in an ungoverned git repository at
every startup (3.11 T7.1, developer: was once per repository, and a `{"shown": ts}` marker left by 3.11 T7 is now
ignored); `pa_install.py --ignore <repo>` silences it (and the model-facing offer) for good. One small JSON file per
ignored repository under the ledger's state dir, keyed by the normalized repository path. The machine-wide switch
is ``setup_offer`` in the ledger's config.json.

Hot path (SessionStart): only json, os, sys and time at module level.
"""

import os
import time


def _key(repo):
    import hashlib

    norm = os.path.normcase(os.path.abspath(str(repo))).replace("\\", "/").rstrip("/")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _path(repo):
    from . import paths

    return paths.state_path("setup_offer", "%s.json" % _key(repo))


def state(repo):
    """``{}`` or ``{"never": true, ...}`` for ``repo`` (a 3.11 T7 ``{"shown": ts}`` counts as ``{}``)."""
    from . import fsutil

    return fsutil.read_json(_path(repo), {}) or {}


def ignore(repo):
    """Never offer ``repo`` again; returns the marker path."""
    from . import fsutil

    path = _path(repo)
    fsutil.atomic_write_json(path, {"repo": str(repo), "never": True, "ts": time.time()})
    return path
