from __future__ import annotations

import json
import fcntl
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from scripts.research_supervisor import (
    AppServer,
    ResearchSupervisor,
    SupervisorError,
    account_type,
    atomic_write_json,
    codex_error_kind,
    terminal_status,
    usage_is_allowed,
)


FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_codex_app_server.py"


def fake_command() -> tuple[str, str]:
    return sys.executable, str(FAKE_SERVER)


def test_chatgpt_is_the_only_accepted_account_shape() -> None:
    assert account_type({"account": {"type": "chatgpt", "email": "x", "planType": "plus"}}) == "chatgpt"
    assert account_type({"account": {"type": "apiKey"}}) == "apiKey"
    assert account_type({"account": None}) is None


def test_usage_permission_does_not_guess_from_percentages() -> None:
    assert usage_is_allowed({"ordinaryUsageAllowed": True, "rateLimits": {}})
    assert not usage_is_allowed({"ordinaryUsageAllowed": False, "rateLimits": {}})
    assert usage_is_allowed(
        {
            "ordinaryUsageAllowed": None,
            "rateLimits": {"primary": {"usedPercent": 100}},
        }
    )
    assert not usage_is_allowed(
        {"ordinaryUsageAllowed": None, "rateLimits": {"rateLimitReachedType": "hardLimit"}}
    )
    assert not usage_is_allowed(
        {"ordinaryUsageAllowed": True, "rateLimits": {"spendControlReached": True}}
    )


def test_only_one_exact_terminal_marker_is_terminal() -> None:
    assert terminal_status("done\nAUTONOMY_STATUS: SUBMISSION_READY\n") == "SUBMISSION_READY"
    assert terminal_status("AUTONOMY_STATUS: CONTINUE_RESEARCH") is None
    assert terminal_status("next: AUTONOMY_STATUS: AUTH_REQUIRED") is None
    assert terminal_status("earlier\nAUTONOMY_STATUS: RUN_EXHAUSTED") == "RUN_EXHAUSTED"
    assert terminal_status("AUTONOMY_STATUS: AUTH_REQUIRED\nexplanation") is None
    assert terminal_status(
        "AUTONOMY_STATUS: AUTH_REQUIRED\nAUTONOMY_STATUS: RUN_EXHAUSTED"
    ) is None
    assert terminal_status(
        "AUTONOMY_STATUS: CONTINUE_RESEARCH\nAUTONOMY_STATUS: RUN_EXHAUSTED"
    ) is None
    assert terminal_status("```text\nAUTONOMY_STATUS: RUN_EXHAUSTED\n```") is None


def test_typed_codex_error_kind_accepts_protocol_string_and_tagged_object() -> None:
    assert codex_error_kind({"error": {"codexErrorInfo": "usageLimitExceeded"}}) == "usageLimitExceeded"
    assert codex_error_kind(
        {"error": {"codexErrorInfo": {"responseStreamDisconnected": {}}}}
    ) == "responseStreamDisconnected"
    assert codex_error_kind({"error": {"message": "unauthorized-looking text"}}) is None


def test_atomic_state_has_private_mode_and_valid_json() -> None:
    # Runtime state lives on the Linux home filesystem. The repository itself
    # may be an NTFS mount that intentionally does not preserve POSIX modes.
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        path = root / "nested" / "state.json"
        atomic_write_json(path, {"schema_version": 1, "thread_id": "thread-1"})
        assert json.loads(path.read_text()) == {"schema_version": 1, "thread_id": "thread-1"}
        assert path.stat().st_mode & 0o777 == 0o600
        assert not list(path.parent.glob(f".{path.name}.*"))
    finally:
        shutil.rmtree(root)


def test_state_schema_is_not_silently_reinterpreted(tmp_path: Path) -> None:
    from scripts.research_supervisor import read_state

    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 2}')
    with pytest.raises(SupervisorError, match="unsupported"):
        read_state(path)


def test_routine_completion_immediately_starts_another_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "continue_then_terminal")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(
        repo=tmp_path, state_path=state, command=fake_command(), prompt="mission"
    )
    assert supervisor.run() == "RUN_EXHAUSTED"
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row.get("method") for row in requests].count("turn/start") == 2
    persisted = json.loads(state.read_text())
    assert persisted["thread_id"] == "thread-1"
    assert persisted["turns_completed"] == 2


def test_api_key_auth_stops_before_creating_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "api_key")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(repo=tmp_path, state_path=state, command=fake_command())
    assert supervisor.run() == "AUTH_REQUIRED"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert "thread/start" not in methods
    assert json.loads(state.read_text())["status"] == "AUTH_REQUIRED"


def test_resume_waits_for_active_turn_without_starting_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    atomic_write_json(
        state,
        {
            "schema_version": 1,
            "status": "RUNNING",
            "thread_id": "thread-1",
            "turns_completed": 3,
        },
    )
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "resume_active")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(repo=tmp_path, state_path=state, command=fake_command())
    assert supervisor.run() == "RUN_EXHAUSTED"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert "thread/resume" in methods
    assert "thread/start" not in methods
    assert "turn/start" not in methods
    assert json.loads(state.read_text())["turns_completed"] == 4


def test_unpersisted_accepted_turn_is_recovered_from_thread_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    atomic_write_json(
        state,
        {
            "schema_version": 1,
            "status": "TURN_STARTING",
            "thread_id": "thread-1",
            "last_turn_id": "older-turn",
            "turns_completed": 3,
        },
    )
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "resume_completed")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(repo=tmp_path, state_path=state, command=fake_command())
    assert supervisor.run() == "RUN_EXHAUSTED"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert "turn/start" not in methods
    persisted = json.loads(state.read_text())
    assert persisted["last_turn_id"] == "accepted-before-crash"
    assert persisted["turns_completed"] == 4


def test_unpersisted_start_does_not_replay_turn_before_history_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    atomic_write_json(
        state,
        {
            "schema_version": 1,
            "status": "TURN_STARTING",
            "thread_id": "thread-1",
            "last_turn_id": "last-known",
            "turns_completed": 3,
        },
    )
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "resume_no_new_turn")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(repo=tmp_path, state_path=state, command=fake_command())
    assert supervisor.run() == "RUN_EXHAUSTED"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert methods.count("turn/start") == 2
    persisted = json.loads(state.read_text())
    assert persisted["last_turn_id"] == "turn-2"
    assert persisted["turns_completed"] == 5


def test_nonretry_typed_usage_error_notification_is_terminal() -> None:
    server = object.__new__(AppServer)
    events = iter(
        [
            {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "willRetry": False,
                    "error": {
                        "message": "quota",
                        "codexErrorInfo": "sessionBudgetExceeded",
                    },
                },
            }
        ]
    )
    server.receive = lambda timeout=0: next(events)  # type: ignore[method-assign]
    from scripts.research_supervisor import UsageExhausted

    with pytest.raises(UsageExhausted, match="sessionBudgetExceeded"):
        server.wait_turn("turn-1")


def test_backend_usage_denial_stops_before_creating_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "messages.jsonl"
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "usage_exhausted")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    supervisor = ResearchSupervisor(repo=tmp_path, state_path=state, command=fake_command())
    assert supervisor.run() == "RUN_EXHAUSTED"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert "thread/start" not in methods


def test_child_never_inherits_model_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "messages.jsonl"
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "continue_then_terminal")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_KEY", "forbidden")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "forbidden")
    monkeypatch.setenv("CODEX_API_KEY", "forbidden")
    supervisor = ResearchSupervisor(
        repo=tmp_path, state_path=tmp_path / "state.json", command=fake_command()
    )
    assert supervisor.check()["account_type"] == "chatgpt"
    environment = json.loads(log.read_text().splitlines()[0])["environment"]
    assert environment == {
        "OPENAI_API_KEY": False,
        "AZURE_OPENAI_API_KEY": False,
        "CODEX_API_KEY": False,
    }


def test_lifetime_lock_fails_before_app_server_spawn(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    supervisor = ResearchSupervisor(
        repo=tmp_path,
        state_path=state,
        command=("this-command-must-never-be-spawned",),
    )
    lock_path = state.with_suffix(".json.lock")
    lock_path.touch()
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SupervisorError, match="another research supervisor"):
            supervisor.run()
