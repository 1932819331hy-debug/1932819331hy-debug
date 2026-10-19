# Public metadata handoff

This program is a read-only, non-model probe. Its SQLite database and Actions
artifacts are derived caches. The existing private canonical Page controls all
task ownership, Builder/Publisher roles and public actions. A cached observation
never authorizes a claim, code change, comment, merge or payment operation.

## Existing hourly controller

Keep the existing controller and schedule. Read its canonical Page CONTROL and
latest receipt first. Refuse an owner/controller mismatch. Use the supported
GitHub workflow-run collection GET, run jobs and raw job logs, or download the
run's `derived-probe-RUN_ID-ATTEMPT` artifact. Match the repository, run ID, event,
head SHA and receipt chain; do not infer a scheduled trigger from the clock.

`PUBLIC_METADATA_RECEIPT` is a bounded projection. `latest.json` holds the public
snapshot; `events.jsonl` holds every cached observation. `event_coverage` and
`validation_coverage` report truncation. A missing artifact, broken receipt
chain, failed source or stale snapshot is a coverage gap, not an empty queue.

Ignore `LEGACY_UNVERIFIED`, `BASELINE_UNTRIAGED` and
`FIRST_OBSERVED_UNVERIFIED` for automatic engineering. `VALIDATION_REQUIRED`
means a meaningful observed delta needs verification; it is not a READY ticket.
A first sight can also require validation when a funded/bounty listing has an
authoritative creation time after its established representation baseline and
before the current observation. Old, missing and future creation times do not
qualify. This does not establish payment eligibility or authorize engineering.
Compare event IDs with the canonical Page's processing records. Verify the
original source, current task, eligibility, amount, policy and acceptance before
creating/updating a ticket. Deep-check at most two new candidates per pass.

For a real READY ticket, retain its original Builder/Publisher and WIP limits.
Record a worker as started only after observing a real worker run with its
repository, branch and tools. Bind test results to the exact commit. Perform
required independent review and publish through the authorized Publisher only.
Re-read the public SHA/comment URL, then persist the receipt to the same Page
using freshly read block hashes and re-read it. On a hash conflict, re-read and
reconcile without replacing concurrent changes. A text lease is not a lock.
Page access failure is `STATE_ACCESS_BLOCKED` and never creates a second ledger.

Keep observed event IDs separately from a source-independent Page action key
containing task ID, repository/branch, expected head, action type and target
review/comment ID when applicable. Multiple sources describing the same action
must map to the same action key. Before retrying an uncertain public write,
inspect the actual remote result. A restarted worker must reconcile its exact
head and receipt before doing more work. Resolved/outdated review records do
not authorize another reply or patch; bot suggestions require normal triage.

## Evidence stages

Each stage has `at`, `status` and `evidence`. Unknown/not reached timestamps
remain null. Only the Page writer can attest downstream stages.

| Stage | Required evidence |
| --- | --- |
| EVENT_DETECTED | Probe receipt, source, canonical task, observation timestamp and diff |
| EVENT_VALIDATED | Fresh source verification and Page processing key |
| TICKET_READY | Canonical Page TASK_ID, owner, Builder/Publisher, scope and gates |
| WORKER_TRIGGER_REQUESTED | Actual accepted run/task request and time |
| WORKER_STARTED | Observed worker ID, environment, start, repository and exact branch/head |
| ACTION_COMPLETED | Actual code/check/publish result, tests, exact SHA or comment URL |
| RECEIPT_PERSISTED | Conditional canonical Page save and independent readback |

Artifacts, notifications and accepted trigger requests do not prove worker
startup. `WORKER_TRIGGER_BLOCKED` remains until a real authorized trigger is
available; it must not disable the existing hourly fallback. No credentials,
model API, invented wake-up endpoint or long-running sleep loop is installed.

## Coverage and failures

The cron is a ten-minute target, not a platform delivery guarantee. Measure
actual schedule runs and source read times. New-source research remains a
bounded attempt at most every two hours; full WIP does not stop lightweight
known-source discovery. Do not add a fifth manifest source without accounting
for the bounded per-pass source budget.

An incomplete review attempt is retained separately from the last complete
`reviews.json`. Its retry state preserves pending cursors. Fetch the complete
snapshot file when a log reports `NOT_DUE`; do not treat an empty projection as
an empty review list. Source failures keep their prior verified cursor; the
workflow preserves recoverable cache and artifacts even when a read fails.
