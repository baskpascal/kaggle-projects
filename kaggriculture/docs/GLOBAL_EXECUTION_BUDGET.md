# Global execution budget

Every match pool now acquires one host-wide lease before starting children. The lease is
shared by all processes and Git worktrees through advisory locks under
`~/.config/kaggriculture/resource-pool/<hostname>`; it is machine state and never belongs
in Git. Kernel ownership makes cancellation and crashes safe: closing or killing the
holder releases every CPU and memory token automatically.

The default capacity leaves one logical CPU free and admits match children only while
their combined memory reservation fits 75% of physical memory. Each child reserves 512
MiB in 256 MiB tokens. Stable machine-specific overrides are available without changing
the repository:

```bash
export ARENA_CPU_BUDGET=14
export ARENA_MEMORY_BUDGET_MB=12288
export ARENA_MEMORY_PER_WORKER_MB=512
```

Acquisition is FIFO at the experiment boundary. A workload receives its useful pool
atomically, runs at full local throughput, and then lets the next waiting workload use the
machine. Requests larger than CPU, memory, or job-count capacity are reduced with a clear
diagnostic. Ray batch workers acquire the same tokens, so a local run cannot oversubscribe
a host while Ray is also using it.

Every match row carries `execution_resources`; league report metadata summarizes requested
and granted workers, CPU and memory budgets, games/s, maximum queue wait, and the
approximate global concurrency seen when the lease was granted. Batch runners also
reject a configured full-batch size smaller than the CPUs reserved by the task. Only the
final tail may contain fewer jobs, and `matches()` reduces that tail's reservation to its
actual number of jobs.
