# Local autonomous research supervisor

`scripts/research_supervisor.py` keeps one durable Codex thread moving through the
Kaggriculture research loop. It talks to the locally installed Codex App Server over
JSONL/stdio and requires the server to report `account.type == "chatgpt"`. It neither
imports an OpenAI SDK nor accepts an API key. An inherited `OPENAI_API_KEY` is removed
from the child environment as a second line of defense.

Runtime state and the exclusive process lock live in
`~/.config/kaggriculture/`, not in Git. The state contains the thread id and lifecycle
metadata, never credentials. Each update is an atomic replacement with mode `0600`.

## Gates

Run the protocol/authentication check without starting a model turn:

```bash
cd kaggriculture
.venv/bin/python scripts/research_supervisor.py check
```

Start the persistent loop in a dedicated clean worktree:

```bash
cd /absolute/path/to/dedicated-kaggriculture-worktree/kaggriculture
.venv/bin/python scripts/research_supervisor.py run
```

The supervisor initializes App Server, verifies ChatGPT authentication and backend
ordinary-usage permission, starts or resumes the persisted thread, and starts another
turn after every routine completion. A line saying `NEXT AUTOMATIC ACTION` has no
control meaning. Only one exact final-answer marker can stop research:

- `AUTONOMY_STATUS: SUBMISSION_READY`
- `AUTONOMY_STATUS: AUTH_REQUIRED`
- `AUTONOMY_STATUS: RUN_EXHAUSTED`

SIGINT/SIGTERM produces `STOPPED`. Protocol or process faults are recorded as
`RETRYABLE_FAILURE` so an external service manager can restart the same thread.
`STOPPED` is resumable on the next explicit/service start; disabling the user service is
the durable operator stop.
Interactive App Server requests are declined: autonomous mode uses `approvalPolicy =
never` and does not synthesize human approval.

The research prompt repeats the prohibition on OpenAI APIs, API keys, API-backed SDKs,
paid model APIs, and automatic Kaggle submission. The account gate is authoritative:
an API-key, unauthenticated, or non-ChatGPT Codex session stops as `AUTH_REQUIRED`.

Protocol and authentication behavior are pinned to the official Codex documentation:

- [Codex App Server](https://developers.openai.com/codex/app-server)
- [Codex authentication](https://developers.openai.com/codex/auth)

## Service policy

Use a user-level service with restart-on-failure if continuous operation across shell
logouts is desired. Machine-specific service files belong under
`~/.config/systemd/user/`, never in this repository. Point the service at one dedicated
worktree; do not share a writable worktree with another research process. The process
lock prevents two supervisor instances using the same state file, but worktree
isolation remains the operator's responsibility.
