"""pa_bugreport.py: one Markdown file a user can attach to a GitHub issue when PA3 stalls or misbehaves.

Run from the project's root folder (the `/pa-bugreport` command does):

    python tools/pa_bugreport.py [--days 30] [--out FILE] [--config-dir DIR] [--home DIR]

Read-only except its one output, `.run/pa-bugreport-<YYYYMMDD-HHMM>.md` (120,000 characters at most). It reads PA3's
versions and settings, the plugins Claude Code lists, `.run/mod/` (loop.json, the inboxes, the session's facts, the
newest stall snapshot), the journal index and this project's helper-agent transcripts (the rules of
pa3_stall_evidence.py, 3.32.1). Before writing, the text is scrubbed for a public issue: the home folder becomes `~`,
the user name `you`, email addresses `<email>`, the value after key, token, secret, password or auth `[redacted]`.
Commands are cut to 120 characters, last words to 300; no file contents, no tool output. Agent and session ids stay.
"""
import argparse
import collections
import glob
import json
import os
import platform
import re
import time
from datetime import datetime

CAP = 120000
PA3_TYPES = ("pa-expert", "pa-coder", "retriever-code", "retriever-digest", "retriever-web", "auditor", "memory-curator",
             "pa-planner", "pa-thinker")
TASK_RE = re.compile(r"^T\d+(\.\d+)*$")
NOTICE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
REQUEST_RE = re.compile(r"^(\d+)-(\d+)-([a-z][a-z0-9_]*)\.json$")
# The mod's own kinds, drained by the loop and marked in loop.json (mod/pa/hooks/inbox.ts LOOP_KINDS); the rest the
# router drains and marks in the session's state.json.
LOOP_KINDS = ("release", "plan_edit", "steer", "forward", "install", "answer", "cleanup", "drive")
HEADER = """# PA3 bug report

Written for a public GitHub issue. **Read it before you post it.** Your home folder became `~`, your user name `you`,
email addresses `<email>`, and the value after words like key, token, secret, password or auth `[redacted]`.
Commands are cut to 120 characters and agents' last words to 300; there are no file contents and no tool output.
Agent and session ids stay: they let us follow what happened.
"""


def when(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def stamp(ts):
    t = when(ts)
    return "-" if t is None else time.strftime("%m-%d %H:%M", time.localtime(t))


def ms_stamp(ms):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ms) / 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"


def age(seconds):
    if seconds is None:
        return "-"
    s = max(0, int(seconds))
    return "%ds" % s if s < 120 else "%dm" % (s // 60) if s < 7200 else "%.1fh" % (s / 3600)


def one(text, n):
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def flat(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def load(path):
    """(parsed JSON, None) or (None, why)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as e:
        return None, "unreadable (%s)" % type(e).__name__


def text_of(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


# --- the scrub -------------------------------------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SECRET_RE = re.compile(r"""(?i)([\w-]*(?:key|token|secret|password|auth)[\w-]*["']?)([ \t]*[=:][ \t]*|[ \t]+)"""
                       r"""("[^"\n]*"|'[^'\n]*'|[^\s,;)\]}]+)""")


def scrubber(home):
    """A function that makes text safe to post: home folder forms -> ~, emails, secrets, then the user name -> you."""
    home = os.path.normpath(home) if home else ""
    name = os.path.basename(home)
    subs = []
    if home:
        for form in {home, home.replace("\\", "/"), home.replace("/", "\\"), home.replace("\\", "\\\\")}:
            subs.append((re.compile(re.escape(form) + r"(?![\w.-])", re.I), "~"))
    if len(name) >= 2:
        n = re.escape(name) + r"(?![\w.-])"
        subs += [(re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+" + n, re.I), "~"),
                 (re.compile(r"(?:/mnt)?(?:/[A-Za-z])?/(?:Users|home)/" + n, re.I), "~"),
                 (re.compile(r"(?:\b[A-Za-z]-)?-(?:Users|home)-" + re.escape(name) + r"(?![A-Za-z0-9])", re.I), "~")]
    name_re = re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I) if len(name) >= 2 else None

    def scrub(text):
        for rx, to in subs:
            text = rx.sub(to, text)
        text = EMAIL_RE.sub("<email>", text)
        text = SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
        if name_re is not None:
            text = name_re.sub("you", text)
        return text
    return scrub


# --- helper transcripts (pa3_stall_evidence.py's rules) --------------------------------------------------------------

def short_arg(name, inp):
    bg = "[background] " if inp.get("run_in_background") else ""
    if name == "Bash":
        return bg + one(inp.get("command", ""), 120)
    if name in ("Agent", "Task"):
        return "%s%s: %s" % (bg, inp.get("subagent_type", "?"), one(inp.get("description", ""), 80))
    if name == "SubagentHandback":
        return one(inp.get("message", ""), 100)
    if name == "SendMessage":
        return "to %s: %s" % (inp.get("to", "?"), one(inp.get("summary") or inp.get("message", ""), 80))
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return os.path.basename(str(inp.get("file_path", "")))
    if name in ("Grep", "Glob"):
        return one(inp.get("pattern", ""), 60)
    if name in ("Monitor", "TaskStop", "KillShell", "BashOutput"):
        return one(json.dumps(inp), 100)
    return ""


def scan(path):
    """One helper's transcript: its tools, what it started in the background, what came back, how it ended."""
    a = {"first": None, "last": None, "versions": set(), "models": set(), "tools": [], "handbacks": [], "started": {},
         "notified": {}, "messages_from": {}, "last_text": "", "open_uses": []}
    uses, answered = {}, set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            typ = e.get("type")
            if typ not in ("user", "assistant"):
                continue
            ts = e.get("timestamp")
            if ts:
                a["first"] = a["first"] or ts
                a["last"] = ts
            if e.get("version"):
                a["versions"].add(e["version"])
            m = e.get("message") or {}
            if typ == "assistant" and m.get("model"):
                a["models"].add(m["model"])
            c = m.get("content")
            blocks = c if isinstance(c, list) else ([{"type": "text", "text": c}] if isinstance(c, str) else [])
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                kind = b.get("type")
                if kind == "tool_use":
                    name, inp = b.get("name", "?"), b.get("input") or {}
                    uses[b.get("id")] = (name, short_arg(name, inp), ts)
                    a["tools"].append((ts, name, short_arg(name, inp)))
                    if name == "SubagentHandback":
                        a["handbacks"].append(ts)
                elif kind == "tool_result":
                    tid = b.get("tool_use_id")
                    answered.add(tid)
                    u = uses.get(tid)
                    text = flat(b.get("content"))
                    for rx, what in ((r"running in background with ID: (\w+)", "shell"), (r"moved to the background \(ID: (\w+)\)", "shell"),
                                     (r"agentId: (\w+)", "agent")):
                        hit = re.search(rx, text)
                        if hit and u is not None and (what == "shell" or u[0] in ("Agent", "Task")):
                            a["started"][hit.group(1)] = (what, ts, u[1])
                elif kind == "text":
                    t = b.get("text") or ""
                    if typ == "assistant" and t.strip():
                        a["last_text"] = t.strip()
                    if typ == "user":
                        for n in NOTICE_RE.finditer(t):
                            body = n.group(1)
                            tid = re.search(r"<task-id>([^<]+)</task-id>", body)
                            st = re.search(r"<status>([^<]+)</status>", body)
                            sm = re.search(r"<summary>([^<]+)</summary>", body)
                            if tid:
                                a["notified"][tid.group(1).strip()] = (ts, st.group(1).strip() if st else "?",
                                                                      one(sm.group(1) if sm else "", 100))
                        for am in re.finditer(r'<agent-message from="([^"]+)">', t):
                            a["messages_from"][am.group(1)] = ts
    a["open_uses"] = [uses[k] for k in uses if k not in answered]
    return a


def open_background(a):
    """What it started in the background and never heard back from, or whose last notice says it was still waiting."""
    out = []
    for bid, (what, ts, arg) in a["started"].items():
        n = a["notified"].get(bid)
        said = a["messages_from"].get(bid)
        if said is not None and (n is None or (when(said) or 0) >= (when(n[0]) or 0) or "waiting" not in n[2].lower()):
            continue  # it reported back by message after its last notice
        if n is None:
            out.append("%s %s started %s, no completion notice: %s" % (what, bid, stamp(ts), arg))
        elif "waiting" in n[2].lower():
            out.append("%s %s last notice %s says still waiting (%s): %s" % (what, bid, stamp(n[0]), n[2], arg))
    return out


def journal_rows(root):
    """Per task start: when its expert first reported (interim) and when the task ended, from the journal index."""
    lines = []
    for p in sorted(glob.glob(os.path.join(root, ".claude-state", "journal", "INDEX-*.md"))):
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = [x.strip() for x in line.split(" | ")]
                if len(parts) >= 4 and TASK_RE.match(parts[1]) and parts[2] in ("start", "interim", "end"):
                    lines.append((parts[0], parts[1], parts[2], parts[3]))
    lines.sort()
    runs, cur = [], {}
    for ts, task, kind, first in lines:
        if kind == "start":
            agent = re.search(r"\b(a[0-9a-f]{16})\b", first)
            cur[task] = {"task": task, "start": ts, "agent": agent.group(1) if agent else "", "report": None,
                         "report_text": "", "end": None, "end_text": ""}
            runs.append(cur[task])
        elif task in cur and kind == "interim" and cur[task]["report"] is None:
            cur[task]["report"], cur[task]["report_text"] = ts, first
        elif task in cur and kind == "end" and cur[task]["end"] is None:
            cur[task]["end"], cur[task]["end_text"] = ts, first
    return runs


# --- the sections ----------------------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, root, config_dir, home, days):
        self.root, self.config_dir, self.home, self.days = root, config_dir, home, days
        self.now = time.time()
        base = os.path.join(config_dir, "projects")
        want = re.sub(r"[^A-Za-z0-9]", "-", root)
        self.proj = next((os.path.join(base, n) for n in (os.listdir(base) if os.path.isdir(base) else [])
                          if n.lower() == want.lower()), None)
        self.pa, self.pa_why = load(os.path.join(root, ".claude", "pa.json"))
        self.loop, self.loop_why = load(os.path.join(root, ".run", "mod", "loop.json"))

    def rel(self, path):
        r = os.path.relpath(path, self.root) if path.startswith(self.root) else path
        return r.replace("\\", "/")

    def missing(self, path, why="missing"):
        return "- (%s: `%s`, skipped)" % (why, self.rel(path))


def sessions(c):
    """`.run/mod/<session>/` folders, newest first (a session folder holds agents/, inbox/ or state.json)."""
    base = os.path.join(c.root, ".run", "mod")
    out = []
    for d in glob.glob(os.path.join(base, "*")):
        if os.path.isdir(d) and any(os.path.exists(os.path.join(d, x)) for x in ("agents", "inbox", "state.json", "shells.json")):
            out.append(d)
    return sorted(out, key=os.path.getmtime, reverse=True)


def sec_versions(c):
    w = ["## 1. Versions and settings\n"]
    vpath = os.path.join(c.home, ".claude", "pa3", "VERSION")
    vtext = text_of(vpath)
    if vtext is None:
        w.append(c.missing(vpath))
    else:
        first = (vtext.lstrip("﻿").strip().splitlines() or [""])[0]
        w.append("- PA3 installed: %s" % one(first.split(":", 1)[1] if first.startswith("version:") else first, 40))
    pa = c.pa if isinstance(c.pa, dict) else None
    if pa is None:
        w.append(c.missing(os.path.join(c.root, ".claude", "pa.json"), c.pa_why or "not an object"))
    else:
        w.append("- pa.json: pa_version %s, mod %s, preset %s, %s, upgrade %s" % (
            pa.get("pa_version", "-"), pa.get("mod", "-"), pa.get("preset") or pa.get("tier") or "-",
            "drive %s" % pa["drive"] if "drive" in pa else "rhythm %s" % pa.get("rhythm", "-"), pa.get("upgrade", "-")))
    cc = set()
    apps = collections.Counter()
    if c.proj is None:
        w.append("- (no Claude Code transcripts for this project under `%s`, skipped)" % c.rel(os.path.join(c.config_dir, "projects")))
    else:
        files = glob.glob(os.path.join(c.proj, "*.jsonl")) + glob.glob(os.path.join(c.proj, "*", "subagents", "*.jsonl"))
        for p in sorted(files, key=os.path.getmtime, reverse=True)[:20]:
            try:
                with open(p, "rb") as fh:  # the tail is enough: the newest lines carry the newest version
                    fh.seek(max(0, os.path.getsize(p) - 262144))
                    t = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
            cc.update(re.findall(r'"version"\s*:\s*"(\d+\.\d+\.\d+[^"]*)"', t))
            apps.update(re.findall(r'"entrypoint"\s*:\s*"([^"]+)"', t)[-1:])  # helpers run in their session's app
        w.append("- Claude Code: %s" % (max(cc, key=vkey) if cc else "(no version in the transcripts)"))
        w.append("- Claude Code app (the entrypoint of the 20 newest transcripts; `cli` is the terminal): %s" % (
            ", ".join("%s %d" % kv for kv in apps.most_common()) if apps else "(not in the transcripts)"))
    w.append("- OS: %s; Python %s" % (platform.platform(), platform.python_version()))
    for label, p in (("home settings", os.path.join(c.config_dir, "settings.json")),
                     ("project settings", os.path.join(c.root, ".claude", "settings.json")),
                     ("project local settings", os.path.join(c.root, ".claude", "settings.local.json"))):
        doc, why = load(p)
        if doc is None:
            w.append(c.missing(p, why))
        else:
            mode = (doc.get("permissions") or {}).get("defaultMode", "(not set)") if isinstance(doc, dict) else "-"
            w.append("- Permission mode, %s: %s" % (label, mode))
    agents = sorted(glob.glob(os.path.join(c.root, ".claude", "agents", "*.md")))
    if not agents:
        w.append(c.missing(os.path.join(c.root, ".claude", "agents"), "no agents"))
    for p in agents:
        t = text_of(p) or ""
        fm = re.match(r"﻿?---\s*\n(.*?)\n---", t, re.S)
        f = dict(re.findall(r"^(model|effort):\s*(.*?)\s*$", fm.group(1), re.M)) if fm else {}
        w.append("- Agent %s: model %s, effort %s" % (os.path.basename(p)[:-3], f.get("model", "-"), f.get("effort", "-")))
    return w


def sec_plugins(c):
    w = ["\n## 2. Plugins\n"]
    for label, p in (("home settings", os.path.join(c.config_dir, "settings.json")),
                     ("project settings", os.path.join(c.root, ".claude", "settings.json")),
                     ("project local settings", os.path.join(c.root, ".claude", "settings.local.json"))):
        doc, why = load(p)
        if doc is None:
            w.append(c.missing(p, why))
            continue
        ep = doc.get("enabledPlugins") if isinstance(doc, dict) else None
        if isinstance(ep, dict):
            names = ["%s%s" % (k, "" if v else " (off)") for k, v in sorted(ep.items())]
        elif isinstance(ep, list):
            names = sorted(str(x) for x in ep)
        else:
            names = []
        w.append("- enabledPlugins, %s: %s" % (label, ", ".join(names) or "(none)"))
    p = os.path.join(c.config_dir, "plugins", "installed_plugins.json")
    doc, why = load(p)
    if doc is None:
        w.append(c.missing(p, why))
    else:
        plugins = doc.get("plugins", doc) if isinstance(doc, dict) else {}
        rows = []
        for name, recs in sorted(plugins.items() if isinstance(plugins, dict) else []):
            recs = recs if isinstance(recs, list) else [recs]
            vers = sorted({str(r.get("version", "?")) for r in recs if isinstance(r, dict)}) or ["?"]
            rows.append("%s %s" % (name, "/".join(vers)))
        w.append("- Installed plugins: %s" % ("; ".join(rows) or "(none)"))
    return w


def sec_loop(c):
    w = ["\n## 3. The loop\n"]
    lp = c.loop if isinstance(c.loop, dict) else None
    if lp is None:
        w.append(c.missing(os.path.join(c.root, ".run", "mod", "loop.json"), c.loop_why or "not an object"))
    else:
        t = lp.get("task") or {}
        hb = lp.get("heartbeatAt")
        w.append("- loop.json: state %s, holds %s, ownerSid %s, heartbeat %s ago" % (
            lp.get("state", "-"), lp.get("holds", "-"), lp.get("ownerSid", "-"),
            age(c.now - hb / 1000) if isinstance(hb, (int, float)) and hb else "-"))
        w.append("- Task %s attempt %s, agentId %s, childBusy %s, stopped %s, recovery %s" % (
            t.get("id", "-"), t.get("attempt", "-"), t.get("agentId", "-"), t.get("childBusy", "-"),
            json.dumps(t.get("stopped")), json.dumps(t.get("recovery"))))
        w.append("- Report in hand (pendingContract): %s" % (one(t.get("pendingContract"), 80) or "none"))
        w.append("- queuedEdits %d, inboxDone %d" % (len(lp.get("queuedEdits") or []), len(lp.get("inboxDone") or {})))
    p = os.path.join(c.root, ".run", "status.json")
    doc, why = load(p)
    w.append(c.missing(p, why) if doc is None else "- status.json: %s" % one(json.dumps(doc, sort_keys=True), 1500))
    for label, p in (("Release marker", os.path.join(c.root, ".run", "mod", "release.json")),
                     ("Update marker", os.path.join(c.root, ".run", "mod", "update.json"))):
        doc, why = load(p)
        if doc is None:
            w.append(c.missing(p, why))
        elif not isinstance(doc, dict):
            w.append("- %s: %s" % (label, one(json.dumps(doc), 200)))
        else:
            at = doc.get("at")
            parts = ["%s %s" % (k, json.dumps(doc[k])) for k in ("state", "version", "kind", "mode", "rc", "from", "to")
                     if k in doc]
            if isinstance(at, (int, float)):
                parts.append("age %s" % age(c.now - (at / 1000 if at > 1e11 else at)))
            w.append("- %s: %s" % (label, one(", ".join(parts) or "(empty)", 400)))
    return w


def done_ids(v):
    return set(v) if isinstance(v, (dict, list)) else set()


def sec_inbox(c):
    w = ["\n## 4. Requests nobody answered\n"]
    loop_done = done_ids((c.loop or {}).get("inboxDone") if isinstance(c.loop, dict) else None)
    found = False
    any_inbox = False
    older = 0  # past the mod's 24 h (state.ts DONE_TTL_MS): it never acts on them, and their done notes have expired
    for d in sessions(c):
        inbox = os.path.join(d, "inbox")
        if not os.path.isdir(inbox):
            continue
        any_inbox = True
        st, _ = load(os.path.join(d, "state.json"))
        router_done = done_ids(st.get("inboxDone") if isinstance(st, dict) else None)
        for name in sorted(os.listdir(inbox)):
            m = REQUEST_RE.match(name)
            if m is None:
                continue
            rid, kind = name[:-5], m.group(3)
            if rid in loop_done or (kind not in LOOP_KINDS and rid in router_done):
                continue
            if c.now - int(m.group(1)) / 1000 > 24 * 3600:
                older += 1
                continue
            found = True
            hold = ""
            if kind == "release":
                req, _ = load(os.path.join(inbox, name))
                inp = req.get("input") if isinstance(req, dict) else None
                hold = ", hold %s" % inp.get("hold") if isinstance(inp, dict) and inp.get("hold") else ""
            w.append("- session %s: %s, kind %s%s, age %s" % (os.path.basename(d), rid, kind, hold,
                                                             age(c.now - int(m.group(1)) / 1000)))
    if not any_inbox:
        w.append("- (no inbox under `.run/mod/`, skipped)")
    elif not found:
        w.append("- (none in the last 24 hours)")
    if older:
        w.append("- %d older than 24 hours left out: the mod no longer acts on them" % older)
    return w


def sec_journal(c):
    w = ["\n## 5. The journal (last 80 lines of the newest index)\n"]
    idx = sorted(glob.glob(os.path.join(c.root, ".claude-state", "journal", "INDEX-*.md")))
    if not idx:
        w.append(c.missing(os.path.join(c.root, ".claude-state", "journal", "INDEX-*.md")))
        return w
    lines = [x for x in (text_of(idx[-1]) or "").splitlines() if x.strip()]
    w.append("From `%s`:\n" % c.rel(idx[-1]))
    w.append("```")
    w += [one(x, 200) for x in lines[-80:]]
    w.append("```")
    return w


def sec_helpers(c):
    w = ["\n## 6. Helpers\n"]
    since = c.now - c.days * 86400
    agents, parent_of = {}, {}
    if c.proj is None:
        w.append("- (no Claude Code transcripts for this project, skipped)")
    else:
        for path in glob.glob(os.path.join(c.proj, "*", "subagents", "agent-*.jsonl")):
            if os.path.getmtime(path) < since:
                continue
            aid = os.path.basename(path)[len("agent-"):-len(".jsonl")]
            meta, _ = load(path[:-len(".jsonl")] + ".meta.json")
            meta = meta if isinstance(meta, dict) else {}
            if meta.get("agentType") not in PA3_TYPES:
                continue
            a = scan(path)
            a.update(id=aid, meta=meta, session=os.path.basename(os.path.dirname(os.path.dirname(path))))
            agents[aid] = a
            for kid, (what, _ts, _arg) in a["started"].items():
                if what == "agent":
                    parent_of[kid] = aid
    for aid, a in agents.items():
        a["parent"] = a["meta"].get("parentAgentId") or parent_of.get(aid, "")
        a["open"] = open_background(a)
        reasons = []
        if a["meta"].get("finished") is False:
            reasons.append("Claude Code marks it not finished")
        if a["open"]:
            reasons.append("background work never reported back")
        if a["open_uses"]:
            reasons.append("its last tool call never returned")
        if not a["handbacks"]:
            reasons.append("never handed back")
        a["reasons"] = reasons
    runs = journal_rows(c.root)
    task_of = {r["agent"]: r["task"] for r in runs if r["agent"]}
    w.append("Helpers read (last %g days): %d. Flagged: %d.\n" % (c.days, len(agents),
                                                                  sum(1 for a in agents.values() if a["reasons"])))

    w.append("### Tasks whose end came 5+ minutes after their expert's first report\n")
    slow = 0
    for r in runs:
        if when(r["start"]) is None or when(r["start"]) < since or r["report"] is None:
            continue
        end = when(r["end"]) if r["end"] else c.now
        wait = (end - (when(r["report"]) or end)) / 60
        if wait >= 5 or r["end"] is None:
            slow += 1
            w.append("- %s %s: started %s, first report %s, ended %s, waited %.0f%s min: %s" % (
                r["task"], r["agent"] or "-", stamp(r["start"]), stamp(r["report"]),
                stamp(r["end"]) if r["end"] else "not yet", wait, "" if r["end"] else "+", one(r["report_text"], 60)))
    if not slow:
        w.append("- (none)")

    w.append("\n### Flagged helpers, newest first\n")
    flagged = sorted((a for a in agents.values() if a["reasons"]), key=lambda a: a["last"] or "", reverse=True)
    for a in flagged[:30]:
        m = a["meta"]
        parent = a["parent"]
        w.append("#### %s %s: %s" % (m.get("agentType"), a["id"], one(m.get("description", ""), 80)))
        w.append("- Why flagged: %s" % "; ".join(a["reasons"]))
        w.append("- Session %s; parent %s%s; task %s; finished flag %s; model %s" % (
            a["session"], parent or "-", "" if parent not in agents else " (%s)" % agents[parent]["meta"].get("agentType"),
            task_of.get(parent) or task_of.get(a["id"]) or "-", m.get("finished", "absent"),
            ", ".join(sorted(a["models"])) or "-"))
        w.append("- Active %s to %s; hand-backs %d%s" % (stamp(a["first"]), stamp(a["last"]), len(a["handbacks"]),
                 "" if not a["handbacks"] else ", last %s" % stamp(a["handbacks"][-1])))
        for o in a["open"]:
            w.append("- Open: %s" % o)
        for name, arg, ts in a["open_uses"][-3:]:
            w.append("- Never returned: %s %s at %s" % (name, arg, stamp(ts)))
        w.append("- Last tool calls:")
        for ts, name, arg in a["tools"][-12:]:
            w.append("  - %s %s %s" % (stamp(ts), name, arg))
        w.append("- Last words: %s\n" % one(a["last_text"], 300))
    if not flagged:
        w.append("- (none)")

    ss = sessions(c)
    w.append("\n### The newest session's facts\n")
    if not ss:
        w.append("- (no session folder under `.run/mod/`, skipped)")
        return w
    d = ss[0]
    w.append("Session %s:" % os.path.basename(d))
    facts = sorted(glob.glob(os.path.join(d, "agents", "*.json")))
    if not facts:
        w.append(c.missing(os.path.join(d, "agents")))
    for p in facts:
        f, why = load(p)
        if not isinstance(f, dict):
            w.append(c.missing(p, why or "not an object"))
            continue
        w.append("- agent %s: type %s, parentId %s, updatedAt %s" % (
            os.path.basename(p)[:-5], f.get("type") or "-", f.get("parentId") or "-", ms_stamp(f.get("updatedAt"))))
    p = os.path.join(d, "shells.json")
    doc, why = load(p)
    if doc is None:
        w.append(c.missing(p, why))
    else:
        for aid, shells in sorted(doc.items() if isinstance(doc, dict) else []):
            for s in shells if isinstance(shells, list) else []:
                if isinstance(s, dict):
                    w.append("- shell of %s: %s %s, %s, started %s%s" % (
                        aid, s.get("id", "-"), one(s.get("name", ""), 120), s.get("type", "-"), ms_stamp(s.get("at")),
                        "" if s.get("exit") is None else ", exit %s" % s.get("exit")))
    p = os.path.join(d, "parents.json")
    doc, why = load(p)
    if doc is not None:
        w.append("- parents.json: %s" % one(json.dumps(doc, sort_keys=True), 2000))
    return w


def sec_stall(c):
    w = ["\n## 7. The newest stall snapshot\n"]
    files = glob.glob(os.path.join(c.root, ".run", "mod", "stalls", "*"))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        w.append(c.missing(os.path.join(c.root, ".run", "mod", "stalls")))
        return w
    p = max(files, key=os.path.getmtime)
    w.append("From `%s`:\n" % c.rel(p))
    w.append(text_of(p) or "")
    return w


def build(c):
    w = ["Project `%s`, written %s.\n" % (c.root, time.strftime("%Y-%m-%d %H:%M"))]
    for sec in (sec_versions, sec_plugins, sec_loop, sec_inbox, sec_journal, sec_helpers, sec_stall):
        try:
            w += sec(c)
        except Exception as e:  # a section that breaks must not cost the rest of the report
            w.append("- (this section failed: %s: %s)" % (type(e).__name__, one(e, 200)))
    return "\n".join(w) + "\n"


def report(root, config_dir, home, days=30.0):
    """The report's text: header + scrubbed body, cut to CAP characters."""
    c = Ctx(os.path.abspath(root), config_dir, home, days)
    text = HEADER + "\n" + scrubber(home)(build(c))
    if len(text) > CAP:
        note = "\n\n(cut at %d characters)\n" % CAP
        text = text[:CAP - len(note)] + note
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--out")
    ap.add_argument("--config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))
    ap.add_argument("--home", default=(os.environ.get("USERPROFILE") if os.name == "nt" else None)
                    or os.path.expanduser("~"))
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    text = report(root, args.config_dir, os.path.abspath(args.home), args.days)
    out = args.out or os.path.join(root, ".run", "pa-bugreport-%s.md" % time.strftime("%Y%m%d-%H%M"))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("wrote %s (%d characters)" % (out.replace("\\", "/"), len(text)))


if __name__ == "__main__":
    main()
