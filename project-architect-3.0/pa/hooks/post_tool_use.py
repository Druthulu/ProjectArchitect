"""PostToolUse: the live half of the ledger (and the only hook that injects text).

Four paths, cheapest first (design A section C):

1. **main thread, non-Agent tool** -- return at once (budget 45 ms; this is the
   hook that fires after every Read/Bash/Edit of every session on the machine).
2. **parent side of ``Agent``** -- ``tool_response`` carries ``agentId``,
   ``agentType``, ``resolvedModel``, ``status`` and the child's ``usage``
   (recon V4): authoritative parent->child linkage plus the D48 model-fallback
   check, once per (session, agent, seen model).
3. **inside an expert/coder** (``role in handoff.roles``) -- one O(1)
   ``tail_last_usage`` of *this agent's* transcript gives ``ctx_next``,
   ``model_seen`` and the request cost; ``running.json`` gets ctx, n_requests,
   cost_live, every retrieval's ``n_after`` and ``saved_live_usd``; crossing the
   threshold injects the handoff task exactly once (``O_EXCL``).
4. **inside any other subagent** -- ctx only, and only when
   ``savings.track_retriever_live`` is on (it is off by default).

The transcript is never opened whole here: ``tail_last_usage`` reads the last
128 KB, which is O(1) even on the 256 MB transcript of recon V8.
"""

import json
import os

from .. import handoff, log, prices, running
from . import (agent_of, agent_transcript, close_db, find_project_root, governed, now_iso,
               open_db, pinned_for, project_config, role_of, sid_of, spool_event,
               status_read, toast)

MANAGED_RECORD = (".claude", "pa3-managed.json")         # pa/install/managed.py RECORD


def run(inp, cfg):
    inp = inp or {}
    tool = inp.get("tool_name")
    agent_id = agent_of(inp)

    if tool in ("Edit", "Write", "MultiEdit"):
        _managed_edit(inp)
    if tool == "Agent":
        return _agent_returned(inp, cfg)
    if not agent_id:
        return _main_credit(inp, cfg, tool)

    role = role_of(inp, cfg)
    if role in handoff.roles(cfg):
        return _inside_worker(inp, cfg, agent_id, role)
    if role in ("planner", "critic"):                    # live ctx/effort/savings, never a handoff
        return _inside_worker(inp, cfg, agent_id, role, may_handoff=False)
    if ((cfg.get("savings") or {}).get("track_retriever_live")):
        _inside_other(inp, cfg, agent_id, role)
    if tool in ("Bash", "Read"):
        return _credit_only(inp, cfg, agent_id, tool)
    return None


# --------------------------------------------------------------------------- managed-file edits

def _managed_edit(inp):
    """3.9.7 T7: an Edit/Write/MultiEdit of a file listed in ``.claude/pa3-managed.json``
    spools one ``managed_edit`` event (path, running task, tool). Never raises."""
    try:
        root = find_project_root(inp.get("cwd"))
        if not root:
            return
        ti = inp.get("tool_input")
        fp = str(ti.get("file_path") or "") if isinstance(ti, dict) else ""
        if not fp:
            return
        rel = os.path.relpath(os.path.join(root, fp), root).replace("\\", "/")
        if rel.startswith(".."):
            return
        with open(os.path.join(root, *MANAGED_RECORD), "r", encoding="utf-8") as fh:
            files = json.load(fh).get("files")
        if not isinstance(files, dict) or rel not in files:
            return
        spool_event(sid_of(inp), "managed_edit",
                    {"path": rel, "task": status_read(root).get("task"),
                     "tool": inp.get("tool_name")})
    except Exception:
        pass


# --------------------------------------------------------------------------- 2. Agent tool

def _agent_returned(inp, cfg):
    import re

    from .. import runs

    raw = inp.get("tool_response")
    resp = raw if isinstance(raw, dict) else {}
    child = resp.get("agentId")
    if not child:
        # a background launch answers with text: "Async agent launched successfully ... agentId: <id>"
        text = raw if isinstance(raw, str) else json.dumps(raw, default=str) if raw is not None else ""
        m = re.search(r"agentId:\s*([0-9a-f]{8,})", text)
        child = m.group(1) if m else None
    if not child:
        return None
    sid = sid_of(inp)
    parent = agent_of(inp) or sid
    tool_input = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    agent_type = resp.get("agentType") or tool_input.get("subagent_type")
    seen = resp.get("resolvedModel")
    status = resp.get("status")

    running.set_agent(sid, child, {
        "parent": parent, "parent_source": "agent_tool", "agent_type": agent_type,
        "model_seen": seen, "model_pinned": _pinned(agent_type, cfg),
        "status": status, "tool_use_id": inp.get("tool_use_id"),
        "description": tool_input.get("description"),
    })

    conn = open_db()
    try:
        runs.link_parent(conn, child, parent, "agent_tool", session_id=sid,
                         agent_type=agent_type, tool_use_id=inp.get("tool_use_id"),
                         description=tool_input.get("description"),
                         model_pinned=_pinned(agent_type, cfg), model_seen=seen,
                         kind=_kind(agent_type, cfg))
        _mismatch_check(conn, cfg, inp, sid, child, agent_type, seen)
    finally:
        close_db(conn)
    return None


def _pinned(agent_type, cfg):
    from .. import config

    return config.pinned_model_for(agent_type, cfg)


def _kind(agent_type, cfg):
    from .. import config

    return config.role_from_agent_type(agent_type, cfg)


def mismatch(pinned, seen):
    """True when ``seen`` is not the pinned model (fallback, or a lost ``[1m]``)."""
    if not pinned or not seen:
        return False
    p, s = str(pinned), str(seen)
    if prices.normalize_model(p) != prices.normalize_model(s):
        return True
    return ("[1m]" in p.lower()) and ("[1m]" not in s.lower())


def _claim_once(key):
    """One-shot flag under ``state/mismatch/`` (``O_EXCL``, same trick as handoff)."""
    from .. import paths

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))[:120]
    path = paths.state_path("mismatch", safe)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError:
        return False
    try:
        os.write(fd, now_iso().encode("ascii", "replace"))
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return True


def _mismatch_check(conn, cfg, inp, sid, run_id, agent_type, seen):
    pinned = _pinned(agent_type, cfg)
    if not mismatch(pinned, seen):
        return False
    if not _claim_once("%s-%s-%s" % (sid, agent_type, seen)):
        return False
    detail = {"agent": agent_type, "expected": pinned, "seen": seen, "source": "agent_tool"}
    if conn is not None:
        from .. import db

        db.insert_event(conn, "model_mismatch", detail, session_id=sid, run_id=run_id)
    else:
        spool_event(sid, "model_mismatch", detail, run_id=run_id)
    root = governed(inp)
    toast(cfg, "PA3 · model fallback",
          "%s expected %s, request ran on %s" % (agent_type, pinned, seen),
          kind="model_mismatch", session_id=sid, project_cfg=project_config(root),
          cause="model", run_id=run_id)
    return True


# --------------------------------------------------------------------------- credit helpers

def _build_credit_rows(tool, inp, root, sid, run_id, model, cfg):
    """Build credit rows: Bash consumes notes, Read builds a read_row."""
    from .. import credit

    credit_rows = []
    read_rel = None
    if tool == "Bash":
        notes = credit.consume(root)
        if notes:
            resp = inp.get("tool_response")
            rc = credit.result_chars(resp)
            credit_rows = credit.rows_for_call(
                notes, session_id=sid, run_id=run_id, ts=now_iso(),
                model=model, result_chars=rc, cfg=cfg)
    elif tool == "Read":
        ti = inp.get("tool_input")
        if isinstance(ti, dict):
            fp = str(ti.get("file_path") or "")
            if fp:
                try:
                    read_rel = os.path.relpath(fp, root).replace("\\", "/")
                except ValueError:
                    read_rel = None
                if read_rel and read_rel.startswith(".."):
                    read_rel = None  # outside project
        if read_rel:
            resp = inp.get("tool_response")
            rc = credit.result_chars(resp)
            kind, ftok, ts = "read", 0, now_iso()
            if ti.get("offset") is not None or ti.get("limit") is not None:
                served = _served_outline_note(root, read_rel)
                if served:
                    # the guard served this path's outline on a denied whole Read
                    # (no PostToolUse of its own): its row lands here, as Bash would
                    credit_rows = credit.rows_for_call(
                        [served], session_id=sid, run_id=run_id, ts=ts, model=model,
                        result_chars=int(served.get("emitted_chars") or 0), cfg=cfg)
                    for crow in credit_rows:
                        crow["tool"] = "Read"
                    ftok = credit_rows[0]["file_tokens"]
                else:
                    ftok = _outline_pending(run_id, read_rel)
                if ftok is not None:
                    kind = "outline-read"
            credit_rows.append(credit.read_row(sid, run_id, ts,
                                               model, read_rel, rc, kind=kind,
                                               file_tokens=ftok or 0))
    return credit_rows, read_rel


def _served_outline_note(root, rel):
    """Consume the pending ``outline`` notes for ``rel`` (other notes stay for Bash);
    the latest one, or None."""
    from .. import credit
    credit_dir = os.path.join(root, ".run", "credit")
    try:
        names = sorted(os.listdir(credit_dir))
    except OSError:
        return None
    latest = None
    for name in names:
        path = os.path.join(credit_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                note = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if (isinstance(note, dict) and note.get("kind") == "outline"
                and credit._rel_path(note.get("path")) == rel):
            try:
                os.remove(path)
            except OSError:
                pass
            latest = note
    return latest


def _outline_pending(run_id, rel):
    """``file_tokens`` of the run's latest ``outline`` row for ``rel`` that has
    no ``outline-read`` row yet, else None (the outline credit: one per outline)."""
    try:
        conn = open_db(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT ts, file_tokens FROM tool_calls WHERE run_id=? AND path=?"
                " AND kind='outline' ORDER BY ts DESC LIMIT 1", (run_id, rel)).fetchone()
            if not row:
                return None
            done = conn.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND path=?"
                " AND kind='outline-read' AND ts >= ? LIMIT 1",
                (run_id, rel, row[0])).fetchone()
            return None if done else int(row[1] or 0)
        finally:
            close_db(conn)
    except Exception:
        return None


def _apply_credit_live(entry_owner, entry, credit_rows, read_rel, tool, model, cfg):
    """Apply credit rows and read voiding to a live running.json entry."""
    if credit_rows and tool == "Bash":
        from .. import credit as _cr, savings as _sv

        rets = entry.setdefault("retrievals", {})
        out_price = _sv.price_output(model)
        _seen_paths = set()
        _read_paths = set()
        # collect read paths from all agents in this session
        for _ae in (entry_owner.get("agents") or {}).values():
            if not isinstance(_ae, dict):
                continue
            for _rv in (_ae.get("retrievals") or {}).values():
                if isinstance(_rv, dict) and _rv.get("kind") == "read" and _rv.get("path"):
                    _read_paths.add(_rv["path"])
        for crow in credit_rows:
            carry = _cr.carry_tokens(crow, _seen_paths, _read_paths, cfg)
            cid = crow.get("id") or ""
            if carry > 0:
                rets[cid] = {"R": carry, "n_after": 0, "returned_at": crow.get("ts"),
                             "kind": crow.get("kind"), "path": crow.get("path")}
            em = int(crow.get("emission_tokens") or 0)
            if em > 0 and out_price > 0 and crow.get("kind") != "outline":
                entry["emission_live_usd"] = round(
                    float(entry.get("emission_live_usd") or 0.0)
                    + em * out_price / 1e6, 6)
    elif read_rel and tool == "Read":
        # record the read path and void matching live tool-call items
        rets = entry.setdefault("retrievals", {})
        rets["read-" + read_rel] = {"R": 0, "n_after": 0, "kind": "read", "path": read_rel}
        for _ae in (entry_owner.get("agents") or {}).values():
            if not isinstance(_ae, dict):
                continue
            for _rv in (_ae.get("retrievals") or {}).values():
                if isinstance(_rv, dict) and _rv.get("path") == read_rel and _rv.get("kind") != "read":
                    _rv["void"] = True


def _upsert_credit(credit_rows):
    """Upsert credit rows to sqlite (one transaction)."""
    if not credit_rows:
        return
    try:
        from .. import db

        conn = open_db()
        try:
            conn.execute("BEGIN")
            for crow in credit_rows:
                db.upsert_tool_call(conn, crow)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        finally:
            close_db(conn)
    except Exception:
        pass


def _credit_only(inp, cfg, agent_id, tool):
    """Credit path for non-worker subagents (coder, retriever, etc.).

    Consumes notes (Bash) or builds a read row (Read), upserts to sqlite, and
    updates the agent's own running.json entry -- the same helpers that
    ``_main_credit`` and ``_inside_worker`` use.
    """
    root = governed(inp)
    if not root:
        return None
    if tool == "Bash" and not os.path.isdir(os.path.join(root, ".run", "credit")):
        return None
    sid = sid_of(inp)
    model = pinned_for(inp, cfg)
    credit_rows, read_rel = _build_credit_rows(tool, inp, root, sid, agent_id, model, cfg)
    if credit_rows or read_rel:
        def _patch(data):
            entry_owner = running.session_ref(data, sid)
            entry = running.agent_ref(data, sid, agent_id)
            _apply_credit_live(entry_owner, entry, credit_rows, read_rel, tool, model, cfg)
            entry_owner["touched"] = now_iso()
            return data
        running.update(_patch)
    _upsert_credit(credit_rows)
    return None


def _main_credit(inp, cfg, tool):
    """Main-thread PostToolUse: bump live figures per request, credit on Bash/Read.

    The transcript tail read detects a new request (same O(1) tail as the worker
    path) and bumps ``n_requests``, ``cost_live_usd``, ``bump_after`` and
    ``saved_live_usd``.  Credit items are only built for Bash/Read (unchanged).
    """
    from .. import transcript

    sid = sid_of(inp)
    run_id = sid                                           # main thread's run_id is the session id
    root = governed(inp)

    # Transcript: detect new request for n_requests / cost / bump_after
    path = inp.get("transcript_path")
    req = None
    if path and os.path.exists(path):
        req = transcript.tail_last_usage(path)
    msg_id = req.get("msg_id") if req else None

    # Model: transcript's latest request model (harness does not send model
    # in a PostToolUse payload); fall back to inp for future-proofing.
    model = (req.get("model") if req else None) or inp.get("model")

    # Credit rows (Bash/Read only, needs governed root)
    credit_rows = []
    read_rel = None
    if tool in ("Bash", "Read") and root:
        if tool != "Bash" or os.path.isdir(os.path.join(root, ".run", "credit")):
            credit_rows, read_rel = _build_credit_rows(tool, inp, root, sid, run_id, model, cfg)

    if not msg_id and not credit_rows and not read_rel:
        return None

    # Compute cost of the new request
    cost = 0.0
    seen = None
    if req and msg_id:
        seen = req.get("model")
        from .. import config

        ttl = config.ttl_for_role("main", cfg)
        cost, _parts = prices.cost_of(req, seen or model, ttl_default=ttl,
                                       at=req.get("ts_iso"))

    def _patch(data):
        entry_owner = running.session_ref(data, sid)
        entry = running.agent_ref(data, sid, run_id)
        # Bump n_requests, cost, bump_after on new request
        if msg_id and entry.get("last_msg_id") != msg_id:
            entry["n_requests"] = int(entry.get("n_requests") or 0) + 1
            entry["last_msg_id"] = msg_id
            entry["cost_live_usd"] = round(float(entry.get("cost_live_usd") or 0.0) + cost, 6)
            running.bump_after(entry, req.get("ts_iso") if req else None)
        # Credit items (Bash/Read)
        if credit_rows or read_rel:
            _apply_credit_live(entry_owner, entry, credit_rows, read_rel, tool, model, cfg)
        # Recompute saved_live_usd
        use_model = seen or model
        price = prices.price_for(use_model)
        price_read = (price or {}).get("read") or 0.0
        price_write = (price or {}).get("write_5m") or 0.0
        price_input = (price or {}).get("input") or 0.0
        entry["saved_live_usd"] = round(running.live_saved(entry, price_read, price_write,
                                                           price_input), 6)
        entry_owner["touched"] = now_iso()
        return data

    running.update(_patch)
    _upsert_credit(credit_rows)
    return None


# --------------------------------------------------------------------------- 3. inside a worker

def _inside_worker(inp, cfg, agent_id, role, may_handoff=True):
    from .. import transcript

    path = agent_transcript(inp)
    if not path or not os.path.exists(path):
        return None
    req = transcript.tail_last_usage(path)
    if not req:
        return None
    meta_parent = transcript.read_meta(path).get("parentAgentId")   # written by the harness
    is_workflow = bool(transcript.workflow_id_of(path))             # T1.1: cost, never savings

    sid = sid_of(inp)
    root = governed(inp)
    pcfg = project_config(root)
    agent_type = inp.get("agent_type")
    pinned = pinned_for(inp, cfg)
    seen = req.get("model")
    ctx = handoff.ctx_next(req)
    msg_id = req.get("msg_id")
    limit = handoff.threshold(cfg, role=role, project_cfg=pcfg, model=seen or pinned)

    from .. import config

    ttl = config.ttl_for_role(role, cfg)
    cost, _parts = prices.cost_of(req, seen, ttl_default=ttl, at=req.get("ts_iso"))
    price = prices.price_for(seen or pinned)
    price_read = (price or {}).get("read") or 0.0
    price_write = (price or {}).get("write_5m") or 0.0
    price_input = (price or {}).get("input") or 0.0

    # credit: consume notes on Bash, prepare read path on Read
    tool = inp.get("tool_name")
    credit_rows = []
    read_rel = None
    if tool in ("Bash", "Read") and root:
        credit_rows, read_rel = _build_credit_rows(
            tool, inp, root, sid, agent_id, seen or pinned, cfg)

    out = {"fire": False, "alert": False}

    def _patch(data):
        entry_owner = running.session_ref(data, sid)
        entry = running.agent_ref(data, sid, agent_id, agent_type=agent_type, role=role,
                                  parent=sid, model_pinned=pinned)
        entry["role"] = role
        entry["agent_type"] = agent_type or entry.get("agent_type")
        if meta_parent and entry.get("parent") != str(meta_parent):
            entry["parent"] = str(meta_parent)
            entry["parent_source"] = "meta"
        entry["ctx"] = ctx
        entry["model_seen"] = seen or entry.get("model_seen")
        if req.get("effort"):
            entry["effort_seen"] = req.get("effort")          # 3.1 T10: statusline line 2
        entry.setdefault("model_pinned", pinned)
        if msg_id and entry.get("last_msg_id") != msg_id:
            entry["n_requests"] = int(entry.get("n_requests") or 0) + 1
            entry["last_msg_id"] = msg_id
            entry["cost_live_usd"] = round(float(entry.get("cost_live_usd") or 0.0) + cost, 6)
            running.bump_after(entry, req.get("ts_iso"))
            # seed carry live: mirror _seed_carry_run
            _seed = int(entry.get("seed_ctx") or 0)
            if _seed > 0 and ctx > 0:
                entry["seed_carry_usd"] = round(
                    float(entry.get("seed_carry_usd") or 0.0)
                    + min(1.0, _seed / ctx) * cost, 6)
        # credit live items: Bash adds carry items, Read voids them
        _apply_credit_live(entry_owner, entry, credit_rows, read_rel, tool,
                           seen or pinned, cfg)
        if not is_workflow:
            entry["saved_live_usd"] = round(running.live_saved(entry, price_read, price_write,
                                                               price_input), 6)
        entry["threshold"] = limit
        # The handoff *injection* is governance: only a PA3 project (a project
        # whose agents know what TASK_PROGRESS.md is) gets the task text, so the
        # once-flag is claimed only there.  Ledger fields above are updated in
        # every project.
        if may_handoff and root and not entry.get("handoff_fired") and ctx >= limit:
            if handoff.claim(agent_id, {"ctx": ctx, "threshold": limit, "session": sid}):
                entry["handoff_fired"] = True
                entry["handoff_at"] = now_iso()
                out["fire"] = True
        if not entry.get("mismatch_alerted") and mismatch(pinned, seen):
            entry["mismatch_alerted"] = True
            out["alert"] = True
        entry_owner["touched"] = now_iso()
        return data

    running.update(_patch)

    _upsert_credit(credit_rows)

    if out["alert"]:
        spool_event(sid, "model_mismatch",
                    {"agent": agent_type, "expected": pinned, "seen": seen,
                     "source": "transcript"}, run_id=agent_id)
        toast(cfg, "PA3 · model fallback",
              "%s expected %s, request ran on %s" % (agent_type, pinned, seen),
              kind="model_mismatch", session_id=sid, project_cfg=pcfg,
              cause="model", run_id=agent_id)

    if not out["fire"]:
        return None

    text = handoff.text(cfg, role=role, project_cfg=pcfg)
    _record_handoff(sid, agent_id, ctx, limit, role)
    log.log("handoff", session=sid, agent=agent_id, ctx=ctx, threshold=limit, role=role)
    return handoff.payload(text)


def _record_handoff(sid, agent_id, ctx, limit, role):
    """Once per agent: flip ``agent_runs.handoff_fired`` and log the event."""
    conn = None
    try:
        from .. import db

        conn = open_db()
        db.upsert_agent_run(conn, {"run_id": agent_id, "session_id": sid,
                                   "handoff_fired": 1, "status": "handoff"})
        db.insert_event(conn, "handoff", {"ctx": ctx, "threshold": limit, "role": role},
                        session_id=sid, run_id=agent_id)
    except Exception:
        spool_event(sid, "handoff", {"ctx": ctx, "threshold": limit, "role": role},
                    run_id=agent_id)
    finally:
        close_db(conn)


# --------------------------------------------------------------------------- 4. other roles

def _inside_other(inp, cfg, agent_id, role):
    from .. import transcript

    path = agent_transcript(inp)
    if not path or not os.path.exists(path):
        return None
    req = transcript.tail_last_usage(path)
    if not req:
        return None
    fields = {"ctx": handoff.ctx_next(req), "role": role,
              "agent_type": inp.get("agent_type"),
              "model_seen": req.get("model"), "last_msg_id": req.get("msg_id")}
    meta_parent = transcript.read_meta(path).get("parentAgentId")
    if meta_parent:
        fields["parent"], fields["parent_source"] = str(meta_parent), "meta"
    running.set_agent(sid_of(inp), agent_id, fields)
    return None
