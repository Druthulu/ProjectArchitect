"""Split a monolithic Markdown file into one file per heading (streaming).

Public:
    plan(path, level, dest, scheme, start=None, resplit=False) -> Plan
    execute(plan, env)

Schemes:
    rules     ``rules/<id>.md`` per heading; ``INDEX.rules.md`` grammar
    cookbook   ``cookbook/C<nnnn>.md``; ``INDEX.cookbook.md`` grammar
    ops       ``docs/ops/<slug>.md``; ``docs/ops/INDEX.md`` one-liner grammar

Nothing touches the filesystem at import time; stdlib only.
"""

import os
import re
from collections import namedtuple


Row = namedtuple("Row", "heading dest_rel index_line")

RESPLIT_THRESHOLD = 400

# ---------------------------------------------------------------------------  patterns

_HD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_RULE_ID_RE = re.compile(r"^([A-Z]\d+)\b")
_CB_FILE_RE = re.compile(r"^C(\d{4})\.md$")


# ---------------------------------------------------------------------------  helpers

class Plan:
    """Split plan produced by :func:`plan`, consumed by :func:`execute`."""

    __slots__ = ("src", "name", "dest", "level", "scheme", "rows",
                 "preamble_rel", "index_rel", "_markers")

    def __init__(self, src, name, dest, level, scheme, rows,
                 preamble_rel, index_rel, markers):
        self.src = src
        self.name = name
        self.dest = dest
        self.level = level
        self.scheme = scheme
        self.rows = list(rows)
        self.preamble_rel = preamble_rel
        self.index_rel = index_rel
        self._markers = markers          # [(heading_text, hashes_str)]


def _parse_heading(line):
    """Return ``(hashes, text)`` or *None*."""
    m = _HD_RE.match(line.rstrip("\n\r"))
    return (m.group(1), m.group(2).strip()) if m else None


def _extract_rule_id(text):
    """``P1``, ``H2``, ``R12`` from heading text, or *None*."""
    m = _RULE_ID_RE.match(text)
    return m.group(1) if m else None


def _slugify(text):
    """Lower-case, hyphen-separated slug for ops filenames."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "untitled"


def _highest_cb(dest):
    """Highest ``C<nnnn>`` number found in *dest*, or ``0``."""
    if not os.path.isdir(dest):
        return 0
    best = 0
    for name in os.listdir(dest):
        m = _CB_FILE_RE.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def _row_id(scheme, heading, v_counter, cb_num):
    """Return ``(file_id, v_counter, cb_num)`` for one heading."""
    if scheme == "rules":
        rid = _extract_rule_id(heading)
        if rid is None:
            rid = "V%d" % v_counter
            v_counter += 1
        return rid, v_counter, cb_num
    if scheme == "cookbook":
        cid = "C%04d" % cb_num
        cb_num += 1
        return cid, v_counter, cb_num
    # ops
    return _slugify(heading), v_counter, cb_num


def _index_line(scheme, fid, heading, section_num, line_count, filename):
    """Build one index line in the grammar of the scheme's ``INDEX.md``."""
    if scheme == "rules":
        return "<!-- %s | %s | | active | | | -->" % (fid, heading)
    if scheme == "cookbook":
        return "<!-- %s | %s | | | | origin: §%d %s -->" % (
            fid, heading, section_num, heading)
    # ops
    return "%s | %s | %d" % (filename, heading, line_count)


def _id_from_line(line, scheme):
    """Extract the unique identifier from an index line for dedup."""
    s = line.strip()
    if not s:
        return None
    if scheme in ("rules", "cookbook"):
        m = re.match(r"<!--\s*(\S+)\s*\|", s)
        return m.group(1) if m else None
    # ops: filename is the first field
    i = s.find("|")
    return s[:i].strip() if i >= 0 else None


# ---------------------------------------------------------------------------  plan

def plan(path, level, dest, scheme, start=None, resplit=False):
    """Build a split plan by streaming *path* for headings at *level*.

    *level* is ``"##"`` or ``"###"``.  *dest* is the destination directory
    relative to the repo root.  *scheme* is ``"rules"``, ``"cookbook"`` or
    ``"ops"``.  *start* overrides auto-detected numbering (cookbook C-number
    or rules V-counter).  *resplit* re-splits sections over 400 lines at the
    next heading level.

    Returns a :class:`Plan` whose ``.rows`` list has one :class:`Row` per
    output file.
    """
    if level not in ("##", "###"):
        raise ValueError("level must be '##' or '###'")
    if scheme not in ("rules", "cookbook", "ops"):
        raise ValueError("unknown scheme %r" % scheme)

    target = level
    sub_level = target + "#"
    name = os.path.splitext(os.path.basename(path))[0]

    # -- first pass: discover headings and line counts -----------------------
    sections = []          # [(heading, content_lines, [(sub_heading, sub_lines)])]
    preamble_count = 0
    cur = None             # [heading, lines, subs]
    sub_cur = None         # [heading, lines]
    in_preamble = True

    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            parsed = _parse_heading(raw)
            if parsed:
                hashes, text = parsed
                if hashes == target:
                    # close previous section
                    if cur is not None:
                        if sub_cur is not None:
                            cur[2].append(tuple(sub_cur))
                            sub_cur = None
                        sections.append(cur)
                    cur = [text, 0, []]
                    in_preamble = False
                    continue
                if resplit and hashes == sub_level and cur is not None:
                    if sub_cur is not None:
                        cur[2].append(tuple(sub_cur))
                    sub_cur = [text, 0]
                    cur[1] += 1          # heading line counted in parent total
                    continue
            # regular content line
            if in_preamble:
                preamble_count += 1
            elif cur is not None:
                cur[1] += 1
                if sub_cur is not None:
                    sub_cur[1] += 1

    if cur is not None:
        if sub_cur is not None:
            cur[2].append(tuple(sub_cur))
        sections.append(cur)

    # -- numbering state -----------------------------------------------------
    if scheme == "cookbook":
        cb_num = start if start is not None else (_highest_cb(dest) + 1)
    else:
        cb_num = 1
    v_counter = start if (start is not None and scheme == "rules") else 1

    # -- build rows ----------------------------------------------------------
    rows = []
    markers = []           # [(heading_text, hashes_str)] for execute()
    section_num = 0

    for heading, content_lines, subs in sections:
        section_num += 1
        do_resplit = (resplit
                      and content_lines > RESPLIT_THRESHOLD
                      and len(subs) > 0)

        if do_resplit:
            # parent intro (lines before the first sub-heading)
            intro_lines = content_lines - len(subs) - sum(s[1] for s in subs)
            fid, v_counter, cb_num = _row_id(scheme, heading, v_counter, cb_num)
            fn = fid + ".md"
            idx = _index_line(scheme, fid, heading, section_num,
                              intro_lines + 1, fn)          # +1 for heading line
            rows.append(Row(heading, "%s/%s" % (dest, fn), idx))
            markers.append((heading, target))
            # sub-sections
            for sub_text, sub_lines in subs:
                section_num += 1
                fid, v_counter, cb_num = _row_id(
                    scheme, sub_text, v_counter, cb_num)
                fn = fid + ".md"
                idx = _index_line(scheme, fid, sub_text, section_num,
                                  sub_lines + 1, fn)
                rows.append(Row(sub_text, "%s/%s" % (dest, fn), idx))
                markers.append((sub_text, sub_level))
        else:
            fid, v_counter, cb_num = _row_id(scheme, heading, v_counter, cb_num)
            fn = fid + ".md"
            idx = _index_line(scheme, fid, heading, section_num,
                              content_lines + 1, fn)
            rows.append(Row(heading, "%s/%s" % (dest, fn), idx))
            markers.append((heading, target))

    preamble_rel = ("%s/%s.preamble.md" % (dest, name)
                    if preamble_count > 0 else None)

    return Plan(src=path, name=name, dest=dest, level=level, scheme=scheme,
                rows=rows, preamble_rel=preamble_rel,
                index_rel="%s/INDEX.md" % dest, markers=markers)


# ---------------------------------------------------------------------------  execute

def execute(p, env):
    """Split the source file according to *p*, writing through ``place()``.

    Index lines are appended once; files that are byte-identical are skipped.
    In dry-run mode nothing is written and a summary is printed via ``note()``.
    """
    from .project import place
    from . import note

    markers = p._markers
    n_markers = len(markers)
    mi = 0                         # index of next expected marker
    buf = []                       # lines for the current section
    written = 0                    # section files processed (excl. preamble)
    in_preamble = True

    with open(p.src, "r", encoding="utf-8") as fh:
        for raw in fh:
            parsed = _parse_heading(raw)
            matched = False
            if parsed and mi < n_markers:
                hashes, text = parsed
                exp_text, exp_hashes = markers[mi]
                if hashes == exp_hashes and text == exp_text:
                    matched = True

            if matched:
                # flush the previous buffer
                if in_preamble:
                    if p.preamble_rel and buf:
                        place(env, p.preamble_rel, text="".join(buf))
                    in_preamble = False
                else:
                    place(env, p.rows[mi - 1].dest_rel, text="".join(buf))
                    written += 1
                buf = [raw]
                mi += 1
            else:
                buf.append(raw)

    # flush last section
    if not in_preamble and mi > 0 and buf:
        place(env, p.rows[mi - 1].dest_rel, text="".join(buf))
        written += 1
    elif in_preamble and p.preamble_rel and buf:
        place(env, p.preamble_rel, text="".join(buf))

    assert written == len(p.rows), \
        "file count %d != heading count %d" % (written, len(p.rows))

    # -- append index lines --------------------------------------------------
    idx_path = env.path(p.index_rel)
    existing = set()
    if os.path.isfile(idx_path):
        with open(idx_path, "r", encoding="utf-8") as fh:
            for line in fh:
                eid = _id_from_line(line, p.scheme)
                if eid:
                    existing.add(eid)

    new_lines = []
    for row in p.rows:
        eid = _id_from_line(row.index_line, p.scheme)
        if eid and eid not in existing:
            new_lines.append(row.index_line)

    if new_lines and not env.dry_run:
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        with open(idx_path, "a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")

    if env.dry_run:
        note("split %s -> %d files in %s (+%d index lines)" % (
            os.path.basename(p.src), len(p.rows), p.dest, len(new_lines)))
