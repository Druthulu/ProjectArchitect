"""pa.guard + pa.hooks.pre_tool_use: the read-only allowlist and PHASE_PLAN rules (T11).

The command table below is the fixture design report A section C.3 asks for: every
row is a command the developer or an agent might actually run during a
``/discuss`` discussion, with the verdict the guard must reach.  Unknown means
deny, so the DENY list is deliberately full of near-misses (``git checkout --``
next to ``git diff``, ``curl -o f`` next to ``curl -s``).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import config, guard  # noqa: E402
from pa import hooks  # noqa: E402
from pa.hooks import pre_tool_use  # noqa: E402

ALLOW = [
    # the ten the milestone spells out
    "ls", "cat x", "grep -rn foo src", "git status", "git log --oneline -5", "git diff",
    "rg pattern", "python tools/analysis/session_decompose.py x", "curl -s url",
    "cd x && git status",
    # more read-only shapes
    "ls -la", "head -50 f", "tail -n 20 f", "wc -l f", "find . -name *.py", "stat f",
    "which python", "echo hi", "pwd", "sort f", "diff a b", "jq . f", "git show HEAD",
    "git blame f", "git rev-parse --show-toplevel", "git ls-files", "git branch",
    "git describe", "git remote -v", "git stash list", "sed -n 1,5p f",
    "cat f | grep x", "ls > /dev/null", "grep -c x f 2>/dev/null", "dotnet --info",
    "wsl -l", "python --version", "curl -s -o - url", "tree", "du -sh .", "df -h",
    "ps", "uname -a", "whoami", "basename /a/b", "realpath .", "date", "env",
    "nl f", "cut -d, -f1 f", "uniq f", "od -c f", "strings f", "tac f",
    "python tools/plan_show.py --tasks",
    # PowerShell, read-only
    "Get-ChildItem", "Get-Content x", "Select-String -Pattern foo -Path x",
    "Measure-Object", "Test-Path x", "Write-Output hi", "ConvertFrom-Json",
    "Sort-Object", "Where-Object", "Format-Table", "Resolve-Path x",
    "Compare-Object a b", "Group-Object", "Get-Process",
]

DENY = [
    # the thirteen the milestone spells out
    "echo a > b", "sed -i s/a/b/ f", "tee f", 'python -c "print(1)"', "git add .",
    "git commit -m x", "rm f", "mv a b", "Set-Content x", "Out-File", "Start-Process",
    "curl -o f url", "git checkout -- f",
    # more mutations and near-misses
    "cat f > g", "cat f >> g", "mkdir d", "touch f", "git push", "git pull",
    "npm install", "chmod +x f", "sed --in-place s/a/b/ f", "perl -i -pe s/a/b/ f",
    "git branch -d x", "git tag v1", "wget -O f url", "dd if=a of=b",
    "find . -delete", "ls; rm f", "ls && rm -rf x", "echo $(rm f)", "git stash",
    "python setup.py install", "python -m pip install x", "bash script.sh",
    "./configure", "git reset --hard", "git clean -fd",
    # PowerShell mutations
    "Remove-Item x", "New-Item x", "Copy-Item a b", "Move-Item a b", "Rename-Item a b",
    "Invoke-WebRequest -OutFile f url", "Invoke-Expression x", "Stop-Process -Name x",
    "Add-Content f x", "Clear-Content f", "Tee-Object f", "Export-Csv f",
    "Restart-Service x", "Set-Location x > out.txt",
]


class ReadOnlyTableTest(unittest.TestCase):
    """T11: >= 60 commands, allow/deny exactly as tabulated."""

    def test_table_is_large_enough(self):
        self.assertGreaterEqual(len(ALLOW) + len(DENY), 60)

    def test_allowed_commands(self):
        for cmd in ALLOW:
            with self.subTest(cmd=cmd):
                self.assertTrue(guard.is_read_only(cmd), "should be read-only: %r" % cmd)

    def test_denied_commands(self):
        for cmd in DENY:
            with self.subTest(cmd=cmd):
                self.assertFalse(guard.is_read_only(cmd), "should be denied: %r" % cmd)

    def test_empty_command_is_harmless(self):
        self.assertTrue(guard.is_read_only(""))
        self.assertTrue(guard.is_read_only(None))

    def test_python_script_allowlist_comes_from_config(self):
        cfg = config.defaults()
        self.assertTrue(guard.is_read_only("python tools/analysis/token_buckets.py x", cfg=cfg))
        self.assertFalse(guard.is_read_only("python tools/task_log.py finish T1", cfg=cfg))
        cfg["discussion_allow_scripts"] = ["tools/task_log.py"]
        self.assertTrue(guard.is_read_only("python tools/task_log.py finish T1", cfg=cfg))

    def test_absolute_script_path_still_matches(self):
        self.assertTrue(guard.is_read_only(
            "python Z:/repo/tools/analysis/session_decompose.py x", cfg=config.defaults()))

    def test_discussion_default_allows_subcommand_entries(self):
        """3.9.7 T9: `<glob> <a|b>` entries gate the first positional arg."""
        cfg = config.defaults()
        py = "C:/Users/u/AppData/Local/Programs/Python/Python314/python.exe"
        for cmd in ("%s tools/discussion.py off" % py, "%s tools/card.py slice expert" % py,
                    "%s tools/plan_edit.py show --task T1" % py):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.decide("Bash", {"command": cmd}, discussion=True, cfg=cfg))
        for cmd in ("%s tools/plan_edit.py set-status T1 done --by x" % py,
                    "%s tools/card.py interview" % py):
            with self.subTest(cmd=cmd):
                self.assertTrue(guard.decide("Bash", {"command": cmd}, discussion=True, cfg=cfg))
        self.assertTrue(guard.decide("Edit", {"file_path": "x.py", "old_string": "a",
                                              "new_string": "b"}, discussion=True, cfg=cfg))

    def test_powershell_detection(self):
        self.assertTrue(guard.looks_like_powershell("Get-ChildItem -Path x"))
        self.assertFalse(guard.looks_like_powershell("grep -rn foo src"))

    def test_is_mutating_bash(self):
        for cmd in ("sed -i s/a/b/ PHASE_PLAN.md", "echo x > PHASE_PLAN.md",
                    "tee PHASE_PLAN.md", "mv a PHASE_PLAN.md", "rm PHASE_PLAN.md"):
            self.assertTrue(guard.is_mutating_bash(cmd), cmd)
        for cmd in ("grep -n T3 PHASE_PLAN.md", "cat PHASE_PLAN.md"):
            self.assertFalse(guard.is_mutating_bash(cmd), cmd)


class ProjectGuardTest(unittest.TestCase):
    """PreToolUse end to end in a throwaway project."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-guard-")
        self.ledger = os.path.join(self.dir, "ledger")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "guard-test"}')
        os.environ["PA_LEDGER_DIR"] = self.ledger
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()

    def tearDown(self):
        os.environ.pop("PA_LEDGER_DIR", None)
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call(self, tool, tool_input):
        return pre_tool_use.run({"session_id": "s1", "cwd": self.root, "tool_name": tool,
                                 "tool_input": tool_input}, self.cfg)

    def _discussion(self, on=True):
        flag = os.path.join(self.root, ".run", "DISCUSSION")
        if on:
            with open(flag, "w", encoding="utf-8") as fh:
                fh.write("on\n")
        elif os.path.exists(flag):
            os.remove(flag)

    # ---- PHASE_PLAN (always, discussion or not)

    def test_edit_phase_plan_always_denied(self):
        out = self._call("Edit", {"file_path": os.path.join(self.root, "phase-ends",
                                                            "current", "PHASE_PLAN.md")})
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertIn("plan_edit.py", hso["permissionDecisionReason"])

    def test_write_and_multiedit_phase_plan_denied(self):
        self.assertIsNotNone(self._call("Write", {"file_path": "a/PHASE_PLAN.md"}))
        self.assertIsNotNone(self._call("MultiEdit", {"file_path": "a/PHASE_PLAN.md"}))
        self.assertIsNotNone(self._call("NotebookEdit", {"notebook_path": "a/PHASE_PLAN.md"}))

    def test_bash_sed_on_phase_plan_denied(self):
        out = self._call("Bash", {"command": "sed -i s/next/done/ phase-ends/current/PHASE_PLAN.md"})
        self.assertIsNotNone(out)
        self.assertIn("frozen", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_plan_edit_py_is_the_exception(self):
        self.assertIsNone(self._call(
            "Bash", {"command": "python tools/plan_edit.py set-status T3 done"}))
        self.assertIsNone(self._call(
            "Bash", {"command": "python tools/plan_edit.py note T3 'x' >> PHASE_PLAN.md"}))

    def test_only_mutations_onto_the_plan_are_denied(self):
        """3.1 T15: the mutation must target PHASE_PLAN.md; naming the file is not writing it."""
        plan = "phase-ends/current/PHASE_PLAN.md"
        for cmd in ("cp .run/PHASE_PLAN.draft.md " + plan,        # the Gen 2 slip: starts with cp
                    "mv .run/draft.md " + plan,
                    "rm " + plan,
                    "rm -f " + plan,
                    "sed -i s/next/done/ " + plan,
                    "echo x > " + plan,
                    "echo x >>" + plan,
                    "cat .run/draft.md | tee " + plan,
                    "python tools/status.py show && cp a.md " + plan,
                    "Set-Content -Path " + plan + " -Value x"):
            self.assertIsNotNone(self._call("Bash", {"command": cmd}), cmd)
        for cmd in ("cat > phase-ends/current/research/R1.md <<'EOF'\n"
                    "see PHASE_PLAN.md, never edit it; rm nothing\nEOF",
                    "grep -n T3 " + plan + " > .run/out.txt",
                    "cp " + plan + " .run/backup.md",
                    "rm .run/x.md && grep T1 " + plan,
                    "python tools/plan_edit.py set-status T3 done",
                    "cat " + plan + " | head -20",
                    "python - <<'EOF'\nprint('PHASE_PLAN.md')\nEOF"):
            self.assertIsNone(self._call("Bash", {"command": cmd}), cmd)

    def _call_as(self, agent_type, tool, tool_input):
        return pre_tool_use.run({"session_id": "s1", "cwd": self.root, "tool_name": tool,
                                 "tool_input": tool_input, "agent_type": agent_type,
                                 "agent_id": "a1"}, self.cfg)

    def test_retriever_code_bash_no_longer_restricted(self):
        """3.8 T2: retriever entries removed from AGENT_BASH_ALLOW."""
        for cmd in ("grep -rn seams .", "echo hi"):
            self.assertIsNone(self._call_as("retriever-code", "Bash", {"command": cmd}), cmd)
        self.assertIsNone(self._call_as("retriever-code", "Read", {"file_path": "a.py"}))
        self.assertIsNone(self._call_as("expert-fable", "Bash", {"command": "grep -rn seams ."}))
        self.assertIsNone(self._call_as(None, "Bash", {"command": "grep -rn seams ."}))

    def test_reading_phase_plan_is_fine(self):
        self.assertIsNone(self._call("Bash", {"command": "grep -n '^- T' PHASE_PLAN.md"}))
        self.assertIsNone(self._call("Read", {"file_path": "PHASE_PLAN.md"}))

    def test_other_files_are_not_protected(self):
        self.assertIsNone(self._call("Edit", {"file_path": os.path.join(self.root, "a.py")}))

    # ---- discussion mode

    def test_discussion_denies_edits_allows_grep_denies_commit(self):
        self._discussion(True)
        denied = self._call("Edit", {"file_path": os.path.join(self.root, "a.py")})
        self.assertIsNotNone(denied)
        self.assertIn("discussion mode",
                      denied["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self._call("Bash", {"command": "grep -rn TODO src"}))
        self.assertIsNotNone(self._call("Bash", {"command": "git commit -m x"}))
        self.assertIsNotNone(self._call("PowerShell", {"command": "Set-Content a.py x"}))
        self.assertIsNone(self._call("PowerShell", {"command": "Get-Content a.py"}))

    def test_discussion_off_allows_edits_again(self):
        self._discussion(True)
        self.assertIsNotNone(self._call("Edit", {"file_path": "a.py"}))
        self._discussion(False)
        self.assertIsNone(self._call("Edit", {"file_path": "a.py"}))
        self.assertIsNone(self._call("Bash", {"command": "git commit -m x"}))

    def test_denial_is_spooled_not_written_to_sqlite(self):
        self._discussion(True)
        self._call("Bash", {"command": "rm -rf build"})
        spool = os.path.join(self.ledger, "spool", "s1.jsonl")
        self.assertTrue(os.path.exists(spool))
        with open(spool, encoding="utf-8") as fh:
            self.assertIn('"deny"', fh.read())
        self.assertFalse(os.path.exists(os.path.join(self.ledger, "ledger.sqlite")))

    # ---- the governance gate

    def test_untracked_project_is_never_guarded(self):
        outside = os.path.join(self.dir, "elsewhere")
        os.makedirs(outside)
        hooks._PROJECT_ROOTS.clear()
        out = pre_tool_use.run({"session_id": "s1", "cwd": outside, "tool_name": "Edit",
                                "tool_input": {"file_path": "PHASE_PLAN.md"}}, self.cfg)
        self.assertIsNone(out)


class RoleGuardTest(unittest.TestCase):
    """T5: spilled-read, whole-plan-role, and tool-source-role guard rules."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-roleguard-")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "guard-test"}')
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()

    def tearDown(self):
        os.environ.pop("PA_LEDGER_DIR", None)
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call_as(self, agent_type, tool, tool_input):
        return pre_tool_use.run({"session_id": "s1", "cwd": self.root, "tool_name": tool,
                                 "tool_input": tool_input, "agent_type": agent_type,
                                 "agent_id": "a1"}, self.cfg)

    # ---- spilled read (any role, guard.spilled_read)

    def test_spilled_read_denied_for_every_role(self):
        """A Read of tool-results/<id>.txt without offset/limit is denied for any role."""
        path = "C:/Users/you/.claude/projects/test/sess1/tool-results/abc123.txt"
        for agent_type in ("expert-fable", "coder-opus46", "review", "critic", "retriever-code"):
            with self.subTest(agent_type=agent_type):
                out = self._call_as(agent_type, "Read", {"file_path": path})
                self.assertIsNotNone(out, "spilled read should be denied for %s" % agent_type)
                self.assertIn("grepped or tailed",
                              out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_spilled_read_allowed_with_limit(self):
        """A Read with limit= is allowed (grep/tail reads a portion)."""
        path = "C:/Users/you/.claude/projects/test/sess1/tool-results/abc123.txt"
        out = self._call_as("expert-fable", "Read", {"file_path": path, "limit": 40})
        self.assertIsNone(out)

    def test_spilled_read_allowed_with_offset(self):
        path = "C:/Users/you/.claude/projects/test/sess1/tool-results/abc123.txt"
        out = self._call_as("expert-fable", "Read", {"file_path": path, "offset": 100})
        self.assertIsNone(out)

    # ---- whole-plan role (guard.whole_plan_roles: review, critic)

    def test_whole_plan_read_denied_for_review_and_critic(self):
        """A Read of PHASE_PLAN.md without offset/limit is denied for review and critic."""
        for agent_type in ("review", "critic"):
            with self.subTest(agent_type=agent_type):
                out = self._call_as(agent_type, "Read",
                                    {"file_path": "/proj/phase-ends/current/PHASE_PLAN.md"})
                self.assertIsNotNone(out, "whole-plan read denied for %s" % agent_type)
                self.assertIn("plan_edit.py show",
                              out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_whole_plan_unscoped_show_denied_for_review(self):
        """A plan_edit.py show without --task or --section is denied for review."""
        out = self._call_as("review", "Bash",
                            {"command": "python tools/plan_edit.py show"})
        self.assertIsNotNone(out)
        self.assertIn("plan_edit.py show",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_whole_plan_allowed_for_expert(self):
        """An expert is not in whole_plan_roles, so whole-plan reads are allowed."""
        out = self._call_as("expert-fable", "Read",
                            {"file_path": "/proj/phase-ends/current/PHASE_PLAN.md"})
        self.assertIsNone(out)

    def test_whole_plan_scoped_show_allowed_for_review(self):
        """A plan_edit.py show --task T1 is allowed for review."""
        out = self._call_as("review", "Bash",
                            {"command": "python tools/plan_edit.py show --task T1"})
        self.assertIsNone(out)

    # ---- tool-source role (guard.tool_source_roles: expert)

    def test_tool_source_read_denied_for_expert_outside_pa(self):
        """An expert reading tools/*.py outside PA is denied."""
        out = self._call_as("expert-fable", "Read",
                            {"file_path": "/proj/tools/plan_edit.py"})
        self.assertIsNotNone(out, "tool-source read denied for expert outside PA")
        self.assertIn("--help",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_tool_source_read_allowed_inside_pa(self):
        """When the project IS ProjectArchitect, tool-source reads are allowed."""
        pa_root = os.path.join(self.dir, "ProjectArchitect")
        os.makedirs(os.path.join(pa_root, ".claude"))
        os.makedirs(os.path.join(pa_root, ".run"))
        with open(os.path.join(pa_root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "PA"}')
        hooks._PROJECT_ROOTS.clear()
        out = pre_tool_use.run({"session_id": "s2", "cwd": pa_root, "tool_name": "Read",
                                "tool_input": {"file_path": os.path.join(pa_root, "tools", "plan_edit.py")},
                                "agent_type": "expert-fable", "agent_id": "a1"}, self.cfg)
        self.assertIsNone(out)

    def test_tool_source_read_allowed_for_coder(self):
        """A coder is not in tool_source_roles, so tool-source reads are allowed."""
        out = self._call_as("coder-opus46", "Read",
                            {"file_path": "/proj/tools/plan_edit.py"})
        self.assertIsNone(out)

    def test_tool_source_bash_cat_denied_for_expert(self):
        """A cat of a tool source file is denied for experts outside PA."""
        out = self._call_as("expert-fable", "Bash",
                            {"command": "cat tools/plan_edit.py"})
        self.assertIsNotNone(out)
        self.assertIn("--help",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_tool_source_bash_head_denied_for_expert(self):
        """A head of .claude/** is denied for experts outside PA."""
        out = self._call_as("expert-fable", "Bash",
                            {"command": "head -20 .claude/settings.json"})
        self.assertIsNotNone(out)
        self.assertIn("--help",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    # ---- whole-read guard (guard.whole_read_chars, every role)

    def _make_file(self, name, size):
        """Create a temp file of exactly *size* bytes and return its path."""
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        return path

    def _make_py(self, name, n_defs):
        """A Python file over the threshold: a docstring header, then *n_defs* documented defs."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        pad = "    x = 1  # " + "p" * 60
        per = max(1, threshold // (n_defs * (len(pad) + 1)) + 1)
        body = ['"""Big module header."""', ""]
        for i in range(n_defs):
            body += ["def f%d(a, b):" % i, '    """Intent of f%d."""' % i]
            body += [pad] * per
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(body) + "\n")
        self.assertGreater(os.path.getsize(path), threshold)
        return path

    def test_whole_read_denied_for_all_roles(self):
        """A Read without offset/limit of a file one byte over the threshold is denied."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("big.py", threshold + 1)
        for agent_type in ("expert-fable", "coder-opus46",
                           "review", "critic", "router"):
            with self.subTest(agent_type=agent_type):
                out = self._call_as(agent_type, "Read", {"file_path": path})
                self.assertIsNotNone(out, "whole-read should be denied for %s" % agent_type)
                reason = out["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("whole_read_chars", reason)
                self.assertIn(str(threshold), reason)
                self.assertEqual(reason.splitlines()[-1], guard.WHOLE_READ_TAIL)

    def test_whole_read_serves_outline_for_coder_and_retriever(self):
        """Coder and retriever get the same served outline: header, symbol rows, instruction."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_py("served.py", 20)
        reasons = []
        for role in ("coder", "retriever"):
            with self.subTest(role=role):
                reason = guard.whole_read_deny({"file_path": path}, role, self.cfg, self.root)
                lines = reason.splitlines()
                self.assertTrue(lines[0].startswith("%s is " % path))
                self.assertTrue(lines[0].endswith("over guard.whole_read_chars (%d):" % threshold))
                self.assertTrue(lines[1].startswith("%s: " % path))           # outline header line
                self.assertEqual(lines[2], '    """Big module header."""')    # header block
                sym = [l for l in lines if l.split(":", 1)[0].isdigit() and "def f" in l]
                self.assertGreaterEqual(len(sym), 10)
                self.assertIn("def f0(a, b)  # Intent of f0.", "\n".join(sym))
                self.assertEqual(lines[-1], guard.WHOLE_READ_TAIL)
                self.assertNotIn("Grep", reason)
                reasons.append(reason)
        self.assertEqual(reasons[0], reasons[1])

    def test_whole_read_under_cap_is_none(self):
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("small.py", threshold - 1)
        self.assertIsNone(guard.whole_read_deny({"file_path": path}, "coder", self.cfg, self.root))

    def test_whole_read_outline_capped_at_60(self):
        """A >60-row outline: 60 outline lines, then the more-rows line, then the instruction."""
        path = self._make_py("many.py", 80)
        reason = guard.whole_read_deny({"file_path": path}, "coder", self.cfg, self.root)
        lines = reason.splitlines()
        self.assertEqual(len(lines), 1 + 60 + 2)
        # outline: header line + header block (1) + 80 rows = 82 -> 22 more
        self.assertEqual(lines[-2], "… 22 more rows: outline.py %s" % path)
        self.assertEqual(lines[-1], guard.WHOLE_READ_TAIL)

    def test_whole_read_writes_outline_note(self):
        """The served outline leaves a credit note of kind outline under <root>/.run/credit."""
        import json
        path = self._make_py("noted.py", 12)
        reason = guard.whole_read_deny({"file_path": path}, "coder", self.cfg, self.root)
        cdir = os.path.join(self.root, ".run", "credit")
        notes = []
        for n in os.listdir(cdir):
            with open(os.path.join(cdir, n), encoding="utf-8") as fh:
                notes.append(json.load(fh))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["kind"], "outline")
        self.assertEqual(notes[0]["emitted_chars"], len(reason) + 1)
        self.assertEqual(notes[0]["file_chars"], os.path.getsize(path))

    def test_whole_read_denied_for_main_session(self):
        """The main session (no agent_type) is also denied."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("big_main.py", threshold + 1)
        out = self._call_as(None, "Read", {"file_path": path})
        self.assertIsNotNone(out, "whole-read should be denied for the main session")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("whole_read_chars", reason)

    def test_whole_read_allowed_with_limit(self):
        """A Read with limit= is allowed even for a large file."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("big_limit.py", threshold + 1)
        out = self._call_as("expert-fable", "Read", {"file_path": path, "limit": 40})
        self.assertIsNone(out)

    def test_whole_read_allowed_with_offset(self):
        """A Read with offset= is allowed even for a large file."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("big_offset.py", threshold + 1)
        out = self._call_as("expert-fable", "Read", {"file_path": path, "offset": 100})
        self.assertIsNone(out)

    def test_whole_read_allowed_at_threshold(self):
        """A file at exactly the threshold is allowed."""
        threshold = self.cfg["guard"]["whole_read_chars"]
        path = self._make_file("exact.py", threshold)
        out = self._call_as("expert-fable", "Read", {"file_path": path})
        self.assertIsNone(out)

    def test_whole_read_allowed_for_binary(self):
        """A 1 MB .png is allowed (binary extensions are exempt)."""
        path = self._make_file("image.png", 1_000_000)
        out = self._call_as("expert-fable", "Read", {"file_path": path})
        self.assertIsNone(out)

    # ---- shell reads (fix-11: bash_read_deny, the shell twin of the Read guards)

    def _bash(self, cmd, agent_type="coder-opus55", tool="Bash"):
        out = self._call_as(agent_type, tool, {"command": cmd})
        return out and out["hookSpecificOutput"]["permissionDecisionReason"]

    def test_shell_whole_read_of_big_file_denied(self):
        threshold = self.cfg["guard"]["whole_read_chars"]
        with open(os.path.join(self.root, "big.md"), "w", encoding="utf-8") as fh:
            fh.write("# Big\n" + "x" * threshold)
        with open(os.path.join(self.root, "small.md"), "w", encoding="utf-8") as fh:
            fh.write("# Small\n")
        for cmd in ("cat big.md", "sed p big.md", "head big.md", "tail big.md",
                    "cd x && cat big.md", "cat -n big.md"):
            with self.subTest(cmd=cmd):
                reason = self._bash(cmd)
                self.assertIsNotNone(reason, cmd)
                self.assertIn("over guard.whole_read_chars (%d)" % threshold, reason)
        self.assertIsNotNone(self._bash("Get-Content big.md", tool="PowerShell"))
        for cmd in ("cat big.md | head -40", "cat big.md | head -n 40", "cat big.md | grep x",
                    "sed -n '1,40p' big.md", "head -n 40 big.md", "head -40 big.md",
                    "tail -n 20 big.md", "cat small.md", "grep -n x big.md", "wc -l big.md",
                    "cat missing.md", "cat > big.md <<'EOF'\nhi\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self._bash(cmd), cmd)

    def test_shell_read_of_spill_denied(self):
        path = "C:/Users/you/.claude/projects/test/sess1/tool-results/abc123.txt"
        for cmd in ("cat " + path, "cat tool-results/abc123.txt", "sed p " + path):
            with self.subTest(cmd=cmd):
                self.assertIn("grepped or tailed", self._bash(cmd) or "")
        for cmd in ("grep x tool-results/abc123.txt", "tail -n 40 " + path,
                    "cat tool-results/abc123.txt | grep x"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self._bash(cmd), cmd)
        self.cfg["guard"]["spilled_read"] = False
        self.assertIsNone(self._bash("cat tool-results/abc123.txt"))

    # ---- AGENT_BASH_ALLOW: no retriever entries

    def test_agent_bash_allow_no_retriever(self):
        """AGENT_BASH_ALLOW has no key starting with 'retriever-'."""
        for key in guard.AGENT_BASH_ALLOW:
            self.assertFalse(key.startswith("retriever-"),
                             "AGENT_BASH_ALLOW should not have retriever entries: %s" % key)


class RereadDenyTest(unittest.TestCase):
    """fix-17: a second whole Read of an unchanged file is denied; escape after three."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-reread-")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "reread-test"}')
        self.path = os.path.join(self.root, "notes.md")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("small file\n")
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()

    def tearDown(self):
        os.environ.pop("PA_LEDGER_DIR", None)
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call(self, tool, tool_input, agent_id="a1"):
        return pre_tool_use.run({"session_id": "s1", "cwd": self.root, "tool_name": tool,
                                 "tool_input": tool_input, "agent_type": "coder-opus55",
                                 "agent_id": agent_id}, self.cfg)

    def _read(self, agent_id="a1", **extra):
        return self._call("Read", dict({"file_path": self.path}, **extra), agent_id)

    def _reads(self, key="a1"):
        with open(os.path.join(self.root, ".run", "guard", "reads-%s.json" % key),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def test_second_whole_read_denied(self):
        self.assertIsNone(self._read())
        out = self._read()
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("notes.md is unchanged and already in this context (read at ", reason)
        self.assertIn("Read a range (offset/limit)", reason)

    def test_fourth_denial_escapes(self):
        self.assertIsNone(self._read())
        for _ in range(3):
            self.assertIsNotNone(self._read())
        self.assertIsNone(self._read())
        self.assertEqual(list(self._reads().values())[0]["denials"], 0)
        self.assertIsNotNone(self._read())

    def test_changed_file_allowed_and_rerecorded(self):
        self.assertIsNone(self._read())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("more\n")
        st = os.stat(self.path)
        os.utime(self.path, (st.st_atime, st.st_mtime + 5))
        self.assertIsNone(self._read())
        rec = list(self._reads().values())[0]
        self.assertEqual(rec["size"], os.stat(self.path).st_size)
        self.assertEqual(rec["denials"], 0)
        self.assertIsNotNone(self._read())

    def test_ranged_read_passes_unrecorded(self):
        self.assertIsNone(self._read(limit=5))
        self.assertIsNone(self._read(offset=1))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".run", "guard", "reads-a1.json")))
        self.assertIsNone(self._read())

    def test_edit_drops_path(self):
        self.assertIsNone(self._read())
        self.assertIsNone(self._call("Edit", {"file_path": self.path, "old_string": "a",
                                              "new_string": "b"}))
        self.assertEqual(self._reads(), {})
        self.assertIsNone(self._read())

    def test_run_ids_have_own_sets(self):
        self.assertIsNone(self._read("a1"))
        self.assertIsNone(self._read("a2"))
        self.assertIsNotNone(self._read("a1"))

    def test_disabled_by_config(self):
        self.cfg["guard"]["reread_deny"] = False
        self.assertIsNone(self._read())
        self.assertIsNone(self._read())

    def test_default_on(self):
        self.assertIs(config.defaults()["guard"]["reread_deny"], True)


if __name__ == "__main__":
    unittest.main()
