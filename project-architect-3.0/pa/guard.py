"""PreToolUse rules: PHASE_PLAN protection and discussion-mode read-only (design A C.3).

Public:
    READ_ONLY_CMDS, GIT_READ_ONLY, MUTATION_MARKERS
    tokenize(cmd)                       quote-aware split of one shell segment
    segments(cmd)                       split a command line into runnable segments
    looks_like_powershell(cmd)
    is_read_only(cmd, cfg=None, shell=None)      -> bool
    is_mutating_bash(cmd)                        -> bool   (mutation indicator present)
    phase_plan_deny(tool_name, tool_input)       -> reason | None
    discussion_deny(tool_name, tool_input, cfg)  -> reason | None
    decide(tool_name, tool_input, discussion=False, cfg=None) -> reason | None
    bash_read_deny(cmd, role, cfg, root=None)    -> reason | None  (whole shell reads)
    reread_deny(inp, sid, run_id, cfg, root)     -> reason | None  (unchanged whole re-Read)
    reread_forget(inp, sid, run_id, root)        drop an edited path from the read set
    deny(reason)                                 -> PreToolUse hook payload

Everything here is pure: no filesystem access, no config writes, no imports
beyond ``os``/``re`` (``pa.config`` is imported lazily, only when a ``python``
invocation has to be matched against ``discussion_allow_scripts``).  The hook
module owns the ``.run/DISCUSSION`` stat and the ``.claude/pa.json`` gate.

Rule of the allowlist: *unknown means deny*.  A segment is read-only when its
first real token is in :data:`READ_ONLY_CMDS` (after stripping ``env``,
``VAR=x``, ``sudo``, ``time``, ``nice``, ``command``), the segment redirects
nowhere but ``/dev/null``/``NUL``/``&1``/``&2``, and the per-command extra rules
hold (``sed`` without ``-i``, ``git`` read-only subcommands, ``python`` only for
``discussion_allow_scripts``, ``curl``/``wget`` without ``-o``/``-O``).  An
allow-list entry ``"<glob> <a|b>"`` (e.g. ``"tools/card.py slice|check"``) also
requires the first positional argument after the script to be ``a`` or ``b``;
an entry without a space matches the script alone.
"""

import os
import re

# --------------------------------------------------------------------------- tables

READ_ONLY_CMDS = frozenset("""
ls dir cat head tail less more grep egrep fgrep rg find fd wc stat file which where type
echo printf pwd env printenv date tree diff cmp sort uniq cut awk jq nl tac column
basename dirname realpath du df ps tasklist uname hostname whoami md5sum sha256sum
xxd od strings tr sed git dotnet python python3 py curl wget wsl true false test cd
""".split())
# ``cd`` is on the list so the doc's "strip a leading ``cd … &&``" rule needs no
# special case: a bare ``cd`` cannot mutate anything, and a redirecting one is
# already caught by :func:`_redirects_ok`.

# ``sed``/``git``/``python``/``curl``/``wget``/``dotnet``/``wsl`` carry extra rules below.

GIT_READ_ONLY = frozenset("""
status log diff show blame branch rev-parse ls-files remote stash tag describe
shortlog config cat-file for-each-ref name-rev symbolic-ref check-ignore
""".split())

DOTNET_READ_ONLY = frozenset(["--info", "--version", "--list-sdks", "--list-runtimes"])

MUTATION_MARKERS = (">", ">>", "sed -i", "perl -i", "tee", " mv ", " cp ", " rm ",
                    "set-content", "add-content", "out-file")

_PS_ALLOWED_VERBS = ("get", "select", "where", "measure", "test", "resolve", "format",
                     "sort", "group", "compare", "split", "join", "convertto", "convertfrom")
_PS_ALLOWED_NAMES = frozenset([
    "write-output", "write-host", "out-string", "out-host", "select-string",
    "ls", "dir", "gci", "gc", "cat", "type", "pwd", "echo", "git", "gl", "gcm", "sls",
])
_PS_DENY_PREFIX = ("set-", "new-", "remove-", "move-", "copy-", "rename-", "add-",
                   "clear-", "export-", "import-", "stop-", "restart-", "start-",
                   "invoke-", "tee-", "out-file", "write-", "push-", "pop-", "send-",
                   "enable-", "disable-", "install-", "uninstall-", "update-", "rm",
                   "del", "mv", "cp", "ni", "sc", "ac", "rni", "ri")

_PREFIX_SKIP = frozenset(["env", "sudo", "time", "nice", "command", "builtin", "exec", "nohup"])
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TOKEN_RE = re.compile(r"""'[^']*'|"[^"]*"|[^\s]+""")
_SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_PS_VERB_RE = re.compile(r"^[A-Za-z]+-[A-Za-z]", re.ASCII)
_NULL_TARGETS = frozenset(["/dev/null", "nul", "$null", "&1", "&2", "/dev/stderr", "/dev/stdout"])


# --------------------------------------------------------------------------- helpers

def _unquote(tok):
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        return tok[1:-1]
    return tok


def tokenize(segment):
    """Quote-aware token list for one segment (no shell expansion)."""
    return [m.group(0) for m in _TOKEN_RE.finditer(segment or "")]


def _pull_substitutions(cmd):
    """Return ``(outer, inner_segments)``: ``$(...)``/`` `...` `` contents split out."""
    inner = []
    text = cmd or ""
    for _ in range(4):                                   # nested substitutions, bounded
        found = _SUBST_RE.findall(text)
        if not found:
            break
        for a, b in found:
            if a:
                inner.append(a)
            if b:
                inner.append(b)
        text = _SUBST_RE.sub(" ", text)
    return text, inner


def segments(cmd):
    """Split a command line on ``&&``, ``||``, ``;``, ``|`` and newlines."""
    text, inner = _pull_substitutions(cmd)
    parts = re.split(r"\|\||&&|;|\||\n|\r", text)
    out = [p.strip() for p in parts if p and p.strip()]
    out.extend(p.strip() for p in inner if p and p.strip())
    return out


def looks_like_powershell(cmd):
    """Heuristic: does this command line read as PowerShell rather than Bash?"""
    text = (cmd or "").strip()
    if not text:
        return False
    first = tokenize(text)[:1]
    if first and _PS_VERB_RE.match(_unquote(first[0])):
        return True
    low = text.lower()
    for marker in ("get-", "set-", "new-", "remove-", "out-file", "write-host",
                   "start-process", "invoke-", "tee-object", "$env:", "select-object",
                   "where-object", "convertto-", "convertfrom-", "-erroraction"):
        if marker in low:
            return True
    return False


def _redirects_ok(tokens, raw):
    """False when the segment redirects to anything but a null/stderr target."""
    if "<<" in raw:                                      # heredoc: only safe without >
        if ">" in raw:
            return False
    joined = " ".join(tokens)
    for m in re.finditer(r"(?<![0-9<])>>?(?:&?)\s*([^\s;|&]*)", joined):
        target = _unquote(m.group(1)).strip().lower()
        if not target:
            return False
        if target in _NULL_TARGETS or target.lstrip("&") in ("1", "2"):
            continue
        return False
    return True


def _glob_to_re(pattern):
    out = []
    for ch in str(pattern or ""):
        if ch == "*":
            out.append(r"[^/]*")
        elif ch == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out) + r"$")


def _allow_scripts(cfg):
    if cfg is None:
        from . import config as _config

        cfg = _config.load()
    pats = cfg.get("discussion_allow_scripts") if isinstance(cfg, dict) else None
    return list(pats or ["tools/analysis/*.py", "tools/plan_show.py", "tools/discussion.py",
                         "tools/card.py slice|check", "tools/plan_edit.py show|grammar"])


def _script_allowed(path, cfg, rest=()):
    norm = _unquote(str(path or "")).replace("\\", "/").lstrip("./")
    sub = next((a for a in rest if not a.startswith("-")), None)
    for pat in _allow_scripts(cfg):
        glob, _, subs = str(pat).strip().partition(" ")
        if subs.strip() and sub not in subs.strip().split("|"):
            continue
        rx = _glob_to_re(glob.replace("\\", "/"))
        if rx.match(norm):
            return True
        # also match when the model passed an absolute path to the same script
        tail = norm
        while "/" in tail:
            tail = tail.split("/", 1)[1]
            if rx.match(tail):
                return True
    return False


# --------------------------------------------------------------------------- per-command rules

def _ok_sed(args):
    for a in args:
        a = _unquote(a)
        if a == "--in-place" or a.startswith("--in-place=") or (a.startswith("-") and "i" in a[1:] and not a.startswith("--")):
            return False
    return True


def _ok_git(args):
    words = [_unquote(a) for a in args if not _unquote(a).startswith("-")]
    if not words:
        return True                                       # bare `git` prints usage
    sub = words[0]
    if sub not in GIT_READ_ONLY:
        return False
    flags = [_unquote(a) for a in args if _unquote(a).startswith("-")]
    if sub == "branch" and any(f in ("-d", "-D", "-m", "-M", "-c", "-C") for f in flags):
        return False
    if sub == "stash" and (len(words) < 2 or words[1] != "list"):
        return False
    if sub == "tag" and (len(words) > 1 or any(f in ("-d", "-a", "-f", "-m") for f in flags)):
        return False
    if sub == "config" and not any(f in ("--get", "--list", "-l", "--get-all") for f in flags):
        return False
    return True


def _ok_python(args, cfg):
    vals = [_unquote(a) for a in args]
    if not vals:
        return False
    if vals[0] in ("--version", "-V", "-VV"):
        return True
    if vals[0].startswith("-"):                           # -c, -m, -i, ...
        return False
    return _script_allowed(vals[0], cfg, vals[1:])


def _ok_curl(args):
    for i, a in enumerate(args):
        a = _unquote(a)
        if a in ("-o", "--output", "-O", "--remote-name"):
            nxt = _unquote(args[i + 1]) if i + 1 < len(args) else ""
            if a in ("-O", "--remote-name") or nxt != "-":
                return False
        elif a.startswith("-o") and len(a) > 2 and a[2:] != "-":
            return False
        elif a.startswith("--output="):
            return False
    return True


def _ok_dotnet(args):
    return bool(args) and _unquote(args[0]) in DOTNET_READ_ONLY


def _ok_wsl(args):
    vals = [_unquote(a) for a in args]
    return bool(vals) and vals[0] in ("-l", "--list", "--status", "--version")


# --------------------------------------------------------------------------- bash

def _segment_read_only_bash(segment, cfg):
    tokens = tokenize(segment)
    if not tokens:
        return True
    if not _redirects_ok(tokens, segment):
        return False
    # strip redirection tokens before looking at the command word
    clean = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if re.match(r"^\d?>>?$", tok) or tok in ("<", "<<", "<<<"):
            skip_next = True
            continue
        if re.match(r"^\d?>>?", tok) or tok.startswith("<"):
            continue
        clean.append(tok)
    i = 0
    while i < len(clean):
        word = _unquote(clean[i])
        if _ASSIGN_RE.match(word) or word.lower() in _PREFIX_SKIP:
            i += 1
            continue
        break
    if i >= len(clean):
        return True                                       # only assignments: harmless
    name = os.path.basename(_unquote(clean[i]).replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args = clean[i + 1:]
    if name in ("tee", "dd", "install", "truncate"):
        return False
    if name not in READ_ONLY_CMDS:
        return False
    if name == "sed":
        return _ok_sed(args)
    if name == "git":
        return _ok_git(args)
    if name in ("python", "python3", "py"):
        return _ok_python(args, cfg)
    if name in ("curl", "wget"):
        return _ok_curl(args)
    if name == "dotnet":
        return _ok_dotnet(args)
    if name == "wsl":
        return _ok_wsl(args)
    if name == "find":
        low = " ".join(_unquote(a).lower() for a in args)
        if "-delete" in low.split() or "-exec" in low.split() or "-execdir" in low.split():
            return False
    if name == "awk":
        low = " ".join(_unquote(a).lower() for a in args)
        if "print >" in low or "> \"" in low or "system(" in low:
            return False
    return True


# --------------------------------------------------------------------------- powershell

def _segment_read_only_ps(segment, cfg):
    tokens = tokenize(segment)
    if not tokens:
        return True
    if not _redirects_ok(tokens, segment):
        return False
    i = 0
    while i < len(tokens) and _unquote(tokens[i]) in ("&", "."):
        i += 1
    if i >= len(tokens):
        return False
    name = _unquote(tokens[i])
    name = os.path.basename(name.replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args = tokens[i + 1:]
    if name in _PS_ALLOWED_NAMES:
        if name == "git":
            return _ok_git(args)
        return True
    if "-" in name:
        verb = name.split("-", 1)[0]
        if name.startswith(_PS_DENY_PREFIX):
            return False
        return verb in _PS_ALLOWED_VERBS
    if name in READ_ONLY_CMDS:                            # native tools run from PS
        return _segment_read_only_bash(segment, cfg)
    return False


# --------------------------------------------------------------------------- public

def is_read_only(cmd, cfg=None, shell=None):
    """True when every segment of ``cmd`` is on the read-only allowlist."""
    text = str(cmd or "").strip()
    if not text:
        return True
    low = text.lower()
    if "invoke-expression" in low or "iex " in low or "out-file" in low or "tee-object" in low:
        return False
    parts = segments(text)
    if not parts:
        return True
    use_ps = (shell == "powershell") or (shell is None and looks_like_powershell(text))
    for seg in parts:
        ok = _segment_read_only_ps(seg, cfg) if use_ps else _segment_read_only_bash(seg, cfg)
        if not ok:
            return False
    return True


def is_mutating_bash(cmd):
    """True when the command carries a mutation indicator (design A C.3, rule 2)."""
    text = " %s " % str(cmd or "").replace("\t", " ")
    low = text.lower()
    for marker in MUTATION_MARKERS:
        if marker in (">", ">>"):
            if re.search(r"(?<![0-9<>])>>?", text):
                return True
        elif marker in low:
            return True
    return False


_HEREDOC_RE = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)^[ \t]*\2[ \t]*$", re.M | re.S)
_REDIRECT_RE = re.compile(r"^(?:\d?&?>>?|>\|)(.*)$")
_TARGET_ANY = frozenset(["rm", "tee", "truncate", "shred", "unlink",
                         "set-content", "add-content", "out-file"])
_TARGET_LAST = frozenset(["mv", "cp", "install", "ln", "rename", "rsync"])
_INPLACE = frozenset(["sed", "perl"])


def _strip_heredocs(cmd):
    """Drop heredoc bodies: what a command feeds to stdin is data, not a command."""
    return _HEREDOC_RE.sub(lambda m: m.group(0).split("\n", 1)[0], str(cmd or ""))


def _is_file(tok, name):
    return os.path.basename(_unquote(tok).replace("\\", "/")).lower() == name


def mutation_targets(cmd, name):
    """True when a segment of ``cmd`` writes, moves or copies onto, or deletes a file
    called ``name`` (design A C.3 rule 2, narrowed in 3.1 T15): a redirect, ``tee``,
    ``rm``, ``sed -i``, or ``mv``/``cp`` whose last argument is that file.  A heredoc
    body or a read that merely names the file is not a mutation."""
    name = str(name or "").lower()
    for seg in segments(_strip_heredocs(cmd)):
        toks = tokenize(seg)
        for i, tok in enumerate(toks):
            m = _REDIRECT_RE.match(tok)
            if not m or re.match(r"^\d?>&\d?$", tok):
                continue
            target = m.group(1) or (toks[i + 1] if i + 1 < len(toks) else "")
            if target and _is_file(target, name):
                return True
        i = 0
        while i < len(toks) and (_ASSIGN_RE.match(toks[i]) or _unquote(toks[i]) in _PREFIX_SKIP):
            i += 1
        if i >= len(toks):
            continue
        head = os.path.basename(_unquote(toks[i]).replace("\\", "/")).lower()
        args = toks[i + 1:]
        files = [a for a in args if not a.startswith("-")]
        if head in _TARGET_ANY and any(_is_file(a, name) for a in files):
            return True
        if head in _INPLACE and any(a == "-i" or a.startswith("-i") or a == "--in-place"
                                    for a in args) and any(_is_file(a, name) for a in files):
            return True
        if head in _TARGET_LAST and files and _is_file(files[-1], name):
            return True
    return False


# --------------------------------------------------------------------------- decisions

_EDIT_TOOLS = ("edit", "write", "multiedit", "notebookedit", "update")
PHASE_PLAN_REASON = ("PHASE_PLAN.md is frozen at approval; use "
                     "`python tools/plan_edit.py` (set-status / add-task / note).")
DISCUSSION_REASON = ("discussion mode (/discuss): read-only tools only until /proceed. "
                     "Record decisions in the discussion note, then run /proceed.")


def _paths_of(tool_input):
    ti = tool_input if isinstance(tool_input, dict) else {}
    out = []
    for key in ("file_path", "notebook_path", "path", "filePath"):
        val = ti.get(key)
        if isinstance(val, str) and val:
            out.append(val)
    edits = ti.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("file_path"), str):
                out.append(e["file_path"])
    return out


def _command_of(tool_input):
    ti = tool_input if isinstance(tool_input, dict) else {}
    for key in ("command", "script", "cmd"):
        val = ti.get(key)
        if isinstance(val, str):
            return val
    return ""


AGENT_BASH_ALLOW = {}
_INTERPRETERS = ("python", "python3", "py", "python.exe", "python3.exe", "py.exe", "bash", "sh")


def _script_of(toks):
    """The script a segment runs: the head token, or the first argument after an interpreter."""
    i = 0
    while i < len(toks) and (_ASSIGN_RE.match(toks[i]) or _unquote(toks[i]) in _PREFIX_SKIP):
        i += 1
    if i >= len(toks):
        return ""
    head = _unquote(toks[i]).replace("\\", "/")
    if os.path.basename(head).lower() in _INTERPRETERS:
        rest = [t for t in toks[i + 1:] if not t.startswith("-")]
        return _unquote(rest[0]).replace("\\", "/") if rest else ""
    return head


def agent_bash_deny(agent_type, tool_name, tool_input):
    """Deny reason when a leaf agent with a Bash allowlist runs anything but its scripts
    (3.1 T15: retriever-code may run tools/research_add.py and nothing else)."""
    name = str(tool_name or "").strip().lower()
    if name not in ("bash", "powershell", "shell"):
        return None
    allow = AGENT_BASH_ALLOW.get(str(agent_type or "").strip().lower())
    if not allow:
        return None
    reason = ("%s may run Bash only for %s; everything else is Read/Grep/Glob."
              % (agent_type, ", ".join("tools/" + a for a in allow)))
    segs = segments(_strip_heredocs(_command_of(tool_input)))
    if not segs:
        return reason
    for seg in segs:
        script = os.path.basename(_script_of(tokenize(seg))).lower()
        if script not in allow:
            return reason
    return None


def phase_plan_deny(tool_name, tool_input):
    """Deny reason when this call would edit ``PHASE_PLAN.md`` outside plan_edit.py."""
    name = str(tool_name or "").strip().lower()
    if name in _EDIT_TOOLS:
        for p in _paths_of(tool_input):
            if os.path.basename(str(p).replace("\\", "/")).lower() == "phase_plan.md":
                return PHASE_PLAN_REASON
        return None
    if name in ("bash", "powershell", "shell"):
        cmd = _command_of(tool_input)
        if "plan_edit.py" in cmd.lower():
            return None
        if mutation_targets(cmd, "phase_plan.md"):
            return PHASE_PLAN_REASON
    return None


def discussion_deny(tool_name, tool_input, cfg=None):
    """Deny reason while ``.run/DISCUSSION`` exists (the caller stats the flag)."""
    name = str(tool_name or "").strip().lower()
    if name in _EDIT_TOOLS:
        return DISCUSSION_REASON
    if name in ("bash", "powershell", "shell"):
        shell = "powershell" if name == "powershell" else None
        if not is_read_only(_command_of(tool_input), cfg=cfg, shell=shell):
            return DISCUSSION_REASON
    return None


def decide(tool_name, tool_input, discussion=False, cfg=None, agent_type=None):
    """Deny reason for this PreToolUse call, or None to stay silent."""
    reason = agent_bash_deny(agent_type, tool_name, tool_input)
    if reason:
        return reason
    reason = phase_plan_deny(tool_name, tool_input)
    if reason:
        return reason
    if discussion:
        return discussion_deny(tool_name, tool_input, cfg=cfg)
    return None


def deny(reason):
    """The PreToolUse hook payload for a denial."""
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


# --------------------------------------------------------------------------- role-based guards

_SPILL_PATH_RE = re.compile(r"tool-results/[a-z0-9]+\.txt$")
_PLAN_SHOW_RE = re.compile(r"plan_edit\.py\s+show\b")
_PLAN_SCOPE_RE = re.compile(r"--(?:section|task(?:s)?|next)\b")

SPILLED_REASON = ("a spilled result is grepped or tailed "
                  "(`grep -n <pattern> <path>`, `tail -n 40 <path>`), never Read whole")
WHOLE_PLAN_ROLE_REASON = ("read the plan through `PY tools/plan_edit.py show --task T<n>` "
                          "or `--section <name>`")
TOOL_SOURCE_REASON = ("learn a tool from the Tools table and `<tool> --help`; "
                      "a --help that does not answer is a `harness:` gotcha in the summary")

BINARY_EXTS = frozenset((
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".gz", ".sqlite", ".db",
))

WHOLE_READ_REASON = (
    "%s is %.1fk chars, over guard.whole_read_chars (%d): "
    "run `PY tools/outline.py %s` (symbols with their intent lines, the header block, "
    "the section comments), then Read with offset and limit")

WHOLE_READ_HEAD = "%s is %.1fk chars, over guard.whole_read_chars (%d):"
WHOLE_READ_TAIL = "Read the ranges you need with offset and limit"
WHOLE_READ_MAX_ROWS = 60

_outline_mod = None


def _load_outline(root):
    """``tools/outline.py`` of the project root, else the package sibling; None if neither loads."""
    global _outline_mod
    if _outline_mod is not None:
        return _outline_mod
    import importlib.util
    cands = []
    if root:
        cands.append(os.path.join(root, "tools", "outline.py"))
    cands.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "tools", "outline.py"))
    for cand in cands:
        if not os.path.isfile(cand):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_pa_outline", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _outline_mod = mod
            return mod
        except Exception:
            continue
    return None


def _served_outline(fp, size, threshold, root):
    """The deny reason carrying the file's outline, or None when the outline cannot be served."""
    try:
        mod = _load_outline(root)
        if mod is None:
            return None
        depth = 2 if os.path.splitext(fp)[1].lower() == ".md" else None
        rows = mod.outline_lines(fp, depth=depth)
        lines = [WHOLE_READ_HEAD % (fp, size / 1000.0, threshold)]
        lines += rows[:WHOLE_READ_MAX_ROWS]
        if len(rows) > WHOLE_READ_MAX_ROWS:
            lines.append("… %d more rows: outline.py %s" % (len(rows) - WHOLE_READ_MAX_ROWS, fp))
        lines.append(WHOLE_READ_TAIL)
        reason = "\n".join(lines)
        mod._credit_note(fp, len(reason) + 1, root=root)
        return reason
    except Exception:
        return None


def whole_read_deny(inp, role, cfg, root=None):
    """Deny a whole Read when the file exceeds ``guard.whole_read_chars``.

    Cheap checks first (tool name, offset/limit, extension) so a Read with a
    range never stats the file.  One ``os.path.getsize`` at most.  Over the cap
    the reason serves the file's outline (``tools/outline.py``, loaded lazily
    from ``<root>/tools`` or the package sibling, same text for every role);
    when it cannot load, the reason names the command instead.
    """
    ti = inp if isinstance(inp, dict) else {}
    if ti.get("offset") or ti.get("limit"):
        return None
    fp = ti.get("file_path") or ti.get("path") or ""
    if not fp:
        return None
    ext = os.path.splitext(fp)[1].lower()
    if ext in BINARY_EXTS:
        return None
    threshold = ((cfg or {}).get("guard") or {}).get("whole_read_chars")
    if not threshold:
        return None
    try:
        size = os.path.getsize(fp)
    except OSError:
        return None
    if size <= threshold:
        return None
    return _whole_read_reason(fp, size, threshold, root)


def _whole_read_reason(fp, size, threshold, root):
    """The over-threshold reason: the served outline, else the outline.py command."""
    return (_served_outline(fp, size, threshold, root)
            or WHOLE_READ_REASON % (fp, size / 1000.0, threshold, fp))


REREAD_REASON = ("%s is unchanged and already in this context (read at %s); "
                 "Read a range (offset/limit) for the part you need, or Edit it directly")
REREAD_ESCAPE = 3  # denials in a row before the next whole Read is let through


def _reads_file(root, key):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(key or "none"))
    return os.path.join(root, ".run", "guard", "reads-%s.json" % safe)


def _load_reads(path):
    import json
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_reads(path, data):
    import json
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def _norm_path(fp):
    return os.path.normcase(os.path.abspath(fp))


def reread_deny(inp, sid, run_id, cfg, root):
    """Deny a second whole Read of a file unchanged since this agent read it (fix-17).

    Called last in the Read branch, so a Read another guard denies is never
    recorded.  The per-agent set is ``<root>/.run/guard/reads-<run_id or sid>.json``
    mapping the normalized path to ``{mtime, size, ts, denials}``; a ranged Read
    passes unrecorded; the fourth denial in a row resets and allows (the escape).
    One ``os.stat``, one small JSON read and write.
    """
    if not ((cfg or {}).get("guard") or {}).get("reread_deny", True) or not root:
        return None
    ti = inp if isinstance(inp, dict) else {}
    if ti.get("offset") or ti.get("limit"):
        return None
    fp = ti.get("file_path") or ti.get("path") or ""
    if not fp:
        return None
    try:
        st = os.stat(fp)
    except OSError:
        return None
    import time
    path = _reads_file(root, run_id or sid)
    reads = _load_reads(path)
    key = _norm_path(fp)
    rec = reads.get(key)
    if (not isinstance(rec, dict) or rec.get("mtime") != st.st_mtime
            or rec.get("size") != st.st_size):
        reads[key] = {"mtime": st.st_mtime, "size": st.st_size,
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "denials": 0}
        _save_reads(path, reads)
        return None
    denials = int(rec.get("denials") or 0) + 1
    if denials > REREAD_ESCAPE:
        rec["denials"] = 0
        _save_reads(path, reads)
        return None
    rec["denials"] = denials
    _save_reads(path, reads)
    try:
        rel = os.path.relpath(fp, root).replace("\\", "/")
    except ValueError:
        rel = fp
    return REREAD_REASON % (rel, rec.get("ts"))


def reread_forget(inp, sid, run_id, root):
    """Drop an edited path from the agent's read set so the next whole Read passes."""
    ti = inp if isinstance(inp, dict) else {}
    fp = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    if not fp or not root:
        return
    path = _reads_file(root, run_id or sid)
    if not os.path.exists(path):
        return
    reads = _load_reads(path)
    if reads.pop(_norm_path(fp), None) is not None:
        _save_reads(path, reads)


def is_project_pa(root):
    """True when *root* is the ProjectArchitect repository itself."""
    return os.path.basename(os.path.normpath(root or "")).lower() in (
        "projectarchitect", "project-architect", "projectarchitect-3.0")


def spilled_read_deny(inp, cfg):
    """Deny a whole Read of ``tool-results/<id>.txt`` (any role, ``guard.spilled_read``)."""
    guard_cfg = (cfg or {}).get("guard") or {}
    if not guard_cfg.get("spilled_read"):
        return None
    ti = inp if isinstance(inp, dict) else {}
    fp = (ti.get("file_path") or ti.get("path") or "").replace("\\", "/")
    if "tool-results/" not in fp:
        return None
    if not _SPILL_PATH_RE.search(fp):
        return None
    if ti.get("offset") or ti.get("limit"):
        return None
    return SPILLED_REASON


_SHELL_READERS = frozenset(["cat", "type", "get-content", "gc", "sed", "head", "tail"])
_PIPE_NARROWERS = frozenset(["grep", "egrep", "fgrep", "rg", "select-string", "sls"])
_REDIRECT_OPS = frozenset([">", ">>", "<", "2>", "2>>", "&>"])
_SED_NARROW_RE = re.compile(r"^(?:\d+(?:,\s*(?:\d+|\$))?|/.*/)\s*p$")
_LINES_RE = re.compile(r"^-(?:n\s*)?\d+$|^--lines=\d+$")


def _stage_head(toks):
    """``(name, args)`` of one pipeline stage: env/prefix words skipped, ``.exe`` dropped."""
    i = 0
    while i < len(toks) and (_ASSIGN_RE.match(_unquote(toks[i]))
                             or _unquote(toks[i]).lower() in _PREFIX_SKIP):
        i += 1
    if i >= len(toks):
        return "", []
    name = os.path.basename(_unquote(toks[i]).replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name, [_unquote(t) for t in toks[i + 1:]]


def _lines_arg(args):
    """True when ``head``/``tail`` args carry a line count (``-n N``, ``-N``, ``-nN``, ``-c N``)."""
    for j, a in enumerate(args):
        if _LINES_RE.match(a):
            return True
        if a in ("-n", "-c") and j + 1 < len(args) and args[j + 1].isdigit():
            return True
    return False


def _narrows(name, args):
    """True when a stage prints only part of its input (a range, a count or a match)."""
    if name in _PIPE_NARROWERS:
        return True
    if name in ("head", "tail"):
        return _lines_arg(args)
    if name == "select-object":
        return any(a.lower() in ("-first", "-last", "-index", "-skip") for a in args)
    return False


def _read_paths(name, args):
    """Path arguments of a whole shell read, or [] when the read is narrowed."""
    if name in ("head", "tail") and _lines_arg(args):
        return []
    if name in ("get-content", "gc") and any(
            a.lower() in ("-totalcount", "-head", "-tail", "-first", "-last") for a in args):
        return []
    rest, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _REDIRECT_OPS:
            skip = True
            continue
        if a.startswith("-") or a[:1] in ("<", ">") or a[:2] in ("2>", "&>"):
            continue
        rest.append(a)
    if name == "sed":
        if not rest:
            return []
        if "-n" in args and _SED_NARROW_RE.match(rest[0]):
            return []
        return rest[1:]
    return rest


def bash_read_deny(cmd, role, cfg, root=None):
    """Deny a whole shell read (``cat``, ``type``, ``Get-Content``, bare ``sed``/``head``/``tail``)
    of a spilled ``tool-results/<id>.txt`` or of a file over ``guard.whole_read_chars``.

    The shell twin of :func:`spilled_read_deny` and :func:`whole_read_deny`, same reasons,
    every role.  A later ``grep``, ``head -n N`` or ``tail -n N`` in the same pipeline narrows
    the read.  Pure string work and one ``os.path.getsize`` at most.
    """
    guard_cfg = (cfg or {}).get("guard") or {}
    spill = guard_cfg.get("spilled_read")
    threshold = guard_cfg.get("whole_read_chars")
    if not (spill or threshold) or not isinstance(cmd, str) or not cmd:
        return None
    text, inner = _pull_substitutions(_strip_heredocs(cmd))
    pipelines = re.split(r"\|\||&&|;|\n|\r", text) + inner
    stat_path = None
    for pipe in pipelines:
        stages = [_stage_head(tokenize(s)) for s in pipe.split("|") if s.strip()]
        for k, (name, args) in enumerate(stages):
            if name not in _SHELL_READERS:
                continue
            paths = _read_paths(name, args)
            if not paths or any(_narrows(n, a) for n, a in stages[k + 1:]):
                continue
            for p in paths:
                if spill and _SPILL_PATH_RE.search(p.replace("\\", "/")):
                    return SPILLED_REASON
                if (stat_path is None and threshold
                        and os.path.splitext(p)[1].lower() not in BINARY_EXTS):
                    stat_path = p
    if stat_path is None:
        return None
    fp = stat_path if (os.path.isabs(stat_path) or not root) else os.path.join(root, stat_path)
    try:
        size = os.path.getsize(fp)
    except OSError:
        return None
    if size <= threshold:
        return None
    return _whole_read_reason(fp, size, threshold, root)


def whole_plan_role_deny(inp, role, cfg):
    """Deny a whole-plan read for roles listed in ``guard.whole_plan_roles``."""
    roles = (cfg or {}).get("guard", {}).get("whole_plan_roles") or []
    if role not in roles:
        return None
    ti = inp if isinstance(inp, dict) else {}
    # Read of PHASE_PLAN.md without offset/limit
    fp = ti.get("file_path") or ti.get("path") or ""
    if fp and (fp.endswith("PHASE_PLAN.md") or fp.endswith("PHASE_PLAN.draft.md")):
        if not ti.get("offset") and not ti.get("limit"):
            return WHOLE_PLAN_ROLE_REASON
    # Bash: plan_edit.py show without --task/--section/--next/--tasks
    cmd = ti.get("command") or ""
    if cmd and _PLAN_SHOW_RE.search(cmd):
        if not _PLAN_SCOPE_RE.search(cmd):
            return WHOLE_PLAN_ROLE_REASON
    return None


def tool_source_deny(inp, role, cfg, root):
    """Deny reading tool/framework source for roles in ``guard.tool_source_roles``.

    Skipped when the project is PA itself (:func:`is_project_pa`).
    """
    roles = (cfg or {}).get("guard", {}).get("tool_source_roles") or []
    if role not in roles:
        return None
    if is_project_pa(root):
        return None
    ti = inp if isinstance(inp, dict) else {}
    from . import paths as _paths
    install = _paths.install_dir().replace("\\", "/")
    prefixes = ("tools/", ".claude/", install + "/", "project-architect-3.0/pa/")
    # Read: file_path matches a source path (skip tool-results: transient output)
    fp = (ti.get("file_path") or ti.get("path") or "").replace("\\", "/")
    if fp and "tool-results/" not in fp:
        for pfx in prefixes:
            if pfx in fp:
                return TOOL_SOURCE_REASON
    # Bash: cat/sed -n/head of a source file
    cmd = ti.get("command") or ""
    if cmd:
        for seg in segments(cmd):
            if _seg_reads_source(seg, prefixes):
                return TOOL_SOURCE_REASON
    return None


def _seg_reads_source(seg, prefixes):
    """True when a segment is ``cat``/``sed -n``/``head`` targeting a source file."""
    toks = tokenize(seg)
    if not toks:
        return False
    i = 0
    while i < len(toks):
        word = _unquote(toks[i])
        if _ASSIGN_RE.match(word) or word.lower() in _PREFIX_SKIP:
            i += 1
            continue
        break
    if i >= len(toks):
        return False
    name = os.path.basename(_unquote(toks[i]).replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in ("cat", "sed", "head"):
        return False
    args = toks[i + 1:]
    if name == "sed":
        has_n = False
        for a in args:
            ua = _unquote(a)
            if ua == "-n":
                has_n = True
            elif ua.startswith("-") and not ua.startswith("--") and "n" in ua[1:]:
                has_n = True
        if not has_n:
            return False
    file_args = [_unquote(a).replace("\\", "/") for a in args
                 if not _unquote(a).startswith("-")]
    for fa in file_args:
        for pfx in prefixes:
            if pfx in fa:
                return True
    return False
