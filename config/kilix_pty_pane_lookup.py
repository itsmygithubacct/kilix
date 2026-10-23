"""Supervisor-only owned-pane data lookup. Never mounts desktop authority."""
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import time

import kilix_pty_selector as selector


class LookupFailure(ValueError):
    pass


def bounded_ls(command, environment, deadline):
    """One fixed ls child, byte limits and one absolute deadline, owned cleanup."""
    process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    try:
        with selectors.DefaultSelector() as events:
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                events.register(stream, selectors.EVENT_READ)
            while events.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LookupFailure('pane_lookup_timeout')
                for key, _ in events.select(min(remaining, .1)):
                    data = os.read(key.fileobj.fileno(), 16384)
                    if not data:
                        events.unregister(key.fileobj)
                        continue
                    output = streams[key.fileobj]
                    output.extend(data)
                    limit = 1048576 if key.fileobj is process.stdout else 16384
                    if len(output) > limit:
                        raise LookupFailure('pane_lookup_overflow')
        try:
            code = process.wait(timeout=max(.001, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise LookupFailure('pane_lookup_timeout') from None
        if code:
            raise LookupFailure('pane_lookup_unavailable')
        return bytes(streams[process.stdout])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        for stream in streams:
            stream.close()


class PaneLookup:
    def __init__(self, owned_panes):
        if (not 1 <= len(owned_panes) <= 64 or any(type(p) is not int or not 0 <= p <= 2147483647
                                                for p in owned_panes)):
            raise ValueError('owned-pane admission requires1..64 valid pane IDs')
        self.owned = frozenset(owned_panes)
        from kilix_sdk import panes
        from kilix_sdk.frontend_context import refresh
        refresh()
        executable = shutil.which(panes.KITTEN)
        if executable is None:
            raise ValueError('trusted pane client unavailable')
        executable = Path(executable).resolve(strict=True)
        info = executable.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022):
            raise ValueError('unsafe trusted pane client')
        self.command = [str(executable), '@']
        password = os.environ.get('KILIX_RC_PASSWORD_FILE') or panes.RC_PASSWORD_FILE
        if password:
            info = os.lstat(password)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise ValueError('unsafe pane credential file')
            self.command += ['--password-file', password]
        self.command += ['ls']
        self.environment = dict(os.environ)

    def snapshot(self, wanted, deadline):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise LookupFailure('pane_lookup_malformed')
                result[key] = value
            return result
        try:
            state = json.loads(bounded_ls(self.command, self.environment, deadline),
                               object_pairs_hook=pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(LookupFailure('pane_lookup_malformed')))
            if not isinstance(state, list):
                raise LookupFailure('pane_lookup_malformed')
            found = []
            for window in state:
                for tab in window['tabs']:
                    for pane in tab['windows']:
                        if type(pane['id']) is not int:
                            raise LookupFailure('pane_lookup_malformed')
                        if pane['id'] == wanted:
                            found.append(pane)
            if not found:
                raise LookupFailure('pane_not_found')
            if len(found) != 1:
                raise LookupFailure('pane_lookup_ambiguous')
            from kilix_sdk import panes
            parsed = [p for p in panes.parse(state).panes() if p.id == wanted]
            if len(parsed) != 1:
                raise LookupFailure('pane_lookup_ambiguous')
            ident = parsed[0].broker_session
            if not selector.valid_id(ident):
                raise LookupFailure('pane_no_persistent_session')
            return ident
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError):
            raise LookupFailure('pane_lookup_malformed') from None

    def lookup(self, pane_id, broker):
        if pane_id not in self.owned:
            return 3, broker.envelope(result='refused', reason='pane_not_admitted', pane_id=pane_id)
        previous_timeout = broker.timeout
        deadline = time.monotonic() + min(5, float(previous_timeout or 5))
        try:
            ident = self.snapshot(pane_id, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LookupFailure('pane_lookup_timeout')
            # Native parse_timeout bounds decimal precision; Python's arbitrary
            # float repr can exceed it and turn an available session into error.
            broker.timeout = format(max(.1, min(remaining, 5)), '.6f')
            code, raw, _ = broker.call('status', ident, '--json')
            if code != 0:
                raise LookupFailure('pane_session_unavailable')
            try:
                session = json.loads(raw)
            except (ValueError, UnicodeError):
                raise LookupFailure('pane_session_unknown') from None
            if (not isinstance(session, dict) or session.get('id') != ident
                    or type(session.get('started_millis')) is not int or session['started_millis'] < 0):
                raise LookupFailure('pane_session_unknown')
            if self.snapshot(pane_id, deadline) != ident:
                raise LookupFailure('pane_mapping_changed')
            if time.monotonic() >= deadline:
                raise LookupFailure('pane_lookup_timeout')
            return 0, broker.envelope(pane_id=pane_id, id=ident,
                                     started_millis=session['started_millis'],
                                     observed_monotonic=time.monotonic(), sampled=True)
        except LookupFailure as error:
            return 4, broker.envelope(result='unknown', reason=str(error), pane_id=pane_id)
        except OSError:
            return 4, broker.envelope(result='unknown', reason='pane_lookup_unavailable', pane_id=pane_id)
        finally:
            broker.timeout = previous_timeout
