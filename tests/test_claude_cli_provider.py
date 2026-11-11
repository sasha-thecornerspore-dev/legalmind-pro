"""Claude subscription (Claude Code CLI) provider."""
import subprocess
import types

import core


def test_registered_and_labeled():
    assert "claude-cli" in core.LLM_PROVIDERS
    assert core.PROVIDER_LABELS["claude-cli"] == "Claude (subscription)"


def test_missing_cli_returns_helpful_error(monkeypatch):
    monkeypatch.setattr(core, "_claude_cli_path", lambda: "")
    out = core.query_claude_cli("hello")
    assert "not found" in out.lower() and "claude.com/claude-code" in out
    assert not core.check_claude_cli()


def test_query_pipes_prompt_via_stdin(monkeypatch):
    seen = {}

    def fake_run(args, input=None, **kw):
        seen["args"] = args
        seen["input"] = input
        return types.SimpleNamespace(returncode=0, stdout="the answer", stderr="")

    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = core.query_claude_cli("analyze this evidence", model="sonnet")
    assert out == "the answer"
    assert seen["args"][0] == "/usr/bin/claude"
    assert "-p" in seen["args"]
    assert "--strict-mcp-config" in seen["args"]      # skips MCP auto-load
    assert "--bare" not in seen["args"]               # --bare disables OAuth
    assert "--model" in seen["args"] and "sonnet" in seen["args"]
    assert seen["input"] == "analyze this evidence"   # piped, not an argv


def test_nonzero_exit_is_reported(monkeypatch):
    def fake_run(args, input=None, **kw):
        return types.SimpleNamespace(returncode=1, stdout="",
                                     stderr="usage limit reached")
    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = core.query_claude_cli("hi")
    # (A missing login gets its own message -- see
    # test_not_logged_in_explains_how_to_sign_in.)
    assert "error" in out.lower() and "usage limit reached" in out


def test_dispatch_routes_to_cli(monkeypatch):
    monkeypatch.setattr(core, "query_claude_cli",
                        lambda prompt, model="": f"CLI:{prompt}")
    assert core.query_llm("x", provider="claude-cli") == "CLI:x"
    assert core.query_llm("x", provider="claude-subscription") == "CLI:x"


def test_stream_flattens_and_emits(monkeypatch):
    import io

    class FakeProc:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = iter(["Motion ", "to ", "dismiss."])
            self.stderr = io.StringIO()
            self.returncode = 0
        def wait(self, timeout=None): pass

    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    got = []
    out = core.query_claude_cli_stream(
        [{"role": "user", "content": "summarize"}], got.append)
    assert "".join(got) == "Motion to dismiss."
    assert out == "Motion to dismiss."


def test_subscription_env_strips_api_key(monkeypatch):
    """The subscription CLI must not inherit ANTHROPIC_API_KEY, or Claude Code
    bills the API ('Credit balance is too low') instead of the subscription."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    env = core._claude_cli_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "PATH" in env or "Path" in env   # real env otherwise preserved


def test_query_passes_stripped_env(monkeypatch):
    import subprocess, types
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    seen = {}

    def fake_run(args, input=None, env=None, **kw):
        seen["env"] = env
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    core.query_claude_cli("hi")
    assert seen["env"] is not None and "ANTHROPIC_API_KEY" not in seen["env"]


# ── Regressions: "Not logged in" + `--model gemma` sent to the Claude CLI ─────

def test_cli_model_never_inherits_another_providers_model():
    """The GUI resolved the model as claude / openai / else-Ollama, so the
    subscription provider was launched with `--model gemma`."""
    assert core.model_for_provider("claude-cli", {}) == ""
    assert core.model_for_provider("claude-cli", {"ollama_model": "gemma"}) == ""
    assert core.model_for_provider(
        "claude-subscription", {"ollama_model": "llama3",
                                "claude_model": "claude-sonnet-4-20250514"}) == ""
    assert core.model_for_provider(
        "claude-cli", {"claude_cli_model": "opus"}) == "opus"


def test_model_for_other_providers_unchanged():
    s = {"ollama_model": "llama3", "claude_model": "c-x", "openai_model": "o-x"}
    assert core.model_for_provider("ollama", s) == "llama3"
    assert core.model_for_provider("claude", s) == "c-x"
    assert core.model_for_provider("openai", s) == "o-x"
    assert core.model_for_provider("ollama", {}) == core.DEFAULT_OLLAMA_MODEL
    assert core.model_for_provider("claude", {}) == core.DEFAULT_CLAUDE_MODEL
    assert core.model_for_provider("openai", {}) == core.DEFAULT_OPENAI_MODEL


def test_gui_resolves_model_through_core():
    """The GUI must not carry its own provider->model ladder again."""
    import os
    gui = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "gui.py")
    src = open(gui, encoding="utf-8").read()
    assert "model_for_provider(provider" in src


def test_cli_never_uses_bare():
    """`--bare` makes Claude Code ignore OAuth/keychain, i.e. the subscription
    login this provider exists for, so every call answers 'Not logged in'."""
    for lean in (True, False):
        assert "--bare" not in core._claude_cli_args("/usr/bin/claude", "", lean)
    assert "--strict-mcp-config" in core._claude_cli_args(
        "/usr/bin/claude", "", True)
    assert "--strict-mcp-config" not in core._claude_cli_args(
        "/usr/bin/claude", "", False)


def test_old_cli_without_lean_flag_is_retried(monkeypatch):
    calls = []

    def fake_run(args, input=None, **kw):
        calls.append(args)
        if "--strict-mcp-config" in args:
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="error: unknown option '--strict-mcp-config'")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert core.query_claude_cli("hi") == "ok"
    assert len(calls) == 2


def test_not_logged_in_explains_how_to_sign_in(monkeypatch):
    """The CLI prints 'Not logged in · Please run /login' on STDOUT with an
    empty stderr. '/login' means nothing inside LegalMind, so say where to
    type it."""
    def fake_run(args, input=None, **kw):
        return types.SimpleNamespace(
            returncode=1, stdout="Not logged in · Please run /login\n", stderr="")
    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = core.query_claude_cli("hi")
    assert "not signed in" in out.lower()
    assert "terminal" in out.lower() and "/login" in out


def test_stream_not_logged_in_explains_how_to_sign_in(monkeypatch):
    import io

    class FakeProc:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = iter(["Not logged in · Please run /login\n"])
            self.stderr = io.StringIO("")
            self.returncode = 1
        def wait(self, timeout=None): pass

    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    got = []
    out = core.query_claude_cli_stream(
        [{"role": "user", "content": "hi"}], got.append)
    assert "not signed in" in out.lower() and "terminal" in out.lower()
    assert "".join(got).strip() == out          # the user saw the same thing
    assert "[Claude CLI error: 1]" not in out   # no bare exit code
