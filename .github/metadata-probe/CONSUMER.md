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

Ignore `LEGACY_UNVERIFIED` for automatic engineering. `BASELINE_UNTRIAGED` and
`FIRST_OBSERVED_UNVERIFIED` need qualification against the live source and Page;
they are not evidence that an opportunity is ineligible. `qualification_handoff`
exposes these alongside `VALIDATION_REQUIRED` from explicitly marked discovery
sources. Its bounded projection preserves statuses and never grants dispatch or
publication authority. Use its coverage and `events.jsonl` when truncated, and
Page source/event processing records to skip consumed observations. The projection
limit is a log bound, not a task intake limit. `VALIDATION_REQUIRED`
means a meaningful observed delta needs verification; it is not a READY ticket.
A first sight can also require validation when a funded/bounty listing has an
authoritative creation time after its established representation baseline and
before the current observation. Old, missing and future creation times do not
qualify. This does not establish payment eligibility or authorize engineering.
Compare event IDs with the canonical Page's processing records. Verify the
original source, current task, eligibility, amount, policy and acceptance before
creating/updating a ticket. Ordinary valid contributions may also be permitted,
but must remain distinct from monetary engineering. A cash task needs an actual
monetary lead from its original source or reward program; ordinary issue labels
and token-saving measurements alone are not money evidence. Unknown amount,
funding, payout eligibility, competition or assignment do not exclude a task
with a monetary lead. A bounty label or confirmed amount is not required.

For a real READY ticket, retain its original Builder/Publisher. Respect actual
resources and dependencies without imposing an arbitrary task-count cap.
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

`HEALTH_INCIDENT_RECEIPT` and `health_incidents.json` are a bounded derived
handoff for the **existing** hourly controller. One overdue heartbeat receipt
anchor produces one stable incident ID and `:OPENED` event; repeated stale
observations retain that event unchanged and still fail the probe job. A later
verified HEALTHY result appends one `:RECOVERED` event. ERROR does not close an
episode. Older observations cannot reopen it; cache corruption or 16-episode
capacity fails visibly without erasing history. Reconcile the canonical Page
before any cache reset. Cache loss may re-emit an event; Page dedupe is decisive.

The original writer must inspect the actual GitHub repository/run/attempt/head,
event and artifact/log chain, then materialize the snapshot as data. Run
`health_incident.py --snapshot health_incidents.json --context context.json`.
The context is a fresh Page-derived `snapshot_sha256` and `accepted_events` map
from event ID to semantic identity digest (event ID plus overdue success time).
The snapshot byte digest and actual source chain are checked separately.
`PENDING_CANONICAL_PAGE_SAVE` returns
only proposed acknowledgements. Conditionally merge new events/digests and source
evidence into the same Page incident object, then read back before recording
`CONTROLLER_CONSUMED`. Re-run with that fresh map: `ALREADY_CONSUMED_NO_ACTION`
means no further action. Conflicting semantic identity is rejected. After cache
eviction a later first observation may carry different run/time evidence; retain
the Page's first evidence and consume no duplicate action.
Map the public incident ID to the original controller/overdue-anchor private
incident key, preserving existing historical evidence. Do not mark a different
current episode recovered from an older event. Explicit private historical replay
must retain its replay label and cannot refresh the natural heartbeat.

The probe never claims artifact upload, Page consumption or human acknowledgement.
Read-back of an actual artifact proves platform persistence; actual Page save and
readback proves the original controller consumed the event. Neither proves GitHub
account notification creation, email/mobile delivery or human read. Keep these
separate; no automatic human acknowledgement or new notification channel exists.

The cron is a ten-minute target, not a platform delivery guarantee. Measure
actual schedule runs and source read times. New-source research remains a
bounded attempt at most every two hours; full WIP does not stop lightweight
known-source discovery.

`max_source_reads` in `sources.json` is a positive integer network-read budget
(default 4), independent of candidate or task capacity. Only due sources consume
it. Due sources rotate by their persisted oldest `last_checked` timestamp, with
manifest order breaking ties; failed attempts also update that timestamp while
preserving their successful cursor and retry backoff. Sources waiting for their
interval/backoff consume no reads. `source_budget.deferred_source_ids` names due
sources left unread in this pass; `processed` counts attempted network reads.
Deferred due sources produce `PARTIAL_BUDGET` / a `PARTIAL` receipt. The next pass
can serve them even while earlier manifest sources are not due. Each selected
source reads one partition/page, retaining the existing exact-page validators.

`known-repository-issues` reads open issues directly through GitHub's repository
issues API for Go-Hybrid, dataprof and rtk, without a bounty-label requirement or
the search API's 1,000-result cap. The repository API also returns PRs; the probe
filters them from candidate observations but counts them for page continuation.
A full page means there may be another page, including when every row was a PR.
No snapshot of a changing paginated listing guarantees complete coverage; inspect
partition/page cursors, timestamps and failed reads before inferring absence.

These ordinary issue observations retain `amount_minor: null`,
`amount_status: UNKNOWN`, `qualification_required: true`, and
`cash_opportunity_verified: false`. Repository membership establishes no funding
or contribution eligibility. They retain baseline/unverified classification and
`dispatch_allowed: false` / `publish_allowed: false`; the existing controller
must verify current policy, issue scope and acceptance before engineering.

An incomplete review attempt is retained separately from the last complete
`reviews.json`. Its retry state preserves pending cursors. Fetch the complete
snapshot file when a log reports `NOT_DUE`; do not treat an empty projection as
an empty review list. Source failures keep their prior verified cursor; the
workflow preserves recoverable cache and artifacts even when a read fails.

## Selected engineering round receipt

Before marking a candidate READY, the existing owner can run the live execution
mode of the existing publication preflight in the **same cloud checkout that will
execute the task**:

```sh
python3 publication_preflight.py --repo OWNER/REPO --actor EXPECTED_LOGIN \
  --workdir /absolute/task/checkout --expected-head EXACT_40_HEX_SHA \
  --environment-id CURRENT_CLOUD_ENVIRONMENT_ID --require-tool python3 \
  --require-file PROJECT_LOCK_OR_INSTALLED_DEPENDENCY_FILE --test-entry tests/test_task.py
```

Repeat `--require-tool`, `--require-file`, and `--test-entry` for the project's
actual prerequisites. Select its pinned versions using the existing environment
workflow first; executable/file presence alone does not prove version compatibility
or that the prerequisite list is complete. This mode records this process's host,
checkout root and exact HEAD, live `gh api user` identity, resolved executables,
regular files inside that checkout, and actual inspection start/end times. The
environment ID is labeled caller context, not independently attested host identity.
Tests are not executed by this read-only preflight. Run the project's acceptance
tests separately and retain their exact SHA, exit codes and logs.

If the existing fork was renamed, add `--fork EXPECTED_LOGIN/RENAMED_REPOSITORY`
to the live command. The helper validates the selector before network reads and
fetches that existing repository through the same live `gh` channel when the
upstream does not report push access. Ownership, actual fork status, upstream
network and reported write access are still checked. A supplied name is never
permission to create a fork or take over a branch. Publication-only live calls
also accept this selector; `--from-json` cannot be combined with `--fork`.

`execution.local_work_ready` is separate from `path_candidate_present`.
`ready_candidate` requires both, while `pr_publication_verified` stays false until
an actual authorized PR and remote HEAD readback. A missing fork or denied
publication path leaves independently runnable local work available. Each missing
execution prerequisite has a concrete recovery condition. Exit 2 means at least
one READY prerequisite remains missing; inspect the separate fields rather than
treating it as a global stop. Continue other owned executable tickets. This mode
does not grant ownership, dispatch rights, create credentials, retry denials, or
write remotely. `--from-json` cannot substitute for the live same-host checks.

Existing publication-only calls retain their original output and exit semantics.
Using this mode once manually does not establish deployment, natural invocation,
complete dependency coverage, or automatic cross-environment recovery.

The original canonical writer can run `python3 round_receipt.py --context
context.json --receipt receipt.json` before closing its existing round. This
helper reads files only; it does not execute commands, dispatch work, store a
ledger, grant authority, publish, or save a Page. Retain its output in the same
canonical round and perform the normal conditional save/readback. It is not
wired into the probe or scheduler. The existing writer must actually invoke it;
adding the file alone does not establish live enforcement or Page persistence.

Both inputs are bounded UTF-8 JSON (256 KiB each), schema version 1. Duplicate
keys, unknown fields, duplicate task IDs and ambiguous results are rejected.
Fresh **context**, selected by the original writer from the existing Page, has
`schema_version`, `round_id`, `coverage` (a concrete reference/explanation of the
due scope and any gaps), and at most 64 `tickets`. Each context ticket has
`task_id`, upstream `repo` (`owner/name`), `eligible` (boolean), and `owner_state`
(`CURRENT` or `EXTERNAL`). Select the current due scope; historical tickets with
no due action need not be repeated. An empty due scope is allowed with explicit
coverage. Coverage and current ownership remain the writer's responsibility;
a caller omitting a task from both inputs cannot be detected by this helper.

For monetary engineering, invoke the same helper with `--cash-only`. Add a
`monetary_leads` object to the context, keyed by each selected eligible CURRENT
task ID. Each value has exactly `source_ref` (the canonical original monetary
promise or reward-program reference) and `source_quote` (its monetary wording).
Missing lead context or a selected current task without a lead is rejected,
including an ordinary task stopped by an unrelated tool error. A supplied
`monetary_leads` object always enables this check, even without the flag.
There is no amount, currency, funded-state, assignment or geography test.

Legacy ordinary-work calls remain supported and return
`cash_scope_enforced: false`; they cannot establish monetary coverage. Cash
calls return `cash_scope_enforced: true` only for this consistency contract.
Both return `monetary_lead_attested: false`: source quotes are caller assertions,
not proof of a real promise, eligibility, funding, execution or eventual payment.
The writer must still verify the original source and include every due monetary
task across bounded batches. Record permitted ordinary contributions separately;
their progress or blockers do not complete or block independent monetary work.

The **receipt** has `schema_version`, matching `round_id`, `outcome`, and
`tickets`. Every context ticket must have one result with `task_id`, `work`,
`publication` (null or an object), and `blockers` (possibly empty). `work` is:

- Observation only: `kind` is `NONE`, `READ`, `QUALIFICATION`, or `SETUP`, with
  `evidence` reference. These activities never count as engineering started.
- Task execution: `kind` is `COMMAND` or `CHANGE`, plus exact 40-character
  `head_sha`, `evidence` reference, `checks`, and `result` (`IN_PROGRESS`, `PASS`,
  or `FAIL`). A command has `argv`; a change has nonempty relative `paths`.
  Checks have `argv`, actual `exit_code`, matching `tested_sha`, and `log_ref`.
  The command's own argv must have a matching check/result. PASS needs at least
  one check and all exits zero; FAIL needs a nonzero check.
  A failed real task command still counts as started. Source/search/setup
  commands must use their observation classification even when a shell ran.

`publication`, when present, has `kind` (`UPSTREAM_PR` or
`FORK_VALIDATION_PR`), `repo`, an exact GitHub `/owner/repo/pull/NUMBER` `url`,
and `head_sha` matching the work. Upstream delivery requires the canonical
upstream repository; a fork validation PR never populates `upstream_submitted`.
A fork validation PR can coexist with blocked upstream publication.

Each blocker has `step` (`WORK` or `PUBLICATION`), concrete `tool`, `error`, and
`evidence` reference. It applies only to that step of that ticket. For example,
`PUBLICATION / gh pr create / HTTP 403 / log reference` cannot excuse missing
independent code/test progress. A missing supported executable entrance can be
reported as a WORK blocker naming the attempted tool and actual error. Preserve
existing external ownership; execution or publication on EXTERNAL/ineligible
tickets is rejected. The helper never asks for renewed approval for authorized
work and never authorizes duplicating an external owner's task.

Outputs include `actual_started`, `upstream_submitted`, `blocked`, and
`required_next`. Missing due engineering without a WORK blocker returns REJECTED
and exit 2. `IN_PROGRESS` can validly record real partial work; `BLOCKED` needs a
concrete blocker. `COMPLETED` claims upstream delivery with passing exact-head
checks for every selected eligible current-owner ticket and otherwise returns
REJECTED/exit 2. A valid blocked receipt preserves the blocker and next step;
exit 0 does not mean delivered. Malformed input also exits 2. Focused tests in
`test_round_receipt.py` contain complete minimal context/receipt examples.

This establishes **consistency, not truth or attestation**. Command arguments,
paths, URLs and logs are data references and are never executed or fetched. The
helper cannot prove a command ran, a diff is real, a log is authentic, a PR exists,
or a test checks the acceptance criteria. The original writer must classify
activities honestly, verify actual source/check/remote evidence, and use the
existing ownership, exact-commit review and publication gates. A receipt SHA
alone, a fork PR, source reading, or a successful validation invocation is never
proof of natural upstream delivery or canonical Page persistence.
