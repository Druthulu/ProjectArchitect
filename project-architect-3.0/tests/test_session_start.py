"""session_start: unstop, phase tag, install offer, and update check."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config, db, paths  # noqa: E402


def _setup_ledger(tmpdir):
    """Create a ledger dir with a db, point PA_LEDGER_DIR at it, return the dir."""
    ledger = os.path.join(tmpdir, "ledger")
    os.makedirs(ledger, exist_ok=True)
    os.environ["PA_LEDGER_DIR"] = ledger
    conn = db.connect(paths.db_path())
    db.init_schema(conn)
    return ledger, conn


def _setup_project(tmpdir):
    """Create a minimal governed project tree and return the root."""
    root = os.path.join(tmpdir, "proj")
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    os.makedirs(os.path.join(root, ".run"), exist_ok=True)
    with open(os.path.join(root, ".claude", "pa.json"), "w") as fh:
        json.dump({"project": "test"}, fh)
    return root


def _write_meta(sub_dir, run_id, doc):
    """Write an agent-<id>.meta.json file and return its path."""
    os.makedirs(sub_dir, exist_ok=True)
    path = os.path.join(sub_dir, "agent-%s.meta.json" % run_id)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, separators=(",", ":")))
    return path


def _write_status(root, run_id):
    """Write a .run/status.json with the run in flight."""
    st = {"task": "T3", "run_id": run_id, "expert_agent_type": "expert-fable",
          "kind": "task", "attempt": 1, "killed_at": "2026-09-20T22:01:39Z"}
    with open(os.path.join(root, ".run", "status.json"), "w") as fh:
        json.dump(st, fh)


class UnstopTest(unittest.TestCase):
    """The stoppedByUser clearing path in session_start._unstop_runs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-unstop-")
        self.addCleanup(self._cleanup)
        self.ledger_dir, self.conn = _setup_ledger(self.tmpdir)
        self.root = _setup_project(self.tmpdir)

        # transcript layout: session_dir strips .jsonl; subagents_dir appends /subagents
        self.sid = "aaaa1111-0000-4000-8000-00000000005b"
        self.base = os.path.join(self.tmpdir, "transcripts")
        self.transcript_path = os.path.join(self.base, self.sid + ".jsonl")
        os.makedirs(os.path.dirname(self.transcript_path), exist_ok=True)
        with open(self.transcript_path, "w") as fh:
            fh.write("{}\n")
        self.sub_dir = os.path.join(self.base, self.sid, "subagents")

    def _cleanup(self):
        os.environ.pop("PA_LEDGER_DIR", None)
        # clear the hooks module's project root cache
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _inp(self, version="2.1.278"):
        return {
            "session_id": self.sid,
            "source": "resume",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
            "version": version,
        }

    def _call_unstop(self, inp):
        from pa.hooks.session_start import _unstop_runs
        _unstop_runs(inp, self.sid)

    def test_clears_stopped_with_backup_and_marker(self):
        """The meta file is cleared, a backup exists, and the marker is set."""
        run_id = "r1"
        _write_status(self.root, run_id)
        meta = _write_meta(self.sub_dir, run_id,
                           {"agentType": "expert-fable", "stoppedByUser": True})
        self._call_unstop(self._inp())
        # meta file should have stoppedByUser: false and the marker
        with open(meta, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertFalse(doc["stoppedByUser"])
        self.assertIn("pa3_cleared_stoppedByUser", doc)
        # backup exists
        baks = [f for f in os.listdir(self.sub_dir) if f.endswith(run_id + ".meta.json") is False
                and "bak" in f]
        self.assertTrue(baks, "no backup file found")
        # events row
        row = self.conn.execute(
            "SELECT kind, detail_json FROM events WHERE kind='unstop'").fetchone()
        self.assertIsNotNone(row)
        detail = json.loads(row["detail_json"])
        self.assertEqual(detail["run_id"], run_id)

    def test_untouched_when_version_unknown(self):
        """No clearing when the CLI version is not in UNSTOP_KNOWN_VERSIONS."""
        run_id = "r2"
        _write_status(self.root, run_id)
        meta = _write_meta(self.sub_dir, run_id,
                           {"agentType": "expert-fable", "stoppedByUser": True})
        self._call_unstop(self._inp(version="9.9.9"))
        with open(meta, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertTrue(doc["stoppedByUser"])

    def test_untouched_when_unstop_false(self):
        """No clearing when resume.unstop is False."""
        run_id = "r3"
        _write_status(self.root, run_id)
        meta = _write_meta(self.sub_dir, run_id,
                           {"agentType": "expert-fable", "stoppedByUser": True})
        # write a config with unstop: false
        cfg = config.defaults()
        cfg["resume"]["unstop"] = False
        config.save(cfg, os.path.join(self.ledger_dir, "config.json"))
        config._CACHE.clear()
        self._call_unstop(self._inp())
        with open(meta, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertTrue(doc["stoppedByUser"])

    def test_untouched_when_stopped_absent(self):
        """No clearing when stoppedByUser is not in the meta file."""
        run_id = "r4"
        _write_status(self.root, run_id)
        meta = _write_meta(self.sub_dir, run_id,
                           {"agentType": "expert-fable"})
        self._call_unstop(self._inp())
        with open(meta, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertNotIn("pa3_cleared_stoppedByUser", doc)


class PhaseTagTest(unittest.TestCase):
    """session_start tags sessions.phase from the open plan."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-phase-")
        self.addCleanup(self._cleanup)
        self.ledger_dir, self.conn = _setup_ledger(self.tmpdir)
        self.root = _setup_project(self.tmpdir)
        self.sid = "pppp1111-2222-4333-8444-555555555555"
        self.base = os.path.join(self.tmpdir, "transcripts")
        self.transcript_path = os.path.join(self.base, self.sid + ".jsonl")
        os.makedirs(os.path.dirname(self.transcript_path), exist_ok=True)
        with open(self.transcript_path, "w") as fh:
            fh.write("{}\n")
        from pa import accounts, notify
        self._old_auth = accounts.auth_status
        accounts.auth_status = lambda *a, **k: {"email": "test@test.com", "source": "auth"}
        notify.set_spawner(lambda cmd, env: None)

    def _cleanup(self):
        from pa import accounts, notify
        accounts.auth_status = self._old_auth
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_plan(self, phase_id):
        pe = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(pe, exist_ok=True)
        with open(os.path.join(pe, "PHASE_PLAN.md"), "w") as fh:
            fh.write("# Phase %s -- test\nApproved: 2026-09-22\n" % phase_id)

    def test_session_phase_set_from_plan(self):
        self._write_plan("3.6")
        from pa.hooks import session_start
        inp = {"session_id": self.sid, "source": "startup", "cwd": self.root,
               "transcript_path": self.transcript_path}
        session_start.run(inp, config.defaults())
        row = self.conn.execute(
            "SELECT phase FROM sessions WHERE session_id=?", (self.sid,)).fetchone()
        self.assertEqual(row["phase"], "3.6")

    def test_no_plan_no_phase(self):
        from pa.hooks import session_start
        inp = {"session_id": self.sid, "source": "startup", "cwd": self.root,
               "transcript_path": self.transcript_path}
        session_start.run(inp, config.defaults())
        row = self.conn.execute(
            "SELECT phase FROM sessions WHERE session_id=?", (self.sid,)).fetchone()
        self.assertIsNone(row["phase"])

    def _phase_with_owner(self, owner):
        """T28: run session_start with status.json naming *owner* as router_session."""
        self._write_plan("3.6")
        st = {"phase": "3.6"}
        if owner:
            st["router_session"] = owner
        with open(os.path.join(self.root, ".run", "status.json"), "w") as fh:
            json.dump(st, fh)
        from pa.hooks import session_start
        inp = {"session_id": self.sid, "source": "startup", "cwd": self.root,
               "transcript_path": self.transcript_path}
        session_start.run(inp, config.defaults())
        return self.conn.execute(
            "SELECT phase FROM sessions WHERE session_id=?", (self.sid,)).fetchone()["phase"]

    def test_other_owner_tags_beside(self):
        self.assertEqual(self._phase_with_owner("other-sid"), "beside 3.6")

    def test_own_sid_owner_tags_phase(self):
        self.assertEqual(self._phase_with_owner(self.sid), "3.6")

    def test_no_owner_tags_phase(self):
        self.assertEqual(self._phase_with_owner(None), "3.6")

    def test_tier_stamped_from_credentials(self):
        cfg_dir = os.path.join(self.tmpdir, "claude")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, ".credentials.json"), "w") as fh:
            json.dump({"claudeAiOauth": {"accessToken": "t", "subscriptionType": "max",
                                         "rateLimitTier": "default_claude_max_20x"}}, fh)
        from pa.hooks import session_start
        inp = {"session_id": self.sid, "source": "startup", "cwd": self.root,
               "transcript_path": self.transcript_path}
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg_dir}):
            session_start.run(inp, config.defaults())
        row = self.conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                                ("test@test.com",)).fetchone()
        self.assertEqual(tuple(row), ("max20", "credentials"))


_CREDS = {"max20": {"rateLimitTier": "default_claude_max_20x"},
          "max5": {"rateLimitTier": "default_claude_max_5x"},
          "pro": {"subscriptionType": "pro"}}


class LadderReconcileTest(unittest.TestCase):
    """3.10.6 T3: _reconcile_ladder re-renders the managed agents for the account's tier."""

    EMAIL = "ladder@test.com"

    def setUp(self):
        from pa.install import ladder, managed
        self.ladder, self.managed = ladder, managed
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-ladder-")
        self.addCleanup(self._cleanup)
        self.ledger_dir, self.conn = _setup_ledger(self.tmpdir)
        self.root = _setup_project(self.tmpdir)
        self.cfg_dir = os.path.join(self.tmpdir, "claude")
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg_dir
        pkg_agents = os.path.join(self.cfg_dir, "pa3-src", "project-architect-3.0", "agents")
        os.makedirs(pkg_agents)
        for agent in ladder.MANAGED:
            shutil.copy(os.path.join(ROOT, "agents", agent + ".md"), pkg_agents)
        self.agents = os.path.join(self.root, ".claude", "agents")
        os.makedirs(self.agents)

    def _cleanup(self):
        self.conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        if self._old_cfg is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg
        else:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _pkg(self, agent):
        with open(os.path.join(ROOT, "agents", agent + ".md"), "r", encoding="utf-8",
                  newline="") as fh:
            return fh.read().replace("\r\n", "\n")

    def _install(self, preset, pa_preset=None):
        """The six agents rendered at ``preset`` with an unedited record; pa.json ``preset``."""
        files = {}
        for agent in self.ladder.MANAGED:
            rel = ".claude/agents/%s.md" % agent
            text = self.ladder.render(agent, self._pkg(agent), preset)
            if text is None:
                files[rel] = {"absent": "tier", "base": None, "sha": None, "user": None}
                continue
            with open(os.path.join(self.root, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            files[rel] = {"base": self.managed.base_of(text),
                          "sha": self.managed.sha(text.encode("utf-8")), "user": None}
        self._write_record(files)
        pa = {"project": "test", "tier": "max5", "warmer": "off"}
        if pa_preset:
            pa["preset"] = pa_preset
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(json.dumps(pa, indent=2) + "\n")

    def _write_record(self, files):
        with open(os.path.join(self.root, ".claude", "pa3-managed.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"format": 1, "git": "abc", "files": files}, indent=2))

    def _record(self):
        with open(os.path.join(self.root, ".claude", "pa3-managed.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _pa(self):
        with open(os.path.join(self.root, ".claude", "pa.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _stamp(self, tier):
        from pa import accounts
        accounts.stamp_tier(self.conn, self.EMAIL, tier, source="override")
        self.conn.commit()

    def _reconcile(self, inp=None):
        from pa.hooks import session_start as ss
        ss._reconcile_ladder(inp or {"session_id": "s", "cwd": self.root}, self.conn, self.EMAIL)

    def _snapshot(self):
        out = {}
        for d, names in ((self.agents, os.listdir(self.agents)),
                         (os.path.join(self.root, ".claude"), ["pa.json", "pa3-managed.json"])):
            for n in names:
                p = os.path.join(d, n)
                with open(p, "rb") as fh:
                    out[p] = (os.stat(p).st_mtime_ns, fh.read())
        return out

    def test_in_step_writes_nothing(self):
        self._install("max20", pa_preset="max20")
        self._stamp("max20")
        before = self._snapshot()
        self._reconcile()
        self.assertEqual(self._snapshot(), before)

    def test_downgrade_removes_expert_fable_and_rerenders(self):
        self._install("max20", pa_preset="max20")
        self._stamp("max5")
        self._reconcile()
        self.assertFalse(os.path.exists(os.path.join(self.agents, "expert-fable.md")))
        self.assertEqual(self.ladder.mismatches(self.agents, "max5"), [])
        files = self._record()["files"]
        self.assertEqual(files[".claude/agents/expert-fable.md"],
                         {"absent": "tier", "base": None, "sha": None, "user": None})
        with open(os.path.join(self.agents, "plain.md"), "rb") as fh:
            self.assertEqual(files[".claude/agents/plain.md"]["sha"], self.managed.sha(fh.read()))
        self.assertEqual(self._record()["git"], "abc")
        pa = self._pa()
        self.assertEqual((pa["preset"], pa["tier"], pa["warmer"]), ("max5", "max5", "off"))

    def test_edited_absent_agent_kept(self):
        self._install("max20", pa_preset="max20")
        path = os.path.join(self.agents, "expert-fable.md")
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write("\nmy own note\n")
        self._stamp("pro")
        from pa.hooks import session_start as ss
        with mock.patch.object(ss.log, "log") as lg:
            self._reconcile()
        self.assertTrue(os.path.exists(path))
        self.assertIn("ladder_reconcile_kept", [c.args[0] for c in lg.call_args_list])
        self.assertIsNone(self._record()["files"][".claude/agents/expert-fable.md"].get("absent"))

    def test_upgrade_installs_expert_fable_from_package(self):
        self._install("max5", pa_preset="max5")
        self._stamp("max20")
        self._reconcile()
        with open(os.path.join(self.agents, "expert-fable.md"), "rb") as fh:
            data = fh.read()
        self.assertEqual(data, self._pkg("expert-fable").encode("utf-8"))
        ent = self._record()["files"][".claude/agents/expert-fable.md"]
        self.assertEqual(ent["sha"], self.managed.sha(data))
        self.assertIsNone(ent["user"])
        self.assertNotIn("absent", ent)
        self.assertEqual(self.ladder.mismatches(self.agents, "max20"), [])

    def test_user_edited_body_kept_model_rewritten(self):
        self._install("max20", pa_preset="max20")
        rel = ".claude/agents/critic.md"
        path = os.path.join(self.root, rel)
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write("\nuser line\n")
        rec = self._record()["files"]
        old = dict(rec[rel])
        rec[rel]["user"] = {"sha": "x", "mark": None, "state": "kept", "at": "t"}
        self._write_record(rec)
        self._stamp("pro")
        self._reconcile()
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        self.assertIn("model: claude-opus-5-5[1m]\n", text)
        self.assertTrue(text.endswith("\nuser line\n"))
        ent = self._record()["files"][rel]
        self.assertEqual((ent["base"], ent["sha"]), (old["base"], old["sha"]))
        self.assertEqual(ent["user"]["sha"], self.managed.sha(text.encode("utf-8")))

    def test_override_and_subagent(self):
        self._install("max20", pa_preset="max20")
        self._stamp("max20")
        pa = self._pa()
        pa["ladder"] = {"critic": {"effort": "high"}}
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump(pa, fh)
        before = self._snapshot()
        self._reconcile({"session_id": "s", "cwd": self.root, "agent_id": "a1"})
        self.assertEqual(self._snapshot(), before)
        self._reconcile()
        with open(os.path.join(self.agents, "critic.md"), encoding="utf-8") as fh:
            self.assertIn("effort: high\n", fh.read())

    def test_probe_payload_writes_nothing(self):
        """T8.c1: doctor's latency probe (``pa_probe``) never mutates the project."""
        self._install("max20", pa_preset="max20")
        self._stamp("max5")
        before = self._snapshot()
        self._reconcile({"session_id": "s", "cwd": self.root, "pa_probe": True})
        self.assertEqual(self._snapshot(), before)
        self.assertTrue(os.path.exists(os.path.join(self.agents, "expert-fable.md")))

    def _timed_run(self, between=None):
        """run() on the governed project (tier from the temp credentials: max20), warmed once;
        ``(seconds of the second run, snapshot before it)``. Network and spawns are patched."""
        with open(os.path.join(self.cfg_dir, ".credentials.json"), "w") as fh:
            json.dump({"claudeAiOauth": dict(_CREDS["max20"], accessToken="t")}, fh)
        from pa.hooks import session_start as ss
        inp = {"session_id": "tttt1111-2222-4333-8444-555555555555", "source": "startup",
               "cwd": self.root}
        with mock.patch.object(ss.accounts, "auth_status",
                               return_value={"email": self.EMAIL, "source": "auth"}), \
                mock.patch.object(ss, "_offer_install", return_value=None), \
                mock.patch.object(ss, "_update_check", return_value=None), \
                mock.patch.object(ss, "_record_behind", return_value=None), \
                mock.patch("pa.warmer.start_if_needed"):
            ss.run(inp, config.defaults())          # warm the imports once
            if between:
                between()
            before = self._snapshot()
            t0 = time.perf_counter()
            ss.run(inp, config.defaults())
            return time.perf_counter() - t0, before

    def test_timed_no_mismatch(self):
        self._install("max20", pa_preset="max20")
        dt, before = self._timed_run()
        self.assertLess(dt, 0.6)
        self.assertEqual(self._snapshot(), before)

    def test_timed_one_mismatch(self):
        self._install("max20", pa_preset="max20")
        path = os.path.join(self.agents, "plain.md")

        def lower_effort():
            with open(path, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text.replace("effort: high", "effort: low"))
        dt, _ = self._timed_run(lower_effort)
        self.assertLess(dt, 0.6)
        self.assertEqual(self.ladder.mismatches(self.agents, "max20"), [])


_GIT = shutil.which("git")


def _git_run(args, **kw):
    """Run a git command with defaults for test repos."""
    kw.setdefault("capture_output", True)
    kw.setdefault("timeout", 10)
    kw.setdefault("check", True)
    return subprocess.run(["git"] + args, **kw)


def _init_repo(path):
    """Create a git repo with one commit at *path*."""
    os.makedirs(path, exist_ok=True)
    _git_run(["init", "-q", path])
    _git_run(["-C", path, "config", "user.email", "test@test.com"])
    _git_run(["-C", path, "config", "user.name", "Test"])
    _git_run(["-C", path, "commit", "--allow-empty", "-m", "init"])


@unittest.skipUnless(_GIT, "git not on PATH")
class OfferInstallTest(unittest.TestCase):
    """_offer_install: the session-start install offer for ungoverned repos."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-offer-")
        self.addCleanup(self._cleanup)
        self.ledger = os.path.join(self.tmpdir, "ledger")
        os.makedirs(self.ledger)
        os.environ["PA_LEDGER_DIR"] = self.ledger

        # install dir with VERSION
        self.pa3_dir = os.path.join(self.tmpdir, "pa3")
        os.makedirs(self.pa3_dir)
        self._version_path = os.path.join(self.pa3_dir, "VERSION")
        self.clone_dir = os.path.join(self.tmpdir, "clone")
        os.makedirs(os.path.join(self.clone_dir, "project-architect-3.0"))
        with open(self._version_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("version: 3.11\ngit: abc1234\nsource: %s\n"
                     % self.clone_dir.replace("\\", "/"))

        # ungoverned git repo
        self.repo = os.path.join(self.tmpdir, "myrepo")
        _init_repo(self.repo)

        # monkeypatch paths
        import pa.paths as _paths
        self._orig_install_dir = _paths.install_dir
        self._orig_clone_dir = _paths.package_clone_dir
        _paths.install_dir = lambda: self.pa3_dir
        _paths.package_clone_dir = lambda config_dir=None: self.clone_dir

    def _cleanup(self):
        import pa.paths as _paths
        _paths.install_dir = self._orig_install_dir
        _paths.package_clone_dir = self._orig_clone_dir
        os.environ.pop("PA_LEDGER_DIR", None)
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _inp(self, source="startup", cwd=None, agent_id=None):
        d = {"session_id": "test-offer", "source": source,
             "cwd": cwd or self.repo}
        if agent_id:
            d["agent_id"] = agent_id
        return d

    def test_offer_for_ungoverned_work_tree(self):
        from pa.hooks.session_start import _offer_install
        result = _offer_install(self._inp(), config.defaults())
        self.assertIsNotNone(result)
        self.assertIn("install pa3", result)
        self.assertIn("pa_install.py --project", result)
        self.assertIn(self.repo.replace("\\", "/"), result)

    def test_user_line_at_every_start(self):
        """3.11 T7.1: the user sees the copy-ready `!` line at every startup until the repository is set up
        or silenced (T7 showed it once per repository); the model keeps its offer."""
        from pa import fsutil, setup_offer
        from pa.hooks.session_start import _offer_model_line, _offer_parts, _offer_user_line
        parts = _offer_parts(self._inp(), config.defaults())
        line = _offer_user_line(parts)
        repo = self.repo.replace("\\", "/")
        self.assertIn('pa_install.py" --project "%s" --yes' % repo, line)
        self.assertIn('pa_install.py" --ignore "%s"' % repo, line)
        self.assertIn("  ! ", line)
        self.assertIn("/exit", line)
        self.assertEqual(_offer_user_line(_offer_parts(self._inp(), config.defaults())), line)
        # a marker T7 left for a repository it showed once no longer silences it
        fsutil.atomic_write_json(setup_offer._path(self.repo), {"repo": repo, "shown": 1.0})
        self.assertEqual(_offer_user_line(_offer_parts(self._inp(), config.defaults())), line)
        self.assertIsNotNone(_offer_model_line(parts))

    def test_setup_offer_off_silences_the_user_line_and_the_offer(self):
        """3.11 T7.1: ``setup_offer: off`` in the machine config silences both, in every repository."""
        from pa.hooks.session_start import _offer_model_line, _offer_parts, _offer_user_line
        for off in ("off", False):
            cfg = config.defaults()
            cfg["setup_offer"] = off
            parts = _offer_parts(self._inp(), cfg)
            self.assertIsNone(_offer_user_line(parts))
            self.assertIsNone(_offer_model_line(parts))

    def test_ignore_silences_the_user_line_and_the_offer(self):
        from pa import setup_offer
        from pa.hooks.session_start import _offer_model_line, _offer_parts, _offer_user_line
        setup_offer.ignore(self.repo)
        parts = _offer_parts(self._inp(), config.defaults())
        self.assertIsNone(_offer_user_line(parts))
        self.assertIsNone(_offer_model_line(parts))

    def test_nothing_for_governed_cwd(self):
        os.makedirs(os.path.join(self.repo, ".claude"), exist_ok=True)
        with open(os.path.join(self.repo, ".claude", "pa.json"), "w") as fh:
            fh.write("{}")
        from pa.hooks.session_start import _offer_install
        result = _offer_install(self._inp(), config.defaults())
        self.assertIsNone(result)

    def test_nothing_for_subagent(self):
        from pa.hooks.session_start import _offer_install
        result = _offer_install(self._inp(agent_id="sub-001"), config.defaults())
        self.assertIsNone(result)

    def test_nothing_for_resume(self):
        from pa.hooks.session_start import _offer_install
        result = _offer_install(self._inp(source="resume"), config.defaults())
        self.assertIsNone(result)

    def test_nothing_for_refused_target(self):
        from pa.hooks.session_start import _offer_install
        from pa.install import project as _proj
        orig = _proj.git_checks
        _proj.git_checks = lambda repo, opts: "refusing to govern %s" % repo
        try:
            result = _offer_install(self._inp(), config.defaults())
        finally:
            _proj.git_checks = orig
        self.assertIsNone(result)

    def test_nothing_for_missing_version(self):
        os.remove(self._version_path)
        from pa.hooks.session_start import _offer_install
        result = _offer_install(self._inp(), config.defaults())
        self.assertIsNone(result)


@unittest.skipUnless(_GIT, "git not on PATH")
class UpdateCheckTest(unittest.TestCase):
    """_update_check: the session-start update check with update.json."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-update-")
        self.addCleanup(self._cleanup)
        self.ledger = os.path.join(self.tmpdir, "ledger")
        os.makedirs(self.ledger)
        os.environ["PA_LEDGER_DIR"] = self.ledger

        # bare origin, clone, and a second clone to push an extra commit
        self.origin = os.path.join(self.tmpdir, "origin.git")
        _git_run(["init", "--bare", "-q", self.origin])
        self.clone = os.path.join(self.tmpdir, "clone")
        _git_run(["clone", "-q", self.origin, self.clone])
        _git_run(["-C", self.clone, "config", "user.email", "test@test.com"])
        _git_run(["-C", self.clone, "config", "user.name", "Test"])
        os.makedirs(os.path.join(self.clone, "project-architect-3.0"))
        with open(os.path.join(self.clone, "project-architect-3.0", "f.txt"), "w") as fh:
            fh.write("v1\n")
        _git_run(["-C", self.clone, "add", "-A"])
        _git_run(["-C", self.clone, "commit", "-q", "-m", "initial"])
        _git_run(["-C", self.clone, "push", "-q", "origin", "HEAD"])

        # second clone to push extra commits (tests call _push) so origin is ahead
        self.pusher = os.path.join(self.tmpdir, "pusher")
        _git_run(["clone", "-q", self.origin, self.pusher])
        _git_run(["-C", self.pusher, "config", "user.email", "test@test.com"])
        _git_run(["-C", self.pusher, "config", "user.name", "Test"])

        # install dir with VERSION matching the clone's current HEAD and package tree
        self.pa3_dir = os.path.join(self.tmpdir, "pa3")
        os.makedirs(self.pa3_dir)
        r = _git_run(["-C", self.clone, "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True)
        self.clone_head_before = r.stdout.strip()
        r = _git_run(["-C", self.clone, "rev-parse", "HEAD:project-architect-3.0"],
                     capture_output=True, text=True)
        self.tree = r.stdout.strip()
        self._write_version(tree=True)

        # monkeypatch paths
        import pa.paths as _paths
        self._orig_install_dir = _paths.install_dir
        self._orig_clone_dir = _paths.package_clone_dir
        _paths.install_dir = lambda: self.pa3_dir
        _paths.package_clone_dir = lambda config_dir=None: self.clone

    def _cleanup(self):
        import pa.paths as _paths
        _paths.install_dir = self._orig_install_dir
        _paths.package_clone_dir = self._orig_clone_dir
        os.environ.pop("PA_LEDGER_DIR", None)
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_version(self, tree, version="3.11"):
        with open(os.path.join(self.pa3_dir, "VERSION"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("version: %s\ngit: %s\n%ssource: %s\n"
                     % (version, self.clone_head_before, ("tree: %s\n" % self.tree) if tree else "",
                        self.clone.replace("\\", "/")))

    def _push(self, rel, msg):
        """Commit a change to *rel* in the pusher and push it to origin."""
        path = os.path.join(self.pusher, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(msg + "\n")
        _git_run(["-C", self.pusher, "add", "-A"])
        _git_run(["-C", self.pusher, "commit", "-q", "-m", msg])
        _git_run(["-C", self.pusher, "push", "-q", "origin", "HEAD"])

    def _inp(self, cwd=None):
        return {"session_id": "test-update", "source": "startup",
                "cwd": cwd or os.path.join(self.tmpdir, "ledger")}

    def _governed(self):
        root = os.path.join(self.tmpdir, "proj")
        os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
        with open(os.path.join(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        return root

    def _assert_steps(self, result, root):
        self.assertIsNotNone(result)
        if root:                                  # T20: pre-wrapper project: the installer once
            self.assertIn("--project %s --yes` once" % os.path.abspath(root).replace("\\", "/"),
                          result)
        else:
            self.assertIn("--root --yes", result)
            self.assertNotIn("--project", result)

    def _wrapper(self, root, py=None):
        os.makedirs(os.path.join(root, "tools"), exist_ok=True)
        with open(os.path.join(root, "tools", "pa3_update.py"), "w", encoding="utf-8") as fh:
            fh.write("# stub\n")
        if py:
            with open(os.path.join(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
                json.dump({"python": py}, fh)

    def test_offer_names_project_refresh_when_governed(self):
        root = self._governed()
        from pa.hooks.session_start import _update_check
        # branch 2: behind > 0
        self._push("project-architect-3.0/f.txt", "feature Z")
        result = _update_check(self._inp(root), config.defaults())
        self.assertIn("update available", result)
        self._assert_steps(result, root)
        # branch 1: pulled clone, VERSION keeps the old tree
        _git_run(["-C", self.clone, "pull", "-q"])
        os.remove(os.path.join(self.pa3_dir, "update.json"))
        result = _update_check(self._inp(root), config.defaults())
        self.assertIn("is behind the clone", result)
        self._assert_steps(result, root)

    def test_upgrade_ask_keeps_offer_text(self):
        """T15/T20: pa.json upgrade "ask", no wrapper → the installer once, exactly."""
        root = self._governed()
        with open(os.path.join(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"upgrade": "ask"}\n')
        from pa.hooks.session_start import _update_steps, _upgrade_mode
        self.assertEqual(_upgrade_mode(root), "ask")
        inst = "%s %s/project-architect-3.0/pa_install.py" % (
            sys.executable.replace("\\", "/"), self.clone.replace("\\", "/"))
        self.assertEqual(
            _update_steps(self.clone, root, _upgrade_mode(root)),
            "Say `update pa3` to run `%s --project %s --yes` once (this project predates "
            "tools/pa3_update.py), then restart." % (inst, root.replace("\\", "/")))

    def test_upgrade_default_auto_text(self):
        """T15/T20: no upgrade key, no wrapper → auto: the installer once before the first task."""
        root = self._governed()
        from pa.hooks.session_start import _update_steps, _upgrade_mode
        self.assertEqual(_upgrade_mode(root), "auto")
        self.assertEqual(_upgrade_mode(None), "ask")
        c = self.clone.replace("\\", "/")
        inst = "%s %s/project-architect-3.0/pa_install.py" % (sys.executable.replace("\\", "/"), c)
        self.assertEqual(
            _update_steps(self.clone, root, "auto"),
            "Upgrade: auto: before the first task run `%s --project %s --yes` once (this project "
            "predates tools/pa3_update.py); then announce from "
            "%s/project-architect-3.0/CHANGES.md." % (inst, root.replace("\\", "/"), c))

    def test_wrapper_steps(self):
        """T20: a governed root with tools/pa3_update.py names the wrapper with pa.json python."""
        root = self._governed()
        self._wrapper(root, py="C:\\Py\\python.exe")
        from pa.hooks.session_start import _update_steps
        c = self.clone.replace("\\", "/")
        self.assertEqual(
            _update_steps(self.clone, root, "auto"),
            "Upgrade: auto: before the first task run `C:/Py/python.exe tools/pa3_update.py` "
            "(one command, never chained); then announce from "
            "%s/project-architect-3.0/CHANGES.md." % c)
        self.assertEqual(_update_steps(self.clone, root, "ask"),
                         "Say `update pa3` to run `C:/Py/python.exe tools/pa3_update.py`, "
                         "then restart.")
        # no pa.json python → sys.executable; no root → unchanged root-only text
        with open(os.path.join(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        self.assertIn("`%s tools/pa3_update.py`" % sys.executable.replace("\\", "/"),
                      _update_steps(self.clone, root, "ask"))
        self.assertEqual(
            _update_steps(self.clone, None, "ask"),
            "Say `update pa3` to run `git -C %s pull --ff-only` and "
            "`%s %s/project-architect-3.0/pa_install.py --root --yes`, then restart."
            % (c, sys.executable.replace("\\", "/"), c))

    def test_legacy_behind_clone_names_project_refresh(self):
        root = self._governed()
        self._write_version(tree=False)
        self._push("project-architect-3.0/f.txt", "feature L")
        _git_run(["-C", self.clone, "pull", "-q"])
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(root), config.defaults())
        self.assertIn("is behind the clone", result)
        self._assert_steps(result, root)

    def test_ungoverned_offer_root_only(self):
        self._push("project-architect-3.0/f.txt", "feature U")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self._assert_steps(result, None)

    def test_results_only_commit_no_offer(self):
        self._push("bench/results/2026-09-28.json", "bench: results")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNone(result)
        with open(os.path.join(self.pa3_dir, "update.json"), encoding="utf-8") as fh:
            stamp = json.loads(fh.read())
        self.assertEqual(stamp["tree"], self.tree)
        self.assertEqual(stamp["remote_tree"], self.tree)

    def test_package_commit_offers(self):
        self._push("bench/results/2026-09-28.json", "bench: results")
        self._push("project-architect-3.0/f.txt", "feature Y")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNotNone(result)
        self.assertIn("update available", result)
        self.assertIn("1 commits", result)
        self.assertIn("feature Y", result)
        self.assertNotIn("bench: results", result)

    def test_version_only_change_offers_and_names_version(self):
        """I11: same package tree, the clone's VERSION differs from the installed version: line."""
        self._push("project-architect-3.0/VERSION", "3.11")
        _git_run(["-C", self.clone, "pull", "-q"])
        r = _git_run(["-C", self.clone, "rev-parse", "HEAD:project-architect-3.0"],
                     capture_output=True, text=True)
        self.tree = r.stdout.strip()
        self._write_version(tree=True, version="3.10")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNotNone(result)
        self.assertIn("(version 3.10 -> 3.11)", result)
        self._write_version(tree=True, version="3.11")    # versions and trees agree: silent
        self.assertIsNone(_update_check(self._inp(), config.defaults()))

    def test_legacy_version_without_tree_offers(self):
        self._write_version(tree=False)
        self._push("bench/results/2026-09-28.json", "bench: results")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNotNone(result)
        self.assertIn("update available", result)
        self.assertIn("bench: results", result)

    def test_update_behind_by_one(self):
        self._push("project-architect-3.0/f.txt", "feature X")
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNotNone(result)
        self.assertIn("update available", result)
        self.assertIn("1 commits", result)
        self.assertIn("feature X", result)
        stamp_path = os.path.join(self.pa3_dir, "update.json")
        self.assertTrue(os.path.exists(stamp_path))
        with open(stamp_path, encoding="utf-8") as fh:
            stamp = json.loads(fh.read())
        self.assertEqual(stamp["behind"], 1)
        self.assertEqual(stamp["status"], "ok")

    def test_second_call_within_window(self):
        self._push("project-architect-3.0/f.txt", "feature X")
        from pa.hooks.session_start import _update_check
        cfg = config.defaults()
        result1 = _update_check(self._inp(), cfg)
        self.assertIsNotNone(result1)
        stamp_path = os.path.join(self.pa3_dir, "update.json")
        with open(stamp_path, encoding="utf-8") as fh:
            stamp1 = fh.read()
        result2, fetches = self._counting(_update_check, cfg)
        self.assertEqual(result2, result1)
        self.assertEqual(fetches, 0)
        with open(stamp_path, encoding="utf-8") as fh:
            stamp2 = fh.read()
        self.assertEqual(stamp1, stamp2)

    def _counting(self, fn, cfg):
        """Run fn(self._inp(), cfg) counting `git fetch` invocations; return (result, count)."""
        import subprocess
        from unittest import mock
        real = subprocess.run
        calls = []

        def spy(args, *a, **kw):
            if "fetch" in args:
                calls.append(args)
            return real(args, *a, **kw)

        with mock.patch.object(subprocess, "run", spy):
            result = fn(self._inp(), cfg)
        return result, len(calls)

    def test_fresh_stamp_nothing_behind_is_silent(self):
        from pa.hooks.session_start import _update_check
        cfg = config.defaults()
        self.assertIsNone(_update_check(self._inp(), cfg))
        result, fetches = self._counting(_update_check, cfg)
        self.assertIsNone(result)
        self.assertEqual(fetches, 0)

    def test_fresh_stamp_after_push_offers_from_cached_remote(self):
        from pa.hooks.session_start import _update_check
        cfg = config.defaults()
        self.assertIsNone(_update_check(self._inp(), cfg))
        stamp_path = os.path.join(self.pa3_dir, "update.json")
        with open(stamp_path, encoding="utf-8") as fh:
            stamp1 = fh.read()
        self._push("project-architect-3.0/f.txt", "feature F")
        result, fetches = self._counting(_update_check, cfg)
        self.assertIsNone(result)           # the stamp skips the fetch: origin/main is stale
        self.assertEqual(fetches, 0)
        _git_run(["-C", self.clone, "fetch", "-q"])
        result, fetches = self._counting(_update_check, cfg)
        self.assertEqual(fetches, 0)
        self.assertIn("1 commits", result)
        self.assertIn("feature F", result)
        with open(stamp_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), stamp1)

    def test_unreachable_origin_offline(self):
        _git_run(["-C", self.clone, "remote", "set-url", "origin",
                  os.path.join(self.tmpdir, "nonexistent.git")])
        from pa.hooks.session_start import _update_check
        result = _update_check(self._inp(), config.defaults())
        self.assertIsNone(result)
        stamp_path = os.path.join(self.pa3_dir, "update.json")
        self.assertTrue(os.path.exists(stamp_path))
        with open(stamp_path, encoding="utf-8") as fh:
            stamp = json.loads(fh.read())
        self.assertEqual(stamp["status"], "offline")


@unittest.skipUnless(_GIT, "git not on PATH")
class RecordBehindTest(unittest.TestCase):
    """T15: _record_behind flags a project whose managed record is behind VERSION git:."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-recbehind-")
        self.addCleanup(self._cleanup)
        self.clone = os.path.join(self.tmpdir, "clone")
        _init_repo(self.clone)
        self.old = self._head()
        _git_run(["-C", self.clone, "commit", "--allow-empty", "-q", "-m", "second"])
        self.new = self._head()
        self.pa3_dir = os.path.join(self.tmpdir, "pa3")
        os.makedirs(self.pa3_dir)
        with open(os.path.join(self.pa3_dir, "VERSION"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("version: 3.11\ngit: %s\n" % self.new)
        self.root = _setup_project(self.tmpdir)
        import pa.paths as _paths
        self._orig_install_dir = _paths.install_dir
        self._orig_clone_dir = _paths.package_clone_dir
        _paths.install_dir = lambda: self.pa3_dir
        _paths.package_clone_dir = lambda config_dir=None: self.clone

    def _cleanup(self):
        import pa.paths as _paths
        _paths.install_dir = self._orig_install_dir
        _paths.package_clone_dir = self._orig_clone_dir
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _head(self):
        return _git_run(["-C", self.clone, "rev-parse", "--short", "HEAD"],
                        text=True).stdout.strip()

    def _record(self, git):
        doc = {"format": 1, "files": {}}
        if git:
            doc["git"] = git
        with open(os.path.join(self.root, ".claude", "pa3-managed.json"), "w") as fh:
            json.dump(doc, fh)

    def _check(self, **extra):
        from pa.hooks.session_start import _record_behind
        inp = {"session_id": "s", "source": "startup", "cwd": self.root}
        inp.update(extra)
        return _record_behind(inp, config.defaults())

    def test_record_behind_flagged(self):
        self._record(self.old)
        c = self.clone.replace("\\", "/")
        cmd = "%s %s/project-architect-3.0/pa_install.py --project %s --yes" % (
            sys.executable.replace("\\", "/"), c, os.path.abspath(self.root).replace("\\", "/"))
        self.assertEqual(self._check(),
                         "PA3: this project's files (record %s) are behind the installed PA3 (%s). "
                         "Upgrade: auto: before the first task run `%s`; then announce from "
                         "%s/project-architect-3.0/CHANGES.md." % (self.old, self.new, cmd, c))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"upgrade": "ask"}, fh)
        self.assertTrue(self._check().endswith("Say `update pa3` to run `%s`, then restart." % cmd))

    def test_record_behind_names_wrapper(self):
        """T20: with tools/pa3_update.py the line names the wrapper, not --project."""
        self._record(self.old)
        os.makedirs(os.path.join(self.root, "tools"), exist_ok=True)
        with open(os.path.join(self.root, "tools", "pa3_update.py"), "w") as fh:
            fh.write("# stub\n")
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"python": "C:/Py/python.exe"}, fh)
        c = self.clone.replace("\\", "/")
        self.assertEqual(self._check(),
                         "PA3: this project's files (record %s) are behind the installed PA3 (%s). "
                         "Upgrade: auto: before the first task run `C:/Py/python.exe "
                         "tools/pa3_update.py` (one command, never chained); then announce from "
                         "%s/project-architect-3.0/CHANGES.md." % (self.old, self.new, c))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"python": "C:/Py/python.exe", "upgrade": "ask"}, fh)
        self.assertTrue(self._check().endswith(
            "Say `update pa3` to run `C:/Py/python.exe tools/pa3_update.py`, then restart."))

    def test_missing_record_hash_flagged(self):
        self._record(None)
        self.assertIn("(record none)", self._check())

    def test_record_at_installed_silent(self):
        self._record(self.new)
        self.assertIsNone(self._check())

    def test_record_ahead_or_subagent_silent(self):
        self._record(self.old)
        self.assertIsNone(self._check(agent_id="sub-1"))
        self.assertIsNone(self._check(source="resume"))
        with open(os.path.join(self.pa3_dir, "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("git: %s\n" % self.old)
        self._record(self.new)                    # record ahead of VERSION: not behind
        self.assertIsNone(self._check())


class AgentNotLoadedTest(unittest.TestCase):
    """fix-1: settings name a main-thread agent Claude Code did not load (CRLF)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-agent-")
        self.root = _setup_project(self.tmpdir)
        os.makedirs(os.path.join(self.root, ".claude", "agents"))
        with open(os.path.join(self.root, ".claude", "settings.json"), "w") as fh:
            json.dump({"agent": "pa-session"}, fh)
        import pa.paths as _paths
        self._orig_clone_dir = _paths.package_clone_dir
        _paths.package_clone_dir = lambda config_dir=None: "C:\\clone"
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import pa.paths as _paths
        _paths.package_clone_dir = self._orig_clone_dir
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _agent(self, data):
        with open(os.path.join(self.root, ".claude", "agents", "pa-session.md"), "wb") as fh:
            fh.write(data)

    def _check(self, **extra):
        from pa.hooks.session_start import _agent_not_loaded
        inp = {"session_id": "s", "source": "startup", "cwd": self.root}
        inp.update(extra)
        return _agent_not_loaded(inp)

    def test_crlf_agent_names_the_refresh(self):
        self._agent(b"---\r\nname: pa-session\r\n---\r\nbody\r\n")
        line = self._check()
        root = os.path.abspath(self.root).replace("\\", "/")
        self.assertEqual(line, "PA3: .claude/settings.json names agent pa-session but Claude Code"
                         " did not load it (the file has CRLF line endings); run %s"
                         " C:/clone/project-architect-3.0/pa_install.py --project %s --yes,"
                         " then restart." % (sys.executable.replace("\\", "/"), root))

    def test_lf_agent_not_loaded_plain_line(self):
        self._agent(b"---\nname: pa-session\n---\nbody\n")
        self.assertEqual(self._check(), "PA3: .claude/settings.json names agent pa-session"
                         " but Claude Code did not load it")

    def test_loaded_agent_is_silent(self):
        self._agent(b"---\r\nname: pa-session\r\n---\r\n")
        self.assertIsNone(self._check(agent_type="pa-session"))

    def test_run_appends_line_on_startup_only(self):
        from pa.hooks import session_start as ss
        self._agent(b"---\r\nname: pa-session\r\n---\r\n")
        with mock.patch.object(ss, "_offer_install", return_value=None), \
                mock.patch.object(ss, "_update_check", return_value=None), \
                mock.patch.object(ss, "_record"), mock.patch.object(ss, "open_db"), \
                mock.patch.object(ss, "close_db"), mock.patch("pa.warmer.start_if_needed"), \
                mock.patch.object(ss.accounts, "auth_status", return_value={}), \
                mock.patch.object(ss.paths, "ensure_ledger_tree"), \
                mock.patch.object(ss, "_unstop_runs"), mock.patch.object(ss, "_progress_from_kill"), \
                mock.patch.object(ss, "_ensure_memory_link"):
            out = ss.run({"session_id": "s", "source": "startup", "cwd": self.root},
                         config.defaults())
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn("names agent pa-session but Claude Code did not load it", ctx)
            self.assertIsNone(ss.run({"session_id": "s", "source": "resume", "cwd": self.root},
                                     config.defaults()))


class RenewalPromptTest(unittest.TestCase):
    """3.11 T9: an account without a renewal day is asked, once per session and account, never guessed."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-renewal-")
        self.addCleanup(self._cleanup)
        self.ledger_dir, self.conn = _setup_ledger(self.tmpdir)
        self.root = _setup_project(self.tmpdir)
        self.sid = "rrrr1111-2222-4333-8444-555555555555"
        from pa import accounts, notify
        self._old_auth = accounts.auth_status
        accounts.auth_status = lambda *a, **k: {"email": "test@test.com", "source": "auth"}
        notify.set_spawner(lambda cmd, env: None)

    def _cleanup(self):
        from pa import accounts, notify
        accounts.auth_status = self._old_auth
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        from pa.hooks import _PROJECT_ROOTS
        _PROJECT_ROOTS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _start(self, cfg, source="startup"):
        from pa.hooks import session_start
        return session_start.run({"session_id": self.sid, "source": source, "cwd": self.root}, cfg) or {}

    def test_unknown_day_asks_the_user_and_the_model_once(self):
        out = self._start(config.defaults())
        user = out.get("systemMessage") or ""
        model = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
        self.assertIn("Settings > Billing", user)
        self.assertIn("accounts renewal test@test.com <day>", user)
        self.assertIn("PA3: the plan renewal day of test@test.com is unknown", model)
        again = self._start(config.defaults(), source="resume")
        self.assertNotIn("renewal", again.get("systemMessage") or "")
        self.assertNotIn("renewal day", (again.get("hookSpecificOutput") or {}).get("additionalContext") or "")

    def test_known_day_asks_nothing(self):
        cfg = config.defaults()
        cfg["accounts"] = {"test@test.com": {"renewal_day": 9}}
        out = self._start(cfg)
        self.assertNotIn("renewal", out.get("systemMessage") or "")

    def test_a_switch_to_an_unknown_account_asks_at_the_next_prompt(self):
        from pa import accounts
        from pa.hooks import user_prompt_submit

        cfg = config.defaults()
        cfg["accounts"] = {"test@test.com": {"renewal_day": 9}}
        self._start(cfg)
        inp = {"session_id": self.sid, "prompt": "go on", "cwd": self.root}
        with mock.patch.object(accounts, "refresh_account", return_value=("new@test.com", True, "test@test.com")):
            out = user_prompt_submit.run(inp, cfg) or {}
            self.assertIn("accounts renewal new@test.com <day>", out.get("systemMessage") or "")
            self.assertIn("new@test.com is unknown",
                          (out.get("hookSpecificOutput") or {}).get("additionalContext") or "")
            self.assertIsNone(user_prompt_submit.run(inp, cfg))             # once per session and account
        with mock.patch.object(accounts, "refresh_account", return_value=("test@test.com", False, None)):
            self.assertIsNone(user_prompt_submit.run(inp, cfg))             # a known account: nothing


if __name__ == "__main__":
    unittest.main()
