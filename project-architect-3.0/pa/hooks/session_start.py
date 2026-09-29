"""SessionStart: stamp the account, open the session and its main run.

Input (verified, recon V5): ``session_id, transcript_path, cwd, source,
agent_type?, model?, session_title?`` plus ``seconds_since_last_response`` and
``context_tokens`` on a resume.

``source`` decides what happens: ``startup``/``clear``/``fork`` open a row,
``resume`` re-stamps the account (and records ``events:account_change`` when the
developer switched accounts between runs), ``compact`` only logs -- the session
row already exists and its cost keeps accumulating.

Budget <= 600 ms, timeout 15 s: this is the one hook that may run
``claude auth status`` (~210 ms, cached on the credentials mtime).
Returns nothing -- SessionStart output would be injected into the model's
context, and D38 keeps ledger data out of it.
"""

import os

from .. import accounts, log, paths, running, summary
from . import (agent_of, close_db, disk_ok, effort_of, find_project_root,
               governed, now_iso, open_db, pinned_for, project_config, role_of,
               session_kind, sid_of, spool_event, status_patch, status_read,
               toast)


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    source = str(inp.get("source") or "startup").lower()
    paths.ensure_ledger_tree()

    status = accounts.auth_status(cfg)
    email = status.get("email")
    if email:
        accounts.stamp_session(sid, email, source=status.get("source") or "auth")

    if not disk_ok(cfg):
        log.log("disk_low", session=sid, hook="session_start")

    conn = open_db()
    try:
        _record(conn, inp, cfg, sid, source, status, email)
        if email:
            # the plan tier from .credentials.json: one file read, no subprocess, never raises
            accounts.restamp_tier(conn, email)
        try:
            _reconcile_ladder(inp, conn, email)
        except Exception as exc:       # fail soft: a stale model line costs a fallback, never the session
            log.log("ladder_reconcile_failed", session_id=sid, error=str(exc)[:200])
    finally:
        close_db(conn)

    # 3.9.5 T4: the session's warmer daemon (never twice: a live <sid>.pid wins)
    root = governed(inp)
    if root:
        try:
            from .. import warmer
            warmer.start_if_needed(root, sid, cfg, os.environ.get("CLAUDE_PID"))
        except Exception as exc:       # fail soft: a cold cache costs a rewrite, never the session
            log.log("warmer_start_failed", session_id=sid, error=str(exc)[:200])

    # memory link repair: governed main session only (never a subagent)
    if not agent_of(inp):
        try:
            _ensure_memory_link(inp, cfg, sid)
        except Exception as exc:       # fail soft: the link is a convenience, the session is not
            log.log("memory_link_failed", session_id=sid, error=str(exc)[:200])

    # clear stoppedByUser on in-flight runs: governed main session resume only
    if source == "resume" and not agent_of(inp):
        try:
            _unstop_runs(inp, sid)
        except Exception as exc:
            log.log("unstop_runs_failed", session_id=sid, error=str(exc)[:200])

    # progress digest from a killed run: governed main session resume only
    if source == "resume" and not agent_of(inp):
        try:
            _progress_from_kill(inp, sid)
        except Exception as exc:
            log.log("progress_from_kill_failed", session_id=sid, error=str(exc)[:200])

    if source == "clear" and governed(inp):
        # /clear empties the context and the main-thread agent's initialPrompt does not re-fire
        # (docs); this line lands in the fresh context so the next message re-seeds the router.
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                "PA3: context cleared. Run the interpreter named in .claude/pa.json on "
                "tools/launch.py --seed-only, read .run/seed.md, and enter the mode it names."}}

    # install offer and update check
    lines = []
    user_lines = []
    try:
        parts = _offer_parts(inp, cfg)                  # one git probe feeds both lines
        offer = _offer_model_line(parts)
        if offer:
            lines.append(offer)
        user_offer = _offer_user_line(parts)            # 3.11 T7: the user's `!` line, once per repository
        if user_offer:
            user_lines.append(user_offer)
    except Exception as exc:
        log.log("offer_install_failed", session_id=sid, error=str(exc)[:200])
    update = None
    try:
        update = _update_check(inp, cfg)
        if update:
            lines.append(update)
    except Exception as exc:
        log.log("update_check_failed", session_id=sid, error=str(exc)[:200])
    if not update:
        # T15: project files behind the installed PA3 (not gated by the update.json stamp)
        try:
            behind = _record_behind(inp, cfg)
            if behind:
                lines.append(behind)
        except Exception as exc:
            log.log("record_behind_failed", session_id=sid, error=str(exc)[:200])
    if source == "startup" and not agent_of(inp):
        try:
            missing = _agent_not_loaded(inp)
            if missing:
                lines.append(missing)
        except Exception as exc:
            log.log("agent_check_failed", session_id=sid, error=str(exc)[:200])

    # 3.11 T9: an account without a renewal day is asked, once per session (never guessed)
    if email and source in ("startup", "resume") and not agent_of(inp) and not inp.get("pa_probe"):
        try:
            from .. import renewal
            if renewal.due(cfg, sid, email):
                user_line, model_line = renewal.prompt(email)
                user_lines.append(user_line)
                lines.append(model_line)
                renewal.mark(sid, email)
        except Exception as exc:
            log.log("renewal_prompt_failed", session_id=sid, error=str(exc)[:200])

    out = {}
    if lines:
        out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}
    if user_lines:
        out["systemMessage"] = "\n".join(user_lines)     # shown to the user, not the model
    return out or None


def _record(conn, inp, cfg, sid, source, status, email):
    from .. import db

    cwd = inp.get("cwd") or ""
    agent_type = inp.get("agent_type")
    kind = session_kind(agent_type, cfg)
    role = role_of(inp, cfg)
    machine = paths.machine_tag()
    now = now_iso()

    first_sight = False
    if email:
        seen = conn.execute("SELECT email FROM accounts WHERE email=?", (email,)).fetchone()
        first_sight = seen is None
        row = accounts.account_row(status, now)
        if not first_sight:
            row.pop("first_seen", None)
        db.upsert_account(conn, row)

    prior = conn.execute("SELECT account, started, end_reason FROM sessions WHERE session_id=?",
                         (sid,)).fetchone()
    if source == "compact" and prior:
        log.log("session_compact", session=sid)       # design C: log only, no row
        return

    if prior and prior["account"] and email and prior["account"] != email:
        db.insert_event(conn, "account_change",
                        {"from": prior["account"], "to": email, "source": source},
                        session_id=sid, account=email)

    if source == "resume":
        # T7.1: a killed run's session resumes -- the session is no longer ended (explicit
        # UPDATE: db.upsert_session's merge only sets columns present in the row).
        prev_reason = prior["end_reason"] if prior else None
        conn.execute("UPDATE sessions SET ended=NULL, end_reason=NULL WHERE session_id=?", (sid,))
        db.insert_event(conn, "resumed", {"end_reason": prev_reason}, session_id=sid, account=email)

    # phase tag: the phase on the router_session, ``beside <phase>`` elsewhere (T28)
    phase_id = None
    root = governed(inp)
    if root:
        from .. import phases as _phases
        phase_id = _phases.session_phase(root, sid, status_read(root))

    session_row = {
        "session_id": sid, "account": email,
        "account_source": status.get("source") or "unknown",
        "machine": machine, "project": (inp.get("cwd") or None), "cwd": cwd,
        "transcript_path": inp.get("transcript_path"), "kind": kind,
        "agent_name": agent_type, "model_launch": inp.get("model") or _launch_field(governed(inp), "model"),
        "effort_launch": effort_of(inp) or _launch_field(governed(inp), "effort"), "version": inp.get("version"),
        "phase": phase_id,
    }
    if not prior or source in ("startup", "clear", "fork"):
        session_row["started"] = (prior["started"] if prior and prior["started"] else now)
    db.upsert_session(conn, session_row)

    db.upsert_agent_run(conn, {
        "run_id": sid, "session_id": sid, "parent_run_id": None, "parent_source": "main",
        "kind": kind, "agent_type": agent_type, "spawn_depth": 0,
        "model_pinned": pinned_for(inp, cfg), "model_seen": inp.get("model"),
        "effort": effort_of(inp), "started": now, "status": "running",
        "transcript_path": inp.get("transcript_path"),
    })
    log.log("session_start", session=sid, source=source, kind=kind, account=email)

    summary.patch_session(sid, {"account": email, "kind": kind, "project": cwd,
                                "started": now, "cost_usd": 0.0})

    running.prune(48)
    running.ensure_session(sid, account=email, kind=kind, project=cwd,
                           started=now, touched=now)

    if first_sight and email:
        toast(cfg, "PA3 · new account", "%s registered in the usage ledger" % email,
              kind="new_account", session_id=sid,
              project_cfg=project_config(_root_of(inp)), cause="account", run_id=sid)
    if agent_of(inp):
        log.log("session_start_in_subagent", session=sid, role=role)


def _root_of(inp):
    from . import find_project_root

    return find_project_root((inp or {}).get("cwd"))


_ABSENT = {"absent": "tier", "base": None, "sha": None, "user": None}


def _reconcile_ladder(inp, conn, email):
    """3.10.6 T3: the managed agents follow the account's plan tier.

    Main session, governed root with ``.claude/pa3-managed.json`` only. A start with no
    mismatch and ``pa.json`` ``preset`` in step reads pa.json, the six agents and the db row
    and writes nothing; a mismatch re-renders the installed text (body edits kept), installs
    or removes expert-fable and updates the managed record.
    A ``pa_probe`` payload (doctor's latency run on a fixture) returns at once: a probe never mutates the project."""
    if agent_of(inp) or (inp or {}).get("pa_probe"):
        return
    root = governed(inp)
    if not root or not os.path.isfile(os.path.join(root, ".claude", "pa3-managed.json")):
        return
    import json as _json
    from ..install import ladder

    pa_path = os.path.join(root, ".claude", "pa.json")
    try:
        with open(pa_path, "r", encoding="utf-8", newline="") as fh:
            pa_text = fh.read()
        pa = _json.loads(pa_text)
    except (OSError, ValueError):
        return
    if not isinstance(pa, dict):
        return
    preset = accounts.tier_for(conn, email, pa)
    override = pa.get("ladder") if isinstance(pa.get("ladder"), dict) else None
    agents_dir = os.path.join(root, ".claude", "agents")
    todo = ladder.mismatches(agents_dir, preset, override)
    if todo:
        _ladder_apply(root, agents_dir, todo, preset, override)
    if pa.get("preset") != preset:
        _write_preset(pa_path, pa_text, pa, preset)


def _pkg_agent(agent):
    """The packaged agent text from the package clone, else None."""
    path = os.path.join(paths.package_clone_dir(), "project-architect-3.0", "agents", agent + ".md")
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


def _ladder_apply(root, agents_dir, todo, preset, override):
    """Re-render, install or remove each mismatched agent; read-modify-write the record's ``files``."""
    from ..install import ladder, managed

    rec_path, rec, files = managed._files(root)
    changed = False
    for agent in todo:
        rel = ".claude/agents/%s.md" % agent
        dst = os.path.join(agents_dir, agent + ".md")
        ent = files.get(rel) if isinstance(files.get(rel), dict) else {}
        cur = None
        if os.path.isfile(dst):
            with open(dst, "rb") as fh:
                cur = fh.read()
        if ladder.row(agent, preset, override) is None:          # absent at this tier
            if cur is None:
                if files.get(rel) != _ABSENT:
                    files[rel] = dict(_ABSENT)
                    changed = True
                continue
            s = managed.sha(cur)
            pkg = _pkg_agent(agent)
            renders = set()
            for p in (ladder.PRESETS if pkg is not None else ()):
                r = ladder.render(agent, pkg, p)
                if r is not None:
                    renders.add(managed.sha(r.encode("utf-8")))
            if s == ent.get("sha") or s in renders:
                os.remove(dst)
                files[rel] = dict(_ABSENT)
                changed = True
                log.log("ladder_reconcile_removed", agent=agent, preset=preset)
            else:
                log.log("ladder_reconcile_kept", agent=agent, preset=preset)
            continue
        if cur is not None:
            src = cur.decode("utf-8")
        else:
            src = _pkg_agent(agent)
            if src is None:
                log.log("ladder_reconcile_no_package", agent=agent, preset=preset)
                continue
            src = src.replace("\r\n", "\n")                      # agents install LF-only (fix-1)
        text = ladder.render(agent, src, preset, override)
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        new_sha = managed.sha(text.encode("utf-8"))
        if (cur is None or managed.sha(cur) == ent.get("sha")
                or (ent.get("absent") and not ent.get("sha"))):
            files[rel] = {"base": managed.base_of(text), "sha": new_sha, "user": None}
        elif ent:                                                # user-edited: keep base/sha
            ent = {k: ent.get(k) for k in ("base", "sha", "user")}
            if isinstance(ent.get("user"), dict):
                ent["user"] = dict(ent["user"], sha=new_sha)
            files[rel] = ent
        changed = True
        log.log("ladder_reconcile_rendered", agent=agent, preset=preset)
    if changed:
        rec = dict(rec)
        rec["files"] = files
        managed._write(rec_path, rec)


def _write_preset(path, text, doc, preset):
    """Set ``pa.json`` ``preset``, keeping every other key and the file's formatting where practical."""
    import json as _json
    import re
    from .. import fsutil

    val = _json.dumps(preset)
    want = dict(doc, preset=preset)
    nl = "\r\n" if "\r\n" in text else "\n"
    new, n = re.subn(r'("preset"\s*:\s*)(?:"[^"]*"|null)', lambda m: m.group(1) + val, text, count=1)
    if not n:
        new, n = re.subn(r'("tier"\s*:\s*"[^"]*")',
                         lambda m: m.group(1) + "," + nl + '  "preset": ' + val, text, count=1)
    try:
        ok = bool(n) and _json.loads(new) == want
    except ValueError:
        ok = False
    if not ok:
        new = _json.dumps(want, indent=2).replace("\n", nl) + nl
    fsutil.atomic_write_text(path, new)


def _launch_field(root, key):
    """``.run/launch.json`` ``model``/``effort`` for a session the launcher started (the hook's own
    input carries neither on a /clear-started or resumed session, observed 2026-09-20)."""
    if not root:
        return None
    from .. import fsutil

    doc = fsutil.read_json(os.path.join(root, ".run", "launch.json"), {}) or {}
    val = doc.get(key)
    return str(val) if val else None


def _ensure_memory_link(inp, cfg, sid):
    """Create the memory junction/symlink when a governed session needs it."""
    root = governed(inp)
    if not root:
        return
    import stat
    import sys as _sys

    repo_mem = os.path.join(root, ".claude-state", "memory")
    if not os.path.isdir(repo_mem):
        return

    slug = paths.project_slug(root.replace("\\", "/"))
    proj_mem = os.path.join(paths.projects_dir(), slug, "memory")

    # already linked?
    try:
        st = os.lstat(proj_mem)
        if stat.S_ISLNK(st.st_mode):
            return
        if (_sys.platform.startswith("win") or os.name == "nt"):
            if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return
    except OSError:
        pass

    # if it is a real directory, move its files into the repo dir
    if os.path.isdir(proj_mem):
        machine = paths.machine_tag()
        for name in sorted(os.listdir(proj_mem)):
            src = os.path.join(proj_mem, name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(repo_mem, name)
            if os.path.exists(dst):
                stem, ext = os.path.splitext(name)
                dst = os.path.join(repo_mem, "%s.%s%s" % (stem, machine, ext))
            import shutil
            shutil.move(src, dst)      # copies across drives; os.replace cannot
        try:
            os.rmdir(proj_mem)          # only an empty directory goes; anything left stays put
        except OSError:
            log.log("memory_link_skipped", session_id=sid, path=proj_mem,
                    reason="old memory dir not empty after the move")
            return

    # create the link
    os.makedirs(os.path.dirname(proj_mem), exist_ok=True)
    if _sys.platform.startswith("win") or os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", proj_mem, repo_mem],
                       capture_output=True, check=True)
    else:
        os.symlink(repo_mem, proj_mem, target_is_directory=True)

    pcfg = project_config(root)
    toast(cfg, "PA3 . memory linked", "restart the session to load the memory index",
          kind="memory_linked", session_id=sid, project_cfg=pcfg, cause="memory", run_id=sid)
    spool_event(sid, "memory_linked", detail={"repo_mem": repo_mem, "proj_mem": proj_mem})


# --------------------------------------------------------------------------- unstop in-flight runs

UNSTOP_KNOWN_VERSIONS = ("2.1.278",)


def _unstop_runs(inp, sid):
    """Clear ``stoppedByUser`` on in-flight runs before the digest is written."""
    root = governed(inp)
    if not root:
        return
    from .. import config as _config

    cfg = _config.load()
    if not _config.get(cfg, "resume.unstop", True):
        return

    # version gate: only known CLI versions
    version = (inp or {}).get("version")
    if not version or str(version) not in UNSTOP_KNOWN_VERSIONS:
        log.log("unstop_skipped", session_id=sid, reason="unknown version %s" % version)
        return

    st = status_read(root)
    run_id = st.get("run_id")
    if not run_id:
        return

    # locate the meta file
    tp = (inp or {}).get("transcript_path") or ""
    sub_dir = paths.subagents_dir(tp)
    if not sub_dir:
        return
    meta_path = os.path.join(sub_dir, "agent-%s.meta.json" % run_id)
    if not os.path.isfile(meta_path):
        return

    import json as _json
    import time as _time

    try:
        with open(meta_path, "rb") as fh:
            raw = fh.read()
        doc = _json.loads(raw.decode("utf-8", "replace"))
    except (OSError, ValueError):
        return

    if not isinstance(doc, dict) or not doc.get("stoppedByUser"):
        return

    # backup
    stamp = _time.strftime("%Y%m%d-%H%M%S")
    bak_path = "%s.bak-%s" % (meta_path, stamp)
    try:
        with open(bak_path, "wb") as fh:
            fh.write(raw)
    except OSError:
        return

    # rewrite with stoppedByUser cleared
    iso_now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    doc["stoppedByUser"] = False
    doc["pa3_cleared_stoppedByUser"] = iso_now
    try:
        with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_json.dumps(doc, separators=(",", ":")))
    except OSError:
        # restore the original
        try:
            with open(meta_path, "wb") as fh:
                fh.write(raw)
        except OSError:
            pass
        return

    # record the event and mark the status for the seed line
    conn = open_db()
    try:
        from .. import db
        db.insert_event(conn, "unstop",
                        {"run_id": run_id, "backup": bak_path},
                        session_id=sid)
    finally:
        close_db(conn)

    status_patch(root, {"unstop_cleared": run_id})
    log.log("unstop_cleared", session_id=sid, run_id=run_id, backup=bak_path)


# --------------------------------------------------------------------------- progress from kill

_TAIL_BYTES = 256 * 1024


def _progress_from_kill(inp, sid):
    """Write TASK_PROGRESS.md from a killed run's transcript on resume."""
    root = governed(inp)
    if not root:
        return
    from . import status_read

    st = status_read(root)
    run_id = st.get("run_id")
    task = st.get("task")
    killed_at = st.get("killed_at")
    if not (run_id and task and killed_at):
        return

    # locate the killed run's transcript inside this session's subagents dir
    tp = (inp or {}).get("transcript_path") or ""
    sub_dir = paths.subagents_dir(tp)
    if not sub_dir:
        return
    transcript = os.path.join(sub_dir, "agent-%s.jsonl" % run_id)
    if not os.path.isfile(transcript):
        log.log("progress_from_kill", run_id=run_id, note="transcript not found")
        return

    # an existing progress file (an expert's own handoff, or a digest of an earlier kill) is never
    # replaced: the digest is appended to it, once per killed run (the marker below)
    pp = paths.project_paths(root)
    progress_path = pp["task_progress"]
    existing = ""
    if os.path.exists(progress_path):
        try:
            with open(progress_path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        except OSError:
            existing = ""
        if ("Run: %s" % run_id) in existing:
            return                                  # this kill is already digested

    # read the tail of the transcript
    try:
        size = os.path.getsize(transcript)
        start = max(0, size - _TAIL_BYTES)
        with open(transcript, "rb") as fh:
            if start > 0:
                fh.seek(start)
            data = fh.read()
    except OSError:
        log.log("progress_from_kill", run_id=run_id, note="transcript read failed")
        return

    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]       # drop the partial first line

    # parse assistant records
    import json as _json

    tool_calls = []      # (tool_name, key_input)
    text_blocks = []     # raw text strings
    coder_runs = []      # (description,)

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = _json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name") or "?"
                inp_data = block.get("input") or {}
                key = _tool_key(name, inp_data)
                tool_calls.append((name, key))
                # track coder spawns
                if name == "Agent":
                    sat = str(inp_data.get("subagent_type") or "")
                    if "coder" in sat.lower():
                        desc = str(inp_data.get("description") or "")[:80]
                        coder_runs.append(desc)
            elif block.get("type") == "text":
                txt = block.get("text") or ""
                if txt.strip():
                    text_blocks.append(txt)

    # git context (2-second timeout, fail-soft)
    git_log = "(git unavailable)"
    git_status = "(git unavailable)"
    try:
        import subprocess
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-6"],
            cwd=root, capture_output=True, text=True, timeout=2
        ).stdout.strip() or "(empty)"
        raw_status = subprocess.run(
            ["git", "status", "--short"],
            cwd=root, capture_output=True, text=True, timeout=2
        ).stdout.strip()
        status_lines = raw_status.split("\n") if raw_status else []
        if len(status_lines) > 12:
            status_lines = status_lines[:12] + ["..."]
        git_status = "\n".join(status_lines) or "(clean)"
    except Exception:
        pass

    # git HEAD short
    git_head = "unknown"
    try:
        import subprocess
        git_head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=2
        ).stdout.strip() or "unknown"
    except Exception:
        pass

    attempt = st.get("attempt") or 1
    task_title = st.get("task_title") or ""
    agent_type = st.get("expert_agent_type") or "?"

    # build the digest
    last_tools = tool_calls[-12:]
    last_texts = text_blocks[-3:]
    out = []
    out.append("# TASK_PROGRESS -- %s attempt %s" % (task, attempt))
    out.append("")
    out.append("Task: %s %s" % (task, task_title))
    out.append("Attempt: %s   Agent: %s   Killed at: %s   Run: %s   Commit: %s"
               % (attempt, agent_type, killed_at, run_id, git_head))
    out.append("")
    out.append("## Done so far")
    for tname, tkey in last_tools:
        out.append("- %s -- %s" % (tname, tkey))
    out.append("- coder runs spawned: %d" % len(coder_runs))
    for desc in coder_runs:
        out.append("  - %s" % desc)
    out.append("- git log: %s" % git_log)
    out.append("- git status: %s" % git_status)
    out.append("")
    out.append("## In flight")
    for txt in last_texts:
        collapsed = " ".join(txt.split())[:400]
        out.append("- %s" % collapsed)
    out.append("")
    out.append("## Hypotheses rejected")
    out.append("- (not recorded: this digest was written by the resume hook from the transcript)")
    out.append("")
    out.append("## Current hypothesis")
    out.append("The killed run's last words are above; the tree must be verified before continuing.")
    out.append("")
    out.append("## Next 5 steps")
    out.append("1. Read logs/%s.c*.md and the coder commits" % task)
    out.append("2. Re-run the task's verify command")
    out.append("3. Continue from the last unfinished step")
    out.append("4. Archive this file as logs/%s.progress%s.md" % (task, attempt))
    out.append("5. Return the contract")
    out.append("")

    if existing:
        # keep the expert's own words; add this kill's digest below them
        cut = out.index("## Hypotheses rejected")
        section = ["", "## Resume digest (killed at %s, run %s, attempt %s)" % (killed_at, run_id, attempt),
                   "Run: %s" % run_id] + out[5:cut]
        body = "\n".join(section) + "\n"
        mode = "a"
    else:
        body = "\n".join(out) + "\n"
        mode = "w"
    os.makedirs(os.path.dirname(progress_path), exist_ok=True)
    with open(progress_path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write(body)

    line_count = len(out)
    log.log("progress_from_kill", run_id=run_id, lines=line_count)


def _tool_key(name, inp_data):
    """One-line summary of a tool call's key input."""
    if name in ("Read", "Edit", "Write"):
        return str(inp_data.get("file_path") or "")[:80]
    if name == "Bash":
        # the call's description says what it did; the command text is often a PY=... prefix
        return str(inp_data.get("description") or inp_data.get("command") or "")[:80]
    if name == "Agent":
        sat = str(inp_data.get("subagent_type") or "")
        desc = str(inp_data.get("description") or "")
        return ("%s %s" % (sat, desc)).strip()[:80]
    if name == "SendMessage":
        msg = str(inp_data.get("message") or inp_data.get("prompt") or "")
        return msg[:80]
    if name == "Grep":
        return str(inp_data.get("pattern") or "")[:80]
    if name == "Glob":
        return str(inp_data.get("pattern") or "")[:80]
    # fallback: first 80 chars of the whole input
    return str(inp_data)[:80]


# --------------------------------------------------------------------------- install offer

def _offer_install(inp, cfg):
    """Offer to govern an ungoverned git work tree (startup, main session only): the model-facing line."""
    return _offer_model_line(_offer_parts(inp, cfg))


def _offer_model_line(parts):
    """The model-facing offer (3.7); None when there is nothing to offer or the repository is ignored."""
    if not parts:
        return None
    top_fwd, clone_fwd, py = parts
    from .. import setup_offer
    if setup_offer.state(top_fwd).get("never"):
        return None
    return ("PA3: this repository is not governed. Say `install pa3` to set it up "
            "with defaults; the session runs `%s %s/project-architect-3.0/pa_install.py "
            "--project %s --yes` and then says: exit and run claude again."
            % (py, clone_fwd, top_fwd))


def _offer_user_line(parts):
    """3.11 T7: the user's copy-ready `!` line (a `!` command needs no permission prompt), at every
    startup (T7.1, developer: was once per repository) and never after `pa_install.py --ignore <repo>`
    or with ``setup_offer: off`` (``_offer_parts``); None otherwise."""
    if not parts:
        return None
    top_fwd, clone_fwd, py = parts
    from .. import setup_offer
    if setup_offer.state(top_fwd).get("never"):
        return None
    pyq = '"%s"' % py if " " in py else py
    installer = '"%s/project-architect-3.0/pa_install.py"' % clone_fwd
    return ("PA3: this repository is not set up for Project Architect yet. To set it up, type:\n"
            '  ! %s %s --project "%s" --yes\n'
            "then restart Claude Code (/exit, then claude). Not for this repository? Type:\n"
            '  ! %s %s --ignore "%s"' % (pyq, installer, top_fwd, pyq, installer, top_fwd))


def _offer_parts(inp, cfg):
    """``(repo, clone, python)`` (forward slashes) for an ungoverned git work tree at a main-session
    startup, else None (also with the machine's ``setup_offer: off``, 3.11 T7.1)."""
    from .. import config as _config
    if str(_config.get(cfg, "setup_offer", "on")).strip().lower() in ("off", "false", "no", "0"):
        return None
    source = str((inp or {}).get("source") or "startup").lower()
    if source != "startup":
        return None
    if agent_of(inp):
        return None
    if find_project_root((inp or {}).get("cwd")):
        return None

    version_path = os.path.join(paths.install_dir(), "VERSION")
    if not os.path.isfile(version_path):
        return None

    cwd = (inp or {}).get("cwd") or ""
    if not cwd:
        return None

    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    if not top:
        return None

    from pa.install.project import git_checks
    import types
    opts = types.SimpleNamespace(dry_run=True, yes=False)
    if git_checks(top, opts):
        return None

    clone = paths.package_clone_dir()
    if not os.path.isdir(clone):
        # fall back to VERSION source: line
        try:
            with open(version_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("source:"):
                        clone = line.split(":", 1)[1].strip()
                        break
        except OSError:
            return None
    if not clone or not os.path.isdir(clone):
        return None

    import sys as _sys
    py = _sys.executable.replace("\\", "/")
    top_fwd = top.replace("\\", "/")
    clone_fwd = clone.replace("\\", "/")
    return top_fwd, clone_fwd, py


# --------------------------------------------------------------------------- update check

def _update_check(inp, cfg):
    """Check for PA3 updates from the package clone (startup, main session only).

    A fresh stamp (update.json checked_at within install.update_check_hours) skips only the
    git fetch and the stamp rewrite; the local comparison and the offer run on every startup.
    """
    source = str((inp or {}).get("source") or "startup").lower()
    if source != "startup":
        return None
    if agent_of(inp):
        return None

    from .. import config as _config

    pa3_dir = paths.install_dir()
    clone = paths.package_clone_dir()
    if not os.path.isdir(clone):
        return None

    check_hours = _config.get(cfg, "install.update_check_hours", 24)
    stamp_path = os.path.join(pa3_dir, "update.json")

    import json as _json
    import time as _time

    now = _time.time()
    fresh = False
    try:
        with open(stamp_path, "r", encoding="utf-8") as fh:
            stamp = _json.loads(fh.read())
        checked_at = stamp.get("checked_at", 0)
        if isinstance(checked_at, (int, float)) and now - checked_at < check_hours * 3600:
            fresh = True
    except (OSError, ValueError):
        pass

    timeout_s = _config.get(cfg, "install.fetch_timeout_s", 8)
    import subprocess
    if not fresh:  # the stamp gates only the network step and the stamp rewrite
        try:
            result = subprocess.run(
                ["git", "-C", clone, "fetch", "--quiet"],
                capture_output=True, timeout=timeout_s)
            if result.returncode != 0:
                _write_stamp(stamp_path, {"checked_at": now, "status": "offline"})
                return None
        except (OSError, subprocess.SubprocessError):
            _write_stamp(stamp_path, {"checked_at": now, "status": "offline"})
            return None

    # installed hash, package tree and version from VERSION git:/tree:/version: lines
    version_path = os.path.join(pa3_dir, "VERSION")
    installed = ""
    tree = ""
    inst_version = ""
    try:
        with open(version_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("version:") and not inst_version:
                    inst_version = line.split(":", 1)[1].strip()
                elif line.startswith("git:") and not installed:
                    installed = line.split(":", 1)[1].strip()
                elif line.startswith("tree:") and not tree:
                    tree = line.split(":", 1)[1].strip()
    except OSError:
        pass

    # remote package tree (only when VERSION carries tree:; legacy keeps the whole-repo rule)
    remote_tree = ""
    if tree:
        try:
            r = subprocess.run(
                ["git", "-C", clone, "rev-parse", "@{u}:project-architect-3.0"],
                capture_output=True, text=True, timeout=5)
            remote_tree = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            remote_tree = ""
    # remote package version (I11): the clone's project-architect-3.0/VERSION where the tree id comes from
    remote_version = ""
    if tree:
        try:
            r = subprocess.run(
                ["git", "-C", clone, "show", "@{u}:project-architect-3.0/VERSION"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                from .. import parse_version
                remote_version = parse_version(r.stdout)
        except (OSError, subprocess.SubprocessError):
            remote_version = ""
    scope = ["--", "project-architect-3.0"] if tree else []

    # behind count
    try:
        r = subprocess.run(
            ["git", "-C", clone, "rev-list", "--count", "HEAD..@{u}"] + scope,
            capture_output=True, text=True, timeout=5)
        behind = int(r.stdout.strip()) if r.returncode == 0 else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        behind = 0

    # first three subject lines of the remote commits
    try:
        r = subprocess.run(
            ["git", "-C", clone, "log", "--format=%s", "-3", "HEAD..@{u}"] + scope,
            capture_output=True, text=True, timeout=5)
        subjects = [s.strip() for s in r.stdout.strip().split("\n")
                    if s.strip()][:3]
    except (OSError, subprocess.SubprocessError):
        subjects = []

    # clone HEAD
    try:
        r = subprocess.run(
            ["git", "-C", clone, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        clone_head = r.stdout.strip() if r.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        clone_head = "unknown"

    # remote HEAD
    try:
        r = subprocess.run(
            ["git", "-C", clone, "rev-parse", "--short", "@{u}"],
            capture_output=True, text=True, timeout=5)
        remote_head = r.stdout.strip() if r.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        remote_head = "unknown"

    doc = {"checked_at": now, "clone_head": clone_head, "remote_head": remote_head,
           "behind": behind, "installed": installed, "subjects": subjects, "status": "ok"}
    if tree:
        doc["tree"] = tree
        doc["remote_tree"] = remote_tree
    if not fresh:
        _write_stamp(stamp_path, doc)

    root = find_project_root((inp or {}).get("cwd"))
    steps = _update_steps(clone, root, _upgrade_mode(root))

    if tree:
        # the package tree decides: commits outside project-architect-3.0/ (bench results) never offer
        tree_changed = bool(remote_tree) and remote_tree != tree
        ver_changed = bool(remote_version) and remote_version != inst_version
        if not (tree_changed or ver_changed):
            return None
        why = (" (version %s -> %s)" % (inst_version or "unknown", remote_version)
               if ver_changed else " (package changed)")
        if behind == 0:
            return ("PA3: the installed copy (%s) is behind the clone (%s)%s. %s"
                    % (installed or tree[:7], clone_head, why, steps))
        subj_str = "; ".join(subjects)
        return ("PA3: update available: %d commits (%s)%s. %s" % (behind, subj_str, why, steps))
    if behind > 0:
        subj_str = "; ".join(subjects)
        return ("PA3: update available: %d commits (%s). %s" % (behind, subj_str, steps))
    if installed and installed != clone_head:
        return ("PA3: the installed copy (%s) is behind the clone (%s). %s"
                % (installed, clone_head, steps))
    return None


def _upgrade_mode(root):
    """T15: ``.claude/pa.json`` ``upgrade``: "ask" -> ask, anything else/absent -> auto; ungoverned -> ask."""
    if not root:
        return "ask"
    import json as _json
    try:
        with open(os.path.join(root, ".claude", "pa.json"), "r", encoding="utf-8") as fh:
            doc = _json.load(fh)
    except (OSError, ValueError):
        return "auto"
    return "ask" if isinstance(doc, dict) and doc.get("upgrade") == "ask" else "auto"


def _installer(clone):
    """``<py> <clone>/project-architect-3.0/pa_install.py`` with forward slashes."""
    import sys as _sys
    py = _sys.executable.replace("\\", "/")
    return "%s %s/project-architect-3.0/pa_install.py" % (py, clone.replace("\\", "/"))


def _wrapper_cmd(root):
    """T20: ``<py> tools/pa3_update.py`` when ``<root>/tools/pa3_update.py`` exists, else None.

    ``<py>`` is ``.claude/pa.json`` ``python`` (fallback sys.executable), forward slashes."""
    if not root or not os.path.isfile(os.path.join(root, "tools", "pa3_update.py")):
        return None
    import json as _json
    import sys as _sys
    py = ""
    try:
        with open(os.path.join(root, ".claude", "pa.json"), "r", encoding="utf-8") as fh:
            doc = _json.load(fh)
        if isinstance(doc, dict):
            py = str(doc.get("python") or "")
    except (OSError, ValueError):
        pass
    return "%s tools/pa3_update.py" % (py or _sys.executable).replace("\\", "/")


def _project_steps(clone, root, mode):
    """T20: the project refresh text for a governed root: the wrapper, or the pre-T20 installer."""
    clone_fwd = clone.replace("\\", "/")
    wrap = _wrapper_cmd(root)
    if wrap:
        if mode == "auto":
            return ("Upgrade: auto: before the first task run `%s` (one command, never chained); "
                    "then announce from %s/project-architect-3.0/CHANGES.md." % (wrap, clone_fwd))
        return "Say `update pa3` to run `%s`, then restart." % wrap
    cmd = "%s --project %s --yes" % (_installer(clone), str(root).replace("\\", "/"))
    if mode == "auto":
        return ("Upgrade: auto: before the first task run `%s` once (this project predates "
                "tools/pa3_update.py); then announce from %s/project-architect-3.0/CHANGES.md."
                % (cmd, clone_fwd))
    return ("Say `update pa3` to run `%s` once (this project predates tools/pa3_update.py), "
            "then restart." % cmd)


def _update_steps(clone, root, mode):
    """The `update pa3` command text: the project wrapper when governed, else pull + root refresh.

    T15: mode "auto" (governed root) names the steps the router runs before the first task.
    T20: governed roots name ``tools/pa3_update.py`` (or the installer once, pre-T20 projects)."""
    if root:
        return _project_steps(clone, root, mode)
    clone_fwd = clone.replace("\\", "/")
    inst = _installer(clone)
    return ("Say `update pa3` to run `git -C %s pull --ff-only` and `%s --root --yes`, "
            "then restart." % (clone_fwd, inst))


def _record_behind(inp, cfg):
    """T15: the project's managed record (``git``) is behind the installed PA3 (VERSION ``git:``).

    Startup, main session, governed root with ``.claude/pa3-managed.json`` only; not gated
    by the update.json stamp. Behind: no record hash, or the record is an ancestor of it."""
    source = str((inp or {}).get("source") or "startup").lower()
    if source != "startup" or agent_of(inp):
        return None
    root = find_project_root((inp or {}).get("cwd"))
    if not root:
        return None
    import json as _json
    try:
        with open(os.path.join(root, ".claude", "pa3-managed.json"), "r", encoding="utf-8") as fh:
            rec_doc = _json.load(fh)
    except (OSError, ValueError):
        return None
    rec = str(rec_doc.get("git") or "").strip() if isinstance(rec_doc, dict) else ""
    inst = ""
    try:
        with open(os.path.join(paths.install_dir(), "VERSION"), "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("git:"):
                    inst = line.split(":", 1)[1].strip()
                    break
    except OSError:
        return None
    clone = paths.package_clone_dir()
    if not inst or not os.path.isdir(clone):
        return None
    if rec:
        if rec.startswith(inst) or inst.startswith(rec):     # same commit, short or full hash
            return None
        import subprocess
        try:
            r = subprocess.run(["git", "-C", clone, "merge-base", "--is-ancestor", rec, inst],
                               capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
    cmd = "%s --project %s --yes" % (_installer(clone), str(root).replace("\\", "/"))
    if _wrapper_cmd(root):                                  # T20: the wrapper texts
        steps = _project_steps(clone, root, _upgrade_mode(root))
    elif _upgrade_mode(root) == "auto":
        steps = ("Upgrade: auto: before the first task run `%s`; then announce from "
                 "%s/project-architect-3.0/CHANGES.md." % (cmd, clone.replace("\\", "/")))
    else:
        steps = "Say `update pa3` to run `%s`, then restart." % cmd
    return ("PA3: this project's files (record %s) are behind the installed PA3 (%s). %s"
            % (rec or "none", inst, steps))


def _agent_not_loaded(inp):
    """fix-1: settings name a main-thread agent but the payload carries no ``agent_type``."""
    import json as _json
    root = governed(inp)
    if not root or (inp or {}).get("agent_type"):
        return None
    try:
        with open(os.path.join(root, ".claude", "settings.json"), "r", encoding="utf-8") as fh:
            name = (_json.load(fh) or {}).get("agent")
    except (OSError, ValueError):
        return None
    if not name or not isinstance(name, str):
        return None
    line = "PA3: .claude/settings.json names agent %s but Claude Code did not load it" % name
    try:
        with open(os.path.join(root, ".claude", "agents", name + ".md"), "rb") as fh:
            crlf = b"\r\n" in fh.read()
    except OSError:
        crlf = False
    if crlf:
        import sys as _sys
        py = _sys.executable.replace("\\", "/")
        clone = paths.package_clone_dir().replace("\\", "/")
        line += (" (the file has CRLF line endings); run %s %s/project-architect-3.0/pa_install.py"
                 " --project %s --yes, then restart." % (py, clone, str(root).replace("\\", "/")))
    return line


def _write_stamp(path, doc):
    """Write update.json."""
    import json as _json
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_json.dumps(doc, indent=2))
    except OSError:
        pass
