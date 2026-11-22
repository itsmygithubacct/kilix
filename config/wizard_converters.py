"""Check OS-installed conversion software before acquiring selected models."""

import os
from pathlib import Path
import subprocess

ENCODEC_HELPER = Path('/usr/sbin/plebian-os-install-encodec-converter')
ENCODEC_REQUESTED = Path('/var/lib/plebian-os/encodec-converter-requested')
ENCODEC_MODELS = {'encodec-24khz-stateful', 'encodec-48khz-frame'}


def encodec_download_bytes():
    """Read the managed OS archive pin without creating a store or downloading."""
    if not ENCODEC_HELPER.is_file():
        return None
    try:
        result = subprocess.run([str(ENCODEC_HELPER), '--download-bytes'],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if result.returncode or not value.isdecimal():
        return None
    return int(value)


def needs_encodec_model(root, spec):
    return spec.asset_id in ENCODEC_MODELS and not os.path.lexists(Path(root, "assets", spec.install_name))


def encodec_transfer(root, specs):
    """Models requiring the shared converter download and its exact archive size."""
    needed = [spec.asset_id for spec in specs if needs_encodec_model(root, spec)
              and not os.access(spec.convert_argv[0], os.X_OK)]
    return (needed, encodec_download_bytes()) if needed else ([], 0)


def prepare_encodec(installer, specs, ask):
    """Offer one shared binary converter for the selected conversions."""
    pending = [spec for spec in specs if needs_encodec_model(installer.root, spec)]
    if not pending:
        return
    if not ENCODEC_HELPER.is_file():
        if Path('/usr/share/plebian-os/VERSION').is_file() or Path('/etc/plebian-os/build-info.env').is_file():
            raise ValueError('Install plebian-os-tools to enable EnCodec model setup.')
        return  # Standalone Kilix retains its catalog-selected tool.
    needed, size = encodec_transfer(installer.root, pending)
    if needed:
        if size is None:
            raise ValueError('Update plebian-os-tools to enable on-demand EnCodec setup.')
        question = (f'Install the shared EnCodec converter ({size:,} bytes, '
                    f'{size / (1024 ** 2):.1f} MiB) on this computer?')
    elif ENCODEC_REQUESTED.is_file():
        return
    else:
        question = 'Use the installed EnCodec converter and enable its updates? No download is needed.'
    if not ask(question):
        raise ValueError('EnCodec setup deferred. You can retry in Model Setup.')
    command = [str(ENCODEC_HELPER)]
    if os.geteuid() != 0:
        command = ['/usr/bin/sudo', '--', *command]
    subprocess.run(command, check=True)
    if any(not os.access(spec.convert_argv[0], os.X_OK) for spec in pending):
        raise ValueError('The installed EnCodec converter is unavailable.')


OS_INSTALLERS = {
    'kilix-diarization-convert': '/usr/local/sbin/plebian-os-install-diarization-converter',
    'pocket-tts-convert-hf-to-gguf': '/usr/local/sbin/plebian-os-install-ollama-converter',
}


def prepare_converters(api, installer, specs, *, input_fn):
    """Check the prerequisites for the other OS-managed converters."""
    report = lambda message: print(message, flush=True)
    for spec in specs:
        if spec.source_mode != 'upstream-convert':
            continue
        try:
            installer.resolve_converter(spec, report)
        except api.ConverterPrerequisiteError as error:
            helper = OS_INSTALLERS.get(error.tool_asset_id)
            repair = (f' Run sudo {helper} from a terminal to repair this component.'
                      if helper else '')
            raise ValueError(
                f'{spec.asset_id} needs model conversion software. Complete the '
                'Plebian-OS installation or upgrade, then retry Model Setup.'
                + repair + ' Your model choices remain pending; no administrator '
                'password is requested by this wizard.') from error
