#!/usr/bin/env python3
"""The one question `kilix ask` asks about the optional LFM2 command model.

Ask reads requests with its grammar until a command model is installed. The
launcher runs this once, at a terminal, when nothing has been chosen and the
model is absent. Yes runs the ordinary first-use install, which shows the
model's terms and asks its own question; this file answers neither and writes
no receipt. No saves the grammar as the Ask selection, so the question is not
repeated. No answer (end of input) saves nothing and the question returns.

A "no" on the model wizard's Kilix Ask page is the same answer: it is saved
here as the grammar selection and nothing is asked. An answer given here is
recorded on that page when the page is unanswered, so the wizard does not ask
again either.
"""
import os
from pathlib import Path
import subprocess
import sys

ASSET = 'lfm2-kilix-commands'
HERE = Path(__file__).resolve().parent
LATER = ('To install it later: kilix-needle workflows setup --model lfm2, '
         'or kilix wizard --all.')


def size_text(number):
    return f'{number / 1e9:.2f} GB' if number >= 1e9 else f'{number / 1e6:.0f} MB'


def workflows(needle, *args):
    """Run Needle's own `workflows` command; it owns the Ask selection."""
    try:
        return subprocess.run([needle, 'workflows', *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None


def save(needle, backend):
    result = workflows(needle, 'select', '--model', backend)
    return result is not None and result.returncode == 0


def facts():
    """Catalog sizes and any earlier delivery on this disk; None when unreadable."""
    try:
        import content_models
        return content_models.first_use_facts(ASSET)
    except Exception:       # noqa: BLE001 - the question is still worth asking
        return None


def wizard_answer():
    try:
        import model_wizard
        return model_wizard.answers().get('workflows', {}).get('answer')
    except Exception:       # noqa: BLE001 - unreadable wizard state is no answer
        return None


def wizard_record(answer):
    """Best effort: an unanswered Kilix Ask page takes the answer given here."""
    try:
        import model_wizard
        with model_wizard.exclusive():
            if model_wizard.answers().get('workflows', {}).get('answer') in ('yes', 'no'):
                return
            if answer == 'no':
                model_wizard.legacy_state('workflows', model_wizard.declined_state('workflows'))
                model_wizard.record('workflows', 'no', [])
            else:
                model_wizard.legacy_state('workflows', {'answer': 'yes', 'scope': 'ask'})
                model_wizard.record('workflows', 'yes', [ASSET])
    except Exception:       # noqa: BLE001 - the Ask selection already holds the answer
        pass


def question(known):
    lines = ['Kilix Ask is reading requests with its built-in grammar.',
             'The optional LFM2 command model proposes a reading when the grammar has none.',
             'It runs on this computer; no request leaves it.']
    if known and known.get('reuse'):
        lines.append('An earlier delivery of the model is on this computer, so nothing is '
                     f'downloaded. It uses {size_text(known["installed_bytes"])} of disk.')
    elif known:
        lines.append(f'It is a {size_text(known["download_bytes"])} download and uses '
                     f'{size_text(known["installed_bytes"])} of disk.')
    lines.append('Its licence terms are shown before anything is installed.')
    return '\n'.join(lines) + '\n'


def offer(needle, *, source=None, output=None, install=None):
    """Ask once. Returns 'installed', 'declined', 'failed' or 'unanswered'."""
    source = sys.stdin if source is None else source
    output = sys.stdout if output is None else output
    if wizard_answer() == 'no':
        save(needle, 'grammar')
        return 'declined'
    output.write(question(facts()))
    output.write('Install the LFM2 command model now? [y/N] ')
    output.flush()
    answer = source.readline(64)
    if not answer:
        output.write('\n')
        return 'unanswered'
    if answer.strip().casefold() not in ('y', 'yes'):
        saved = save(needle, 'grammar')
        wizard_record('no')
        output.write('Ask stays on its grammar' + (' and will not ask again. ' if saved else '. ')
                     + LATER + '\n')
        output.flush()
        return 'declined'
    if install is None:
        install = lambda: subprocess.run(   # noqa: E731
            [sys.executable, '-B', str(HERE / 'content_models.py'), 'ensure', ASSET]).returncode
    if install() != 0:
        output.write('The LFM2 command model was not installed. Ask is using its grammar '
                     'and will offer the model again.\n')
        output.flush()
        return 'failed'
    if not save(needle, 'lfm2'):
        output.write('The model is installed; Ask uses it while no other choice is saved.\n')
    wizard_record('yes')
    output.flush()
    return 'installed'


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: kilix_ask_offer.py NEEDLE-COMMAND', file=sys.stderr)
        return 2
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return 0
        offer(argv[0])
    except KeyboardInterrupt:
        print(file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
