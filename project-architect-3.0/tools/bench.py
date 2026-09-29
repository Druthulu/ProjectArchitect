#!/usr/bin/env python
"""bench.py -- the agent bench: fixtures, plan, run, pull, compare, check.

  bench.py [--fixtures DIR] fixtures --pin     write MANIFEST.json (sha256 per file + tree hash)
  bench.py [--fixtures DIR] fixtures --check   'fixtures OK <manifest hash>', or the drifted/missing/extra paths, exit 1
  bench.py plan <light|full|canary|max|<agent>>  one line of non-zero arms: 'coder 12 · expert 8 · …'
  bench.py run [light|full|canary|max|<agent>] [--suite light|full|canary|max] [--repeat k] [--model M] [--effort E]
               [--jobs 4] [--force] [--no-stop] [--out JSON] [--work-dir DIR] [--agents-dir DIR] [--no-rubric]
               [--preset max20|max5|pro]
      plan and run: --preset > ./.claude/pa.json preset > max20; an arm whose agent the ladder omits at the preset
      is skipped with one line; run renders each agent via pa.install.ladder into <work>/_agents/ and runs that copy.
      Arms coder, expert, planner, retriever, router, critic, review in that order; an <agent> runs its one arm
      (role: line, else the default table, else the name prefix) on --suite (default light). --repeat k (default 1)
      runs each task k times: k >= 2 clones <task>/a<i>/, retained files <task>.a<i>.*; records carry attempt i
      (results format 2; a task passes when every ran attempt passes). Each task: a git-inited clone
      of repo/ (+ overlay/files) under <work>/<run_id>/<arm>/<task>/ (work default ~/pa3-bench; never under ~/.claude: a sensitive path, edits denied), the
      agent file in its .claude/agents/, `claude -p --agent …` with the prompt on stdin, graded by _bench_grade.
      Cache order: per arm the first task's attempt 1 alone, then <= --jobs attempts in flight. Window: refuses (exit 2, one line)
      when five-hour pct + estimate > bench.window_max_pct (80) unless --force; re-polls after each arm and
      after a task when >= 60 s since the last poll, clean-stops over the cap (--no-stop: never). Writes the
      results JSON (docs/pa3-build/design/bench.md ## Results format; --out, else ./bench/results/<date>.json), the
      README.md beside a results/ dir, else inside it, then `pa_ledger.py bench import <json>`. Planner, critic and review tasks get a
      rubric side score after grading (rubric/grader.json's model, tools off, in an empty <work>/_rubric/<arm>-<id>/;
      its cost added to the task's; never the pass); --no-rubric skips it.
      Scored (suite max): a coder task whose expect has score_cmd (argv; {clone} {fixtures} {id}) is graded by
      _bench_grade.grade_scored, passing at score >= expect.pass_score (1.0); records carry score; a task's
      "timeout" (s) overrides TASK_TIMEOUT.
  bench.py ab <agentA> <agentB> [light|full|canary|max] [--repeat k] [--jobs n] [--work-dir D] [--agents-dir D]
               [--results-dir D]
      Same role: required (else 'ab: roles differ (<a> vs <b>)', exit 2, nothing launched). Runs A then B as
      `run <agent> --suite S --repeat k` (results files in D, default ./bench/results; two ledger imports), then per
      task id '<id> | A p/k $x t | B p/k $x t' and 'A better a · B better b · tie t · cost A $x / B $y · turns A t /
      B u'; no significance claim; exit = worst of the two runs.
  bench.py pull [--source URL|DIR]             each format-1/2 results JSON of DIR (or the one at URL) into the
      ledger as source=public; source default .claude/pa.json bench.source; none, or a missing dir: one line, exit 0.
  bench.py compare [--agents DIR] [--base DIR] per installed agent (default ./.claude/agents), by name, one line
      '<name> <verdict> [why]': no-series | update | match | run-offered | lightly-altered (docs/pa3-build/design/bench.md
      ## Comparison). User marks: version '1+u1' or 'user/1'; a marked body within 20 % of the base agent's
      lines (--base, default package agents/, else pa3-src) is lightly-altered, never overwritten.
  bench.py check [--results DIR]               four kinds of line, DIR default ./bench/results:
      'budget OK|FAIL (light L, full F points)' (newest light < 4, full < 10: last w5h_after - first w5h_before;
      a part is the reason when unmeasured or over); 'graders OK|FAIL (full: g grader, h harness, u unaudited;
      light: ...)' (failed records of the newest runs by failure_kind, agent not counted; FAIL when any);
      'noise <agent> ±f/n [(date)]|unknown' per unstopped arm of the newest full (ids whose attempts disagree, from
      that run's repeats, else the newest doc's, any date); 'calibration in/tiers [(out: ...)]' (arm tiers, by task
      id, within 50-85 %). A max run adds 'max <pts>' to budget (never a FAIL), 'max: ...' to graders, and
      'noise max <agent> ±f/n [Δscore d]' per unstopped arm of its newest run. Exit 1 only on a budget or graders FAIL.
  bench.py audit [<results.json> | --latest light|full|canary] [--arm A] [--unaudited] [--show N]
               [--set <arm>/<id> agent|grader|harness --why TEXT] [--results DIR]
      Every failed task '<arm>/<id> [<kind>|unaudited] <detail>' of one results JSON (default: the newest in DIR,
      default ./bench/results); --show N: the first N lines of the retained answer (<work_dir>/<arm>/<id>.out.json);
      ids '<arm>/<id>#a<i>' when the doc's repeat >= 2; --set <arm>/<id> (every failed attempt) or <arm>/<id>#a<i>
      writes failure_kind + audit_note into the JSON, then re-imports it into the ledger. Ends 'unaudited u · agent a · grader g · harness h';
      exit 0, or 2 when the file is missing (docs/pa3-build/design/bench.md ## Audit).
  bench.py readme [--results DIR]              regenerate README.md beside a results/ DIR, else inside DIR
      (default ./bench/results), from its
      results JSONs; prints 'readme: <path>' (the bench Action's publish job, branch bench-results).

Fixture root: --fixtures, else <script dir>/../tests/fixtures/bench, else
~/.claude/pa3-src/project-architect-3.0/tests/fixtures/bench. Hashes read bytes with CRLF->LF
normalised so a Windows checkout and Linux CI agree. `pa` modules and pa_ledger.py: <script>/.. if present,
else ~/.claude/pa3. Stdlib only.
"""

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bench_grade  # noqa: E402
from _bench_grade import fixture_root  # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MANIFEST = "MANIFEST.json"


def _files(root):
    """Sorted posix relative paths of every fixture file except the manifest and bytecode."""
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(root).as_posix()
        if rel != MANIFEST:
            out.append(rel)
    return sorted(out)


def _sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def manifest(root):
    files = {rel: _sha(root / rel) for rel in _files(root)}
    lines = "\n".join(f"{rel} {sha}" for rel, sha in sorted(files.items()))
    return {"version": 1, "files": files, "hash": hashlib.sha256(lines.encode("utf-8")).hexdigest()}


def cmd_fixtures(args):
    root = fixture_root(args.fixtures)
    if not root.is_dir():
        print(f"no fixtures at {root}")
        return 1
    now = manifest(root)
    if args.pin:
        text = json.dumps(now, indent=2, sort_keys=True) + "\n"
        with open(root / MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"pinned {len(now['files'])} files {now['hash'][:12]}")
        return 0
    try:
        pinned = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"missing {MANIFEST}")
        return 1
    old, new = pinned.get("files", {}), now["files"]
    bad = [f"drifted {rel}" for rel in sorted(old) if rel in new and old[rel] != new[rel]]
    bad += [f"missing {rel}" for rel in sorted(old) if rel not in new]
    bad += [f"extra {rel}" for rel in sorted(new) if rel not in old]
    if not bad and pinned.get("hash") != now["hash"]:
        bad.append("drifted MANIFEST.json hash")
    if bad:
        print("\n".join(bad))
        return 1
    print(f"fixtures OK {now['hash']}")
    return 0


# --------------------------------------------------------------------------- plan / run
HERE = Path(__file__).resolve().parent
ARMS = ("coder", "expert", "planner", "retriever", "router", "critic", "review")
DEFAULT_AGENT = {"coder": "coder-opus55", "expert": "expert-opus55", "planner": "planner-phase",
                 "retriever": "retriever-code", "router": "pa-session", "critic": "critic", "review": "review"}
SUITES = ("light", "full", "canary", "max")
TASK_TIMEOUT = 1200
CODER_AGENT = "bench-coder"   # 3.9.7 T3: the expert arm's consequence stage (fixture agents/bench-coder.md)
CONSEQUENCE_PREFIX = "Implement this brief in the current repository.\n\n"
POLL_EVERY_S = 60
RUBRIC_ARMS = ("planner", "critic", "review")   # 3.9.7 T4: the rubric side score
FALLBACK_COST = 0.30
POINTS_PER_USD = 0.3


def _pa_home():
    """<script>/.. when it holds pa/, else ~/.claude/pa3."""
    return HERE.parent if (HERE.parent / "pa").is_dir() else Path.home() / ".claude" / "pa3"


def _pa():
    root = str(_pa_home())
    if root not in sys.path:
        sys.path.insert(0, root)
    import pa
    import pa.config
    import pa.prices
    import pa.usage_api
    return pa


def window_pct():
    """Five-hour window pct from the usage API (forced poll), None when unavailable. Tests patch this."""
    try:
        pa = _pa()
        return float(pa.usage_api.poll(pa.config.load(), force=True)["five_hour"]["pct"])
    except Exception:
        return None


def _window_cap():
    try:
        v = json.loads(Path(".claude/pa.json").read_text(encoding="utf-8"))["bench"]["window_max_pct"]
        if v is not None:
            return float(v)
    except Exception:
        pass
    try:
        return float(_pa().config.DEFAULTS["bench"]["window_max_pct"])
    except Exception:
        return 80.0


def _preset(arg):
    """Resolved plan preset: --preset > cwd .claude/pa.json `preset` > max20 (as plan_edit.py _preset)."""
    if arg:
        return arg
    try:
        return json.loads(Path(".claude/pa.json").read_text(encoding="utf-8")).get("preset") or "max20"
    except Exception:
        return "max20"


def _ladder_arms(arms, agents, preset):
    """(kept [(arm, name, rendered text | None)], skip lines): drops arms whose agent is absent at the preset."""
    _pa()
    from pa.install import ladder
    kept, skips = [], []
    for arm, name in arms:
        path = agents / f"{name}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        out = ladder.render(name, text or "", preset)
        if out is None:
            skips.append(f"skipped {name}: preset {preset} has no hard rung")
            continue
        kept.append((arm, name, out if text is not None else None))
    return kept, skips


def _agents_dir(arg):
    if arg:
        return Path(arg).resolve()
    beside = HERE.parent / "agents"
    return beside if beside.is_dir() else Path.cwd() / ".claude" / "agents"


def _frontmatter(path):
    """({key: value}, body) of an agent file; body = text below the closing '---' (CRLF->LF)."""
    return _frontmatter_text(Path(path).read_text(encoding="utf-8"))


def _frontmatter_text(text):
    """_frontmatter of an agent file's text."""
    text = text.replace("\r\n", "\n")
    meta, body = {}, text
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body = "\n".join(lines[i + 1:])
                for ln in lines[1:i]:
                    if ":" in ln and not ln.startswith((" ", "\t")):
                        k, v = ln.split(":", 1)
                        meta[k.strip()] = v.strip()
                break
    return meta, body


def _body_hash(body):
    """Arm-key body hash (docs/pa3-build/design/bench.md ## Arm key): sha256 of the text below the frontmatter."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _arm_of(name, agents):
    """Arm of an agent name: its role: line, else the default table, else the prefix before '-'; None if unknown."""
    path = agents / f"{name}.md"
    if path.is_file():
        role = _frontmatter(path)[0].get("role")
        if role in ARMS:
            return role
    for arm, default in DEFAULT_AGENT.items():
        if default == name:
            return arm
    prefix = name.split("-", 1)[0]
    return prefix if prefix in ARMS else None


def _target(target, agents, suite="light"):
    """(suite, [(arm, agent name)]) or None for an unknown target; suite applies to an agent target."""
    if target in SUITES:
        return target, [(arm, DEFAULT_AGENT[arm]) for arm in ARMS]
    arm = _arm_of(target, agents)
    return (suite, [(arm, target)]) if arm else None


def _tasks(root, arm, suite):
    out = []
    for p in (root / "tasks" / arm).glob("*.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        if suite in t.get("suites", []):
            out.append(t)
    return sorted(out, key=lambda t: t["id"])


def cmd_plan(args):
    root, agents = fixture_root(args.fixtures), _agents_dir(args.agents_dir)
    tgt = _target(args.target, agents)
    if tgt is None:
        print(f"unknown suite or agent: {args.target}")
        return 2
    suite, arms = tgt
    kept, skips = _ladder_arms(arms, agents, _preset(args.preset))
    for line in skips:
        print(line)
    counts = [(arm, len(_tasks(root, arm, suite))) for arm, _, _ in kept]
    if kept:
        print(" · ".join(f"{arm} {n}" for arm, n in counts if n))
    return 0


def _base_model(m):
    return (m or "").split("[", 1)[0]


def _parse_out(stdout):
    try:
        return json.loads(stdout)
    except ValueError:
        for ln in reversed((stdout or "").splitlines()):
            if ln.strip().startswith("{"):
                try:
                    return json.loads(ln)
                except ValueError:
                    pass
    return None


def _transcript_turns(session_id):
    """(path, distinct assistant message ids) of the session transcript, or (None, None)."""
    if not session_id:
        return None, None
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    hits = sorted((cfg / "projects").glob(f"*/{session_id}.jsonl"))
    if not hits:
        return None, None
    ids = set()
    with open(hits[0], encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if rec.get("type") == "assistant" and isinstance(msg, dict) and msg.get("id"):
                ids.add(msg["id"])
    return hits[0], len(ids)


def _unrun(task, attempt=1):
    return {"id": task["id"], "tier": task.get("tier"), "attempt": attempt, "passed": None, "cost_usd": None, "secs": None,
            "turns": None, "session_id": None, "turns_source": None, "input": None, "output": None,
            "cache_read": None, "cache_write": None, "rubric": None, "score": None, "denials": None, "error": None,
            "failure_kind": None, "audit_note": None, "consequence": None, "detail": None}


def _harness(rec, cause):
    """Driver rule (docs/pa3-build/design/bench.md ## Audit): a failure the harness caused is classified at run time."""
    if rec.get("passed") is False:
        rec.update(failure_kind="harness", audit_note=cause)
    return rec


def _retain(clone, base, out, transcript, detail):
    """Beside the clone: <task>.out.json, .transcript.jsonl (when found), .grade.txt, .diff (after grading)."""
    base.with_name(base.name + ".out.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    if transcript is not None:
        shutil.copyfile(transcript, base.with_name(base.name + ".transcript.jsonl"))
    base.with_name(base.name + ".grade.txt").write_text(str(detail) + "\n", encoding="utf-8")
    try:
        _git(clone, "add", "-A")
        r = subprocess.run(["git", "-c", "core.autocrlf=false", "diff", "--cached"], cwd=clone,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        diff = r.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        diff = f"# diff failed: {exc!r}\n"
    base.with_name(base.name + ".diff").write_text(diff, encoding="utf-8")


def _grade(arm, task, clone, answer, transcript, fixtures):
    exp = task.get("expect", {})
    if arm == "coder":
        return _bench_grade.grade_coder(clone, task, fixtures)
    if arm == "planner":
        return _bench_grade.grade_planner(clone / exp["draft"], exp)
    if arm == "router":
        if transcript is None:
            return False, "no transcript"
        return _bench_grade.grade_router(transcript, exp)
    fn = {"retriever": _bench_grade.grade_retriever, "expert": _bench_grade.grade_expert,
          "critic": _bench_grade.grade_critic, "review": _bench_grade.grade_review}[arm]
    return fn(answer, exp)


def _force_remove(func, path, _exc):
    """rmtree onexc: git object files are read-only on Windows; make writable and retry."""
    os.chmod(path, 0o700)
    func(path)


def _git(clone, *args):
    subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost",
                    "-c", "core.autocrlf=false", *args], cwd=clone, check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


def _clone(root, task, clone):
    """A fresh git-inited clone of the task's repo dir (task["repo"], default repo/; + its overlay and files) at clone."""
    if clone.exists():
        shutil.rmtree(clone, onexc=_force_remove)
    shutil.copytree(root / task.get("repo", "repo"), clone, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if task.get("overlay"):
        shutil.copytree(root / task["overlay"], clone, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in task.get("files", []):
        shutil.copyfile(root / rel, clone / Path(rel).name)
    _git(clone, "init", "-q")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "bench fixture")
    (clone / ".claude" / "agents").mkdir(parents=True, exist_ok=True)


def _env():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(PA_HOOKS_OFF="1", PYTHONIOENCODING="utf-8")
    return env


def _consequence(ctx, task, brief, sfx=""):
    """3.9.7 T3: bench-coder implements the expert's brief in a fresh clone <work>/expert/<id>.c/, graded by the
    hidden tests of the coder task the expert task is based on; {passed, cost_usd, secs, turns, session_id, detail}.
    sfx: '' (repeat 1) or '.a<i>' (3.9.7 T5: <id>.a<i>.c/)."""
    root, work = ctx["fixtures"], ctx["work"]
    clone = work / "expert" / f"{task['id']}{sfx}.c"
    con = {"passed": False, "cost_usd": None, "secs": None, "turns": None, "session_id": None, "detail": None}
    _clone(root, task, clone)
    shutil.copyfile(root / "agents" / f"{CODER_AGENT}.md", clone / ".claude" / "agents" / f"{CODER_AGENT}.md")
    cmd = [ctx["claude"], "-p", "--agent", CODER_AGENT, "--effort", "medium", "--permission-mode", "acceptEdits",
           "--permission-prompts", "none", "--settings", str((root / "settings" / "allow.json").resolve()),
           "--output-format", "json"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=CONSEQUENCE_PREFIX + brief, cwd=clone, env=_env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=TASK_TIMEOUT)
    except subprocess.TimeoutExpired:
        con.update(secs=round(time.time() - t0, 1), detail=f"timeout after {TASK_TIMEOUT} s")
        return con
    con["secs"] = round(time.time() - t0, 1)
    out = _parse_out(r.stdout)
    if not isinstance(out, dict):
        con["detail"] = f"no JSON (exit {r.returncode}): {(r.stderr or r.stdout or '')[-300:]}"
        return con
    con.update(cost_usd=out.get("total_cost_usd"), session_id=out.get("session_id"))
    transcript, turns = _transcript_turns(con["session_id"])
    con["turns"] = turns if turns is not None else out.get("num_turns")
    try:
        ok, detail = _bench_grade.grade_coder(clone, {"hidden": f"hidden/{task['expect']['based_on']}"}, root)
    except Exception as exc:
        ok, detail = False, f"grader error: {exc!r}"
    con.update(passed=bool(ok), detail=str(detail)[-500:])
    _retain(clone, work / "expert" / f"{task['id']}{sfx}.c", out, transcript, detail)
    return con


def _rubric(ctx, arm, task, answer, rec, sfx=""):
    """3.9.7 T4: the rubric side score. Prompt at <work>/<arm>/<id>.rubric.txt, one grader call in the empty
    <work>/_rubric/<arm>-<id>/; rec rubric {score, max, model}, the grader cost added to cost_usd, a detail note.
    A failure leaves rubric null with a note; never touches passed. sfx '.a<i>' (3.9.7 T5): <id>.a<i>.rubric.txt,
    _rubric/<arm>-<id>-a<i>."""
    root, work = ctx["fixtures"], ctx["work"]
    try:
        grader = json.loads((root / "rubric" / "grader.json").read_text(encoding="utf-8"))
        prompt, mx = _bench_grade.rubric_prompt(arm, task, answer, root)
        (work / arm / f"{task['id']}{sfx}.rubric.txt").write_text(prompt, encoding="utf-8")
        cwd = work / "_rubric" / f"{arm}-{task['id']}{sfx.replace('.', '-')}"
        cwd.mkdir(parents=True, exist_ok=True)
        score, mx, d = _bench_grade.rubric_score(prompt, dict(grader, max=mx), ctx["claude"], cwd)
    except Exception as exc:
        rec["detail"] += f"; rubric error: {exc!r}"[:300]
        return
    if d.get("cost_usd"):
        rec["cost_usd"] = round((rec["cost_usd"] or 0) + d["cost_usd"], 6)
    cost = f" ${d['cost_usd']:.2f}" if d.get("cost_usd") else ""
    if score is None:
        rec["detail"] += f"; rubric failed{cost}: {d.get('error')}"
        return
    rec["rubric"] = {"score": score, "max": mx, "model": d.get("model")}
    rec["detail"] += f"; rubric {score:g}/{mx}{cost}"


def run_task(ctx, arm, task, meta, attempt=1, repeat=1):
    """Clone, launch claude, grade; the attempt's results record. repeat 1: <work>/<arm>/<id>/ and <id>.*;
    repeat >= 2 (3.9.7 T5): clone <work>/<arm>/<id>/a<i>/, retained files <id>.a<i>.*."""
    root, work = ctx["fixtures"], ctx["work"]
    sfx = "" if repeat == 1 else f".a{attempt}"
    clone = work / arm / task["id"] / f"a{attempt}" if sfx else work / arm / task["id"]
    rec = _unrun(task, attempt)
    _clone(root, task, clone)
    shutil.copyfile(meta["path"], clone / ".claude" / "agents" / f"{meta['agent']}.md")
    settings = (root / (task.get("settings") or "settings/allow.json")).resolve()
    cmd = [ctx["claude"], "-p", "--agent", meta["agent"]]
    if meta["effort"]:
        cmd += ["--effort", meta["effort"]]
    cmd += ["--permission-mode", "acceptEdits", "--permission-prompts", "none", "--settings", str(settings),
            "--output-format", "json"]
    if ctx["model"]:
        cmd += ["--model", ctx["model"]]
    cmd += list(task.get("cli", []))
    env = _env()
    limit = task.get("timeout") or TASK_TIMEOUT   # T24: a task's own timeout (max suite)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=task["prompt"], cwd=clone, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=limit)
    except subprocess.TimeoutExpired:
        rec.update(passed=False, secs=round(time.time() - t0, 1), detail="timeout")
        return _harness(rec, f"timeout after {limit} s")
    rec["secs"] = round(time.time() - t0, 1)
    out = _parse_out(r.stdout)
    if not isinstance(out, dict):
        rec.update(passed=False, detail=f"no JSON (exit {r.returncode}): {(r.stderr or r.stdout or '')[-300:]}")
        return _harness(rec, f"no JSON from claude (exit {r.returncode})")
    usage = out.get("usage") or {}
    rec.update(cost_usd=out.get("total_cost_usd"), session_id=out.get("session_id"),
               input=usage.get("input_tokens"), output=usage.get("output_tokens"),
               cache_read=usage.get("cache_read_input_tokens"),
               cache_write=usage.get("cache_creation_input_tokens"),
               denials=len(out.get("permission_denials") or []),
               error=out.get("subtype") if out.get("is_error") else None)
    mu = out.get("modelUsage") or {}
    notes = []
    if mu:
        rec["model"] = max(sorted(mu), key=lambda k: (mu[k] or {}).get("costUSD") or 0)
        if _base_model(rec["model"]) != _base_model(meta["file_model"]):
            notes.append(f"model {rec['model']} != file {meta['file_model']}")
    transcript, turns = _transcript_turns(rec["session_id"])
    if turns is not None:
        rec.update(turns=turns, turns_source="transcript")
    else:
        rec.update(turns=out.get("num_turns"), turns_source="json")
    notes.append(f"num_turns {out.get('num_turns')}")
    if out.get("is_error"):
        notes.append(f"is_error {out.get('subtype')}")
    exp = task.get("expect") or {}
    scored = arm == "coder" and "score_cmd" in exp   # T24: the scored grader (max suite)
    try:
        if scored:
            ok, detail, rec["score"] = _bench_grade.grade_scored(clone, task, root)
        else:
            ok, detail = _grade(arm, task, clone, str(out.get("result") or ""), transcript, root)
    except Exception as exc:
        ok, detail = False, f"grader error: {exc!r}"
    rec.update(passed=bool(ok), detail="; ".join([str(detail)[-500:]] + notes))
    if scored and not ok:   # the grader measured it: agent below the bar, or no score (grader)
        if rec["score"] is not None:
            rec.update(failure_kind="agent", audit_note=f"score {rec['score']:.2f} < {exp.get('pass_score', 1.0)}")
        else:
            rec.update(failure_kind="grader", audit_note=str(detail)[-300:])
    _retain(clone, work / arm / f"{task['id']}{sfx}", out, transcript, detail)
    if arm == "expert" and ok:
        rec["consequence"] = con = _consequence(ctx, task, str(out.get("result") or ""), sfx)
        rec["passed"] = bool(con["passed"])
        rec["detail"] += f"; consequence {'pass' if con['passed'] else 'FAIL'}: {str(con['detail'])[-300:]}"
    if arm in RUBRIC_ARMS and ctx.get("rubric", True):
        answer = str(out.get("result") or "")
        if arm == "planner":
            draft = clone / task.get("expect", {}).get("draft", "")
            answer = draft.read_text(encoding="utf-8", errors="replace") if draft.is_file() else ""
        _rubric(ctx, arm, task, answer, rec, sfx)
    # 3.11 T26: a scored attempt the grader measured keeps its kind (agent); denials or an error only
    # make it the harness's when no score came back
    measured = scored and rec["score"] is not None
    if rec["denials"]:
        tools = sorted({str((d or {}).get("tool_name")) for d in out.get("permission_denials") or []})
        cause = f"{rec['denials']} permission denial(s): {', '.join(tools)}"
        if not measured:
            _harness(rec, cause)
        elif rec.get("passed") is False:
            rec["audit_note"] = f"{rec['audit_note']}; {cause}"
    elif rec["error"] and not measured:
        _harness(rec, f"claude error {rec['error']}")
    return rec


def _results_dir(args):
    if args.out:
        return Path(args.out).resolve().parent
    return Path(getattr(args, "results_dir", None) or Path.cwd() / "bench" / "results").resolve()


def _load_results(rdir):
    docs = []
    for p in sorted(rdir.glob("*.json")) if rdir.is_dir() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("format") in (1, 2):
            docs.append(d)
    return docs


def _median_cost(rdir, suite):
    same = [d for d in _load_results(rdir) if d.get("suite") == suite]
    if same:
        newest = max(same, key=lambda d: str(d.get("date")))
        costs = [t["cost_usd"] for a in newest.get("arms", []) for t in a.get("tasks", [])
                 if t.get("cost_usd") is not None]
        if costs:
            return statistics.median(costs)
    return FALLBACK_COST


def _by_id(tasks):
    """{id: [records]} in first-seen id order (format 1: one record per id; format 2: one per attempt)."""
    out = {}
    for t in tasks:
        out.setdefault(t.get("id"), []).append(t)
    return out


def _tally(tasks):
    """(k, n, tier cells, cost) per task id: an id ran when any attempt ran; it passes when every ran attempt
    passes (3.9.7 T5). Cost sums every record."""
    ran = {i: [t for t in ts if t.get("passed") is not None] for i, ts in _by_id(tasks).items()}
    ran = {i: ts for i, ts in ran.items() if ts}
    ok = {i: all(t["passed"] for t in ts) for i, ts in ran.items()}
    tier = {i: ts[0].get("tier") for i, ts in ran.items()}
    tiers = []
    for tr in sorted({v for v in tier.values() if v is not None}):
        ids = [i for i in ran if tier[i] == tr]
        tiers.append(f"t{tr} {sum(1 for i in ids if ok[i])}/{len(ids)}")
    cost = sum(t.get("cost_usd") or 0 for t in tasks)
    return sum(ok.values()), len(ran), tiers, cost


def _arm_summary(arm):
    k, n, tiers, cost = _tally(arm["tasks"])
    cost += sum((t.get("consequence") or {}).get("cost_usd") or 0 for t in arm["tasks"])   # 3.9.7 T3
    ids = [[t["score"] for t in ts if t.get("passed") is not None and t.get("score") is not None]
           for ts in _by_id(arm["tasks"]).values()]   # T24: mean over ids of the mean score of ran attempts
    ids = [s for s in ids if s]
    mu = f" · score μ{statistics.mean(statistics.mean(s) for s in ids):.2f}" if ids else ""
    if arm.get("stopped"):
        return f"clean-stop {k}/{len(_by_id(arm['tasks']))}" + mu, cost
    return f"pass {k}/{n}" + (f" ({' · '.join(tiers)})" if tiers else "") + mu, cost


def _rubric_cell(arm):
    """3.9.7 T4: mean rubric score / mean max over the arm's scored tasks, one decimal; an em dash when none."""
    rs = [t["rubric"] for t in arm.get("tasks", []) if isinstance(t.get("rubric"), dict)
          and t["rubric"].get("score") is not None and t["rubric"].get("max")]
    if not rs:
        return "—"
    return f"{statistics.mean(r['score'] for r in rs):.1f}/{statistics.mean(r['max'] for r in rs):.1f}"


def _write_readme(rdir):
    docs = sorted(_load_results(rdir), key=lambda d: str(d.get("date")), reverse=True)
    lines = ["# Bench results", "", "Regenerated by `bench.py run` from `results/*.json`; newest first.", "",
             "| date | suite | preset | role | agent | model/effort | version | pass | rubric | cost | claude_code |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in docs:
        for a in d.get("arms", []):
            summary, cost = _arm_summary(a)
            lines.append(f"| {str(d.get('date'))[:10]} | {d.get('suite')} | {d.get('preset') or '—'} | "
                         f"{a.get('role')} | {a.get('agent')} | "
                         f"{a.get('model')}/{a.get('effort')} | {a.get('version')} | {summary} | {_rubric_cell(a)} | "
                         f"${cost:.2f} | {d.get('claude_code')} |")
    path = (rdir.parent if rdir.name == "results" else rdir) / "README.md"   # never a parent README (fix-1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _out_path(args, now):
    if args.out:
        return Path(args.out).resolve()
    rdir = _results_dir(args)
    base = now.strftime("%Y-%m-%d")
    p, i = rdir / f"{base}.json", 2
    while p.exists():
        p, i = rdir / f"{base}-{i}.json", i + 1
    return p


def _ledger_import(path):
    beside = HERE.parent / "pa_ledger.py"
    ledger = beside if beside.is_file() else Path.home() / ".claude" / "pa3" / "pa_ledger.py"
    r = subprocess.run([sys.executable, str(ledger), "bench", "import", str(path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    lines = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    print(lines[-1] if lines else f"ledger exit {r.returncode}")
    return r.returncode


def cmd_run(args):
    root, agents = fixture_root(args.fixtures), _agents_dir(args.agents_dir)
    tgt = _target(args.target, agents, args.suite)
    if tgt is None:
        print(f"unknown suite or agent: {args.target}")
        return 2
    suite, arm_list = tgt
    k = max(1, args.repeat)
    preset = _preset(args.preset)
    kept, skips = _ladder_arms(arm_list, agents, preset)
    for line in skips:
        print(line)
    if not kept:
        return 0
    plan, rendered = [], {}
    for arm, name, text in kept:
        tasks = _tasks(root, arm, suite)
        if not tasks:
            continue
        path = agents / f"{name}.md"
        if text is None:
            print(f"no agent file {path}")
            return 2
        rendered[name] = text
        fm, body = _frontmatter_text(text)
        plan.append((arm, tasks, {
            "path": path, "agent": fm.get("name") or name, "role": fm.get("role") or arm,
            "file_model": fm.get("model"), "effort": args.effort or fm.get("effort") or None,
            "version": fm.get("version"), "body_hash": _body_hash(body)}))
    claude = shutil.which("claude")
    if not claude:
        print("claude not found on PATH")
        return 2
    rdir = _results_dir(args)
    per_task = _median_cost(rdir, suite) * POINTS_PER_USD
    cap = _window_cap()
    total = sum(len(t) for _, t, _ in plan) * k   # attempts
    pct = window_pct()
    if pct is None:
        print("window: unknown")
    elif pct + total * per_task > cap and not args.force:
        print(f"window: five-hour {pct:.0f}% + estimate {total * per_task:.1f} > cap {cap:.0f}; "
              f"nothing launched (--force to run anyway)")
        return 2
    try:
        cv = subprocess.run([claude, "--version"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60).stdout.split()
        claude_code = cv[0] if cv else None
    except (OSError, subprocess.SubprocessError):
        claude_code = None
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + suite
    work = Path(args.work_dir or Path.home() / "pa3-bench").resolve() / run_id
    ctx = {"fixtures": root, "work": work, "claude": claude, "model": args.model, "rubric": not args.no_rubric}
    (work / "_agents").mkdir(parents=True, exist_ok=True)
    for _, _, meta in plan:   # the ladder-rendered copy runs; never written into any .claude/agents/
        meta["path"] = work / "_agents" / f"{meta['path'].stem}.md"
        meta["path"].write_text(rendered[meta["path"].stem], encoding="utf-8", newline="")
    pa = _pa()
    doc = {"format": 2, "repeat": k, "run_id": run_id, "date": now.replace(tzinfo=None).isoformat() + "Z",
           "suite": suite, "preset": preset,
           "harness_version": pa.__version__, "price_version": pa.prices.PRICES_VERSION,
           "fixture_hash": manifest(root)["hash"], "claude_code": claude_code, "work_dir": str(work), "arms": []}
    state = {"pct": pct, "last": time.monotonic(), "stop": False, "left": total}
    lock = threading.Lock()

    def check(force_poll):
        """Re-poll (after an arm, or >= POLL_EVERY_S since the last poll); set stop when over the cap."""
        with lock:
            if not force_poll and (args.no_stop or time.monotonic() - state["last"] < POLL_EVERY_S):
                return
            state["pct"], state["last"] = window_pct(), time.monotonic()
            p = state["pct"]
            if not args.no_stop and p is not None and p + state["left"] * per_task > cap:
                state["stop"] = True

    for arm, tasks, meta in plan:
        entry = {"role": meta["role"], "agent": meta["agent"], "model": meta["file_model"],
                 "effort": meta["effort"], "body_hash": meta["body_hash"], "version": meta["version"],
                 "stopped": 0, "w5h_before": None, "w5h_after": None, "tasks": []}
        doc["arms"].append(entry)
        if state["stop"]:
            entry["stopped"] = 1
            entry["tasks"] = [_unrun(t, i) for t in tasks for i in range(1, k + 1)]
            continue
        entry["w5h_before"] = state["pct"]
        (work / arm).mkdir(parents=True, exist_ok=True)
        done, pending = {}, [(t, i) for t in tasks for i in range(1, k + 1)]   # id-major: t1a1, t1a2, t2a1, ...

        def finish(rec):
            done[(rec["id"], rec["attempt"])] = rec
            with lock:
                state["left"] -= 1
            check(False)

        first, a1 = pending.pop(0)
        finish(run_task(ctx, arm, first, meta, a1, k))
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            inflight = set()
            while inflight or (pending and not state["stop"]):
                while pending and not state["stop"] and len(inflight) < max(1, args.jobs):
                    t, i = pending.pop(0)
                    inflight.add(ex.submit(run_task, ctx, arm, t, meta, i, k))
                fin, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for f in fin:
                    finish(f.result())
        if pending:
            entry["stopped"] = 1
        check(True)
        entry["w5h_after"] = state["pct"]
        entry["tasks"] = [done.get((t["id"], i)) or _unrun(t, i) for t in tasks for i in range(1, k + 1)]
        models = [t.pop("model") for t in entry["tasks"] if t.get("model")]
        if models:
            entry["model"] = max(sorted(set(models)), key=models.count)
    for a in doc["arms"]:
        for t in a["tasks"]:
            t.pop("model", None)
    out = _out_path(args, now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _write_readme(out.parent)
    for a in doc["arms"]:
        summary, cost = _arm_summary(a)
        print(f"{a['role']} {a['agent']} {a['model']}/{a['effort']} {summary} ${cost:.2f}")
    print(f"results: {out}")
    args.result_path = out   # read back by cmd_ab
    return 1 if _ledger_import(out) else 0


def _ab_side(recs):
    """(p passed attempts, k ran attempts, mean cost per attempt incl consequence, mean turns or None)."""
    ran = [t for t in recs if t.get("passed") is not None]
    costs = [(t.get("cost_usd") or 0) + ((t.get("consequence") or {}).get("cost_usd") or 0) for t in ran]
    turns = [t["turns"] for t in ran if t.get("turns") is not None]
    return (sum(1 for t in ran if t["passed"]), len(ran), statistics.mean(costs) if costs else 0.0,
            statistics.mean(turns) if turns else None)


def cmd_ab(args):
    """3.9.7 T5: two agents of one role on one suite; A then B as `run`, then the paired per-id table."""
    agents = _agents_dir(args.agents_dir)
    roles = [(_frontmatter(agents / f"{n}.md")[0].get("role") if (agents / f"{n}.md").is_file() else None)
             for n in (args.a, args.b)]
    if roles[0] is None or roles[0] != roles[1]:
        print(f"ab: roles differ ({roles[0] or 'none'} vs {roles[1] or 'none'})")
        return 2
    runs, rcs = [], []
    for name in (args.a, args.b):
        if runs:   # a distinct run_id (second resolution) for B
            time.sleep(1.0 - time.time() % 1.0 + 0.01)
        ra = argparse.Namespace(fixtures=args.fixtures, target=name, suite=args.suite, repeat=args.repeat,
                                jobs=args.jobs, work_dir=args.work_dir, agents_dir=args.agents_dir,
                                results_dir=args.results_dir, out=None, model=None, effort=None, force=False,
                                no_stop=False, no_rubric=False, result_path=None, preset=None)
        rcs.append(cmd_run(ra))
        runs.append(ra.result_path)
    if not all(runs):
        return max(rcs)
    arms = [_by_id(json.loads(p.read_text(encoding="utf-8"))["arms"][0]["tasks"]) for p in runs]
    better = [0, 0, 0]
    for tid in sorted(set(arms[0]) | set(arms[1])):
        sides = [_ab_side(a.get(tid, [])) for a in arms]
        cells = [f"{lab} {p}/{k} ${c:.2f} {'—' if t is None else f'{t:.1f}'}"
                 for lab, (p, k, c, t) in zip("AB", sides)]
        print(f"{tid} | {cells[0]} | {cells[1]}")
        ra, rb = [(p / k if k else 0.0) for p, k, _, _ in sides]
        better[0 if ra > rb else 1 if rb > ra else 2] += 1
    tot = [_ab_side([t for ts in a.values() for t in ts]) for a in arms]
    cost = [c * k for _, k, c, _ in tot]
    turns = ["—" if t is None else f"{t:.1f}" for _, _, _, t in tot]
    print(f"A better {better[0]} · B better {better[1]} · tie {better[2]} · cost A ${cost[0]:.2f} / "
          f"B ${cost[1]:.2f} · turns A {turns[0]} / B {turns[1]}")
    return max(rcs)


# --------------------------------------------------------------------------- pull / compare
LIGHT_ALTER = 0.20   # a marked body differing in <= this share of the base body's lines is lightly-altered


def _pa_json_bench(key):
    try:
        return json.loads(Path(".claude/pa.json").read_text(encoding="utf-8"))["bench"].get(key)
    except Exception:
        return None


def _ledger():
    """(ledger_cli module, open connection) of the local ledger (PA_LEDGER_DIR honoured by pa.paths)."""
    _pa()
    import pa.ledger_cli as lc
    return lc, lc.open_db()


def cmd_pull(args):
    src = args.source or _pa_json_bench("source")
    if not src:
        _pa()
        import pa.paths
        src = str(Path(pa.paths.package_clone_dir()) / "bench" / "results")
        if not Path(src).is_dir():
            print(f"pull: no source (--source URL|DIR, bench.source in .claude/pa.json, or {src}); "
                  "nothing pulled")
            return 0
    docs = []
    if str(src).startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(src, timeout=60) as r:
                docs.append((src, json.loads(r.read().decode("utf-8"))))
        except Exception as exc:
            print(f"pull: {src}: {exc}")
            return 1
    else:
        d = Path(src)
        if not d.is_dir():
            print(f"pull: no directory {d}; nothing pulled")
            return 0
        for p in sorted(d.glob("*.json")):
            try:
                docs.append((str(p), json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    docs = [(n, doc) for n, doc in docs if isinstance(doc, dict) and doc.get("format") in (1, 2)]
    lc, conn = _ledger()
    try:
        rows = sum(lc.bench_import(conn, doc, "public") for _, doc in docs)
    finally:
        lc.db.close(conn)
    print(f"pull: {rows} rows from {len(docs)} results ({src})")
    return 0


def _mark(version):
    """(ordering key tuple or None, user mark or '') of a version: string ('7', '3.10.7', '3.10.7+u1', 'user/1').

    Key: plain ``N`` -> ``(0, N)`` (pre-3.10 scheme, below every dotted version); ``a.b.c`` -> ``(1, a, b, c)``."""
    v = str(version or "").strip()
    if v.startswith("user/"):
        return None, v
    base, plus, _ = v.partition("+u")
    mark = v if plus else ""
    parts = base.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) == 2:
        return None, mark
    return ((0, int(parts[0])) if len(parts) == 1 else (1, *map(int, parts))), mark


def _last_full(rows):
    """(k, n) of the newest full-suite, not stopped run in rows, or None."""
    runs = {}
    for r in rows:
        if r["suite"] == "full" and not r["stopped"]:
            runs.setdefault(r["run_id"], []).append(r)
    if not runs:
        return None
    rid = max(runs, key=lambda k: (runs[k][0]["started"] or 0.0, k))
    by_id = {}   # 3.9.7 T5: one ledger row per attempt; a task passes when every ran attempt passes
    for r in runs[rid]:
        if r["passed"] is not None:
            by_id.setdefault(r["task_id"], []).append(r["passed"])
    return sum(1 for v in by_id.values() if all(v)), len(by_id)


def _rate(kn):
    return kn[0] / kn[1] if kn and kn[1] else 0.0


def _changed_share(base_body, body):
    """Share of the base body's lines that differ in body (difflib opcodes; 1.0 for an empty base)."""
    a, b = base_body.splitlines(), body.splitlines()
    if not a:
        return 1.0
    diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
               difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")
    return diff / len(a)


def _base_dir(arg):
    if arg:
        return Path(arg).resolve()
    beside = HERE.parent / "agents"
    return beside if beside.is_dir() else Path.home() / ".claude" / "pa3-src" / "project-architect-3.0" / "agents"


def _verdict(path, rows, base):
    fm, body = _frontmatter(path)
    name = fm.get("name") or path.stem
    if not rows:
        return "no-series", ""
    installed, mark = _mark(fm.get("version"))
    bh, model, effort = _body_hash(body), _base_model(fm.get("model")), fm.get("effort") or None
    seen = any(r["body_hash"] == bh and _base_model(r["model"]) == model and (r["effort"] or None) == effort
               for r in rows)
    if mark:
        if seen:
            return "match", f"mark {mark}"
        bp = base / f"{name}.md"
        if bp.is_file():
            share = _changed_share(_frontmatter(bp)[1], body)
            if share <= LIGHT_ALTER:
                return "lightly-altered", f"mark {mark}: {share:.0%} of base lines differ; never overwritten"
            return "run-offered", f"mark {mark}: {share:.0%} of base lines differ"
        return "run-offered", f"mark {mark}: no base agent"
    by_ver, label = {}, {}   # key tuple -> rows; key tuple -> version string as recorded
    for r in rows:
        s = str(json.loads(r["detail"] or "{}").get("version") or "").strip()
        v = _mark(s)[0]
        if v is not None:
            by_ver.setdefault(v, []).append(r)
            label.setdefault(v, s)
    ikey = installed or (0, 0)
    mine = _last_full(by_ver.get(ikey, []))
    for v in sorted((v for v in by_ver if v > ikey), reverse=True):
        theirs = _last_full(by_ver[v])
        if theirs and _rate(theirs) >= _rate(mine):
            ours = f"{mine[0]}/{mine[1]}" if mine else "never run"
            return "update", (f"version {label[v]} full {theirs[0]}/{theirs[1]} >= "
                              f"version {fm.get('version') or 0} {ours}")
    if seen:
        return "match", ""
    return "run-offered", "body not in the series"


def _row_preset(path, rows):
    """Detail preset of the newest row matching the installed key (as _verdict), else of the newest row, else '—'."""
    fm, body = _frontmatter(path)
    bh, model, effort = _body_hash(body), _base_model(fm.get("model")), fm.get("effort") or None
    match = [r for r in rows if r["body_hash"] == bh and _base_model(r["model"]) == model
             and (r["effort"] or None) == effort]
    for pick in (match, rows):
        if pick:
            return json.loads(pick[-1]["detail"] or "{}").get("preset") or "—"
    return "—"


def cmd_compare(args):
    agents = Path(args.agents).resolve() if args.agents else Path.cwd() / ".claude" / "agents"
    files = sorted(agents.glob("*.md"), key=lambda p: p.stem) if agents.is_dir() else []
    if not files:
        print(f"compare: no agents in {agents}")
        return 0
    base = _base_dir(args.base)
    lc, conn = _ledger()
    try:
        for p in files:
            name = _frontmatter(p)[0].get("name") or p.stem
            rows = conn.execute("SELECT * FROM bench WHERE agent=? ORDER BY rowid", (name,)).fetchall()
            verdict, why = _verdict(p, rows, base)
            print(f"{name} {verdict}" + (f" {why}" if why else "") + f" · preset {_row_preset(p, rows)}")
    finally:
        lc.db.close(conn)
    return 0


CAL_LO, CAL_HI = 0.50, 0.85            # calibration band per tier (docs/pa3-build/design/bench.md ## check)
BUDGET = {"light": 4, "full": 10}       # five-hour points per suite run, strictly below


def _newest(docs, suite):
    same = [d for d in docs if d.get("suite") == suite]
    return max(same, key=lambda d: str(d.get("date"))) if same else None


def _calibration(doc):
    """The calibration line of the newest full run: (arm, tier) cells of unstopped arms within CAL_LO..CAL_HI,
    counted by task id (an id passes when every ran attempt passes, as _tally). Never affects the exit code."""
    if doc is None:
        return "calibration unknown (no full run)"
    cells, out = 0, []
    for a in doc.get("arms", []):
        if a.get("stopped"):
            continue
        ran = {i: [t for t in ts if t.get("passed") is not None] for i, ts in _by_id(a.get("tasks", [])).items()}
        ran = {i: ts for i, ts in ran.items() if ts}
        for tier in sorted({ts[0].get("tier") for ts in ran.values()}, key=str):
            ids = [ts for ts in ran.values() if ts[0].get("tier") == tier]
            k, n = sum(1 for ts in ids if all(t["passed"] for t in ts)), len(ids)
            cells += 1
            if not CAL_LO <= k / n <= CAL_HI:
                out.append(f"{a.get('role')} {a.get('agent')} t{tier} {k}/{n} ({round(100 * k / n)}%)")
    line = f"calibration {cells - len(out)}/{cells}"
    return line + (f" (out: {', '.join(out)})" if out else "")


def _budget_points(doc):
    """(points, None) of a run: last w5h_after minus first w5h_before; (None, why) when it can't be measured."""
    if doc is None:
        return None, "no run"
    arms = doc.get("arms", [])
    if any(a.get("stopped") for a in arms):
        return None, "an arm stopped"
    before = [a["w5h_before"] for a in arms if a.get("w5h_before") is not None]
    after = [a["w5h_after"] for a in arms if a.get("w5h_after") is not None]
    if not before or not after:
        return None, "no window reading (null w5h)"
    pts = after[-1] - before[0]
    if pts < 0:
        return None, "window reset during run"
    return pts, None


def _budget(docs):
    """(ok, line): newest light and full run each measured and below its BUDGET cap."""
    parts = []
    for suite, cap in BUDGET.items():
        p, why = _budget_points(_newest(docs, suite))
        if why is None and p >= cap:
            why = f"{p:.1f} over limit {cap}"
        parts.append((suite, why or f"{p:.1f}", why is None))
    ok = all(v for _, _, v in parts)
    if _newest(docs, "max") is not None:   # T24: reported, never a FAIL cause
        p, why = _budget_points(_newest(docs, "max"))
        parts.append(("max", why or f"{p:.1f}", True))
    return ok, f"budget {'OK' if ok else 'FAIL'} ({', '.join(f'{s} {x}' for s, x, _ in parts)} points)"


def _graders(docs):
    """(ok, line): failed records of the newest full and light runs by failure_kind; 'agent' is not counted."""
    parts, bad = [], 0
    for suite in ("full", "light") + (("max",) if _newest(docs, "max") else ()):   # T24: max when run
        doc = _newest(docs, suite)
        if doc is None:
            parts.append(f"{suite}: no run")
            continue
        kinds = [t.get("failure_kind") for a in doc.get("arms", []) for t in a.get("tasks", [])
                 if t.get("passed") is False]
        g, h = kinds.count("grader"), kinds.count("harness")
        u = sum(1 for k in kinds if not k)
        bad += g + h + u
        parts.append(f"{suite}: {g} grader, {h} harness, {u} unaudited")
    return bad == 0, f"graders {'OK' if bad == 0 else 'FAIL'} ({'; '.join(parts)})"


def _flips(tasks):
    """(f, n) of an arm's records when some id has >= 2 attempts: n ids, f ids whose attempts disagree; else None."""
    by = _by_id(tasks)
    if not any(len(ts) >= 2 for ts in by.values()):
        return None
    f = sum(1 for ts in by.values() if len({t["passed"] for t in ts if t.get("passed") is not None}) > 1)
    return f, len(by)


def _dscore(tasks):
    """T24: mean |difference| between scored attempts (pairwise) over ids with >= 2 of them; None when none."""
    per = []
    for ts in _by_id(tasks).values():
        s = [t["score"] for t in ts if t.get("score") is not None]
        if len(s) >= 2:
            per.append(statistics.mean(abs(x - y) for i, x in enumerate(s) for y in s[i + 1:]))
    return statistics.mean(per) if per else None


def _noise(docs, doc):
    """Noise lines per unstopped arm of the newest full run; repeats in that run, else the newest other doc of any
    date with repeats for the same agent (its date shown), else unknown. T24: then 'noise max <agent> ...' per
    unstopped arm of the newest max run, from that run's repeats only, with Δscore when scores exist."""
    lines = ["noise unknown (no full run)"] if doc is None else []
    mx = _newest(docs, "max")
    for a in (mx or {}).get("arms", []):
        if a.get("stopped"):
            continue
        fn, ds = _flips(a.get("tasks", [])), _dscore(a.get("tasks", []))
        line = f"noise max {a.get('agent')} " + (f"±{fn[0]}/{fn[1]}" if fn else "unknown")
        lines.append(line + (f" Δscore {ds:.2f}" if ds is not None else ""))
    if doc is None:
        return lines
    others = sorted((d for d in docs if d is not doc), key=lambda d: str(d.get("date")), reverse=True)
    full = []
    for a in doc.get("arms", []):
        if a.get("stopped"):
            continue
        agent, fn, when = a.get("agent"), _flips(a.get("tasks", [])), ""
        for d in others if fn is None else []:
            fn = next((x for b in d.get("arms", []) if b.get("agent") == agent
                       for x in [_flips(b.get("tasks", []))] if x), None)
            if fn:
                when = f" ({str(d.get('date'))[:10]})"
                break
        full.append(f"noise {agent} ±{fn[0]}/{fn[1]}{when}" if fn else f"noise {agent} unknown")
    return full + lines


def cmd_check(args):
    docs = _load_results(Path(args.results).resolve() if args.results else Path.cwd() / "bench" / "results")
    full = _newest(docs, "full")
    bud_ok, bud = _budget(docs)
    gr_ok, gr = _graders(docs)
    for line in [bud, gr, *_noise(docs, full), _calibration(full)]:
        print(line)
    return 0 if bud_ok and gr_ok else 1


KINDS = ("agent", "grader", "harness")


def cmd_audit(args):
    rdir = Path(args.results).resolve() if args.results else Path.cwd() / "bench" / "results"
    if args.file:
        path = Path(args.file).resolve()
    else:
        docs = []
        for p in sorted(rdir.glob("*.json")) if rdir.is_dir() else []:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and (args.latest is None or d.get("suite") == args.latest):
                docs.append((str(d.get("date")), p.name, p))
        path = max(docs)[2] if docs else None
    if path is None or not path.is_file():
        where = path or f"{args.latest or 'any'} suite in {rdir}"
        print(f"audit: no results JSON ({where})")
        return 2
    doc = json.loads(path.read_text(encoding="utf-8"))
    failed = [(a.get("role"), t) for a in doc.get("arms", []) for t in a.get("tasks", [])
              if t.get("passed") is False and (args.arm is None or a.get("role") == args.arm)]
    rep = (doc.get("repeat") or 1) >= 2   # 3.9.7 T5: attempt ids <arm>/<id>#a<i>; a format 1 record is attempt 1

    def ident(arm, t):
        return f"{arm}/{t.get('id')}" + (f"#a{t.get('attempt') or 1}" if rep else "")

    if args.set:
        key, kind = args.set
        hit = [t for arm, t in failed if key in (ident(arm, t), f"{arm}/{t.get('id')}")]
        if kind not in KINDS:
            print(f"audit: kind must be one of {'|'.join(KINDS)}")
        elif not args.why:
            print("audit: --set needs --why")
        elif not hit:
            print(f"audit: no failed task {key}")
        else:
            for t in hit:
                t.update(failure_kind=kind, audit_note=args.why)
            path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            print(f"set {key} {kind}")
            _ledger_import(path)   # the ledger failure_kind follows the JSON
    work = doc.get("work_dir")
    for arm, t in failed:
        kind = t.get("failure_kind")
        if args.unaudited and kind:
            continue
        print(f"{ident(arm, t)} [{kind or 'unaudited'}] {t.get('detail')}"
              + (f" -- {t['audit_note']}" if t.get("audit_note") else ""))
        if args.show:
            sfx = f".a{t.get('attempt') or 1}" if rep else ""
            out = Path(work) / arm / f"{t.get('id')}{sfx}.out.json" if work else None
            try:
                answer = str(json.loads(out.read_text(encoding="utf-8")).get("result") or "")
            except (OSError, ValueError, AttributeError):
                print("    (no retained answer)")
                continue
            for ln in answer.splitlines()[:args.show]:
                print(f"    {ln}")
    n = {k: sum(1 for _, t in failed if t.get("failure_kind") == k) for k in KINDS}
    un = sum(1 for _, t in failed if not t.get("failure_kind"))
    print(f"unaudited {un} · agent {n['agent']} · grader {n['grader']} · harness {n['harness']}")
    return 0


def cmd_readme(args):
    rdir = Path(args.results).resolve() if args.results else Path.cwd() / "bench" / "results"
    print(f"readme: {_write_readme(rdir)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bench.py", description="Agent bench (fixtures, plan, run).")
    ap.add_argument("--fixtures", metavar="DIR", help="fixture root (default: package, else pa3-src)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fx = sub.add_parser("fixtures", help="pin or check the fixture manifest")
    mode = fx.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pin", action="store_true", help="write MANIFEST.json")
    mode.add_argument("--check", action="store_true", help="compare files against MANIFEST.json")
    fx.set_defaults(func=cmd_fixtures)
    pl = sub.add_parser("plan", help="print the task count per arm of a suite or agent")
    pl.add_argument("target", metavar="light|full|canary|<agent>")
    pl.add_argument("--agents-dir", metavar="DIR", help="agent files (default: package agents/, else ./.claude/agents)")
    pl.add_argument("--preset", choices=("max20", "max5", "pro"), help="plan tier (default ./.claude/pa.json preset, else max20)")
    pl.set_defaults(func=cmd_plan)
    rn = sub.add_parser("run", help="run a suite or one agent's arm, write results JSON and ledger rows")
    rn.add_argument("target", nargs="?", default="light", metavar="light|full|canary|<agent>")
    rn.add_argument("--model", metavar="M", help="--model passed to claude")
    rn.add_argument("--effort", metavar="E", help="override the agent file's effort")
    rn.add_argument("--jobs", type=int, default=4, help="tasks in flight after an arm's first (default 4)")
    rn.add_argument("--force", action="store_true", help="run even when the window estimate is over the cap")
    rn.add_argument("--no-stop", action="store_true", help="no mid-run window stop")
    rn.add_argument("--out", metavar="JSON", help="results path (default ./bench/results/<date>.json)")
    rn.add_argument("--work-dir", metavar="DIR", help="clone root (default ~/pa3-bench)")
    rn.add_argument("--agents-dir", metavar="DIR", help="agent files (default: package agents/, else ./.claude/agents)")
    rn.add_argument("--no-rubric", action="store_true", help="skip the rubric side score (planner, critic, review)")
    rn.add_argument("--suite", choices=SUITES, default="light", help="suite for an <agent> target (default light)")
    rn.add_argument("--repeat", type=int, default=1, metavar="K", help="attempts per task (default 1)")
    rn.add_argument("--preset", choices=("max20", "max5", "pro"), help="plan tier (default ./.claude/pa.json preset, else max20)")
    rn.set_defaults(func=cmd_run)
    ab = sub.add_parser("ab", help="run two agents of one role on one suite; paired per-task table")
    ab.add_argument("a", metavar="agentA")
    ab.add_argument("b", metavar="agentB")
    ab.add_argument("suite", nargs="?", choices=SUITES, default="light")
    ab.add_argument("--repeat", type=int, default=1, metavar="K", help="attempts per task (default 1)")
    ab.add_argument("--jobs", type=int, default=4, help="attempts in flight after an arm's first (default 4)")
    ab.add_argument("--work-dir", metavar="DIR", help="clone root (default ~/pa3-bench)")
    ab.add_argument("--agents-dir", metavar="DIR", help="agent files (default: package agents/, else ./.claude/agents)")
    ab.add_argument("--results-dir", metavar="DIR", help="both results files land here (default ./bench/results)")
    ab.set_defaults(func=cmd_ab)
    pu = sub.add_parser("pull", help="import public results JSONs into the ledger (source=public)")
    pu.add_argument("--source", metavar="URL|DIR", help="results dir or one JSON URL (default bench.source)")
    pu.set_defaults(func=cmd_pull)
    cp = sub.add_parser("compare", help="installed agents vs the bench series: one verdict line each")
    cp.add_argument("--agents", metavar="DIR", help="installed agents (default ./.claude/agents)")
    cp.add_argument("--base", metavar="DIR", help="base agents for marked files (default package agents/)")
    cp.set_defaults(func=cmd_compare)
    ck = sub.add_parser("check", help="budget, graders, noise, calibration of the results; exit 1 on budget or graders FAIL")
    ck.add_argument("--results", metavar="DIR", help="results dir (default ./bench/results)")
    ck.set_defaults(func=cmd_check)
    au = sub.add_parser("audit", help="list failed tasks of a results JSON; classify one with --set")
    au.add_argument("file", nargs="?", metavar="results.json", help="results JSON (default: newest in --results)")
    au.add_argument("--latest", choices=("light", "full", "canary"), help="newest results JSON of this suite")
    au.add_argument("--results", metavar="DIR", help="results dir (default ./bench/results)")
    au.add_argument("--arm", metavar="A", help="only this arm (role)")
    au.add_argument("--unaudited", action="store_true", help="only failures without a failure_kind")
    au.add_argument("--show", type=int, metavar="N", help="first N lines of each retained answer")
    au.add_argument("--set", nargs=2, metavar=("ARM/ID", "KIND"), help="write failure_kind (agent|grader|harness)")
    au.add_argument("--why", default=None, help="audit_note written with --set")
    au.set_defaults(func=cmd_audit)
    rm = sub.add_parser("readme", help="regenerate README.md beside a results/ dir, else inside it")
    rm.add_argument("--results", metavar="DIR", help="results dir (default ./bench/results)")
    rm.set_defaults(func=cmd_readme)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
