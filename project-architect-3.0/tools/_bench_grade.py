#!/usr/bin/env python
"""_bench_grade.py -- graders for the agent bench (bench.py) and the fixture-root lookup it shares.

grade_coder(clone, task)       hidden tests copied into the clone's tests/, whole suite must pass.
grade_scored(clone, task)      T24 (suite max): expect.score_cmd's last 'score: <float>' line >= expect.pass_score.
grade_retriever(answer, key)   every expected path:line and token cited (3.9.7: suffix paths, line mentions).
grade_expert(brief, expect)    coder-brief fields, CHANGE <= 8 lines, exact BUILD/TEST, must_files named.
grade_planner(draft, expect)   plan_edit.py lint 0 ERROR, milestone clauses covered, files+verify per task.
grade_router(transcript, exp)  first Agent call's subagent_type and brief head, or end text (any token) with no Agent call.
grade_critic(answer, expect)   contract invariants (fields, DECISION/TIER values, EDITS grammar); reference reported only.
grade_review(answer, expect)   numbered decisions with `plan_edit.py <sub> ... --by developer` options, RECOMMENDED ids
                               exist and are listed first; reference reported only (3.9.7 T4).
rubric_prompt / rubric_score   the rubric side score (planner, critic, review): a grader model scores the answer
                               against rubric/<arm>.md; never the pass (3.9.7 T4).
Stdlib only.
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLED_ROOT = Path.home() / ".claude" / "pa3-src" / "project-architect-3.0" / "tests" / "fixtures" / "bench"
TAIL_CHARS = 2000
TEST_TIMEOUT = 300
LINE_SLACK = 2


def fixture_root(arg=None):
    """--fixtures DIR, else the package beside this script, else the installed pa3-src copy."""
    if arg:
        return Path(arg).resolve()
    beside = HERE.parent / "tests" / "fixtures" / "bench"
    return beside if beside.is_dir() else INSTALLED_ROOT


def grade_coder(clone, task, fixtures=None):
    """Copy hidden/<id>/test_*.py into clone/tests/ (overwrite), run the suite; (passed, output tail)."""
    clone = Path(clone)
    hidden = fixture_root(fixtures) / task["hidden"]
    tests = sorted(hidden.glob("test_*.py"))
    if not tests:
        return False, f"no hidden tests in {hidden}"
    (clone / "tests").mkdir(exist_ok=True)
    for src in tests:
        shutil.copyfile(src, clone / "tests" / src.name)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=clone,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {TEST_TIMEOUT}s"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out[-TAIL_CHARS:]


_SCORE = re.compile(r"^\s*score:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")


def grade_scored(clone, task, fixtures=None):
    """T24: run expect.score_cmd (argv; {clone} {fixtures} {id} substituted; cwd the fixture root); the last stdout
    line 'score: <float>' is the score; (score >= expect.pass_score (1.0), output tail, score). No score line, a
    non-zero exit or a timeout: (False, tail, None)."""
    exp, root = task.get("expect") or {}, fixture_root(fixtures)
    subs = {"{clone}": str(Path(clone)), "{fixtures}": str(root), "{id}": str(task.get("id"))}
    argv = []
    for a in exp["score_cmd"]:
        for k, v in subs.items():
            a = a.replace(k, v)
        argv.append(a)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {TEST_TIMEOUT}s", None
    tail = ((r.stdout or "") + (r.stderr or ""))[-TAIL_CHARS:]
    hits = [m.group(1) for m in map(_SCORE.match, (r.stdout or "").splitlines()) if m]
    if r.returncode != 0 or not hits:
        return False, tail, None
    score = float(hits[-1])
    return score >= float(exp.get("pass_score", 1.0)), tail, score


_CITE = re.compile(r"([\w./\-]+):(\d+)(?:\s*[-–]\s*(\d+))?")
# 3.9.7 T2: a line named apart from its path counts when a path was cited earlier in the same paragraph.
_PATH_CTX = re.compile(r"[\w./\-]*[\w-]\.(?:py|md|json|txt|toml|cfg|ini|ya?ml|sh)\b")
_LINE_MENTION = re.compile(r"(?:\blines?|\bl\.)\s*(\d+)(?:\s*[-–]\s*(\d+))?", re.I)
_BARE_LINE = re.compile(r"(?<![\w:]):(\d+)(?:\s*[-–]\s*(\d+))?")


def _span(lo, hi):
    lo = int(lo)
    hi = int(hi) if hi else lo
    return min(lo, hi), max(lo, hi)


def _citations(answer):
    """[(posix path, first line, last line)] for every path:N or path:N-M in the answer, plus `line N`,
    `lines N-M`, `(line N)`, `l. N` or a bare `:N` after the last cited path in the same paragraph."""
    out = []
    for para in re.split(r"\n[ \t]*\n", answer):
        events, taken = [], []
        for m in _CITE.finditer(para):
            taken.append(m.span())
            events.append((m.start(), m.group(1).replace("\\", "/"), *_span(m.group(2), m.group(3))))
        free = lambda m: not any(a <= m.start() < b for a, b in taken)  # noqa: E731
        for m in _PATH_CTX.finditer(para):
            if free(m):
                events.append((m.start(), m.group(0).replace("\\", "/"), None, None))
        for rx in (_LINE_MENTION, _BARE_LINE):
            for m in rx.finditer(para):
                if free(m):
                    events.append((m.start(), None, *_span(m.group(1), m.group(2))))
        last = None
        for _, path, lo, hi in sorted(events, key=lambda e: e[0]):
            if path is not None:
                last = path
                if lo is not None:
                    out.append((path, lo, hi))
            elif last is not None:
                out.append((last, lo, hi))
    return out


def _norm_path(path):
    for prefix in ("./", "repo/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def _path_match(cited, want):
    """Equal, or either path ends with "/" + the other (a leading ./ or repo/ stripped)."""
    cited, want = _norm_path(cited), _norm_path(want)
    return cited == want or cited.endswith("/" + want) or want.endswith("/" + cited)


def _covers(lo, hi, line, to=None):
    """A cite lo-hi (lo == hi for one line) covers line..to (to = the def's last line, else line)
    when the two spans overlap within ±LINE_SLACK."""
    to = line if to is None else to
    return lo - LINE_SLACK <= to and line <= hi + LINE_SLACK


def grade_retriever(answer, key):
    """key = task["expect"]: refs [{path, line, to?, symbol}], tokens [...]; other keys ignored."""
    cites = _citations(answer)
    problems = []
    for ref in key.get("refs", []):
        to = ref.get("to")
        if not any(_path_match(p, ref["path"]) and _covers(lo, hi, ref["line"], to) for p, lo, hi in cites):
            where = f"{ref['line']}-{to}" if to is not None else f"{ref['line']}"
            problems.append(f"missing ref {ref['path']}:{where} ({ref.get('symbol', '')})")
    for tok in key.get("tokens", []):
        if tok not in answer:
            problems.append(f"missing token {tok!r}")
    return (not problems), ("; ".join(problems) if problems else "all refs and tokens present")


def _result(problems, ok_text):
    return (not problems), ("; ".join(problems) if problems else ok_text)


def _as_list(value):
    return [value] if isinstance(value, str) else list(value or [])


# --------------------------------------------------------------------------- expert
BRIEF_FIELDS = ("CODER TASK", "CHANGE", "INTERFACES", "CONSTRAINTS", "BUILD/TEST", "DONE WHEN", "LOG", "RETURN")
CHANGE_MAX_LINES = 8


def _fields(text, names=BRIEF_FIELDS):
    """{FIELD: [lines]} for a contract whose fields start lines (`FIELD: value`, or `**FIELD**` then lines)."""
    pat = re.compile(r"^[\s#*>`-]*(%s)\b[*`]*\s*:?\s*(.*)$" % "|".join(re.escape(f) for f in names))
    out, cur = {}, None
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            rest = m.group(2).strip()
            if rest:
                out[cur].append(rest)
        elif cur is not None and line.strip() and line.strip() != "```":
            out[cur].append(line.strip())
    return out


def _repo_paths(text, roots):
    """Sorted repo-relative paths under `roots` named in text (a leading ./ or repo/ dropped)."""
    if not roots:
        return []
    alt = "|".join(re.escape(r) for r in sorted(roots))
    pat = re.compile(r"(?<![\w.-])(?:\./|repo/)?((?:%s)/[\w./-]*\.\w+)" % alt)
    return sorted({m.group(1) for m in pat.finditer(text.replace("\\", "/"))})


def grade_expert(brief, expect):
    """expect: files (fnmatch patterns, a note only), must_files, build_test (verbatim inside BUILD/TEST)."""
    f = _fields(brief)
    problems = [f"missing {name}" for name in ("CODER TASK", "CHANGE", "INTERFACES", "BUILD/TEST", "DONE WHEN")
                if name not in f]
    change = f.get("CHANGE", [])
    if "CHANGE" in f and not 1 <= len(change) <= CHANGE_MAX_LINES:
        problems.append(f"CHANGE has {len(change)} lines (1-{CHANGE_MAX_LINES})")
    if "BUILD/TEST" in f and expect["build_test"] not in " ".join(f["BUILD/TEST"]):
        problems.append(f"BUILD/TEST lacks {expect['build_test']!r}")
    allowed = expect.get("files", [])
    roots = {p.split("/")[0] for p in allowed + expect.get("must_files", []) if "/" in p}
    named = _repo_paths(brief, roots)
    # 3.9.7 T3: the files bound is a note, never a problem (the consequence stage judges the change).
    notes = [f"names {p} outside expect.files" for p in named
             if not any(fnmatch.fnmatchcase(p, pat) for pat in allowed)]
    problems += [f"does not name {p}" for p in expect.get("must_files", []) if p not in named]
    passed, detail = _result(problems, f"brief ok ({len(change)} change lines, {len(named)} files)")
    return passed, "; ".join([detail] + notes)


# --------------------------------------------------------------------------- planner
LINT_TIMEOUT = 60
EMPTY = ("", "-", "—", "none")


def grade_planner(draft_path, expect):
    """expect: milestone [{clause, tokens}] -> (passed, detail); the rubric side score is rubric_prompt's (T4)."""
    draft_path = Path(draft_path).resolve()
    try:
        text = draft_path.read_text(encoding="utf-8")
    except OSError:
        return False, f"no draft at {draft_path}"
    problems = []
    try:
        r = subprocess.run([sys.executable, str(HERE / "plan_edit.py"), "lint", str(draft_path)],
                           cwd=draft_path.parent, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=LINT_TIMEOUT)
        out = (r.stdout or "") + (r.stderr or "")
        errors = [ln for ln in out.splitlines() if ln.startswith("ERROR")]
        if errors or r.returncode:
            problems.append("lint: " + (" / ".join(errors[:3]) or out.strip()[-200:]))
    except subprocess.TimeoutExpired:
        problems.append("lint timeout")
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import plan_edit
    tasks = plan_edit.parse(text)[1]
    if not tasks:
        problems.append("no task lines")
    for t in tasks:
        problems += [f"{t.id} has no {key}" for key in ("files", "verify") if t.get(key).strip() in EMPTY]
    fields = [(t.get("done-when") + " " + t.get("verify")).lower() for t in tasks]
    for c in expect.get("milestone", []):
        toks = [tok.lower() for tok in c["tokens"]]
        if not any(all(tok in f for tok in toks) for f in fields):
            problems.append(f"clause uncovered: {' + '.join(c['tokens'])}")
    return _result(problems, f"lint ok, {len(tasks)} tasks, {len(expect.get('milestone', []))} clauses covered")


# --------------------------------------------------------------------------- router
AGENT_TOOLS = ("Agent", "Task")


def _transcript(path):
    """(Agent call inputs, assistant texts) from a Claude Code session .jsonl, in order."""
    calls, texts = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            msg = row.get("message") if isinstance(row, dict) else None
            if not isinstance(msg, dict) or (row.get("type") != "assistant" and msg.get("role") != "assistant"):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                texts.append(content)
                continue
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use" and block.get("name") in AGENT_TOOLS:
                    calls.append(block.get("input") or {})
                elif block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                    texts.append(json.dumps(block.get("input"), ensure_ascii=False))
    return calls, texts


def _head_match(line, head):
    if line.startswith(head):
        return True
    try:
        return re.search(head, line) is not None
    except re.error:
        return False


def grade_router(transcript, expect):
    """expect: {subagent_type (str|list), brief_head?} for the first Agent call, or {no_agent, end_text_any}."""
    try:
        calls, texts = _transcript(transcript)
    except OSError:
        return False, f"no transcript at {transcript}"
    problems = []
    if expect.get("no_agent"):
        if calls:
            problems.append(f"spawned {calls[0].get('subagent_type')!r}, expected no Agent call")
        said = "\n".join(texts).lower()
        want = expect.get("end_text_any", [])
        if want and not any(tok.lower() in said for tok in want):
            problems.append(f"missing text, none of {want!r}")
        return _result(problems, "no Agent call, end text ok")
    if not calls:
        return False, "no Agent call"
    first = calls[0]
    got = first.get("subagent_type")
    if got not in _as_list(expect["subagent_type"]):
        problems.append(f"subagent_type {got!r}, expected {expect['subagent_type']!r}")
    head = expect.get("brief_head")
    if head:
        lines = [ln.strip() for ln in str(first.get("prompt", "")).splitlines() if ln.strip()]
        if not lines or not _head_match(lines[0], head):
            problems.append(f"brief head {(lines[0][:60] if lines else '')!r} !~ {head!r}")
    return _result(problems, f"first Agent call {got}")


# --------------------------------------------------------------------------- critic
CRITIC_FIELDS = ("DECISION", "TIER", "EDITS", "WHY", "BRIEF")
CRITIC_DECISIONS = ("continue", "edit", "needs-developer")
CRITIC_TIERS = ("additive", "subtractive", "milestone")
PLAN_SUBCOMMANDS = ("set-status", "reopen", "add-task", "append-change")   # plan_edit.py's plan-change grammar
_PLAN_CMD = re.compile(r"plan_edit\.py\s+([\w-]+)")


def _value(lines):
    return re.sub(r"[`*\s]", "", lines[0]).lower() if lines else ""


def _subcommand(line):
    """The token after `plan_edit.py` ('' when none)."""
    m = _PLAN_CMD.search(line)
    return m.group(1) if m else ""


def _alts(value):
    return "|".join(_as_list(value))


def _with_ref(problems, ok_text, notes):
    passed, detail = _result(problems, ok_text)
    return passed, "; ".join([detail] + notes) if notes else detail


def grade_critic(answer, expect):
    """Pass = the critic contract's invariants; expect.reference {decision, tier, edits_must} is reported, never gates."""
    f = _fields(answer, CRITIC_FIELDS)
    problems = [f"missing {name}" for name in ("DECISION", "TIER", "EDITS", "WHY") if name not in f]
    decision, tier = _value(f.get("DECISION", [])), _value(f.get("TIER", []))
    if "DECISION" in f and decision not in CRITIC_DECISIONS:
        problems.append(f"DECISION {decision!r} not one of {CRITIC_DECISIONS}")
    if "TIER" in f and tier not in CRITIC_TIERS:
        problems.append(f"TIER {tier!r} not one of {CRITIC_TIERS}")
    commands = [ln for ln in f.get("EDITS", []) if "plan_edit.py" in ln]
    problems += [f"EDITS unknown subcommand {_subcommand(ln)!r}" for ln in commands
                 if _subcommand(ln) not in PLAN_SUBCOMMANDS]
    if decision == "edit" and not commands:
        problems.append("edit with no plan_edit.py line in EDITS")
    if decision == "continue" and commands:
        problems.append("continue with a plan_edit.py line in EDITS")
    if decision == "needs-developer" and "Recommended:" not in " ".join(f.get("BRIEF", [])):
        problems.append("BRIEF lacks 'Recommended:'")
    if "WHY" in f and not f["WHY"]:
        problems.append("WHY empty")
    ref = expect.get("reference", {})
    notes = []
    if ref:
        notes.append(f"ref {_alts(ref.get('decision'))}/{_alts(ref.get('tier'))} · got {decision}/{tier}")
        joined = "\n".join(commands)
        notes += [f"ref edits lack {tok!r}" for tok in ref.get("edits_must", []) if tok not in joined]
    return _with_ref(problems, f"{decision} / {tier}", notes)


# --------------------------------------------------------------------------- review
_DECISION = re.compile(r"^\s{0,3}(\d+)\.\s+\S")
_OPTION = re.compile(r"^\s*([a-z])[.)]\s")
_RECOMMENDED = re.compile(r"^[\s#*>`]*RECOMMENDED\b[*`]*\s*:(.*)$", re.I)
_OPTION_ID = re.compile(r"\b(\d+)\s*([a-z])\b")


def _developer_command(text):
    """True when an option carries `plan_edit.py <grammar subcommand> ... --by developer`."""
    return _subcommand(text) in PLAN_SUBCOMMANDS and "--by developer" in text[text.index("plan_edit.py"):]


def grade_review(answer, expect):
    """Pass = >= min_decisions decisions, each with a `plan_edit.py <sub> ... --by developer` option; RECOMMENDED ids
    exist and each is its decision's first option. expect.reference {recommended, commands, first_option}: reported."""
    blocks, cur, rec = {}, None, None
    for line in answer.splitlines():
        m = _RECOMMENDED.match(line)
        if m:
            rec, cur = m.group(1), None
            continue
        m = _DECISION.match(line)
        if m:
            cur = m.group(1)
            blocks[cur] = [line]
        elif cur is not None and line.strip():
            blocks[cur].append(line)
    options = {}   # {n: [(letter, text)]}, continuation lines joined to their option
    for n, block in blocks.items():
        opts = options.setdefault(n, [])
        for line in block[1:]:
            m = _OPTION.match(line)
            if m:
                opts.append((m.group(1), line.strip()))
            elif opts:
                opts[-1] = (opts[-1][0], opts[-1][1] + " " + line.strip())
    problems = []
    want = expect.get("min_decisions", 1)
    if len(blocks) < want:
        problems.append(f"{len(blocks)} decisions, expected >= {want}")
    problems += [f"decision {n} has no `plan_edit.py <subcommand> ... --by developer` option"
                 for n, opts in options.items() if not any(_developer_command(text) for _, text in opts)]
    ids = []
    if rec is None:
        problems.append("no RECOMMENDED line")
    else:
        ids = _OPTION_ID.findall(rec.lower())
        if not ids:
            problems.append(f"RECOMMENDED names no option: {rec.strip()!r}")
        for n, letter in ids:
            letters = [ltr for ltr, _ in options.get(n, [])]
            if letter not in letters:
                problems.append(f"RECOMMENDED {n}{letter}: no such option")
            elif letters[0] != letter:
                problems.append(f"RECOMMENDED {n}{letter} is not listed first in decision {n}")
    ref = expect.get("reference", {})
    notes = []
    if ref:
        got = ", ".join(n + ltr for n, ltr in ids) or "-"
        notes.append(f"ref {', '.join(ref.get('recommended', [])) or '-'} · got {got}")
        body = "\n".join(ln for b in blocks.values() for ln in b)
        notes += [f"ref command {tok!r} absent" for tok in ref.get("commands", []) if tok not in body]
        for n, tok in ref.get("first_option", {}).items():
            first = options[n][0][1] if options.get(n) else ""
            if tok not in first:
                notes.append(f"ref decision {n} first option lacks {tok!r}")
    return _with_ref(problems, f"{len(blocks)} decisions, recommended {rec.strip() if rec else '-'}", notes)


# --------------------------------------------------------------------------- rubric
RUBRIC_TIMEOUT = 600
RUBRIC_TOOLS_OFF = "Read,Edit,Write,Bash,Grep,Glob,Agent,WebFetch,WebSearch"
_ITEM_ID = re.compile(r"^[SCR]\d+\s", re.M)
_TOTAL = re.compile(r"(?:\|\s*)?\btotal\s+(-?\d+(?:\.\d+)?)", re.I)
_SLOT = re.compile(r"\{(rubric|items|material|reference|answer)\}")


def rubric_prompt(arm, task, answer, fixtures=None):
    """(prompt, max) for the grader: rubric/grader-prompt.txt with {rubric}, {items} (numbered expect.rubric_items
    or "none"), {material} (the task prompt, plus plan/*.md for planner, task["files"] overriding), {reference} (<id>.ref.md), {answer}.
    max = the rubric's item lines (S1…, C1…, R1…) + len(rubric_items). Names no arm, agent or model."""
    root = fixture_root(fixtures)
    rubric = (root / (task.get("rubric") or f"rubric/{arm}.md")).read_text(encoding="utf-8", errors="replace")
    template = (root / "rubric" / "grader-prompt.txt").read_text(encoding="utf-8")
    items = task.get("expect", {}).get("rubric_items", [])
    material = task.get("prompt", "")
    if arm == "planner":   # a task["files"] entry replaces the plan/*.md of the same basename (p07-p12: plan2)
        files = {p.name: p for p in sorted((root / "plan").glob("*.md"))}
        files.update({(root / f).name: root / f for f in task.get("files", [])})
        material += "".join(f"\n\n### {name}\n{p.read_text(encoding='utf-8', errors='replace')}"
                            for name, p in sorted(files.items()))
    ref = root / "tasks" / arm / f"{task['id']}.ref.md"
    values = {"rubric": rubric.strip(),
              "items": "\n".join(f"P{i} {it}" for i, it in enumerate(items, 1)) or "none",
              "material": material.strip(),
              "reference": ref.read_text(encoding="utf-8", errors="replace").strip() if ref.is_file() else "none",
              "answer": str(answer).strip()}
    prompt = _SLOT.sub(lambda m: values[m.group(1)], template)   # one pass: a filled value is never re-filled
    return prompt, len(_ITEM_ID.findall(rubric)) + len(items)


def rubric_score(prompt, grader, claude, cwd):
    """-> (score|None, max, {line, cost_usd, model, error}): one `claude -p` grader call, tools off, in the empty
    dir cwd (no CLAUDE.md above it); the score is `total <n>` / `| total <n>` on the result's first line.
    max is grader["max"] (the driver passes dict(grader, max=n)); never --agent, never --bare."""
    cmd = [claude, "-p", "--model", grader["model"], "--effort", grader["effort"], "--permission-mode", "acceptEdits",
           "--permission-prompts", "none", "--disallowedTools", RUBRIC_TOOLS_OFF, "--output-format", "json"]
    mx = grader.get("max")
    detail = {"line": None, "cost_usd": None, "model": grader["model"], "error": None}
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(PA_HOOKS_OFF="1", PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(cmd, input=prompt, cwd=cwd, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=RUBRIC_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        detail["error"] = f"grader call failed: {exc!r}"
        return None, mx, detail
    try:
        out = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        out = None
    if not isinstance(out, dict):
        detail["error"] = f"no JSON (exit {r.returncode}): {(r.stderr or r.stdout or '')[-200:]}"
        return None, mx, detail
    mu = out.get("modelUsage") or {}
    if mu:
        detail["model"] = max(sorted(mu), key=lambda k: (mu[k] or {}).get("costUSD") or 0)
    detail["cost_usd"] = out.get("total_cost_usd")
    lines = [ln.strip() for ln in str(out.get("result") or "").splitlines() if ln.strip()]
    detail["line"] = lines[0] if lines else ""
    m = _TOTAL.search(detail["line"])
    if out.get("is_error") or not m:
        detail["error"] = f"is_error {out.get('subtype')}" if out.get("is_error") else "no `total <n>` on line 1"
        return None, mx, detail
    return float(m.group(1)), mx, detail
