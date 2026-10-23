A valid CSV field containing a newline is currently split into separate records by the pre-commit sort. For example, `z,"first\nlast"` and `a,plain` become three parsed records and the original field is truncated. A quoted delimiter before a non-leading slug column also produces the wrong sort key.

Use Python's CSV parser to identify logical record boundaries and slug values, while retaining each record's original source text. This preserves quoting and embedded line endings and keeps an unterminated final record separate after reordering. Malformed unterminated CSV fails before that file is rewritten or staged.

Validation at `61183af412d1edd0e4f657c68a02e316272d9fad`:
- `python3 -B -m unittest discover -s git-hooks/tests -v`: 12 tests pass, with actual temporary Git staging. Seven of the initial ten regressions fail against the original sorter.
- An isolated archive of current main contains 959 CSV files. Sorting changes zero existing bytes, and a second sort is identical in both the working tree and Git index.
- Independent boundary probes passed 1,458 combinations of LF/CRLF/CR, blank records, multiline fields and terminal-newline presence.
- Python compilation and `git diff --check` pass. The network-downloading full hook and hosted CI have not run.

No current main CSV contains a multiline record; the trigger is valid future contributor input. This change keeps the existing staging scope. Related PRs #3827 and #3995 address staging behavior and overlap the same function, but their inspected patches still parse physical lines; maintainer coordination may be needed if either lands first.

AI assistance was used for implementation and review. This code change has no confirmed grant eligibility; no database fields or reward claim are included.

