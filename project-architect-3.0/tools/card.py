#!/usr/bin/env python
"""card.py -- manage the HOW_WE_WORK.md card.

interview [--name --experience --domain --autonomy --notify --rhythm] [--defaults]
          fill the card's placeholders in place (TTY prompts for missing, --defaults
          writes the template defaults)
slice <role>
          print, in file order, each section whose role tag names the role
check     print chars/cap/placeholders/standing and exit 1 when over cap,
          with placeholders or a Standing decisions heading

Stdlib only; no imports from `pa/`.
"""

import argparse
import json
import os
import re
import sys

_SCRIPT = "card.py"

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
    """Read the cap from .claude/pa.json ``card.max_chars`` (default 7000)."""
    pa_json = os.path.join(root, ".claude", "pa.json")
    try:
        with open(pa_json, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg.get("card", {}).get("max_chars", 7000)
    except (OSError, json.JSONDecodeError, KeyError):
        return 7000


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
    "rhythm": ("RHYTHM", "Rhythm (autonomous or review)"),
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
            elif field == "rhythm":
                values[placeholder] = "autonomous"
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
            elif field == "rhythm":
                default = "autonomous"
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

def cmd_slice(args, root):
    """Print sections whose role tag names the given role."""
    role = args.role
    text = _read_card(root)
    _cnote(_card_path(root), script=_SCRIPT, sub="slice", whole=True, root=root)
    sections = _parse_sections(text)
    out = []
    for heading, tag_line, roles, body in sections:
        if heading is None:
            # preamble: always print (the title and comment)
            out.append(body)
            continue
        if roles is None or role in roles:
            out.append(tag_line)
            out.append(body)
    result = "".join(out)
    # strip trailing whitespace but keep one final newline
    print(result.rstrip())
    return 0


# -- check --------------------------------------------------------------------

def cmd_check(args, root):
    """Print card stats and exit 1 when unhealthy."""
    text = _read_card(root)
    _cnote(_card_path(root), script=_SCRIPT, sub="check", whole=True, root=root)
    chars = len(text)
    cap = _max_chars(root)
    placeholders = len(_PH_RE.findall(text))
    standing = 1 if re.search(r"^## Standing decisions", text, re.MULTILINE) else 0
    print("chars=%d cap=%d placeholders=%d standing=%d" % (chars, cap, placeholders, standing))
    if chars > cap or placeholders > 0 or standing:
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
    iv.add_argument("--rhythm", default=None)
    iv.add_argument("--defaults", action="store_true",
                    help="write the template defaults without prompting")

    sl = sub.add_parser("slice", help="print sections for a role")
    sl.add_argument("role", help="the role name (e.g. expert, router)")

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
