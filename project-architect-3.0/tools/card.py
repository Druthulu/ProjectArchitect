#!/usr/bin/env python
"""card.py -- manage the HOW_WE_WORK.md card.

interview [--name --experience --domain --autonomy --notify] [--defaults]
          fill the card's placeholders in place (TTY prompts for missing, --defaults
          writes the template defaults)
slice <role>
          print, in file order, each section whose role tag names the role
check     print chars/own/cap/placeholders/standing and exit 1 when the project's own lines
          pass the cap, with placeholders or a Standing decisions heading (3.33 C2: a line
          equal to a current template line, after the card's fills, is PA3's and not counted)

Stdlib only; no imports from `pa/`.
"""

import argparse
import json
import os
import re
import sys

_SCRIPT = "card.py"
DEFAULT_MAX_CHARS = 10000   # 3.32 B7: was 7000; characters stay the unit: a tool needs no tokenizer to count them
TEMPLATE = "HOW_WE_WORK.template.md"
_GENERIC_CHARS = 10         # curate.py's: a template line with fewer literal chars matches any line

# placeholder pattern
_PH_RE = re.compile(r"\{\{(\w+)\}\}")

# role tag pattern: <!-- roles: expert coder ... -->
_ROLE_TAG_RE = re.compile(r"<!--\s*roles:\s*([\w\s,]+?)\s*-->")

# section heading pattern
_HEADING_RE = re.compile(r"^(## .+?)(\s*<!--.*-->)?\s*$")


def _cnote(path, **kw):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def _find_root():
    """Walk up from cwd to find the project root (has .claude/ or HOW_WE_WORK.md)."""
    d = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(d, "HOW_WE_WORK.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def _card_path(root):
    return os.path.join(root, "HOW_WE_WORK.md")


def _read_card(root):
    path = _card_path(root)
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_card(root, text):
    path = _card_path(root)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _max_chars(root):
    """Read the cap from .claude/pa.json ``card.max_chars`` (default 10000)."""
    pa_json = os.path.join(root, ".claude", "pa.json")
    try:
        with open(pa_json, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg.get("card", {}).get("max_chars", DEFAULT_MAX_CHARS)
    except (OSError, json.JSONDecodeError, KeyError):
        return DEFAULT_MAX_CHARS


def _parse_sections(text):
    """Parse the card into a list of ``(heading, tag_line, roles, body)`` tuples.

    *roles* is a set of role names from the heading's tag, or ``None`` when
    there is no tag (printed for every role).  *body* is everything from the
    heading line (exclusive) to the next heading (exclusive).
    """
    sections = []
    lines = text.splitlines(True)
    header_lines = []  # lines before the first ## heading
    current_heading = None
    current_tag_line = None
    current_roles = None
    body_lines = []

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            if current_heading is not None:
                sections.append((current_heading, current_tag_line, current_roles,
                                 "".join(body_lines)))
            elif header_lines:
                # everything before the first heading is the preamble
                sections.append((None, None, None, "".join(header_lines)))
            current_heading = m.group(1)
            current_tag_line = line
            tag = m.group(2) or ""
            tm = _ROLE_TAG_RE.search(tag)
            if tm:
                current_roles = set(r.strip() for r in re.split(r"[\s,]+", tm.group(1)) if r.strip())
            else:
                current_roles = None
            body_lines = []
        else:
            if current_heading is None:
                header_lines.append(line)
            else:
                body_lines.append(line)

    # flush last section
    if current_heading is not None:
        sections.append((current_heading, current_tag_line, current_roles,
                         "".join(body_lines)))
    elif header_lines:
        sections.append((None, None, None, "".join(header_lines)))

    return sections


# -- interview ----------------------------------------------------------------

_INTERVIEW_FIELDS = {
    "name": ("DEVELOPER_NAME", "Developer name"),
    "experience": ("DEVELOPER_EXPERIENCE", "Experience level (e.g. advanced, intermediate)"),
    "domain": ("DEVELOPER_DOMAIN", "Primary domain"),
    "autonomy": ("AUTONOMY_POSTURE", "Autonomy posture"),
    "notify": ("NOTIFY_CHANNEL", "Notification channel (e.g. toast, discord)"),
}


def _default_name():
    """Git user.name or the OS login."""
    try:
        import subprocess
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, Exception):
        pass
    return os.environ.get("USER") or os.environ.get("USERNAME") or "developer"


def _default_domain(root):
    """The repo folder name."""
    return os.path.basename(os.path.abspath(root))


def cmd_interview(args, root):
    """Fill placeholders in the card from interview answers."""
    text = _read_card(root)
    _cnote(_card_path(root), script=_SCRIPT, sub="interview", whole=True, root=root)

    values = {}
    for field, (placeholder, prompt_text) in _INTERVIEW_FIELDS.items():
        val = getattr(args, field, None)
        if val:
            values[placeholder] = val
        elif args.defaults:
            if field == "name":
                values[placeholder] = _default_name()
            elif field == "domain":
                values[placeholder] = _default_domain(root)
            elif field == "experience":
                values[placeholder] = "advanced"
            elif field == "autonomy":
                values[placeholder] = "full inside an approved plan; stop only at the two gates"
            elif field == "notify":
                values[placeholder] = "toast"
        elif sys.stdin.isatty():
            default = ""
            if field == "name":
                default = _default_name()
            elif field == "domain":
                default = _default_domain(root)
            elif field == "experience":
                default = "advanced"
            elif field == "autonomy":
                default = "full inside an approved plan; stop only at the two gates"
            elif field == "notify":
                default = "toast"
            try:
                answer = input("%s [%s]: " % (prompt_text, default))
            except (EOFError, KeyboardInterrupt):
                answer = ""
            values[placeholder] = answer.strip() or default

    # replace placeholders in text
    for placeholder, val in values.items():
        text = text.replace("{{%s}}" % placeholder, val)

    # when --defaults, fill any remaining {{...}} with a dash (not a prompt)
    if args.defaults:
        remaining_phs = _PH_RE.findall(text)
        for ph in remaining_phs:
            text = text.replace("{{%s}}" % ph, "—")
            values[ph] = "—"

    _write_card(root, text)
    _cnote(_card_path(root), script=_SCRIPT, sub="interview",
           added=len(values), root=root)
    remaining = len(_PH_RE.findall(text))
    print("interview: filled %d placeholder(s), %d remaining" % (len(values), remaining))
    return 0


# -- slice --------------------------------------------------------------------

# 3.24 T3: the Gen 3 router (pa-session) left; its slice is offered no more.
_RETIRED_ROLES = {"router": "the Gen 3 router left at 3.24"}


def cmd_slice(args, root):
    """Print sections whose role tag names the given role."""
    role = args.role
    if role in _RETIRED_ROLES:
        sys.stderr.write("card.py: no %s slice (%s)\n" % (role, _RETIRED_ROLES[role]))
        return 2
    text = _read_card(root)
    _cnote(_card_path(root), script=_SCRIPT, sub="slice", whole=True, root=root)
    # strip trailing whitespace but keep one final newline
    print(slice_text(text, role))
    return 0


def slice_text(text, role):
    """The card's text for ``role``: the preamble and the sections its tag names (or that carry no tag)."""
    out = []
    for heading, tag_line, roles, body in _parse_sections(text):
        if heading is None:
            # preamble: always print (the title and comment)
            out.append(body)
            continue
        if roles is None or role in roles:
            out.append(tag_line)
            out.append(body)
    return "".join(out).rstrip()


# -- check --------------------------------------------------------------------

def _pkg_home():
    """The package root (curate.py:_pkg_home): this script's parent when it has pa/, else ~/.claude/pa3."""
    up = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(up, "pa")):
        return up
    return os.path.join(os.path.expanduser("~"), ".claude", "pa3")


def template_text(root):
    """3.33 C2: the card template: the project's managed ``templates/`` copy, else the package's
    (curate.py:_template_file); None when there is none."""
    for base in (os.path.join(root, "templates"), os.path.join(_pkg_home(), "templates")):
        path = os.path.join(base, TEMPLATE)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as fh:
                return fh.read()
    return None


def collapse(line):
    """``line`` with its whitespace runs collapsed: how a card line is compared with a template line."""
    return " ".join(line.split())


def _line_re(line):
    """A template line as a full-match regex, each {{KEY}} capturing its fill (mirrors curate.py:_line_re)."""
    out, seen, pos = [], set(), 0
    for m in _PH_RE.finditer(line):
        out.append(re.escape(line[pos:m.start()]))
        key = m.group(1)
        out.append("(?P=%s)" % key if key in seen else "(?P<%s>.*?)" % key)
        seen.add(key)
        pos = m.end()
    out.append(re.escape(line[pos:]))
    return re.compile("".join(out) + r"\s*$")


def pa3_lines(text, tmpl_text):
    """3.33 C2: the indexes of ``text``'s lines that are PA3's: equal, whitespace collapsed, to a current
    template line with the card's fills. The fills come from the template's placeholder lines with at
    least _GENERIC_CHARS literal chars, matched against the card (first match wins); a template line
    with a placeholder left unfilled matches nothing."""
    lines = text.replace("\r\n", "\n").split("\n")
    tmpl = [ln for ln in tmpl_text.replace("\r\n", "\n").split("\n") if ln.strip()]
    rxs = [_line_re(t) for t in tmpl
           if _PH_RE.search(t) and len(_PH_RE.sub("", t).strip()) >= _GENERIC_CHARS]
    fills = {}
    for ln in lines:
        for rx in rxs:
            m = rx.match(ln)
            if m:
                for k, v in m.groupdict().items():
                    fills.setdefault(k, v)
    ours = set()
    for t in tmpl:
        keys = _PH_RE.findall(t)
        if all(k in fills for k in keys):
            ours.add(collapse(_PH_RE.sub(lambda m: fills[m.group(1)], t)))
    return {i for i, ln in enumerate(lines) if ln.strip() and collapse(ln) in ours}


def own_chars(text, tmpl_text):
    """3.33 C2: the cap's count: the chars of the card's own lines, each with its newline (PA3's lines,
    :func:`pa3_lines`, don't count); the whole card when there is no template."""
    if not tmpl_text:
        return len(text)
    lines = text.split("\n")
    last = len(lines) - 1
    return len(text) - sum(len(lines[i]) + (i < last) for i in pa3_lines(text, tmpl_text))


def cmd_check(args, root):
    """Print card stats and exit 1 when unhealthy."""
    text = _read_card(root)
    _cnote(_card_path(root), script=_SCRIPT, sub="check", whole=True, root=root)
    chars = len(text)
    own = own_chars(text, template_text(root))
    cap = _max_chars(root)
    placeholders = len(_PH_RE.findall(text))
    standing = 1 if re.search(r"^## Standing decisions", text, re.MULTILINE) else 0
    print("chars=%d own=%d cap=%d placeholders=%d standing=%d" % (chars, own, cap, placeholders, standing))
    if own > cap or placeholders > 0 or standing:
        return 1
    return 0


# -- main ---------------------------------------------------------------------

def main(argv=None):
    try:  # the card carries non-ASCII punctuation; a cp1252 console must not break `slice`
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="card.py",
                                description="Manage the HOW_WE_WORK.md card.")
    sub = p.add_subparsers(dest="command")

    iv = sub.add_parser("interview", help="fill card placeholders from answers")
    iv.add_argument("--name", default=None)
    iv.add_argument("--experience", default=None)
    iv.add_argument("--domain", default=None)
    iv.add_argument("--autonomy", default=None)
    iv.add_argument("--notify", default=None)
    iv.add_argument("--defaults", action="store_true",
                    help="write the template defaults without prompting")

    sl = sub.add_parser("slice", help="print sections for a role")
    sl.add_argument("role", help="the role name (e.g. expert, coder, auditor)")

    sub.add_parser("check", help="print card health and exit 1 when unhealthy")

    args = p.parse_args(sys.argv[1:] if argv is None else list(argv))
    if not args.command:
        p.print_help()
        return 2

    root = _find_root()

    if args.command == "interview":
        return cmd_interview(args, root)
    if args.command == "slice":
        return cmd_slice(args, root)
    if args.command == "check":
        return cmd_check(args, root)

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
