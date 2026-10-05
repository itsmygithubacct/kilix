"""Authoritative pane-to-screen geometry from the native frontend."""
from dataclasses import dataclass
import json
import math
import os


class GeometryUnavailable(ValueError):
    pass


def _integers(value, size, positive=False):
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(type(n) is not int or abs(n) > 10000000 for n in value)
            or positive and any(n <= 0 for n in value)):
        raise GeometryUnavailable('Invalid native pane geometry')
    return tuple(value)


@dataclass(frozen=True)
class PaneGeometry:
    pane_id: int
    render_rect: tuple[int, int, int, int]
    grid: tuple[int, int]
    cell_size: tuple[int, int]
    framebuffer_size: tuple[int, int]
    window_size: tuple[int, int]
    screen_origin: tuple[int, int] | None
    visible: bool
    focused: bool

    @classmethod
    def parse(cls, value, expected_pane=None):
        if not isinstance(value, dict) or value.get('version') != 1:
            raise GeometryUnavailable('Unsupported native pane geometry')
        pane = value.get('pane_id')
        if type(pane) is not int or pane <= 0 or expected_pane is not None and pane != expected_pane:
            raise GeometryUnavailable('Native geometry belongs to another pane')
        flags = ('screen_coordinates_supported', 'visible', 'focused')
        if any(type(value.get(name)) is not bool for name in flags):
            raise GeometryUnavailable('Invalid native geometry state')
        rect = _integers(value.get('render_rect'), 4)
        grid = _integers(value.get('grid'), 2, True)
        cell = _integers(value.get('cell_size'), 2, True)
        framebuffer = _integers(value.get('framebuffer_size'), 2, True)
        window = _integers(value.get('window_size'), 2, True)
        left, top, right, bottom = rect
        if (not 0 <= left < right <= framebuffer[0]
                or not 0 <= top < bottom <= framebuffer[1]
                or grid[0] * cell[0] > right - left
                or grid[1] * cell[1] > bottom - top):
            raise GeometryUnavailable('Pane cells are outside the native render area')
        origin = (_integers(value.get('screen_origin'), 2)
                  if value['screen_coordinates_supported'] else None)
        return cls(pane, rect, grid, cell, framebuffer, window, origin,
                   value['visible'], value['focused'])

    def _transform(self, canvas, grid):
        canvas = _integers(canvas, 2, True)
        grid = _integers(grid, 2, True)
        if self.screen_origin is None or not self.visible or grid != self.grid:
            raise GeometryUnavailable('The live pane has no matching screen placement')
        sx = self.window_size[0] / self.framebuffer_size[0]
        sy = self.window_size[1] / self.framebuffer_size[1]
        return (self.screen_origin[0] + self.render_rect[0] * sx,
                self.screen_origin[1] + self.render_rect[1] * sy,
                self.grid[0] * self.cell_size[0] * sx / canvas[0],
                self.grid[1] * self.cell_size[1] * sy / canvas[1])

    def screen_rect(self, rect, canvas, grid):
        x, y, width, height = _integers(rect, 4)
        if width < 0 or height < 0:
            raise GeometryUnavailable('Invalid canvas rectangle')
        ox, oy, sx, sy = self._transform(canvas, grid)
        left, top = math.floor(ox + x * sx), math.floor(oy + y * sy)
        right = math.ceil(ox + (x + width) * sx) if width else left
        bottom = math.ceil(oy + (y + height) * sy) if height else top
        return left, top, right - left, bottom - top

    def canvas_point(self, x, y, canvas, grid):
        x, y = _integers((x, y), 2)
        ox, oy, sx, sy = self._transform(canvas, grid)
        return (x - ox) / sx, (y - oy) / sy


def current(*, timeout=.4):
    """Query this exact live pane through the SDK's authenticated route.

    Refresh broker reattachment before reading the pane ID. Missing context,
    older native frontends and ambiguous/malformed replies never select the
    active pane as a substitute.
    """
    from . import frontend_context, panes
    frontend_context.refresh()
    value = os.environ.get('KITTY_WINDOW_ID', '')
    if not value.isdecimal() or int(value) <= 0:
        raise GeometryUnavailable('No live pane context')
    pane = int(value)
    raw = panes._check(['get-pane-geometry', '--match', 'id:' + str(pane)],
                       'read pane geometry', timeout=timeout)
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        raise GeometryUnavailable('Invalid native geometry response') from None
    return result, PaneGeometry.parse(result, pane)
