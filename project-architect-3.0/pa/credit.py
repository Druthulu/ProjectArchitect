"""Credit notes: consume script-written notes and build tool_calls rows.

Public:
    consume(root) -> list[dict]
    tokens(chars) -> int
    result_chars(resp) -> int
    rows_for_call(notes, *, session_id, run_id, ts, model, result_chars, cfg) -> list[dict]
    read_row(session_id, run_id, ts, model, path, result_chars, kind="read", file_tokens=0) -> dict
    carry_tokens(row, seen_paths, read_paths, cfg) -> int

Notes are JSON files under ``<root>/.run/credit/`` written by the scripts
(T2) and ``run.sh``.  ``consume`` reads and deletes them; unparsable ones
are silently dropped.
"""

import json
import os
import time


def consume(root):
    """Read and delete every note file under ``<root>/.run/credit/``.

    A missing directory is treated as empty (never created).
    Returns a list of parsed note dicts.
    """
    credit_dir = os.path.join(root, ".run", "credit")
    try:
        names = os.listdir(credit_dir)
    except OSError:
        return []
    notes = []
    for name in names:
        path = os.path.join(credit_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                note = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
            # unparsable: delete and skip
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            os.remove(path)
        except OSError:
            pass
        if isinstance(note, dict):
            notes.append(note)
    return notes


def tokens(chars):
    """Approximate token count from character count: ``chars // 4``."""
    try:
        return int(chars) // 4
    except (TypeError, ValueError):
        return 0


def result_chars(resp):
    """Total length of every string value in a response object.

    A str is parsed as JSON first (the harness may pass a JSON string);
    dict/list values are recursed; other types contribute 0.
    """
    if isinstance(resp, str):
        try:
            parsed = json.loads(resp)
        except (json.JSONDecodeError, ValueError):
            return len(resp)
        if isinstance(parsed, (dict, list)):
            return result_chars(parsed)
        return len(resp)
    if isinstance(resp, dict):
        return sum(result_chars(v) for v in resp.values())
    if isinstance(resp, list):
        return sum(result_chars(item) for item in resp)
    return 0


def _rel_path(path):
    """Normalise a path to forward-slash relative form (no drive letter).

    ``Z:/a/b`` and ``/mnt/z/a/b`` both become ``a/b``.
    """
    p = str(path or "").replace("\\", "/")
    # strip drive letter: Z:/… -> /…
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    # strip /mnt/<letter>/… -> /…
    elif len(p) >= 7 and p[:5] == "/mnt/" and p[6] == "/":
        p = p[7:]
    # strip leading slashes
    p = p.lstrip("/")
    return p


def rows_for_call(notes, *, session_id, run_id, ts, model, result_chars, cfg):
    """Build tool_calls rows from consumed notes for one Bash PostToolUse.

    ``result_chars`` is the length of the Bash call's stdout+stderr.
    Returns a list of row dicts ready for ``db.upsert("tool_calls", row)``.
    """
    result_tokens = tokens(result_chars)
    rows = []
    for note in notes:
        kind = note.get("kind", "script")
        path_raw = note.get("path") or ""
        rel = _rel_path(path_raw)
        whole = bool(note.get("whole"))
        removed = int(note.get("removed_chars") or 0)
        added = int(note.get("added_chars") or 0)
        anchor = int(note.get("anchor_chars") or 0)
        file_chars = int(note.get("file_chars") or 0)

        if kind == "outline":
            # the outline's printed chars; file_tokens = the 2,000-line cap
            emission = tokens(int(note.get("emitted_chars") or 0))
            file_chars = int(note.get("cap_chars") or file_chars)
        elif whole:
            emission = tokens(file_chars)
        else:
            emission = tokens(removed + added + anchor)

        row = {
            "id": "%s-%s-%s" % (session_id, ts, rel or note.get("sub") or "unknown"),
            "session_id": session_id,
            "run_id": run_id,
            "ts": ts,
            "tool": "Bash",
            "kind": kind,
            "script": note.get("script"),
            "sub": note.get("sub"),
            "path": rel,
            "file_tokens": tokens(file_chars),
            "emission_tokens": emission,
            "result_tokens": result_tokens,
            "model": model,
        }

        if kind == "runsh":
            kept_chars = int(note.get("kept_chars") or 0)
            spill = int((cfg.get("credit") or {}).get("spill_chars") or 20000)
            row["kept_tokens"] = min(tokens(kept_chars), tokens(spill))
        else:
            row["kept_tokens"] = 0

        rows.append(row)
    return rows


def read_row(session_id, run_id, ts, model, path, result_chars, kind="read",
             file_tokens=0):
    """Build a tool_calls row for a Read PostToolUse.

    ``kind="outline-read"``: the first ranged Read after an outline of the
    path; ``file_tokens`` = tokens of the 2,000-line cap.
    """
    rel = _rel_path(path)
    return {
        "id": "%s-%s-%s-%s" % (session_id, ts, kind, rel),
        "session_id": session_id,
        "run_id": run_id,
        "ts": ts,
        "tool": "Read",
        "kind": kind,
        "path": rel,
        "file_tokens": int(file_tokens or 0),
        "emission_tokens": 0,
        "result_tokens": tokens(result_chars),
        "kept_tokens": 0,
        "model": model,
    }


def carry_tokens(row, seen_paths, read_paths, cfg):
    """Carry tokens for one tool_calls row.

    The guard: 0 when the path is in ``read_paths`` (the model read the file
    itself in this session).  First script note per (session, path) earns
    ``max(0, file_tokens - result_tokens)``.  A ``runsh`` row earns
    ``min(kept_tokens, tokens(spill_chars))``.  An ``outline`` row earns 0;
    an ``outline-read`` row earns ``max(0, file_tokens - outline_emission_tokens
    - result_tokens)`` (the outline row's emission, set by the fold), unguarded.
    """
    path = row.get("path") or ""
    kind = row.get("kind") or ""

    if kind == "outline":
        return 0
    if kind == "outline-read":
        return max(0, int(row.get("file_tokens") or 0)
                   - int(row.get("outline_emission_tokens") or 0)
                   - int(row.get("result_tokens") or 0))

    if path and path in read_paths:
        return 0

    if kind == "runsh":
        spill = int((cfg.get("credit") or {}).get("spill_chars") or 20000)
        kept = int(row.get("kept_tokens") or 0)
        return min(kept, tokens(spill))

    # script note: first per (session, path)
    if path and path in seen_paths:
        return 0
    if path:
        seen_paths.add(path)
    file_tok = int(row.get("file_tokens") or 0)
    result_tok = int(row.get("result_tokens") or 0)
    return max(0, file_tok - result_tok)
