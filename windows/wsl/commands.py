"""Platform adapters for the shared Kilix command implementations."""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'wsl-bin'
sys.path.insert(0, str(SHARED / 'config'))


def load_file(name, path):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def settings(args):
    module = load_file('wsl_settings', SHARED / 'kilix-settings')
    # KITTY_PID is a Windows PID. Sending it a Linux signal can hit an
    # unrelated process. WSL settings belong to Linux companion consumers;
    # native renderer configuration is exposed by `kilix native settings`.
    module._notify_running_kilix = lambda: None
    module.UI_SECTIONS = module.SECTION_SPECS
    module.SECTION_ALIASES = module._section_aliases()

    def unavailable(*_args):
        raise ValueError('This tool needs a separate Linux installation. '
                         'Use kilix models --help for receipt-gated model setup.')

    module._model_install_argv = unavailable
    module._exec_tool = unavailable
    return module.main(args)


def main(verb, args):
    sys.argv[0] = 'kilix ' + verb
    if verb == 'settings':
        return settings(args)
    if verb == 'models':
        import content_models
        import companion
        # The shared default is the Linux desktop installer, not this
        # companion's model store. Its explicit --root still takes priority.
        content_models.apps_root = lambda: str((companion.STATE / 'content').resolve())
        return content_models.main(args)
    if verb == 'tmux':
        import tmux_control
        if not args or args in (['--help'], ['-h']):
            print(tmux_control.HELP.replace(
                'With no arguments, kilix tmux opens the interactive Tmux Manager.',
                'With no arguments, the WSL companion shows this help. '
                'Install tmux in this distribution before using a socket.'))
            return 0
        return tmux_control.main(args)
    if verb in ('agent-control', 'action'):
        import agent_adapter
        agent_adapter.install()
        if verb == 'agent-control':
            import agent_control
            return agent_control.main(args)
        import agent_actions
        capabilities = agent_actions.capabilities
        agent_actions.capabilities = lambda: {
            **capabilities(), 'identity_kind': 'windows-instance-pane',
            'foreground_scope': 'invoking-wsl-distribution-and-user',
            'input_requires_observed_readiness': True}
        return agent_actions.main(args)
    if verb == 'transcript':
        import transcripts
        return transcripts.main(args, shared=SHARED)
    raise ValueError(f'Unknown shared command: {verb}')


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1], sys.argv[2:]))
    except (OSError, ValueError, ImportError, subprocess.TimeoutExpired) as error:
        print(f'kilix: {error}', file=sys.stderr)
        raise SystemExit(2)
