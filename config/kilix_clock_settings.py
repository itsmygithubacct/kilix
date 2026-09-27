"""Clock-only settings panel, persisted through the shared settings writer."""

import curses
from datetime import datetime

from kilix_sdk import settings


def clock_options(values):
    fmt = values.get(settings.CLOCK_FORMAT_KEY, settings.CLOCK_FORMAT_DEFAULT)
    return '%I' in fmt, '%Y' in fmt or '%x' in fmt, '%S' in fmt, settings.truthy(values.get('KILIX_CHROME_CALENDAR', '1'))


def clock_change(values, index):
    twelve, date, seconds, calendar = clock_options(values)
    if index == 3:
        return {'KILIX_CHROME_CALENDAR': not calendar}
    if index == 0:
        twelve = not twelve
    elif index == 1:
        date = not date
    elif index == 2:
        seconds = not seconds
    else:
        raise ValueError('unknown clock option')
    fmt = ('%Y-%m-%d ' if date else '') + ('%I:%M' if twelve else '%H:%M')
    if seconds:
        fmt += ':%S'
    if twelve:
        fmt += ' %p'
    return {settings.CLOCK_FORMAT_KEY: fmt}


def run(stdscr):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED)
    stdscr.keypad(True)
    stdscr.timeout(1000)
    selected, error = 0, ''
    while True:
        values = settings.load()
        twelve, date, seconds, calendar = clock_options(values)
        now = datetime.now().astimezone()
        fmt = values[settings.CLOCK_FORMAT_KEY]
        try:
            preview = now.strftime(fmt)
        except (ValueError, OverflowError):
            preview = 'Invalid clock format'
        labels = (
            'Time format: ' + ('12 hour (AM/PM)' if twelve else '24 hour'),
            'Show date: ' + ('on' if date else 'off'),
            'Show seconds: ' + ('on' if seconds else 'off'),
            'Calendar icon: ' + ('on' if calendar else 'off'),
        )
        lines = ['Clock Settings', preview, '', *[f'{i + 1}. {label}' for i, label in enumerate(labels)], '',
                 'Time zone: ' + (now.tzname() or 'local'),
                 error or 'Changes save immediately.', 'Click or ↑/↓ + Enter · Esc close']
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        for row, line in enumerate(lines[:height]):
            try:
                stdscr.addnstr(row, 1, line, max(0, width - 2),
                              curses.A_REVERSE if row == selected + 3 else curses.A_NORMAL)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        activate = False
        if key in (27, ord('q'), 3):
            return 0
        if key == curses.KEY_UP:
            selected = (selected - 1) % 4
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % 4
        elif key in (10, 13, curses.KEY_ENTER, ord(' ')):
            activate = True
        elif ord('1') <= key <= ord('4'):
            selected, activate = key - ord('1'), True
        elif key == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
                if 1 <= x < width - 1 and 3 <= y <= 6 and state & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                    selected, activate = y - 3, True
            except curses.error:
                pass
        if activate:
            try:
                settings.update(clock_change(settings.load(), selected))
                error = ''
            except (OSError, ValueError) as exc:
                error = 'Could not save: ' + str(exc)
