"""Bounded, read-only PTY facts; these never authorize an end operation.

Marker order is RAW PTY BYTES at broker journal-commit observation time. It is
not application write order or rendered terminal text.
"""
from __future__ import annotations

import bisect
import contextlib
from decimal import Decimal, ROUND_FLOOR
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time

import kilix_pty as pty
from kilix_pty_pane_lookup import bounded_query, LookupFailure

SCHEMA = 'kilix.pty.semantic/v1'
MARKER_MAX = 128
SESSION_MAX = 8
JOURNAL_MAX = 1024 * 1024
INDEX_RECORD_MAX = 4096
INDEX_MAX = INDEX_RECORD_MAX * 160
WORK_SECONDS = 10.0
RESULT_MAX = 24576
PROC_SCAN_MAX = 4096
ARGV_MAX = 4096
ARGV_COUNT_MAX = 32
ARG_MAX = 256


class Unknown(Exception):
    def __init__(self, reason):
        self.reason = reason


def _time_left(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise Unknown('deadline')
    return left


def _broker_json(broker, deadline, *argv):
    # Native broker timeout has a 100 ms minimum. Refuse before launch when
    # the one absolute work budget cannot fit it, and use the same endpoint for
    # output capture and owned-child cleanup.
    remaining = _time_left(deadline)
    native_timeout = Decimal.from_float(min(remaining, broker.timeout_seconds)).quantize(
        Decimal('.000001'), rounding=ROUND_FLOOR)
    if native_timeout < Decimal('.1'):
        raise Unknown('deadline')
    command = [broker.path, '--runtime-dir', broker.runtime, '--timeout',
               format(native_timeout, '.6f'), *argv]
    try:
        raw = bounded_query(command, getattr(broker, 'environment', None), deadline)
    except LookupFailure as error:
        reason = {'pane_lookup_timeout': 'deadline',
                  'pane_lookup_cleanup_timeout': 'broker_cleanup_timeout',
                  'pane_lookup_overflow': 'broker_output_cap'}.get(str(error), 'broker_unavailable')
        raise Unknown(reason) from None
    except OSError:
        raise Unknown('broker_unavailable') from None
    _time_left(deadline)
    if len(raw) > 131072:
        raise Unknown('broker_output_cap')
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError):
        raise Unknown('broker_malformed') from None
    _time_left(deadline)
    return parsed


def _census(broker, deadline):
    rows = _broker_json(broker, deadline, 'list', '--json', '--all')
    if not isinstance(rows, list):
        raise Unknown('census_malformed')
    if len(rows) > SESSION_MAX:
        raise Unknown('candidate_overflow')
    candidates = []
    for row in rows:
        if (not isinstance(row, dict) or row.get('reachable') is False or
                not isinstance(row.get('id'), str) or
                not pty.SESSION_ID.fullmatch(row['id']) or row['id'] in ('.', '..')):
            raise Unknown('census_incomplete')
        candidates.append(row['id'])
    if len(set(candidates)) != len(candidates):
        raise Unknown('census_ambiguous')
    return sorted(candidates)


def _status(broker, deadline, ident):
    row = _broker_json(broker, deadline, 'status', ident, '--json')
    if (not isinstance(row, dict) or row.get('id') != ident or
            any(type(row.get(k)) is not int for k in
                ('broker_pid', 'child_pid', 'foreground_pgrp', 'started_millis',
                 'journal_bytes', 'journal_epoch')) or
            type(row.get('replay_complete')) is not bool):
        raise Unknown('status_malformed')
    return row


def _private_file(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=directory_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise Unknown('index_untrusted')
    return fd


def _same_clock_domain(broker_pid):
    try:
        own = os.stat('/proc/self/ns/time')
        other = os.stat(f'/proc/{broker_pid}/ns/time')
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        raise Unknown('clock_unknown') from None
    if (own.st_dev, own.st_ino) != (other.st_dev, other.st_ino) or not boot:
        raise Unknown('clock_incomparable')
    return boot, f'{own.st_dev}:{own.st_ino}'


def _read_index_and_journal(broker, row, deadline):
    if not row['replay_complete'] or row['journal_epoch'] != 0:
        raise Unknown('history_incomplete')
    if row['journal_bytes'] > JOURNAL_MAX:
        raise Unknown('scan_cap')
    with contextlib.ExitStack() as files:
        try:
            runtime_fd = os.open(broker.runtime, os.O_RDONLY | os.O_DIRECTORY |
                                 os.O_NOFOLLOW | os.O_CLOEXEC)
            files.callback(os.close, runtime_fd)
            sessions_fd = os.open('sessions', os.O_RDONLY | os.O_DIRECTORY |
                                  os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=runtime_fd)
            files.callback(os.close, sessions_fd)
            directory_fd = os.open(row['id'], os.O_RDONLY | os.O_DIRECTORY |
                                   os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=sessions_fd)
            files.callback(os.close, directory_fd)
        except OSError:
            raise Unknown('session_path_unavailable') from None
        with contextlib.ExitStack() as opened:
            try:
                index_fd = _private_file(directory_fd, 'journal.time')
                opened.callback(os.close, index_fd)
                journal_fd = _private_file(directory_fd, 'journal.bin')
                opened.callback(os.close, journal_fd)
            except OSError:
                raise Unknown('index_unavailable') from None
            while True:
                _time_left(deadline)
                try:
                    fcntl.flock(index_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(.005, _time_left(deadline)))
            try:
                index_size = os.fstat(index_fd).st_size
                journal_size = os.fstat(journal_fd).st_size
                if index_size > INDEX_MAX or journal_size > JOURNAL_MAX:
                    raise Unknown('scan_cap')
                if journal_size != row['journal_bytes']:
                    raise Unknown('unstable_snapshot')
                index = os.pread(index_fd, index_size, 0)
                journal = os.pread(journal_fd, journal_size, 0)
                if len(index) != index_size or len(journal) != journal_size:
                    raise Unknown('unstable_snapshot')
            finally:
                fcntl.flock(index_fd, fcntl.LOCK_UN)
    if index and not index.endswith(b'\n'):
        raise Unknown('index_partial')
    lines = index.splitlines()
    if len(lines) > INDEX_RECORD_MAX:
        raise Unknown('index_cap')
    chunks = []
    previous = 0
    previous_time = 0
    for index, line in enumerate(lines):
        if index % 64 == 0:
            _time_left(deadline)
        parts = line.split(b' ')
        if (len(line) > 160 or len(parts) != 5 or
                any(not 1 <= len(part) <= 20 or not re.fullmatch(rb'[0-9]+', part)
                    for part in parts)):
            raise Unknown('index_malformed')
        started, epoch, begin, end, observed = map(int, parts)
        if (started != row['started_millis'] or epoch != row['journal_epoch'] or
                begin != previous or end <= begin or end > journal_size or
                observed < previous_time or observed > 2**63 - 1):
            raise Unknown('index_gap')
        chunks.append((end, observed))
        previous, previous_time = end, observed
    if previous != journal_size:
        raise Unknown('index_gap')
    _time_left(deadline)
    return journal, chunks


def _last_occurrence(journal, chunks, marker, cutoff_ns, deadline=None):
    ends = [end for end, _ in chunks]
    last = None
    offset = journal.find(marker)
    checked = 0
    while offset >= 0:
        if deadline is not None and checked % 1024 == 0:
            _time_left(deadline)
        end_offset = offset + len(marker)
        index = bisect.bisect_left(ends, end_offset)
        if index >= len(chunks):
            raise Unknown('index_gap')
        observed_ns = chunks[index][1]
        if observed_ns <= cutoff_ns:
            last = (observed_ns, end_offset)
        offset = journal.find(marker, offset + 1)
        checked += 1
    if deadline is not None:
        _time_left(deadline)
    return last


def marker_occurrences(broker, marker):
    """Whole live-session census; no actor-selected subset or action grant."""
    if (type(marker) is not str or not marker.isascii() or
            not 1 <= len(marker.encode()) <= MARKER_MAX or
            any(ord(c) < 33 or ord(c) > 126 for c in marker)):
        raise ValueError('marker must be 1..128 printable non-space ASCII bytes')
    deadline = time.monotonic() + min(WORK_SECONDS, broker.guard)
    result = {'schema': SCHEMA, 'operation': 'marker_occurrences',
              'match_mode': 'raw_pty_bytes', 'criterion': 'broker_observed_output',
              'candidate_scope': 'complete_live_session_census', 'marker': marker,
              'result': 'unknown', 'reason': None, 'winner_session_id': None,
              'sessions': [], 'candidate_ids': [], 'cutoff_monotonic_ns': None,
              'clock_boot_id': None, 'clock_time_namespace': None}
    try:
        sessions_dir = os.path.join(broker.runtime, 'sessions')
        before_census = os.stat(sessions_dir)
        candidates = _census(broker, deadline)
        cutoff_ns = time.monotonic_ns()
        result['cutoff_monotonic_ns'] = cutoff_ns
        result['candidate_ids'] = candidates
        boot_domain = None
        winners = []
        for ident in candidates:
            row = _status(broker, deadline, ident)
            domain = _same_clock_domain(row['broker_pid'])
            broker_start_ticks = _proc_stat(row['broker_pid'])['start_ticks']
            if boot_domain is None:
                boot_domain = domain
            elif domain != boot_domain:
                raise Unknown('clock_incomparable')
            journal, chunks = _read_index_and_journal(broker, row, deadline)
            last = _last_occurrence(journal, chunks, marker.encode('ascii'), cutoff_ns, deadline)
            again = _status(broker, deadline, ident)
            if any(again[k] != row[k] for k in ('broker_pid', 'started_millis', 'journal_epoch',
                                                'journal_bytes', 'replay_complete')):
                raise Unknown('unstable_snapshot')
            if _proc_stat(row['broker_pid'])['start_ticks'] != broker_start_ticks:
                raise Unknown('incarnation_changed')
            record = {'id': ident, 'started_millis': row['started_millis'],
                      'broker_start_ticks': broker_start_ticks,
                      'journal_epoch': row['journal_epoch'],
                      'occurrence': 'found' if last else 'absent',
                      'latest_end_offset': last[1] if last else None,
                      'latest_observed_monotonic_ns': last[0] if last else None}
            result['sessions'].append(record)
            if last:
                winners.append((last[0], ident))
        if _census(broker, deadline) != candidates:
            raise Unknown('census_changed')
        after_census = os.stat(sessions_dir)
        if any(getattr(before_census, key) != getattr(after_census, key) for key in
               ('st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns')):
            raise Unknown('census_changed')
        _time_left(deadline)
        result['clock_boot_id'] = boot_domain[0] if boot_domain else None
        result['clock_time_namespace'] = boot_domain[1] if boot_domain else None
        if not winners:
            result['result'] = 'no_match'
        else:
            latest = max(time_ns for time_ns, _ in winners)
            top = [ident for time_ns, ident in winners if time_ns == latest]
            if len(top) != 1:
                raise Unknown('tie')
            result['result'] = 'unique'
            result['winner_session_id'] = top[0]
    except OSError:
        result['result'] = 'unknown'
        result['reason'] = 'census_unavailable'
        result['winner_session_id'] = None
    except Unknown as error:
        result['result'] = 'unknown'
        result['reason'] = error.reason
        result['winner_session_id'] = None
    if len(json.dumps(result, ensure_ascii=True).encode()) > RESULT_MAX:
        result = {k: result[k] for k in ('schema', 'operation', 'match_mode', 'criterion',
                                        'candidate_scope', 'marker')}
        result.update(result='unknown', reason='result_cap', winner_session_id=None,
                      sessions=[], candidate_ids=[], cutoff_monotonic_ns=None,
                      clock_boot_id=None, clock_time_namespace=None)
    return result


def _proc_stat(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_bytes()
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ESRCH):
            raise Unknown('proc_gone') from None
        raise Unknown('proc_unavailable') from None
    if len(raw) > 4096 or b') ' not in raw:
        raise Unknown('proc_malformed')
    head, tail = raw.rsplit(b') ', 1)
    try:
        if int(head.split(b' (', 1)[0]) != pid:
            raise ValueError('pid mismatch')
        fields = tail.split()
        return {'pid': pid, 'ppid': int(fields[1]), 'pgrp': int(fields[2]),
                'os_session_id': int(fields[3]), 'tty_nr': int(fields[4]),
                'start_ticks': int(fields[19])}
    except (ValueError, IndexError):
        raise Unknown('proc_malformed') from None


def _argv(pid):
    try:
        with open(f'/proc/{pid}/cmdline', 'rb', buffering=0) as stream:
            raw = stream.read(ARGV_MAX + 1)
    except OSError:
        raise Unknown('argv_unavailable') from None
    if len(raw) > ARGV_MAX:
        raise Unknown('argv_cap')
    if not raw:
        raise Unknown('argv_empty')
    if raw[-1:] != b'\0':
        raise Unknown('argv_unterminated')
    parts = raw[:-1].split(b'\0')
    if len(parts) > ARGV_COUNT_MAX or any(len(part) > ARG_MAX for part in parts):
        raise Unknown('argv_cap')
    try:
        return [part.decode('utf-8', errors='strict') for part in parts]
    except UnicodeError:
        raise Unknown('argv_invalid_utf8') from None


def _foreground_candidates(child, foreground, deadline):
    pids = []
    with os.scandir('/proc') as entries:
        for entry in entries:
            if entry.name.isdecimal():
                pids.append(int(entry.name))
                if len(pids) > PROC_SCAN_MAX:
                    raise Unknown('proc_scan_cap')
    members = {}
    for pid in sorted(pids):
        _time_left(deadline)
        try:
            sampled = _proc_stat(pid)
        except Unknown as error:
            if error.reason == 'proc_gone':
                # The sample is taken after this process disappeared. A
                # second complete membership scan below checks stability.
                continue
            raise
        if (sampled['pgrp'] == foreground and
                sampled['os_session_id'] == child['os_session_id'] and
                sampled['tty_nr'] == child['tty_nr']):
            if len(members) >= ARGV_COUNT_MAX:
                raise Unknown('process_result_cap')
            members[pid] = sampled
    return members


def foreground_sample(broker, ident):
    if type(ident) is not str or not pty.SESSION_ID.fullmatch(ident) or ident in ('.', '..'):
        raise ValueError('invalid exact session ID')
    deadline = time.monotonic() + min(WORK_SECONDS, broker.guard)
    result = {'schema': SCHEMA, 'operation': 'foreground_process_sample',
              'id': ident, 'state': 'unknown', 'reason': None,
              'launch_command': None, 'child_cwd_now': None,
              'sampled_child_command': None, 'foreground_processes': [],
              'singular_foreground_command': None, 'subject': None,
              'sampled_at_monotonic_ns': None}
    try:
        row = _status(broker, deadline, ident)
        result['launch_command'] = row.get('command') if type(row.get('command')) is str else None
        result['child_cwd_now'] = row.get('cwd_now') if type(row.get('cwd_now')) is str else None
        child_pid = row['child_pid']
        foreground = row['foreground_pgrp']
        if child_pid <= 0 or foreground <= 0:
            raise Unknown('process_unavailable')
        child = _proc_stat(child_pid)
        if child['tty_nr'] == 0:
            raise Unknown('tty_unavailable')
        result['subject'] = {'broker_pid': row['broker_pid'], 'child_pid': child_pid,
                             'child_start_ticks': child['start_ticks'],
                             'session_started_millis': row['started_millis'],
                             'foreground_pgrp': foreground,
                             'os_session_id': child['os_session_id'], 'tty_nr': child['tty_nr']}
        result['sampled_at_monotonic_ns'] = time.monotonic_ns()
        child_argv = _argv(child_pid)
        if _proc_stat(child_pid) != child:
            raise Unknown('child_changed')
        result['sampled_child_command'] = {'pid': child_pid,
                                           'start_ticks': child['start_ticks'],
                                           'argv': child_argv, 'state': 'sampled'}
        first_members = _foreground_candidates(child, foreground, deadline)
        for pid, first in first_members.items():
            argv = _argv(pid)
            if _proc_stat(pid) != first:
                raise Unknown('foreground_changed')
            result['foreground_processes'].append({'pid': pid,
                                                    'start_ticks': first['start_ticks'],
                                                    'argv': argv, 'state': 'sampled'})
        if _foreground_candidates(child, foreground, deadline) != first_members:
            raise Unknown('foreground_changed')
        again = _status(broker, deadline, ident)
        if any(again[k] != row[k] for k in ('broker_pid', 'child_pid', 'foreground_pgrp',
                                           'started_millis')) or _proc_stat(child_pid) != child:
            raise Unknown('foreground_changed')
        _time_left(deadline)
        if not result['foreground_processes']:
            raise Unknown('foreground_missing')
        result['state'] = 'complete'
        if len(result['foreground_processes']) == 1:
            result['singular_foreground_command'] = result['foreground_processes'][0]['argv']
    except Unknown as error:
        result['state'] = 'unknown'
        result['reason'] = error.reason
        result['singular_foreground_command'] = None
    except OSError:
        result['state'] = 'unknown'
        result['reason'] = 'proc_unavailable'
        result['singular_foreground_command'] = None
    if len(json.dumps(result, ensure_ascii=True).encode()) > RESULT_MAX:
        result['state'] = 'unknown'
        result['reason'] = 'result_cap'
        result['sampled_child_command'] = None
        result['foreground_processes'] = []
        result['singular_foreground_command'] = None
    return result
