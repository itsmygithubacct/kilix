"""Kilix Playalong's default models, and whether this machine declined them.

Plebian-OS installs these on install and upgrade when they are missing, by the
owner's 2026-11-08 decision, unless the machine declines them. Two answers
decline, and either one is enough:

* the model wizard's kilix-playalong page was answered "no";
* ``KILIX_PLAYALONG_MODELS`` in the shared settings file is ``0`` (or any
  value the shared settings treat as off).

No answer on the page and no key in the file mean "install". Whisper large-v3
is never a default: Playalong offers it for lyrics that are not in English,
and it is installed only after a yes on its own licence screen.

This module decides nothing about licences and installs nothing. The caller
does both, through Kilix Content and kilix-license.
"""
from __future__ import annotations

import argparse
import json
import sys

#: Playalong's default set, in install order: the separator, the guitar-note
#: model, and the English Whisper model.
DEFAULT_MODELS = ('demucs-htdemucs-6s', 'basic-pitch-icassp-2022-onnx', 'faster-whisper-medium-en')
#: Offered on the Playalong wizard page, never installed by default.
OPTIONAL_MODELS = ('faster-whisper-large-v3',)
#: The model wizard page whose "no" declines the default set.
WIZARD_PAGE = 'kilix-playalong'
SETTING = 'KILIX_PLAYALONG_MODELS'
#: ``declined`` exit statuses. Distinct, so that no failure reads as "install".
DECLINED, NOT_DECLINED, UNREADABLE = 0, 3, 4


def _wizard_answer():
    import model_wizard
    return model_wizard.answers().get(WIZARD_PAGE, {}).get('answer')


def _setting_value(path=None):
    from kilix_sdk import settings
    text, exists = settings.read_text(path)
    return settings.parse_text(text).get(SETTING) if exists else None


def declined(*, settings_path=None):
    """The reason this machine declined Playalong's default models, or None.

    An unreadable wizard state or settings file raises: the caller cannot tell
    a "no" from a "yes", so it must not install on a guess.
    """
    from kilix_sdk import settings
    if _wizard_answer() == 'no':
        return "the model wizard's Playalong page was answered no"
    value = _setting_value(settings_path)
    if value is not None and not settings.truthy(value):
        return f'{SETTING}={value} in {settings.settings_path() if settings_path is None else settings_path}'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('declined', 'list'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'list':
        report = {'default': list(DEFAULT_MODELS), 'optional': list(OPTIONAL_MODELS)}
        print(json.dumps(report) if args.json else '\n'.join(DEFAULT_MODELS))
        return 0
    try:
        reason = declined()
    except (OSError, ValueError) as error:
        print(f'cannot read whether Playalong models were declined: {error}', file=sys.stderr)
        return UNREADABLE
    if args.json:
        print(json.dumps({'declined': reason is not None, 'reason': reason}))
    else:
        print('Playalong models declined: ' + reason if reason else 'Playalong models are not declined.')
    return DECLINED if reason else NOT_DECLINED


if __name__ == '__main__':
    sys.exit(main())
