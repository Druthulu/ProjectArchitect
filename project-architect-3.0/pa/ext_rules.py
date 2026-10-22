"""3.30.1: what an external coder may do without asking, shared by tools/pa_ext.py and the PermissionRequest hook.

An external coder runs with prompts off, so a call no rule approved goes to the hook (pa/hooks/permission_request.py):
two fixed rules first (no network, nothing outside the project), then this project's learned decisions, then the
judge. What the judge learns lives in ``<project>/.run/mod/ext/learned.json``; pa_ext passes its allow rules to every
later coder at launch, so a kind of call is judged once per project.

Public:
    segments(command) -> [[word]] | None          a shell command's simple commands (None: it does not parse)
    network_reason(tool, inp) -> str              "" or what in the call reaches the network
    outside_path(tool, inp, proj, dirs, own, home, keep) -> str
                                                  "" or the path outside the project the call names
    remember_ok(rule, proj) -> bool               only Bash(<program>:*) or Bash(<interpreter> <script>:*)
    deny_key(tool, inp) -> str
    learned_path(proj), load_learned(proj), allow_rules(proj)
    remember(proj, kind, entry), forget(proj, rule) -> int
    learned_match(proj, tool, inp) -> (kind | None, entry | None)
    record(run_dir, entry), decisions(run_dir), tally(decisions)
"""

import json
import os
import re
import shlex

from . import fsutil

# --------------------------------------------------------------------------- shell words

_SEP_CHARS = set("();<>|&")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Words that only start or wrap a command: the program is the next word that is not an option or a number.
_LEAD = frozenset(("if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time", "timeout", "nice",
                   "nohup", "env", "stdbuf", "xargs", "sudo", "doas", "setsid", "ionice", "unbuffer", "command",
                   "exec", "builtin"))


def segments(command):
    """The simple commands of a shell command line, each a list of words (redirection operators dropped, their
    targets kept); None when it does not parse (an open quote)."""
    text = str(command or "").replace("\r\n", "\n").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.wordchars += ":@%+,{}[]^$!"      # URLs, drive letters (C:/…), $VAR, globs stay one word
        toks = list(lex)
    except ValueError:
        return None
    segs, cur = [], []
    for t in toks:
        if t and set(t) <= _SEP_CHARS:
            if "<" in t or ">" in t:
                continue                    # a redirection: its target is the next word, kept
            if cur:
                segs.append(cur)
            cur = []
            continue
        cur.append(t.strip("`"))
    if cur:
        segs.append(cur)
    return [s for s in segs if any(w for w in s)]


def _base(word):
    b = os.path.basename(str(word).replace("\\", "/")).lower()
    return b[:-4] if b.endswith((".exe", ".com", ".cmd", ".bat")) else b


_WRAP_ARG = re.compile(r"-|\d+(?:\.\d+)?[smhd]?\Z|[A-Za-z_][A-Za-z0-9_]*=")


def _program(words):
    """(program, the words after it) past env assignments, shell keywords and wrappers such as `timeout 60`."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _ENV_RE.match(w) or w in _LEAD:
            i += 1
            if w in _LEAD:
                while i < n and _WRAP_ARG.match(words[i]):
                    i += 1
            continue
        return w, words[i + 1:]
    return "", []


# --------------------------------------------------------------------------- rule 1: no network

NET_PROGRAMS = frozenset((
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp", "tftp", "aria2c", "http",
    "https", "httpie", "xh", "lynx", "w3m", "links", "elinks", "socat", "nmap", "ping", "ping6", "traceroute",
    "tracepath", "dig", "nslookup", "host", "whois", "mosh", "invoke-webrequest", "invoke-restmethod", "iwr", "irm",
    "certutil", "bitsadmin", "npx", "bunx", "uvx", "pipx", "winget", "choco", "scoop"))
NET_VERBS = {
    "pip": ("install", "download", "search", "index", "wheel"),
    "pip3": ("install", "download", "search", "index", "wheel"),
    "npm": ("install", "i", "ci", "add", "update", "upgrade", "publish", "view", "info", "audit", "exec", "init"),
    "pnpm": ("install", "i", "add", "update", "up", "dlx", "publish"),
    "yarn": ("install", "add", "up", "upgrade", "dlx", "publish", "info"),
    "bun": ("install", "i", "add", "x", "update", "upgrade"),
    "apt": ("install", "update", "upgrade", "download", "source", "full-upgrade"),
    "apt-get": ("install", "update", "upgrade", "download", "source", "dist-upgrade"),
    "brew": ("install", "update", "upgrade", "fetch", "tap"),
    "cargo": ("install", "fetch", "update", "search", "publish", "add"),
    "gem": ("install", "fetch", "update", "search", "push"),
    "go": ("get", "install", "mod"),
    "docker": ("pull", "push", "login", "search"),
    "podman": ("pull", "push", "login", "search"),
    "conda": ("install", "update", "create", "search"),
    "uv": ("pip", "add", "sync", "lock", "tool"),
    "git": ("clone", "fetch", "pull", "push", "ls-remote"),
}
_GIT_SUB = {"remote": ("update", "prune"), "submodule": ("update", "sync", "add")}
_INLINE = frozenset(("python", "python2", "python3", "py", "pypy3", "node", "nodejs", "deno", "perl", "ruby", "php"))
_URL_RE = re.compile(r"\b(?:https?|ftp|wss?)://|\burllib\b|\brequests\.|\bhttp\.client\b|\bsocket\b|\bfetch\(", re.I)


def _first_arg(rest):
    for w in rest:
        if not w.startswith("-"):
            return w
    return ""


def network_reason(tool, inp):
    """"" or what in the call reaches the network: WebFetch or WebSearch; a Bash command running a network program,
    a package manager's or git's network verb, `python -m pip install`, or inline code that opens a connection."""
    if tool in ("WebFetch", "WebSearch"):
        return tool
    if tool not in ("Bash", "PowerShell"):
        return ""
    cmd = str((inp or {}).get("command") or "")
    for words in segments(cmd) or [cmd.split()]:
        prog, rest = _program(words)
        base = _base(prog)
        if base in NET_PROGRAMS:
            return base
        verb = _first_arg(rest)
        if base in NET_VERBS and verb in NET_VERBS[base]:
            return "%s %s" % (base, verb)
        if base == "git" and verb in _GIT_SUB and _first_arg(rest[rest.index(verb) + 1:]) in _GIT_SUB[verb]:
            return "git %s" % verb
        if base in _INLINE:
            if "-m" in rest:
                mod = rest[rest.index("-m") + 1] if rest.index("-m") + 1 < len(rest) else ""
                nxt = _first_arg(rest[rest.index("-m") + 2:])
                if mod in ("pip", "pip3") and nxt in NET_VERBS["pip"] or mod == "http.server":
                    return "%s -m %s" % (base, mod)
            for flag in ("-c", "-e", "--eval"):
                if flag in rest and rest.index(flag) + 1 < len(rest) and _URL_RE.search(rest[rest.index(flag) + 1]):
                    return "%s %s (a connection in its code)" % (base, flag)
    return ""


# --------------------------------------------------------------------------- rule 2: nothing outside the project

FILE_TOOLS = {"Read": ("file_path", False), "Grep": ("path", False), "Glob": ("path", False), "LS": ("path", False),
              "Edit": ("file_path", True), "MultiEdit": ("file_path", True), "Write": ("file_path", True),
              "NotebookEdit": ("notebook_path", True)}
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")
_GITBASH_RE = re.compile(r"/([A-Za-z])(?=/|$)")
_MOUNTS = ("/mnt/", "/media/", "/run/media/", "/Volumes/")
_WIN_SYSTEM = ("c:/windows/", "c:/program files/", "c:/program files (x86)/", "c:/programdata/")


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _inside(p, root):
    if not root:
        return False
    p, root = _norm(p), _norm(root)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def _expand(word, proj, home):
    """An absolute path for a path-like word (absolute, ~, $HOME, a drive, or a relative path that climbs with ..);
    None for any other word."""
    w = word
    for pre in ("${HOME}", "$HOME"):
        if w == pre or w.startswith(pre + "/"):
            w = home + w[len(pre):]
    if w == "~" or w.startswith(("~/", "~\\")):
        w = home + w[1:]
    if os.name == "nt":
        m = _GITBASH_RE.match(w)
        if m:
            w = "%s:%s" % (m.group(1).upper(), w[2:] or "/")
    if w.startswith("/") or _DRIVE_RE.match(w):
        return os.path.normpath(w)
    if w.startswith("..") or "/../" in w or "\\..\\" in w:
        return os.path.normpath(os.path.join(proj, w))
    return None


def _executable(p):
    try:
        if os.name == "nt":
            return os.path.isfile(p) and p.lower().endswith((".exe", ".com"))
        return os.path.isfile(p) and os.access(p, os.X_OK)
    except OSError:
        return False


def _foreign(p, home):
    """A path a coder must not reach: under the home folder, or on a mounted or other drive (system folders pass)."""
    if home and _inside(p, home):
        return True
    f = p.replace("\\", "/")
    if os.name == "nt":
        return bool(_DRIVE_RE.match(f)) and not f.lower().startswith(_WIN_SYSTEM)
    return f.startswith(_MOUNTS)


def outside_path(tool, inp, proj, dirs=(), own=(), home=None, keep=()):
    """"" or the path outside the project a call names. A file tool: any path outside the project, except reads in a
    declared folder (`dirs`) or the coder's own session folder (`own`). A command: a word under the home folder or
    on another drive, outside the project, `dirs` and `own`; a program being run (its first word, any executable
    file, the paths in `keep`) is never one."""
    inp = inp or {}
    home = home if home is not None else os.path.expanduser("~")
    if tool in FILE_TOOLS:
        key, write = FILE_TOOLS[tool]
        vals = [inp.get(key)] + ([inp.get("pattern")] if tool == "Glob" else [])
        for v in vals:
            if not isinstance(v, str) or not v.strip():
                continue
            p = _expand(v.strip(), proj, home)
            if p is None:
                if tool == "Glob" and v is inp.get("pattern"):
                    continue
                p = os.path.normpath(os.path.join(proj, v.strip()))
            if _inside(p, proj):
                continue
            if not write and any(_inside(p, d) for d in list(dirs) + list(own)):
                continue
            return p
        return ""
    if tool not in ("Bash", "PowerShell"):
        return ""
    cmd = str(inp.get("command") or "")
    kept = [_norm(k) for k in keep if k]
    for words in segments(cmd) or [cmd.split()]:
        prog, _rest = _program(words)
        skip_prog = True
        for w in words:
            if skip_prog and w == prog:
                skip_prog = False
                continue
            cands = [w]
            if "=" in w:
                cands.append(w.split("=", 1)[1])
            if len(w) > 2 and w.startswith("-") and (w[2:3] in ("/", "~") or _DRIVE_RE.match(w[2:])):
                cands.append(w[2:])
            for c in cands:
                p = _expand(c, proj, home)
                if p is None or _inside(p, proj) or any(_inside(p, d) for d in list(dirs) + list(own)):
                    continue
                if _norm(p) in kept or _executable(p):
                    continue
                if _foreign(p, home):
                    return p
    return ""


# --------------------------------------------------------------------------- what is learned

LEARNED = "learned.json"        # <proj>/.run/mod/ext/learned.json {"allow": [...], "deny": [...]}
# Never remembered as a rule (judged each time): shells and interpreters on their own, anything that runs another
# command or writes or deletes freely, and the network programs.
NEVER = frozenset((
    "sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "busybox", "python", "python2", "python3", "pythonw",
    "py", "pypy", "pypy3", "node", "nodejs", "deno", "bun", "perl", "ruby", "php", "lua", "luajit", "tclsh", "wish",
    "rscript", "julia", "osascript", "powershell", "pwsh", "cmd", "wsl", "env", "xargs", "sudo", "doas", "su", "eval",
    "exec", "command", "builtin", "source", ".", "nohup", "setsid", "timeout", "nice", "ionice", "time", "watch",
    "stdbuf", "unbuffer", "script", "expect", "parallel", "find", "awk", "gawk", "mawk", "nawk", "sed", "git", "tar",
    "zip", "unzip", "rm", "rmdir", "mv", "cp", "dd", "ln", "chmod", "chown", "chgrp", "install", "truncate", "shred",
    "tee", "kill", "pkill", "killall", "systemctl", "service", "crontab", "at", "mount", "umount", "strace", "ltrace",
    "gdb", "lldb", "vim", "vi", "nvim", "emacs", "less", "more", "man", "start", "open", "xdg-open", "explorer",
    "kubectl")) | NET_PROGRAMS | frozenset(NET_VERBS)
INTERPRETERS = frozenset(("bash", "sh", "python", "python3", "py", "node", "perl", "ruby"))
_RULE_RE = re.compile(r"Bash\((.+):\*\)\Z")
_WORD_OK = re.compile(r"(?:[A-Za-z]:)?[A-Za-z0-9_./+-]+\Z")


def remember_ok(rule, proj, home=None):
    """A rule the judge may make permanent for the project: `Bash(<program>:*)` for one program that is no shell,
    interpreter or runner (NEVER) and not under the home folder outside the project, or `Bash(<interpreter>
    <script>:*)` for one script path inside the project."""
    m = _RULE_RE.match(str(rule or "").strip())
    if not m:
        return False
    home = home if home is not None else os.path.expanduser("~")
    words = m.group(1).split()
    if not words or not all(_WORD_OK.match(w) and ".." not in w.replace("\\", "/").split("/") for w in words):
        return False
    if len(words) == 1:
        prog = words[0]
        if _base(prog) in NEVER or prog.startswith("-"):
            return False
        p = _expand(prog, proj, home)
        return p is None or _inside(p, proj) or not _foreign(p, home)
    if len(words) == 2:
        interp, script = words
        if _base(interp) not in INTERPRETERS or script.endswith("/") or script.startswith(("-", "/", "~")):
            return False
        return not _DRIVE_RE.match(script) and _inside(os.path.join(proj, script), proj)
    return False


def deny_key(tool, inp):
    """What a refusal is remembered by: the command (spaces collapsed), else the tool and its path."""
    inp = inp or {}
    if tool in ("Bash", "PowerShell"):
        return "%s:%s" % (tool, " ".join(str(inp.get("command") or "").split()))
    key = FILE_TOOLS.get(tool, ("file_path", False))[0]
    return "%s:%s" % (tool, str(inp.get(key) or ""))


def learned_path(proj):
    return os.path.join(proj, ".run", "mod", "ext", LEARNED)


def load_learned(proj):
    """{"allow": [{rule, at, label, model, example, reason}], "deny": [{key, at, label, reason}]}."""
    d = fsutil.read_json(learned_path(proj), {})
    d = d if isinstance(d, dict) else {}
    return {"allow": [e for e in d.get("allow") or [] if isinstance(e, dict) and isinstance(e.get("rule"), str)],
            "deny": [e for e in d.get("deny") or [] if isinstance(e, dict) and isinstance(e.get("key"), str)]}


def allow_rules(proj):
    return [e["rule"] for e in load_learned(proj)["allow"]]


def remember(proj, kind, entry):
    """Add one learned decision (`allow` by its rule, `deny` by its key) unless it is there; under the file's lock."""
    field = "rule" if kind == "allow" else "key"
    path = learned_path(proj)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def fn(data):
        data = data if isinstance(data, dict) else {}
        items = [e for e in data.get(kind) or [] if isinstance(e, dict)]
        if any(e.get(field) == entry.get(field) for e in items):
            return None
        data[kind] = items + [entry]
        return data
    fsutil.locked_update(path, fn)


def forget(proj, rule):
    """Drop a learned allow rule or a remembered refusal (by its rule or key); the number dropped."""
    path = learned_path(proj)
    if not os.path.isfile(path):
        return 0
    gone = [0]

    def fn(data):
        data = data if isinstance(data, dict) else {}
        for kind, field in (("allow", "rule"), ("deny", "key")):
            items = [e for e in data.get(kind) or [] if isinstance(e, dict)]
            keep = [e for e in items if e.get(field) != rule]
            gone[0] += len(items) - len(keep)
            data[kind] = keep
        return data if gone[0] else None
    fsutil.locked_update(path, fn)
    return gone[0]


def learned_match(proj, tool, inp):
    """("deny", entry) for a call refused before; ("allow", entry) for a simple command a learned rule covers (one
    learned after this coder started); (None, None) otherwise."""
    data = load_learned(proj)
    key = deny_key(tool, inp)
    for e in data["deny"]:
        if e["key"] == key:
            return "deny", e
    if tool == "Bash":
        segs = segments((inp or {}).get("command"))
        if segs and len(segs) == 1:
            words = segs[0]
            while words and _ENV_RE.match(words[0]):
                words = words[1:]
            for e in data["allow"]:
                m = _RULE_RE.match(e["rule"])
                rw = m.group(1).split() if m else []
                if rw and words[:len(rw)] == rw:
                    return "allow", e
    return None, None


# --------------------------------------------------------------------------- the run's record

DECISIONS = "decisions.jsonl"   # <run dir>/decisions.jsonl: one line per decision of the hook


def record(run_dir, entry):
    if run_dir and os.path.isdir(run_dir):
        fsutil.append_line(os.path.join(run_dir, DECISIONS), json.dumps(entry, ensure_ascii=False))


def decisions(run_dir):
    out = []
    path = os.path.join(run_dir, DECISIONS) if run_dir else ""
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def tally(decs):
    """The hook's decisions in a run: the judge's allows and refusals (an unanswered call is a refusal), what it
    learned, the fixed rules' refusals (network, outside), the calls learned decisions answered again."""
    t = {"allowed": 0, "denied": 0, "learned": [], "network": 0, "outside": 0, "again": 0, "judge_s": 0.0,
         "examples": []}
    for d in decs:
        by, beh = d.get("by"), d.get("decision")
        if by in ("network", "outside"):
            t[by] += 1
        elif by == "learned":
            t["again"] += 1
        elif by in ("judge", "judge-error"):
            t["allowed" if beh == "allow" else "denied"] += 1
            t["judge_s"] += float(d.get("s") or 0)
            if d.get("remember") and d.get("remember") not in t["learned"]:
                t["learned"].append(d["remember"])
        if beh == "deny" and len(t["examples"]) < 3:
            t["examples"].append("%s: %s" % (d.get("what") or d.get("tool"), d.get("reason") or by))
    t["judge_s"] = round(t["judge_s"], 1)
    return t
