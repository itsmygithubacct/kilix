"""Explicit optional OS software setup for licensed wizard selections."""
from pathlib import Path
import os
import stat
import subprocess


# OS provision delivers these root-controlled helpers. Their --dry-run output
# owns the selected software URL, byte count, checksum and prerequisites.
OS_INSTALLERS = {
    'kilix-diarization-convert': '/usr/local/sbin/plebian-os-install-diarization-converter',
    'pocket-tts-convert-hf-to-gguf': '/usr/local/sbin/plebian-os-install-ollama-converter',
}


def checked_helper(name):
    path = Path(name)
    for entry in (*reversed(path.parents), path):
        info = entry.lstat()
        expected_type = stat.S_ISREG if entry == path else stat.S_ISDIR
        if not expected_type(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f'Optional software installer is not root-controlled: {entry}')
    if not os.access(path, os.X_OK):
        raise ValueError(f'Optional software installer is not executable: {path}')
    return str(path)


def prepare_converters(api, installer, specs, *, input_fn):
    """Preflight every accepted converter before the first model download.

    Called only by finish after model licence decisions, never by listing,
    staging choices or declining a licence. Failure leaves the batch pending.
    """
    from wizard_licenses import ask
    report = lambda message: print(message, flush=True)
    for spec in specs:
        if spec.source_mode != 'upstream-convert':
            continue
        try:
            installer.resolve_converter(spec, report)
        except api.ConverterPrerequisiteError as error:
            helper = OS_INSTALLERS.get(error.tool_asset_id)
            if helper is None:
                raise
            try:
                helper = checked_helper(helper)
            except OSError as missing:
                raise ValueError(f'{spec.asset_id} needs {helper}. Install the Plebian OS '
                                 'optional converter helper, then retry kilix wizard finish. '
                                 'Your model choices remain pending.') from missing
            print(f'\n{spec.label} needs optional conversion software.')
            print('Software download details (model weights and licence receipts are separate):', flush=True)
            try:
                subprocess.run([helper, '--dry-run'], check=True)
                print(f'The next step runs: /usr/bin/sudo {helper}', flush=True)
                if ask('Install this optional software now using administrator privileges?', input_fn) != 'yes':
                    raise ValueError('Optional software was not installed. Your model choices remain '
                                     'pending; retry kilix wizard finish, or revisit and decline this model.')
                subprocess.run(['/usr/bin/sudo', helper], check=True)
                # Prove the selected converter resolves after a successful helper.
                installer.resolve_converter(spec, report)
            except (OSError, subprocess.CalledProcessError, api.ConverterPrerequisiteError) as failure:
                raise ValueError(f'Optional software setup for {spec.asset_id} failed. '
                                 'Your model choices remain pending; retry kilix wizard finish.') from failure
