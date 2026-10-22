"""tools/pa_ext.py: the external-coder setup and check (3.3 T2), run and say (3.3 T5); the mod-side writes, ask and
run --resume (3.3 T5.c2).

Every test runs in a scratch home (USERPROFILE and HOME, saved and restored) against a fake backend:
a ThreadingHTTPServer on 127.0.0.1:0 in a thread, answering POST /v1/messages, GET /v1/models, GET /v1/key and
GET /v1/models/<id>/endpoints
(the host advice tests patch pa_ext.is_openrouter so the fake counts as OpenRouter).
run uses a fake claude (FAKE_CLAUDE, through pa_ext._CLAUDE) speaking stream-json. No network.
"""

import glob
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))

import pa_ext  # noqa: E402

GOOD = "sk-good-0123456789"
BAD = "sk-bad-9876543210"
DS = "deepseek/deepseek-v4.1-flash-1017"
GLM = "z-ai/glm-5.3-flash"
ENV_KEYS = ("USERPROFILE", "HOME", "PA3_DOUBLECLICK", "PA_MOD_PA3_DIR")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": DS, "name": "DeepSeek: DeepSeek V4.1 Flash 1017"}]})
        elif self.path == "/v1/key":
            if self.headers.get("Authorization") != "Bearer " + GOOD:
                return self._send(401, {})
            self._send(200, {"data": {"usage": self.server.key_usage, "limit_remaining": 9.5}})
        elif self.path.startswith("/v1/models/") and self.path.endswith("/endpoints"):
            mid = self.path[len("/v1/models/"):-len("/endpoints")]
            self.server.ep_seen.append((mid, self.headers.get("Authorization")))
            if mid in self.server.endpoints:
                self._send(200, {"data": self.server.endpoints[mid]})
            else:
                self._send(404, {})
        else:
            self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        srv = self.server
        srv.seen.append((self.headers.get("Authorization"), body.get("model"), body.get("max_tokens")))
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer " + GOOD:
            return self._send(401, {"error": {"message": "invalid key %s" % auth[7:]}})
        model = body.get("model")
        if model in srv.models:
            return self._send(200, {"content": [{"type": "text", "text": "ok"}]})
        return self._send(400, {"error": {"message": "unknown model %s" % model}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ("PA3_DOUBLECLICK", "PA_MOD_PA3_DIR"):
            os.environ.pop(k, None)
        self.home = tempfile.mkdtemp(prefix="pa3-ext-")
        os.environ["USERPROFILE"] = self.home
        os.environ["HOME"] = self.home
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.seen, self.srv.models = [], {DS, GLM}
        self.srv.ep_seen, self.srv.endpoints = [], {}
        self.srv.key_usage = 1.25
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.root = os.path.join(self.home, ".claude", "pa3-ext")
        self._sandbox_missing = pa_ext.sandbox_missing
        pa_ext.sandbox_missing = lambda: False

    def tearDown(self):
        pa_ext.sandbox_missing = self._sandbox_missing
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)

    def run_setup(self, answers, secrets=()):
        a, s, out = list(answers), list(secrets), []

        def ask(prompt):
            out.append(prompt)
            if not a:
                raise EOFError
            return a.pop(0)

        def ask_secret(prompt):
            out.append(prompt)
            if not s:
                raise EOFError
            return s.pop(0)

        code = pa_ext.setup(ask=ask, ask_secret=ask_secret, out=out.append)
        return code, "\n".join(out)

    def new_config(self, key=GOOD, ids=DS + ", " + GLM):
        # URL, name (default), ids, alias/display per model (defaults)
        return self.run_setup([self.url, "", ids, "", "", "", ""], [key])

    def status(self):
        with open(os.path.join(self.root, "status.json"), "rb") as fh:
            raw = fh.read()
        return raw, json.loads(raw)


class NewConfigTest(_Base):
    def test_two_good_models_write_both_files(self):
        code, out = self.new_config()
        self.assertEqual(code, 0)
        folder = os.path.join(self.root, "local")
        with open(os.path.join(folder, "settings.json"), "rb") as fh:
            settings = fh.read()
        want = json.dumps({"env": {"ANTHROPIC_BASE_URL": self.url, "ANTHROPIC_AUTH_TOKEN": GOOD,
                                   "ANTHROPIC_API_KEY": ""}}, indent=2) + "\n"
        self.assertEqual(settings, want.encode("utf-8"))
        with open(os.path.join(folder, "pa3-ext.json"), "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.endswith(b"}\n"))
        self.assertNotIn(b"\r", raw)
        meta = json.loads(raw)
        self.assertEqual(list(meta), ["name", "url", "models", "tested_at"])
        self.assertIsInstance(meta.pop("tested_at"), int)
        self.assertEqual(meta, {"name": "local", "url": self.url, "models": {
            "deepseek": {"id": DS, "name": "DeepSeek V4.1 Flash 1017"},
            "glm": {"id": GLM, "name": "glm 5.3 flash"}}})
        raw, st = self.status()
        self.assertTrue(st["ok"])
        self.assertEqual(list(st), ["at", "ok", "text"])
        self.assertEqual(st["text"], "local saved: deepseek ok, glm ok")
        self.assertIn(st["text"], out)
        self.assertIn("deepseek (%s): ok" % DS, out)
        self.assertIn("The key is written only to %s on this machine and sent only to %s"
                      % (os.path.join(folder, "settings.json"), self.url), out)
        self.assertNotIn(GOOD.encode(), raw)
        self.assertNotIn(GOOD, out)
        if os.name != "nt":
            self.assertEqual(os.stat(os.path.join(folder, "settings.json")).st_mode & 0o777, 0o600)


class BadKeyTest(_Base):
    def test_bad_key_saves_nothing(self):
        code, out = self.new_config(key=BAD)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "local")))
        raw, st = self.status()
        self.assertFalse(st["ok"])
        self.assertTrue(st["text"].startswith("local not saved: deepseek failed: 401 invalid key ***"), st["text"])
        self.assertIn("Nothing saved: deepseek failed: 401", out)
        self.assertNotIn(BAD.encode(), raw)
        self.assertNotIn(BAD, out)


class UnknownModelTest(_Base):
    def test_one_unknown_model_saves_nothing(self):
        code, out = self.new_config(ids=DS + ", x/nope-1")
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "local")))
        self.assertIn("nope (x/nope-1): failed: 400 unknown model x/nope-1", out)
        self.assertEqual(self.status()[1]["text"], "local not saved: nope failed: 400 unknown model x/nope-1")


class UpdateTest(_Base):
    def test_enter_at_the_key_keeps_it(self):
        self.new_config()
        # menu 1 (the config), URL, name, ids, alias/display x2: all Enter; the key Enter too
        code, out = self.run_setup(["1", "", "", "", "", "", "", ""], [""])
        self.assertEqual(code, 0)
        self.assertIn("Key (Enter keeps the current one): ", out)
        self.assertIn("Alias for %s [deepseek]: " % DS, out)
        with open(os.path.join(self.root, "local", "settings.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["env"]["ANTHROPIC_AUTH_TOKEN"], GOOD)
        self.assertTrue(self.status()[1]["ok"])


class MenuTest(_Base):
    def test_menu_order_and_exit_writes_nothing(self):
        folder = os.path.join(self.root, "openrouter")
        pa_ext._write_settings(folder, "https://openrouter.ai/api", GOOD)
        pa_ext._write_meta(folder, "openrouter", "https://openrouter.ai/api",
                           {"deepseek": {"id": DS, "name": "d"}, "glm": {"id": GLM, "name": "g"}})
        before = sorted(os.listdir(self.root))
        code, out = self.run_setup(["3"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        i = lines.index("1. openrouter  https://openrouter.ai/api  (deepseek, glm)")
        self.assertEqual(lines[i + 1:i + 3], ["2. New config", "3. Exit"])
        self.assertEqual(sorted(os.listdir(self.root)), before)


def _ep(provider, tag, quant, prompt, completion, cache=None):
    e = {"provider_name": provider, "tag": tag,
         "pricing": {"prompt": prompt, "completion": completion}}
    if quant is not None:
        e["quantization"] = quant
    if cache is not None:
        e["pricing"]["input_cache_read"] = cache
    return e


DS_HOSTS = {"name": "DeepSeek: DS From Endpoints", "endpoints": [
    _ep("Cheap", "cheap/fp4", "fp4", "0.0000001", "0.0000002", "0.00000001"),
    _ep("Mystery", "mystery", None, "0.0000002", "0.0000004", "0.00000002"),
    _ep("Big", "big", "bf16", "0.000001", "0.000002"),
    _ep("InferenceNet", "inferencenet/fp8", "fp8", "0.0000005", "0.000001", "0.00000005")]}
GUARD = ("To keep each model on its host, add a guardrail to this key on openrouter.ai: Guardrails, "
         "allowed providers: %s. Claude Code cannot choose the host itself.")
ONE_KEY = "These models are best on different hosts."


class HostAdviceTest(_Base):
    def setUp(self):
        super().setUp()
        self._is_or = pa_ext.is_openrouter
        pa_ext.is_openrouter = lambda url: True
        self.srv.endpoints = {DS: DS_HOSTS, GLM: {"name": "Z.ai: GLM 5.3 Flash", "endpoints": [
            _ep("InferenceNet", "inferencenet", "fp8", "0.0000004", "0.000001")]}}

    def tearDown(self):
        pa_ext.is_openrouter = self._is_or
        super().tearDown()

    def test_ranking_guardrail_and_display_names(self):
        code, out = self.new_config()
        self.assertEqual(code, 0)
        lines = out.splitlines()
        i = lines.index("Hosts for deepseek (%s), best first (a typical coder run: 883K cached, "
                        "48K new, 5K output tokens):" % DS)
        self.assertEqual(lines[i + 1:i + 6], [
            "  InferenceNet (inferencenet/fp8)  fp8  $0.073  <- recommended",
            "  Big (big)  bf16  $0.941",
            "  Mystery (mystery)  unstated  $0.029",
            "  Cheap (cheap/fp4)  fp4  $0.015",
            "  Lower precision (fp4) is cheaper but may code worse."])
        self.assertIn(GUARD % "InferenceNet", lines)
        self.assertNotIn(ONE_KEY, out)
        self.assertLess(lines.index("local saved: deepseek ok, glm ok"), i)
        self.assertEqual(sorted(self.srv.ep_seen), [(DS, None), (GLM, None)])
        with open(os.path.join(self.root, "local", "pa3-ext.json"), encoding="utf-8") as fh:
            models = json.load(fh)["models"]
        self.assertEqual(models["deepseek"]["name"], "DS From Endpoints")
        self.assertEqual(models["glm"]["name"], "GLM 5.3 Flash")
        self.assertNotIn(GOOD, out)

    def test_different_hosts_advise_one_key_per_model(self):
        self.srv.endpoints[GLM]["endpoints"] = [_ep("Other", "other", "fp16", "0.0000004", "0.000001")]
        code, out = self.new_config()
        self.assertEqual(code, 0)
        self.assertIn(GUARD % "InferenceNet, Other", out)
        self.assertIn(ONE_KEY + " One guardrail covers the whole key, so use one key per model: choose "
                      "New config for each, with its own key.", out)

    def test_failed_host_list_still_saves(self):
        del self.srv.endpoints[GLM]
        code, out = self.new_config()
        self.assertEqual(code, 0)
        self.assertIn("glm: host list unavailable (HTTP 404)", out)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "local", "pa3-ext.json")))
        self.assertIn("Hosts for deepseek", out)
        self.assertIn(GUARD % "InferenceNet", out)
        self.assertNotIn(ONE_KEY, out)

    def test_other_url_prints_no_advice(self):
        pa_ext.is_openrouter = self._is_or
        code, out = self.new_config()
        self.assertEqual(code, 0)
        self.assertNotIn("Hosts for", out)
        self.assertNotIn("guardrail", out)
        self.assertEqual(self.srv.ep_seen, [])

    def test_is_openrouter(self):
        self.assertTrue(self._is_or("https://openrouter.ai/api"))
        self.assertFalse(self._is_or("https://api.deepseek.com"))


class NameDefaultTest(_Base):
    def test_taken_name_offers_dash_2(self):
        self.new_config()
        code, out = self.run_setup(["2", self.url, "", DS, "", ""], [GOOD])
        self.assertEqual(code, 0)
        self.assertIn("Name [local-2]: ", out)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "local-2", "pa3-ext.json")))


class CheckTest(_Base):
    def _make(self, key):
        folder = os.path.join(self.root, "local")
        pa_ext._write_settings(folder, self.url, key)
        pa_ext._write_meta(folder, "local", self.url,
                           {"deepseek": {"id": DS, "name": "d"}, "glm": {"id": GLM, "name": "g"}})

    def _check(self):
        out = []
        return pa_ext.check(out=out.append), "\n".join(out)

    def test_connected(self):
        self._make(GOOD)
        self.assertEqual(self._check(), (0, "local: connected (deepseek ok, glm ok)"))

    def test_not_connected(self):
        self._make(BAD)
        code, out = self._check()
        self.assertEqual(code, 1)
        self.assertEqual(out, "local: not connected (401 invalid key ***)")

    def test_partly_connected(self):
        self._make(GOOD)
        self.srv.models = {DS}
        self.assertEqual(self._check(), (1, "local: deepseek ok, glm failed: 400 unknown model " + GLM))

    def test_no_folders(self):
        self.assertEqual(self._check(),
                         (1, "no external coders configured on this machine: run setup-external"))

    def test_linux_without_the_sandbox(self):
        self._make(GOOD)
        pa_ext.sandbox_missing = lambda: True
        self.assertEqual(self._check(), (1, "local: connected (deepseek ok, glm ok)\n" + pa_ext.SANDBOX_LINE))


class DefaultsTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(pa_ext.default_name("https://openrouter.ai/api"), "openrouter")
        self.assertEqual(pa_ext.default_name("https://api.deepseek.com"), "deepseek")
        self.assertEqual(pa_ext.default_name("http://localhost:11434"), "local")
        self.assertEqual(pa_ext.default_name("http://[::1]:8080"), "local")
        self.assertEqual(pa_ext.default_name("http://127.0.0.1:11434", {"local": "http://localhost:8080"}),
                         "local-11434")
        self.assertEqual(pa_ext.default_name("http://localhost:8080/", {"local": "http://localhost:8080"}),
                         "local")

    def test_aliases_and_display_names(self):
        self.assertEqual(pa_ext.default_alias(DS), "deepseek")
        self.assertEqual(pa_ext.default_alias(GLM), "glm")
        self.assertEqual(pa_ext.default_alias("other/deepseek-r9", {"deepseek": {}}), "deepseek-2")
        self.assertEqual(pa_ext.default_display(DS, {DS: "DeepSeek: DeepSeek V4.1 Flash 1017"}),
                         "DeepSeek V4.1 Flash 1017")
        self.assertEqual(pa_ext.default_display(GLM), "glm 5.3 flash")


FAKE_CLAUDE = r'''
import json, os, sys, threading
threading.Timer(20, lambda: os._exit(7)).start()        # never hang the suite: exit 7 without EOF
args = sys.argv[1:]
model = args[args.index("--model") + 1]
mode = os.environ.get("FAKE_MODE", "")
rec = {"argv": args, "env": dict(os.environ), "msgs": [], "eof": False}


def save():
    with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


save()
for line in sys.stdin:
    msg = json.loads(line)["message"]
    text = msg["content"][0]["text"]
    rec["msgs"].append(text)
    save()
    if len(rec["msgs"]) == 1:
        emit({"type": "system", "subtype": "init", "model": model})
    emit({"type": "user", "message": msg})
    if len(rec["msgs"]) == 1:
        emit({"type": "assistant", "message": {"usage": {"input_tokens": 7, "cache_read_input_tokens": 300,
                                                         "cache_creation_input_tokens": 20, "output_tokens": 4},
              "content": [{"type": "text", "text": "I will fix the name.\n\nThen test it.\nThird line."}]}})
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Edit", "input": {
            "file_path": os.path.join(os.getcwd(), "src", "a.py"), "old_string": "a = 1\nb = 2\nc = 3\n",
            "new_string": "a = 1\nb = 20\nc = 3\n"}}]}})
        emit({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
              "content": "The file has been updated.", "is_error": False}]}})
    if mode == "bigctx" and len(rec["msgs"]) == 1:      # 3.30: past the handoff threshold; the result waits
        emit({"type": "assistant", "message": {"id": "big", "usage": {"input_tokens": 10, "cache_read_input_tokens": 400000,
              "output_tokens": 5}, "content": [{"type": "thinking", "thinking": "The pin sits on $4."},
                                               {"type": "text", "text": "still going"}]}})
        continue
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
    if mode == "noresult":
        emit({"type": "system", "subtype": "api_retry", "error": "429 Provider returned error"})
        sys.stderr.write("boom\n")
        sys.stderr.flush()
        os._exit(1)
    usage = {model: {"inputTokens": 1000, "cacheReadInputTokens": 2000, "cacheCreationInputTokens": 0,
                     "outputTokens": 500}}
    if mode == "leak":
        usage["claude-haiku-4-5"] = {"inputTokens": 1}
    result, is_error = "done: " + text.splitlines()[0] + "\nline 2" + os.environ.get("FAKE_TAIL", ""), False
    if mode == "plan" and len(rec["msgs"]) == 1:        # a turn that ends on its plan
        result = "I'll start by reading the pointers."
    if mode == "toolong":
        result, is_error = "Prompt is too long", True
    if mode == "judged":                                # 3.30.1: what the judge's hook would have recorded
        with open(os.path.join(os.environ["PA_EXT_RUN_DIR"], "decisions.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"by": "judge", "decision": "allow", "tool": "Bash", "s": 4.2,
                                 "what": "mipsel-linux-gnu-objdump -d a.o", "remember": "Bash(mipsel-linux-gnu-objdump:*)"}) + "\n")
            fh.write(json.dumps({"by": "network", "decision": "deny", "tool": "Bash", "what": "curl x",
                                 "reason": "network: curl"}) + "\n")
    emit({"type": "result", "subtype": "success", "is_error": is_error, "result": result,
          "num_turns": len(rec["msgs"]), "modelUsage": usage, "permission_denials": [{"tool_name": "Bash"}],
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 5}})
rec["eof"] = True
save()
os._exit(0)
'''

RUN_ENV = ("FAKE_LOG", "FAKE_MODE", "FAKE_TAIL", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_BASE_URL",
           "PA_EXT_PARENT", "PA_EXT_LABEL", "PA_EXT_ASK_TIMEOUT")


class _RunBase(_Base):
    def setUp(self):
        _Base.setUp(self)
        self._renv = {k: os.environ.get(k) for k in RUN_ENV}
        self._claude = pa_ext._CLAUDE
        self.proj = os.path.join(self.home, "proj")
        c = os.path.join(self.proj, ".claude")
        self._w(os.path.join(c, "pa.json"), json.dumps({"python": "/x/py3"}))
        self._w(os.path.join(c, "settings.json"), json.dumps({
            "env": {"FOO": "1", "ANTHROPIC_BASE_URL": "http://elsewhere"}, "autoCompactEnabled": True,
            "permissions": {"allow": ["Bash(bash tools/run.sh:*)", "Bash", "Bash(/x/py3 tools/pa3_update.py)"],
                            "deny": ["Bash(git push:*)"]}}))
        self._w(os.path.join(c, "agents", "pa-coder.md"),
                "---\nname: pa-coder\ndescription: Edit-build-test loop.\nmodel: claude-opus-5-5[1m]\neffort: low\n"
                "tools: Read, Edit, Bash, Agent(retriever-code)\nskills:\n  - project-architect\n---\n\nCODER BODY\n")
        self._w(os.path.join(c, "skills", "project-architect", "SKILL.md"),
                "---\nname: project-architect\n---\n# House rules\nSKILL BODY\n")
        self._w(os.path.join(self.proj, "CLAUDE.md"), "PROJECT CLAUDE\n")
        self.brief = os.path.join(self.proj, ".run", "T5.c1.md")
        self._w(self.brief, "CODER TASK T5.c1 · do it\nCHANGE: x\n")
        self.folder = os.path.join(self.root, "local")
        pa_ext._write_settings(self.folder, self.url, GOOD)
        pa_ext._write_meta(self.folder, "local", self.url, {"deepseek": {"id": DS, "name": "DeepSeek V4.1 Flash"}})
        fake = os.path.join(self.home, "fake_claude.py")
        self._w(fake, FAKE_CLAUDE)
        pa_ext._CLAUDE = [sys.executable, fake]
        self.log = os.path.join(self.home, "fake.json")
        os.environ.update({"FAKE_LOG": self.log, "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent-sid",
                           "ANTHROPIC_BASE_URL": "http://inherited"})
        for k in ("FAKE_MODE", "FAKE_TAIL", "PA_EXT_PARENT", "PA_EXT_LABEL", "PA_EXT_ASK_TIMEOUT"):
            os.environ.pop(k, None)
        self.ext = os.path.join(self.proj, ".run", "mod", "ext")

    def tearDown(self):
        pa_ext._CLAUDE = self._claude
        for k, v in self._renv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _Base.tearDown(self)

    def _w(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _run(self, target="local/deepseek"):
        out = []
        code = pa_ext.run(target, self.brief, out=out.append, cwd=self.proj)
        return code, "\n".join(out)

    def _fake(self):
        with open(self.log, encoding="utf-8") as fh:
            return json.load(fh)

    def _record(self, label="T5.c1"):
        with open(os.path.join(self.ext, label + ".json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _running(self, label, config="local"):
        self._w(os.path.join(self.ext, label + ".json"), json.dumps(
            {"label": label, "config": config, "pid": os.getpid(), "status": "running"}))


class RunTest(_RunBase):
    def test_run_end_to_end(self):
        inbox = os.path.join(self.ext, "T5.c1", "in")
        self._w(os.path.join(inbox, "1700000000000-000000.json"), json.dumps({"text": "fix the name", "at": 1}))
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [
            "done: fix the name", "line 2",
            "HEALTH (for the expert: friction a later external coder would hit too):",   # 3.30: the result's denial
            "- denied 1 (no allow rule covers them): Bash ×1",
            "external coder T5.c1: DeepSeek V4.1 Flash · 2 turns · 115 tokens · denials 1"])
        fake = self._fake()
        self.assertTrue(fake["eof"], "stdin was not closed after the result")
        self.assertEqual(fake["msgs"], ["CODER TASK T5.c1 · do it\nCHANGE: x\n", "fix the name"])
        self.assertEqual(os.listdir(os.path.join(inbox, "done")), ["1700000000000-000000.json"])
        self.assertEqual([f for f in os.listdir(inbox) if f.endswith(".json")], [])
        rec = self._record()
        self.assertEqual(rec["status"], "done")
        self.assertEqual((rec["config"], rec["alias"], rec["model"], rec["parent_sid"]),
                         ("local", "deepseek", DS, "parent-sid"))
        self.assertEqual((rec["turns"], rec["health"]), (2, {"denied": {"Bash": 1}, "sandbox": 0, "guard": 0,
                                                              "retries": 0, "nudges": 0}))   # 3.30: for the review
        self.assertGreaterEqual(rec["ended"], rec["started"])
        # argv
        a = fake["argv"]
        for flag, val in (("--setting-sources", "user"), ("--model", DS), ("--effort", "high"),   # EXT_EFFORT, not the file's low
                          ("--session-id", rec["child_sid"]), ("--agent", "pa-coder")):
            self.assertEqual(a.count(flag), 1, flag)
            self.assertEqual(a[a.index(flag) + 1], val, flag)
        self.assertEqual(a.count("--allowedTools"), 1)
        allow = a[a.index("--allowedTools") + 1].split(",")
        self.assertIn("Bash(bash tools/run.sh:*)", allow)
        self.assertIn("Bash(git status)", allow)
        self.assertEqual(allow[0], "Edit(/**)")        # 3.30.1: edits inside the project only, no bare file tools
        for bare in ("Read", "Edit", "Write", "MultiEdit", "Grep", "Glob", "Bash"):
            self.assertNotIn(bare, allow)
        self.assertFalse([x for x in allow if "pa3_update" in x])
        self.assertEqual(a.count("--disallowedTools"), 1)
        self.assertEqual(a[a.index("--disallowedTools") + 1], "Agent,WebFetch,WebSearch,Bash(git push:*)")
        self.assertNotIn("--add-dir", a)
        # env
        env = fake["env"]
        self.assertFalse([k for k in env if k.upper().startswith("ANTHROPIC_")])
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], self.folder)
        self.assertEqual((env["PA_EXT"], env["PA_EXT_MODEL"], env["PA_EXT_PARENT"], env["PA_EXT_LABEL"]),
                         ("local/deepseek", DS, "parent-sid", "T5.c1"))
        run_dir = os.path.join(self.ext, "T5.c1")
        self.assertEqual((env["PA_EXT_PROJECT"], env["PA_EXT_RUN_DIR"], env["PA_EXT_DIRS"], env["PA_EXT_PY"]),
                         (self.proj, run_dir, "", "/x/py3"))       # 3.30.1: for the judge's hook
        self.assertNotIn("PA_EXT_CLAUDE", env)                      # a test's fake claude is no judge
        self.assertTrue(rec["report_word"])
        self.assertFalse(os.path.exists(os.path.join(run_dir, "auto-mode.json")))   # 3.30.5: no rulebook captured
        # the generated files
        with open(os.path.join(run_dir, "agents.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"pa-coder": {"description": "Edit-build-test loop.",
                                                          "prompt": "CODER BODY\n", "tools": ["Read", "Edit", "Bash"]}})
        with open(os.path.join(run_dir, "append.md"), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r", raw)
        app = raw.decode("utf-8")
        self.assertTrue(app.startswith(
            'You run on an external model through tools/pa_ext.py, outside Claude Code\'s subscription. Your Agent '
            'tool is off: for a code lookup run "/x/py3" tools/pa_ext.py ask "<question>"; a code retriever answers '
            'in under 40 lines.\nCorrections from your expert arrive as messages in this conversation: apply each at '
            'your next step.\nA command that fails at once with a `bwrap:` line never ran: its sandbox collided with '
            'another coder\'s. Run it again; that is no reason to stop.\n\n' + pa_ext.HELP_TEXT + '\n\n'
            '# The project-architect skill (binding)\n\n# House rules\nSKILL BODY\n\n'
            '# CLAUDE.md (project instructions)\n\nPROJECT CLAUDE\n'), app)
        self.assertNotIn("name: project-architect", app)
        os.environ["PA_EXT_HELP"] = "0"     # the A/B's arm without it
        try:
            self.assertNotIn("# Working well in this harness", pa_ext.append_text(self.proj, "/x/py3"))
        finally:
            del os.environ["PA_EXT_HELP"]
        with open(os.path.join(run_dir, "settings.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(st["env"]["FOO"], "1")
        self.assertNotIn("ANTHROPIC_BASE_URL", st["env"])
        for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
            self.assertEqual(st["env"][k], DS)
        self.assertEqual(st["env"]["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"], "1")
        self.assertEqual(st["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
        self.assertEqual(st["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "65536")
        self.assertEqual(sorted(st["hooks"]), sorted(pa_ext.HOOK_EVENTS + ("PermissionRequest",)))
        self.assertIn("pa_hook.py session_start", json.dumps(st["hooks"]))
        self.assertIn("pa_hook.py permission_request", json.dumps(st["hooks"]["PermissionRequest"]))
        self.assertEqual(st["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 180)
        self.assertEqual(st["permissions"], {"blockReadsOutsideWorkingDirectories": True})   # 3.30.1: the read fence
        self.assertIn("/x/py3", json.dumps(st["hooks"]))
        self.assertEqual((st["autoCompactEnabled"], st["todoFeatureEnabled"]), (True, False))
        self.assertNotIn("permissions", pa_ext.child_settings([], DS, {}))   # the bench's coders: as before

    def test_set_effort_overrides_the_default(self):
        with open(os.path.join(self.proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"python": "/x/py3", "external": {"effort": "max"}}, fh)
        code, out = self._run()
        self.assertEqual(code, 0, out)
        a = self._fake()["argv"]
        self.assertEqual(a[a.index("--effort") + 1], "max")

    def test_past_the_handoff_threshold_the_coder_is_asked_once(self):
        """3.30: as an in-house coder at its threshold: pa.handoff's text (350k by default), its own progress file."""
        os.environ["FAKE_MODE"] = "bigctx"
        code, out = self._run()
        self.assertEqual(code, 0, out)
        msgs = self._fake()["msgs"]
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(msgs[1].startswith("HANDOFF — context threshold reached."), msgs[1])
        self.assertIn("phase-ends/current/logs/T5.c1.progress.md", msgs[1])
        self.assertEqual(pa_ext.handoff_ask({}, "x")["limit"], 350000)
        self.assertEqual(pa_ext.handoff_ask({"handoff_ctx": {"coder": 200000}}, "x")["limit"], 200000)

    def test_a_turn_ended_on_its_plan_is_nudged(self):
        os.environ["FAKE_MODE"] = "plan"
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._fake()["msgs"], ["CODER TASK T5.c1 · do it\nCHANGE: x\n", pa_ext.NUDGE_TEXT])
        self.assertTrue(out.startswith("done: " + pa_ext.NUDGE_TEXT), out)

    def test_an_error_result_is_no_report(self):
        os.environ["FAKE_MODE"] = "toolong"
        code, out = self._run()
        self.assertEqual(code, 3)
        self.assertEqual(out.splitlines()[:2], ["blocked: the external coder ended without a report (exit 0)",
                                                "Prompt is too long"])
        self.assertEqual(len(self._fake()["msgs"]), 1)
        self.assertEqual(self._record()["status"], "blocked")

    def test_no_result_is_blocked(self):
        os.environ["FAKE_MODE"] = "noresult"
        code, out = self._run()
        self.assertEqual(code, 3)
        self.assertEqual(out.splitlines()[:2], ["blocked: the external coder ended without a report (exit 1)",
                                                "429 Provider returned error"])
        self.assertEqual(self._record()["status"], "blocked")

    def test_claude_key_in_model_usage_is_a_leak(self):
        os.environ["FAKE_MODE"] = "leak"
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[:2], ["done: CODER TASK T5.c1 · do it",
                                                "LEAK: requests ran on claude-haiku-4-5"])

    def test_lane_full(self):
        self._w(os.path.join(self.proj, ".claude", "pa.json"), json.dumps({"external": {"lanes": {"local": 1}}}))
        self._running("T9.c1")
        self.assertEqual(self._run(), (4, "blocked: the local lane is full (1 running)"))
        self.assertFalse(os.path.exists(self.log))

    def test_duplicate_label(self):
        self._running("T5.c1")
        self.assertEqual(self._run(), (4, "blocked: T5.c1 is already running"))

    def test_unknown_config_or_alias(self):
        code, out = self._run("nope/deepseek")
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("blocked: unknown external config nope"), out)
        code, out = self._run("local/zzz")
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("blocked: unknown model zzz in local"), out)

    def test_no_sandbox_starts_nothing(self):
        pa_ext.sandbox_missing = lambda: True
        self.assertEqual(self._run(), (3, "blocked: " + pa_ext.SANDBOX_LINE))
        self.assertFalse(os.path.exists(self.log))
        self.assertFalse(os.path.exists(os.path.join(self.ext, "T5.c1.json")))


class SayTest(_RunBase):
    def test_say_to_a_running_coder(self):
        self._running("T5.c1")
        out = []
        self.assertEqual(pa_ext.say("T5.c1", "use the other name", out=out.append, cwd=self.proj), 0)
        self.assertEqual(out, ["queued for T5.c1"])
        inbox = os.path.join(self.ext, "T5.c1", "in")
        files = os.listdir(inbox)
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"\A\d{13}-\d{6}\.json\Z")
        with open(os.path.join(inbox, files[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["text"], "use the other name")

    def test_say_to_a_missing_label(self):
        out = []
        self.assertEqual(pa_ext.say("T7.c1", "hello", out=out.append, cwd=self.proj), 1)
        self.assertEqual(out, ["no running external coder T7.c1"])


class _ModBase(_RunBase):
    def setUp(self):
        _RunBase.setUp(self)
        self.mod = os.path.join(self.proj, ".run", "mod", "parent-sid")
        os.makedirs(self.mod)

    def _json(self, path, nl=True):
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.endswith(b"}\n" if nl else b"}"), raw)     # ext.json: fsutil.locked_update, no newline
        self.assertNotIn(b"\r", raw)
        return json.loads(raw)

    def _journal(self):
        """[(file name, front matter dict, body)] in name order."""
        out = []
        for p in sorted(glob.glob(os.path.join(self.proj, ".claude-state", "journal", "*", "*.md"))):
            self.assertRegex(os.path.basename(os.path.dirname(p)), r"\A\d{4}-\d{2}-\d{2}\Z")
            with open(p, "rb") as fh:
                text = fh.read().decode("utf-8")
            self.assertTrue(text.startswith("---\n"), text)
            front, _, body = text[4:].partition("\n---\n")
            out.append((os.path.basename(p), dict(ln.split(": ", 1) for ln in front.split("\n")), body))
        return out


class ShowTest(_ModBase):
    def test_reasoning_is_journaled_as_an_in_house_coders(self):
        """3.30: each thinking block is one `thinking` entry with pa-watch's fields (ts, session, agentId, model,
        effort, turnId); a block the stream repeats is journaled once; the handoff ask shows on the card feed."""
        os.environ["FAKE_MODE"] = "bigctx"
        self.assertEqual(self._run()[0], 0)
        th = [(n, f, b) for n, f, b in self._journal() if n.endswith("-thinking.md")]
        self.assertEqual(len(th), 1, th)
        name, fields, body = th[0]
        self.assertRegex(name, r"\A\d{6}-\d{3}x-coder-thinking\.md\Z")
        self.assertRegex(fields.pop("ts"), r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(fields, {"session": "parent-sid", "agentId": "ext-T5.c1", "model": DS, "effort": "high",
                                  "turnId": "big"})
        self.assertEqual(body, "The pin sits on $4.\n")
        feed = os.path.join(self.mod, "feed", "in")
        texts = [self._json(os.path.join(feed, n))["text"] for n in sorted(os.listdir(feed))]
        self.assertIn("Context at 400k tokens: asked to hand off", texts)

    def test_feed_facts_journal_and_card(self):
        self._w(os.path.join(self.ext, "T5.c1", "in", "1700000000000-000000.json"),
                json.dumps({"text": "fix the name", "at": 1}))
        os.environ["FAKE_TAIL"] = "\nCommitted abc1234def"
        code, out = self._run()
        self.assertEqual(code, 0, out)
        # feed records
        feed = os.path.join(self.mod, "feed", "in")
        names = os.listdir(feed)
        for n in names:
            self.assertRegex(n, r"\A\d{13}-\d{6}\.json\Z")
        recs = [self._json(os.path.join(feed, n)) for n in sorted(names)]
        for r in recs:
            self.assertEqual((r["agent"], r["type"], r["desc"], r["planning"]), ("ext-T5.c1", "ext-coder", "T5.c1", False))
            self.assertIsInstance(r["at"], int)
        self.assertEqual((recs[0]["text"], recs[0]["kind"]), ("Starting: T5.c1 on DeepSeek V4.1 Flash", "text"))
        texts = [r["text"] for r in recs]
        self.assertIn("I will fix the name.\n\nThen test it.\nThird line.", texts)
        self.assertIn("Correction received: fix the name", texts)
        edits = [r for r in recs if r["kind"] == "edit"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["text"], "Updated src/a.py")
        self.assertEqual(edits[0]["edit"], {"path": "src/a.py", "created": False, "add": 1, "del": 1, "more": 0,
                                            "hunks": ["@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 20\n c = 3"]})
        self.assertLess(texts.index("Updated src/a.py"), texts.index("Correction received: fix the name"))
        # facts
        f = self._json(os.path.join(self.mod, "agents", "ext-T5.c1.json"))
        self.assertEqual((f["model"], f["effort"], f["ctx"], f["type"], f["description"]),
                         ("DeepSeek V4.1 Flash", "high", 327, "ext-coder", "T5.c1"))
        self.assertEqual(f["note"], "I will fix the name.\nThen test it.")
        self.assertEqual(f["steps"], ["Edit src/a.py"])
        self.assertEqual(f["line"], "working")
        self.assertNotIn("parentId", f)
        self.assertIsInstance(f["at"], int)
        self.assertIsInstance(f["updatedAt"], int)
        # journal
        j = self._journal()
        for n, _, _ in j:
            self.assertRegex(n, r"\A\d{6}-\d{3}x-coder-(spawn|note|end)\.md\Z")
        self.assertEqual([n.rsplit("-", 1)[1] for n, _, _ in j], ["spawn.md", "note.md", "end.md"])   # 3.30: end
        self.assertEqual(j[0][1], {"agent": "ext-T5.c1", "type": "ext-coder", "model": DS, "name": "DeepSeek V4.1 Flash",
                                   "effort": "high", "label": "T5.c1", "parent": "parent-sid"})
        self.assertEqual(j[0][2], "External coder T5.c1 started on DeepSeek V4.1 Flash.\n")
        self.assertEqual(j[1][1], {"agent": "ext-T5.c1", "label": "T5.c1"})
        end = dict(j[2][1])
        self.assertRegex(end.pop("ts"), r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(end, {"session": "parent-sid", "agentId": "ext-T5.c1", "role": "pa-coder", "agent": "ext-T5.c1",
                               "label": "T5.c1", "status": "done", "commit": "abc1234def"})
        self.assertTrue(j[2][2].startswith("done: fix the name"), j[2][2])
        # the finish card
        cards = os.listdir(os.path.join(self.mod, "inbox"))
        self.assertEqual(len(cards), 1)
        self.assertRegex(cards[0], r"\A\d+-\d+-card\.json\Z")
        c = self._json(os.path.join(self.mod, "inbox", cards[0]))
        self.assertEqual((c["id"], c["kind"], c["caller"]), (cards[0][:-5], "card", "pa_ext"))
        self.assertEqual(c["input"]["text"].split("\n\n"), [
            "**\U0001F7E9 ✓ External coder · T5.c1 · DeepSeek V4.1 Flash, committed abc1234**",
            "fix the name"])

    def test_cost_file_over_two_runs(self):
        self.assertEqual(self._run()[0], 0)
        e = self._json(os.path.join(self.mod, "ext.json"), False)
        self.assertEqual((e["runs"], e["tokens"], e["models"][DS]["tokens"], e["leaked"]), (1, 3500, 3500, []))
        one = (1000 * 0.098 + 2000 * 0.00294 + 500 * 0.294) / 1e6
        self.assertAlmostEqual(e["usd"], one, places=12)
        k = e["key"]["local"]
        self.assertEqual((k["usage_first"], k["usage"], k["limit_remaining"]), (1.25, 1.25, 9.5))
        self.srv.key_usage = 2.0
        self.assertEqual(self._run()[0], 0)
        e = self._json(os.path.join(self.mod, "ext.json"), False)
        self.assertEqual((e["runs"], e["tokens"], e["models"][DS]["tokens"]), (2, 7000, 7000))
        self.assertGreater(e["usd"], 0)
        self.assertAlmostEqual(e["models"][DS]["usd"], 2 * one, places=12)
        k = e["key"]["local"]
        self.assertEqual((k["usage_first"], k["usage"]), (1.25, 2.0))
        self.assertNotIn(GOOD, json.dumps(e))
        self.assertEqual(e["live"], {})   # each finished run left the live tally

    def test_health_block_kinds_counts_and_placement(self):
        """3.30: denied calls grouped by what a rule must cover (past `cd … &&` and `VAR=`; an interpreter with its
        script's folder), sandbox, read guard, retries and nudges; Claude Code's permission_denials win; the block
        sits between the report and the summary, and the journal's report has none of it."""
        h = pa_ext.Health()
        ev = lambda t, b: {"type": t, "message": {"content": [b]}}   # noqa: E731
        denied = "Permission for this tool use was denied. It requires approval"
        calls = [("Bash", "cd /p && /usr/bin/python3 .run/T1/dis.py a.o", denied),
                 ("Bash", "mipsel-linux-gnu-objdump -drz a.o", denied), ("Bash", "mipsel-linux-gnu-objdump -d b.o", denied),
                 ("Bash", "X=1 bash .run/T1/bin/dt.sh d", denied), ("Write", "/etc/x", denied),
                 ("Bash", "ls", "Exit code 1 bwrap: Can't find source path /h/.mcp.json"),
                 ("Bash", "cat big.c", "PreToolUse:Bash hook error: big.c is 113.4k chars")]
        for i, (tool, arg, text) in enumerate(calls):
            h.record(ev("assistant", {"type": "tool_use", "id": "t%d" % i, "name": tool,
                                      "input": {"file_path" if tool == "Write" else "command": arg}}))
            h.record(ev("user", {"type": "tool_result", "tool_use_id": "t%d" % i, "is_error": True, "content": text}))
        h.record({"type": "system", "subtype": "api_retry"})
        h.nudges = 1
        lines = h.lines()
        self.assertEqual(lines[0], "HEALTH (for the expert: friction a later external coder would hit too):")
        self.assertTrue(lines[1].startswith("- denied 5 (no allow rule covers them): mipsel-linux-gnu-objdump ×2, "
                                            "e.g. `mipsel-linux-gnu-objdump -drz a.o`; python3 .run/… ×1"), lines[1])
        self.assertIn("bash .run/… ×1", lines[1])
        self.assertIn("Write ×1, e.g. `/etc/x`", lines[1])
        self.assertEqual(lines[2:], ["- sandbox (bwrap) failures 1, each re-run",
                                     "- read-guard blocks 1 (whole reads of big files)",
                                     "- API retries 1; nudges after a turn with no report 1"])
        result = [{"tool_name": "Bash", "tool_input": {"command": "make all"}}]
        self.assertIn("- denied 1 (no allow rule covers them): make ×1, e.g. `make all`", h.lines(result))
        self.assertEqual(pa_ext.Health().lines(), [])
        state = pa_ext._new_state()
        state["result"] = {"result": "done: fixed", "num_turns": 3, "permission_denials": result, "usage": {}}
        text, ok = pa_ext.finish(state, 0, "T5.c1", "DeepSeek", DS, h)
        self.assertTrue(ok)
        out = text.split("\n")
        self.assertEqual((out[0], out[1], out[-1]), ("done: fixed", lines[0],
                                                       "external coder T5.c1: DeepSeek · 3 turns · 0 tokens · denials 0"))
        self.assertEqual(state["report"], "done: fixed")

    def test_health_verb_reads_the_runs_transcript(self):
        """3.30: `health <label>` finds the run's transcript by its record's config and child session."""
        sid = "11111111-0000-4000-8000-000000000016"
        self._w(os.path.join(self.ext, "T5.c9.json"), json.dumps({"label": "T5.c9", "config": "local", "child_sid": sid}))
        tr = os.path.join(self.folder, "projects", "-proj", sid + ".jsonl")
        os.makedirs(os.path.dirname(tr), exist_ok=True)
        with open(tr, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "objdump -d a.o"}}]}}) + "\n")
            fh.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "u1", "is_error": True,
                 "content": "Permission for this tool use was denied."}]}}) + "\n")
        out = []
        self.assertEqual(pa_ext.health("T5.c9", out=out.append, cwd=self.proj), 0)
        self.assertIn("objdump ×1, e.g. `objdump -d a.o`", out[0])
        self.assertEqual(pa_ext.health("T5.c0", out=out.append, cwd=self.proj), 1)

    def test_runs_since_the_review_count_table_and_mark(self):
        """3.30: `runs` lists the finished runs ended since the last review with their health and what is in force;
        --count prints the number, --mark stamps the review so the next close counts only newer runs."""
        now = int(time.time())
        self._w(os.path.join(self.ext, "T2.c1.json"), json.dumps({
            "label": "T2.c1", "config": "local", "alias": "deepseek", "name": "DeepSeek V4.1 Flash", "status": "done",
            "started": now - 600, "ended": now - 60, "turns": 40,
            "health": {"denied": {"mipsel-linux-gnu-objdump": 3}, "sandbox": 1, "guard": 0, "retries": 0, "nudges": 0}}))
        self._w(os.path.join(self.ext, "T2.c2.json"), json.dumps({"label": "T2.c2", "status": "running", "started": now}))
        os.makedirs(os.path.join(self.folder, "notes"), exist_ok=True)
        with open(pa_ext.model_notes_path(self.folder, "deepseek"), "w", encoding="utf-8") as fh:
            fh.write("- No cd chains (T10, 2026-10-20)\n")
        with open(os.path.join(self.proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"python": "/x/py3", "external": {"notes": ["Disassemble with tools/dis.py"], "effort": "max",
                                                        "allowed": [{"rule": "Bash(x:*)", "at": "2026-10-21"}]}}, fh)
        out = []
        self.assertEqual(pa_ext.runs(count=True, out=out.append, cwd=self.proj), 0)
        self.assertEqual(out, ["1"])
        out = []
        pa_ext.runs(out=out.append, cwd=self.proj)
        self.assertEqual(out[0], "External coder runs since the last review: 1")
        self.assertEqual(out[1], "- T2.c1 · DeepSeek V4.1 Flash · done · 9 min · 40 turns · denied 3 "
                                 "(mipsel-linux-gnu-objdump 3) · sandbox 1 · read guard 0 · retries 0")
        for want in ("- Disassemble with tools/dis.py", "- Bash(x:*) (added 2026-10-21)",
                     "Model notes for local/deepseek: ", "- No cd chains (T10, 2026-10-20)", 'Settings: {"effort": "max"}'):
            self.assertIn(want, out)
        self.assertEqual(pa_ext.runs(mark=True, out=out.append, cwd=self.proj), 0)
        out = []
        pa_ext.runs(count=True, out=out.append, cwd=self.proj)
        self.assertEqual(out, ["0"])

    def test_the_next_coder_gets_the_project_and_model_notes(self):
        """3.30: append.md carries this project's notes and this model's notes, after the help."""
        os.makedirs(os.path.join(self.folder, "notes"), exist_ok=True)
        with open(pa_ext.model_notes_path(self.folder, "deepseek"), "w", encoding="utf-8") as fh:
            fh.write("- Use each item's full budget (T11, 2026-10-21)\n")
        text = pa_ext.append_text(self.proj, "/x/py3", {"external": {"notes": ["Disassemble with tools/dis.py"]}},
                                  self.folder, "deepseek", "DeepSeek V4.1 Flash")
        self.assertIn("# Notes for external coders in this project\n- Disassemble with tools/dis.py", text)
        self.assertIn("# Notes for coders on DeepSeek V4.1 Flash\n- Use each item's full budget (T11, 2026-10-21)", text)
        self.assertLess(text.index("# Working well in this harness"), text.index("# Notes for external coders"))
        self.assertNotIn("# Notes", pa_ext.append_text(self.proj, "/x/py3"))

    def test_the_next_coder_gets_the_project_memory_index_as_in_house_agents_do(self):
        """3.30.6: append.md ends with the project's auto-memory index (Claude Code's projects/<slug>/memory/MEMORY.md,
        attached to in-house agents at their start), its first 200 lines; its files named only where the coder can
        read them (a memory folder inside the project)."""
        self.assertNotIn("memory index", pa_ext.append_text(self.proj, "/x/py3"))
        slug = pa_ext.paths.project_slug(os.path.abspath(self.proj))
        outside = os.path.join(pa_ext.paths.projects_dir(), slug, "memory")
        os.makedirs(outside)
        with open(os.path.join(outside, "MEMORY.md"), "wb") as fh:
            fh.write(("- [Build quirks](build.md) — make needs -j1\r\n" +
                      "".join("- line %d\n" % i for i in range(300))).encode("utf-8"))
        text = pa_ext.append_text(self.proj, "/x/py3")
        self.assertIn("\n\n# The project's memory index (MEMORY.md; the files it links are outside the project: this "
                      "index is all you get)\n\n- [Build quirks](build.md) — make needs -j1\n- line 0\n", text)
        self.assertTrue(text.endswith("- line 198\n"), text[-60:])     # Claude Code's first 200 lines
        inside = os.path.join(self.proj, ".claude-state", "projects")
        os.makedirs(os.path.join(inside, slug, "memory"))
        with open(os.path.join(inside, slug, "memory", "MEMORY.md"), "w", encoding="utf-8") as fh:
            fh.write("- [Build quirks](build.md)\n")
        real = pa_ext.paths.projects_dir
        pa_ext.paths.projects_dir = lambda: inside
        try:
            text = pa_ext.append_text(self.proj, "/x/py3")
        finally:
            pa_ext.paths.projects_dir = real
        self.assertTrue(text.endswith("# The project's memory index (MEMORY.md; the files it links are in "
                                      ".claude-state/projects/%s/memory/)\n\n- [Build quirks](build.md)\n" % slug), text)

    def test_live_tally_while_running_then_into_the_totals(self):
        """3.30: start() and each heartbeat write live[<label>] (one usage per message id, priced) and the key's first
        reading, so the External line shows while coders run; cost() moves the run into the totals."""
        show = pa_ext.Show(self.proj, "parent-sid", "T5.c2", "DeepSeek V4.1 Flash", DS, "high", "local", self.folder)
        show.start()
        e = self._json(os.path.join(self.mod, "ext.json"), False)
        self.assertEqual((e["live"]["T5.c2"]["tokens"], e["live"]["T5.c2"]["usd"]), (0, 0))
        self.assertEqual((e["key"]["local"]["usage_first"], e["key"]["local"]["limit_remaining"]), (1.25, 9.5))
        msg = lambda mid, out: {"type": "assistant", "message": {   # noqa: E731
            "id": mid, "model": DS, "content": [], "usage": {"input_tokens": 1000, "cache_read_input_tokens": 2000,
                                                             "output_tokens": out}}}
        for ev in (msg("m1", 10), msg("m1", 500), msg("m2", 500)):   # m1's content blocks: the last write counts
            show.event(ev)
        show.live()
        live = self._json(os.path.join(self.mod, "ext.json"), False)["live"]["T5.c2"]
        self.assertEqual(live["tokens"], 7000)
        self.assertAlmostEqual(live["usd"], 2 * (1000 * 0.098 + 2000 * 0.00294 + 500 * 0.294) / 1e6, places=12)
        show.cost({"modelUsage": {DS: {"inputTokens": 2000, "cacheReadInputTokens": 4000, "outputTokens": 1000}}}, [])
        e = self._json(os.path.join(self.mod, "ext.json"), False)
        self.assertEqual((e["live"], e["runs"], e["tokens"]), ({}, 1, 7000))

    def test_no_mod_side_writes_without_the_mod_folder(self):
        shutil.rmtree(self.mod)
        self.assertEqual(self._run()[0], 0)
        self.assertFalse(os.path.exists(self.mod))
        self.assertEqual(len(self._journal()), 3)


class AskTest(_ModBase):
    def setUp(self):
        _ModBase.setUp(self)
        os.environ.update({"PA_EXT_PARENT": "parent-sid", "PA_EXT_LABEL": "T5.c1"})

    def _ask(self):
        out = []
        code = pa_ext.ask("where is run?", out=out.append, cwd=self.proj)
        return code, out

    def test_request_and_answer(self):
        seen = []

        def answer():
            inbox = os.path.join(self.mod, "inbox")
            for _ in range(100):
                names = [n for n in os.listdir(inbox) if n.endswith(".json")] if os.path.isdir(inbox) else []
                if names:
                    req = self._json(os.path.join(inbox, names[0]))
                    seen.append((names[0], req))
                    self._w(req["input"]["answer"], "run is at tools/pa_ext.py:1\n")
                    time.sleep(0.3)                 # an answer with no .done marker yet is never read
                    self._w(req["input"]["answer"] + ".done", "")
                    return
                time.sleep(0.05)
        th = threading.Thread(target=answer)
        th.start()
        code, out = self._ask()
        th.join()
        self.assertEqual((code, out), (0, ["run is at tools/pa_ext.py:1"]))
        name, req = seen[0]
        self.assertRegex(name, r"\A\d+-\d+-retrieve\.json\Z")
        self.assertEqual((req["id"], req["kind"], req["caller"]), (name[:-5], "retrieve", "pa_ext"))
        self.assertEqual((req["input"]["question"], req["input"]["label"]), ("where is run?", "T5.c1"))
        self.assertEqual(req["input"]["answer"],
                         os.path.abspath(os.path.join(self.mod, "ext", "answers", req["id"] + ".md")).replace("\\", "/"))

    def test_timeout(self):
        os.environ["PA_EXT_ASK_TIMEOUT"] = "1"
        self.assertEqual(self._ask(), (0, ["no answer within 1 s; go on without it"]))

    def test_outside_an_external_coder(self):
        os.environ.pop("PA_EXT_PARENT")
        self.assertEqual(self._ask(), (1, ["ask works only inside an external coder"]))


class ResumeTest(_ModBase):
    def _resume(self):
        out = []
        code = pa_ext.run(None, None, out=out.append, cwd=self.proj, resume="T5.c1")
        return code, "\n".join(out)

    def test_resume_with_the_transcript(self):
        self.assertEqual(self._run()[0], 0)
        sid = self._record()["child_sid"]
        self.assertTrue(self._record()["brief"].endswith("/.run/T5.c1.md"))
        self._w(os.path.join(self.folder, "projects", "Z--proj", sid + ".jsonl"), "{}\n")
        code, out = self._resume()
        self.assertEqual(code, 0, out)
        a = self._fake()["argv"]
        self.assertEqual(a[a.index("--resume") + 1], sid)
        self.assertNotIn("--session-id", a)
        self.assertEqual(self._fake()["msgs"][0], pa_ext.RESUME_TEXT)

    def test_resume_without_the_transcript(self):
        self.assertEqual(self._run()[0], 0)
        sid = self._record()["child_sid"]
        code, out = self._resume()
        self.assertEqual(code, 0, out)
        a = self._fake()["argv"]
        self.assertNotIn("--resume", a)
        self.assertNotEqual(a[a.index("--session-id") + 1], sid)
        first = self._fake()["msgs"][0]
        self.assertTrue(first.startswith("CODER TASK T5.c1 · do it\nCHANGE: x\n\nA previous run of this task was cut "
                                         "off. What it did:\n"), first)
        self.assertIn("I will fix the name.", first)
        self.assertIn("done: CODER TASK T5.c1 · do it", first)

    def test_resume_unknown_label(self):
        self.assertEqual(self._resume(), (3, "blocked: no earlier run of T5.c1 to resume"))


class JudgeRunTest(_ModBase):
    """3.30.1: what PA3's judge decided shows in the run's HEALTH and record; what it learned reaches every later
    coder, `runs` lists it and `set --forget` drops it; declared folders are working directories."""

    def test_a_judged_run_shows_the_judge_in_health_and_the_record(self):
        os.environ["FAKE_MODE"] = "judged"
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn("- PA3's judge allowed 1, refused 0; learned for this project: "
                      "`Bash(mipsel-linux-gnu-objdump:*)`; refused by the fixed rules: network 1, outside the project 0 "
                      "(e.g. curl x: network: curl)", out.splitlines())
        rec = self._record()
        self.assertEqual(rec["health"]["judge"], {"allowed": 1, "denied": 0, "network": 1, "outside": 0, "again": 0,
                                                  "judge_s": 4.2, "learned": ["Bash(mipsel-linux-gnu-objdump:*)"]})
        self.assertFalse(pa_ext.run_clean(self.proj, rec))

    def test_learned_rules_reach_the_next_coder_runs_lists_them_and_forget_drops_them(self):
        from pa import ext_rules
        ext_rules.remember(self.proj, "allow", {"rule": "Bash(mipsel-linux-gnu-objdump:*)", "at": int(time.time()),
                                                "label": "T5.c0", "example": "mipsel-linux-gnu-objdump -d a.o"})
        code, out = self._run()
        self.assertEqual(code, 0, out)
        a = self._fake()["argv"]
        self.assertIn("Bash(mipsel-linux-gnu-objdump:*)", a[a.index("--allowedTools") + 1].split(","))
        out = []
        pa_ext.runs(out=out.append, cwd=self.proj)
        self.assertIn("Learned by PA3's judge (`set --forget <rule>` drops one): ", out)
        self.assertIn("- Bash(mipsel-linux-gnu-objdump:*) (%s, T5.c0: `mipsel-linux-gnu-objdump -d a.o`)"
                      % time.strftime("%Y-%m-%d"), out)
        self.assertIn("Learning mode (a model runs one coder at a time in this project until a run comes back clean):",
                      out)
        self.assertIn("- local/deepseek: learning: the last one, T5.c1, met friction", out)   # the fake's denial
        said = []
        self.assertEqual(pa_ext.set_external(forget="Bash(mipsel-linux-gnu-objdump:*)", out=said.append,
                                             cwd=self.proj), 0)
        self.assertEqual(ext_rules.allow_rules(self.proj), [])
        self.assertEqual(pa_ext.set_external(forget="Bash(mipsel-linux-gnu-objdump:*)", out=said.append,
                                             cwd=self.proj), 1)

    def test_declared_folders_are_working_directories(self):
        ghidra = os.path.join(self.home, "ghidra")
        os.makedirs(ghidra)
        with open(os.path.join(self.proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"python": "/x/py3", "external": {"dirs": ["~/ghidra", "~/missing"]}}, fh)
        code, out = self._run()
        self.assertEqual(code, 0, out)
        fake = self._fake()
        a = fake["argv"]
        self.assertEqual(a[a.index("--add-dir") + 1], os.path.abspath(ghidra))
        self.assertEqual(a.count("--add-dir"), 1)
        self.assertEqual(fake["env"]["PA_EXT_DIRS"], os.path.abspath(ghidra))

    def test_tool_lists_drop_the_projects_wide_file_rules(self):
        sets = [{"permissions": {"allow": ["Read", "Edit(//etc/**)", "Read(~/x)", "Write(C:/x)", "Read(./src/**)",
                                           "Bash(make:*)"], "deny": ["Bash(rm -rf:*)"]}}]
        allow, deny = pa_ext.tool_lists(sets, {}, ["Bash(x:*)"])
        self.assertEqual(allow, ["Edit(/**)", "Read(./src/**)", "Bash(make:*)", "Bash(x:*)"])
        self.assertEqual(deny, ["Agent", "WebFetch", "WebSearch", "Bash(rm -rf:*)"])


class LearningTest(_RunBase):
    """3.30.1, learning mode: a model runs one coder at a time in a project until its last finished run there came
    back clean; a project with one lane sees no change."""

    def _rec(self, label, **kw):
        now = int(time.time())
        rec = {"label": label, "config": "local", "alias": "deepseek", "name": "DeepSeek V4.1 Flash", "status": "done",
               "started": now - 600, "ended": now - 60, "report_word": True,
               "health": {"denied": {}, "sandbox": 0, "guard": 0, "retries": 0, "nudges": 0}}
        rec.update(kw)
        self._w(os.path.join(self.ext, label + ".json"), json.dumps(rec))
        return rec

    def _alive(self, label, alias="deepseek"):
        self._w(os.path.join(self.ext, label + ".json"), json.dumps(
            {"label": label, "config": "local", "alias": alias, "pid": os.getpid(), "status": "running"}))

    def test_one_at_a_time_until_a_run_comes_back_clean(self):
        self._alive("T9.c1")
        self.assertEqual(self._run(), (4, "blocked: learning mode: DeepSeek V4.1 Flash runs one coder at a time in "
                                          "this project until a run comes back clean (no run of it has finished in "
                                          "this project yet); start T5.c1 again when T9.c1 reports"))
        self.assertFalse(os.path.exists(self.log))
        self._rec("T8.c1")
        code, out = self._run()
        self.assertEqual(code, 0, out)

    def test_friction_or_a_note_after_the_run_keeps_it_learning(self):
        self._alive("T9.c1")
        for health, kw in ((dict(denied={"Bash": 1}), {}), (dict(guard=1), {}), (dict(nudges=1), {}),
                           ({"judge": {"denied": 1}}, {}), ({}, {"report_word": False}),
                           ({}, {"status": "blocked"})):
            rec = self._rec("T8.c1", **kw)
            rec["health"].update(health)
            self._w(os.path.join(self.ext, "T8.c1.json"), json.dumps(rec))
            code, out = self._run()
            self.assertEqual(code, 4, (health, kw, out))
            self.assertIn("(the last one, T8.c1, met friction)", out)
        rec = self._rec("T8.c1", ended=int(time.time()) - 60)
        self.assertTrue(pa_ext.run_clean(self.proj, rec))
        # 3.30.4: what the judge allowed or learned is no friction (bfm: each batch's scratch scripts were learned)
        rec["health"]["judge"] = {"allowed": 170, "denied": 0, "network": 0, "outside": 0,
                                  "learned": ["Bash(make:*)", "Bash(bash .run/P40/T11/RM2/_tools/score.sh:*)"]}
        self.assertTrue(pa_ext.run_clean(self.proj, rec))
        rec["health"]["sandbox"] = 1      # 3.30.7: two coders' sandboxes colliding (bfm G18) is no friction either
        self.assertTrue(pa_ext.run_clean(self.proj, rec))
        pa_ext._log_note(self.proj, "model", "local/glm", "another model's note")
        self.assertTrue(pa_ext.run_clean(self.proj, rec))
        pa_ext._log_note(self.proj, "model", "local/deepseek", "use the whole budget")
        self.assertFalse(pa_ext.run_clean(self.proj, rec))
        self.assertEqual(self._run()[0], 4)

    def test_one_lane_and_other_models_see_no_change(self):
        self._w(os.path.join(self.proj, ".claude", "pa.json"), json.dumps({"external": {"lanes": {"local": 1}}}))
        self._alive("T9.c1")
        self.assertEqual(self._run(), (4, "blocked: the local lane is full (1 running)"))
        self._w(os.path.join(self.proj, ".claude", "pa.json"), json.dumps({"python": "/x/py3"}))
        self._alive("T9.c1", alias="glm")
        code, out = self._run()
        self.assertEqual(code, 0, out)


class WrapperTextTest(unittest.TestCase):
    def test_cmd(self):
        with open(os.path.join(HERE, "setup-external.cmd"), "rb") as fh:
            raw = fh.read()
        text = raw.decode("ascii")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertNotIn(b"\r", raw.replace(b"\r\n", b""))
        self.assertIn('set "PA3_DOUBLECLICK=1"', text)
        self.assertIn("if defined PA3_DOUBLECLICK pause", text)
        self.assertIn('tools\\pa_ext.py" setup', text)

    def test_sh(self):
        with open(os.path.join(HERE, "setup-external.sh"), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.startswith(b"#!/usr/bin/env sh\n"))
        self.assertIn(b'tools/pa_ext.py" setup', raw)


TEMPLATE = os.path.join(HERE, "templates", "pa.json.template")


class SetTest(_Base):
    """3.3 T6: `set` writes only pa.json's external block, checked against this machine's configs."""

    def setUp(self):
        _Base.setUp(self)
        self.proj = os.path.join(self.home, "proj")
        os.makedirs(os.path.join(self.proj, ".claude"))
        self.pa = os.path.join(self.proj, ".claude", "pa.json")
        with open(TEMPLATE, "rb") as fh:
            self.orig = fh.read().replace(b"{{PROJECT_NAME}}", b"proj").replace(b"{{PY}}", b"/x/py3")
        with open(self.pa, "wb") as fh:
            fh.write(self.orig)
        pa_ext._write_meta(os.path.join(self.root, "openrouter-deepseek"), "openrouter-deepseek", self.url,
                           {"deepseek": {"id": DS, "name": "DeepSeek"}})

    def set(self, **kw):
        out = []
        code = pa_ext.set_external(out=out.append, cwd=self.proj, **kw)
        return code, "\n".join(out)

    def raw(self):
        with open(self.pa, "rb") as fh:
            return fh.read()

    def test_insert_keeps_every_other_byte(self):
        code, out = self.set(coder="openrouter-deepseek/deepseek", lanes="openrouter-deepseek=4")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "external: coder openrouter-deepseek/deepseek, lanes openrouter-deepseek=4")
        raw = self.raw()
        blob = b',\n  "external": {"coder": "openrouter-deepseek/deepseek", "lanes": {"openrouter-deepseek": 4}}'
        cut = self.orig.rstrip().rfind(b"}")
        self.assertEqual(raw, self.orig[:cut].rstrip() + blob + self.orig[len(self.orig[:cut].rstrip()):])
        self.assertNotIn(b"\r", raw)

    def test_health_proposals_notes_model_notes_allow(self):
        """3.30: approved proposals: project notes in pa.json (deduped, dropped by number), a model's notes in its
        config folder, an allow rule in the project's settings.json (never a bare Bash), each stored once."""
        self.assertEqual(self.set(note="Disassemble with  tools/dis.py, not objdump (T11)"), (0, "external: note added"))
        self.set(note="Disassemble with tools/dis.py, not objdump (T11)")
        self.set(note="Second note")
        self.assertEqual(json.loads(self.raw())["external"]["notes"],
                         ["Disassemble with tools/dis.py, not objdump (T11)", "Second note"])
        self.assertEqual(self.set(drop_note="1"), (0, "external: dropped note 1"))
        self.assertEqual(json.loads(self.raw())["external"]["notes"], ["Second note"])
        self.assertEqual(self.set(drop_note="5")[0], 1)
        code, out = self.set(model_note=("openrouter-deepseek/deepseek", "Use the full budget (T11, 2026-10-21)"))
        self.assertEqual(code, 0, out)
        self.set(model_note=("openrouter-deepseek/deepseek", "Use the full budget (T11, 2026-10-21)"))
        path = pa_ext.model_notes_path(os.path.join(self.root, "openrouter-deepseek"), "deepseek")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "- Use the full budget (T11, 2026-10-21)\n")
        self.assertEqual(self.set(model_note=("nowhere/x", "y"))[0], 1)
        before = self.raw()
        self.assertEqual(self.set(allow="Bash(mipsel-linux-gnu-objdump:*)"),
                         (0, "external: allow Bash(mipsel-linux-gnu-objdump:*) in .claude/settings.json"))
        self.set(allow="Bash(mipsel-linux-gnu-objdump:*)")
        with open(os.path.join(self.proj, ".claude", "settings.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["permissions"]["allow"], ["Bash(mipsel-linux-gnu-objdump:*)"])
        # 3.30: pa.json keeps which rules health proposals added, and since when, for the phase-end review
        self.assertEqual(json.loads(self.raw())["external"]["allowed"],
                         [{"rule": "Bash(mipsel-linux-gnu-objdump:*)", "at": time.strftime("%Y-%m-%d")}])
        before = self.raw()
        self.set(model_note=("openrouter-deepseek/deepseek", "Another note"))
        self.assertEqual(self.raw(), before)   # a model note never touches pa.json
        for bad in ("Bash", "Bash(*)", "rm -rf /"):
            self.assertEqual(self.set(allow=bad)[0], 1, bad)

    def test_notes_are_logged_and_reach_claude_as_an_fyi_with_their_undo(self):
        """3.30.1: the expert applies notes itself; each new one is logged (learning mode reads when) and sent to
        Claude in the parent session as a one-line notice naming its undo; drop-model-note removes one by number."""
        old = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "psid"
        inbox = os.path.join(self.proj, ".run", "mod", "psid", "inbox")
        os.makedirs(os.path.join(self.proj, ".run", "mod", "psid"))
        try:
            self.assertEqual(self.set(note="Use the whole time budget (T11.cG05)"), (0, "external: note added"))
            self.set(note="Use the whole time budget (T11.cG05)")       # the same note: no second notice
            code, out = self.set(model_note=("openrouter-deepseek/deepseek", "Never chain cd (T10)"))
            self.assertEqual(code, 0, out)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old
        reqs = []
        for f in sorted(os.listdir(inbox)):
            with open(os.path.join(inbox, f), encoding="utf-8") as fh:
                reqs.append(json.load(fh))
        self.assertEqual([r["kind"] for r in reqs], ["notice", "notice"])
        self.assertEqual(reqs[0]["input"]["text"], "‹pa› FYI, nothing to ask: a note was added for every external "
                         "coder in this project: “Use the whole time budget (T11.cG05)”. Undo: `/x/py3 "
                         "tools/pa_ext.py set --drop-note 1`. Tell the developer in one line.")
        self.assertIn("every coder on openrouter-deepseek/deepseek, in every project: “Never chain cd (T10)”. Undo: "
                      "`/x/py3 tools/pa_ext.py set --drop-model-note openrouter-deepseek/deepseek 1`",
                      reqs[1]["input"]["text"])
        logged = pa_ext.notes_log(self.proj)
        self.assertEqual([(n["kind"], n["target"], n["text"]) for n in logged],
                         [("project", "", "Use the whole time budget (T11.cG05)"),
                          ("model", "openrouter-deepseek/deepseek", "Never chain cd (T10)")])
        self.assertEqual(self.set(drop_model_note=("openrouter-deepseek/deepseek", "2"))[0], 1)
        self.assertEqual(self.set(drop_model_note=("openrouter-deepseek/deepseek", "1"))[0], 0)
        path = pa_ext.model_notes_path(os.path.join(self.root, "openrouter-deepseek"), "deepseek")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_folders_outside_the_project_for_coders_to_read(self):
        """3.30.1: `--dir` declares a folder its tools read (checked: it exists, is no root, home or secrets folder,
        not inside the project); `--drop-dir` removes it."""
        os.makedirs(os.path.join(self.home, "ghidra"))
        os.makedirs(os.path.join(self.home, ".ssh"))
        os.makedirs(os.path.join(self.proj, "src"))
        self.assertEqual(self.set(add_dir="~/ghidra"), (0, "external: folder ~/ghidra for coders to read"))
        self.assertEqual(json.loads(self.raw())["external"]["dirs"], ["~/ghidra"])
        for bad in ("~", "~/.ssh", os.path.join(self.proj, "src"), "~/missing", os.path.dirname(self.home)):
            self.assertEqual(self.set(add_dir=bad)[0], 1, bad)
        self.assertEqual(self.set(drop_dir="~/ghidra"), (0, "external: dropped folder ~/ghidra"))
        self.assertEqual(json.loads(self.raw())["external"]["dirs"], [])
        self.assertEqual(self.set(drop_dir="~/ghidra")[0], 1)

    def test_effort(self):
        code, out = self.set(effort="max")
        self.assertEqual((code, out), (0, "external: effort max"))
        self.assertEqual(json.loads(self.raw())["external"], {"effort": "max"})
        self.assertEqual(self.set(effort="ultra")[0], 1)
        self.assertEqual(json.loads(self.raw())["external"], {"effort": "max"})
        self.assertEqual(self.set(effort="default"), (0, "external: effort default"))
        self.assertEqual(json.loads(self.raw())["external"], {})

    def test_replace_existing_block(self):
        self.set(coder="openrouter-deepseek/deepseek", max_coders="3")
        before = self.raw()
        code, out = self.set(use="off", escalate="in-house,openrouter-deepseek/deepseek")
        self.assertEqual(code, 0, out)
        doc = json.loads(self.raw())
        self.assertEqual(doc["external"], {"coder": "openrouter-deepseek/deepseek", "max_coders": 3, "use": False,
                                           "escalate": ["in-house", "openrouter-deepseek/deepseek"]})
        head = before[:before.index(b'"external"')]
        self.assertTrue(self.raw().startswith(head))
        self.assertTrue(self.raw().endswith(b"}\n"))
        code, out = self.set(coder="in-house")
        self.assertEqual(code, 0, out)
        self.assertNotIn("coder", json.loads(self.raw())["external"])

    def test_refusals_write_nothing(self):
        for kw, word in (({"coder": "openrouter-deepseek/glm"}, "no external coder openrouter-deepseek/glm"),
                         ({"coder": "nope/deepseek"}, "run setup-external first"),
                         ({"escalate": "in-house,nope/x"}, "no external coder nope/x"),
                         ({"lanes": "elsewhere=2"}, "unknown lane elsewhere"),
                         ({"lanes": "in-house=17"}, "bad count 17"),
                         ({"max_coders": "-1"}, "bad count -1"),
                         ({"max_coders": "two"}, "bad count two")):
            code, out = self.set(**kw)
            self.assertEqual(code, 1, kw)
            self.assertIn(word, out)
            self.assertEqual(len(out.splitlines()), 1, out)
            self.assertEqual(self.raw(), self.orig, kw)

    def test_show(self):
        code, out = self.set()
        self.assertEqual((code, out), (0, "external coders: not set up in this project"))
        self.set(lanes="in-house=2")
        code, out = self.set()
        self.assertEqual(json.loads(out), {"lanes": {"in-house": 2}})
        self.assertIn("\n", out)

    def test_brackets_in_strings(self):
        with open(self.pa, "wb") as fh:
            fh.write(b'{\n  "a": "x}{\\"]",\n  "external": {"note": "}\\\\", "lanes": {}},\n  "b": [1, {"c": "]"}]\n}\n')
        code, out = self.set(max_coders="2")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.raw(), b'{\n  "a": "x}{\\"]",\n  "external": {"note": "}\\\\", "lanes": {}, '
                                     b'"max_coders": 2},\n  "b": [1, {"c": "]"}]\n}\n')


if __name__ == "__main__":
    unittest.main()
