"""Desktop clocks use AM/PM at midnight, noon, and on first paint."""
import time
from unittest.mock import patch

import harness as H
import taskbar as TB
import theme as T
from PIL import ImageDraw


time_strftime = time.strftime

desk = H.make_desk()
bar = desk.taskbar
for hour, expected in [(0, "12:05 AM"), (11, "11:05 AM"),
                       (12, "12:05 PM"), (23, "11:05 PM")]:
    now = time.struct_time((2026, 9, 28, hour, 5, 6, 0, 271, -1))
    original = ImageDraw.ImageDraw.text
    drawn = []

    def record(draw, xy, text, *args, **kwargs):
        drawn.append((xy, text))
        return original(draw, xy, text, *args, **kwargs)

    with patch.object(TB.time, "localtime", return_value=now), \
            patch.object(TB.time, "strftime", side_effect=lambda fmt, *args:
                         time_strftime(fmt, now)), \
            patch.object(ImageDraw.ImageDraw, "text", record):
        bar._minute = ""
        desk.render()
        assert any(text == expected for xy, text in drawn), drawn
        desk.dirty = False
        bar.tick(0)
        assert bar._minute == expected and desk.dirty
        desk.dirty = False
        bar.tick(1)
        assert not desk.dirty, "same minute needlessly repaints"
        x0, y0, x1, y1 = bar._clock_rect()
        assert T.text_w(T.FONT, expected) + 14 <= x1 - x0
        popup = TB._ClockPopup(desk, bar, 0, 0)
        popup.draw_client(ImageDraw.Draw(desk.fb), desk.fb)
        seconds = expected.replace(":05", ":05:06")
        assert any(text == seconds for xy, text in drawn), drawn
        popup.close()

print("ok")
