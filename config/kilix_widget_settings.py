"""Small, mouse and keyboard accessible context menus for individual widgets."""

from __future__ import annotations

import curses
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from kilix_sdk import settings

ROOT = Path(__file__).resolve().parent.parent
WIDGETS = ('system', 'temperature', 'speech', 'microphone', 'network', 'weather')
TITLES = dict(zip(WIDGETS, ('System CPU and RAM', 'Temperature', 'Read pane aloud',
                          'Microphone / dictation', 'Network / Wi-Fi', 'Weather')))


def clean(value: object, limit: int = 160) -> str:
    return ''.join(c for c in str(value) if c.isprintable())[:limit]


@dataclass(frozen=True)
class Entry:
    label: str
    key: str = ''
    value: str = ''
    action: str = ''


def selected(label: str, key: str, value: str, values: dict) -> Entry:
    return Entry(f'[{"x" if values.get(key) == value else " "}] {label}', key, value)


def toggle(label: str, key: str, values: dict) -> Entry:
    on = settings.truthy(values.get(key, '1'))
    return Entry(f'{label}: {"on" if on else "off"}', key, '0' if on else '1')


def cycle(label: str, key: str, choices: tuple[str, ...], default: str, values: dict) -> Entry:
    current = values.get(key, default)
    if current not in choices:
        current = default
    return Entry(f'{label}: {current}', key, choices[(choices.index(current) + 1) % len(choices)])


def menu(kind: str, values: dict) -> tuple[list[str], list[Entry]]:
    if kind == 'system':
        notes = ['CPU is busy logical cores across the whole system (1.0 = one core).',
                 'RAM is total minus available memory, in GiB. This is not GPU use.',
                 'Linux /proc sampling; unavailable on native Windows.']
        entries = [selected(f'System display: {label}', settings.SYSTEM_LOAD_MODE_KEY, mode, values)
                   for mode, label in (('always', 'always'), ('auto', 'auto (>1 core / at least 1 GiB)'), ('off', 'hidden'))]
        entries += [cycle('Pane CPU display', settings.PANE_CPU_MODE_KEY, settings.PANE_CPU_MODE_CHOICES,
                          settings.PANE_CPU_MODE_DEFAULT, values),
                    cycle('Pane RAM display', settings.PANE_MEMORY_MODE_KEY, settings.PANE_MEMORY_MODE_CHOICES,
                          settings.PANE_MEMORY_MODE_DEFAULT, values), Entry('Open Kilix Memory', action='memory')]
    elif kind == 'temperature':
        notes = ['Hottest available hardware sensor; this is not outdoor weather.']
        entries = [selected('Celsius (°C)', settings.TEMPERATURE_UNIT_KEY, 'celsius', values),
                   selected('Fahrenheit (°F)', settings.TEMPERATURE_UNIT_KEY, 'fahrenheit', values),
                   Entry('Open kilix-temps dashboard', action='temps'),
                   toggle('Show temperature', 'KILIX_CHROME_TEMPERATURE', values)]
    elif kind == 'speech':
        notes = ['Reads terminal text aloud; the microphone button handles dictation.',
                 f'Speech output engine: {settings.tts_engine()} · Voice: {settings.tts_voice()}',
                 f'Output device: {settings.voice_device_out()}']
        entries = [Entry('Speech voice / model selector and installer', action='tts'),
                   cycle('Read extent', settings.VOICE_TTS_EXTENT_KEY, settings.VOICE_TTS_EXTENT_CHOICES,
                         settings.VOICE_TTS_EXTENT_DEFAULT, values),
                   cycle('Speaking rate', settings.VOICE_TTS_RATE_KEY, settings.VOICE_TTS_RATE_CHOICES,
                         settings.VOICE_TTS_RATE_DEFAULT, values),
                   toggle('Show read-aloud button', 'KILIX_CHROME_SPEAK', values)]
    elif kind == 'microphone':
        engine = settings.stt_engine()
        model = {'vibevoice': 'vibevoice-asr-bitnet', 'whistle': 'whistle'}.get(engine, settings.stt_model())
        notes = ['Click to dictate into the active pane; text never presses Enter.',
                 f'Recognition engine: {engine} · Model: {model}',
                 f'Input device: {settings.voice_device_in()}']
        if engine != 'off' and settings.stt_engine_for_model(model) != engine:
            notes.append('Engine and model do not match. Select a compatible model below.')
        entries = [Entry('Microphone input device / level settings', action='microphone'),
                   Entry('Dictation model selector / installer', action='models'),
                   Entry('Dictation behavior settings', action='dictation'),
                   cycle('Dictation time limit (seconds)', settings.VOICE_STT_MAX_SECONDS_KEY,
                         settings.VOICE_STT_MAX_SECONDS_CHOICES, settings.VOICE_STT_MAX_SECONDS_DEFAULT, values),
                   toggle('Show microphone button', 'KILIX_CHROME_DICTATE', values)]
    elif kind == 'network':
        notes = ['Device · type · state · connection (NetworkManager)',
                 'The Wi-Fi icon opens connection information; it is not a signal meter.']
        entries = [Entry('Open nmtui / network manager', action='network'),
                   Entry('Refresh device information', action='probe'),
                   toggle('Show network button', 'KILIX_CHROME_NETWORK', values)]
    elif kind == 'weather':
        notes = ['Enter your US ZIP; no location is detected automatically.',
                 'ZIP is sent to Open-Meteo / GeoNames; coordinates to Open-Meteo.',
                 'Weather: open-meteo.com (CC BY 4.0); free API for non-commercial use.',
                 'Refresh runs while Kilix is open; daily time uses the system time zone.']
        entries = [Entry('ZIP code: ' + (values.get('KILIX_WEATHER_ZIP') or 'not set'),
                         key='KILIX_WEATHER_ZIP', action='edit'),
                   Entry('Refresh now', action='refresh'),
                   *[selected('Refresh: ' + label, 'KILIX_WEATHER_REFRESH', mode, values)
                     for mode, label in (('manual', 'on load and manually'), ('15', 'every 15 minutes'),
                                         ('30', 'every 30 minutes'), ('60', 'every hour'),
                                         ('180', 'every 3 hours'), ('daily', 'daily at the time below'))],
                   Entry('Daily local time: ' + values.get('KILIX_WEATHER_TIME', '07:00'),
                         key='KILIX_WEATHER_TIME', action='edit'),
                   selected('Celsius (°C)', settings.TEMPERATURE_UNIT_KEY, 'celsius', values),
                   selected('Fahrenheit (°F)', settings.TEMPERATURE_UNIT_KEY, 'fahrenheit', values),
                   toggle('Show weather', 'KILIX_CHROME_WEATHER', values)]
    else:
        raise ValueError('Unknown widget')
    return notes, entries


def tool_argv(action: str) -> list[str]:
    kilix = str(ROOT / 'kilix')
    routes = {
        'temps': ['temps'], 'memory': ['memory'], 'tts': ['tts'],
        'models': ['stt', '--section', 'models'],
        'microphone': ['stt', '--section', 'microphone'],
        'dictation': ['stt', '--section', 'dictation'],
    }
    if action in routes:
        return [kilix, *routes[action]]
    if action == 'network':
        if nmtui := shutil.which('nmtui'):
            return [nmtui]
        if manager := shutil.which('nm-connection-editor'):
            return [manager]
        raise FileNotFoundError('Install NetworkManager (nmtui or nm-connection-editor) to manage connections.')
    raise ValueError('Unknown widget tool')


def split_nmcli(line: str) -> list[str]:
    fields, current, escaped = [], '', False
    for char in line:
        if escaped:
            current += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(current)
            current = ''
        else:
            current += char
    fields.append(current)
    return fields


class NetworkInfo:
    """A bounded local probe; opening or dismissing the menu never waits on it."""
    def __init__(self) -> None:
        self.process = None
        self.lines = ['Reading device information…']
        self.deadline = 0.0

    def start(self) -> None:
        self.close()
        try:
            self.process = subprocess.Popen(
                ['nmcli', '-t', '-e', 'yes', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device', 'status'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, 'LC_ALL': 'C'})
            os.set_blocking(self.process.stdout.fileno(), False)
            self.output = bytearray()
            self.deadline = time.monotonic() + 3
            self.lines = ['Reading device information…']
        except OSError:
            self.lines = ['NetworkManager device information unavailable (nmcli not installed).']

    def poll(self) -> None:
        process = self.process
        if process is None:
            return
        while len(self.output) <= 65536:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            self.output.extend(chunk)
        if time.monotonic() >= self.deadline or len(self.output) > 65536:
            self.close()
            self.lines = ['NetworkManager device query timed out. Use Refresh to retry.']
        elif process.poll() is not None:
            rows = [split_nmcli(line) for line in self.output.decode('utf-8', 'replace').splitlines()]
            self.lines = [' · '.join(clean(field, 60) for field in row) for row in rows if len(row) == 4]
            if process.returncode or not self.lines:
                self.lines = ['No device information available; NetworkManager may not be running.']
            self.close()

    def close(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=1)
            self.process.stdout.close()
            self.process = None


def visible_rows(total: int, selected_index: int, height: int, notes: int) -> tuple[int, int, int]:
    # Keep the selected entry and footer visible before spending rows on notes.
    first = min(2 + notes, max(1, height - 3))
    count = max(0, height - first - 2)
    scroll = max(0, min(selected_index - count + 1, total - count)) if count else 0
    return first, count, scroll


def put(screen, row: int, text: str, selected_row: bool = False) -> None:
    height, width = screen.getmaxyx()
    if 0 <= row < height and width > 2:
        try:
            screen.addnstr(row, 1, clean(text, 500), width - 2,
                           curses.A_REVERSE if selected_row else curses.A_NORMAL)
        except curses.error:
            pass


def edit(screen, key: str, initial: str) -> str | None:
    value = initial
    message = 'Five digits; empty clears location' if key == 'KILIX_WEATHER_ZIP' else '24-hour local time: HH:MM'
    while True:
        screen.erase()
        put(screen, 0, 'ZIP code' if key == 'KILIX_WEATHER_ZIP' else 'Daily refresh time')
        put(screen, 2, '> ' + value + '▏')
        put(screen, 4, message)
        put(screen, 6, 'Type to edit · Backspace removes · Enter saves · Esc cancels')
        screen.refresh()
        char = screen.getch()
        if char in (27, 3):
            return None
        if char in (10, 13, curses.KEY_ENTER):
            try:
                return settings.normalize_weather(key, value)
            except ValueError as error:
                message = str(error)
        elif char in (curses.KEY_BACKSPACE, 127, 8):
            value = value[:-1]
        elif char == 21:  # Ctrl+U
            value = ''
        elif 32 <= char <= 126 and len(value) < 32:
            value += chr(char)


def _run(screen, kind: str) -> int | list[str]:
    with_cursor = False
    try:
        curses.curs_set(0)
        with_cursor = True
    except curses.error:
        pass
    curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED)
    screen.keypad(True)
    screen.timeout(250)
    probe = NetworkInfo() if kind == 'network' else None
    if probe:
        probe.start()
    index, message = 0, ''
    try:
        while True:
            values = settings.load()
            notes, entries = menu(kind, values)
            if probe:
                probe.poll()
                notes += probe.lines
            screen.erase()
            height, width = screen.getmaxyx()
            first, count, scroll = visible_rows(len(entries), index, height, len(notes))
            put(screen, 0, TITLES[kind])
            for row, note in enumerate(notes[:max(0, first - 2)], 1):
                put(screen, row, note)
            targets = {}
            if height >= 4 and width >= 4:
                for row, item_index in enumerate(range(scroll, min(len(entries), scroll + count)), first):
                    put(screen, row, ('▶ ' if index == item_index else '  ') + entries[item_index].label,
                        index == item_index)
                    targets[row] = item_index
                put(screen, height - 2, message or 'Changes save immediately. ↑/↓ scrolls all options.')
                put(screen, height - 1, 'Click or ↑/↓ + Enter · Esc closes')
            screen.refresh()
            char = screen.getch()
            activate = False
            if char in (27, ord('q'), 3):
                return 0
            if char in (curses.KEY_UP, ord('k')):
                index = (index - 1) % len(entries)
            elif char in (curses.KEY_DOWN, ord('j'), 9):
                index = (index + 1) % len(entries)
            elif char == curses.KEY_HOME:
                index = 0
            elif char == curses.KEY_END:
                index = len(entries) - 1
            elif char == curses.KEY_PPAGE:
                index = max(0, index - max(1, count))
            elif char == curses.KEY_NPAGE:
                index = min(len(entries) - 1, index + max(1, count))
            elif char in (10, 13, curses.KEY_ENTER, ord(' ')):
                activate = bool(targets)
            elif char == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    if 1 <= x < width - 1 and y in targets and state & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                        index, activate = targets[y], True
                except curses.error:
                    pass
            if not activate:
                continue
            entry = entries[index]
            try:
                if entry.action == 'edit':
                    result = edit(screen, entry.key, values.get(entry.key, ''))
                    if result is not None:
                        settings.update({entry.key: result})
                elif entry.action == 'refresh':
                    if not values.get('KILIX_WEATHER_ZIP'):
                        raise ValueError('Enter a ZIP code first.')
                    settings.update({'KILIX_WEATHER_REQUEST': str(time.time_ns())})
                elif entry.action == 'probe':
                    if probe:
                        probe.start()
                elif entry.action:
                    return tool_argv(entry.action)
                else:
                    settings.update({entry.key: entry.value})
                message = 'Saved.'
            except (OSError, ValueError, KeyError) as error:
                message = clean(error)
    finally:
        if probe:
            probe.close()
        if with_cursor:
            curses.curs_set(1)


def run(kind: str) -> int:
    result = curses.wrapper(_run, kind)
    if isinstance(result, list):
        # Restore the terminal before handing it to the established tool. Its
        # own model/licence flow is retained and no shell parses these arguments.
        try:
            os.execv(result[0], result)
        except OSError as error:
            print(f'Could not open widget tool: {error}', file=sys.stderr)
            return 1
    return int(result)
