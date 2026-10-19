"""Compile both patched Amp callers against the installed SDL headers/library."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
import catalog_amp


FIXTURES = r'''
#include <SDL.h>
#include <assert.h>
#if SDL_VERSION_ATLEAST(2, 24, 0)
static int result, calls;
static const SDL_Point *received;
static int fixture_point(const SDL_Point *point) {
    ++calls; received = point; return result;
}
#define SDL_GetPointDisplayIndex fixture_point
#else
static int count = 3, failed = -1, queries;
static SDL_Rect displays[] = {{-1920, 0, 1920, 1080}, {0, 0, 1920, 1080},
                              {0, -900, 1600, 900}};
static int fixture_count(void) { return count; }
static int fixture_bounds(int index, SDL_Rect *bounds) {
    ++queries;
    if (index == failed || failed == -2) return -1;
    *bounds = displays[index]; return 0;
}
#define SDL_GetNumVideoDisplays fixture_count
#define SDL_GetDisplayBounds fixture_bounds
#endif
#include "CALLER"
int main(void) {
    SDL_Point point = {0, 0};
#if SDL_VERSION_ATLEAST(2, 24, 0)
    result = 73;
    assert(ka_point_display_index(&point) == 73 && received == &point && calls == 1);
    result = -1;
    assert(ka_point_display_index(&point) == -1 && calls == 2);
    assert(ka_point_display_index(NULL) == -1 && received == NULL && calls == 3);
#else
    const int cases[][3] = {{-1920, 0, 0}, {-1, 1079, 0}, {0, 0, 1},
        {1919, 1079, 1}, {0, -900, 2}, {1599, -1, 2}, {1600, -1, 1},
        {1920, 0, 1}, {0, 1080, 1}, {-1921, 0, 0}};
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        point = (SDL_Point){cases[i][0], cases[i][1]};
        assert(ka_point_display_index(&point) == cases[i][2]);
    }
    failed = 0;
    point = (SDL_Point){-1, 0};
    assert(ka_point_display_index(&point) == 1);
    point.x = 0;
    assert(ka_point_display_index(&point) == 1); /* Skip failed bounds. */
    failed = -1;
    displays[2] = displays[1];
    assert(ka_point_display_index(&point) == 1); /* First containing display. */
    count = 2;
    displays[0] = (SDL_Rect){-100, 0, 100, 100};
    displays[1] = (SDL_Rect){101, 0, 1000, 100};
    point = (SDL_Point){50, 50};
    assert(ka_point_display_index(&point) == 0); /* Equal-distance gap: first. */
    point.x = 51;
    assert(ka_point_display_index(&point) == 1);
    point = (SDL_Point){150, -10};
    assert(ka_point_display_index(&point) == 1); /* Nearest bounds, not center. */
    failed = -2;
    assert(ka_point_display_index(&point) == -1); /* No usable bounds. */
    queries = 0; count = 0;
    assert(ka_point_display_index(&point) == -1 && queries == 0);
    count = -1;
    assert(ka_point_display_index(&point) == -1 && queries == 0);
    assert(ka_point_display_index(NULL) == -1);
#endif
    return 0;
}
'''

LIVE = r'''
#include <SDL.h>
#include <assert.h>
#include "CALLER"
int main(void) {
    assert(SDL_Init(SDL_INIT_VIDEO) == 0);
    SDL_Rect bounds;
    assert(SDL_GetNumVideoDisplays() == 1);
    assert(SDL_GetDisplayBounds(0, &bounds) == 0);
    SDL_Point point = {bounds.x + bounds.w / 2, bounds.y + bounds.h / 2};
    assert(ka_point_display_index(&point) == 0);
    SDL_Quit();
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    options = parser.parse_args()
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'sdl2'], text=True))
    with tempfile.TemporaryDirectory(prefix='amp-sdl-check-') as temporary:
        source = Path(temporary)
        shutil.copytree(options.source / 'src', source / 'src')
        snapshots = {name: (source / name).read_bytes() for name in catalog_amp.PATCHES}
        (source / 'src/menu.c').write_bytes(snapshots['src/menu.c'] + b'\n/* changed */\n')
        try:
            catalog_amp.patch(source)
        except ValueError:
            pass
        else:
            raise AssertionError('Unexpected upstream source must be rejected')
        (source / 'src/menu.c').write_bytes(snapshots['src/menu.c'])
        assert snapshots == {name: (source / name).read_bytes() for name in snapshots}
        catalog_amp.patch(source)
        snapshots = {name: (source / name).read_bytes() for name in snapshots}
        catalog_amp.patch(source)
        assert snapshots == {name: (source / name).read_bytes() for name in snapshots}
        for caller in ('dialog.c', 'menu.c'):
            for name, program in [('fixtures', FIXTURES), ('live', LIVE)]:
                probe = source / (caller + '-' + name + '.c')
                probe.write_text(program.replace('CALLER', caller))
                binary = probe.with_suffix('')
                subprocess.run(['cc', '-O2', '-std=c11', '-D_GNU_SOURCE',
                                '-Werror=implicit-function-declaration', '-ffunction-sections', '-fdata-sections',
                                '-I' + str(source / 'src'), str(probe), '-Wl,--gc-sections',
                                *flags, '-o', str(binary)], check=True, timeout=30)
                subprocess.run([str(binary)], check=True, timeout=10,
                               env=dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy'))
                print(caller + ' ' + name + ': PASS', flush=True)


if __name__ == '__main__':
    main()
