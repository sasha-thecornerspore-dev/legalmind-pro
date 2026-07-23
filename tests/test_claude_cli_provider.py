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
    assert seen["args"][:2] == ["/usr/bin/claude", "-p"]
    assert "--model" in seen["args"] and "sonnet" in seen["args"]
    assert seen["input"] == "analyze this evidence"   # piped, not an argv


def test_nonzero_exit_is_reported(monkeypatch):
    def fake_run(args, input=None, **kw):
        return types.SimpleNamespace(returncode=1, stdout="",
                                     stderr="not logged in")
    monkeypatch.setattr(core, "_claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = core.query_claude_cli("hi")
    assert "error" in out.lower() and "not logged in" in out


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
