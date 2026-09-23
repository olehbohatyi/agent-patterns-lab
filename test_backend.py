"""Tests for the local/API backend switch in agent_common.py. No live API calls: the
`anthropic` module is replaced by a fake, and `subprocess.run` by a stub."""
import subprocess
import sys
import types

import pytest

import agent_common
from agent_common import (
    API_MODEL_IDS, call_claude, get_backend, parse_cli, resolve_api_model, set_backend,
)


class FakeBlock:
    def __init__(self, type, text=None):
        self.type = type
        if text is not None:
            self.text = text


class FakeMessage:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(agent_common, "_backend", "local")
    monkeypatch.setattr(agent_common, "_api_client", None)
    yield
    monkeypatch.setattr(agent_common, "_backend", "local")


@pytest.fixture
def fake_sdk(monkeypatch):
    """Installs a fake `anthropic` module; returns a record of constructions and calls."""
    record = {"clients": [], "messages": FakeMessages(FakeMessage([FakeBlock("text", "  hello  ")]))}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            record["clients"].append(kwargs)
            self.messages = record["messages"]

    module = types.ModuleType("anthropic")
    module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return record


def test_default_backend_is_local():
    assert get_backend() == "local"


def test_set_backend_rejects_unknown_names():
    with pytest.raises(ValueError):
        set_backend("bogus")


def test_local_backend_uses_the_cli(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return types.SimpleNamespace(stdout="  from cli \n", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert call_claude("hi", model="haiku") == "from cli"
    assert seen["cmd"] == ["claude", "--model", "haiku", "-p", "hi"]
    assert seen["kwargs"]["timeout"] == 120


def test_api_backend_maps_aliases_and_extracts_text(fake_sdk):
    set_backend("api")
    assert call_claude("hi", model="sonnet") == "hello"
    call = fake_sdk["messages"].calls[0]
    assert call["model"] == API_MODEL_IDS["sonnet"]
    assert call["max_tokens"] == agent_common.API_MAX_TOKENS
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_api_backend_passes_unknown_model_names_through(fake_sdk):
    set_backend("api")
    call_claude("hi", model="claude-some-future-model")
    assert fake_sdk["messages"].calls[0]["model"] == "claude-some-future-model"
    assert resolve_api_model("haiku") == "claude-haiku-4-5-20251001"


def test_api_backend_ignores_non_text_blocks(fake_sdk):
    fake_sdk["messages"].reply = FakeMessage(
        [FakeBlock("thinking"), FakeBlock("text", "part one "), FakeBlock("text", "part two")]
    )
    set_backend("api")
    assert call_claude("hi") == "part one part two"


def test_api_backend_warns_on_truncation(fake_sdk, capsys):
    fake_sdk["messages"].reply = FakeMessage([FakeBlock("text", "cut")], stop_reason="max_tokens")
    set_backend("api")
    assert call_claude("hi") == "cut"
    assert "max_tokens" in capsys.readouterr().err


def test_api_client_is_created_once_with_a_timeout(fake_sdk):
    set_backend("api")
    call_claude("a")
    call_claude("b")
    assert len(fake_sdk["clients"]) == 1
    assert fake_sdk["clients"][0] == {"timeout": agent_common.API_TIMEOUT_SECONDS}


def test_api_client_never_receives_a_key_from_code(fake_sdk):
    set_backend("api")
    call_claude("a")
    assert "api_key" not in fake_sdk["clients"][0]


def test_missing_sdk_gives_an_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail
    set_backend("api")
    with pytest.raises(RuntimeError, match="pip install anthropic"):
        call_claude("hi")


def test_api_errors_propagate(fake_sdk):
    class Boom(Exception):
        pass

    def create(**kwargs):
        raise Boom("rate limited")

    fake_sdk["messages"].create = create
    set_backend("api")
    with pytest.raises(Boom):
        call_claude("hi")


def test_parse_cli_defaults_to_local(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_x.py", "reverse a string"])
    assert parse_cli() == "reverse a string"
    assert get_backend() == "local"


@pytest.mark.parametrize("argv", [
    ["agent_x.py", "--backend", "api", "reverse a string"],
    ["agent_x.py", "reverse a string", "--backend", "api"],
])
def test_parse_cli_selects_api_in_either_position(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    assert parse_cli() == "reverse a string"
    assert get_backend() == "api"


def test_parse_cli_requires_a_task(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_x.py", "--backend", "api"])
    with pytest.raises(SystemExit) as exc:
        parse_cli()
    assert exc.value.code == 2


def test_parse_cli_rejects_unknown_backend(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_x.py", "--backend", "bogus", "task"])
    with pytest.raises(SystemExit) as exc:
        parse_cli()
    assert exc.value.code == 2
