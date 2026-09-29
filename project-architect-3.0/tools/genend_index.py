#!/usr/bin/env python
"""genend_index.py -- assemble phase-ends/GenerationEnd_<G>.md from PhaseEnd files.

    genend_index.py assemble <G>
    genend_index.py lint [<G>]

The recap placeholder is filled from `## Generation Recap` in current/RECAP.md, or in
phase-<N>/RECAP.md once phaseend_index.py archive has moved it there (the last phase's
closer writes it from the GENERATION END addendum); a filled recap survives re-runs.
Stdlib only; no imports from `pa/` (it may import plan_edit.py, its neighbour).
"""

import argparse
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_edit as PE                                          # noqa: E402
import _genplan                                                 # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RECAP_PH = "{{AUTHORED:recap}}"


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def gen_phases(root, gen):
    path = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(path):
        die("no GENERATION_PLAN.md at the project root")
    out = []
    for pl in _genplan.phase_lines(PE.read_text(path)):
        f = _genplan.fields(pl)
        if f["id"].split(".")[0] != str(gen).split(".")[0]:
            continue
        out.append({"id": f["id"], "name": f["name"],
                    "milestone": f["milestone"] or "-", "status": f["status"] or "-",
                    "link": f["phase_end"] or "-"})
    return out


def section(text, title):
    m = re.search(r"(?ms)^## %s\s*\n(.*?)(?=\n## |\Z)" % re.escape(title), text)
    if not m:
        return []
    out = []
    for raw in m.group(1).split("\n"):
        s = raw.rstrip()
        if s.strip() and not s.strip().startswith("{{") and s.strip() != "- (none)":
            out.append(s)
    return out


def gen_recap(root, gen):
    """`## Generation Recap` from current/RECAP.md, else from phase-<N>/RECAP.md of this
    generation's phases, newest first (phaseend_index.py archive moves RECAP.md with current/)."""
    pdir = PE.phase_dir(root)
    cands = [os.path.join(pdir, "current", "RECAP.md")]
    for p in reversed(gen_phases(root, gen)):
        m = re.search(r"PhaseEnd_Phase(\S+)\.md", p["link"] or "")
        if m:
            cands.append(os.path.join(pdir, "phase-%s" % m.group(1), "RECAP.md"))
    for path in cands:
        if os.path.isfile(path):
            body = section(PE.read_text(path), "Generation Recap")
            if body:
                return "\n".join(body)
    return None


def existing_recap(path):
    if not os.path.isfile(path):
        return None
    body = section(PE.read_text(path), "Plain-English Recap")
    return "\n".join(body) if body else None


def cmd_assemble(a, root):
    phases = gen_phases(root, a.gen)
    if not phases:
        die("no '- %s.<n>' phase lines in GENERATION_PLAN.md" % a.gen)
    pdir = PE.phase_dir(root)
    target = os.path.join(pdir, "GenerationEnd_%s.md" % a.gen)
    recap = gen_recap(root, a.gen) or existing_recap(target) or RECAP_PH

    gname = "-"
    for raw in PE.read_text(os.path.join(root, "GENERATION_PLAN.md")).split("\n")[:3]:
        m = re.match(r"^#\s*Generation\s+\S+\s*[-—]+\s*(.+)$", raw.strip())
        if m:
            gname = m.group(1).strip()
            break

    out = ["# GenerationEnd — Generation %s: %s" % (a.gen, gname),
           "Closed: %s | Phases: %d (%d closed)"
           % (datetime.date.today().isoformat(), len(phases),
              len([p for p in phases if p["status"] == "closed"])),
           "", "## Phases"]
    binding, deferred, missing = [], [], []
    for p in phases:
        out.append("- %s | %s | milestone: %s | %s | %s"
                   % (p["id"], p["name"], p["milestone"], p["status"], p["link"]))
        path = p["link"] if p["link"] not in ("-", "") else None
        full = os.path.join(root, path) if path else None
        if not full or not os.path.isfile(full):
            missing.append(p["id"])
            continue
        text = PE.read_text(full)
        binding += section(text, "Decisions that still bind")
        deferred += section(text, "Deferred")

    def dedup(rows):
        seen, out2 = set(), []
        for r in rows:
            k = r.strip().lower()
            if k and k not in seen:
                seen.add(k)
                out2.append(r.rstrip())
        return out2

    out += ["", "## Decisions that still bind"] + (dedup(binding) or ["- (none)"])
    out += ["", "## Deferred"] + (dedup(deferred) or ["- (none)"])
    out += ["", "## Plain-English Recap", recap, ""]
    PE.write_text(target, "\n".join(out))
    PE._cnote(target, script="genend_index.py", sub="assemble", whole=True, root=root)
    parts = [PE.rel(root, target),
             "phases: %d" % len(phases),
             "binding: %d" % len(dedup(binding)),
             "deferred: %d" % len(dedup(deferred))]
    if missing:
        parts.append("missing: %s" % ",".join(missing))
    if RECAP_PH in recap:
        parts.append("placeholder left")
    sys.stdout.write(" | ".join(parts) + "\n")
    return 0


def cmd_lint(a, root):
    gen = a.gen
    problems = []
    if not gen:
        for name in sorted(os.listdir(PE.phase_dir(root))):
            m = re.match(r"^GenerationEnd_(\S+)\.md$", name)
            if m:
                gen = m.group(1)
    if not gen:
        die("no GenerationEnd_<G>.md found; pass the generation id")
    target = os.path.join(PE.phase_dir(root), "GenerationEnd_%s.md" % gen)
    if not os.path.isfile(target):
        die("no %s" % PE.rel(root, target))
    text = PE.read_text(target)
    if RECAP_PH in text:
        problems.append("%s still has %s" % (PE.rel(root, target), RECAP_PH))
    for p in gen_phases(root, gen):
        if p["status"] != "closed":
            problems.append("phase %s is %s (must be closed)" % (p["id"], p["status"]))
        link = os.path.join(root, p["link"]) if p["link"] not in ("-", "") else None
        if not link or not os.path.isfile(link):
            problems.append("phase %s has no PhaseEnd file (%s)" % (p["id"], p["link"]))
    if problems:
        PE.emit(["ERROR " + x for x in problems]
                + ["FAILED: %d problem(s)" % len(problems)])
        return 1
    sys.stdout.write("OK\n")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="genend_index.py", description="Assemble GenerationEnd_<G>.md.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("assemble", help="build GenerationEnd_<G>.md")
    s.add_argument("gen")
    s = sub.add_parser("lint", help="all phases closed, no placeholder left")
    s.add_argument("gen", nargs="?")
    a = p.parse_args(argv)
    root = PE.find_root()
    return {"assemble": cmd_assemble, "lint": cmd_lint}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
