"""Check OS-installed conversion software before acquiring selected models."""

OS_INSTALLERS = {
    'kilix-diarization-convert': '/usr/local/sbin/plebian-os-install-diarization-converter',
    'pocket-tts-convert-hf-to-gguf': '/usr/local/sbin/plebian-os-install-ollama-converter',
}


def prepare_converters(api, installer, specs, *, input_fn):
    """Never elevate or install software from the model wizard.

    Plebian-OS installs the pinned conversion closures during provisioning and
    upgrades. Missing software leaves choices pending, with no weight fetch.
    """
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
