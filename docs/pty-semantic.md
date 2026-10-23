# PTY semantic reads (private candidate)

These are additive read-only observations. `status.command` remains the recorded
launch command. Neither read issues, renews, or consumes a session-end grant.
The trusted controller admits `host_read` with the same read-only isolation as
`list`, `status`, and `observe`; actor arguments never supply timestamps,
candidate subsets, broker paths, or process identity. Ordinary `kilix pty`
exposes the same data through `marker TOKEN --json` and `foreground ID --json`.
The host JSON request route also accepts
`{"schema":"kilix.pty.request/v1","verb":"marker","args":{"marker":"TOKEN"}}`
and `{"schema":"kilix.pty.request/v1","verb":"foreground","args":{"id":"ID"}}`.
The supervisor's constrained broker facade forwards only these exact read
forms to the trusted host reader. Needle's structured requests are exactly
`{"operation":"marker","marker":"TOKEN"}`
and `{"operation":"foreground","id":"ID"}`. Needle also accepts the explicit
forms `which session last showed raw marker TOKEN` and
`show foreground processes of session ID`; it does not map the original
“most recently printed” wording to a raw-marker result.

Both replies use `kilix.pty.semantic/v1` and exit zero even for a typed unknown.
Malformed requests exit 2. Marker is 1–128 printable non-space ASCII bytes.
`marker_occurrences` has no ID list: it attempts the complete live broker
`list --all` census, refuses any unreachable/malformed candidate, and returns
unknown on more than eight sessions. Its `candidate_ids` disclose the admitted
scope when available. A complete candidate with no marker has `occurrence:
"absent"` and does not block another candidate's unique result. If all are
absent, `result` is `no_match`. Any incomplete candidate, tie, changed census,
or failed read gives `result:"unknown"`, a non-null `reason`, and a null winner.

`marker_occurrences` reply fields are `schema`, `operation`, `match_mode` (always
`raw_pty_bytes`), `criterion` (always `broker_observed_output`),
`candidate_scope` (always `complete_live_session_census`), `marker`, `result`
(`unique|no_match|unknown`), `reason` (null unless unknown),
`winner_session_id` (string only for unique), `sessions` (array, at most eight),
`candidate_ids`, `cutoff_monotonic_ns`, `clock_boot_id`, and
`clock_time_namespace`. These keys are always present; unavailable values are
empty arrays or null. Each session record has `id`, `started_millis`,
`broker_start_ticks`, `journal_epoch`,
`occurrence` (`found|absent`), `latest_end_offset` and
`latest_observed_monotonic_ns` (integers only when found; otherwise null).
All time numbers here are nanoseconds on Linux `CLOCK_MONOTONIC` in a verified
common time namespace and boot. Equal latest timestamps across sessions are
unknown, even when offsets differ. Within one session the last byte offset
orders same-timestamp occurrences.
Marker `reason` is null or one of `deadline`, `broker_unavailable`,
`broker_cleanup_timeout`, `broker_output_cap`, `broker_malformed`,
`census_unavailable`, `census_malformed`,
`candidate_overflow`, `census_incomplete`, `census_ambiguous`,
`census_changed`, `status_malformed`, `clock_unknown`,
`clock_incomparable`, `incarnation_changed`, `history_incomplete`,
`session_path_unavailable`, `index_unavailable`, `index_untrusted`,
`index_partial`, `index_malformed`, `index_gap`, `index_cap`, `scan_cap`,
`unstable_snapshot`, `proc_gone`, `proc_unavailable`, `proc_malformed`,
`tie`, or `result_cap`.

The broker creates private `journal.time` beside `journal.bin`. Each ASCII line
is exactly five unsigned decimal fields separated by one space and a newline:
`started_millis epoch begin_offset end_offset observed_monotonic_ns`. Its
exclusive nonblocking file lock covers journal and index append. The reader
takes a shared lock and validates complete contiguous indexed coverage of
the journal through the read watermark. A failed, skipped, partial, capped,
or interrupted index append creates a gap and makes ranking unknown; live PTY
output continues. A journal rollover marks replay incomplete, so ranking is
unknown even if retained bytes contain a marker. A restarted or unreachable
broker cannot be a complete live candidate. No status wire field changes.

The query takes one cutoff after the first census, reads every candidate after
that cutoff under a shared index lock, checks status before and after, and
requires a second identical census and unchanged sessions-directory identity
and modification times. A post-cutoff append can therefore yield unknown
rather than a stale ranking. The read deadline is one absolute endpoint, at
most ten seconds (or the caller's smaller guard); all broker calls and scans
share it. Native calls refuse before launch if less than the broker's 100 ms
minimum remains. Captured output is bounded, and owned CLI cleanup has one
fixed deadline at most one second after the work endpoint. Each journal scan
is at most 1 MiB; each index at most 4096 records
and 640 KiB; the result at most 24 KiB. Caps never narrow the candidate set
or silently truncate a positive response.

This orders **broker-observed raw PTY bytes**. Broker scheduling can invert the
order of application writes, and raw bytes can differ from rendered text.
It does not qualify the original “most recently printed” wording or change
its grading.

`foreground_process_sample` takes one exact session ID. The reply contains
`schema`, `operation`, `id`, `state` (`complete|unknown`), `reason` (null for
complete), `launch_command`, `child_cwd_now`, `sampled_child_command`,
`foreground_processes` (array), and `singular_foreground_command` (argv array
only when exactly one stable foreground process; otherwise null). `subject`
identifies broker PID, child PID/start ticks, session
start milliseconds, foreground process group, OS session ID, and PTY number;
`sampled_at_monotonic_ns` records the host sample time. Both are null when
unavailable. These are process
samples, not authority or a guarantee that a short-lived job is still running
when the reply is read.
The sampled child is null or an object with integer `pid`, integer
`start_ticks`, UTF-8 `argv` array, and `state:"sampled"`. Each foreground
member has the same four fields. `foreground_processes` is empty on an early
unknown and may contain partial facts on a later unknown; neither supports
a singular answer. The `reason` for unknown is one of `deadline`,
`broker_unavailable`, `broker_cleanup_timeout`, `broker_output_cap`,
`broker_malformed`, `status_malformed`, `process_unavailable`,
`tty_unavailable`, `proc_gone`, `proc_unavailable`,
`proc_malformed`, `proc_scan_cap`, `process_result_cap`, `argv_unavailable`,
`argv_empty`, `argv_unterminated`, `argv_invalid_utf8`, `argv_cap`,
`child_changed`, `foreground_changed`, `foreground_missing`, or
`result_cap`.

The reader scans at most 4096 numeric `/proc` entries twice. It filters by
the selected child's OS session, PTY number, and broker-reported foreground
group **before** reading argv, rechecks every member's PID/start ticks/group/
session/PTY after argv, then checks the membership set, child, and broker
foreground group again. At most 32 members are returned. Each cmdline is at
most 4096 bytes, 32 NUL-terminated arguments, and 256 bytes per argument;
UTF-8 must decode strictly. Empty, unterminated, invalid, overlong, or
unreadable cmdlines make the sample unknown, with no lossy singular fallback.
The child cwd is never reported as the foreground job's cwd.
