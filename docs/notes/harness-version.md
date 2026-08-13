# Harness version log — Claude Code

The version of the *agents' own harness*, read from `claude --version` and dated. One
line per observation, appended.

**Why this file exists, and what it is not.** DESIGN §10 A2 pins the ladder to
`us.anthropic.claude-opus-4-5-20251101-v1:0`, so what an arm *calls* is a dated id
recorded in `metrics.json`'s run block and in every `agent_calls.jsonl` line. The harness
that drives the session is a separate thing and is recorded nowhere: DESIGN §10 A2 and
`baseline-model-family.md` §"하네스" both name it in prose ("Claude Code on opus-5") and
neither carries a version. That prose is the argument that the harness/model generation gap
confounds nothing — the harness writes no rules and is scored on nothing — and the argument
does not need a version number to hold. This log is therefore **not** a run record and
nothing reads it: it is the answer to "what was the tooling on the day", for a reader
reconstructing a session months later.

It is also not a substitute for the freeze. A window is frozen per arm immediately before
its call (DESIGN §6.3) and `window_hashes()` covers the prompt and config files. The
harness version is not in the window and must not be added to it — an arm's window is what
the *call* ran under, and re-freezing a frozen arm is impossible by construction.

## Log

| Date | `claude --version` | Note |
|---|---|---|
| 2026-08-14 | `2.1.223 (Claude Code)` | First recorded value. Not a change — no earlier version was ever written down, here or anywhere else in the repository (checked: `docs/`, `git log -S`). So this row opens the log rather than closing an interval, and the harness version for every session before today is unknown and stays unknown. |
