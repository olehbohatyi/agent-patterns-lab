"""Tests for the --judge {llm,jev} switch and the Jev judge in agent_review.py. No live
calls: the `typesafe_sdk` module is replaced by a fake that mirrors the real SDK's
names (checked against typesafe-sdk 0.7.1), and call_claude by a stub."""
import sys
import types

import pytest

import agent_common
import agent_review
from agent_common import get_judge, parse_cli, set_judge
from agent_review import JEV_BLOCK_THRESHOLD, aggregate_verdict, judge_review


class FakeTypeSafeError(Exception):
    pass


class FakeAPIError(FakeTypeSafeError):
    def __init__(self, msg, status=500, request_id="req_1"):
        super().__init__(f"{status} {msg} (request_id={request_id})")
        self.status, self.request_id = status, request_id


class FakeBadRequest(FakeAPIError):
    pass


class FakeUnprocessable(FakeAPIError):
    pass


class FakeAnswer:
    def __init__(self, noul):
        self.noul = noul


class FakeResponse:
    def __init__(self, nouls, request_id="req_ok"):
        self.nouls, self.request_id = nouls, request_id


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(agent_common, "_judge", "llm")
    monkeypatch.setattr(agent_review, "_jev_client", None)
    yield
    monkeypatch.setattr(agent_common, "_judge", "llm")


@pytest.fixture
def fake_sdk(monkeypatch):
    """Installs a fake `typesafe_sdk`; `record["reply"]` is the response (or an exception
    to raise) for system_one."""
    record = {"clients": [], "calls": [], "questions": [], "reply": FakeResponse({"block": FakeAnswer(0.9)})}

    class FakeNoul:
        def __init__(self, **kwargs):
            record["questions"].append(kwargs)
            self.kwargs = kwargs

    class FakeClient:
        def __init__(self, **kwargs):
            record["clients"].append(kwargs)

        def system_one(self, state, questions, **kwargs):
            record["calls"].append({"state": state, "questions": questions, **kwargs})
            if isinstance(record["reply"], Exception):
                raise record["reply"]
            return record["reply"]

    module = types.ModuleType("typesafe_sdk")
    module.Noul = FakeNoul
    module.TypeSafeClient = FakeClient
    module.TypeSafeError = FakeTypeSafeError
    module.TypeSafeAPIError = FakeAPIError
    module.TypeSafeBadRequestError = FakeBadRequest
    module.TypeSafeUnprocessableEntityError = FakeUnprocessable
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    return record


def no_claude(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the jev judge must not call claude")
    monkeypatch.setattr(agent_review, "call_claude", boom)


# --- selector and CLI --------------------------------------------------------------

def test_default_judge_is_llm():
    assert get_judge() == "llm"


def test_set_judge_rejects_unknown_names():
    with pytest.raises(ValueError):
        set_judge("bogus")


def test_llm_judge_is_the_default_path(monkeypatch):
    seen = []
    monkeypatch.setattr(agent_review, "call_claude", lambda p, model="sonnet": seen.append(p) or "ok\nVERDICT: OK")
    assert judge_review("security", "No issues found.") == ("security", "OK", "ok\nVERDICT: OK")
    assert "strict, isolated verifier" in seen[0]


def test_parse_cli_defaults_to_llm_judge(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_x.py", "task"])
    parse_cli()
    assert get_judge() == "llm"


@pytest.mark.parametrize("argv", [
    ["agent_x.py", "--judge", "jev", "task"],
    ["agent_x.py", "task", "--judge", "jev"],
])
def test_parse_cli_selects_jev_in_either_position(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    assert parse_cli() == "task"
    assert get_judge() == "jev"


def test_parse_cli_rejects_unknown_judge(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_x.py", "--judge", "bogus", "task"])
    with pytest.raises(SystemExit) as exc:
        parse_cli()
    assert exc.value.code == 2


# --- the Jev judge -----------------------------------------------------------------

@pytest.mark.parametrize("p, verdict", [
    (0.99, "BLOCK"),
    (JEV_BLOCK_THRESHOLD, "BLOCK"),          # the threshold itself blocks
    (JEV_BLOCK_THRESHOLD - 0.001, "OK"),
    (0.0, "OK"),
])
def test_threshold_decides_the_verdict(monkeypatch, fake_sdk, p, verdict):
    no_claude(monkeypatch)
    set_judge("jev")
    fake_sdk["reply"] = FakeResponse({"block": FakeAnswer(p)})
    name, got, text = judge_review("security", "some review")
    assert (name, got) == ("security", verdict)
    assert "INTEGRATION ERROR" not in text and f"{p:.3f}" in text and "req_ok" in text


def test_missing_request_id_does_not_discard_a_valid_judgment(fake_sdk):
    class NoRequestId(FakeResponse):
        @property
        def request_id(self):  # the real SDK raises TypeSafeError here when the header is absent
            raise FakeTypeSafeError("The response did not include a request ID.")

        @request_id.setter
        def request_id(self, value):
            pass

    set_judge("jev")
    fake_sdk["reply"] = NoRequestId({"block": FakeAnswer(0.9)})
    _, verdict, text = judge_review("security", "x")
    assert verdict == "BLOCK" and "INTEGRATION ERROR" not in text and "request_id=None" in text


def test_one_holistic_question_with_the_shared_rubric(monkeypatch, fake_sdk):
    no_claude(monkeypatch)
    set_judge("jev")
    judge_review("performance", "O(n^2) loop")
    call = fake_sdk["calls"][0]
    assert list(call["questions"]) == ["block"]              # one question, not one per criterion
    assert call["state"] == {"lens": "performance", "review": "O(n^2) loop"}
    q = fake_sdk["questions"][0]
    assert agent_review._judge_rubric("performance") in q["instructions"]
    assert set(q["criteria"]) == {"true", "false"}


def test_client_is_shared_and_never_gets_a_key_from_code(fake_sdk):
    set_judge("jev")
    judge_review("security", "a")
    judge_review("style", "b")
    assert fake_sdk["clients"] == [{}]


@pytest.mark.parametrize("error, category", [
    (FakeUnprocessable("bad question", 422), "schema-error"),
    (FakeBadRequest("bad request", 400), "schema-error"),
    (FakeAPIError("rate limited", 429), "api-error"),
    (FakeAPIError("overloaded", 529), "api-error"),
    (FakeTypeSafeError("No API key was provided."), "client-error"),
])
def test_sdk_errors_fail_safe_to_block_with_a_named_category(fake_sdk, error, category):
    set_judge("jev")
    fake_sdk["reply"] = error
    name, verdict, text = judge_review("security", "clean review")
    assert verdict == "BLOCK"
    assert text.startswith("[jev] INTEGRATION ERROR (" + category + ")")
    assert "not a judgment" in text and type(error).__name__ in text


def test_api_error_text_keeps_status_and_request_id(fake_sdk):
    set_judge("jev")
    fake_sdk["reply"] = FakeAPIError("overloaded", 529, "req_xyz")
    _, _, text = judge_review("security", "x")
    assert "529" in text and "req_xyz" in text


def test_missing_sdk_is_named(monkeypatch):
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)  # makes `import typesafe_sdk` fail
    set_judge("jev")
    _, verdict, text = judge_review("security", "x")
    assert verdict == "BLOCK" and "(sdk-missing)" in text and "typesafe-sdk" in text


def test_question_construction_failure_is_a_schema_error(fake_sdk):
    def bad_noul(**kwargs):
        raise TypeError("unexpected keyword")
    sys.modules["typesafe_sdk"].Noul = bad_noul
    set_judge("jev")
    _, verdict, text = judge_review("security", "x")
    assert verdict == "BLOCK" and "(schema-error)" in text


@pytest.mark.parametrize("reply", [
    FakeResponse({}),                                   # answer missing
    FakeResponse({"block": FakeAnswer(None)}),
    FakeResponse({"block": FakeAnswer(float("nan"))}),
    FakeResponse({"block": FakeAnswer(1.5)}),
    FakeResponse({"block": FakeAnswer("0.1")}),          # wrong type must not read as OK
    FakeResponse({"block": FakeAnswer(True)}),
])
def test_unusable_responses_fail_safe_to_block(fake_sdk, reply):
    set_judge("jev")
    fake_sdk["reply"] = reply
    _, verdict, text = judge_review("security", "x")
    assert verdict == "BLOCK" and "(bad-response)" in text


def test_aggregate_verdict_works_with_the_jev_judge(monkeypatch, fake_sdk):
    no_claude(monkeypatch)
    set_judge("jev")

    def by_lens(state, questions, **kwargs):
        return FakeResponse({"block": FakeAnswer(0.9 if state["lens"] == "security" else 0.1)})

    fake_sdk["reply"] = None
    monkeypatch.setattr(agent_review, "_get_jev_client",
                        lambda: types.SimpleNamespace(system_one=by_lens))
    passed, report, verdicts = aggregate_verdict(
        {"security": "s", "performance": "p", "style": "y", "test_coverage": "t"})
    assert not passed
    assert verdicts == {"security": "BLOCK", "performance": "OK", "style": "OK", "test_coverage": "OK"}
    assert report.startswith("security: BLOCK")


# --- .env loader -------------------------------------------------------------------

def test_loader_reads_only_the_named_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=should-not-load\n# c\nexport TYPESAFE_API_KEY='ts-secret'\nOTHER=1\n")
    for k in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OTHER"):
        monkeypatch.delenv(k, raising=False)
    agent_common.load_env_key("TYPESAFE_API_KEY", str(env))
    assert agent_common.os.environ["TYPESAFE_API_KEY"] == "ts-secret"
    assert "ANTHROPIC_API_KEY" not in agent_common.os.environ and "OTHER" not in agent_common.os.environ
    monkeypatch.delenv("TYPESAFE_API_KEY")


def test_loader_never_overrides_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TYPESAFE_API_KEY=from-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    agent_common.load_env_key("TYPESAFE_API_KEY", str(env))
    assert agent_common.os.environ["TYPESAFE_API_KEY"] == "from-env"


def test_loader_tolerates_a_missing_file_and_empty_value(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    agent_common.load_env_key("TYPESAFE_API_KEY", str(tmp_path / "nope"))
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=\n")
    agent_common.load_env_key("TYPESAFE_API_KEY", str(tmp_path / ".env"))
    assert "TYPESAFE_API_KEY" not in agent_common.os.environ


def test_jev_client_construction_triggers_the_loader(fake_sdk, monkeypatch):
    seen = []
    monkeypatch.setattr(agent_review, "load_env_key", lambda name: seen.append(name))
    set_judge("jev")
    judge_review("security", "x")
    assert seen == ["TYPESAFE_API_KEY"]
