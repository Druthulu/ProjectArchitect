#!/usr/bin/env python
"""records.py -- the journal's records: parse filings, assign ids, print the index, seed and to-dos (3.31).

    PY tools/records.py [--root DIR] <command> ...

Record entries (mod/pa/hooks/journal.ts, kind `record`): live
`<root>/.claude-state/journal/<YYYY-MM-DD>/<HHMMSS>-<seq><w>-claude-record.md` and the `## <stem>` sections of rolled
day files `<root>/.claude-state/journal/<YYYY-MM-DD>.md` (journal_rollup.split_day). Entry = `---`, `key: value`
lines (`ts`, `through`, ...), `---`, blank line, body: the filing reply.

Filing reply (docs/pa3-build/3.3/3.31-journal-records.md §1): `nothing to file` (any case, optional `.`; the mod's
body `(nothing to file)`), or topic blocks: `topic:`, `idea:`, `refinement:`, `headline:` single fields;
`decision:`, `finding:`, `open:` outcomes with indented attributes `overrides:`, `supersedes: <id>[, <id>]`,
`binds work: yes|no`, `landed:`, `evidence:`; `retire: <id> - <why>` lines (<id>: `R<n>`). An indented line that is
no attribute continues the value above; so does a non-indented line that is no field. CRLF, `- ` bullets, `**bold**`
keys and blank lines are tolerated. Text before the first `topic:` (or a reply with none) becomes one in-force outcome of
kind `note` under the topic `(unparsed filing)`.

Ids `R1..Rn`: decision, finding, open and note outcomes in filing order (entries by (ts, path), then position).
Status, over the merged timeline (all entries by (ts, path), then position): `in force`; `superseded by <id>` (a later
outcome's `supersedes:`); `retired by <entry> (<why>)` (a later `retire:` line); the first event wins; an unknown or
not-earlier id is ignored. Topics group by case-insensitive title; a topic's title, date and headline are its newest filing's (headline: the newest one given).
Entry paths print as `.claude-state/journal/<date>/<stem>.md` (live) or `.claude-state/journal/<date>.md#<stem>`.

Commands (output shapes):
  index      rewrites `.claude-state/journal/TOPICS.md` (only when changed; written once any record entry exists):
             three `#` header lines, the second `# filed through: <ISO>` (the newest `through` of every live record
             entry, `nothing to file` ones included; `(none)` without one: the mod reads it at session start), then
             `<id> | <date> | <kind> | <status> | <topic> | <first sentence, <=100 chars> | <entry path>` per
             outcome, in timeline order (pipes escaped `\\|`).
             Prints `indexed: <n> outcomes in <m> topics`.
  seed [--budget-chars N]   (default 28500, ~10k real tokens: the mod passes it, seed.ts holds the rate; headlines
             get a sixth)
             `## Outcomes in force`: the current phase's outcomes (filed on or after phase_start) and `binds work`
             outcomes from any phase. Per topic, newest first: `### <topic> (<date>) · <headline>`, then
             `- R<n> <kind>: <text>` with `  overrides: `, `  binds work: `, `  evidence: ` lines; a blank line.
             `## Earlier topics` / `- <date> <topic>: <headline>` per topic with nothing in force, and the other
             in-force topics (older phases', or past the budget) with ` (in force; see TOPICS.md)`, newest first; then
             `(<n> more: .claude-state/journal/TOPICS.md)` when headlines overflow. Nothing at all without topics.
  seed --all  3.32 T4b: no budget: every in-force outcome in full, every earlier topic's headline, no
             `(<n> more: ...)` line, however large (the generation planner reads it; the mod's seed keeps the budget).
  state [--budget-chars N]   (default 8550, ~3k real tokens) 3.32 B6: `## Pre-PA3 state`, docs/PRE_PA3_STATE.md, until
             an outcome is on record for the current phase; the file stays on disk. Nothing without the file.
  inforce [--binds-work] [--ids] [--max-tokens N]   (default 8000)
             in-force outcomes, newest first: `- <id> <kind>: <text>` and attribute lines
             (overrides, supersedes, binds work, landed, evidence), or with --ids `<id> <kind>: <first sentence>`;
             past the cap
             `(<n> more: .claude-state/journal/TOPICS.md)`. Nothing when none.
  todo <entry>       for that entry: `retire or narrow: <overrides> (R<n> overrides it)`; `R<n> open: <text>`.
             <entry>: live or rolled form, absolute or root-relative.
  headlines <entry>  `<topic>: <headline>` per topic block (no headline: the first outcome's first sentence).
  check <file>       parses a reply (a body, or an entry with frontmatter); ids continue after --root's records;
             `supersedes:` and `retire:` resolve against --root's ids and the reply's earlier ones.
             Prints `nothing to file`, or per topic `topic: <t>` / `  headline: <h>` / `  <id> <kind>: <sentence>`
             / `  retire <id>`; then `warning: ...` lines. Exit 1 only when nothing usable was read and the
             reply is not `nothing to file`.
Exit 0 unless stated; an unknown entry for todo/headlines prints `no record entry: <path>`, exit 1. Stdlib only.
"""

import argparse
import json
import os
import re
import sys
import textwrap

try:                          # UTF-8 output even when not started with -X utf8; LF on Windows too (3.31 T6: the seed)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _genplan  # noqa: E402
import journal_rollup  # noqa: E402

_SCRIPT = "records.py"
J = ".claude-state/journal"
JOURNAL_DIR = os.path.join(".claude-state", "journal")
TOPICS = J + "/TOPICS.md"
STATE_REL = "docs/PRE_PA3_STATE.md"     # 3.32 B6
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
RECORD_STEM_RE = re.compile(r"^[0-9]{6}-[0-9]{3}[rw]-claude-record$")
ENTRY_ARG_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:[/\\]|\.md#)([0-9]{6}-[0-9]{3}[rw]-claude-record)(?:\.md)?$")
FIELDS = ("topic", "idea", "refinement", "headline", "decision", "finding", "open", "retire")
OUTCOMES = ("decision", "finding", "open")
ATTRS = ("overrides", "supersedes", "binds work", "landed", "evidence")
KEY_RE = re.compile(r"^(?:\*\*|__)?([A-Za-z][A-Za-z ]*?)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*)$")
RETIRE_RE = re.compile(r"^(R[0-9]+)\b\s*(?:[-—–:]\s*)?(.*)$")
ID_RE = re.compile(r"\b(R[0-9]+)\b")
UNPARSED = "(unparsed filing)"
APPROVED_RE = re.compile(r"^Approved:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.M)
INDEX_HEADER = ("# Journal records -- rebuilt by tools/records.py index; one line per outcome\n"
                "# filed through: %s\n"
                "# id | date | kind | status | topic | first sentence | entry\n")


def _cnote(path, **kw):
    try:
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


# ------------------------------------------------------------ parsing --- #

def parse_entry(text):
    """-> (meta, body) of an entry text; (({}, text) when it has no frontmatter)."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}, text.replace("\r\n", "\n").strip("\n")
    end = lines.index("---", 1)
    meta = {}
    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z]+): (.*)$", line)
        if not m:
            continue
        raw = m.group(2)
        if raw.startswith('"'):
            try:
                raw = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(raw, str):
                continue
        meta[m.group(1)] = raw
    return meta, "\n".join(lines[end + 1:]).strip("\n")


def is_nothing(body):
    t = body.strip().strip("()").strip().rstrip(".").strip().lower()
    return t == "nothing to file"


def _key(line):
    """-> (key, value) when the line (bullet and bold stripped) starts with a known key, else None."""
    s = re.sub(r"^[-*+]\s+", "", line.strip())
    m = KEY_RE.match(s)
    if not m:
        return None
    k = re.sub(r"\s+", " ", m.group(1).strip().lower())
    if k in FIELDS or k in ATTRS:
        return k, m.group(2).strip()
    return None


def _new_topic(title):
    return {"title": title, "idea": "", "refinement": "", "headline": "", "items": []}


def parse_filing(body):
    """-> {"nothing": bool, "topics": [topic], "warnings": [str]}.

    topic = {title, idea, refinement, headline, items}; item = {kind, text, attrs{}} (kind decision|finding|open|
    note) or {kind: "retire", id: int|None, why, raw}.
    """
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    res = {"nothing": False, "topics": [], "warnings": []}
    if is_nothing(text):
        res["nothing"] = True
        return res
    if not text.strip():
        return res
    text = textwrap.dedent(text)
    topics, preamble = [], []
    topic, target = None, None          # target: (dict, key) the next continuation extends
    for line in text.split("\n"):
        if not line.strip():
            continue
        indented = line[:1] in (" ", "\t")
        kv = _key(line)
        if kv and (not indented or kv[0] in ATTRS or kv[0] == "topic"):
            k, v = kv
            if k == "topic":
                topic = _new_topic(v)
                topics.append(topic)
                target = (topic, "title")
                continue
            if topic is None:
                preamble.append(line.strip())
                continue
            if k in ("idea", "refinement", "headline"):
                topic[k] = v
                target = (topic, k)
            elif k in OUTCOMES:
                item = {"kind": k, "text": v, "attrs": {}}
                topic["items"].append(item)
                target = (item, "text")
            elif k == "retire":
                item = {"kind": "retire", "raw": v, "id": None, "why": ""}
                topic["items"].append(item)
                target = (item, "raw")
            else:                                    # an attribute
                outs = [i for i in topic["items"] if i["kind"] != "retire"]
                if not outs:
                    res["warnings"].append('topic "%s": %s: before any outcome, ignored' % (topic["title"], k))
                    target = None
                    continue
                outs[-1]["attrs"][k] = v
                target = (outs[-1]["attrs"], k)
            continue
        s = line.strip()
        if topic is None:
            preamble.append(s)
        elif target is None:
            res["warnings"].append('topic "%s": unparsed line ignored: %s' % (topic["title"], s[:60]))
        else:
            d, k = target
            d[k] = (d[k] + " " + s).strip() if d[k] else s
    for t in topics:
        for it in t["items"]:
            if it["kind"] == "retire":
                m = RETIRE_RE.match(it["raw"].strip())
                if m:
                    it["id"], it["why"] = m.group(1), m.group(2).strip()
                else:
                    res["warnings"].append('topic "%s": retire line not read: %s' % (t["title"], it["raw"][:60]))
    if preamble:
        note = _new_topic(UNPARSED)
        note["items"].append({"kind": "note", "text": "\n".join(preamble), "attrs": {}})
        topics.insert(0, note)
        res["warnings"].append("unparsed text kept as a note (%d chars)" % len("\n".join(preamble)))
    for t in topics:
        if t["title"] == UNPARSED:
            continue
        if not t["headline"]:
            res["warnings"].append('topic "%s": no headline' % t["title"])
        if not any(i["kind"] != "retire" for i in t["items"]):
            res["warnings"].append('topic "%s": no outcomes' % t["title"])
    res["topics"] = topics
    return res


def first_sentence(text, cap=100):
    t = " ".join(text.split())
    m = re.match(r"^(.+?[.!?])(?:\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= cap else s[:cap - 1].rstrip() + "…"


# ------------------------------------------------------------ loading --- #

def read_entries(root):
    """-> [{"path": display path, "date", "ts", "meta", "body"}] of every record entry, in filing order."""
    jdir = os.path.join(root, JOURNAL_DIR)
    out, chars = [], 0
    if not os.path.isdir(jdir):
        return out
    for name in sorted(os.listdir(jdir)):
        full = os.path.join(jdir, name)
        if DAY_RE.match(name) and os.path.isdir(full):
            for f in sorted(os.listdir(full)):
                if f.endswith(".md") and RECORD_STEM_RE.match(f[:-3]):
                    text = _read(os.path.join(full, f))
                    chars += len(text)
                    meta, body = parse_entry(text)
                    out.append({"path": "%s/%s/%s" % (J, name, f), "date": name,
                                "ts": meta.get("ts", ""), "meta": meta, "body": body})
        elif name.endswith(".md") and DAY_RE.match(name[:-3]) and os.path.isfile(full):
            text = _read(full)
            chars += len(text)
            for stem, etext in sorted(journal_rollup.split_day(text).items()):
                if RECORD_STEM_RE.match(stem):
                    meta, body = parse_entry(etext)
                    out.append({"path": "%s/%s#%s" % (J, name, stem), "date": name[:-3],
                                "ts": meta.get("ts", ""), "meta": meta, "body": body})
    if out:
        _cnote(jdir, script=_SCRIPT, sub="read", whole=True, file_chars=chars, root=root)
    out.sort(key=lambda e: (e["ts"], e["path"]))
    return out


def build(entries):
    """-> (outcomes, topics). Parses every entry, assigns ids, sets statuses, groups topics.

    outcome = {id ("R<n>"), kind, text, attrs, topic (key), title, date, path, status}; topics: {key: {title,
    date, headline, order, filings: [(entry, topic block)]}}. Outcomes in timeline order.
    """
    outcomes, topics, by_id, count = [], {}, {}, 0
    for order, e in enumerate(entries):
        e["parsed"] = parse_filing(e["body"])
        for t in e["parsed"]["topics"]:
            key = t["title"].strip().lower()
            tp = topics.setdefault(key, {"title": t["title"], "date": e["date"], "headline": "", "order": order,
                                         "filings": []})
            tp.update(title=t["title"], date=e["date"], order=order)
            if t["headline"]:
                tp["headline"] = t["headline"]
            tp["filings"].append((e, t))
            for it in t["items"]:
                if it["kind"] == "retire":
                    tgt = by_id.get(it["id"])
                    if tgt is not None and tgt["status"] == "in force":
                        why = " (%s)" % it["why"] if it["why"] else ""
                        tgt["status"] = "retired by %s%s" % (e["path"], why)
                    continue
                count += 1
                o = {"id": "R%d" % count, "kind": it["kind"], "text": it["text"], "attrs": it["attrs"],
                     "topic": key, "title": t["title"], "date": e["date"], "path": e["path"],
                     "status": "in force"}
                it["rid"] = o["id"]
                outcomes.append(o)
                for ref in ID_RE.findall(it["attrs"].get("supersedes", "")):
                    tgt = by_id.get(ref)          # only earlier outcomes are in by_id yet
                    if tgt is not None and tgt["status"] == "in force":
                        tgt["status"] = "superseded by %s" % o["id"]
                by_id[o["id"]] = o
    for tp in topics.values():
        if not tp["headline"]:
            firsts = [it for _e, t in tp["filings"] for it in t["items"] if it["kind"] != "retire"]
            tp["headline"] = first_sentence(firsts[0]["text"]) if firsts else ""
    return outcomes, topics


def load(root):
    return build(read_entries(root))


# ----------------------------------------------------------- commands --- #

def _esc(s):
    return s.replace("\\", "\\\\").replace("|", "\\|")


def render_index(outcomes, topics, through=""):
    rows = ["%s | %s | %s | %s | %s | %s | %s\n" % (
        o["id"], o["date"], o["kind"], _esc(o["status"]), _esc(topics[o["topic"]]["title"]),
        _esc(first_sentence(o["text"])), o["path"]) for o in outcomes]
    return INDEX_HEADER % (through or "(none)") + "".join(rows)


def cmd_index(root):
    entries = read_entries(root)
    outcomes, topics = build(entries)
    path = os.path.join(root, JOURNAL_DIR, "TOPICS.md")
    through = max((e["meta"].get("through", "") for e in entries), default="")
    text = render_index(outcomes, topics, through)
    old = _read(path) if os.path.isfile(path) else None
    if old != text and (entries or old is not None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        _cnote(path, script=_SCRIPT, sub="index", whole=True, added=len(text), root=root)
    n_topics = len(set(o["topic"] for o in outcomes))
    sys.stdout.write("indexed: %d outcomes in %d topics\n" % (len(outcomes), n_topics))
    return 0


def _attr_lines(o, keys):
    return ["  %s: %s\n" % (k, o["attrs"][k]) for k in keys if o["attrs"].get(k)]


def _outcome_block(o, keys):
    return "- %s %s: %s\n%s" % (o["id"], o["kind"], o["text"], "".join(_attr_lines(o, keys)))


def _newest_topics(topics):
    return sorted(topics.items(), key=lambda kv: -kv[1]["order"])


def _binds_work(o):
    return o["attrs"].get("binds work", "").strip().lower().startswith("yes")


def phase_start(root):
    """3.32 B4: the current phase's first day, `YYYY-MM-DD`: the plan's `Approved:` line, else the newest PhaseEnd's
    date; '' when there is neither (then every outcome counts as this phase's)."""
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            ped = (json.load(fh) or {}).get("phase_ends_dir") or "phase-ends"
    except (OSError, ValueError):
        ped = "phase-ends"
    pdir = os.path.join(root, ped)
    plan = os.path.join(pdir, "current", "PHASE_PLAN.md")
    m = APPROVED_RE.search(_read(plan)) if os.path.isfile(plan) else None
    if m:
        return m.group(1)
    names = os.listdir(pdir) if os.path.isdir(pdir) else []
    ends = [n for n in names if n.startswith("PhaseEnd_") and n.endswith(".md")]
    return max((_genplan.phaseend_date(_read(os.path.join(pdir, n))) for n in ends), default="")


def render_seed(outcomes, topics, budget_chars, since=""):
    """budget_chars None is `seed --all`: no budget and every in-force outcome; else what phase_start `since` allows."""
    if not topics:
        return ""
    head_budget = out_budget = float("inf")
    if budget_chars is not None:
        head_budget = budget_chars // 6
        out_budget = budget_chars - head_budget
    live, older = {}, set()
    for o in outcomes:
        if o["status"] == "in force":
            if budget_chars is None or o["date"] >= since or _binds_work(o):
                live.setdefault(o["topic"], []).append(o)
            else:
                older.add(o["topic"])
    blocks, used, earlier, full = [], 0, [], False
    for key, tp in _newest_topics(topics):
        if key in live and not full:
            b = "### %s (%s) · %s\n%s\n" % (tp["title"], tp["date"], tp["headline"], "".join(
                _outcome_block(o, ("overrides", "binds work", "evidence")) for o in live[key]))
            if used + len(b) <= out_budget:
                blocks.append(b)
                used += len(b)
                continue
            full = True
        mark = " (in force; see TOPICS.md)" if key in live or key in older else ""
        earlier.append("- %s %s: %s%s\n" % (tp["date"], tp["title"], tp["headline"], mark))
    parts = []
    if blocks:
        parts.append("## Outcomes in force\n\n" + "".join(blocks))
    if earlier:
        lines, used = [], 0
        for ln in earlier:
            if used + len(ln) > head_budget:
                break
            lines.append(ln)
            used += len(ln)
        more = len(earlier) - len(lines)
        tail = "(%d more: %s)\n" % (more, TOPICS) if more else ""
        parts.append("## Earlier topics\n\n" + "".join(lines) + tail)
    return "".join(parts)   # the in-force section ends with a blank line


def render_state(root, outcomes, budget_chars, since=""):
    """3.32 B6: the seed's `## Pre-PA3 state`: the page, its title dropped, whole lines within the budget; nothing with
    no page, or once an outcome is on record for the current phase (filed on or after phase_start `since`)."""
    path = os.path.join(root, *STATE_REL.split("/"))
    if not os.path.isfile(path) or any(o["date"] >= since for o in outcomes):
        return ""
    lines = _read(path).replace("\r\n", "\n").strip("\n").split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    kept, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > budget_chars:
            kept.append("(more: %s)" % STATE_REL)
            break
        kept.append(ln)
        used += len(ln) + 1
    body = "\n".join(kept).strip("\n")
    return "## Pre-PA3 state\n\n%s\n" % body if body else ""


def render_inforce(outcomes, binds_work, ids, max_tokens):
    sel = [o for o in reversed(outcomes) if o["status"] == "in force" and (not binds_work or _binds_work(o))]
    cap = max_tokens * 4
    lines, used = [], 0
    for o in sel:
        if ids:
            ln = "%s %s: %s\n" % (o["id"], o["kind"], first_sentence(o["text"]))
        else:
            ln = _outcome_block(o, ATTRS)
        if cap and used + len(ln) > cap:
            break
        lines.append(ln)
        used += len(ln)
    more = len(sel) - len(lines)
    return "".join(lines) + ("(%d more: %s)\n" % (more, TOPICS) if more else "")


def _find_entry(entries, arg):
    m = ENTRY_ARG_RE.search(arg.strip().replace("\\", "/"))
    if not m:
        return None
    date, stem = m.group(1), m.group(2)
    forms = ("%s/%s/%s.md" % (J, date, stem), "%s/%s.md#%s" % (J, date, stem))
    for e in entries:
        if e["path"] in forms:
            return e
    return None


def render_todo(entry):
    out = []
    for t in entry["parsed"]["topics"]:
        for it in t["items"]:
            if "rid" not in it:
                continue
            n, a = it["rid"], it["attrs"]
            if a.get("overrides"):
                out.append("retire or narrow: %s (%s overrides it)\n" % (a["overrides"], n))
            if it["kind"] == "open":
                out.append("%s open: %s\n" % (n, it["text"]))
    return "".join(out)


def render_headlines(entry):
    out = []
    for t in entry["parsed"]["topics"]:
        h = t["headline"]
        if not h:
            firsts = [i for i in t["items"] if i["kind"] != "retire"]
            h = first_sentence(firsts[0]["text"]) if firsts else ""
        out.append("%s: %s\n" % (t["title"], h))
    return "".join(out)

def check_reply(root, body):
    """-> (nothing, usable, lines, warnings) of a filing reply; new ids continue after root's."""
    outcomes, _topics = load(root)
    p = parse_filing(body)
    if p["nothing"]:
        return True, True, ["nothing to file\n"], []
    known = {o["id"] for o in outcomes}
    n = max((int(o["id"][1:]) for o in outcomes), default=0)
    usable = False
    warnings = list(p["warnings"])
    lines = []
    for t in p["topics"]:
        lines.append("topic: %s\n" % t["title"])
        if t["headline"]:
            lines.append("  headline: %s\n" % t["headline"])
            usable = usable or t["title"] != UNPARSED
        for it in t["items"]:
            if it["kind"] == "retire":
                if it["id"] is None:
                    continue
                lines.append("  retire %s\n" % it["id"])
                usable = True
                if it["id"] not in known:
                    warnings.append('topic "%s": retire %s: not an earlier record' % (t["title"], it["id"]))
                continue
            n += 1
            rid = "R%d" % n
            lines.append("  %s %s: %s\n" % (rid, it["kind"], first_sentence(it["text"])))
            usable = usable or it["kind"] != "note"
            for s in ID_RE.findall(it["attrs"].get("supersedes", "")):
                if s not in known:
                    warnings.append('topic "%s": %s supersedes %s: not an earlier record' % (t["title"], rid, s))
            known.add(rid)
    return False, usable, lines, warnings


def cmd_check(root, path):
    _meta, body = parse_entry(_read(path))
    _nothing, usable, lines, warnings = check_reply(root, body)
    sys.stdout.write("".join(lines) + "".join("warning: %s\n" % w for w in warnings))
    return 0 if usable else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="records.py", description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="rewrite .claude-state/journal/TOPICS.md")
    s = sub.add_parser("seed", help="the seed's two record sections")
    s.add_argument("--budget-chars", type=int, default=28500)
    s.add_argument("--all", action="store_true", help="no budget: everything, however large")
    s = sub.add_parser("state", help="the seed's pre-PA3 state section")
    s.add_argument("--budget-chars", type=int, default=8550)    # ~3k real tokens; the mod passes it
    s = sub.add_parser("inforce", help="in-force outcomes, newest first")
    s.add_argument("--binds-work", action="store_true")
    s.add_argument("--ids", action="store_true")
    s.add_argument("--max-tokens", type=int, default=8000)
    for name in ("todo", "headlines"):
        sub.add_parser(name, help="for one record entry").add_argument("entry")
    sub.add_parser("check", help="parse a filing reply").add_argument("file")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if a.cmd == "index":
        return cmd_index(root)
    if a.cmd == "check":
        return cmd_check(root, a.file)
    entries = read_entries(root)
    outcomes, topics = build(entries)
    if a.cmd == "seed":
        sys.stdout.write(render_seed(outcomes, topics, None if a.all else a.budget_chars, phase_start(root)))
    elif a.cmd == "state":
        sys.stdout.write(render_state(root, outcomes, a.budget_chars, phase_start(root)))
    elif a.cmd == "inforce":
        sys.stdout.write(render_inforce(outcomes, a.binds_work, a.ids, a.max_tokens))
    else:
        e = _find_entry(entries, a.entry)
        if e is None:
            sys.stdout.write("no record entry: %s\n" % a.entry)
            return 1
        sys.stdout.write(render_todo(e) if a.cmd == "todo" else render_headlines(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
