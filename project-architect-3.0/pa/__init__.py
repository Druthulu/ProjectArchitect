"""Project Architect 3.0 usage-ledger package (stdlib only).

Public:
    __version__     package version string, read from ``VERSION`` beside the package (I11)
    parse_version   the version from VERSION text (bare ``3.11`` or ``version: 3.11``)
    SCHEMA_VERSION  ledger.sqlite schema version written to meta.schema_version

Import cost matters: this module imports only ``os`` (the guarded VERSION read).  The hot-path
modules (paths, fsutil, log, config, prices, transcript) import only cheap
stdlib modules; ``pa.db`` is the only module that pulls in ``sqlite3`` and is
imported on cold paths only.
"""

import os as _os

_FALLBACK_VERSION = "3.14.1"  # was 3.14


def parse_version(text):
    """The version on the first line of *text*: ``3.11`` or ``version: 3.11``; ``""`` if none."""
    line = (text or "").lstrip("\ufeff").splitlines()[0].strip() if (text or "").strip() else ""
    if line.startswith("version:"):
        line = line.split(":", 1)[1].strip()
    return line if line and ":" not in line else ""


def _read_version():
    """``<package-dir>/VERSION`` (the package file, or the install's generated metadata)."""
    try:
        path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                             "VERSION")
        with open(path, "r", encoding="utf-8") as fh:
            return parse_version(fh.readline()) or _FALLBACK_VERSION
    except Exception:
        return _FALLBACK_VERSION


__version__ = _read_version()
SCHEMA_VERSION = 1
