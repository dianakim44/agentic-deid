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
| 2026-08-19 | `2.1.227 (Claude Code)` | Second row that closes an interval, and a one-day one: 2.1.226 → 2.1.227, so the session of 2026-08-18 ran under 2.1.226 and this one does not. Nothing else in the environment moved — Python 3.11.11 and boto3/botocore 1.43.54 are the same values the row below records, macOS reports 27.0 (26A5416b), and the `aws` CLI binary is still absent from the path, which still costs nothing because every call this repository makes goes through boto3. No credential check was run this session: no arm called, so nothing needed one, and a `sts:GetCallerIdentity` on a day with no call would be a record of the tooling rather than of a run. Still no arm under any recorded version: the two `agent_calls.jsonl` lines in the tree are dated 2026-08-11T04:49:15Z and 2026-08-11T21:33:43Z, before this log's first row, and `port-loop` has not called. So this change also bounds nothing and closes no window. |
| 2026-08-18 | `2.1.226 (Claude Code)` | First row that closes an interval: the harness moved 2.1.223 → 2.1.226 across a macOS and Claude Code upgrade, so the sessions of 2026-08-14 through 2026-08-16 ran under 2.1.223 and this one does not. Same session also observed Python 3.11.11 and boto3/botocore 1.43.54, and the `aws` CLI binary is again absent from the path — credentials were confirmed live through `boto3` (`sts:GetCallerIdentity`) instead, which is the check that matters here since every call this repository makes goes through boto3 and none through the CLI. No arm ran under 2.1.223: the three frozen records predate it and `port-loop` has not called, so the version change bounds nothing and closes no window. |
| 2026-08-24 | `2.1.231 (Claude Code)` | 2.1.227 → 2.1.231, four patch versions across five days, so 2.1.228–2.1.230 were never observed and the sessions of 2026-08-20 through 2026-08-23 ran under *some* version in [2.1.227, 2.1.231] rather than under 2.1.227. Earlier rows phrased their intervals as "the sessions of X through Y ran under [the old version]"; that is an inference from an absence of observations and this row does not repeat it. Environment otherwise unmoved: Python 3.11.11, boto3/botocore 1.43.54, macOS 27.0 (26A5416b), `aws` CLI still absent from the path — and credentials confirmed live through boto3 (`sts:GetCallerIdentity`) because this session *does* call, which is when that check is a record of a run rather than of the tooling. **The row below is wrong about `port-loop`, and the falsifier is dated the same UTC day the row was written.** It closed with "`port-loop` has not called", which was true at its commit (2026-08-19T16:25Z) and false by 2026-08-19T22:50:48Z, the timestamp of round 1's `rule_author` call — six hours later, same day, so the row was overtaken rather than mistaken. Rounds 2 and 3 called on 2026-08-23 (02:07Z, 13:00Z), inside the unobserved interval. So the harness version for rounds 1–3 is **bracketed, not known**: ≥ 2.1.227 (observed before round 1's call) and ≤ 2.1.231 (observed now), and no reading exists in between. Round 4, run in this session, is the first `port-loop` round whose harness version is *observed*. That still bounds nothing about the arm: the harness writes no rules and is scored on nothing (see above), and the arm's window is unchanged — `window_freeze.json` is revision 1, generated 2026-08-19T22:50:09Z, and re-freezing a frozen arm is impossible by construction. |

**Row order.** The header says rows are appended, and they were, except that the 2026-08-19 row sits above the 2026-08-18 row it postdates. Left as it is rather than sorted: reordering a log to look like it was written in order is the one edit that makes the log unable to show its own history. Read by the `Date` column, not by position.
