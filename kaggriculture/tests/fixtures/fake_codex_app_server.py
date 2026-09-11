"""Deterministic JSONL peer used by research-supervisor protocol tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SCENARIO = os.environ.get("FAKE_CODEX_SCENARIO", "continue_then_terminal")
LOG = Path(os.environ["FAKE_CODEX_LOG"])
turns_started = 0


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def record(value: dict) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, separators=(",", ":")) + "\n")


record(
    {
        "environment": {
            name: name in os.environ
            for name in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY")
        }
    }
)


def complete(turn_id: str, text: str) -> None:
    emit(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": turn_id,
                "item": {
                    "id": f"message-{turn_id}",
                    "type": "agentMessage",
                    "text": text,
                    "phase": "final_answer",
                },
            },
        }
    )
    emit(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": turn_id, "items": [], "status": "completed"},
            },
        }
    )


for line in sys.stdin:
    message = json.loads(line)
    record(message)
    if "id" not in message:
        continue
    request_id = message["id"]
    method = message.get("method")
    if method == "initialize":
        emit({"id": request_id, "result": {"userAgent": "fake/1"}})
    elif method == "account/read":
        account_kind = "apiKey" if SCENARIO == "api_key" else "chatgpt"
        emit(
            {
                "id": request_id,
                "result": {
                    "account": {"type": account_kind},
                    "requiresOpenaiAuth": True,
                },
            }
        )
    elif method == "account/rateLimits/read":
        emit(
            {
                "id": request_id,
                "result": {
                    "ordinaryUsageAllowed": SCENARIO != "usage_exhausted",
                    "rateLimits": {},
                },
            }
        )
    elif method == "thread/start":
        emit(
            {
                "id": request_id,
                "result": {"thread": {"id": "thread-1", "status": {"type": "idle"}, "turns": []}},
            }
        )
    elif method == "thread/resume":
        turns = []
        status = {"type": "idle"}
        if SCENARIO == "resume_active":
            turns = [{"id": "active-turn", "items": [], "status": "inProgress"}]
            status = {"type": "active", "activeFlags": []}
        elif SCENARIO == "resume_completed":
            turns = [
                {"id": "older-turn", "items": [], "status": "completed"},
                {
                    "id": "accepted-before-crash",
                    "items": [
                        {
                            "id": "recovered-message",
                            "type": "agentMessage",
                            "text": "recovered terminal\nAUTONOMY_STATUS: RUN_EXHAUSTED",
                            "phase": "final_answer",
                        }
                    ],
                    "status": "completed",
                }
            ]
        elif SCENARIO == "resume_no_new_turn":
            turns = [
                {"id": "oldest-turn", "items": [], "status": "completed"},
                {"id": "last-known", "items": [], "status": "completed"},
            ]
        emit(
            {
                "id": request_id,
                "result": {"thread": {"id": "thread-1", "status": status, "turns": turns}},
            }
        )
        if SCENARIO == "resume_active":
            complete("active-turn", "recovered\nAUTONOMY_STATUS: RUN_EXHAUSTED")
    elif method == "turn/start":
        turns_started += 1
        turn_id = f"turn-{turns_started}"
        emit(
            {
                "id": request_id,
                "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}},
            }
        )
        if turns_started == 1:
            complete(turn_id, "NEXT AUTOMATIC ACTION: execute it\nAUTONOMY_STATUS: CONTINUE_RESEARCH")
        else:
            complete(turn_id, "finished\nAUTONOMY_STATUS: RUN_EXHAUSTED")
