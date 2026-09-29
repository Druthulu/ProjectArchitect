"""Layout detection for ``pa_install.py --project`` (design B J.2, step P1).

Public:
    LAYOUTS, MIGRATION, LABEL
    detect(repo)        -> "fresh" | "pa3" | "stock20" | "onex" | "overlay"
    signals(repo)       the evidence behind that verdict (printed by P1)
    evidence(sig, lay)  one line naming what decided it
    inventory(repo)     the counts P1 shows before asking for confirmation
    environment()       -> dict of runtime facts for the card
    legacy_skills(repo) PA3 skill dirs under an older name (pruned on refresh)

The five layouts, in the order they are tested (most specific first):

``pa3``      ``.claude/pa.json``, or the installed agents + the PA3 skill
             (``project-architect``, or a legacy PA3 skill under an older name).
             Upgrade path: refresh the packaged files, touch nothing else.
``overlay``  an overlay kit beside the project: a ``*-architect``/``*-kit`` folder
             carrying its own ``agents/``/``commands/``, or ``DIGEST.md``, or
             R-numbered rules past 100 (bfm-decomp).
``stock20``  stock Project Architect 2.0: ``RULES_REGISTRY.md`` at the root **and**
             a ``phase-ends/`` directory (TerrainDiffusion7DTD).
``onex``     1.x-shaped: a ``Project Context Markdowns``-style folder, a
             ``*Rules_Registry*.md``/``*Project_Context*.md`` under a project prefix,
             or PhaseEnd files living outside ``phase-ends/`` (Vantage).
``fresh``    none of the above.

Only ``fresh`` and ``pa3`` are executed today; the three migration layouts are
recognised so the installer can refuse cleanly instead of half-installing over a
2.0 tree (M6 implements them).

The walk is bounded (``MAX_DEPTH`` levels, ``MAX_ENTRIES`` entries, VCS/build
directories skipped) so classification stays fast on a large repository.
"""

import glob as _glob
import os
import platform
import re
import shutil
import subprocess
import sys

LAYOUTS = ("fresh", "pa3", "stock20", "onex", "overlay")
MIGRATION = ("stock20", "onex", "overlay")
LABEL = {
    "fresh": "fresh repository",
    "pa3": "Project Architect 3.0 (upgrade)",
    "stock20": "stock Project Architect 2.0",
    "onex": "1.x-shaped Project Architect",
    "overlay": "overlay kit",
}

SKIP_DIRS = frozenset((
    ".git", ".hg", ".svn", ".run", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".gradle",
    "dist", "build", "target", "out", "bin", "obj", ".idea", ".vscode", ".next",
    "site-packages", ".claude-state",
))
MAX_DEPTH = 3
MAX_ENTRIES = 20000

# A registry/context *template* or an already-archived copy proves nothing about
# the layout -- only a live document at a live path does.
INERT_TOPS = frozenset(("templates", ".claude", "corpus"))
INERT_PREFIXES = ("docs/retired/", "docs/corpus/", "docs/research-archive/")
INERT_MARKS = (".template.", ".skeleton.", ".seed.", ".example.", ".sample.")

STOCK_REGISTRY = "RULES_REGISTRY.md"
STOCK_CONTEXT = "PROJECT_CONTEXT.md"

_REGISTRY_RE = re.compile(r"rules[ _-]?registry", re.I)
_CONTEXT_RE = re.compile(r"project[ _-]?context", re.I)

SKILL_NAME = "project-architect"
# a PA3 skill is known by its frontmatter description, not its directory name
SKILL_DESC = "Standing rules, contracts and house style for Project Architect 3.0"
_PHASEEND_RE = re.compile(r"^phase[ _-]?end[_ -].*\.md$", re.I)   # not *-README/*.template
_RRULE_RE = re.compile(r"^R(\d{1,4})\.md$")
_KIT_RE = re.compile(r"[-_](architect|kit)$", re.I)


def _live(rel, name):
    """False for a template/seed/archived copy: only a live document classifies."""
    if rel.split("/")[0] in INERT_TOPS or rel.startswith(INERT_PREFIXES):
        return False
    lowered = name.lower()
    return not any(mark in lowered for mark in INERT_MARKS)


def _walk(repo):
    """``(rel_posix, name, is_dir)`` for every entry worth classifying on."""
    seen = 0
    stack = [(repo, "", 0)]
    while stack:
        path, rel, depth = stack.pop()
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            continue
        for name in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                return
            full = os.path.join(path, name)
            child = "%s/%s" % (rel, name) if rel else name
            isdir = os.path.isdir(full)
            if isdir and name in SKIP_DIRS:
                continue
            yield child, name, isdir
            if isdir and depth + 1 < MAX_DEPTH:
                stack.append((full, child, depth + 1))


def legacy_skills(repo):
    """Names of ``.claude/skills/<d>/`` dirs, ``d != SKILL_NAME``, holding a PA3 skill."""
    base = os.path.join(repo, ".claude", "skills")
    found = []
    if not os.path.isdir(base):
        return found
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "SKILL.md")
        if name == SKILL_NAME or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        lines = head.splitlines()
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.startswith("description:") and                     line[len("description:"):].strip().startswith(SKILL_DESC):
                found.append(name)
                break
    return found


def signals(repo):
    """Every fact the five rules are decided on (cheap, one bounded walk)."""
    repo = os.path.abspath(repo)
    join = os.path.join
    sig = {
        "repo": repo,
        "pa_json": os.path.isfile(join(repo, ".claude", "pa.json")),
        "pa3_agents": (os.path.isfile(join(repo, ".claude", "agents", "router.md"))
                       and (os.path.isfile(join(repo, ".claude", "skills",
                                                SKILL_NAME, "SKILL.md"))
                            or bool(legacy_skills(repo)))),
        "digest": os.path.isfile(join(repo, "DIGEST.md")),
        "registry": os.path.isfile(join(repo, STOCK_REGISTRY)),
        "phase_ends_dir": os.path.isdir(join(repo, "phase-ends")),
        "settings": os.path.isfile(join(repo, ".claude", "settings.json")),
        "kit_dirs": [],
        "registry_like": [],
        "context_like": [],
        "loose_phaseends": [],
        "phase_ends": 0,
        "rules": 0,
        "high_r_rules": 0,
        "md_files": 0,
    }

    for rel, name, isdir in _walk(repo):
        top = rel.split("/")[0]
        if isdir:
            if _KIT_RE.search(name) and (
                    "/" not in rel
                    or os.path.isdir(join(repo, rel, "agents"))
                    or os.path.isdir(join(repo, rel, "commands"))):
                sig["kit_dirs"].append(rel)
            elif "/" not in rel and (_CONTEXT_RE.search(name) or _REGISTRY_RE.search(name)):
                sig["context_like"].append(rel)
            continue

        if name.endswith(".md"):
            sig["md_files"] += 1
        if _PHASEEND_RE.match(name):
            if top == "phase-ends":
                sig["phase_ends"] += 1
            else:
                sig["loose_phaseends"].append(rel)
        if name.endswith(".md") and _live(rel, name):
            if name != STOCK_REGISTRY and _REGISTRY_RE.search(name):
                sig["registry_like"].append(rel)
            elif name != STOCK_CONTEXT and _CONTEXT_RE.search(name):
                sig["context_like"].append(rel)
        if top == "rules" and name.endswith(".md") and name != "INDEX.md":
            sig["rules"] += 1
            m = _RRULE_RE.match(name)
            if m and int(m.group(1)) > 100:
                sig["high_r_rules"] += 1

    for key in ("kit_dirs", "registry_like", "context_like", "loose_phaseends"):
        sig[key] = sorted(set(sig[key]))
    return sig


def detect(repo):
    """Classify ``repo`` as one of :data:`LAYOUTS` (most specific rule wins)."""
    return classify(signals(repo))


def classify(sig):
    """The verdict for an already-computed :func:`signals` mapping."""
    if sig["pa_json"] or sig["pa3_agents"]:
        return "pa3"
    if sig["kit_dirs"] or sig["digest"] or sig["high_r_rules"]:
        return "overlay"
    if sig["registry"] and sig["phase_ends_dir"]:
        return "stock20"
    if sig["registry_like"] or sig["context_like"] or len(sig["loose_phaseends"]) >= 2:
        return "onex"
    return "fresh"


def evidence(sig, layout=None):
    """One line naming what decided the verdict (for the P1 table)."""
    layout = layout or classify(sig)
    if layout == "pa3":
        return ".claude/pa.json" if sig["pa_json"] else ".claude/agents + PA3 skill"
    if layout == "overlay":
        bits = []
        if sig["kit_dirs"]:
            bits.append("kit " + ", ".join(sig["kit_dirs"][:2]))
        if sig["digest"]:
            bits.append("DIGEST.md")
        if sig["high_r_rules"]:
            bits.append("%d R-rules past R100" % sig["high_r_rules"])
        return "; ".join(bits)
    if layout == "stock20":
        return "%s + phase-ends/ (%d PhaseEnd file(s))" % (STOCK_REGISTRY, sig["phase_ends"])
    if layout == "onex":
        bits = []
        for key, what in (("registry_like", "registry"), ("context_like", "context")):
            if sig[key]:
                bits.append("%s %s" % (what, sig[key][0]))
        if len(sig["loose_phaseends"]) >= 2:
            bits.append("%d PhaseEnd file(s) outside phase-ends/"
                        % len(sig["loose_phaseends"]))
        return "; ".join(bits)
    return "no 2.0/1.x/kit markers found"


def inventory(repo):
    """``(layout, signals, [(label, value)])`` -- the P1 confirmation table rows."""
    sig = signals(repo)
    layout = classify(sig)
    rows = [
        ("layout", "%s -- %s" % (LABEL[layout], evidence(sig, layout))),
        ("markdown", "%d file(s) in the first %d levels" % (sig["md_files"], MAX_DEPTH)),
    ]
    if sig["phase_ends"] or sig["loose_phaseends"]:
        rows.append(("phase ends", "%d in phase-ends/, %d elsewhere"
                     % (sig["phase_ends"], len(sig["loose_phaseends"]))))
    if sig["rules"]:
        rows.append(("rules", "%d file(s) in rules/" % sig["rules"]))
    rows.append((".claude", "settings.json %s, pa.json %s"
                 % ("present" if sig["settings"] else "absent",
                    "present" if sig["pa_json"] else "absent")))
    return layout, sig, rows

# --------------------------------------------------------------------------- environment


def _git_version():
    """Return the ``git --version`` string or ``""`` (5 s timeout)."""
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _shells():
    """List of shell names available on PATH."""
    found = []
    for name in ("bash", "pwsh", "powershell", "zsh"):
        if shutil.which(name):
            found.append(name)
    return found


def _build_test_run(root):
    """Guess build, test and run commands from file signals in *root*.

    Returns ``(build_guess, test_guess, run_guess)``.
    """
    join = os.path.join
    isfile = os.path.isfile

    # Python: pyproject.toml or setup.py
    if isfile(join(root, "pyproject.toml")) or isfile(join(root, "setup.py")):
        # prefer pytest when a pyproject.toml mentions it, else unittest
        test = "python -m pytest"
        if isfile(join(root, "pyproject.toml")):
            try:
                with open(join(root, "pyproject.toml"), "r", encoding="utf-8",
                          errors="replace") as fh:
                    if "pytest" not in fh.read():
                        test = "python -m unittest"
            except OSError:
                pass
        elif not isfile(join(root, "pyproject.toml")):
            test = "python -m unittest"
        return "", test, ""

    # Node: package.json
    if isfile(join(root, "package.json")):
        return "npm run build", "npm test", ""

    # Make
    if isfile(join(root, "Makefile")):
        return "make", "make test", ""

    # .NET: *.sln or *.csproj
    if _glob.glob(join(root, "*.sln")) or _glob.glob(join(root, "*.csproj")):
        return "dotnet build", "dotnet test", ""

    # Rust: Cargo.toml
    if isfile(join(root, "Cargo.toml")):
        return "cargo build", "cargo test", ""

    # Go: go.mod
    if isfile(join(root, "go.mod")):
        return "go build ./...", "go test ./...", ""

    return "", "", ""


def environment(root=None):
    """Runtime facts for the card's Environment and Build / run / test sections.

    Returns ``dict{os, shells, interpreter, py_version, git_version,
    build_guess, test_guess, run_guess}``.
    """
    shells = _shells()
    gv = _git_version()
    build, test, run = _build_test_run(root) if root else ("", "", "")
    return {
        "os": platform.system(),
        "shells": shells,
        "interpreter": sys.executable or "",
        "py_version": "%d.%d.%d" % sys.version_info[:3],
        "git_version": gv,
        "build_guess": build,
        "test_guess": test,
        "run_guess": run,
    }
