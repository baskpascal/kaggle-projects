#!/usr/bin/env python3
"""Persistent local Codex App Server supervisor for Kaggriculture research.

This process deliberately uses the installed ``codex`` binary over stdio.  It
does not import an OpenAI SDK, call an OpenAI HTTP API, or accept an API key.
The first protocol gate requires the active Codex account to be ChatGPT.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TERMINAL_MARKER = re.compile(
    r"^AUTONOMY_STATUS: (SUBMISSION_READY|AUTH_REQUIRED|RUN_EXHAUSTED)$"
)
CONTROL_MARKER = re.compile(r"^AUTONOMY_STATUS:\s+\S+.*$")
USAGE_ERRORS = {
    "usageLimitExceeded",
    "UsageLimitExceeded",
    "rateLimitExceeded",
    "sessionBudgetExceeded",
}
AUTH_ERRORS = {"unauthorized", "Unauthorized", "authenticationRequired"}

DEFAULT_PROMPT = """Continue the autonomous Kaggriculture research mission in this repository.

Treat AGENTS.md, kaggriculture/research/state.json, the newest relevant journal entries,
current branches/issues, current champion hash, current Kaggle field, and raw experiment
evidence as authoritative. Execute the next highest-information causal experiment; do not
merely describe a next action. Reuse existing Ray/harness/replay/evaluator instruments and
isolated worktrees. Optimize competitive win probability and information per CPU-hour. Keep
Stage 2 closed until the current mechanism passes its executed-state gates. Persist reusable
evidence, tests for measurement bugs, research memory, issue updates, and coherent commits.
Use subagents asymmetrically when useful and prevent concurrent writers in one worktree.

Hard constraints: use only local Codex authenticated by the existing ChatGPT account. Never
use an OpenAI API key, OPENAI_API_KEY, the OpenAI Agents API, Responses API, an API-backed
Agents SDK, or any paid model API. Never submit to Kaggle without explicit human approval.
Do not stop for failed hypotheses, routine choices, pivots, or a discovered next action:
execute the next experiment automatically.

Only when one condition is actually true, put exactly one of these on its own line in the
final answer:
AUTONOMY_STATUS: SUBMISSION_READY
AUTONOMY_STATUS: AUTH_REQUIRED
AUTONOMY_STATUS: RUN_EXHAUSTED

Otherwise finish the turn with AUTONOMY_STATUS: CONTINUE_RESEARCH on its own line. The local
supervisor will immediately start another turn in this same persistent thread.
"""


class SupervisorError(RuntimeError):
    pass


class AuthenticationRequired(SupervisorError):
    pass


class UsageExhausted(SupervisorError):
    pass


class ProtocolError(SupervisorError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "status": "NEW", "turns_completed": 0}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SupervisorError(f"unsupported supervisor state: {path}")
    return value


def account_type(result: dict[str, Any]) -> str | None:
    account = result.get("account")
    return account.get("type") if isinstance(account, dict) else None


def usage_is_allowed(result: dict[str, Any]) -> bool:
    # The protocol explicitly says null is unknown and clients must not infer
    # exhaustion from percentages or reset timestamps.
    if result.get("ordinaryUsageAllowed") is False:
        return False
    snapshots = [result.get("rateLimits")]
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        snapshots.extend(by_id.values())
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("rateLimitReachedType") is not None:
            return False
        if snapshot.get("spendControlReached") is True:
            return False
    return True


def error_info_kind(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return info
    if isinstance(info, dict) and len(info) == 1:
        return next(iter(info))
    return None


def codex_error_kind(turn: dict[str, Any]) -> str | None:
    return error_info_kind(turn.get("error"))


def terminal_status(final_text: str) -> str | None:
    lines: list[str] = []
    in_fence = False
    for raw_line in final_text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if line and not in_fence:
            lines.append(line)
    if not lines:
        return None
    if sum(bool(CONTROL_MARKER.fullmatch(line)) for line in lines) != 1:
        return None
    matches = [TERMINAL_MARKER.fullmatch(line) for line in lines]
    recognized = [match for match in matches if match]
    if len(recognized) != 1 or matches[-1] is None:
        return None
    return recognized[0].group(1)


def final_agent_text(item: dict[str, Any]) -> str | None:
    if item.get("type") != "agentMessage":
        return None
    phase = item.get("phase")
    if phase not in (None, "final_answer"):
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


@dataclass
class TurnResult:
    turn: dict[str, Any]
    final_text: str


def result_from_completed_turn(turn: dict[str, Any], fallback_text: str = "") -> TurnResult:
    final_text = fallback_text
    items = turn.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                candidate = final_agent_text(item)
                if candidate is not None:
                    final_text = candidate
    return TurnResult(turn=turn, final_text=final_text)


class AppServer:
    """Small synchronous JSONL client for the local Codex App Server."""

    def __init__(self, command: Sequence[str], cwd: Path):
        env = os.environ.copy()
        # An inherited API key must never affect provider selection or auth.
        for variable in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(variable, None)
        self.process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._pending_notifications: list[dict[str, Any]] = []
        self._stderr_tail: list[str] = []
        self._next_id = 1
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout
        try:
            for line in self.process.stdout:
                if not line.strip():
                    continue
                self._messages.put(json.loads(line))
        except BaseException as exc:  # forwarded to the supervising thread
            self._messages.put(exc)

    def _read_stderr(self) -> None:
        assert self.process.stderr
        for line in self.process.stderr:
            self._stderr_tail.append(line.rstrip())
            del self._stderr_tail[:-20]

    def send(self, message: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            raise ProtocolError(f"app server exited with {self.process.returncode}")
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _receive_wire(self, timeout: float = 300.0) -> dict[str, Any]:
        try:
            value = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise ProtocolError("timed out waiting for Codex App Server") from exc
        if isinstance(value, BaseException):
            raise ProtocolError(f"invalid App Server output: {value}") from value
        return value

    def receive(self, timeout: float = 300.0) -> dict[str, Any]:
        if self._pending_notifications:
            return self._pending_notifications.pop(0)
        return self._receive_wire(timeout)

    def respond_fail_closed(self, request: dict[str, Any]) -> None:
        method = str(request.get("method", ""))
        if "requestApproval" in method:
            self.send({"id": request["id"], "result": {"decision": "decline"}})
            return
        if "requestUserInput" in method:
            self.send({"id": request["id"], "result": {"answers": {}}})
            return
        self.send(
            {
                "id": request["id"],
                "error": {
                    "code": -32001,
                    "message": "interactive requests are disabled in autonomous mode",
                },
            }
        )

    def request(self, method: str, params: Any, timeout: float = 300.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        deadline = time.monotonic() + timeout
        while True:
            # Do not consume the pending-notification backlog while waiting for
            # a response: those events may belong to an active resumed turn.
            incoming = self._receive_wire(max(0.1, deadline - time.monotonic()))
            if incoming.get("id") == request_id:
                if "error" in incoming:
                    raise ProtocolError(f"{method} failed: {incoming['error']}")
                result = incoming.get("result")
                return result if isinstance(result, dict) else {}
            if "id" in incoming and "method" in incoming:
                self.respond_fail_closed(incoming)
            elif "method" in incoming:
                self._pending_notifications.append(incoming)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "kaggriculture-research-supervisor",
                    "title": "Kaggriculture Research Supervisor",
                    "version": "1.0.0",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=30,
        )
        self.send({"method": "initialized"})

    def verify_chatgpt(self) -> None:
        result = self.request("account/read", {"refreshToken": False}, timeout=30)
        kind = account_type(result)
        if kind != "chatgpt":
            raise AuthenticationRequired(
                "Codex must already be authenticated with ChatGPT; no login or API-key fallback was attempted"
            )
        limits = self.request("account/rateLimits/read", None, timeout=30)
        if not usage_is_allowed(limits):
            raise UsageExhausted("ChatGPT Codex ordinary usage is currently unavailable")

    def start_or_resume_thread(
        self,
        thread_id: str | None,
        cwd: Path,
        expected_turn_id: str | None = None,
        recover_unpersisted_start: bool = False,
        last_turn_id: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        if thread_id:
            result = self.request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(cwd),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                },
            )
        else:
            result = self.request(
                "thread/start",
                {
                    "cwd": str(cwd),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "ephemeral": False,
                    "threadSource": "kaggriculture-local-supervisor",
                },
            )
        thread = result.get("thread")
        new_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(new_id, str):
            raise ProtocolError("thread response did not contain thread.id")
        if thread_id and new_id != thread_id:
            raise ProtocolError("thread/resume returned a different thread id")
        recovered_turn = None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if isinstance(turns, list):
            if expected_turn_id:
                recovered_turn = next(
                    (
                        turn
                        for turn in reversed(turns)
                        if isinstance(turn, dict) and turn.get("id") == expected_turn_id
                    ),
                    None,
                )
                if recovered_turn is None:
                    raise ProtocolError("persisted active turn is absent from resumed thread")
            elif recover_unpersisted_start:
                valid_turns = [turn for turn in turns if isinstance(turn, dict)]
                if last_turn_id is None:
                    candidates = valid_turns
                else:
                    anchors = [
                        index
                        for index, turn in enumerate(valid_turns)
                        if turn.get("id") == last_turn_id
                    ]
                    if len(anchors) != 1:
                        raise ProtocolError(
                            "cannot anchor unpersisted turn in resumed thread history"
                        )
                    candidates = valid_turns[anchors[0] + 1 :]
                if len(candidates) > 1:
                    raise ProtocolError("multiple turns appeared after an unpersisted start")
                recovered_turn = candidates[0] if candidates else None
            if recovered_turn is None:
                recovered_turn = next(
                    (
                        turn
                        for turn in reversed(turns)
                        if isinstance(turn, dict) and turn.get("status") == "inProgress"
                    ),
                    None,
                )
        status = thread.get("status") if isinstance(thread, dict) else None
        status_type = status.get("type") if isinstance(status, dict) else None
        if thread_id and status_type == "active" and recovered_turn is None:
            raise ProtocolError("resumed thread is active but its in-progress turn was omitted")
        return new_id, recovered_turn

    def start_turn(self, thread_id: str, prompt: str, cwd: Path) -> str:
        started = self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "cwd": str(cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "input": [{"type": "text", "text": prompt}],
            },
        )
        started_turn = started.get("turn")
        turn_id = started_turn.get("id") if isinstance(started_turn, dict) else None
        if not isinstance(turn_id, str):
            raise ProtocolError("turn/start response did not contain turn.id")
        return turn_id

    def wait_turn(self, turn_id: str | None) -> TurnResult:
        final_text = ""
        while True:
            incoming = self.receive(timeout=3600)
            if "id" in incoming and "method" in incoming:
                self.respond_fail_closed(incoming)
                continue
            method = incoming.get("method")
            params = incoming.get("params")
            if not isinstance(params, dict):
                continue
            if method == "item/completed":
                item_turn_id = params.get("turnId")
                if turn_id and item_turn_id != turn_id:
                    continue
                item = params.get("item")
                if isinstance(item, dict):
                    candidate = final_agent_text(item)
                    if candidate is not None:
                        final_text = candidate
            if method == "error" and params.get("turnId") in (None, turn_id):
                if params.get("willRetry") is False:
                    kind = error_info_kind(params.get("error"))
                    if kind in USAGE_ERRORS:
                        raise UsageExhausted(f"Codex turn failed with {kind}")
                    if kind in AUTH_ERRORS:
                        raise AuthenticationRequired(f"Codex turn failed with {kind}")
            if method == "turn/completed":
                turn = params.get("turn")
                if not isinstance(turn, dict):
                    raise ProtocolError("turn/completed omitted turn")
                if turn_id and turn.get("id") != turn_id:
                    continue
                return result_from_completed_turn(turn, final_text)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)
        if self.process.poll() is None:
            self.process.kill()


class ResearchSupervisor:
    def __init__(
        self,
        *,
        repo: Path,
        state_path: Path,
        command: Sequence[str] = ("codex", "app-server", "--stdio"),
        prompt: str = DEFAULT_PROMPT,
        max_turns: int | None = None,
    ):
        self.repo = repo.resolve()
        self.state_path = state_path.expanduser().resolve()
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        self.command = tuple(command)
        self.prompt = prompt
        self.max_turns = max_turns
        self._stop = False

    def _save(self, state: dict[str, Any], status: str, **extra: Any) -> None:
        state.update(extra)
        state["status"] = status
        state["updated_at"] = utc_now()
        state["repo"] = str(self.repo)
        atomic_write_json(self.state_path, state)

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self._stop = True

    def check(self) -> dict[str, Any]:
        server = AppServer(self.command, self.repo)
        try:
            server.initialize()
            server.verify_chatgpt()
            return {"account_type": "chatgpt", "ordinary_usage_allowed": True}
        finally:
            server.close()

    def run(self) -> str:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SupervisorError("another research supervisor holds the lock") from exc
            return self._run_locked()

    def _run_locked(self) -> str:
        state = read_state(self.state_path)
        if state.get("status") in {
            "SUBMISSION_READY",
            "AUTH_REQUIRED",
            "RUN_EXHAUSTED",
        }:
            return str(state["status"])
        saved_status = state.get("status")
        saved_active_turn = state.get("active_turn_id")
        saved_last_turn = state.get("last_turn_id")
        self._save(state, "STARTING", pid=os.getpid())
        server = AppServer(self.command, self.repo)
        try:
            server.initialize()
            server.verify_chatgpt()
            if not isinstance(saved_active_turn, str):
                saved_active_turn = None
            thread_id, recovered_turn = server.start_or_resume_thread(
                state.get("thread_id"),
                self.repo,
                expected_turn_id=saved_active_turn,
                recover_unpersisted_start=saved_status == "TURN_STARTING",
                last_turn_id=saved_last_turn,
            )
            active_turn_id = recovered_turn.get("id") if recovered_turn else None
            self._save(
                state,
                "RUNNING",
                thread_id=thread_id,
                active_turn_id=active_turn_id,
                pid=os.getpid(),
            )
            turns_this_run = 0
            while not self._stop:
                if self.max_turns is not None and turns_this_run >= self.max_turns:
                    self._save(state, "RUN_EXHAUSTED", reason="configured turn budget reached")
                    return "RUN_EXHAUSTED"
                # Refresh the authoritative backend usage permission before each turn.
                limits = server.request("account/rateLimits/read", None, timeout=30)
                if not usage_is_allowed(limits):
                    raise UsageExhausted("ChatGPT Codex ordinary usage is currently unavailable")
                if recovered_turn:
                    self._save(
                        state,
                        "TURN_RUNNING",
                        active_turn_id=active_turn_id,
                    )
                    if recovered_turn.get("status") == "inProgress":
                        result = server.wait_turn(active_turn_id)
                    else:
                        result = result_from_completed_turn(recovered_turn)
                    recovered_turn = None
                    active_turn_id = None
                else:
                    self._save(
                        state,
                        "TURN_STARTING",
                        current_turn_started_at=utc_now(),
                        active_turn_id=None,
                    )
                    active_turn_id = server.start_turn(thread_id, self.prompt, self.repo)
                    self._save(state, "TURN_RUNNING", active_turn_id=active_turn_id)
                    result = server.wait_turn(active_turn_id)
                    active_turn_id = None
                turns_this_run += 1
                state["turns_completed"] = int(state.get("turns_completed", 0)) + 1
                state["last_turn_id"] = result.turn.get("id")
                state["last_turn_status"] = result.turn.get("status")
                state["last_final_excerpt"] = result.final_text[-2000:]
                state["active_turn_id"] = None
                kind = codex_error_kind(result.turn)
                if kind in USAGE_ERRORS:
                    raise UsageExhausted(f"Codex turn failed with {kind}")
                if kind in AUTH_ERRORS:
                    raise AuthenticationRequired(f"Codex turn failed with {kind}")
                if result.turn.get("status") != "completed":
                    raise ProtocolError(f"turn ended with status {result.turn.get('status')!r}")
                terminal = terminal_status(result.final_text)
                if terminal:
                    self._save(state, terminal, reason="terminal marker from Codex")
                    return terminal
                self._save(state, "RUNNING")
            self._save(state, "STOPPED", reason="operator signal")
            return "STOPPED"
        except AuthenticationRequired as exc:
            self._save(state, "AUTH_REQUIRED", reason=str(exc))
            return "AUTH_REQUIRED"
        except UsageExhausted as exc:
            self._save(state, "RUN_EXHAUSTED", reason=str(exc))
            return "RUN_EXHAUSTED"
        except BaseException as exc:
            self._save(state, "RETRYABLE_FAILURE", reason=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            server.close()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    default_state = Path.home() / ".config/kaggriculture/research-supervisor.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "check", "status"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, default=default_state)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--max-turns", type=int)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "status":
        print(json.dumps(read_state(args.state.expanduser()), indent=2, sort_keys=True))
        return 0
    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else DEFAULT_PROMPT
    supervisor = ResearchSupervisor(
        repo=args.repo,
        state_path=args.state,
        prompt=prompt,
        max_turns=args.max_turns,
    )
    signal.signal(signal.SIGINT, supervisor.request_stop)
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    if args.command == "check":
        print(json.dumps(supervisor.check(), sort_keys=True))
        return 0
    print(supervisor.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
