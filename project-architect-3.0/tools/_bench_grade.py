#!/usr/bin/env python
"""_bench_grade.py -- graders for the agent bench (bench.py) and the fixture-root lookup it shares.

grade_coder(clone, task)       hidden tests copied into the clone's tests/, whole suite must pass.
grade_scored(clone, task)      T24 (suite max): expect.score_cmd's last 'score: <float>' line >= expect.pass_score.
grade_retriever(answer, key)   every expected path:line and token cited (3.9.7: suffix paths, line mentions).
grade_expert(brief, expect)    coder-brief fields, CHANGE <= 8 lines, exact BUILD/TEST, must_files named.
grade_partner(transcript, expect)  3.27 T6: the first Agent call's subagent_type and `[effort <level>]` hint, or none.
3.24: the planner, router, critic and review graders and the rubric side score left with their arms.
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


_CITE = re.compile(r"([\w./\-]+):(\d+)(?:\s*[-–]\s*(\d+))?((?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?(?!\w))*)")
# 2026-09-29 T2: the comma tail of a cite (`path:45,56`, `path:11-14, 19-20`), one item each.
_CITE_ITEM = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?")
# 3.9.7 T2: a line named apart from its path counts when a path was cited earlier in the same paragraph.
_PATH_CTX = re.compile(r"[\w./\-]*[\w-]\.(?:py|md|json|txt|toml|cfg|ini|ya?ml|sh)\b")
_LINE_MENTION = re.compile(r"(?:\blines?|\bl\.)\s*(\d+)(?:\s*[-–]\s*(\d+))?", re.I)
_BARE_LINE = re.compile(r"(?<![\w:]):(\d+)(?:\s*[-–]\s*(\d+))?")


def _span(lo, hi):
    lo = int(lo)
    hi = int(hi) if hi else lo
    return min(lo, hi), max(lo, hi)


def _citations(answer):
    """[(posix path, first line, last line)] for every path:N or path:N-M in the answer, each item of a
    comma list continuing it (`path:45,56`, `path:11-14, 19-20`), plus `line N`,
    `lines N-M`, `(line N)`, `l. N` or a bare `:N` after the last cited path in the same paragraph."""
    out = []
    for para in re.split(r"\n[ \t]*\n", answer):
        events, taken = [], []
        for m in _CITE.finditer(para):
            taken.append(m.span())
            path = m.group(1).replace("\\", "/")
            events.append((m.start(), path, *_span(m.group(2), m.group(3))))
            for k in _CITE_ITEM.finditer(m.group(4)):
                events.append((m.start(4) + k.start(), path, *_span(k.group(1), k.group(2))))
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



# --------------------------------------------------------------------------- partner
# 3.27 T6: revived from 3.24's router grader (git ebe35267^ grade_router); the deny-agent hook records the spawn
AGENT_TOOLS = ("Agent", "Task")
_HINT = re.compile(r"\s*\[effort (low|medium|high|xhigh|max|deep)\]\s*$")   # mod/pa-watch/hooks/ladder.ts HINT


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
    return calls, texts


def grade_partner(transcript, expect):
    """expect: {subagent_type (str|list), effort? (str|list; the description's trailing hint, "" = none)} for the
    first Agent call, or {no_agent, end_text_any?}."""
    if transcript is None:
        return False, "no transcript"
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
    got = calls[0].get("subagent_type")
    m = _HINT.search(str(calls[0].get("description") or ""))
    level = m.group(1) if m else ""
    if got not in _as_list(expect["subagent_type"]):
        problems.append(f"subagent_type {got!r}, expected {expect['subagent_type']!r}")
    if "effort" in expect and level not in _as_list(expect["effort"]):
        problems.append(f"effort hint {level or 'none'!r}, expected {expect['effort']!r}")
    return _result(problems, f"first Agent call {got}" + (f" [effort {level}]" if level else ""))
