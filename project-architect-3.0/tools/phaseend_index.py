#!/usr/bin/env python
"""phaseend_index.py -- assemble, archive, lint and legacy-index PhaseEnd files.

    phaseend_index.py assemble <N>     build phase-ends/PhaseEnd_Phase<N>.md
    phaseend_index.py lint [<N>]       tasks done|superseded, MILESTONE green, no placeholder left
    phaseend_index.py --archive [<N>]  git mv current/ -> phase-<N>/, fresh current/ (refuses while a placeholder is left)
    phaseend_index.py legacy-index     one line per pre-PA3 PhaseEnd
    phaseend_index.py verify [<N>]     run every `verified by:` clause of the Milestone line; GREEN/RED each

The PhaseEnd's `## Agent runs` section is generated from the usage ledger (agent_runs rows
stamped with this phase, else this project's runs since approval), capped at 40 lines.

`assemble` is idempotent: it leaves {{AUTHORED:recap}} / {{AUTHORED:binding}}
placeholders, a second run fills them from current/RECAP.md, later runs keep the filled
blocks. Order at a phase end: assemble, write RECAP.md, assemble, lint, commit, archive.
archive moves RECAP.md with current/; genend_index.py reads the Generation Recap from
phase-<N>/RECAP.md after that.
Stdlib only; no imports from `pa/` (it may import plan_edit.py, its neighbour).
"""

import argparse
import datetime
import os
import re
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
RECAP_PH = "{{AUTHORED:recap}}"
BIND_PH = "{{AUTHORED:binding}}"
PLACEHOLDERS = (RECAP_PH, BIND_PH)
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


def placeholders_left(path):
    """The {{AUTHORED:...}} placeholders still in an assembled PhaseEnd ([] when none or no file)."""
    if not os.path.isfile(path):
        return []
    text = PE.read_text(path)
    return [ph for ph in PLACEHOLDERS if ph in text]


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


def closer(summaries):
    """The summary titled 'phase close', else PHASE-END id, else last Verified:."""
    for s in summaries:
        if re.search(r"phase\s+close", s["title"], re.I):
            return s
    for s in summaries:
        if not re.match(r"^T[0-9]", s["id"]):
            return s
    for s in reversed(summaries):
        if s["sec"].get("Verified"):
            return s
    return None


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
    picked = [r for r in rows if str(r["phase"] or "") == str(phase)]
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
                sorted_phases = sorted(by_phase.keys())
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
        body = [x for x in m.group(1).split("\n")
                if x.strip() and not x.strip().startswith("- T")]
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
    recap = recap or prev["recap"] or RECAP_PH
    binding = binding or prev["binding"] or BIND_PH

    cl = closer(summaries)
    verified = "-"
    if cl and cl["sec"].get("Verified"):
        verified = " ".join(cl["sec"]["Verified"]).strip()

    out = ["# PhaseEnd — Phase %s: %s%s" % (
        phase, bits["name"], (" (implements Gen %s)" % bits["gen"]) if bits["gen"] else "")]
    out.append("Approved: %s | Closed: %s | Planner: %s | Tasks: %d done, %d superseded"
               % (bits["approved"], datetime.date.today().isoformat(),
                  bits["planner"], len(done), len(sup)))
    out += ["", "## Milestone", bits["milestone"] or "Milestone: -",
            "Verified: %s" % verified]

    out += ["", "## Tasks"]
    if not summaries:
        out.append("- (no task summaries in current/tasks/)")
    for s in summaries:
        if cl is not None and s["path"] == cl["path"]:
            continue                       # the closer's line lives under Milestone
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
    last_done = None                        # the last done plan task, not the closer
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
    left = [p for p in (RECAP_PH, BIND_PH) if p in "\n".join(out)]
    if left:
        sys.stdout.write("placeholders left: %s (write current/RECAP.md, re-run)\n"
                         % " ".join(left))
    return 0


def cmd_lint_file(path):
    """Lint a single PhaseEnd file: no placeholder markers, required sections present."""
    if not os.path.isfile(path):
        sys.stdout.write("no such file: %s\n" % path)
        return 1
    text = PE.read_text(path)
    problems = []
    # {{...}} placeholder markers
    for m in re.finditer(r"\{\{[^}]*\}\}", text):
        problems.append("placeholder marker: %s" % m.group(0))
    # template <...> markers (lowercase words, 5+ chars, not URLs)
    for m in re.finditer(r"<([a-z][a-z ]{4,}[^>]*)>", text):
        inner = m.group(1)
        if not inner.startswith(("http", "ftp", "mailto")):
            problems.append("template marker: <%s>" % inner[:60])
    # literal PLACEHOLDER
    if "PLACEHOLDER" in text:
        problems.append("contains PLACEHOLDER")
    # required sections
    if "## Milestone" not in text:
        problems.append("missing ## Milestone")
    if "## Plain-English Recap" not in text:
        problems.append("missing ## Plain-English Recap")
    if problems:
        for p in problems:
            sys.stdout.write("%s\n" % p)
        return 1
    sys.stdout.write("OK\n")
    return 0


def cmd_lint(a, root, conf):
    if getattr(a, "lint_file", None):
        return cmd_lint_file(a.lint_file)
    bits = plan_bits(root, conf, phase=a.phase)
    phase = a.phase or bits["phase"]
    problems = []
    for t in bits["tasks"]:
        if t.status not in ("done", "superseded"):
            problems.append("%s is %s (must be done or superseded)" % (t.id, t.status))
    summaries = load_summaries(root, conf, phase=phase)
    # the verdict counts only where a closer states it: a Verified: line, or a line of RECAP.md
    # that starts with the verdict (a mention elsewhere, such as a gotcha, is not a verdict)
    verdicts = [" ".join(s["sec"].get("Verified", [])) for s in summaries]
    lint_base = _phase_base(root, conf, phase) if phase else cur_dir(root, conf)
    rp = os.path.join(lint_base, "RECAP.md")
    if os.path.isfile(rp):
        verdicts += [x.strip() for x in PE.read_text(rp).split("\n")
                     if x.strip().upper().startswith(("MILESTONE:", "VERIFIED:"))]
    if not any(re.match(r"^\s*(?:Verified:\s*)?MILESTONE:\s*green", v, re.I) for v in verdicts):
        problems.append("no 'MILESTONE: green' verdict in the closer's Verified: line or RECAP.md")
    pe_file = phaseend_path(root, conf, phase)
    if not os.path.isfile(pe_file):
        problems.append("no %s yet (run assemble %s first)" % (PE.rel(root, pe_file), phase))
    for ph in placeholders_left(pe_file):
        problems.append("%s still has %s (write current/RECAP.md, re-run assemble)"
                        % (PE.rel(root, pe_file), ph))
    # 3.10 T1: an untracked ledger slice would be left out of the archive commit
    res = _git(root, "ls-files", "--others", "--exclude-standard", "--", ".claude-state/ledger/")
    if res is not None and res.returncode == 0:
        for p in [x.strip() for x in res.stdout.splitlines() if x.strip()]:
            problems.append("untracked ledger slice %s (git add it and commit)" % p)
    # 3.10 T14: a changed agent body bumps <phase>.<N+1>; N counts for the agent's lifetime
    problems += agent_version_problems(root, phase)
    # 3.10 T15: a phase that changed the package writes its CHANGES.md section
    problems += changes_problems(root, phase)
    # T29: an inbox row left `now` must be named by a task or a Changes line, or be marked
    for rid, topic in ST.unresolved_rows(os.path.join(lint_base, "discussions", "INDEX.md"),
                                         os.path.join(lint_base, "PHASE_PLAN.md")):
        problems.append("inbox row %s still 'now' with no task or Changes line naming it (%s)"
                        % (rid, topic[:50]))
    # fix-2: an unconsumed inbox bullet would be archived unrouted (current phase only)
    if os.path.normcase(os.path.abspath(lint_base)) == \
            os.path.normcase(os.path.abspath(cur_dir(root, conf))):
        n = _inbox_pending_count(root, conf)
        if n:
            problems.append("current/INBOX.md holds %d unconsumed bullet(s) "
                            "(run status.py inbox-consume, inbox-mark the rows)" % n)
    if problems:
        if len(problems) <= 4:
            out = ["ERROR " + p for p in problems]
        else:
            out = ["ERROR " + p for p in problems[:3]]
            out.append("... and %d more problem(s)" % (len(problems) - 3))
        out.append("FAILED: %d problem(s)" % len(problems))
        sys.stdout.write("\n".join(out) + "\n")
        return 1
    sys.stdout.write("OK\n")
    return 0


def _split_outside_backticks(text):
    """Split at ``;`` outside backtick spans: a command's own ``;`` (```PY -c "a; b"```) stays in
    its clause (3.11 T11: was every ``;``, which cut a planner's ``python -c`` check into four RED
    pieces)."""
    out, cur, inside = [], [], False
    for ch in text:
        if ch == "`":
            inside = not inside
        elif ch == ";" and not inside:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return [c.strip() for c in out if c.strip()]


def milestone_clauses(milestone):
    """``(gate, clauses)`` from the plan's Milestone line: the clauses follow ``verified by:``,
    separated by ``;`` outside backticks."""
    text = re.sub(r"^Milestone:\s*", "", str(milestone or "")).strip()
    m = re.search(r"verified by:\s*(.*)$", text, re.I | re.S)
    if not m:
        return text, []
    gate = text[:m.start()].rstrip(" \u2014-\u2013")
    return gate, _split_outside_backticks(m.group(1))


_CMD_HEAD = re.compile(r"^(?:PY|python3?|py|bash|sh|grep|test|git|ls|diff|cat|pytest|make|npm|node|cargo"
                       r"|dotnet|wsl(?:\.exe)?|powershell|find|wc|sort|head|tail|\./|[A-Za-z]:/|/|~/)", re.I)
_EXPECT_RE = re.compile(r"(?:with|prints?|shows?|contains?|outputs?|→|->)\s+(?:[a-z]+\s+)?`([^`]+)`", re.I)


def _fill(cmd, py):
    cmd = re.sub(r"(?<![\w/.-])PY(?![\w])", py.replace(chr(92), "/"), cmd)
    home = os.path.expanduser("~").replace(chr(92), "/") + "/"
    # A `~/` inside single quotes belongs to another shell (a WSL command in a clause): leave it (3.6 T7).
    parts = cmd.split("'")
    for i in range(0, len(parts), 2):                       # even indexes are outside single quotes
        parts[i] = parts[i].replace("~/", home)
    return "'".join(parts)


def clause_command(clause, py):
    """``(command, expects)`` for one clause of a prose milestone: the first backticked fragment
    that looks like a command runs (``PY`` = the project interpreter, ``~/`` = home); fragments
    introduced by "with", "prints", "shows", "contains" or an arrow are strings the output must
    contain (``exits 0 with `Ran 32 tests```); other fragments are prose and ignored."""
    parts = re.findall(r"`([^`]+)`", clause)
    if not parts:
        return _fill(clause, py), []
    cmd_i = next((i for i, p in enumerate(parts) if _CMD_HEAD.match(p.strip())), 0)
    cmd = _fill(parts[cmd_i], py)
    expects = [e for e in _EXPECT_RE.findall(clause) if e != parts[cmd_i]]
    return cmd, expects


def cmd_verify(a, root, conf):
    """3.1 T17: run each verified-by clause through run.sh; GREEN/RED per clause and overall."""
    bits = plan_bits(root, conf)
    phase = a.phase or bits["phase"]
    gate, clauses = milestone_clauses(bits["milestone"])
    if not clauses:
        die("the Milestone line has no 'verified by:' clauses")
    py = conf.get("python") or "python"
    runner = os.path.join(root, "tools", "run.sh")
    if not os.path.isfile(runner):
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.sh")
    all_lines, red_lines, green = [], [], 0
    for i, clause in enumerate(clauses, 1):
        name = "verify%d" % i
        cmd, expects = clause_command(clause, py)
        try:
            res = subprocess.run(["bash", runner, name, "--tail", "6", "--", "bash", "-c", cmd],
                                 cwd=root, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace")
            code, text = res.returncode, (res.stdout or "") + (res.stderr or "")
        except Exception as exc:
            code, text = 1, str(exc)
        log_text = ""
        try:  # 3.10: a non-UTF-8 byte in a clause log must never abort verify
            with open(os.path.join(root, ".run", "logs", name + ".log"),
                      encoding="utf-8", errors="replace") as fh:
                log_text = fh.read()
        except OSError:
            pass
        missing = [e for e in expects if e not in log_text]
        # 3.8 T8: a clause that names an output (`prints`/`shows`/…) is judged on the output; the exit
        # code counts only when the clause says `exits 0` or names no output (`grep -c` exits 1 on 0)
        needs_exit0 = not expects or bool(re.search(r"\bexits?\s+0\b", clause, re.I))
        ok = (code == 0 or not needs_exit0) and not missing
        green += 1 if ok else 0
        clause_hdr = "%s %d: %s (.run/logs/%s.log)" % ("GREEN" if ok else "RED", i,
                                                        clause[:110], name)
        all_lines.append(clause_hdr)
        if not ok:
            detail = []
            for e in missing:
                detail.append("      expected in the output: `%s`" % e[:100])
            tail = [x for x in text.rstrip().split("\n") if x.strip()][-3:]
            detail += ["      " + x[:150] for x in tail]
            all_lines += detail
            red_lines.append(clause_hdr)
            red_lines += detail
    summary = "VERIFY: %s (%d/%d)" % ("GREEN" if green == len(clauses) else "RED",
                                       green, len(clauses))
    verbose = getattr(a, "verbose", False)
    if verbose:
        PE.emit(all_lines + [summary])
    else:
        PE.emit(red_lines + [summary])
    return 0 if green == len(clauses) else 1


def _git(root, *args):
    try:
        return subprocess.run(("git",) + args, cwd=root, capture_output=True, text=True)
    except Exception:
        return None


AGENTS_REL = "project-architect-3.0/agents/"
_VERSION_LINE = re.compile(r"^version:[ \t]*(.*?)[ \t]*$", re.M)


def _split_version(text):
    """(version, body): the frontmatter ``version:`` value and the text without that line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    m = _VERSION_LINE.search(text)
    if not m:
        return "", text
    end = m.end() + 1 if text[m.end():m.end() + 1] == "\n" else m.end()
    return m.group(1), text[:m.start()] + text[end:]


def agent_version_problem(name, old_text, new_text, phase):
    """The version rule for one agent file change (None when fine). ``None`` text = added/deleted.
    User marks (``+uK``, ``user/``) are never judged. Body unchanged: version unchanged, or the
    legacy migration ``N`` -> ``<phase>.N``. Body changed: ``<phase>.<oldN+1>`` (oldN = last
    integer of old; N never resets), or legacy ``N`` -> ``N+1``."""
    if old_text is None or new_text is None:
        return None
    old, old_body = _split_version(old_text)
    new, new_body = _split_version(new_text)
    if any("+u" in v or v.startswith("user/") for v in (old, new)):
        return None
    legacy = re.fullmatch(r"\d+", old) is not None
    if old_body == new_body:
        if new == old or (legacy and new == "%s.%s" % (phase, old)):
            return None
        return "agent %s: version %s -> %s without a body change (expected %s)" % (
            name, old or "(none)", new or "(none)", old or "(none)")
    nums = re.findall(r"\d+", old)
    old_n = int(nums[-1]) if nums else 0
    expected = "%s.%d" % (phase, old_n + 1)
    if new == expected or (legacy and new == str(old_n + 1)):
        return None
    return "agent %s: body changed, version %s -> %s (expected %s)" % (
        name, old or "(none)", new or "(none)", expected)


def _git_utf8(root, *args):
    """stdout of a git command decoded as UTF-8, or None on any failure."""
    try:
        res = subprocess.run(("git",) + args, cwd=root, capture_output=True)
    except Exception:
        return None
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", errors="replace")


def _approval_commit(root):
    """The commit that approved the live plan (last one adding/removing ``Approved:``), or ""."""
    plan_rel = "phase-ends/current/PHASE_PLAN.md"
    return (_git_utf8(root, "log", "-1", "-SApproved:", "--format=%H", "--", plan_rel) or "").strip()


PACKAGE_REL = "project-architect-3.0/"


def changes_problems(root, phase):
    """A package path other than CHANGES.md changed since plan approval (commits and working tree)
    and ``CHANGES.md`` has no ``## <phase>`` line. [] without package dir, git or approval."""
    if not phase or not os.path.isdir(os.path.join(root, PACKAGE_REL)):
        return []
    approval = _approval_commit(root)
    if not approval:
        return []
    names = _git_utf8(root, "diff", "--name-only", approval, "--", PACKAGE_REL) or ""
    changes_rel = PACKAGE_REL + "CHANGES.md"
    if not [n for n in names.splitlines() if n.strip() and n.strip() != changes_rel]:
        return []
    try:
        with open(os.path.join(root, changes_rel), encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\r\n", "\n")
    except OSError:
        text = ""
    if re.search(r"^## %s(?: |$)" % re.escape(phase), text, re.M):
        return []
    return ["package changed in phase %s; %s has no '## %s' section" % (phase, changes_rel, phase)]


_TASK_SUBJECT = re.compile(r"^(T\d+(?:\.\d+)?)(?:\.c\d+)?:")


def task_runs(entries):
    """``[(first_sha, last_sha)]`` from ``[(sha, subject)]`` oldest first: consecutive commits of one
    task (subject ``T2:``, ``T2.c1:``, ``T4.1.c2:``) form one run, judged as one change, so an agent
    needs one version bump per task (developer 2026-09-29); any other commit is a run of its own."""
    runs, key = [], None
    for sha, subject in entries:
        m = _TASK_SUBJECT.match(subject)
        k = m.group(1) if m else None
        if k is not None and k == key:
            runs[-1] = (runs[-1][0], sha)
        else:
            runs.append((sha, sha))
        key = k
    return runs


def agent_version_problems(root, phase):
    """Version-rule problems for every agent file change since plan approval: each first-parent
    commit (``<c>^`` vs ``<c>``), then the working tree vs HEAD. [] without agents dir, git or approval."""
    if not phase or not os.path.isdir(os.path.join(root, AGENTS_REL)):
        return []
    approval = _approval_commit(root)
    if not approval:
        return []
    log = _git_utf8(root, "log", "--first-parent", "--reverse", "--format=%H %s",
                    "%s..HEAD" % approval, "--", AGENTS_REL) or ""
    entries = [tuple((ln.split(" ", 1) + [""])[:2]) for ln in log.splitlines() if ln.strip()]
    pairs = [(last, first + "^", last) for first, last in task_runs(entries)]
    pairs.append(("worktree", "HEAD", None))
    problems = []
    for where, old_ref, new_ref in pairs:
        diff_args = ("diff", "--name-status", "--no-renames", old_ref) + ((new_ref,) if new_ref else ())
        names = _git_utf8(root, *(diff_args + ("--", AGENTS_REL)))
        for line in (names or "").splitlines():
            parts = line.split("\t")
            if len(parts) != 2 or parts[0] != "M" or not parts[1].endswith(".md"):
                continue
            path = parts[1]
            old_text = _git_utf8(root, "show", "%s:%s" % (old_ref, path))
            if new_ref:
                new_text = _git_utf8(root, "show", "%s:%s" % (new_ref, path))
            else:
                try:
                    with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                        new_text = fh.read()
                except OSError:
                    new_text = None
            label = "%s at %s" % (os.path.splitext(os.path.basename(path))[0],
                                  where[:7] if new_ref else where)
            p = agent_version_problem(label, old_text, new_text, phase)
            if p:
                problems.append(p)
    return problems


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
    left = placeholders_left(pe_file)
    if left:
        die("%s still has %s: write current/RECAP.md and re-run assemble %s before archive"
            % (PE.rel(root, pe_file), " ".join(left), phase))
    n = _inbox_pending_count(root, conf)
    if n:
        die("current/INBOX.md holds %d unconsumed bullet(s): run `PY tools/status.py inbox-consume`, "
            "then inbox-mark the routed rows, re-assemble, lint, then archive" % n)
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
    # fix-18: the archived phase's owner stamp goes; the next `go` restamps it
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
        out.append("RUN: %s tools/plan_edit.py gen-close %s --by router" % (py, gen))
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
    path = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(path):
        return []
    out = []
    for raw in PE.read_text(path).split("\n"):
        b = PE.split_eol(raw)[0]
        m = re.match(r"^\s*-\s+([0-9]+(?:\.[0-9]+)*)\s", b)
        if not m or m.group(1) == gen:
            continue
        if m.group(1).split(".")[0] != gen.split(".")[0]:
            continue
        st = re.search(r"status:\s*(\S+)", b)
        if not st or st.group(1) != "closed":
            out.append(m.group(1))
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--archive":
        argv[0] = "archive"
    p = argparse.ArgumentParser(
        prog="phaseend_index.py",
        description="Assemble / archive / lint PhaseEnd files from phase-ends/current/.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("assemble", help="write phase-ends/PhaseEnd_Phase<N>.md")
    s.add_argument("phase", nargs="?")
    s = sub.add_parser("lint", help="all tasks closed and MILESTONE green")
    s.add_argument("phase", nargs="?")
    s.add_argument("--file", dest="lint_file",
                   help="lint a single file (no plan needed)")
    s = sub.add_parser("archive", aliases=["--archive"],
                       help="git mv current/ -> phase-<N>/ and extend the indexes")
    s.add_argument("phase", nargs="?")
    sub.add_parser("legacy-index", help="index pre-PA3 PhaseEnd files")
    s = sub.add_parser("verify", help="run the Milestone's verified-by clauses; GREEN/RED each")
    s.add_argument("phase", nargs="?")
    s.add_argument("--verbose", action="store_true",
                   help="print every clause, not just the red ones")
    a = p.parse_args(argv)
    root = PE.find_root()
    conf = PE.cfg(root)
    return {"assemble": cmd_assemble, "lint": cmd_lint, "archive": cmd_archive,
            "--archive": cmd_archive, "legacy-index": cmd_legacy,
            "verify": cmd_verify}[a.cmd](a, root, conf)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
