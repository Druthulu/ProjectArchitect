"""Project Architect 3.0 installer package (stdlib only, cold path).

Public:
    main(argv=None)     argument parsing + dispatch for ``pa_install.py``
    build_parser()      the ``argparse`` parser (tests introspect it)
    step(), note()      the shared ``SKIP``/``DONE``/``FAIL`` step printer
    is_tty(), ask()     the shared one-prompt-per-question rule
    same_bytes(a, b)    the byte compare every copy step verifies with

Submodules:
    root            ``--root``: per-machine install (R1 detect .. R5 manifest)
    project         ``--project``: per-repo install (P1 detect .. P10 close)
    detect          the layout classifier behind P1 (fresh/pa3/stock20/onex/overlay)
    settings_merge  the ``settings/*.snippet.json`` merge engine (pure, no I/O)
    split_by_heading  one file per heading with an index line (streamed, three schemes)
    natural_sort    ``sort -V``-like key with ``_``/``-`` as ``.``
    phaseend_normalize  rename plan: ``_`` → ``.`` between version tokens
    legacy_index    ``rows()`` / ``text()`` for the legacy PhaseEnd index
    migrate         the PA2 -> PA3 migration engine (Row, run)
    stock20         stock 2.0 layout mapping (TerrainDiffusion7DTD-shaped)
    howwework       P8: HOW_WE_WORK.md placeholder collection and fill

Nothing here imports ``sqlite3`` or touches the filesystem at import time: the
entry script adds its own directory to ``sys.path`` and calls :func:`main`.
"""

import sys

USAGE_EPILOG = """\
examples:
  pa_install.py --root                       install into ~/.claude (or $CLAUDE_CONFIG_DIR)
  pa_install.py --root --dry-run             print the plan, write nothing
  pa_install.py --root --config-dir ~/.claude-vantage --yes
  pa_install.py --root --python /usr/bin/python3       (WSL)
  pa_install.py --project Z:/git/Vantage     install PA3 into one repository
  pa_install.py --project . --dry-run        print the plan, write nothing
"""


# --------------------------------------------------------------------------- shared

def step(sid, title, status, reason):
    """One step line: ``P2  bootstrap          DONE  <one-line reason>``."""
    print("%-3s %-18s %-4s  %s" % (sid, title, status, reason))


def note(text):
    """An indented detail line under the current step."""
    print("      %s" % text)


def is_tty():
    """True when a question can actually be asked (stdin is a terminal)."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def ask(question, default, opts):
    """Prompt once; ``--yes`` and a non-TTY stdin both take the default."""
    if getattr(opts, "yes", False) or not is_tty():
        return default
    try:
        answer = input("      %s [%s]: " % (question, default))
    except (EOFError, KeyboardInterrupt, OSError):
        return default
    return answer.strip() or default


_SHELLS = frozenset(("powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "zsh.exe", "fish.exe", "nu.exe",
                     "wsl.exe"))


def owned_console(names):
    """True when a console's attached processes (image names) hold no shell but the one ``cmd.exe`` running
    install.cmd: the console was opened for this run, so it closes when the run ends (3.11 T6)."""
    names = [str(n).lower() for n in names or ()]
    return bool(names) and not any(n in _SHELLS for n in names) and names.count("cmd.exe") <= 1


def _console_process_names():
    """Image names of the processes attached to this console (Windows), [] when unknown."""
    try:
        import ctypes
        import os
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pids = (wintypes.DWORD * 64)()
        count = k32.GetConsoleProcessList(pids, 64)
        if count <= 0 or count > 64:
            return []
        names = []
        for pid in pids[:count]:
            handle = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                continue
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    names.append(os.path.basename(buf.value))
            finally:
                k32.CloseHandle(handle)
        return names
    except Exception:
        return []


def hold_if_double_clicked():
    """Keep a double-clicked console open so its result can be read (3.11 T6).

    install.cmd and setup-project.cmd set ``PA3_DOUBLECLICK=1`` when their own name is in a ``cmd /c``
    command line, which a double-click produces and so does a run from PowerShell or bash; the console's
    process list tells them apart (a shell is attached only in the second case).
    """
    import os

    if os.name != "nt" or os.environ.get("PA3_DOUBLECLICK") != "1" or not is_tty():
        return False
    if not owned_console(_console_process_names()):
        return False
    try:
        input("\nPress Enter to close this window.")
    except (EOFError, KeyboardInterrupt, OSError):
        pass
    return True


def same_bytes(path, data):
    """True when ``path`` already holds exactly ``data`` (bytes)."""
    try:
        with open(path, "rb") as fh:
            return fh.read() == data
    except OSError:
        return False


def build_parser():
    """The installer's command line (``--root`` today, ``--project`` in M2)."""
    import argparse

    p = argparse.ArgumentParser(
        prog="pa_install.py",
        description="Project Architect 3.0 installer.",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_argument_group("what to install")
    mode.add_argument("--root", action="store_true",
                      help="per-machine install: package copy, ledger, settings merge")
    mode.add_argument("--project", metavar="DIR", nargs="?", const="", default=None,
                      help="per-repo install: bootstrap, agents/skill/commands/tools, "
                           "CLAUDE.md, project settings (no DIR: asked at a terminal, else the current folder)")
    mode.add_argument("--ignore", metavar="REPO", default=None,
                      help="stop the session-start offer to set up REPO (the offer's own line names it)")

    p.add_argument("--config-dir", metavar="DIR", default=None,
                   help="Claude config dir (default: $CLAUDE_CONFIG_DIR, else ~/.claude)")
    p.add_argument("--pa3-dir", metavar="DIR", default=None,
                   help="where the package is installed (default: <home>/.claude/pa3)")
    p.add_argument("--python", metavar="EXE", default=None,
                   help="interpreter written into the hook commands "
                        "(default: the py -3 launcher on Windows, else sys.executable)")
    p.add_argument("--dry-run", action="store_true",
                   help="print every change and write nothing")
    p.add_argument("--yes", "-y", action="store_true",
                   help="accept the default answer to every prompt")
    p.add_argument("--renewal-day", metavar="EMAIL=N", action="append", default=None,
                   help="the day of the month EMAIL's plan renews (the Claude app shows it under "
                        "Settings > Billing); repeatable. Unset, PA3 asks when it first needs it")
    p.add_argument("--extra-root", metavar="DIR", action="append", default=None,
                   help="another machine's usage-ledger to union into reports (repeatable)")
    p.add_argument("--label", metavar="EMAIL=LABEL", action="append", default=None,
                   help="account label for config.json (repeatable)")
    p.add_argument("--source", metavar="URL|PATH", default=None,
                   help="git clone source for the package (default: install.source from config; "
                        "an existing clone keeps its own remote)")
    p.add_argument("--no-clone", dest="no_clone", action="store_true",
                   help="skip the clone step and use the package next to pa_install.py")
    p.add_argument("--allow-stale", dest="allow_stale", action="store_true",
                   help="install even when the source clone could not be updated (prints WARN)")
    p.add_argument("--no-statusline", action="store_true",
                   help="leave settings.json statusLine alone")
    p.add_argument("--hooks", choices=("on", "off"), default="on",
                   help="'off' writes no hook entries at all (default: on)")

    proj = p.add_argument_group("per-repo install (--project)")
    proj.add_argument("--name", metavar="NAME", default=None,
                      help="project name for CLAUDE.md/pa.json (default: the repo dir name)")
    proj.add_argument("--tagline", metavar="TEXT", default=None,
                      help="the one-line description under the CLAUDE.md title")
    proj.add_argument("--force", action="store_true",
                      help="overwrite project-edited copies of packaged files "
                           "(default: skip them and list them)")
    proj.add_argument("--no-commit", dest="no_commit", action="store_true",
                      help="write the files but do not create the install commit")
    proj.add_argument("--interphase", metavar="NAME", default=None,
                      help="override the interphase PhaseEnd label "
                           "(default: PA3 migration)")
    return p


def main(argv=None):
    """Entry point used by ``pa_install.py``; returns a process exit code."""
    parser = build_parser()
    opts = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    if opts.root and opts.project is not None:
        parser.error("--root and --project are separate runs; pick one")
    if opts.ignore:                                          # 3.11 T7
        from .. import setup_offer

        marker = setup_offer.ignore(opts.ignore)
        print("PA3 will not offer to set up %s again (delete %s to undo)." % (opts.ignore, marker))
        return 0
    if opts.project is not None:
        if opts.project == "":                               # 3.11 T7: no folder given
            import os

            opts.project = ask("repository folder to set up", os.getcwd(), opts)
        from . import project as _project

        rc = _project.install_project(opts)
        hold_if_double_clicked()
        return rc
    if not opts.root:
        parser.print_help()
        return 2

    from . import root as _root

    rc = _root.install_root(opts)
    hold_if_double_clicked()
    return rc
