"""Build literal resume commands only from captured interactive launches."""
from __future__ import annotations

import json
from pathlib import Path
import re

SESSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}')
VALUE_FLAGS = {
    'codex': {'--model', '-m', '-c', '--config', '--profile', '-p', '--sandbox', '-s',
              '--ask-for-approval', '-a', '--cd', '-C', '--add-dir', '--enable', '--disable'},
    'claude': {'--model', '--effort', '--permission-mode', '--allowedTools', '--disallowedTools',
               '--settings', '--setting-sources', '--add-dir', '--mcp-config', '--agent',
               '--agents', '--append-system-prompt', '--system-prompt', '--tools'},
    'grok': {'--model', '--reasoning-effort', '--effort', '--config', '--profile', '--cwd'},
    'omp': {'--model', '--provider', '--thinking', '--config', '--profile', '--cwd',
            '--approval-mode', '--tools', '--extension', '-e', '--skill', '--prompt-template', '--theme'},
}
BOOLEAN_FLAGS = {
    'codex': {'--no-daemon', '--yolo', '--dangerously-bypass-approvals-and-sandbox',
              '--full-auto', '--no-alt-screen', '--search'},
    'claude': {'--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
               '--verbose', '--debug', '--no-session-persistence'},
    'grok': {'--yolo', '--always-approve', '--trust', '--no-alt-screen'},
    'omp': {'--auto-approve', '--allow-home', '--no-extensions', '--no-skills',
            '--no-prompt-templates', '--no-themes'},
}


def resume_argv(provider, argv, session_id):
    if provider not in VALUE_FLAGS or not SESSION.fullmatch(session_id) or not argv:
        raise ValueError('invalid provider or conversation identity')
    output = [argv[0]]
    index = 1
    old_resume = False
    while index < len(argv):
        item = argv[index]
        if provider == 'codex' and item == 'resume' and not old_resume:
            old_resume = True
            index += 1
            continue
        if item in ('--resume', '--session-id'):
            if index + 1 >= len(argv) or not SESSION.fullmatch(argv[index + 1]):
                raise ValueError('ambiguous prior conversation argument')
            index += 2
            continue
        if item.startswith(('--resume=', '--session-id=')):
            if not SESSION.fullmatch(item.partition('=')[2]):
                raise ValueError('ambiguous prior conversation argument')
            index += 1
            continue
        if provider == 'codex' and old_resume and SESSION.fullmatch(item) and not item.startswith('-'):
            old_resume = False
            index += 1
            continue
        option, equal, value = item.partition('=')
        if option in VALUE_FLAGS[provider]:
            if equal:
                if not value:
                    raise ValueError('empty captured option')
                output.append(item)
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise ValueError('missing captured option value')
                output.extend(argv[index:index + 2]); index += 2
        elif item in BOOLEAN_FLAGS[provider]:
            output.append(item); index += 1
        else:
            raise ValueError('cannot safely resume this captured launch option: ' + item)
    if provider == 'codex':
        if '--no-daemon' not in output:
            output.append('--no-daemon')
        output.extend(['resume', session_id])
    elif provider == 'omp':
        output.append('--resume=' + session_id)
    else:
        output.extend(['--resume', session_id])
    return output


def log_identity(provider, path):
    """Read only the already resolved log, never search for a newer session."""
    if provider == 'grok':
        sid = Path(path).parent.name
    else:
        ids = set()
        with Path(path).open('rb') as stream:
            # Headers are near the beginning. Missing identity refuses safely.
            for _ in range(100):
                line = stream.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    raise ValueError('oversized conversation header')
                row = json.loads(line)
                if provider == 'codex' and row.get('type') == 'session_meta':
                    value = row.get('payload', {}).get('id')
                elif provider == 'omp' and row.get('type') == 'session':
                    value = row.get('id')
                elif provider == 'claude':
                    value = row.get('sessionId')
                else:
                    value = None
                if value:
                    ids.add(value)
                if provider in ('codex', 'omp') and ids:
                    break
        if len(ids) != 1:
            raise ValueError('conversation log has no unambiguous identity')
        sid = ids.pop()
    if not isinstance(sid, str) or not SESSION.fullmatch(sid):
        raise ValueError('invalid conversation identity')
    return sid


def clean_environment(environment):
    # Preserve HOME, provider config roots and ordinary user runtime settings.
    stale = ('KITTY_', 'KILIX_RC_', 'KILIX_CALLER_', 'KILIX_BROKER_',
             'CODEX_THREAD', 'CODEX_SESSION', 'CODEX_INTERNAL', 'CODEX_TUI')
    exact = {'TMUX', 'TMUX_PANE', 'AGENT_SESSION_DIR', 'WINDOWID', 'TERM_SESSION_ID',
             'KILIX_AGENT_RUN_DIR', 'KILIX_CODING_LAUNCH_RECORD', 'KILIX_PANE_ID'}
    result = {key: value for key, value in environment.items()
              if not key.startswith(stale) and key not in exact}
    if environment.get('KILIX_AGENT_RUN_DIR'):
        for key in ('TMPDIR', 'TMP', 'TEMP'):
            if result.get(key, '').startswith(environment['KILIX_AGENT_RUN_DIR'] + '/'):
                result.pop(key, None)
    return result
