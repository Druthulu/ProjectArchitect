"""outline.py — table of contents for source files.

CLI: outline.py <path> [<path> …] [--depth N] [--kind md|py|cs|js|ts]
Per file: a header line, the file's header block (4-space indent, up to 20
lines), then rows with line numbers and nesting: symbols with their intent
(``  # <first doc line>``) and ``start-end`` rows for comment blocks of 3+ lines.
Exit 0 always.
"""

import re
import os
import sys
import argparse

# ---------------------------------------------------------------------------
# Language regex tables
# ---------------------------------------------------------------------------

# md: heading lines
_MD_HEADING = re.compile(r'^(#{1,6})\s+(.*)')

# md: fenced block delimiters
_MD_FENCE = re.compile(r'^(`{3,}|~{3,})')

# py: class, def, async def
_PY_PATTERNS = [
    re.compile(r'^(\s*)(class\s+\w[^:]*?):\s*'),
    re.compile(r'^(\s*)(async\s+def\s+\w[^:]*?):\s*'),
    re.compile(r'^(\s*)(def\s+\w[^:]*?):\s*'),
]
# py: a def/class whose signature continues on the next lines
_PY_DEF_START = re.compile(r'^(\s*)(?:async\s+)?(?:def|class)\s+\w')
# py: docstring opener (optional r/b/u prefix)
_PY_DOC_OPEN = re.compile(r'^[rRuUbB]{0,2}("""|\x27{3})')
_PY_ENCODING = re.compile(r'^#.*coding[:=]')

# cs: namespace, class/struct/interface/enum/record, methods
_CS_NAMESPACE = re.compile(
    r'^\s*namespace\s+([\w.]+)')
_CS_TYPE = re.compile(
    r'^\s*(?:(?:public|private|protected|internal|static|sealed|abstract|partial|readonly|new)\s+)*'
    r'(class|struct|interface|enum|record)\s+(\w+)')
_CS_METHOD = re.compile(
    r'^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|new|partial|readonly)\s+)*'
    r'(?:[\w<>\[\],\s]+?\s+)?(\w+)\s*(\([^)]*\))')
# things that look like methods but are not
_CS_KEYWORDS = frozenset({
    'if', 'else', 'while', 'for', 'foreach', 'switch', 'catch', 'return',
    'new', 'using', 'throw', 'lock', 'class', 'struct', 'interface', 'enum',
    'record', 'namespace', 'try', 'finally', 'do', 'typeof', 'sizeof',
    'checked', 'unchecked', 'fixed', 'delegate', 'event', 'var', 'get', 'set',
})

# js: function, class, arrow const, export default, methods
_JS_FUNCTION = re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(\([^)]*\))')
_JS_CLASS = re.compile(r'^\s*(?:export\s+)?class\s+(\w+)')
_JS_ARROW = re.compile(r'^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>')
_JS_EXPORT_DEFAULT = re.compile(r'^\s*export\s+default\s+(.*)')
_JS_METHOD = re.compile(r'^\s+(?:async\s+)?(\w+)\s*(\([^)]*\))\s*\{')

# ts extras: interface, type, enum, namespace
_TS_INTERFACE = re.compile(r'^\s*(?:export\s+)?interface\s+(\w+)')
_TS_TYPE = re.compile(r'^\s*(?:export\s+)?type\s+(\w+)')
_TS_ENUM = re.compile(r'^\s*(?:export\s+)?(?:const\s+)?enum\s+(\w+)')
_TS_NAMESPACE = re.compile(r'^\s*(?:export\s+)?namespace\s+(\w+)')

# ---------------------------------------------------------------------------
# Extension → language mapping
# ---------------------------------------------------------------------------

_EXT_MAP = {
    '.md': 'md',
    '.py': 'py',
    '.cs': 'cs',
    '.js': 'js',
    '.ts': 'ts',
    '.tsx': 'ts',
    '.jsx': 'js',
    '.mjs': 'js',
    '.mts': 'ts',
}

# ---------------------------------------------------------------------------
# Outline functions
# ---------------------------------------------------------------------------


def _header(path, lines, chars):
    return "%s: %d lines, %d chars" % (path, lines, chars)


def _row(lineno, depth, text):
    return "%d:%s%s" % (lineno, "  " * depth, text)


def outline_md(lines, depth):
    """Outline a Markdown file. Fenced blocks are skipped."""
    rows = []
    in_fence = False
    fence_marker = None
    for i, line in enumerate(lines, 1):
        m = _MD_FENCE.match(line)
        if m:
            if not in_fence:
                in_fence = True
                fence_marker = m.group(1)[0]  # ` or ~
            elif line.strip().startswith(fence_marker):
                in_fence = False
                fence_marker = None
            continue
        if in_fence:
            continue
        m = _MD_HEADING.match(line)
        if m:
            level = len(m.group(1))
            if depth is not None and level > depth:
                continue
            rows.append(_row(i, level - 1, m.group(0)))
    return rows


def outline_py(lines, depth):
    """Outline a Python file. Nesting by indent."""
    rows = []
    for i, line in enumerate(lines, 1):
        if _PY_DEF_START.match(line) and not line.rstrip().endswith(':'):
            # multi-line signature: join up to the line ending with ':'
            j = i
            while j < len(lines) and j - i < 20 and not lines[j - 1].rstrip().endswith(':'):
                j += 1
            line = " ".join([lines[i - 1].rstrip()] + [l.strip() for l in lines[i:j]])
        for pat in _PY_PATTERNS:
            m = pat.match(line)
            if m:
                indent = len(m.group(1))
                nest = indent // 4
                rows.append(_row(i, nest, m.group(2)))
                break
    return rows


def outline_cs(lines, depth):
    """Outline a C# file. Nesting by brace depth."""
    rows = []
    brace_depth = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # namespace
        m = _CS_NAMESPACE.match(line)
        if m:
            rows.append(_row(i, brace_depth, "namespace %s" % m.group(1)))
            brace_depth += stripped.count('{') - stripped.count('}')
            continue
        # type (class, struct, interface, enum, record)
        m = _CS_TYPE.match(line)
        if m:
            rows.append(_row(i, brace_depth, "%s %s" % (m.group(1), m.group(2))))
            brace_depth += stripped.count('{') - stripped.count('}')
            continue
        # method (only at member level, brace_depth >= 2)
        if brace_depth >= 2:
            m = _CS_METHOD.match(line)
            if m and m.group(1) not in _CS_KEYWORDS:
                rows.append(_row(i, brace_depth, "%s%s" % (m.group(1), m.group(2))))
                brace_depth += stripped.count('{') - stripped.count('}')
                continue
        # track braces
        brace_depth += stripped.count('{') - stripped.count('}')
        if brace_depth < 0:
            brace_depth = 0
    return rows


def outline_js(lines, depth, is_ts=False):
    """Outline a JS/TS file. Nesting by brace depth."""
    rows = []
    brace_depth = 0
    in_class = False
    class_depth = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        matched = False

        # ts-specific: interface, type, enum, namespace
        if is_ts:
            m = _TS_INTERFACE.match(line)
            if m:
                rows.append(_row(i, brace_depth, "interface %s" % m.group(1)))
                matched = True
            if not matched:
                m = _TS_TYPE.match(line)
                if m:
                    rows.append(_row(i, brace_depth, "type %s" % m.group(1)))
                    matched = True
            if not matched:
                m = _TS_ENUM.match(line)
                if m:
                    rows.append(_row(i, brace_depth, "enum %s" % m.group(1)))
                    matched = True
            if not matched:
                m = _TS_NAMESPACE.match(line)
                if m:
                    rows.append(_row(i, brace_depth, "namespace %s" % m.group(1)))
                    matched = True

        # class
        if not matched:
            m = _JS_CLASS.match(line)
            if m:
                rows.append(_row(i, brace_depth, "class %s" % m.group(1)))
                in_class = True
                class_depth = brace_depth
                matched = True

        # function
        if not matched:
            m = _JS_FUNCTION.match(line)
            if m:
                rows.append(_row(i, brace_depth, "function %s%s" % (m.group(1), m.group(2))))
                matched = True

        # arrow const
        if not matched:
            m = _JS_ARROW.match(line)
            if m:
                rows.append(_row(i, brace_depth, "const %s = (%s) =>" % (m.group(1), m.group(2))))
                matched = True

        # export default
        if not matched:
            m = _JS_EXPORT_DEFAULT.match(line)
            if m:
                rows.append(_row(i, brace_depth, "export default %s" % m.group(1).strip()))
                matched = True

        # method inside class
        if not matched and in_class and brace_depth > class_depth:
            m = _JS_METHOD.match(line)
            if m:
                rows.append(_row(i, brace_depth, "%s%s" % (m.group(1), m.group(2))))
                matched = True

        # track braces
        brace_depth += stripped.count('{') - stripped.count('}')
        if brace_depth < 0:
            brace_depth = 0
        if in_class and brace_depth <= class_depth:
            in_class = False
    return rows


# ---------------------------------------------------------------------------
# Commentary: header block, intent lines, comment blocks
# ---------------------------------------------------------------------------

_HEADER_MAX = 20
_TRIPLE = ('"""', "'''")


def _block_end(lines, start, closer):
    """1-based line of the first line at or after ``start`` containing ``closer``."""
    for k in range(start, len(lines) + 1):
        if closer in lines[k - 1]:
            return k
    return len(lines)


def _run(lines, start, prefix):
    """(start, end) of consecutive lines from ``start`` whose stripped text starts with ``prefix``."""
    end = start
    while end < len(lines) and lines[end].strip().startswith(prefix):
        end += 1
    return (start, end)


def _depth_of(line):
    return (len(line) - len(line.lstrip())) // 4


def header_block(lines, lang):
    """(start, end) 1-based of the file's leading comment or docstring, or None."""
    first = 1
    if lang == 'py':
        while first <= min(2, len(lines)) and (
                lines[first - 1].startswith('#!') or _PY_ENCODING.match(lines[first - 1])):
            first += 1
    if first > len(lines):
        return None
    head = lines[first - 1].strip()
    if lang == 'py':
        m = _PY_DOC_OPEN.match(head)
        if m:
            q = m.group(1)
            if head.count(q) >= 2:
                return (first, first)
            return (first, _block_end(lines, first + 1, q))
        if head.startswith('#'):
            return _run(lines, first, '#')
    elif lang in ('cs', 'js', 'ts'):
        if head.startswith('//'):
            return _run(lines, first, '//')
        if head.startswith('/*'):
            return (first, _block_end(lines, first, '*/'))
    elif lang == 'md' and head.startswith('<!--'):
        return (first, _block_end(lines, first, '-->'))
    return None


def comment_blocks(lines, lang):
    """(start, end) of every run of 3+ comment lines or a 3+ line block comment."""
    blocks = []
    n = len(lines)
    i = 1
    in_doc = None  # py: the open triple quote, so '#' inside strings is skipped
    while i <= n:
        st = lines[i - 1].strip()
        if lang == 'py':
            if in_doc:
                if st.count(in_doc) % 2 == 1:
                    in_doc = None
                i += 1
                continue
            if st.startswith('#'):
                s, e = _run(lines, i, '#')
                if e - s >= 2:
                    blocks.append((s, e))
                i = e + 1
                continue
            for q in _TRIPLE:
                if st.count(q) % 2 == 1:
                    in_doc = q
                    break
        elif lang in ('cs', 'js', 'ts'):
            if st.startswith('//'):
                s, e = _run(lines, i, '//')
                if e - s >= 2:
                    blocks.append((s, e))
                i = e + 1
                continue
            if st.startswith('/*'):
                e = _block_end(lines, i, '*/')
                if e - i >= 2:
                    blocks.append((i, e))
                i = e + 1
                continue
        elif lang == 'md' and st.startswith('<!--'):
            e = _block_end(lines, i, '-->')
            if e - i >= 2:
                blocks.append((i, e))
            i = e + 1
            continue
        i += 1
    return blocks


def intent_of(lines, lineno, lang):
    """The symbol's intent: docstring first line (py) or the // line above (cs/js/ts)."""
    if lang == 'py':
        j = lineno  # 0-based index of the line after the signature's end
        while j < len(lines) and j - lineno < 20 and not lines[j - 1].rstrip().endswith(':'):
            j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            return None
        st = lines[j].strip()
        m = _PY_DOC_OPEN.match(st)
        if not m:
            return None
        q = m.group(1)
        text = st[m.end():]
        if text.endswith(q):
            text = text[:-len(q)]
        text = text.strip()
        if not text and j + 1 < len(lines):
            text = lines[j + 1].strip()
            if text.endswith(q):
                text = text[:-len(q)].strip()
        return text or None
    if lang in ('cs', 'js', 'ts') and lineno >= 2:
        st = lines[lineno - 2].strip()
        if st.startswith('//'):
            return st.lstrip('/').strip() or None
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_OUTLINERS = {
    'md': lambda lines, depth: outline_md(lines, depth),
    'py': lambda lines, depth: outline_py(lines, depth),
    'cs': lambda lines, depth: outline_cs(lines, depth),
    'js': lambda lines, depth: outline_js(lines, depth, is_ts=False),
    'ts': lambda lines, depth: outline_js(lines, depth, is_ts=True),
}


def outline_file(path, depth=None, kind=None):
    """Return the outline text for a single file."""
    return "\n".join(outline_lines(path, depth, kind))


def outline_lines(path, depth=None, kind=None):
    """The outline of a single file as a list of lines (``outline_file``'s lines)."""
    if not os.path.isfile(path):
        return ["%s: not found" % path]

    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    file_lines = content.splitlines()
    num_lines = len(file_lines)
    num_chars = len(content)

    header = _header(path, num_lines, num_chars)

    lang = kind
    if lang is None:
        ext = os.path.splitext(path)[1].lower()
        lang = _EXT_MAP.get(ext)

    if lang is None:
        ext = os.path.splitext(path)[1]
        return [header, "no outline for %s" % ext]

    outliner = _OUTLINERS.get(lang)
    if outliner is None:
        ext = os.path.splitext(path)[1]
        return [header, "no outline for %s" % ext]

    rows = outliner(file_lines, depth)
    head = header_block(file_lines, lang)
    parts = [header]
    if head:
        parts += ["    " + l for l in file_lines[head[0] - 1:head[1]][:_HEADER_MAX]]
    keyed = []
    for r in rows:
        lineno = int(r.split(":", 1)[0])
        intent = intent_of(file_lines, lineno, lang)
        keyed.append((lineno, r + ("  # %s" % intent if intent else "")))
    for s, e in comment_blocks(file_lines, lang):
        if head and s <= head[1] and e >= head[0]:
            continue  # the header block is printed above, not repeated
        first = file_lines[s - 1]
        keyed.append((s, "%d-%d:%s%s" % (s, e, "  " * _depth_of(first), first.strip())))
    keyed.sort(key=lambda t: t[0])
    parts += [r for _, r in keyed]
    return parts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Table of contents for source files.")
    parser.add_argument("paths", nargs="+", metavar="path", help="Files to outline.")
    parser.add_argument("--depth", type=int, default=None,
                        help="Max heading level (md only).")
    parser.add_argument("--kind", choices=["md", "py", "cs", "js", "ts"], default=None,
                        help="Force language.")
    args = parser.parse_args()
    # fix-3: outlined text carries U+2212 and other non-cp1252 characters; a Windows
    # console or pipe defaults to cp1252 and raised UnicodeEncodeError (3.10 T30).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    results = []
    for path in args.paths:
        results.append(outline_file(path, depth=args.depth, kind=args.kind))
    print("\n".join(results))
    for path, text in zip(args.paths, results):
        _credit_note(path, len(text) + 1)


def _credit_note(path, emitted, root=None):
    """Note kind ``outline`` for the credit (the ranged Read that follows).

    ``cap_chars``: chars of the first 2,000 lines, the whole-file Read the
    counterfactual would have made.  Never raises.
    """
    try:
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        cap = sum(len(l) for l in content.splitlines(True)[:2000])
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, script="outline.py", kind="outline",
                     file_chars=len(content), cap_chars=cap, emitted=emitted,
                     root=root)
    except Exception:
        pass


if __name__ == "__main__":
    main()
