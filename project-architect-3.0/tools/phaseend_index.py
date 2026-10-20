#!/usr/bin/env python
"""phaseend_index.py -- assemble, archive and legacy-index PhaseEnd files.

    phaseend_index.py assemble <N>     build phase-ends/PhaseEnd_Phase<N>.md
    phaseend_index.py --archive [<N>]  git mv current/ -> phase-<N>/, fresh current/
    phaseend_index.py legacy-index     one line per pre-PA3 PhaseEnd

The PhaseEnd's `## Agent runs` section is generated from the usage ledger (agent_runs rows
stamped with this phase, else this project's runs since approval), capped at 40 lines.

`assemble` is idempotent: the recap and binding blocks come from current/RECAP.md (written
before the close chain), else from an earlier assembled PhaseEnd, else `-`. Order at a phase
end: write RECAP.md, assemble, commit, archive. The milestone is confirmed by the developer
at the close. archive moves RECAP.md with current/; genend_index.py reads the Generation
Recap from phase-<N>/RECAP.md after that.
Stdlib only; no imports from `pa/` (it may import plan_edit.py, its neighbour).
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_edit as PE                                          # noqa: E402
import status as ST                                             # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LABELS = ("Status:", "Done:", "Files:", "Decisions:", "Deviations:", "Findings:",
          "Gotchas:", "Research:", "Rule candidate:", "Next task needs:",
          "Recommended:", "Verified:", "Full log:", "Tags:")
RECAP_PH = "{{AUTHORED:recap}}"     # an older assembled PhaseEnd may still hold these; never carried forward
BIND_PH = "{{AUTHORED:binding}}"
TASK_HEADER = ("# Tasks -- cumulative\n"
               "# phase | id | status | title | tags | summary | log | research\n")
RES_HEADER = ("# Research -- cumulative\n"
              "# phase | id | task | title | tags | agent | date | lines | path\n")
DISC_HEADER = ("# Discussions -- cumulative\n"
               "# phase | id | topic | date | status | path\n")
LEGACY_HEADER = ("# Legacy PhaseEnds (pre-PA3)\n"
                 "# phase | title | milestone | date | path\n")
RUNS_CAP = 40


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def cur_dir(root, conf=None):
    return os.path.join(PE.phase_dir(root, conf), "current")


def phaseend_path(root, conf, phase):
    return os.path.join(PE.phase_dir(root, conf), "PhaseEnd_Phase%s.md" % phase)


def natural_key(label):
    # the same rule as pa/install/natural_sort.key (a test asserts the two agree):
    # drop a file-name suffix, split digit and non-digit runs, numbers before words
    out = []
    base = re.sub(r"(\.[A-Za-z~][A-Za-z0-9~]*)+$", "", str(label))
    for part in re.split(r"[._\-]+", base):
        for run in re.findall(r"\d+|[^\d]+", part):
            if run.isdigit():
                out.append((0, int(run), ""))
            else:
                out.append((1, 0, run.lower()))
    return out


def sections(lines):
    out, cur = {}, None
    for raw in lines:
        s = raw.strip()
        if s.startswith("#"):
            cur = None
            continue
        hit = None
        for lab in LABELS:
            if s.startswith(lab):
                hit = lab
                break
        if hit:
            cur = hit[:-1]
            rest = s[len(hit):].strip()
            out[cur] = [rest] if rest else []
        elif cur and s:
            out[cur].append(s)
        elif not s:
            cur = cur
    return out


def read_summary(path):
    lines = PE.read_text(path).split("\n")
    head = lines[0].strip() if lines else ""
    m = re.match(r"^#\s*(\S+)\s*[-—]+\s*(.*)$", head)
    tid = m.group(1) if m else os.path.basename(path)[:-3]
    title = m.group(2).strip() if m else ""
    sec = sections(lines)
    status_line = " ".join(sec.get("Status", []))
    commit = "-"
    cm = re.search(r"commit:\s*([0-9a-f]{6,40})", status_line)
    if cm:
        commit = cm.group(1)
    st = status_line.split("|")[0].strip() or "-"
    return {"id": tid, "title": title, "status": st, "commit": commit,
            "sec": sec, "path": path}


def load_summaries(root, conf=None, phase=None):
    base = _phase_base(root, conf, phase) if phase else cur_dir(root, conf)
    d = os.path.join(base, "tasks")
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md") or name == "INDEX.md":
            continue
        out.append(read_summary(os.path.join(d, name)))
    out.sort(key=lambda s: natural_key(s["id"].lstrip("T")))
    return out


def _phase_base(root, conf, phase):
    """The directory holding the plan for *phase*: current/ when it matches, else the archive."""
    cur = cur_dir(root, conf)
    cur_plan = os.path.join(cur, "PHASE_PLAN.md")
    if phase and os.path.isfile(cur_plan):
        text = PE.read_text(cur_plan)
        m = re.match(r"^#\s*Phase\s+(\S+)\s", text.split("\n", 1)[0])
        cur_phase = m.group(1) if m else None
        if cur_phase != phase:
            arch = os.path.join(PE.phase_dir(root, conf), "phase-%s" % phase)
            if os.path.isdir(arch):
                return arch
            die("phase %s is not current (%s) and phase-%s/ does not exist"
                % (phase, cur_phase, phase))
    if phase and not os.path.isfile(cur_plan):
        arch = os.path.join(PE.phase_dir(root, conf), "phase-%s" % phase)
        if os.path.isdir(arch):
            return arch
        die("no current plan and phase-%s/ does not exist" % phase)
    return cur


def plan_bits(root, conf=None, phase=None):
    base = _phase_base(root, conf, phase)
    path = os.path.join(base, "PHASE_PLAN.md")
    if not os.path.isfile(path):
        die("no plan at %s" % PE.rel(root, path))
    text = PE.read_text(path)
    lines, tasks, headings = PE.parse(text)
    head = lines[0].strip() if lines else ""
    m = re.match(r"^#\s*Phase\s+(\S+)\s*[-—]+\s*(.*?)\s*(?:\(implements(.*)\))?$", head)
    phase = m.group(1) if m else "?"
    name = (m.group(2) or "").strip() if m else ""
    gen = ""
    if m and m.group(3):
        g = re.search(r"([0-9]+(?:\.[0-9]+)*)", m.group(3))
        gen = g.group(1) if g else ""
    milestone = ""
    approved = planner = "-"
    for raw in lines[:8]:
        b = PE.split_eol(raw)[0]
        if b.startswith("Milestone:"):
            milestone = b.strip()
        if b.startswith("Approved:"):
            am = re.match(r"Approved:\s*(\S+)\s+Planner:\s*(.*?)\s{2,}Plan-hash", b)
            if am:
                approved, planner = am.group(1), am.group(2).strip()
            else:
                approved = b.split()[1] if len(b.split()) > 1 else "-"
    changes = []
    ci = headings.get("Changes")
    if ci is not None:
        for raw in lines[ci + 1:]:
            b = PE.split_eol(raw)[0]
            if b.startswith("## "):
                break
            if b.strip():
                changes.append(b.rstrip())
    return {"phase": phase, "name": name, "gen": gen, "milestone": milestone,
            "approved": approved, "planner": planner, "tasks": tasks,
            "changes": changes, "path": path}


def index_lines(path, skip_comments=True):
    if not os.path.isfile(path):
        return []
    out = []
    for raw in PE.read_text(path).split("\n"):
        s = PE.split_eol(raw)[0].rstrip()
        if not s.strip():
            continue
        if skip_comments and (s.lstrip().startswith("#") or s.lstrip().startswith("<!--")
                              or s.lstrip().startswith("-->")):
            # index headers are `#` lines or HTML comment blocks (the INDEX.* templates); neither is an entry
            continue
        out.append(s)
    return out


def cookbook_for_phase(root, phase):
    out = []
    for line in index_lines(os.path.join(root, "cookbook", "INDEX.md")):
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 5 and (cells[4] == str(phase)
                                or cells[4].startswith(str(phase) + "/")):
            out.append(line)
    return out


def recap_parts(root, conf=None, phase=None):
    """(recap, binding) from current/RECAP.md (or phase-<N>/RECAP.md for an archive)."""
    base = _phase_base(root, conf, phase) if phase else cur_dir(root, conf)
    path = os.path.join(base, "RECAP.md")
    if not os.path.isfile(path):
        return None, None
    got = {}
    cur = None
    for raw in PE.read_text(path).split("\n"):
        b = PE.split_eol(raw)[0]
        if b.startswith("## "):
            head = b[3:].strip().lower()
            if head.startswith("recap"):
                cur = "recap"
            elif "bind" in head:
                cur = "binding"
            else:
                cur = None
            if cur:
                got.setdefault(cur, [])
            continue
        if cur:
            got[cur].append(b.rstrip())

    def clean(key):
        v = got.get(key)
        if v is None:
            return None
        while v and not v[0].strip():
            v.pop(0)
        while v and not v[-1].strip():
            v.pop()
        return "\n".join(v) if v else None
    return clean("recap"), clean("binding")


def ledger_path():
    """The usage ledger (``$PA_LEDGER_DIR`` or ``<config dir>/usage-ledger/ledger.sqlite``)."""
    d = os.environ.get("PA_LEDGER_DIR")
    if not d:
        cfgdir = (os.environ.get("CLAUDE_CONFIG_DIR")
                  or os.path.join(os.path.expanduser("~"), ".claude"))
        d = os.path.join(cfgdir, "usage-ledger")
    return os.path.join(d, "ledger.sqlite")


def _norm(path):
    return str(path or "").replace(chr(92), "/").rstrip("/").lower()


def agent_run_lines(root, phase, approved, cap=RUNS_CAP):
    """One generated line per ledger run of this phase (3.1 T11): task, agent type, kind,
    model, effort, ctx at end, cost, saved, status, parent.  Rows stamped with ``phase`` are
    the audit; only when none exist do unstamped rows of this project since approval stand
    in.  Main sessions never appear (they are the parents)."""
    path = ledger_path()
    if not os.path.isfile(path):
        return ["- (no ledger at %s)" % path]
    import sqlite3
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT r.task_id, r.agent_type, r.kind, r.model_seen, r.model_pinned, r.effort, "
            "r.ctx_at_end, r.cost_usd, r.status, r.parent_run_id, r.phase, r.started, s.project, "
            "(SELECT v.measured_saved_usd FROM savings v WHERE v.run_id = r.run_id) AS saved "
            "FROM agent_runs r LEFT JOIN sessions s ON s.session_id = r.session_id "
            "WHERE COALESCE(r.agent_type, '') != '' AND COALESCE(r.kind, '') != 'probe' "
            "AND r.run_id != COALESCE(r.session_id, '') ORDER BY r.started").fetchall()
        conn.close()
    except Exception as exc:                                     # sqlite3.Error, OSError
        return ["- (ledger unreadable: %s)" % exc]
    want, since = _norm(root), str(approved or "")
    picked = [r for r in rows if str(r["phase"] or "") == str(phase) and _norm(r["project"]) == want]
    if not picked:                                               # pre-stamping ledgers only
        picked = [r for r in rows if not r["phase"] and _norm(r["project"]) == want
                  and (since in ("", "-") or str(r["started"] or "")[:10] >= since[:10])]
    out = []
    for r in picked:
        ctx = r["ctx_at_end"]
        ctx_txt = "%dk" % round(ctx / 1000.0) if isinstance(ctx, (int, float)) and ctx else "-"
        cost = r["cost_usd"]
        cost_txt = "$%.3f" % cost if isinstance(cost, (int, float)) else "-"
        saved = r["saved"]
        saved_txt = "saved $%.2f" % saved if isinstance(saved, (int, float)) and saved > 0 else "saved -"
        out.append("- %s | %s | %s | %s | %s | ctx %s | %s | %s | %s | parent %s" % (
            r["task_id"] or "-", r["agent_type"] or "-", r["kind"] or "-",
            r["model_seen"] or r["model_pinned"] or "-", r["effort"] or "-", ctx_txt, cost_txt,
            saved_txt, r["status"] or "-", str(r["parent_run_id"] or "-")[:8]))
    if not out:
        return ["- (no runs recorded for phase %s)" % phase]
    if len(out) > cap:
        out = out[:cap - 1] + ["- … %d more: PY ~/.claude/pa3/pa_ledger.py report"
                               % (len(out) - (cap - 1))]
    return out


def audit_lines(root, phase, phases_avail=None):
    """The ``## Audit`` section (T10): seed stats, suspects with deltas, governed sizes."""
    out = []

    # -- seed stats from the ledger ----------------------------------------
    path = ledger_path()
    med, mx, n, prev_phase, prev_med, growth = None, None, None, None, None, None
    if os.path.isfile(path):
        import sqlite3
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            want = _norm(root)
            rows = conn.execute(
                "SELECT s.project, s.cwd, ar.phase, ar.seed_ctx "
                "FROM agent_runs ar "
                "JOIN sessions s ON s.session_id = ar.session_id "
                "WHERE ar.kind = 'expert' AND ar.seed_ctx IS NOT NULL AND ar.seed_ctx > 0"
            ).fetchall()
            conn.close()
            by_phase = {}
            for r in rows:
                p = _norm(r["project"] or r["cwd"])
                if p != want:
                    continue
                ph = str(r["phase"] or "")
                if ph:
                    by_phase.setdefault(ph, []).append(int(r["seed_ctx"]))
            if by_phase and str(phase) in by_phase:
                vals = sorted(by_phase[str(phase)])
                n = len(vals)
                mid_i = n // 2
                med = (vals[mid_i - 1] + vals[mid_i]) // 2 if n % 2 == 0 else vals[mid_i]
                mx = max(vals)
                sorted_phases = sorted(by_phase.keys(), key=natural_key)   # 3.14.1: was text order, 3.10 before 3.9
                idx = sorted_phases.index(str(phase)) if str(phase) in sorted_phases else -1
                if idx > 0:
                    prev_phase = sorted_phases[idx - 1]
                    pv = sorted(by_phase[prev_phase])
                    pn = len(pv)
                    pm = pn // 2
                    prev_med = (pv[pm - 1] + pv[pm]) // 2 if pn % 2 == 0 else pv[pm]
                    if prev_med > 0:
                        growth = round((med - prev_med) / prev_med * 100, 1)
        except Exception:
            pass

    if med is not None:
        out.append("- seed: median %dk, max %dk, n=%d (phase %s)" % (med // 1000, mx // 1000, n, phase))
        if prev_phase is not None and prev_med is not None:
            out.append("- previous phase %s: median %dk, growth %.1f%%" % (prev_phase, prev_med // 1000, growth or 0))
    else:
        out.append("- seed: no expert runs for phase %s" % phase)

    # -- suspects with sizes and deltas ------------------------------------
    prev_commit = None
    if prev_phase is not None:
        try:
            prev_pe = "phase-ends/PhaseEnd_Phase%s.md" % prev_phase
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H", "--", prev_pe],
                cwd=root, capture_output=True, text=True)
            h = (result.stdout or "").strip()
            if h:
                prev_commit = h
        except Exception:
            pass

    suspect_paths = ["CLAUDE.md"]
    skills_dir = os.path.join(root, ".claude", "skills")
    if os.path.isdir(skills_dir):
        try:
            for name in sorted(os.listdir(skills_dir)):
                if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md")):
                    suspect_paths.append(".claude/skills/%s/SKILL.md" % name)
        except OSError:
            pass
    mem_repo = os.path.join(root, ".claude-state", "memory", "MEMORY.md")
    if os.path.isfile(mem_repo):
        suspect_paths.append(".claude-state/memory/MEMORY.md")

    for sp in suspect_paths:
        full = os.path.join(root, sp.replace("/", os.sep))
        cur_size = os.path.getsize(full) if os.path.isfile(full) else None
        delta_txt = ""
        if prev_commit is not None and cur_size is not None:
            try:
                result = subprocess.run(
                    ["git", "show", "%s:%s" % (prev_commit, sp)],
                    cwd=root, capture_output=True)
                if result.returncode == 0:
                    old_size = len(result.stdout)
                    d = cur_size - old_size
                    delta_txt = " (%+d)" % d if d != 0 else " (unchanged)"
                else:
                    delta_txt = " (new)"
            except Exception:
                pass
        if cur_size is not None:
            out.append("- %s: %d bytes%s" % (sp, cur_size, delta_txt))

    # -- governed files' sizes ---------------------------------------------
    governed = [
        ("HOW_WE_WORK.md", 14000), ("cookbook/INDEX.md", None), ("rules/INDEX.md", None),
    ]
    for gf, _ in governed:
        full = os.path.join(root, gf.replace("/", os.sep))
        if os.path.isfile(full):
            sz = os.path.getsize(full)
            out.append("- %s: %d bytes" % (gf, sz))

    # -- carry audit via the CLI --------------------------------------------
    cli = os.path.expanduser("~/.claude/pa3/pa_ledger.py")
    if os.path.isfile(cli):
        audit_cmd = [sys.executable, cli, "audit", "--phase", str(phase),
                     "--project", str(root), "--md"]
        if phases_avail:
            # determine the previous phase
            try:
                idx = list(phases_avail).index(str(phase))
                if idx > 0:
                    audit_cmd += ["--prev", str(phases_avail[idx - 1])]
            except (ValueError, IndexError):
                pass
        try:
            result = subprocess.run(audit_cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",   # T4.c4: the CLI prints UTF-8 (the em dash)
                                    timeout=120, cwd=root)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    out.append(line)
            else:
                reason = (result.stderr or "").strip()[:200] or "exit %d" % result.returncode
                out.append("- audit unavailable: %s" % reason)
        except subprocess.TimeoutExpired:
            out.append("- audit unavailable: timeout (120 s)")
        except OSError as exc:
            out.append("- audit unavailable: %s" % str(exc)[:200])
    else:
        out.append("- audit unavailable: CLI not installed (%s)" % cli)

    return out or ["- (no audit data)"]


def existing_authored(path):
    """Authored blocks already in a previously assembled PhaseEnd."""
    out = {"recap": None, "binding": None}
    if not os.path.isfile(path):
        return out
    text = PE.read_text(path)
    m = re.search(r"(?ms)^## Plain-English Recap\s*\n(.*?)(?=\n## |\Z)", text)
    if m and RECAP_PH not in m.group(1) and m.group(1).strip():
        out["recap"] = m.group(1).strip("\n")
    m = re.search(r"(?ms)^## Decisions that still bind\s*\n(.*?)(?=\n## |\Z)", text)
    if m and BIND_PH not in m.group(1):
        # 3.25 T4: skip the generated `- <id> — …` lines (any summary id, was `- T` only) and the `-` fallback
        body = [x for x in m.group(1).split("\n")
                if x.strip() and x.strip() != "-" and not re.match(r"^- \S+ — ", x.strip())]
        if body:
            out["binding"] = "\n".join(body).strip("\n")
    return out


def _card_check(root):
    """Run ``tools/card.py check`` and refuse when it exits 1."""
    card_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.py")
    if not os.path.isfile(card_py):
        return  # card.py not available: skip silently
    res = subprocess.run([sys.executable, card_py, "check"],
                         cwd=root, capture_output=True, text=True)
    if res.returncode != 0:
        line = (res.stdout or "").strip()
        die("card check failed: %s" % line)


def _discussion_summary_lines(disc_dir):
    """``- D<n> | <topic> | <status>`` from discussions/INDEX.md."""
    idx = os.path.join(disc_dir, "INDEX.md")
    out = []
    for line in index_lines(idx):
        parts = [c.strip() for c in line.split("|")]
        if len(parts) >= 5:
            # id | topic | date | status | path
            out.append("- %s | %s | %s" % (parts[0], parts[1], parts[3]))
        elif len(parts) >= 4:
            # old format: id | topic | date | path
            out.append("- %s | %s | open" % (parts[0], parts[1]))
    return out


def _discussion_deferred_lines(disc_dir):
    """``- from D<n>: <what> → <phase>`` from each record's ``## Deferred`` section."""
    out = []
    if not os.path.isdir(disc_dir):
        return out
    for name in sorted(os.listdir(disc_dir)):
        m = re.match(r"^(D[0-9]+)\.md$", name)
        if not m:
            continue
        did = m.group(1)
        path = os.path.join(disc_dir, name)
        text = PE.read_text(path)
        in_deferred = False
        for line in text.split("\n"):
            if line.startswith("## Deferred"):
                in_deferred = True
                continue
            if in_deferred and line.startswith("## "):
                break
            if in_deferred and line.strip().startswith("- deferred:"):
                body = line.strip()[len("- deferred:"):].strip()
                if body and not body.startswith("<"):  # skip template placeholder
                    out.append("- from %s: %s" % (did, body))
    return out


def _discussion_cumulative_rows(disc_dir, phase):
    """``phase | id | topic | date | status | path`` rows from disc_dir/INDEX.md."""
    rows = []
    for line in index_lines(os.path.join(disc_dir, "INDEX.md")):
        line = line.replace("discussions/", "phase-%s/discussions/" % phase)
        rows.append("%s | %s" % (phase, line))
    return rows


def _update_cumulative_discussion_index(root, conf, phase, disc_dir):
    """Create/refresh phase-ends/DISCUSSION_INDEX.md: replace this phase's rows with the
    current ones from disc_dir/INDEX.md. Idempotent -- assemble then assemble again, or
    assemble then archive, leaves the file byte-identical. Returns the row count written
    for this phase."""
    pdir = PE.phase_dir(root, conf)
    di = os.path.join(pdir, "DISCUSSION_INDEX.md")
    lines = PE.read_text(di).split("\n") if os.path.isfile(di) else DISC_HEADER.split("\n")
    header = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if l.strip() and not l.startswith("#")
           and l.split("|")[0].strip() != str(phase)]
    rows = _discussion_cumulative_rows(disc_dir, phase)
    body += rows
    PE.write_text(di, "\n".join(header + body) + "\n")
    return len(rows)


def cmd_assemble(a, root, conf):
    # Adopt any pending research reports first
    import research_add as _ra
    cur = cur_dir(root, conf)
    adopted = _ra.adopt_pending(cur)
    if adopted:
        sys.stdout.write("adopted: %d pending report(s)\n" % len(adopted))
    bits = plan_bits(root, conf, phase=a.phase)
    phase = a.phase or bits["phase"]
    summaries = load_summaries(root, conf, phase=phase)
    by_id = dict((t.id, t) for t in bits["tasks"])
    done = [t for t in bits["tasks"] if t.status == "done"]
    sup = [t for t in bits["tasks"] if t.status == "superseded"]
    target = phaseend_path(root, conf, phase)
    prev = existing_authored(target)
    recap, binding = recap_parts(root, conf, phase=phase)
    recap = recap or prev["recap"] or "-"          # 3.25 T4: never a placeholder
    binding = binding or prev["binding"] or "-"

    out = ["# PhaseEnd — Phase %s: %s%s" % (
        phase, bits["name"], (" (implements Gen %s)" % bits["gen"]) if bits["gen"] else "")]
    out.append("Approved: %s | Closed: %s | Planner: %s | Tasks: %d done, %d superseded"
               % (bits["approved"], datetime.date.today().isoformat(),
                  bits["planner"], len(done), len(sup)))
    out += ["", "## Milestone", bits["milestone"] or "Milestone: -",
            "Confirmed by the developer at the close."]

    out += ["", "## Tasks"]
    if not summaries:
        out.append("- (no task summaries in current/tasks/)")
    for s in summaries:
        did = " ".join(s["sec"].get("Done", [])).strip() or "-"
        link = "tasks/%s.md" % s["id"]
        log = "logs/%s.md" % s["id"]
        out.append("- %s — %s | Done: %s | commit: %s | %s | %s"
                   % (s["id"], s["title"] or "-", did, s["commit"], link, log))

    out += ["", "## Decisions that still bind"]
    nbind = 0
    for s in summaries:
        for line in s["sec"].get("Decisions", []):
            bare = line.lstrip("-* ").strip()                 # the template writes bullets
            if bare.lower().startswith("binding:"):
                out.append("- %s — %s" % (s["id"], bare[len("binding:"):].strip()))
                nbind += 1
    out.append(binding)

    out += ["", "## Rules proposed"]
    rules = []
    for s in summaries:
        for line in s["sec"].get("Rule candidate", []):
            rules.append("- %s — %s" % (s["id"], line))
    out += rules or ["- (none)"]

    out += ["", "## Cookbook entries added"]
    cook = cookbook_for_phase(root, phase)
    out += cook or ["- (none)"]

    out += ["", "## Research"]
    res_dir = _phase_base(root, conf, phase) if phase else cur_dir(root, conf)
    res = index_lines(os.path.join(res_dir, "research", "INDEX.md"))
    out += res or ["- (none)"]

    out += ["", "## Audit"]
    aud = audit_lines(root, phase)
    out += aud

    out += ["", "## Agent runs"]
    runs = agent_run_lines(root, phase, bits["approved"])
    out += runs

    # -- Discussions section ------------------------------------------------ #
    out += ["", "## Discussions"]
    disc_dir = os.path.join(
        _phase_base(root, conf, phase) if phase else cur_dir(root, conf),
        "discussions")
    disc_lines = _discussion_summary_lines(disc_dir)
    out += disc_lines or ["- (none)"]
    _update_cumulative_discussion_index(root, conf, phase, disc_dir)

    out += ["", "## Deferred"]
    deferred = []
    for t in bits["tasks"]:
        if t.status in ("superseded", "blocked"):
            deferred.append("- %s | %s — %s"
                            % (t.id, t.status, t.get("title") or t.get("done-when", "")))
    last_done = None                        # the last done plan task
    for s in summaries:
        if s["status"].startswith("done") and by_id.get(s["id"]) is not None:
            last_done = s["id"]
    for s in summaries:
        needs = " ".join(s["sec"].get("Next task needs", [])).strip()
        if needs and s["id"] == last_done:
            deferred.append("- from %s: %s (unconsumed)" % (s["id"], needs))
    # deferred items from discussion records
    deferred += _discussion_deferred_lines(disc_dir)
    # routed inbox bullets (status.py inbox-consume): I<n> rows with status deferred:<P>
    for line in index_lines(os.path.join(disc_dir, "INDEX.md")):
        parts = [c.strip() for c in line.split("|")]
        if len(parts) >= 5 and parts[0].startswith("I") and parts[3].startswith("deferred:"):
            deferred.append("- %s (%s): %s" % (parts[0], parts[3], parts[1]))
    out += deferred or ["- (none)"]

    out += ["", "## Changes"]
    out += bits["changes"] or ["- (none)"]

    out += ["", "## Plain-English Recap", recap, ""]
    PE.write_text(target, "\n".join(out))
    PE._cnote(target, script="phaseend_index.py", sub="assemble", whole=True, root=root)
    sys.stdout.write("%s\n" % PE.rel(root, target))
    sys.stdout.write("tasks: %d done, %d superseded | binding: %d | cookbook: %d | "
                     "research: %d | runs: %d\n"
                     % (len(done), len(sup), nbind, len(cook), len(res),
                        len([r for r in runs if not r.startswith("- (")])))
    return 0


def _git(root, *args):
    try:
        return subprocess.run(("git",) + args, cwd=root, capture_output=True, text=True)
    except Exception:
        return None


def fresh_current(root, conf):
    cur = cur_dir(root, conf)
    for sub in ("tasks", "logs", "research", "discussions"):
        os.makedirs(os.path.join(cur, sub), exist_ok=True)
    PE.write_text(os.path.join(cur, "tasks", "INDEX.md"),
                  "# Task summaries -- this phase\n"
                  "# id | status | title | tags | summary | log | research\n")
    PE.write_text(os.path.join(cur, "research", "INDEX.md"),
                  "# Research reports -- this phase\n"
                  "# id | task | title | tags | agent | date | lines\n")
    PE.write_text(os.path.join(cur, "discussions", "INDEX.md"),
                  "# Discussions -- one line per record\n"
                  "# id | topic | date | status | path\n")
    tmpl = os.path.join(root, "templates", "INBOX.template.md")    # status.py cmd_inbox_consume
    if os.path.isfile(tmpl):
        PE.write_text(os.path.join(cur, "INBOX.md"), PE.read_text(tmpl))


def _inbox_pending_count(root, conf):
    """Real (non-placeholder) bullets in current/INBOX.md; 0 when the file is absent (fix-2)."""
    p = os.path.join(cur_dir(root, conf), "INBOX.md")
    return len(ST.inbox_pending(PE.read_text(p))) if os.path.isfile(p) else 0


def _export_ledger_slice(root, conf, phase, out, cli=None):
    """Run the installed ledger CLI's export before archiving.

    A missing CLI or a failed export is a printed note, never a failure. Returns the slice path
    (None when nothing was exported); a slice under ``root`` is staged and named in a ``stage:``
    line so the closer's commit carries it (3.10 T1).
    """
    slice_path = None
    py = "python"
    try:
        import json as _json
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            py = _json.load(fh).get("python") or "python"
    except Exception:
        pass
    pa3_dir = os.path.join(os.path.expanduser("~"), ".claude", "pa3")
    cli = cli or os.path.join(pa3_dir, "pa_ledger.py")
    if not os.path.isfile(cli):
        out.append("note: ledger CLI not found at %s, skipping export" % cli)
        return None
    slice_dir = os.path.join(root, ".claude-state", "ledger")
    try:
        res = subprocess.run(
            [py, cli, "export", "--project", root, "--phase", str(phase)],
            capture_output=True, text=True, timeout=120)
        if res.returncode != 0 and "no sessions" in (res.stderr or "") + (res.stdout or ""):
            # a by-hand phase stamps no session with its id: keep the whole project's slice
            out.append("note: no session carries phase %s; exporting the whole project" % phase)
            res = subprocess.run(
                [py, cli, "export", "--project", root],
                capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            # find the slice path from the output
            for line in res.stdout.splitlines():
                if line.startswith("exported:"):
                    slice_path = line.split(":", 1)[1].strip()
                    out.append("ledger slice: %s" % slice_path)
                    break
            else:
                out.append("ledger slice: exported to %s" % slice_dir)
        else:
            detail = (res.stderr or res.stdout or "").strip()[:200]
            out.append("note: ledger export failed (rc %d): %s" % (res.returncode, detail))
    except FileNotFoundError:
        out.append("note: interpreter %s not found, skipping ledger export" % py)
    except Exception as exc:
        out.append("note: ledger export error: %s" % exc)
    if slice_path:
        full = slice_path if os.path.isabs(slice_path) else os.path.join(root, slice_path)
        try:
            rel = os.path.relpath(os.path.abspath(full), os.path.abspath(root))
        except ValueError:                                  # another drive: not under root
            rel = ".."
        if os.path.isfile(full) and not rel.startswith(".."):
            rel = rel.replace(os.sep, "/")
            res = _git(root, "add", "--", rel)
            if res is not None and res.returncode == 0:
                out.append("stage: %s" % rel)
            else:
                detail = ((res.stderr or res.stdout or "") if res is not None else "git missing")
                out.append("note: git add %s failed: %s" % (rel, detail.strip()[:200]))
    return slice_path


def _ops_reindex(root, out):
    """Refresh docs/ops/INDEX.md line counts via curate.py ops --reindex (fix-16).

    Absent index: nothing. A failure is a ``warn:`` line, never an archive failure. A changed
    index is staged and named in a ``stage:`` line so the closer's commit carries it.
    """
    idx = os.path.join(root, "docs", "ops", "INDEX.md")
    if not os.path.isfile(idx):
        return
    import contextlib
    import io
    buf = io.StringIO()
    try:
        import curate
        before = PE.read_text(idx)
        with contextlib.redirect_stdout(buf):
            curate._ops_reindex(argparse.Namespace(reindex=True, dry_run=False, sunset=False,
                                                   gen=None), root)
    except (Exception, SystemExit) as exc:
        detail = (buf.getvalue().strip() or str(exc)).replace("\n", "; ")[:200]
        out.append("warn: ops reindex failed: %s" % detail)
        return
    if PE.read_text(idx) == before:
        out.append("ops index: no change")
        return
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    out.append("ops index: %s" % "; ".join(lines))
    res = _git(root, "add", "--", "docs/ops/INDEX.md")
    if res is not None and res.returncode == 0:
        out.append("stage: docs/ops/INDEX.md")
    else:
        detail = ((res.stderr or res.stdout or "") if res is not None else "git missing")
        out.append("note: git add docs/ops/INDEX.md failed: %s" % detail.strip()[:200])


def cmd_archive(a, root, conf):
    pdir = PE.phase_dir(root, conf)
    src = os.path.join(pdir, "current")
    if not os.path.isfile(os.path.join(src, "PHASE_PLAN.md")):
        die("nothing to archive: no %s/PHASE_PLAN.md" % PE.rel(root, src))
    bits = plan_bits(root, conf)
    phase = a.phase or bits["phase"]
    dst = os.path.join(pdir, "phase-%s" % phase)
    if os.path.exists(dst):
        die("%s already exists" % PE.rel(root, dst))
    pe_file = phaseend_path(root, conf, phase)
    if not os.path.isfile(pe_file):
        die("assemble %s first (no %s)" % (phase, PE.rel(root, pe_file)))
    n = _inbox_pending_count(root, conf)
    if n:
        die("current/INBOX.md holds %d unconsumed bullet(s): run `PY tools/status.py inbox-consume`, "
            "then inbox-mark the routed rows, re-assemble, then archive" % n)
    out = []
    _ops_reindex(root, out)
    # card check: refuse when card.py check exits 1
    _card_check(root)
    # export the ledger slice for this phase before archiving
    _export_ledger_slice(root, conf, phase, out)
    res = _git(root, "mv", PE.rel(root, src), PE.rel(root, dst))
    if res is None or res.returncode != 0:
        os.rename(src, dst)
        out.append("warn: git mv failed, used a plain rename")
    out.append("archived: %s -> %s" % (PE.rel(root, src), PE.rel(root, dst)))
    fresh_current(root, conf)
    out.append("fresh: %s/current/{tasks,logs,research,discussions}" % PE.rel(root, pdir))
    # fix-18: the archived phase's owner stamp goes; the mod restamps it at its next session
    if os.path.isfile(ST.status_path(root)):
        sdata = ST.load(root)
        sdata["router_session"] = None
        ST.save(root, sdata)
        out.append("owner: cleared (router_session)")

    ti = os.path.join(pdir, "TASK_INDEX.md")
    if not os.path.isfile(ti):
        PE.write_text(ti, TASK_HEADER)
    rows = []
    for line in index_lines(os.path.join(dst, "tasks", "INDEX.md")):
        line = line.replace("tasks/", "phase-%s/tasks/" % phase)
        line = line.replace("logs/", "phase-%s/logs/" % phase)
        rows.append("%s | %s" % (phase, line))
    if rows:
        with open(ti, "a", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(rows) + "\n")
    out.append("TASK_INDEX.md += %d line(s)" % len(rows))

    ri = os.path.join(pdir, "RESEARCH_INDEX.md")
    if not os.path.isfile(ri):
        PE.write_text(ri, RES_HEADER)
    rrows = []
    for line in index_lines(os.path.join(dst, "research", "INDEX.md")):
        rid = line.split("|")[0].strip()
        rrows.append("%s | %s | phase-%s/research/%s.md" % (phase, line, phase, rid))
    if rrows:
        with open(ri, "a", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(rrows) + "\n")
    out.append("RESEARCH_INDEX.md += %d line(s)" % len(rrows))

    ndrows = _update_cumulative_discussion_index(
        root, conf, phase, os.path.join(dst, "discussions"))
    out.append("DISCUSSION_INDEX.md += %d line(s)" % ndrows)

    gen = bits["gen"]
    if gen:
        py = "python"
        try:
            import json as _json
            with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
                py = _json.load(fh).get("python") or "python"
        except Exception:
            pass
        out.append("RUN: %s tools/plan_edit.py gen-close %s" % (py, gen))   # the mod runs it (--by defaults to router)
        others = _other_open_phases(root, gen)
        if not others:
            # a RUN: line, never hidden by the info cap below (3.11 T11: was an info line,
            # "generation <G> has no other open phase: …", cut as "... (N more)")
            out.append("RUN: %s tools/genend_index.py assemble %s" % (py, gen.split(".")[0]))
    # separate RUN: and stage: lines from informational lines; cap info at 5
    run_lines = [l for l in out if l.startswith("RUN:")]
    stage_lines = [l for l in out if l.startswith("stage:")]
    main = [l for l in out
            if not l.startswith(("RUN:", "stage:", "note:", "warn:"))]
    notes = [l for l in out if l.startswith("note:") or l.startswith("warn:")]
    info = main + notes
    if len(info) > 5:
        info = info[:4] + ["... (%d more)" % (len(info) - 4)]
    sys.stdout.write("\n".join(info + stage_lines + run_lines) + "\n")
    return 0


def _other_open_phases(root, gen):
    """Not-closed phases of `gen`'s generation besides `gen`: only `## Phases` phase lines
    (_genplan grammar) that carry `status:` (I30: a numbered rationale bullet is no phase)."""
    path = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(path):
        return []
    section, inside = [], False
    for raw in PE.read_text(path).split("\n"):
        b = PE.split_eol(raw)[0]
        if b.startswith("## "):
            inside = b[3:].strip().lower().startswith("phases")
            continue
        if inside:
            section.append(b)
    out = []
    for line in PE._genplan.phase_lines("\n".join(section)):
        if not re.search(r"\|\s*status:", line):
            continue
        f = PE._genplan.fields(line)
        if f["id"] == gen or f["id"].split(".")[0] != gen.split(".")[0]:
            continue
        if f["status"] != "closed":
            out.append(f["id"])
    return out


def cmd_legacy(a, root, conf):
    pdir = PE.phase_dir(root, conf)
    rows = []
    if not os.path.isdir(pdir):
        die("no %s directory" % PE.rel(root, pdir))
    for name in os.listdir(pdir):
        if not (name.startswith("PhaseEnd_") and name.endswith(".md")):
            continue
        label = name[len("PhaseEnd_"):-3]
        label = re.sub(r"^Phase[_\-]?", "", label)
        label = re.sub(r"[_\-]+", ".", label).strip(".")
        path = os.path.join(pdir, name)
        title = milestone = date = "-"
        for raw in PE.read_text(path).split("\n")[:40]:
            b = PE.split_eol(raw)[0].strip()
            if title == "-" and b.startswith("#"):
                title = b.lstrip("#").strip()
            elif milestone == "-" and b.lower().startswith("milestone"):
                milestone = b.split(":", 1)[-1].strip()[:90]
            elif date == "-":
                dm = re.match(r"(?i)^(?:closed|date)\s*:\s*(.+)$", b)
                if dm:
                    date = dm.group(1).strip()[:30]
                else:
                    dm = re.search(r"\b(20\d\d-\d\d-\d\d)\b", b)
                    if dm:
                        date = dm.group(1)
        rows.append((label, "%s | %s | %s | %s | %s"
                     % (label, title, milestone, date,
                        PE.rel(root, path))))
    rows.sort(key=lambda r: natural_key(r[0]))
    target = os.path.join(pdir, "LEGACY_INDEX.md")
    PE.write_text(target, LEGACY_HEADER + "\n".join(r[1] for r in rows) + "\n")
    PE.emit(["%s (%d entries)" % (PE.rel(root, target), len(rows))]
            + [r[1] for r in rows])
    return 0


_SCRATCH_STATUSES = ("done", "next", "queued", "blocked", "superseded")


def cleanup_done(plan_text):
    """True when the plan has a phase-cleanup task (`title: phase cleanup`) whose status is done (3.29 T5)."""
    for line in plan_text.split("\n"):
        if not PE.TASK_RE.match(line):
            continue
        parts = [x.strip() for x in line.split("|")]
        title = next((x[len("title:"):].strip() for x in parts if x.lower().startswith("title:")), "")
        if not title.lower().startswith("phase cleanup"):
            continue
        status = parts[1].lower() if len(parts) > 1 and parts[1].lower() in _SCRATCH_STATUSES else "queued"
        if status == "done":
            return True
    return False


def cmd_scratch(a, root, conf):
    """The close's last cleanup (3.29 T5): delete .run/<phase>/, the phase's temp folder, once the phase's plan
    (archived, else current) has its phase-cleanup task done; else keep it. Never fails the close: always exit 0."""
    phase = (a.phase or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", phase) or phase in (".", ".."):
        PE.emit(["scratch: no phase id; nothing deleted"])
        return 0
    folder = os.path.join(root, ".run", phase)
    if not os.path.isdir(folder):
        PE.emit(["scratch: no .run/%s/" % phase])
        return 0
    text = ""
    for parts in (("phase-ends", "phase-%s" % phase, "PHASE_PLAN.md"), ("phase-ends", "current", "PHASE_PLAN.md")):
        path = os.path.join(root, *parts)
        if os.path.isfile(path):
            text = PE.read_text(path)
            break
    if not cleanup_done(text):
        PE.emit(["scratch: .run/%s/ kept: the plan has no phase-cleanup task done" % phase])
        return 0
    n = sum(len(files) for _, _, files in os.walk(folder))
    shutil.rmtree(folder, ignore_errors=True)
    left = " (some files were in use and stay)" if os.path.isdir(folder) else ""
    PE.emit(["scratch: .run/%s/ deleted, %d files%s" % (phase, n, left)])
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--archive":
        argv[0] = "archive"
    p = argparse.ArgumentParser(
        prog="phaseend_index.py",
        description="Assemble / archive PhaseEnd files from phase-ends/current/.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("assemble", help="write phase-ends/PhaseEnd_Phase<N>.md")
    s.add_argument("phase", nargs="?")
    s = sub.add_parser("archive", aliases=["--archive"],
                       help="git mv current/ -> phase-<N>/ and extend the indexes")
    s.add_argument("phase", nargs="?")
    sub.add_parser("legacy-index", help="index pre-PA3 PhaseEnd files")
    s = sub.add_parser("scratch", help="delete .run/<N>/ once the plan's phase-cleanup task is done")
    s.add_argument("phase", nargs="?")
    a = p.parse_args(argv)
    root = PE.find_root()
    conf = PE.cfg(root)
    return {"assemble": cmd_assemble, "archive": cmd_archive, "--archive": cmd_archive,
            "legacy-index": cmd_legacy, "scratch": cmd_scratch}[a.cmd](a, root, conf)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
