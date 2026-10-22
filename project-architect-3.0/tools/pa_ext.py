#!/usr/bin/env python
"""pa_ext.py -- external coders: one config folder per backend under ~/.claude/pa3-ext/ (3.3 T2).

setup   interactive: add or update a backend (URL, key, models); saves only when every model answers
check   one tiny request per config and model, one line per config; exit 0 when every model is ok (and, on
        Linux, bubblewrap and socat are installed)
run     <config>/<alias> <brief file>: one self-contained headless `claude -p` coder on that backend (3.3 T5);
        prints its report, its HEALTH block when it met friction (3.30) and one summary line; exit 0 with a
        report, 3 without, 4 when blocked by a lane/label; `run --resume <label>` restarts a cut-off run (its
        transcript, else its brief and what it did)
say     <label> <text>: queue a correction for a running external coder
ask     <question>: inside an external coder, a code lookup through the mod's retriever (prints its answer)
health  <label>: the HEALTH block of a running or finished external coder, from its transcript (3.30)
runs    for the phase-end external review (3.30): the runs ended since the last review and what is in force (notes,
        the allow rules health proposals added, what PA3's judge learned, learning mode, the settings); --count
        only the number; --mark stamps the review
set     the project's pa.json `external` block (3.3 T6): --coder, --lanes, --max-coders, --escalate, --use,
        --remind, --effort; --show (or no flags) prints it; checked, and only that block's text changes. What a
        run teaches (3.30, applied by the expert since 3.30.1, each an FYI to Claude with its undo): --note/--drop-note
        (this project's external coders), --model-note <config>/<alias> <text> / --drop-model-note <config>/<alias> <n>
        (every coder on that model: <folder>/notes/<alias>.md); --allow <rule> (the project's .claude/settings.json);
        --forget <rule> (drop what the judge learned); --dir/--drop-dir <path> (a folder outside the project its
        tools read, given to coders as a working directory)

run shows the coder like an in-house one (3.3 T5.c2): feed records, a facts file, journal entries, a finish card
and the session's cost file (ext.json), when the parent session's .run/mod/<sid>/ exists.

3.30.1: a coder reads only the project and system folders and writes only inside the project (the read fence and
project-anchored edit rules); a call no rule approves goes to PA3's judge (pa/hooks/permission_request.py: no network,
nothing outside the project, the project's learned decisions, then Opus 5.5); a model new to the project runs one
coder at a time until a run comes back clean (learning mode).

Stdlib only. The key is written only to <folder>/settings.json; it never reaches the output,
status.json, pa3-ext.json or an exception message.
"""

import argparse
import difflib
import getpass
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://openrouter.ai/api"
SANDBOX_LINE = ("this machine: external coders need bubblewrap and socat on Linux: "
                "sudo apt-get install -y bubblewrap socat")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def _pa_home():
    """PA_MOD_PA3_DIR when it holds pa/, else <script>/.. when it holds pa/, else ~/.claude/pa3."""
    home = os.environ.get("PA_MOD_PA3_DIR") or ""
    if not (home and os.path.isdir(os.path.join(home, "pa"))):
        home = os.path.dirname(HERE)
        if not os.path.isdir(os.path.join(home, "pa")):
            home = os.path.join(os.path.expanduser("~"), ".claude", "pa3")
    if home not in sys.path:
        sys.path.insert(0, home)


_pa_home()
from pa import ext_rules, fsutil, paths  # noqa: E402


# --------------------------------------------------------------------------- files

def root():
    return os.path.join(paths.claude_dir(), "pa3-ext")


def load_configs():
    """[(name, folder, meta)] sorted by name, for the folders holding pa3-ext.json."""
    r = root()
    out = []
    if not os.path.isdir(r):
        return out
    for name in sorted(os.listdir(r)):
        folder = os.path.join(r, name)
        meta = fsutil.read_json(os.path.join(folder, "pa3-ext.json"))
        if os.path.isdir(folder) and isinstance(meta, dict):
            out.append((name, folder, meta))
    return out


def _read_key(folder):
    s = fsutil.read_json(os.path.join(folder, "settings.json")) if folder else None
    env = s.get("env") if isinstance(s, dict) else None
    key = env.get("ANTHROPIC_AUTH_TOKEN") if isinstance(env, dict) else None
    return key if isinstance(key, str) else ""


def _write_settings(folder, url, key):
    """settings.json, mode 0600 from creation on POSIX (never chmod'ed after the key is on disk)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "settings.json")
    text = json.dumps({"env": {"ANTHROPIC_BASE_URL": url, "ANTHROPIC_AUTH_TOKEN": key,
                               "ANTHROPIC_API_KEY": ""}}, indent=2) + "\n"
    tmp = "%s.tmp.%d" % (path, os.getpid())
    if os.path.exists(tmp):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _write_meta(folder, name, url, models):
    obj = {"name": name, "url": url, "models": models, "tested_at": int(time.time())}
    fsutil.atomic_write_text(os.path.join(folder, "pa3-ext.json"),
                             json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _status(ok, text):
    obj = {"at": int(time.time()), "ok": bool(ok), "text": text}
    fsutil.atomic_write_text(os.path.join(root(), "status.json"),
                             json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------- requests

def _scrub(text, key):
    return text.replace(key, "***") if key else text


def _headers(key):
    return {"Authorization": "Bearer " + key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"}


def _post(url, key, model, max_tokens, timeout=60):
    """One test request. Returns (code, error): code 200 is ok, None is a connection error."""
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": "Reply with the word ok."}]}).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/v1/messages", data=body, method="POST",
                                 headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        finally:
            e.close()
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        return None, _scrub("connection failed: %s" % (reason,), key)
    if code == 200:
        return 200, ""
    msg = None
    try:
        msg = json.loads(raw)["error"]["message"]
    except Exception:
        pass
    if not isinstance(msg, str) or not msg.strip():
        msg = raw[:200]
    return code, _scrub(("%d %s" % (code, msg.strip())).strip(), key)


def _fetch_names(url, key):
    """{model id: display name} from one GET <url>/v1/models; {} on any failure (silent)."""
    req = urllib.request.Request(url.rstrip("/") + "/v1/models", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        out = {}
        for m in data.get("data") or []:
            if isinstance(m, dict) and isinstance(m.get("id"), str) and isinstance(m.get("name"), str):
                out[m["id"]] = m["name"]
        return out
    except Exception:
        return {}


# --------------------------------------------------------------------------- host advice (OpenRouter)

RUN_TOKENS = (883000, 48000, 5100)  # a typical coder run: cached input, new input, output tokens
HIGH_PRECISION = ("bf16", "fp16", "fp32", "fp8")


def is_openrouter(url):
    return (urllib.parse.urlparse(url).hostname or "").lower() == "openrouter.ai"


def _fetch_endpoints(url, model_id):
    """(data, None) from GET <url>/v1/models/<id>/endpoints (no auth), else (None, reason)."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/v1/models/" + model_id + "/endpoints")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace")).get("data")
    except urllib.error.HTTPError as e:
        e.close()
        return None, "HTTP %d" % e.code
    except Exception as e:
        return None, str(getattr(e, "reason", None) or e) or type(e).__name__
    if not isinstance(data, dict) or not isinstance(data.get("endpoints"), list) or not data["endpoints"]:
        return None, "no endpoints listed"
    return data, None


def _price(p, k):
    try:
        return float(p[k])
    except Exception:
        return None


def rank_hosts(endpoints):
    """[(group, cost, endpoint)] best first: fp8 and up, then unstated, then lower precision; then cost."""
    rows = []
    for e in endpoints:
        if not isinstance(e, dict):
            continue
        q = str(e.get("quantization") or "").lower()
        group = 1 if q in HIGH_PRECISION else 2 if q in ("", "unknown") else 3
        p = e.get("pricing") if isinstance(e.get("pricing"), dict) else {}
        prompt = _price(p, "prompt") or 0.0
        cache = _price(p, "input_cache_read")
        cost = (RUN_TOKENS[0] * (prompt if cache is None else cache) + RUN_TOKENS[1] * prompt
                + RUN_TOKENS[2] * (_price(p, "completion") or 0.0))
        rows.append((group, cost, str(e.get("provider_name") or ""), e))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [(g, c, e) for g, c, _n, e in rows]


def host_advice(models, hosts, out):
    """The per-model host lists, the guardrail line and the one-key-per-model advice (advice only)."""
    picks = []
    for alias, m in models.items():
        data = hosts.get(m["id"])
        if not data:
            continue
        rows = rank_hosts(data["endpoints"])
        if not rows:
            continue
        rec = next((i for i, r in enumerate(rows) if r[0] == 1), 0)
        out("Hosts for %s (%s), best first (a typical coder run: 883K cached, 48K new, 5K output tokens):"
            % (alias, m["id"]))
        for i, (_g, cost, e) in enumerate(rows[:6]):
            q = str(e.get("quantization") or "")
            out("  %s%s  %s  $%.3f%s" % (e.get("provider_name") or "?",
                                         " (%s)" % e["tag"] if e.get("tag") else "",
                                         q if q and q.lower() != "unknown" else "unstated", cost,
                                         "  <- recommended" if i == rec else ""))
        if any(r[0] == 3 for r in rows):
            out("  Lower precision (fp4) is cheaper but may code worse.")
        picks.append(str(rows[rec][2].get("provider_name") or "?"))
    if not picks:
        return
    names = list(dict.fromkeys(picks))
    out("To keep each model on its host, add a guardrail to this key on openrouter.ai: Guardrails, allowed "
        "providers: %s. Claude Code cannot choose the host itself." % ", ".join(names))
    if len(models) >= 2 and len(names) > 1:
        out("These models are best on different hosts. One guardrail covers the whole key, so use one key "
            "per model: choose New config for each, with its own key.")


# --------------------------------------------------------------------------- defaults

def _slug(s):
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "ext"


def default_name(url, existing=None):
    """openrouter / local (local-<port> when `local` holds another URL) / the host's second-level label."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if host == "openrouter.ai":
        return "openrouter"
    if host in ("localhost", "127.0.0.1", "::1"):
        other = (existing or {}).get("local")
        if other is not None and other.rstrip("/") != url.rstrip("/"):
            try:
                port = p.port
            except ValueError:
                port = None
            return "local-%d" % (port or (443 if p.scheme == "https" else 80))
        return "local"
    labels = [x for x in host.split(".") if x]
    return _slug(labels[-2] if len(labels) >= 2 else (labels[0] if labels else "ext"))


def default_alias(model_id, taken=()):
    """The id's part after the last `/`, up to the first `-` or `.`; `-2`, `-3` ... on a clash."""
    base = _slug(re.split(r"[-.]", model_id.rsplit("/", 1)[-1], maxsplit=1)[0])
    alias, n = base, 2
    while alias in taken:
        alias = "%s-%d" % (base, n)
        n += 1
    return alias


def default_display(model_id, names=None):
    n = (names or {}).get(model_id)
    if n:
        return re.sub(r"^[^:]*:\s+", "", n, count=1).strip() or n
    return model_id.rsplit("/", 1)[-1].replace("-", " ")


# --------------------------------------------------------------------------- setup

def _ask(ask, prompt, default=""):
    a = ask("%s [%s]: " % (prompt, default) if default else "%s: " % prompt).strip()
    return a or default


def _edit(current, configs, ask, ask_secret, out):
    """One new or updated config. Returns None (nothing attempted), True (saved) or False (failed)."""
    if current:
        cname, cfolder, cmeta = current
        cur_url = cmeta.get("url") or DEFAULT_URL
        cur_models = cmeta.get("models") if isinstance(cmeta.get("models"), dict) else {}
    else:
        cname, cfolder, cur_url, cur_models = "", "", DEFAULT_URL, {}
    url = _ask(ask, "URL", cur_url).rstrip("/")
    existing = {n: m.get("url", "") for n, _, m in configs}
    dname = cname
    if not dname:
        base = dname = default_name(url, existing)
        n = 2
        # a folder holding a different URL, or any folder for a New config: offer <name>-2, -3 ...
        while os.path.isdir(os.path.join(root(), dname)) and (
                not current or existing.get(dname, "").rstrip("/") != url):
            dname = "%s-%d" % (base, n)
            n += 1
    while True:
        name = _ask(ask, "Name", dname)
        if NAME_RE.match(name):
            break
        out("A name uses a-z, 0-9 and -, starting with a letter or digit.")
    folder = os.path.join(root(), name)
    out("The key is written only to %s on this machine and sent only to %s for the test requests."
        % (os.path.join(folder, "settings.json"), url))
    old_key = _read_key(cfolder)
    key = ask_secret("Key (Enter keeps the current one): " if old_key else "Key: ").strip() or old_key
    if not key:
        out("no key given, nothing saved")
        return None
    cur_ids = ", ".join(m.get("id", "") for m in cur_models.values() if isinstance(m, dict))
    while True:
        ids = []
        for x in _ask(ask, "Model ids (comma-separated)", cur_ids).split(","):
            if x.strip() and x.strip() not in ids:
                ids.append(x.strip())
        if ids:
            break
        out("At least one model id is required.")
    by_id = {m.get("id"): (a, m) for a, m in cur_models.items() if isinstance(m, dict)}
    names = _fetch_names(url, key)
    hosts, host_err = {}, {}
    if is_openrouter(url):
        for mid in ids:
            data, err = _fetch_endpoints(url, mid)
            if data:
                hosts[mid] = data
                if isinstance(data.get("name"), str) and data["name"].strip():
                    names[mid] = data["name"]
            else:
                host_err[mid] = err
    models = {}
    for mid in ids:
        old = by_id.get(mid)
        dflt = old[0] if old and old[0] not in models else default_alias(mid, models)
        while True:
            alias = _ask(ask, "Alias for %s" % mid, dflt)
            if NAME_RE.match(alias) and alias not in models:
                break
            out("An alias uses a-z, 0-9 and -, and is unique in this config.")
        if mid in host_err:
            out("%s: host list unavailable (%s)" % (alias, host_err[mid]))
        disp = _ask(ask, "Display name for %s" % alias,
                    (old[1].get("name") if old else "") or default_display(mid, names))
        models[alias] = {"id": mid, "name": disp}
    fails = []
    for alias, m in models.items():
        code, err = _post(url, key, m["id"], 16)
        if code == 200:
            out("%s (%s): ok" % (alias, m["id"]))
        else:
            out("%s (%s): failed: %s" % (alias, m["id"], err))
            fails.append((alias, err))
    if fails:
        alias, err = fails[0]
        out("Nothing saved: %s failed: %s" % (alias, err))
        _status(False, "%s not saved: %s failed: %s" % (name, alias, err))
        return False
    _write_settings(folder, url, key)
    _write_meta(folder, name, url, models)
    text = "%s saved: %s" % (name, ", ".join("%s ok" % a for a in models))
    _status(True, text)
    out(text)
    if hosts:
        host_advice(models, hosts, out)
    return True


def setup(ask=input, ask_secret=getpass.getpass, out=print):
    """The menu loop. Exit code: 0 when the last attempt saved or nothing was attempted, else 1."""
    last = None
    first = True
    while True:
        configs = load_configs()
        try:
            current = None
            if configs or not first:
                for i, (name, _folder, meta) in enumerate(configs, 1):
                    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
                    out("%d. %s  %s  (%s)" % (i, name, meta.get("url", ""), ", ".join(models)))
                out("%d. New config" % (len(configs) + 1))
                out("%d. Exit" % (len(configs) + 2))
                choice = ask("Choose: ").strip()
                if not choice.isdigit() or not 1 <= int(choice) <= len(configs) + 2:
                    out("Type a number from the list.")
                    continue
                if int(choice) == len(configs) + 2:
                    break
                if int(choice) <= len(configs):
                    current = configs[int(choice) - 1]
            first = False
            result = _edit(current, configs, ask, ask_secret, out)
        except (EOFError, KeyboardInterrupt):
            out("")
            break
        if result is not None:
            last = result
    from pa.install import hold_if_double_clicked
    hold_if_double_clicked()
    return 0 if last in (None, True) else 1


# --------------------------------------------------------------------------- check

def sandbox_missing():
    """Linux: a coder's commands run in Claude Code's sandbox, which keeps the key out; it needs bwrap and socat,
    and without them no run starts or every command fails."""
    return sys.platform.startswith("linux") and not all(shutil.which(p) for p in ("bwrap", "socat"))


def check(out=print):
    configs = load_configs()
    if not configs:
        out("no external coders configured on this machine: run setup-external")
        return 1
    all_ok = True
    for name, folder, meta in configs:
        key, url = _read_key(folder), meta.get("url", "")
        models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
        res = []
        for alias, m in models.items():
            code, err = _post(url, key, (m or {}).get("id", ""), 16)
            res.append((alias, code == 200, err))
        oks = [r for r in res if r[1]]
        if len(oks) == len(res):
            out("%s: connected (%s)" % (name, ", ".join("%s ok" % a for a, _, _ in res)))
            continue
        all_ok = False
        if not oks:
            out("%s: not connected (%s)" % (name, res[0][2]))
        else:
            out("%s: %s" % (name, ", ".join("%s ok" % a if ok else "%s failed: %s" % (a, e)
                                            for a, ok, e in res)))
    if sandbox_missing():
        out(SANDBOX_LINE)
        all_ok = False
    return 0 if all_ok else 1


# --------------------------------------------------------------------------- run: resolve

_CLAUDE = None      # tests: the argv prefix in place of the claude executable (as pa3_update.py's _SPAWNER)
LABEL_RE = re.compile(r"CODER TASK (T\d+(?:\.\d+)*\.c\d+)")
SAFE_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
BASE_ALLOW = ["Read", "Edit", "Write", "MultiEdit", "Grep", "Glob"]   # the bench's coders (tools/bench.py)
# 3.30.1, the fence: a run's coder edits only inside the project (an Edit rule passed as a flag anchors at the working
# directory and covers Write and MultiEdit; reads inside need no rule), and the read fence keeps its file tools and
# its sandboxed commands out of the home folder and other drives. The project's bare or outside file-tool rules are
# dropped (FILE_RULE_RE). Web tools are off: web needs go to the expert's web retriever.
CODER_ALLOW = ["Edit(/**)"]
CODER_DENY = ["Agent", "WebFetch", "WebSearch"]
FILE_RULE_RE = re.compile(r"(Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|LS)(\((//|~|[A-Za-z]:).*\))?\Z")
HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
# 3.30.1: the judge, in the coder's own settings only (pa/hooks/permission_request.py); its 150 s judge fits.
PERM_HOOK = {"PermissionRequest": [{"hooks": [{"type": "command", "timeout": 180,
                                               "command": "\"{{PY_EXE}}\" -I -X utf8 {{PA3}}/pa_hook.py permission_request"}]}]}
FENCE = {"blockReadsOutsideWorkingDirectories": True}
EXT_WINDOW = "1000000"      # the context window Claude Code is told for an external model (child_settings)
EXT_MAX_OUTPUT = "65536"    # output per request (Claude Code's default is 32000): room for max effort's reasoning
EFFORTS = ("low", "medium", "high", "max")      # `set --effort`; `default` drops it (EXT_EFFORT)
# The external coders' effort when the project sets none (the developer, 2026-10-21): on the bench, DeepSeek at high
# passed what max passed on the light and full suites for about 20% less. The coder agent file's effort is tuned for
# Claude, and DeepSeek runs medium as high anyway.
EXT_EFFORT = "high"
# 3.3 T10b: the external coder's help, appended after the preamble (PA_EXT_HELP=0 leaves it out: the A/B). The lines
# the A/B showed working (2026-10-21, DeepSeek): `cd`/`git -C` denials 36 to 1, edits outside the brief's files 4/6
# to 0/6, re-tested after the last edit 4/6 to 6/6. Turns, budget and constraints moved nothing and went.
HELP_TEXT = """# Working well in this harness
You run in Claude Code, a harness tuned for Claude models. These habits keep you in step with it.
- Commands: you start in the project root. Never prefix a command with `cd <dir> &&` and never use `git -C`: use
  relative paths. A chained command is denied when any part of it is not allowed, so keep each Bash call to commands
  the project allows.
- Scope: edit only the files the brief names. Write scratch scripts and notes under .run/ with the Write tool, which
  makes the folders (no mkdir); never put them in the project's own folders.
- Checks: after your last code change, run the brief's test or check again and report its result."""
FLAGS = ("autoCompactEnabled", "todoFeatureEnabled", "awaySummaryEnabled", "promptSuggestionEnabled")
MODEL_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
              "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET",
             "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID",
             "AI_AGENT", "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH")
HEARTBEAT_S = 30
POLL_S = 1.0


class Blocked(Exception):
    """One `blocked: <text>` line and the exit code."""

    def __init__(self, text, code=3):
        Exception.__init__(self, text)
        self.code = code


def _fwd(p):
    return p.replace("\\", "/")


def find_root(start=None):
    """Walk up from cwd to the folder holding .claude/pa.json."""
    d = os.path.abspath(start or os.getcwd())
    while not os.path.isfile(os.path.join(d, ".claude", "pa.json")):
        parent = os.path.dirname(d)
        if parent == d:
            raise Blocked("no PA3 project here (no .claude/pa.json from %s up)" % _fwd(start or os.getcwd()))
        d = parent
    return d


def resolve_model(target):
    """`<config>/<alias>` -> (config, alias, folder, model id, display name)."""
    config, _, alias = target.partition("/")
    folder = os.path.join(root(), config)
    meta = fsutil.read_json(os.path.join(folder, "pa3-ext.json")) if NAME_RE.match(config) else None
    if not isinstance(meta, dict):
        raise Blocked("unknown external config %s (run setup-external)" % (config or target))
    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    m = models.get(alias)
    if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"]:
        raise Blocked("unknown model %s in %s (has: %s)" % (alias or "''", config, ", ".join(models) or "none"))
    return config, alias, folder, m["id"], m.get("name") or m["id"]


def brief_label(path, text):
    """The brief's first line `CODER TASK T<n>.c<k>`, else the file's stem (path-safe)."""
    m = LABEL_RE.search(text.splitlines()[0] if text else "")
    if m:
        return m.group(1)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.splitext(os.path.basename(path))[0]).strip("-.")
    return stem or "brief"


def parent_sid(proj):
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not sid:
        last = fsutil.read_json(os.path.join(proj, ".run", "mod", "last.json"))
        sid = last.get("sid") if isinstance(last, dict) else ""
    return sid if isinstance(sid, str) else ""


def _dict(x):
    return x if isinstance(x, dict) else {}


def project_settings(proj):
    """[.claude/settings.json, .claude/settings.local.json] as dicts ({} when missing)."""
    return [_dict(fsutil.read_json(os.path.join(proj, ".claude", n))) for n in ("settings.json",
                                                                                "settings.local.json")]


def _py(pa_json):
    p = pa_json.get("python")
    return _fwd(p if isinstance(p, str) and p else sys.executable)


def _pa3_dir():
    """PA_MOD_PA3_DIR when it holds pa_hook.py, else the folder `pa` came from when it does, else ~/.claude/pa3."""
    import pa
    for d in (os.environ.get("PA_MOD_PA3_DIR") or "", os.path.dirname(os.path.dirname(os.path.abspath(pa.__file__)))):
        if d and os.path.isfile(os.path.join(d, "pa_hook.py")):
            return _fwd(d)
    return _fwd(os.path.join(paths.claude_dir(), "pa3"))


def lane_limit(pa_json, config):
    ext = _dict(pa_json.get("external"))
    for v in (_dict(ext.get("lanes")).get(config), ext.get("max_coders")):
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
    return 4


# --------------------------------------------------------------------------- run: generated files

def _strip_frontmatter(text):
    """(frontmatter lines, body) split on the first two `---` lines; ([], text) without frontmatter."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return [], text.replace("\r\n", "\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return [], text.replace("\r\n", "\n")


def parse_agent(path):
    """(name, description, tools without Agent…, effort, prompt) from an agent file."""
    text = fsutil.read_text(path, None)
    if text is None:
        raise Blocked("no agent file %s" % _fwd(path))
    front, body = _strip_frontmatter(text)
    meta = {}
    for ln in front:
        m = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip("\"'")
    tools = [t.strip() for t in re.findall(r"[^,(]+(?:\([^)]*\))?", meta.get("tools", ""))]
    tools = [t for t in tools if t and not t.startswith("Agent")]
    return (meta.get("name") or "pa-coder", meta.get("description", ""), tools,
            meta.get("effort") or "medium", body.strip("\n") + "\n")


def model_notes_path(folder, alias):
    """3.30: the notes for every coder on one model of a config, in every project (`set --model-note`)."""
    return os.path.join(folder, "notes", "%s.md" % alias)


MEMORY_LINES = 200      # Claude Code gives its agents the first 200 lines of a project's MEMORY.md


def memory_index(proj):
    """3.30.6: the project's auto-memory index (`<home>/.claude/projects/<slug>/memory/MEMORY.md`), which Claude Code
    attaches to in-house agents at their start, as an appended section: its first lines, and where its linked files
    are when the coder can read them (a memory folder inside the project); None when there is none."""
    folder = os.path.join(paths.projects_dir(), paths.project_slug(os.path.abspath(proj)), "memory")
    text = fsutil.read_text(os.path.join(folder, "MEMORY.md"), None)
    if text is None or not text.strip():
        return None
    lines = text.replace("\r\n", "\n").strip("\n").split("\n")[:MEMORY_LINES]
    real, root = os.path.realpath(folder), os.path.realpath(proj)
    if os.path.normcase(real).startswith(os.path.normcase(root) + os.sep):
        where = "the files it links are in %s/" % _fwd(os.path.relpath(real, root))
    else:
        where = "the files it links are outside the project: this index is all you get"
    return "# The project's memory index (MEMORY.md; %s)\n\n%s" % (where, "\n".join(lines))


def append_text(proj, py, pa_json=None, folder=None, alias=None, name=None):
    """The child's appended system prompt: the launcher's preamble, HELP_TEXT, this project's and this model's notes
    (3.30, from approved health proposals), the project-architect skill, CLAUDE.md and (3.30.6) the project's memory
    index, as in-house agents get them."""
    parts = ['You run on an external model through tools/pa_ext.py, outside Claude Code\'s subscription. '
             'Your Agent tool is off: for a code lookup run "%s" tools/pa_ext.py ask "<question>"; '
             'a code retriever answers in under 40 lines.\n'
             'Corrections from your expert arrive as messages in this conversation: apply each at your next step.\n'
             'A command that fails at once with a `bwrap:` line never ran: its sandbox collided with another '
             'coder\'s. Run it again; that is no reason to stop.'
             % py]
    if os.environ.get("PA_EXT_HELP") != "0":
        parts.append(HELP_TEXT)
    notes = [n for n in _dict(_dict(pa_json).get("external")).get("notes") or [] if isinstance(n, str) and n.strip()]
    if notes:
        parts.append("# Notes for external coders in this project\n" + "\n".join("- " + n.strip() for n in notes))
    mine = fsutil.read_text(model_notes_path(folder, alias), None) if folder and alias else None
    if mine is not None and mine.strip():
        parts.append("# Notes for coders on %s\n%s" % (name or alias, mine.replace("\r\n", "\n").strip("\n")))
    skill = fsutil.read_text(os.path.join(proj, ".claude", "skills", "project-architect", "SKILL.md"), None)
    if skill is not None:
        parts.append("# The project-architect skill (binding)\n\n" + _strip_frontmatter(skill)[1].strip("\n"))
    cmd = fsutil.read_text(os.path.join(proj, "CLAUDE.md"), None)
    if cmd is not None:
        parts.append("# CLAUDE.md (project instructions)\n\n" + cmd.replace("\r\n", "\n").strip("\n"))
    mem = memory_index(proj)
    if mem is not None:
        parts.append(mem)
    return "\n\n".join(parts) + "\n"


def child_settings(sets, model_id, snippet, perm_hook=None):
    """The generated settings.json: the project's env (no ANTHROPIC_*), our model pins, 5 hook events, 4 flags; with
    `perm_hook` (a run's coder, 3.30.1) the judge's PermissionRequest hook and the read fence too."""
    env = {}
    for s in sets:
        env.update((k, v) for k, v in _dict(s.get("env")).items() if not k.startswith("ANTHROPIC_"))
    for k in MODEL_VARS:
        env[k] = model_id
    # Claude Code assumes 200k for an unknown model and, with auto-compact off, stops a run at about 177k ("Prompt is
    # too long", 3.3 T10; its window-enforcement flag does not lift that). DeepSeek and GLM take about 1M at the
    # pinned hosts; a model with less ends at its own limit with the API's error.
    env.update({"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_MAX_CONTEXT_TOKENS": EXT_WINDOW,
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": EXT_MAX_OUTPUT})
    hooks = _dict(snippet.get("hooks"))
    out = {"env": env, "hooks": dict((e, hooks[e]) for e in HOOK_EVENTS if e in hooks)}
    if perm_hook:
        out["hooks"].update(perm_hook)
        out["permissions"] = dict(FENCE)
    for k in FLAGS:
        val = False
        for s in sets:
            if isinstance(s.get(k), bool):
                val = s[k]
        out[k] = val
    return out


def tool_lists(sets, snippet, learned=()):
    """(--allowedTools, --disallowedTools) lists; never a bare Bash, never pa3_update. 3.30.1: edits inside the
    project only (CODER_ALLOW, no bare or outside file-tool rule from the project), no web tools, and what PA3's judge
    learned for the project (`learned`)."""
    allow, deny = list(CODER_ALLOW), list(CODER_DENY)
    for s in sets + [snippet]:
        perms = _dict(s.get("permissions"))
        allow += [x for x in perms.get("allow") or [] if isinstance(x, str)]
        if s is not snippet:
            deny += [x for x in perms.get("deny") or [] if isinstance(x, str)]
    allow += [x for x in learned if isinstance(x, str)]
    allow = [x for x in allow if "pa3_update" not in x and x.strip() not in ("Bash", "Bash(*)", "Bash(:*)")
             and not FILE_RULE_RE.match(x.strip())]
    return list(dict.fromkeys(CODER_ALLOW + allow)), list(dict.fromkeys(deny))


def write_run_dir(run_dir, proj, pa_json, model_id, folder=None, alias=None, display=None):
    """settings.json, agents.json and append.md (with the model's notes when `folder` and `alias` are given);
    returns (agent name, effort, allow, deny)."""
    from pa.install import settings_merge
    py = _py(pa_json)
    snippet = settings_merge.fill(settings_merge.load_snippet(), py, _pa3_dir())
    perm = settings_merge.fill(json.loads(json.dumps(PERM_HOOK)), py, _pa3_dir())
    sets = project_settings(proj)
    name, desc, tools, effort, prompt = parse_agent(os.path.join(proj, ".claude", "agents", "pa-coder.md"))
    os.makedirs(os.path.join(run_dir, "in", "done"), exist_ok=True)
    fsutil.atomic_write_text(os.path.join(run_dir, "settings.json"), json.dumps(
        child_settings(sets, model_id, snippet, perm), ensure_ascii=False, indent=2) + "\n")
    fsutil.atomic_write_text(os.path.join(run_dir, "agents.json"), json.dumps(
        {name: {"description": desc, "prompt": prompt, "tools": tools}}, ensure_ascii=False, indent=2) + "\n")
    fsutil.atomic_write_text(os.path.join(run_dir, "append.md"), append_text(proj, py, pa_json, folder, alias, display))
    allow, deny = tool_lists(sets, snippet, ext_rules.allow_rules(proj))
    return name, effort, allow, deny


def claude_exe():
    """The claude to start (tests: _CLAUDE)."""
    if _CLAUDE is not None:
        return list(_CLAUDE)
    c = shutil.which("claude") or os.environ.get("CLAUDE_CODE_EXECPATH")
    if not c:
        raise Blocked("claude not found on PATH (and no CLAUDE_CODE_EXECPATH)")
    return [c]


def claude_argv(run_dir, agent, model_id, effort, sid, allow, deny, resume=False, dirs=()):
    """The child's argv; 3.30.1: `dirs`, the folders outside the project its tools read (`set --dir`), as
    working directories."""
    extra = []
    for d in dirs:
        extra += ["--add-dir", d]
    return claude_exe() + ["-p", "--agent", agent, "--agents", os.path.join(run_dir, "agents.json"), "--model",
                           model_id, "--effort", effort, "--resume" if resume else "--session-id", sid,
                           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                           "--replay-user-messages", "--settings", os.path.join(run_dir, "settings.json"),
                           "--setting-sources", "user", "--permission-prompts", "none", "--strict-mcp-config",
                           "--allowedTools", ",".join(allow), "--disallowedTools", ",".join(deny)] + extra + [
        "--append-system-prompt-file", os.path.join(run_dir, "append.md")]


def child_env(folder, target, model_id, psid, label, extra=None):
    """3.30.1 `extra`: what the judge's hook reads (PA_EXT_PROJECT, PA_EXT_RUN_DIR, PA_EXT_DIRS, PA_EXT_PY,
    PA_EXT_CLAUDE)."""
    env = dict((k, v) for k, v in os.environ.items()
               if k.upper() not in STRIP_ENV and not k.upper().startswith("ANTHROPIC_"))
    env.update({"CLAUDE_CONFIG_DIR": folder, "PA_EXT": target, "PA_EXT_MODEL": model_id, "PA_EXT_PARENT": psid,
                "PA_EXT_LABEL": label, "PYTHONIOENCODING": "utf-8"})
    env.update(extra or {})
    return env


def ext_dirs(pa_json):
    """3.30.1: the project's declared folders outside it (`external.dirs`, `set --dir`), expanded, those that
    exist."""
    out = []
    for d in _dict(pa_json.get("external")).get("dirs") or []:
        if isinstance(d, str) and d.strip():
            p = os.path.abspath(os.path.expanduser(d.strip()))
            if os.path.isdir(p):
                out.append(p)
    return out


# --------------------------------------------------------------------------- run: records

def _ext_dir(proj):
    return os.path.join(proj, ".run", "mod", "ext")


def _running(rec):
    return isinstance(rec, dict) and rec.get("status") == "running" and fsutil.pid_alive(rec.get("pid"))


NOTES_LOG = "notes.json"    # <proj>/.run/mod/ext/notes.json [{at, kind, target, text}]: notes `set` added (3.30.1)


def notes_log(proj):
    d = fsutil.read_json(os.path.join(_ext_dir(proj), NOTES_LOG), [])
    return [n for n in d if isinstance(n, dict)] if isinstance(d, list) else []


def _log_note(proj, kind, target, text):
    path = os.path.join(_ext_dir(proj), NOTES_LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fsutil.locked_update(path, lambda d: (d if isinstance(d, list) else []) + [
        {"at": int(time.time()), "kind": kind, "target": target, "text": text}], default=[])


def run_clean(proj, rec):
    """3.30.1, learning mode: a finished run met no friction: done, its report starting with a status word, no denial
    of any kind (rules, the fixed rules, the judge), no read-guard failure, no nudge, and no note added for it after it
    ended (the expert's check-in found nothing to teach). 3.30.4: what the judge allowed or learned is no friction: a
    learned rule reaches the coders already running at their next call, a note only those that start after it (bfm:
    every batch's own scratch scripts were learned, so no run could come back clean). 3.30.7: nor is a sandbox
    (`bwrap:`) failure, two coders' sandboxes colliding, which the coder just runs again (bfm G18: counted, running
    side by side would put a model back to one at a time by itself)."""
    h = _dict(rec.get("health"))
    if rec.get("status") != "done" or rec.get("report_word") is False or not h:
        return False
    j = _dict(h.get("judge"))
    if any(h.get(k) for k in ("denied", "guard", "nudges")) or any(
            j.get(k) for k in ("denied", "network", "outside")):
        return False
    ended, target = int(rec.get("ended") or 0), "%s/%s" % (rec.get("config"), rec.get("alias"))
    return not any(int(n.get("at") or 0) >= ended and (n.get("kind") == "project" or n.get("target") == target)
                   for n in notes_log(proj))


def last_finished(proj, config, alias):
    """The model's last finished run record in this project, or None."""
    best = None
    for f in glob.glob(os.path.join(_ext_dir(proj), "*.json")):
        r = fsutil.read_json(f)
        if (isinstance(r, dict) and r.get("config") == config and r.get("alias") == alias and r.get("ended")
                and r.get("status") in ("done", "blocked") and (best is None or int(r["ended"]) > int(best["ended"]))):
            best = r
    return best


def learning(proj, config, alias):
    """3.30.1: (True, why) while a model learns this project: none of its runs has finished here, or its last one was
    not clean; (False, the clean run's label) once its last finished run was clean."""
    last = last_finished(proj, config, alias)
    if last is None:
        return True, "no run of it has finished in this project yet"
    if run_clean(proj, last):
        return False, last.get("label")
    return True, "the last one, %s, met friction" % last.get("label")


def claim(proj, rec, limit):
    """Write the running record unless the label already runs, the config's lane is full, or (3.30.1, learning mode)
    the model is still learning this project and one of its coders runs (exit 4 each)."""
    d = _ext_dir(proj)
    os.makedirs(d, exist_ok=True)
    with fsutil.file_lock(os.path.join(d, "lanes")):
        n, mine = 0, []
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json"):
                continue
            other = fsutil.read_json(os.path.join(d, f))
            if not _running(other):
                continue
            if other.get("label") == rec["label"]:
                raise Blocked("%s is already running" % rec["label"], 4)
            if other.get("config") == rec["config"]:
                n += 1
                if other.get("alias") == rec.get("alias"):
                    mine.append(other.get("label"))
        if n >= limit:
            raise Blocked("the %s lane is full (%d running)" % (rec["config"], n), 4)
        if mine:
            on, why = learning(proj, rec["config"], rec.get("alias"))
            if on:
                raise Blocked("learning mode: %s runs one coder at a time in this project until a run comes back "
                              "clean (%s); start %s again when %s reports" % (
                                  rec.get("name") or rec.get("model"), why, rec["label"], mine[0]), 4)
        _update(proj, rec["label"], lambda d0: rec)


def _update(proj, label, fn):
    def apply(data):
        out = fn(data)
        return out if out is not None else data
    fsutil.locked_update(os.path.join(_ext_dir(proj), label + ".json"), apply)


def _set(proj, label, **kw):
    def fn(data):
        data.update(kw)
        return data
    _update(proj, label, fn)


# --------------------------------------------------------------------------- run: what the mod shows (3.3 T5.c2)

EDIT_TOOLS = ("Edit", "MultiEdit", "Write")
CONTEXT = 3             # pa-watch edit.ts CONTEXT: context lines around each change
EDIT_CAP = 30           # pa-watch edit.ts EDIT_CAP: the rows an edit card shows at most
CARD_FILES = 8          # the finish card's commit files at most
CODER_SQUARE = "\U0001F7E9"     # card.ts SQUARE.coder
RESUME_TEXT = "You were cut off. Continue the task from where you stopped, then report as usual."
STATUS_RE = re.compile(r"\s*(?:STATUS:\s*)?(done|partial|blocked|question)\b[\s:.,;-]*", re.I)
REPORT_RE = re.compile(r"\s*(?:STATUS:\s*)?(?:done|partial|blocked|question|handoff)\b", re.I)
NUDGE_TEXT = "Go on with the task. End your turn only with your report: its first word is done, question or blocked."
NUDGES = 2          # a model may end its turn on its plan (DeepSeek did, 3.3 T10); at most this many nudges a run
COMMIT_RE = re.compile(r"\bcommit(?:ted)?\b[\s:=*`'\"]*([0-9a-f]{7,40})\b", re.I)
_LOCK = threading.Lock()
_SEQ = {"name": 0, "journal": 0}


def _now_ms():
    return int(time.time() * 1000)


def _write_new(d, name_of, text_of):
    """The first free name_of(ms, seq) in d, written as text_of(name, ms) (temp name, then os.replace); its name."""
    os.makedirs(d, exist_ok=True)
    with _LOCK:
        ms = _now_ms()
        while True:
            _SEQ["name"] += 1
            name = name_of(ms, _SEQ["name"])
            if not os.path.exists(os.path.join(d, name)):
                break
        fsutil.atomic_write_text(os.path.join(d, name), text_of(name, ms))
    return name


def request(mod, kind, input_of):
    """An inbox request <mod>/inbox/<ms>-<seq>-<kind>.json (mod/pa inbox.ts requestName); returns its id."""
    name = _write_new(os.path.join(mod, "inbox"), lambda ms, seq: "%d-%d-%s.json" % (ms, seq, kind),
                      lambda n, ms: json.dumps({"id": n[:-5], "kind": kind, "caller": "pa_ext",
                                                "input": input_of(n[:-5]), "at": ms}, ensure_ascii=False) + "\n")
    return name[:-5]


def _lines_of(text):
    """edit.ts linesOf: CRLF and CR as LF, control characters but tab as spaces, no empty last line."""
    if not text:
        return []
    ls = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
    if ls[-1] == "":
        ls.pop()
    return ls


def _last_line(text):
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines[-1] if lines else ""


def _rows(a, b):
    """Every row (sign, old pos, new pos, text) of the line diff of a to b, removals first."""
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [(" ", i1 + k, j1 + k, a[i1 + k]) for k in range(i2 - i1)]
        else:
            rows += [("-", k, j1, a[k]) for k in range(i1, i2)]
            rows += [("+", i2, k, b[k]) for k in range(j1, j2)]
    return rows


def _hunks_of(rows):
    """edit.ts hunksOf: the changed rows with CONTEXT rows around each; hunks closer than 2 x CONTEXT merge."""
    out, lo, hi = [], -1, -1
    for k in [k for k, r in enumerate(rows) if r[0] != " "]:
        if lo >= 0 and k - CONTEXT <= hi + CONTEXT + 1:
            hi = k
            continue
        if lo >= 0:
            out.append(rows[max(0, lo - CONTEXT):hi + CONTEXT + 1])
        lo = hi = k
    if lo >= 0:
        out.append(rows[max(0, lo - CONTEXT):hi + CONTEXT + 1])
    return out


def _hunk_text(rows):
    """edit.ts hunkText: `@@ -a,b +c,d @@`, then each row with its sign."""
    ol = sum(1 for r in rows if r[0] != "+")
    nl = sum(1 for r in rows if r[0] != "-")
    o, n = rows[0][1] + (1 if ol else 0), rows[0][2] + (1 if nl else 0)
    return "\n".join(["@@ -%d,%d +%d,%d @@" % (o, ol, n, nl)] + [r[0] + r[3] for r in rows])


def edit_view(tool, inp, path, created):
    """The FeedEdit {path, created, add, del, hunks, more} of one Edit, MultiEdit or Write, from its input."""
    if tool == "Write":
        c = inp.get("content")
        hunks = [[("+", 0, k, t) for k, t in enumerate(_lines_of(c if isinstance(c, str) else ""))]]
    else:
        pairs = inp.get("edits") if tool == "MultiEdit" else [inp]
        hunks = []
        for e in pairs if isinstance(pairs, list) else []:
            e = _dict(e)
            old, new = e.get("old_string"), e.get("new_string")
            hunks += _hunks_of(_rows(_lines_of(old if isinstance(old, str) else ""),
                                     _lines_of(new if isinstance(new, str) else "")))
    hunks = [h for h in hunks if h]
    rows = [r for h in hunks for r in h]
    out, room, more = [], EDIT_CAP, 0
    for h in hunks:
        kept = h[:max(room, 0)]
        more += len(h) - len(kept)
        room -= len(kept)
        if kept:
            out.append(_hunk_text(kept))
    return {"path": path, "created": created, "add": sum(1 for r in rows if r[0] == "+"),
            "del": sum(1 for r in rows if r[0] == "-"), "hunks": out, "more": more}


def _result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return " ".join(x.get("text") for x in c if isinstance(x, dict) and isinstance(x.get("text"), str))
    return c if isinstance(c, str) else ""


def _git(proj, args):
    try:
        p = subprocess.run(["git"] + args, cwd=proj, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""


def report_status(report):
    """(done, commit): its status word is done (no word: a report at all), and the hash after commit/Committed."""
    m = STATUS_RE.match(report.split("\n", 1)[0])
    c = COMMIT_RE.search(report)
    return (m.group(1).lower() == "done") if m else bool(report.strip()), c.group(1).lower() if c else ""


def finish_card(proj, label, name, report, done, commit):
    """card.ts cardText('coder', …) as coderFinish draws it: the title, the report's first line, the commit's files."""
    what = "%s · %s%s" % (label, name, ", committed " + commit[:7] if commit else "")
    title = "**%s %sExternal coder · %s**" % (CODER_SQUARE, "✓ " if done else "", what)
    first = report.split("\n", 1)[0]
    m = STATUS_RE.match(first)
    files = []
    if commit:
        for ln in _git(proj, ["show", "--numstat", "--format=", commit]).splitlines():
            parts = ln.split("\t", 2)
            if len(parts) == 3:
                files.append("- `%s` %s" % (parts[2], "(binary)" if parts[0] == "-"
                                            else "+%s −%s" % (parts[0], parts[1])))
    lst = files[:CARD_FILES] + (["- … +%d files" % (len(files) - CARD_FILES)] if len(files) > CARD_FILES else [])
    body = "\n\n".join(p for p in ((first[m.end():] if m else first).strip(), "\n".join(lst)) if p)
    return "\n\n".join(p for p in (title, body.strip()) if p)


def _fetch_key(url, key):
    """{usage, limit_remaining} from GET <url>/v1/key (5 s), or None on any failure."""
    req = urllib.request.Request(url.rstrip("/") + "/v1/key", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    d = _dict(_dict(data).get("data") or data)
    return {"usage": d.get("usage"), "limit_remaining": d.get("limit_remaining")} if "usage" in d else None


DENY_RE = re.compile(r"permission[^.\n]{0,40}denied|haven't granted|requires approval|refused by pa3|"
                     r"blocks reads outside", re.I)


def _deny_kind(tool, cmd):
    """A denied call's kind: Bash by the command a rule would have to cover (after any `cd <dir> &&` or `VAR=x`),
    an interpreter with its script's folder (`python3 .run/…`); any other tool by its name."""
    if tool != "Bash":
        return tool or "?"
    saw_cd = False
    for seg in re.split(r"&&|\|\||;|\n", cmd):
        words = [w for w in seg.strip().split() if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w)]
        if words and words[0] == "cd":
            saw_cd = True
        if not words or words[0] == "cd":
            continue
        head = words[0].rsplit("/", 1)[-1]
        if head in ("bash", "sh", "python", "python3") and len(words) > 1 and "/" in words[1]:
            return "%s %s/…" % (head, words[1].split("/", 1)[0])
        return head
    return "cd" if saw_cd else tool


class Health:
    """One external coder's friction, for the expert's HEALTH lines (3.30): denied calls by kind, sandbox and
    read-guard failures, API retries and nudges. Fed the stream's events while it runs, or a transcript's records."""

    def __init__(self):
        self.uses, self.denied, self.sandbox, self.guard, self.retries, self.nudges = {}, [], 0, 0, 0, 0
        self.judge = {}     # 3.30.1: ext_rules.tally of the run's decisions.jsonl

    def record(self, rec):
        t, msg = rec.get("type"), _dict(rec.get("message"))
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        if t == "assistant":
            for b in blocks:
                b = _dict(b)
                if b.get("type") == "tool_use":
                    self.uses[b.get("id")] = (b.get("name") if isinstance(b.get("name"), str) else "", _dict(b.get("input")))
        elif t == "user":
            for b in blocks:
                b = _dict(b)
                if b.get("type") != "tool_result" or not b.get("is_error"):
                    continue
                text = _result_text(b)
                tool, inp = self.uses.get(b.get("tool_use_id"), ("", {}))
                if DENY_RE.search(text):
                    self.denied.append((tool, str(inp.get("command") or inp.get("file_path") or "")))
                elif "bwrap:" in text:
                    self.sandbox += 1
                elif "PreToolUse" in text:
                    self.guard += 1
        elif t == "system" and rec.get("subtype") == "api_retry":
            self.retries += 1

    def kinds(self, denials=None):
        """[(kind, count, an example)] of the denied calls, most first. `denials`: the result's permission_denials,
        Claude Code's own list, over the stream's (a transcript has no result)."""
        denied = [(_dict(d).get("tool_name") or "", str(_dict(_dict(d).get("tool_input")).get("command")
                   or _dict(_dict(d).get("tool_input")).get("file_path") or "")) for d in denials] if denials else self.denied
        groups = {}
        for tool, cmd in denied:
            g = groups.setdefault(_deny_kind(tool, cmd), [0, cmd])
            g[0] += 1
        return [(k, n, ex) for k, (n, ex) in sorted(groups.items(), key=lambda kv: -kv[1][0])]

    def summary(self, denials=None):
        """What the run record keeps for the phase-end review (3.30): denied calls by kind, and the other counts;
        3.30.1: the judge's tally (`judge`), when the hook decided anything."""
        out = {"denied": dict((k, n) for k, n, _ in self.kinds(denials)), "sandbox": self.sandbox,
               "guard": self.guard, "retries": self.retries, "nudges": self.nudges}
        if self.judge:
            out["judge"] = dict((k, v) for k, v in self.judge.items() if k != "examples")
        return out

    def lines(self, denials=None):
        """The HEALTH block, [] for a run without friction."""
        kinds = self.kinds(denials)
        out = []
        if kinds:
            out.append("- denied %d (no allow rule covers them): %s%s" % (sum(n for _, n, _ in kinds), "; ".join(
                "%s ×%d%s" % (k, n, ", e.g. `%s`" % re.sub(r"\s+", " ", ex).strip()[:90] if ex.strip() else "")
                for k, n, ex in kinds[:8]),
                "; +%d more kinds" % (len(kinds) - 8) if len(kinds) > 8 else ""))
        j = self.judge
        if j and (j.get("allowed") or j.get("denied") or j.get("network") or j.get("outside")):
            parts = []
            if j.get("allowed") or j.get("denied"):
                parts.append("PA3's judge allowed %d, refused %d" % (j.get("allowed", 0), j.get("denied", 0)))
            if j.get("learned"):
                parts.append("learned for this project: %s" % ", ".join("`%s`" % r for r in j["learned"]))
            if j.get("network") or j.get("outside"):
                parts.append("refused by the fixed rules: network %d, outside the project %d" % (
                    j.get("network", 0), j.get("outside", 0)))
            out.append("- " + "; ".join(parts) + (" (e.g. %s)" % "; ".join(j["examples"][:2]) if j.get("examples")
                                                 else ""))
        if self.sandbox:
            out.append("- sandbox (bwrap) failures %d, each re-run" % self.sandbox)
        if self.guard:
            out.append("- read-guard blocks %d (whole reads of big files)" % self.guard)
        if self.retries or self.nudges:
            out.append("- API retries %d; nudges after a turn with no report %d" % (self.retries, self.nudges))
        return ["HEALTH (for the expert: friction a later external coder would hit too):"] + out if out else []


class Show:
    """One external coder as the mod shows an in-house one: feed records, the facts file, journal entries, the finish
    card and ext.json. Only the journal is written without <proj>/.run/mod/<psid>/ (no mod session)."""

    def __init__(self, proj, psid, label, name, model_id, effort, config, folder):
        self.proj, self.psid, self.label, self.name = proj, psid, label, name
        self.model_id, self.effort, self.config, self.folder = model_id, effort, config, folder
        self.mod = os.path.join(proj, ".run", "mod", psid) if psid else ""
        self.on = bool(self.mod) and os.path.isdir(self.mod)
        self.agent = "ext-" + label
        self.tools, self.steps, self.note, self.line = {}, [], "", ""
        self.ctx, self.edited, self.texts_in, self.facts_last, self.key_before = 0, False, 0, None, None
        self.usage = {}   # message id -> (model, usage): the run so far, for ext.json's live tally
        self.health = Health()
        self.thought = set()   # (message id, start) of the thinking blocks journaled
        meta = _dict(fsutil.read_json(os.path.join(folder, "pa3-ext.json")))
        self.url = meta.get("url") if isinstance(meta.get("url"), str) else ""

    def _rel(self, path):
        try:
            rel = os.path.relpath(os.path.abspath(os.path.join(self.proj, path)), self.proj)
        except ValueError:
            return _fwd(path)
        return _fwd(path) if rel.startswith("..") or os.path.isabs(rel) else _fwd(rel)

    def feed(self, text, kind, edit=None):
        """One pa-watch feedRecord in <mod>/feed/in/<13-digit ms>-<6-digit seq>.json."""
        self.line = _last_line(text)
        if not self.on:
            return
        rec = {"agent": self.agent, "type": "ext-coder", "desc": self.label, "planning": False, "text": text,
               "at": 0, "kind": kind}
        if edit is not None:
            rec["edit"] = edit
        _write_new(os.path.join(self.mod, "feed", "in"), lambda ms, seq: "%013d-%06d.json" % (ms, seq),
                   lambda n, ms: json.dumps(dict(rec, at=ms), ensure_ascii=False) + "\n")

    def facts(self):
        """<mod>/agents/ext-<label>.json (pa-watch stepFactsText with MoreFacts, no parentId), when a field changed."""
        if not self.on:
            return
        note = "\n".join([ln.strip() for ln in self.note.splitlines() if ln.strip()][:2])
        f = {"model": self.name, "effort": self.effort, "ctx": self.ctx, "type": "ext-coder",
             "description": self.label, "note": note, "steps": list(self.steps), "line": self.line}
        if f == self.facts_last:
            return
        self.facts_last, ms = f, _now_ms()
        obj = {"model": f["model"], "effort": f["effort"], "ctx": f["ctx"], "at": ms, "type": f["type"],
               "description": f["description"], "note": note, "steps": f["steps"], "updatedAt": ms, "line": f["line"]}
        fsutil.atomic_write_text(os.path.join(self.mod, "agents", self.agent + ".json"),
                                 json.dumps(obj, ensure_ascii=False) + "\n")

    def journal(self, kind, fields, body):
        """pa-watch journal.ts entryPath (writer x, who coder, local time) and entryText."""
        t = time.localtime()
        path = os.path.join(self.proj, ".claude-state", "journal", time.strftime("%Y-%m-%d", t),
                            "%s-%03dx-coder-%s.md" % (time.strftime("%H%M%S", t), _next_journal(), kind))
        lines = ["%s: %s" % (k, re.sub(r"[\r\n]+", " ", str(v))) for k, v in fields.items()]
        fsutil.atomic_write_text(path, "---\n%s\n---\n%s\n" % ("\n".join(lines), body))

    def start(self):
        self.journal("spawn", {"agent": self.agent, "type": "ext-coder", "model": self.model_id, "name": self.name,
                               "effort": self.effort, "label": self.label, "parent": self.psid},
                     "External coder %s started on %s." % (self.label, self.name))
        self.feed("Starting: %s on %s" % (self.label, self.name), "text")
        self.facts()
        if self.on and self.url and "usage_first" not in _dict(_dict(_dict(
                fsutil.read_json(os.path.join(self.mod, "ext.json"))).get("key")).get(self.config)):
            self.key_before = _fetch_key(self.url, _read_key(self.folder))
        self.live()

    def live(self):
        """ext.json `live[<label>]`: this run's tokens and usd so far (pa.prices rates, one usage per message id), at
        the start and each heartbeat, so the External line shows while coders run (3.30); the key's first reading
        too, for the line's `left`. cost() moves the run into the totals."""
        if not self.on:
            return
        from pa import prices
        tok = usd = 0
        for mid, u in list(self.usage.values()):
            tok += sum(v for v in (u.get(k) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens", "output_tokens"))
                       if isinstance(v, int))
            usd += prices.cost_of(u, mid, ttl_default="5m", ctx=0)[0]

        def fn(data):
            data = _dict(data)
            live = _dict(data.get("live"))
            live[self.label] = {"tokens": tok, "usd": usd, "at": int(time.time())}
            data["live"] = live
            keys = _dict(data.get("key"))
            if self.key_before is not None and self.config not in keys:
                keys[self.config] = {"usage_first": self.key_before["usage"], "usage": self.key_before["usage"],
                                     "limit_remaining": self.key_before["limit_remaining"], "at": int(time.time())}
                data["key"] = keys
            return data
        fsutil.locked_update(os.path.join(self.mod, "ext.json"), fn)

    def _step(self, tool, inp):
        for k in ("file_path", "command", "pattern", "path", "query", "url", "description"):
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                v = self._rel(v) if k in ("file_path", "path") else re.sub(r"\s+", " ", v).strip()
                return "%s %s" % (tool, v[:60])
        return tool

    def event(self, ev):
        """One stdout event: texts and edits to the feed, the approach note to the journal, the facts refreshed."""
        self.health.record(ev)
        t = ev.get("type")
        if t == "assistant":
            msg = _dict(ev.get("message"))
            u = _dict(msg.get("usage"))
            if u:
                self.ctx = sum(v for v in (u.get(k) for k in ("input_tokens", "cache_read_input_tokens",
                                                              "cache_creation_input_tokens")) if isinstance(v, int))
                if isinstance(msg.get("id"), str):   # each content block repeats its message: the last write counts
                    model = msg.get("model")
                    self.usage[msg["id"]] = (model if isinstance(model, str) and model else self.model_id, u)
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                b = _dict(b)
                if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                    text = b["text"].strip()
                    if not self.note and not self.edited:
                        self.note = text
                        self.journal("note", {"agent": self.agent, "label": self.label}, text)
                    self.feed(text, "text")
                elif b.get("type") == "thinking" and isinstance(b.get("thinking"), str) and b["thinking"].strip():
                    # 3.30: the reasoning, journaled as pa-watch journals an in-house coder's (its fields), once a block
                    key = (msg.get("id"), b["thinking"].strip()[:200])
                    if key not in self.thought:
                        self.thought.add(key)
                        self.journal("thinking", {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                  "session": self.psid, "agentId": self.agent, "model": self.model_id,
                                                  "effort": self.effort, "turnId": msg.get("id") or ""},
                                     b["thinking"].strip())
                elif b.get("type") == "tool_use":
                    tool, inp = b.get("name") if isinstance(b.get("name"), str) else "", _dict(b.get("input"))
                    self.tools[b.get("id")] = (tool, inp)
                    self.steps = (self.steps + [self._step(tool, inp)])[-3:]
            self.facts()
        elif t == "user":
            content = _dict(ev.get("message")).get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for b in content if isinstance(content, list) else []:
                b = _dict(b)
                if b.get("type") == "tool_result":
                    tool, inp = self.tools.pop(b.get("tool_use_id"), ("", {}))
                    fp = inp.get("file_path")
                    if tool in EDIT_TOOLS and not b.get("is_error") and isinstance(fp, str) and fp:
                        created = "created" in _result_text(b).lower()
                        self.edited = True
                        view = edit_view(tool, inp, self._rel(fp), created)
                        self.feed("%s %s" % ("Created" if created else "Updated", view["path"]), "edit", view)
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    self.texts_in += 1
                    if self.texts_in > 1:       # the first is the brief (or the resume message)
                        self.feed("Correction received: " + b["text"].strip()[:120], "text")
            self.facts()
        elif t == "system" and ev.get("subtype") == "api_retry":
            err = ev.get("error") or ev.get("message") or ""
            self.line = "retrying: %s %s, attempt %s" % (
                ev.get("error_status") or "", err if isinstance(err, str) else json.dumps(err, ensure_ascii=False),
                ev.get("attempt") or 1)
            self.facts()

    def finish(self, report, res, leaks):
        """The report's journal entry; with a mod session, the finish card and ext.json."""
        done, commit = report_status(report)
        # 3.30: `end`, as the router journals an in-house coder's report (its ts, session, agentId and role fields)
        self.journal("end", {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": self.psid,
                             "agentId": self.agent, "role": "pa-coder", "agent": self.agent, "label": self.label,
                             "status": "done" if done else "blocked", "commit": commit}, report)
        if not self.on:
            return
        request(self.mod, "card", lambda _id: {"text": finish_card(self.proj, self.label, self.name, report, done,
                                                                    commit)})
        self.cost(res, leaks)

    def cost(self, res, leaks):
        """ext.json: runs, tokens and usd (pa.prices rates, never costUSD) by model; the key's usage; leaked ids."""
        from pa import prices
        runs = {}
        for mid, u in _dict(res.get("modelUsage")).items():
            u = _dict(u)
            tok = sum(v for v in (u.get(k) for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens",
                                                     "outputTokens")) if isinstance(v, (int, float)))
            runs[mid] = (int(tok), prices.cost_of(u, mid, ttl_default="5m", ctx=0)[0])
        key = _fetch_key(self.url, _read_key(self.folder)) if self.url else None

        def fn(data):
            data = _dict(data)
            live = _dict(data.get("live"))
            live.pop(self.label, None)
            data["live"] = live
            data["runs"] = int(data.get("runs") or 0) + 1
            data["tokens"] = int(data.get("tokens") or 0) + sum(t for t, _ in runs.values())
            data["usd"] = float(data.get("usd") or 0) + sum(u for _, u in runs.values())
            models = _dict(data.get("models"))
            for mid, (tok, usd) in runs.items():
                m = _dict(models.get(mid))
                models[mid] = {"tokens": int(m.get("tokens") or 0) + tok, "usd": float(m.get("usd") or 0) + usd}
            data["models"] = models
            keys = _dict(data.get("key"))
            if key is not None:
                k = _dict(keys.get(self.config))
                first = k["usage_first"] if "usage_first" in k else (self.key_before or key)["usage"]
                keys[self.config] = {"usage_first": first, "usage": key["usage"],
                                     "limit_remaining": key["limit_remaining"], "at": int(time.time())}
            data["key"] = keys
            data["leaked"] = list(dict.fromkeys(list(data.get("leaked") or []) + list(leaks)))
            return data
        fsutil.locked_update(os.path.join(self.mod, "ext.json"), fn)


def _next_journal():
    with _LOCK:
        _SEQ["journal"] += 1
        return _SEQ["journal"]


def resume_context(proj, label):
    """The fresh brief's tail: the label's journal note and report bodies (last 20 lines), its commits (at most 5)."""
    bodies = []
    for p in sorted(glob.glob(os.path.join(proj, ".claude-state", "journal", "*", "*x-coder-*.md"))):
        if p.endswith(("-note.md", "-end.md", "-report.md")):   # 3.30 writes `end`; `report` before
            front, body = _strip_frontmatter(fsutil.read_text(p, ""))
            if "label: " + label in [ln.strip() for ln in front]:
                bodies.append(body.strip("\n"))
    lines = "\n".join(bodies).split("\n")[-20:] if bodies else []
    log = _git(proj, ["log", "--format=%h %s", "--fixed-strings", "--grep", label, "-5"]).strip("\n")
    return "\n\nA previous run of this task was cut off. What it did:\n%s\n" % "\n".join(
        lines + (log.split("\n") if log else []))


def _quiet(fn, *args):
    """A display write never stops the run."""
    try:
        fn(*args)
    except (OSError, ValueError, TypeError, KeyError):
        pass


# --------------------------------------------------------------------------- run: the stream

def _user_msg(text):
    return (json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}},
                       ensure_ascii=False) + "\n").encode("utf-8")


def _send(proc, text):
    try:
        proc.stdin.write(_user_msg(text))
        proc.stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def _on_event(state, ev):
    """One stdout event; the Show (c2) draws it."""
    if state.get("show") is not None:
        _quiet(state["show"].event, ev)
    t, sub = ev.get("type"), ev.get("subtype")
    if t == "system" and sub == "init":
        state["init_model"] = ev.get("model") if isinstance(ev.get("model"), str) else ""
    elif t == "system" and sub == "api_retry":
        err = ev.get("error") or ev.get("message") or ev
        state["retry_error"] = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
    elif t == "result":
        state["result"] = ev
        state["denials"] = len(ev.get("permission_denials") or [])
        state["results"] += 1
        state["wake"].set()


def _read_stdout(proc, state):
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            _on_event(state, ev)
    state["wake"].set()


def _read_stderr(proc, state):
    for raw in proc.stderr:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            state["stderr"] = (state["stderr"] + [line])[-5:]


def _queued(run_dir):
    inbox = os.path.join(run_dir, "in")
    try:
        return sorted(f for f in os.listdir(inbox) if f.endswith(".json"))
    except OSError:
        return []


def _relay(proc, run_dir):
    """Each in/*.json {text}, oldest first, to stdin as a user message; then moved to in/done/."""
    inbox = os.path.join(run_dir, "in")
    for f in _queued(run_dir):
        msg = fsutil.read_json(os.path.join(inbox, f))
        text = msg.get("text") if isinstance(msg, dict) else None
        if isinstance(text, str) and text:
            _send(proc, text)
        os.makedirs(os.path.join(inbox, "done"), exist_ok=True)
        os.replace(os.path.join(inbox, f), os.path.join(inbox, "done", f))


def _is_report(res):
    """The result ends the run: an error, no text, or text whose first word is a report status."""
    text = (res or {}).get("result")
    return bool((res or {}).get("is_error")) or not isinstance(text, str) or bool(REPORT_RE.match(text))


def handoff_ask(pa_json, label):
    """3.30: what an in-house coder is told at its context threshold, for an external one: pa.handoff's threshold (the
    config's, the project's handoff_ctx, at most 0.8 of EXT_WINDOW) and its text, plus where this coder writes its
    progress, since several may run at once. The launcher sends it once, the first time the context passes it."""
    from pa import config as pa_config, handoff
    cfg = pa_config.load()
    text = handoff.text(cfg, role="coder", project_cfg=pa_json) + (
        "\nSeveral coders may run at once: write your progress to phase-ends/current/logs/%s.progress.md, and a patch "
        "of your own files to phase-ends/current/logs/%s.progress.patch, instead of TASK_PROGRESS.md and "
        "TASK_PROGRESS.patch; name them in your report." % (label, label))
    return {"limit": handoff.threshold(cfg, role="coder", project_cfg=pa_json, window=int(EXT_WINDOW)), "text": text,
            "sent": False}


def drive(proc, run_dir, brief, state, beat):
    """Brief in; relay say files every POLL_S; the handoff ask once the context passes its threshold (3.30); after a
    result with nothing queued, a nudge when it is no report (at most NUDGES), else close stdin once; wait for exit."""
    _send(proc, brief)
    closed, seen, nudged, last_beat = False, 0, 0, time.time()
    while True:
        if not closed:
            _relay(proc, run_dir)
            h, sh = state.get("handoff"), state.get("show")
            if h and not h["sent"] and sh is not None and sh.ctx >= h["limit"]:
                h["sent"] = True
                _send(proc, h["text"])
                _quiet(sh.feed, "Context at %dk tokens: asked to hand off" % (sh.ctx // 1000), "text")
            if state["results"] > seen:
                seen = state["results"]
                if not _queued(run_dir):
                    if nudged < NUDGES and not _is_report(state["result"]):
                        nudged += 1
                        state["nudges"] = nudged
                        _send(proc, NUDGE_TEXT)
                    else:
                        try:
                            proc.stdin.close()
                        except OSError:
                            pass
                        closed = True
        if proc.poll() is not None:
            break
        if time.time() - last_beat >= HEARTBEAT_S:
            last_beat = time.time()
            beat()
        state["wake"].wait(POLL_S)
        state["wake"].clear()
    return proc.wait()


def _new_state():
    return {"init_model": "", "result": None, "results": 0, "denials": 0, "retry_error": "", "stderr": [],
            "wake": threading.Event(), "show": None, "leaks": []}


def finish(state, rc, label, name, model_id, health=None):
    """(text to print, has a report): the report, its HEALTH block (3.30, for the expert; `state["report"]` keeps the
    report alone for the journal and the card), the one-line summary."""
    res = state["result"] or {}
    text = res.get("result")
    ok = isinstance(text, str) and bool(text.strip()) and not res.get("is_error")
    if ok:
        lines = text.rstrip("\n").split("\n")
    else:   # an error result's text ("Prompt is too long", "API Error: …") is the cause, never a report
        lines = ["blocked: the external coder ended without a report (exit %s)" % rc]
        lines += [text.strip()] if res.get("is_error") and isinstance(text, str) and text.strip() else []
        lines += [state["retry_error"]] if state["retry_error"] else state["stderr"][-5:]
    leaks = [k for k in _dict(res.get("modelUsage")) if k != model_id]
    if state["init_model"].startswith("claude") and state["init_model"] not in leaks:
        leaks.append(state["init_model"])
    state["leaks"] = leaks
    if leaks:
        lines.insert(1, "LEAK: requests ran on %s" % ", ".join(leaks))
    u = _dict(res.get("usage"))
    tokens = sum(v for v in (u.get(k) for k in ("input_tokens", "cache_read_input_tokens", "output_tokens"))
                 if isinstance(v, int))
    state["report"] = "\n".join(lines)
    if health is not None:
        health.nudges = state.get("nudges", 0)
        lines += health.lines(res.get("permission_denials"))
    lines.append("external coder %s: %s · %s turns · %d tokens · denials %d"
                 % (label, name, res.get("num_turns") or 0, tokens, state["denials"]))
    return "\n".join(lines), ok


def run(target, brief_path, out=print, cwd=None, resume=None):
    """`run <config>/<alias> <brief file>`: 0 with a report, 3 without (or unresolved), 4 lane full/duplicate.
    `resume` (a label): its record's target and brief; `--resume <child sid>` when the child's transcript is in the
    config folder, else a fresh session whose brief carries what the cut-off run did."""
    label = None
    proj = None
    try:
        old, resume_sid = None, ""
        if resume is not None:
            proj = find_root(cwd)
            old = fsutil.read_json(os.path.join(_ext_dir(proj), resume + ".json")) if SAFE_LABEL_RE.match(resume) else None
            if not isinstance(old, dict) or not isinstance(old.get("brief"), str) or not old.get("config"):
                raise Blocked("no earlier run of %s to resume" % resume)
            target, brief_path = "%s/%s" % (old.get("config"), old.get("alias")), old["brief"]
        config, alias, folder, model_id, name = resolve_model(target)
        if sandbox_missing():
            raise Blocked(SANDBOX_LINE)
        proj = find_root(cwd)
        brief = fsutil.read_text(brief_path, None)
        if brief is None:
            raise Blocked("no brief file %s" % _fwd(brief_path))
        label = brief_label(brief_path, brief) if old is None else resume
        first = brief
        if old is not None:
            csid = old.get("child_sid") if isinstance(old.get("child_sid"), str) else ""
            if csid and glob.glob(os.path.join(folder, "projects", "*", glob.escape(csid) + ".jsonl")):
                resume_sid, first = csid, RESUME_TEXT
            else:
                first = brief.rstrip("\n") + resume_context(proj, label)
        pa_json = _dict(fsutil.read_json(os.path.join(proj, ".claude", "pa.json")))
        psid, sid, now = parent_sid(proj), resume_sid or str(uuid.uuid4()), int(time.time())
        rec = {"label": label, "config": config, "alias": alias, "model": model_id, "name": name,
               "pid": os.getpid(), "child_sid": sid, "parent_sid": psid, "started": now, "heartbeat": now,
               "status": "running", "brief": _fwd(os.path.abspath(brief_path))}
        claim(proj, rec, lane_limit(pa_json, config))
    except Blocked as e:
        out("blocked: %s" % e)
        return e.code
    run_dir = os.path.join(_ext_dir(proj), label)
    status = "blocked"
    try:
        agent, _, allow, deny = write_run_dir(run_dir, proj, pa_json, model_id, folder, alias, name)
        effort = _dict(pa_json.get("external")).get("effort") or EXT_EFFORT   # `set --effort`, else high
        dirs = ext_dirs(pa_json)
        argv = claude_argv(run_dir, agent, model_id, effort, sid, allow, deny, bool(resume_sid), dirs)
        if old is None:     # 3.30.1: a fresh run's own decisions (a resumed one keeps its earlier ones)
            try:
                os.remove(os.path.join(run_dir, ext_rules.DECISIONS))
            except OSError:
                pass
        hook_env = {"PA_EXT_PROJECT": proj, "PA_EXT_RUN_DIR": run_dir, "PA_EXT_DIRS": os.pathsep.join(dirs),
                    "PA_EXT_PY": _py(pa_json)}
        if _CLAUDE is None:
            hook_env["PA_EXT_CLAUDE"] = argv[0]
        kwargs = {"cwd": proj, "env": child_env(folder, "%s/%s" % (config, alias), model_id, psid, label, hook_env),
                  "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW; never DETACHED_PROCESS
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except OSError as e:
            raise Blocked("could not start %s: %s" % (_fwd(argv[0]), e))
        state = _new_state()
        show = Show(proj, psid, label, name, model_id, effort, config, folder)
        _quiet(show.start)
        state["show"] = show
        try:
            state["handoff"] = handoff_ask(pa_json, label)
        except (ImportError, OSError, ValueError, TypeError) as e:   # no handoff ask beats no run
            state["stderr"].append("handoff threshold not set: %s" % e)
        readers = [threading.Thread(target=f, args=(proc, state), daemon=True) for f in (_read_stdout, _read_stderr)]
        for t in readers:
            t.start()
        def beat():
            _set(proj, label, heartbeat=int(time.time()))
            _quiet(show.live)
        rc = drive(proc, run_dir, first, state, beat)
        for t in readers:
            t.join(10)
        for fh in (proc.stdin, proc.stdout, proc.stderr):
            try:
                fh.close()
            except OSError:
                pass
        decs = ext_rules.decisions(run_dir)     # 3.30.1: what the judge's hook decided, for HEALTH and the record
        show.health.judge = ext_rules.tally(decs) if decs else {}
        text, ok = finish(state, rc, label, name, model_id, show.health)
        state["show"] = None
        _quiet(show.finish, state["report"], state["result"] or {}, state["leaks"])
        res = state["result"] or {}   # 3.30: the record keeps what the phase-end review reads
        word = isinstance(res.get("result"), str) and bool(REPORT_RE.match(res["result"]))
        _set(proj, label, ended=int(time.time()), turns=res.get("num_turns") or 0, report_word=word,
             health=show.health.summary(res.get("permission_denials")))
        out(text)
        status = "done" if ok else "blocked"
        return 0 if ok else 3
    except Blocked as e:
        out("blocked: %s" % e)
        return e.code
    finally:
        _set(proj, label, status=status, heartbeat=int(time.time()))


def say(label, text, out=print, cwd=None):
    """Queue `text` for the running coder `label`: in/<13-digit ms>-<6-digit seq>.json (temp name, then rename)."""
    try:
        proj = find_root(cwd)
    except Blocked as e:
        out("blocked: %s" % e)
        return 3
    rec = fsutil.read_json(os.path.join(_ext_dir(proj), label + ".json")) if SAFE_LABEL_RE.match(label) else None
    if not _running(rec) or rec.get("label") != label:
        out("no running external coder %s" % label)
        return 1
    inbox = os.path.join(_ext_dir(proj), label, "in")
    os.makedirs(inbox, exist_ok=True)
    ms, seq = int(time.time() * 1000), 0
    while True:
        fname = "%013d-%06d.json" % (ms, seq)
        if not (os.path.exists(os.path.join(inbox, fname)) or os.path.exists(os.path.join(inbox, "done", fname))):
            break
        seq += 1
    fsutil.atomic_write_text(os.path.join(inbox, fname),
                             json.dumps({"text": text, "at": ms}, ensure_ascii=False) + "\n")
    out("queued for %s" % label)
    return 0


def ask(question, out=print, cwd=None):
    """Inside an external coder (env PA_EXT_PARENT, PA_EXT_LABEL): a `retrieve` inbox request for the mod's
    retriever, then its answer file polled every POLL_S up to PA_EXT_ASK_TIMEOUT s (300). Exit 0 either way."""
    psid, label = os.environ.get("PA_EXT_PARENT") or "", os.environ.get("PA_EXT_LABEL") or ""
    if not psid or not label:
        out("ask works only inside an external coder")
        return 1
    try:
        proj = find_root(cwd)
    except Blocked as e:
        out("blocked: %s" % e)
        return 1
    try:
        wait = int(os.environ.get("PA_EXT_ASK_TIMEOUT") or 300)
    except ValueError:
        wait = 300
    mod = os.path.join(proj, ".run", "mod", psid)
    answers = os.path.join(mod, "ext", "answers")
    rid = request(mod, "retrieve", lambda i: {"question": question, "label": label,
                                              "answer": _fwd(os.path.join(os.path.abspath(answers), i + ".md"))})
    path, end = os.path.join(answers, rid + ".md"), time.time() + wait
    while True:
        # the mod writes the answer, then `<answer>.done`: read only once the marker says it is whole
        text = fsutil.read_text(path, None) if os.path.isfile(path + ".done") else None
        if text is not None:
            out(text.rstrip("\n"))
            return 0
        if time.time() >= end:
            break
        time.sleep(POLL_S)
    out("no answer within %d s; go on without it" % wait)
    return 0


# --------------------------------------------------------------------------- set: the project's external block (3.3 T6)

COUNT_MAX = 16
_WS = " \t\r\n"


def _string_end(text, i):
    """The index after the JSON string starting at text[i] (a `"`), escapes respected."""
    i += 1
    while text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _value_span(text, i):
    """(start, end) of the JSON value at or after text[i] (whitespace skipped): brackets matched outside strings."""
    while text[i] in _WS:
        i += 1
    start = i
    if text[i] == '"':
        return start, _string_end(text, i)
    if text[i] not in "{[":
        return start, re.compile(r"[^,}\]\s]*").match(text, i).end()
    depth = 0
    while True:
        c = text[i]
        if c == '"':
            i = _string_end(text, i)
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1


def _top_value_span(text, key):
    """(start, end) of the root object's `key` value in a valid JSON text, else None."""
    depth, i, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = _string_end(text, i)
            k = j
            while k < n and text[k] in _WS:
                k += 1
            if depth == 1 and k < n and text[k] == ":" and json.loads(text[i:j]) == key:
                return _value_span(text, k + 1)
            i = j
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return None


def with_external(text, ext):
    """`text` (pa.json) with only the "external" value's text changed to json.dumps(ext); None when the result does
    not parse or any other key changed value."""
    old = json.loads(text)
    blob = json.dumps(ext)
    span = _top_value_span(text, "external")
    if span is not None:
        new = text[:span[0]] + blob + text[span[1]:]
    else:
        j = len(text[:text.rstrip().rfind("}")].rstrip())
        new = text[:j] + ',\n  "external": ' + blob + text[j:]
    try:
        doc = json.loads(new)
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("external") != ext:
        return None
    if {k: v for k, v in doc.items() if k != "external"} != {k: v for k, v in old.items() if k != "external"}:
        return None
    return new


def _check_target(t):
    config, _, alias = t.partition("/")
    meta = fsutil.read_json(os.path.join(root(), config, "pa3-ext.json")) if NAME_RE.match(config) else None
    models = meta.get("models") if isinstance(meta, dict) and isinstance(meta.get("models"), dict) else {}
    if not alias or not isinstance(models.get(alias), dict):
        raise Blocked("no external coder %s on this machine: run setup-external first" % t, 1)


def _count(v, what):
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = -1
    if not (0 <= n <= COUNT_MAX) or str(n) != str(v).strip():
        raise Blocked("bad count %s for %s: an integer from 0 to %d" % (v, what, COUNT_MAX), 1)
    return n


ALLOW_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\(.+\))?\Z")


def _add_allow(proj, rule):
    """3.30: an approved allow rule into the project's .claude/settings.json (never a bare Bash)."""
    if not ALLOW_RE.match(rule) or rule.replace(" ", "") in ("Bash", "Bash(*)", "Bash(:*)"):
        raise Blocked("bad allow rule %s: a tool with a pattern, e.g. Bash(mipsel-linux-gnu-objdump:*)" % rule, 1)
    path = os.path.join(proj, ".claude", "settings.json")
    doc = fsutil.read_json(path) if os.path.exists(path) else {}
    if not isinstance(doc, dict):
        raise Blocked(".claude/settings.json does not parse", 1)
    perms = doc.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
        fsutil.atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _add_model_note(target, note):
    """3.30: a note for every coder on `<config>/<alias>`, in every project (one `- ` line each); (path, its number
    among the notes, added)."""
    _check_target(target)
    config, _, alias = target.partition("/")
    path = model_notes_path(os.path.join(root(), config), alias)
    old = fsutil.read_text(path, None) or ""
    line = "- " + " ".join(note.split())
    added = line not in old.splitlines()
    if added:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_write_text(path, (old.rstrip("\n") + "\n" if old.strip() else "") + line + "\n")
    lines = [ln for ln in (fsutil.read_text(path, "") or "").splitlines() if ln.startswith("- ")]
    return path, lines.index(line) + 1, added


def _drop_model_note(target, n):
    """3.30.1: drop a model's note by its number (the `- ` lines in order)."""
    _check_target(target)
    config, _, alias = target.partition("/")
    path = model_notes_path(os.path.join(root(), config), alias)
    lines = (fsutil.read_text(path, "") or "").splitlines()
    notes = [i for i, ln in enumerate(lines) if ln.startswith("- ")]
    k = _count(n, "drop-model-note")
    if not 1 <= k <= len(notes):
        raise Blocked("no note %s for %s: it has %d" % (n, target, len(notes)), 1)
    del lines[notes[k - 1]]
    fsutil.atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n" if any(ln.strip() for ln in lines) else "")


_SECRET_DIRS = (".claude", ".ssh", ".aws", ".gnupg", ".config", ".azure", ".kube", ".docker")


def _check_dir(proj, d):
    """3.30.1: a folder outside the project its tools read: it exists, is not the home folder, a root, a folder
    holding them or a secrets folder (.claude, .ssh, …), and is not inside the project."""
    p = os.path.normpath(os.path.abspath(os.path.expanduser(d)))
    home = os.path.normpath(paths.home())
    if not os.path.isdir(p):
        raise Blocked("no folder %s on this machine" % _fwd(d), 1)
    if os.path.dirname(p) == p or ext_rules._inside(home, p) or ext_rules._inside(p, proj):
        raise Blocked("%s is too wide (a root, the home folder or one holding it) or inside the project" % _fwd(d), 1)
    for s in _SECRET_DIRS:
        if ext_rules._inside(p, os.path.join(home, s)):
            raise Blocked("%s holds credentials or settings: never a coder's folder" % _fwd(d), 1)


def _fyi(proj, text):
    """3.30.1: a one-line notice to Claude in the parent session (the mod's inbox), when that session's folder
    exists."""
    psid = parent_sid(proj)
    mod = os.path.join(proj, ".run", "mod", psid) if psid else ""
    if mod and os.path.isdir(mod):
        _quiet(request, mod, "notice", lambda i: {"text": text})


def set_external(coder=None, lanes=None, max_coders=None, escalate=None, use=None, remind=None, effort=None, out=print,
                 cwd=None, note=None, drop_note=None, model_note=None, allow=None, forget=None, drop_model_note=None,
                 add_dir=None, drop_dir=None):
    """`set`: change the given keys of pa.json's `external` block (the others stay); no change shows it. Exit 1 with
    a one-line reason and nothing written on any failed check. What a run teaches (3.30; since 3.30.1 the expert
    applies notes itself, each an FYI to Claude with its undo): `note` (and `drop_note`, 1-based) for this project's
    external coders, `model_note` (target, text) and `drop_model_note` (target, n) for every coder on a model,
    `allow` a rule in the project's settings, `forget` a rule or refusal PA3's judge learned, `add_dir`/`drop_dir` a
    folder outside the project its coders' tools read."""
    try:
        proj = find_root(cwd)
        path = os.path.join(proj, ".claude", "pa.json")
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
        try:
            doc = json.loads(text)
        except ValueError:
            raise Blocked(".claude/pa.json does not parse", 1)
        ext = dict(_dict(doc.get("external"))) if isinstance(doc, dict) else {}
        py = _py(doc if isinstance(doc, dict) else {})
        fyis = []
        if all(v is None for v in (coder, lanes, max_coders, escalate, use, remind, effort, note, drop_note,
                                   model_note, allow, forget, drop_model_note, add_dir, drop_dir)):
            if isinstance(doc, dict) and isinstance(doc.get("external"), dict):
                out(json.dumps(doc["external"], indent=2))
            else:
                out("external coders: not set up in this project")
            return 0
        said = []
        if coder is not None:
            if coder == "in-house":
                ext.pop("coder", None)
            else:
                _check_target(coder)
                ext["coder"] = coder
            said.append("coder " + coder)
        if lanes is not None:
            names = set(n for n, _, _ in load_configs())
            got = dict(_dict(ext.get("lanes")))
            for part in [x.strip() for x in lanes.split(",") if x.strip()]:
                name, eq, v = part.partition("=")
                name = name.strip()
                if not eq:
                    raise Blocked("bad lane %s: use <name>=<n>" % part, 1)
                if name != "in-house" and name not in names:
                    raise Blocked("unknown lane %s: name a config on this machine or in-house" % name, 1)
                got[name] = _count(v, "lane " + name)
            ext["lanes"] = got
            said.append("lanes " + ", ".join("%s=%d" % kv for kv in got.items()))
        if max_coders is not None:
            ext["max_coders"] = _count(max_coders, "max-coders")
            said.append("max_coders %d" % ext["max_coders"])
        if escalate is not None:
            steps = [x.strip() for x in escalate.split(",") if x.strip()]
            for t in steps:
                if t != "in-house":
                    _check_target(t)
            ext["escalate"] = steps
            said.append("escalate " + " then ".join(steps))
        for key, v in (("use", use), ("remind", remind)):
            if v is not None:
                ext[key] = v == "on"
                said.append("%s %s" % (key, v))
        if effort is not None:   # 3.3 T10: the external coders' reasoning effort over EXT_EFFORT
            if effort == "default":
                ext.pop("effort", None)
            elif effort in EFFORTS:
                ext["effort"] = effort
            else:
                raise Blocked("bad effort %s: one of %s, or default" % (effort, ", ".join(EFFORTS)), 1)
            said.append("effort " + effort)
        notes = [n for n in ext.get("notes") or [] if isinstance(n, str)]
        if drop_note is not None:
            k = _count(drop_note, "drop-note")
            if not 1 <= k <= len(notes):
                raise Blocked("no note %s: `set --show` lists %d" % (drop_note, len(notes)), 1)
            said.append("dropped note %d" % k)
            notes.pop(k - 1)
        if note is not None and note.strip():
            clean = " ".join(note.split())
            if clean not in notes:
                notes.append(clean)
                fyis.append(("project", "", clean, "‹pa› FYI, nothing to ask: a note was added for every external "
                             "coder in this project: “%s”. Undo: `%s tools/pa_ext.py set --drop-note %d`. Tell the "
                             "developer in one line." % (clean, py, notes.index(clean) + 1)))
            said.append("note added")
        if note is not None or drop_note is not None:
            ext["notes"] = notes
        if model_note is not None:
            mpath, k, added = _add_model_note(model_note[0], model_note[1])
            if added:
                fyis.append(("model", model_note[0], " ".join(model_note[1].split()), (
                    "‹pa› FYI, nothing to ask: a note was added for every coder on %s, in every project: “%s”. Undo: "
                    "`%s tools/pa_ext.py set --drop-model-note %s %d`. Tell the developer in one line." % (
                        model_note[0], " ".join(model_note[1].split()), py, model_note[0], k))))
            said.append("model note in %s" % _fwd(mpath))
        if drop_model_note is not None:
            _drop_model_note(drop_model_note[0], drop_model_note[1])
            said.append("dropped model note %s for %s" % (drop_model_note[1], drop_model_note[0]))
        if allow is not None:
            _add_allow(proj, allow)
            allowed = [a for a in ext.get("allowed") or [] if isinstance(a, dict)]
            if all(a.get("rule") != allow for a in allowed):   # the review's record: which rules, since when
                ext["allowed"] = allowed + [{"rule": allow, "at": time.strftime("%Y-%m-%d")}]
            said.append("allow %s in .claude/settings.json" % allow)
        if forget is not None:
            if not ext_rules.forget(proj, forget):
                raise Blocked("PA3's judge learned no %s here (`runs` lists what it learned)" % forget, 1)
            said.append("forgot %s" % forget)
        dirs = [d for d in ext.get("dirs") or [] if isinstance(d, str)]
        if add_dir is not None:
            _check_dir(proj, add_dir)
            if add_dir not in dirs:
                dirs.append(add_dir)
            said.append("folder %s for coders to read" % add_dir)
        if drop_dir is not None:
            if drop_dir not in dirs:
                raise Blocked("no folder %s in external.dirs" % drop_dir, 1)
            dirs.remove(drop_dir)
            said.append("dropped folder %s" % drop_dir)
        if add_dir is not None or drop_dir is not None:
            ext["dirs"] = dirs
        if any(v is not None for v in (coder, lanes, max_coders, escalate, use, remind, effort, note, drop_note,
                                       allow, add_dir, drop_dir)):
            new = with_external(text, ext)
            if new is None:
                raise Blocked("could not write the external block without changing the rest of .claude/pa.json", 1)
            tmp = path + ".tmp-%d" % os.getpid()
            with open(tmp, "wb") as fh:
                fh.write(new.encode("utf-8"))
            os.replace(tmp, path)
        for kind, target, clean, fyi in fyis:   # 3.30.1: learning mode reads when a note came; Claude hears of it
            _log_note(proj, kind, target, clean)
            _fyi(proj, fyi)
    except Blocked as e:
        out("refused: %s" % e)
        return 1
    out("external: " + ", ".join(said))
    return 0


# --------------------------------------------------------------------------- CLI

def _cmd_setup(_args):
    return setup()


def _cmd_check(_args):
    return check()


def _cmd_run(args):
    if args.resume:
        return run(None, None, resume=args.resume)
    if not (args.target and args.brief):
        print("usage: pa_ext.py run <config>/<alias> <brief file> | run --resume <label>")
        return 2
    return run(args.target, args.brief)


def _cmd_say(args):
    return say(args.label, " ".join(args.text))


def _cmd_ask(args):
    return ask(" ".join(args.question))


def _cmd_set(args):
    if args.show:
        return set_external()
    return set_external(args.coder, args.lanes, args.max_coders, args.escalate, args.use, args.remind, args.effort,
                        note=args.note, drop_note=args.drop_note, model_note=args.model_note, allow=args.allow,
                        forget=args.forget, drop_model_note=args.drop_model_note, add_dir=args.dir,
                        drop_dir=args.drop_dir)


def _transcript_health(rec):
    """A run record's Health from its child's transcript in the config folder, or None without one."""
    hits = glob.glob(os.path.join(root(), str(rec.get("config") or ""), "projects", "*",
                                  "%s.jsonl" % rec.get("child_sid"))) if rec.get("child_sid") else []
    if not hits:
        return None
    h = Health()
    with open(hits[0], encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                h.record(_dict(json.loads(ln)))
            except ValueError:
                continue
    return h


def health(label, out=print, cwd=None):
    """`health <label>` (3.30): the HEALTH block of a running or finished external coder, from its transcript."""
    try:
        proj = find_root(cwd)
        rec = fsutil.read_json(os.path.join(_ext_dir(proj), label + ".json"))
        if not isinstance(rec, dict) or not rec.get("child_sid"):
            raise Blocked("no external coder %s in this project" % label, 1)
        h = _transcript_health(rec)
        if h is None:
            raise Blocked("no transcript for %s (session %s)" % (label, rec["child_sid"]), 1)
    except Blocked as e:
        out("refused: %s" % e)
        return e.code
    decs = ext_rules.decisions(os.path.join(_ext_dir(proj), label))    # 3.30.1: the judge's decisions
    h.judge = ext_rules.tally(decs) if decs else {}
    lines = h.lines()
    out("\n".join(lines) if lines else "HEALTH: no friction in %s" % label)
    return 0


REVIEW_FILE = "review.json"   # <proj>/.run/mod/ext/review.json {"at"}: the last phase-end external review (3.30)


def runs(count=False, mark=False, out=print, cwd=None):
    """`runs` (3.30): for the phase-end external review, the external coder runs ended since the last review (one
    line each: model, outcome, minutes, turns, health) and what is in force: this project's notes, the allow rules
    health proposals added (with their dates), each run model's notes. --count prints only the number of runs;
    --mark stamps the review."""
    try:
        proj = find_root(cwd)
    except Blocked as e:
        out("refused: %s" % e)
        return e.code
    d = _ext_dir(proj)
    since = _dict(fsutil.read_json(os.path.join(d, REVIEW_FILE))).get("at") or 0
    recs = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        r = _dict(fsutil.read_json(f)) if os.path.basename(f) != REVIEW_FILE else {}
        if r.get("status") in ("done", "blocked") and int(r.get("ended") or r.get("heartbeat") or 0) > int(since):
            recs.append(r)
    if mark:
        fsutil.atomic_write_text(os.path.join(d, REVIEW_FILE), json.dumps({"at": int(time.time())}) + "\n")
        out("external runs reviewed: %d" % len(recs))
        return 0
    if count:
        out(str(len(recs)))
        return 0
    out("External coder runs since the last review: %d" % len(recs))
    for r in recs:
        h = _dict(r.get("health"))
        if not h:   # a run from before 3.30's record: from its transcript
            t = _transcript_health(r)
            h = t.summary() if t is not None else {}
        denied, j = _dict(h.get("denied")), _dict(h.get("judge"))
        mins = (int(r.get("ended") or r.get("heartbeat") or 0) - int(r.get("started") or 0)) // 60
        judged = " · judge allowed %d, refused %d, learned %d" % (j.get("allowed", 0), j.get("denied", 0), len(
            j.get("learned") or [])) if j else ""
        out("- %s · %s · %s · %d min%s · denied %d%s · sandbox %s · read guard %s · retries %s%s%s" % (
            r.get("label"), r.get("name") or r.get("model"), r.get("status"), mins,
            " · %s turns" % r["turns"] if r.get("turns") else "", sum(denied.values()),
            " (%s)" % ", ".join("%s %d" % kv for kv in list(denied.items())[:6]) if denied else "",
            h.get("sandbox", "?"), h.get("guard", "?"), h.get("retries", "?"), judged,
            " · clean" if run_clean(proj, r) else ""))
    pa_json = _dict(fsutil.read_json(os.path.join(proj, ".claude", "pa.json")))
    ext = _dict(pa_json.get("external"))
    dated = dict((n.get("text"), time.strftime("%Y-%m-%d", time.localtime(int(n.get("at") or 0))))
                 for n in notes_log(proj) if n.get("kind") == "project")
    out("Project notes (pa.json external.notes, `set --note`): %s" % ("none" if not ext.get("notes") else ""))
    for n in ext.get("notes") or []:
        out("- %s%s" % (n, " (added %s)" % dated[n] if n in dated else ""))
    out("Allow rules from health proposals (`set --allow`): %s" % ("none" if not ext.get("allowed") else ""))
    for a in ext.get("allowed") or []:
        a = _dict(a)
        out("- %s (added %s)" % (a.get("rule"), a.get("at")))
    learned = ext_rules.load_learned(proj)     # 3.30.1
    out("Learned by PA3's judge (`set --forget <rule>` drops one): %s" % ("none" if not learned["allow"] else ""))
    for e in learned["allow"]:
        out("- %s (%s, %s%s)" % (e.get("rule"), time.strftime("%Y-%m-%d", time.localtime(int(e.get("at") or 0))),
                                 e.get("label") or "?", ": `%s`" % e["example"][:80] if e.get("example") else ""))
    if learned["deny"]:
        out("Refused by the judge and refused again without asking (`set --forget <key>`): %d, e.g. %s" % (
            len(learned["deny"]), "; ".join("%s (%s)" % (e.get("key")[:80], e.get("reason") or "?")
                                            for e in learned["deny"][-3:])))
    for target in sorted(set("%s/%s" % (r.get("config"), r.get("alias")) for r in recs if r.get("config"))):
        config, _, alias = target.partition("/")
        notes = fsutil.read_text(model_notes_path(os.path.join(root(), config), alias), None)
        out("Model notes for %s: %s" % (target, "none" if not (notes or "").strip() else ""))
        for ln in (notes or "").splitlines():
            if ln.strip():
                out(ln.rstrip())
    models = sorted(set((r.get("config"), r.get("alias")) for r in recs if r.get("config")))
    if models:
        out("Learning mode (a model runs one coder at a time in this project until a run comes back clean):")
        for config, alias in models:
            on, why = learning(proj, config, alias)
            out("- %s/%s: %s" % (config, alias, ("learning: " + why) if on else "open (last run %s clean)" % why))
    out("Settings: %s" % json.dumps(dict((k, v) for k, v in ext.items() if k not in ("notes", "allowed"))))
    return 0


def _cmd_health(args):
    return health(args.label)


def _cmd_runs(args):
    return runs(count=args.count, mark=args.mark)


VERBS = {
    "setup": (_cmd_setup, "add or update an external backend; saves only when every model answers", ()),
    "check": (_cmd_check, "one test request per config and model; one line per config", ()),
    "run": (_cmd_run, "run one external coder: <config>/<alias> <brief file>, or --resume <label>",
            (("target", {"nargs": "?"}), ("brief", {"nargs": "?"}), ("--resume", {"metavar": "LABEL"}))),
    "say": (_cmd_say, "queue a correction for a running external coder: <label> <text>",
            (("label", {}), ("text", {"nargs": "+"}))),
    "ask": (_cmd_ask, "inside an external coder: a code lookup through the mod's retriever: <question>",
            (("question", {"nargs": "+"}),)),
    "set": (_cmd_set, "the project's pa.json external block; --show (or no flags) prints it",
            (("--coder", {"metavar": "CONFIG/ALIAS|in-house"}), ("--lanes", {"metavar": "NAME=N[,...]"}),
             ("--max-coders", {"metavar": "N"}), ("--escalate", {"metavar": "A[,B...]"}),
             ("--use", {"choices": ("on", "off")}), ("--remind", {"choices": ("on", "off")}),
             ("--effort", {"choices": EFFORTS + ("default",)}), ("--show", {"action": "store_true"}),
             ("--note", {"metavar": "TEXT"}), ("--drop-note", {"metavar": "N"}),
             ("--model-note", {"nargs": 2, "metavar": ("CONFIG/ALIAS", "TEXT")}),
             ("--drop-model-note", {"nargs": 2, "metavar": ("CONFIG/ALIAS", "N")}), ("--allow", {"metavar": "RULE"}),
             ("--forget", {"metavar": "RULE"}), ("--dir", {"metavar": "PATH"}), ("--drop-dir", {"metavar": "PATH"}))),
    "health": (_cmd_health, "the HEALTH block of a running or finished external coder: <label>", (("label", {}),)),
    "runs": (_cmd_runs, "for the phase-end external review: the runs since the last review and what is in force",
             (("--count", {"action": "store_true"}), ("--mark", {"action": "store_true"}))),
}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(prog="pa_ext.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for verb, (fn, text, params) in VERBS.items():
        p = sub.add_parser(verb, help=text)
        for pname, kw in params:
            p.add_argument(pname, **kw)
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
