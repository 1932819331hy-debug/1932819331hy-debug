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

## Checked worker-report transport

`worker_report.py --report report.json --context context.json` validates one
bounded UTF-8 JSON report (at most 256 KiB). It is a read-only helper for the
existing Page writer, with no dispatcher, credentials, scheduler or state store.
The context comes from the freshly read canonical ticket, never from the report:
`task_id`, `repo`, `branch`, `owner`, `builder`, `publisher`, `head_sha`,
`ticket_state`, `report_sha256` (the same Page Files readback digest), and
`accepted_reports` (the Page's report-id-to-digest acknowledgements).

Report schema version 1 has those seven identity fields, `report_id`,
`tested_sha`, `outcome` (`COMPLETED`, `IN_PROGRESS`, or `BLOCKED`),
`report_kind` (`WORKER_RESULT` or `PRIVATE_HISTORICAL_TRANSPORT_CONTROL`),
`evidence` (`path`, UTF-8 `text`, `sha256`), and `checks` (`argv`, actual
`exit_code`, `tested_sha`, `log_path`). Every check must reference an included
log. The module's focused tests provide a complete minimal example. Include
patch/source and logs as data when needed; payload commands are never executed.

Worker: upload the report into the existing Page Files destination and read it
back, then hand its exact reference/digest to the original writer. Writer: read
the fresh ticket and acknowledgement map, retrieve the report, run this helper,
and retain its exact output. `VALIDATED` proves matching metadata and transport
bytes only: worker-authored test claims still require the normal exact-commit
evidence/review gates. Nonzero checks and blocked ticket state are preserved.
This helper never promotes READY, transfers roles, proves source-to-commit
identity, or attests natural delivery. Apply the existing canonical policy.

For a new validated report, conditionally save `{report_id: report_sha256}` and
the reference/checked receipt in the same Page, then read back. No acknowledgement
exists until that save succeeds. On conflict or uncertain write, read fresh state
and rerun the validator; `ALREADY_CONSUMED` means no further import/action. Same
ID with changed bytes is rejected. Only the sole existing writer can acknowledge;
the map is not a lock for concurrent writers. A historical private transport
control must stay labelled and cannot become new bounty or natural delivery credit.

## Interrupted committed-candidate recovery

`worker_recovery.py --checkpoint checkpoint.json --context context.json --target
/absolute/new/private/path` restores source for one pending **idempotent local
check**. It reuses the original Page, roles and step/action receipt. It is not a
runner or a second ledger. Dirty/uncommitted work, external exactly-once actions,
large repositories and unsupported Git modes are outside this bounded version.

After committing a candidate and confirming its working tree is clean, its owner
can use `git bundle create checkpoint.bundle HEAD`. Wrap the bundle bytes in
readable JSON (at most 256 KiB): version 1, the seven worker-report identity
fields, `checkpoint_id`, `tree_sha`, `next_step_id`, `checkpoint_kind`
(`COMMITTED_CANDIDATE` or `PRIVATE_HISTORICAL_RECOVERY_CONTROL`),
`bundle_base64`, and `bundle_sha256`. Upload/read back through the same Page
Files before relying on the checkpoint. The helper verifies prerequisites in an
empty Git repository: a thin/partial bundle cannot silently fetch its missing base.

The fresh canonical context contains the seven identity fields plus
`checkpoint_sha256` (Page Files readback), `step_id`,
`step_kind: IDEMPOTENT_LOCAL_CHECK`, `step_state`,
`completed_checkpoint_sha256` (null until completed), and `resume_authorized`.
The original writer decides authorization from the actual owner/branch/ticket;
the checkpoint cannot grant it. Examples are in the focused real-Git tests.

PENDING restores into a new private directory, checks exact commit/tree/index and
raw regular-file bytes, and returns `RESTORED_PENDING_CHECK`. Git uses only local
bundle transport; files are written without checkout filters, hooks from source,
submodule checkout or checkpoint-authored command execution. The restored repo
has a detached HEAD and no configured publication remote. Existing paths are
never replaced; a partial failure leaves only its newly reserved target for
reconciliation. This is not a sandbox against another process with the same UID.

Before executing the trusted check from the **canonical ticket**, the sole writer
conditionally records/read-backs STARTED for the returned source-independent
action key. Preserve real argv/exit/log and a single invocation marker in the
private drill; on success save the completed checkpoint digest and actual result
to the same Page, read back, then repeat the helper with fresh COMPLETED context.
It must return `ALREADY_COMPLETED_NO_ACTION`, without restoring or running a step.
STARTED with no durable result returns `IN_FLIGHT_REQUIRES_RECONCILIATION` (CLI3),
never blind retry. This conservative gap handling does not promise exactly-once
arbitrary side effects. Original test/review/publication gates still apply.

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
