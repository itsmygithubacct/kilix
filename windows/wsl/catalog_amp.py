"""Reviewed libsndfile and SDL compatibility deltas for Amp's pinned source."""
import hashlib
import json


BEFORE = '7cebc63a9b962d0bf10ed8d267e57cf639689e9ca5d3df360ff4614f5b9f04f4'
CHANGES = (
    ('#include <sndfile.h>', '#include <sndfile.h>\n#include <stdio.h>'),
    ('        fmt = SF_FORMAT_MPEG | SF_FORMAT_MPEG_LAYER_III;',
     '#if KILIX_HAVE_SNDFILE_MPEG\n'
     '        fmt = SF_FORMAT_MPEG | SF_FORMAT_MPEG_LAYER_III;\n'
     '#else\n'
     '        fmt = 0;\n'
     '#endif'),
    ('    info.format = format_for_ext(filepath);',
     '    info.format = format_for_ext(filepath);\n'
     '    if (!info.format || !sf_format_check(&info)) {\n'
     '        fputs("kilix-amp: this libsndfile cannot export the selected format; "\n'
     '              "MP3 requires libsndfile 1.1+ with MPEG support.\\n", stderr);\n'
     '        return false;\n'
     '    }'),
)
SDL_HELPER = '''
/* SDL 2.24 added the point lookup. Keep older SDL builds local to this file. */
static int ka_point_display_index(const SDL_Point *point)
{
#if SDL_VERSION_ATLEAST(2, 24, 0)
    return SDL_GetPointDisplayIndex(point);
#else
    if (!point)
        return -1;
    int count = SDL_GetNumVideoDisplays();
    int closest = -1;
    double closest_distance = 0;
    for (int i = 0; i < count; i++) {
        SDL_Rect bounds;
        if (SDL_GetDisplayBounds(i, &bounds) != 0 || bounds.w <= 0 || bounds.h <= 0)
            continue;
        if (SDL_PointInRect(point, &bounds))
            return i;
        /* Like SDL 2.24, choose the nearest bounds for off-screen points.
         * Use floating arithmetic to avoid overflowing squared distances. */
        double right = (double)bounds.x + bounds.w - 1;
        double bottom = (double)bounds.y + bounds.h - 1;
        double dx = point->x < bounds.x ? (double)bounds.x - point->x :
                    point->x > right ? point->x - right : 0;
        double dy = point->y < bounds.y ? (double)bounds.y - point->y :
                    point->y > bottom ? point->y - bottom : 0;
        double distance = dx * dx + dy * dy;
        if (closest < 0 || distance < closest_distance) {
            closest = i;
            closest_distance = distance;
        }
    }
    /* With no valid bounds, both callers skip clamping their coordinates. */
    return closest;
#endif
}
'''
SDL_CHANGES = (
    ('#include <SDL.h>', '#include <SDL.h>\n' + SDL_HELPER),
    ('SDL_GetPointDisplayIndex(&(SDL_Point)', 'ka_point_display_index(&(SDL_Point)'),
)
PATCHES = {
    'src/audio_data.c': (BEFORE, CHANGES),
    'src/dialog.c': ('8a53e1f933d83b0c02787796dfa001049e6db7e13f17cb01a6e6609acc774f0d', SDL_CHANGES),
    'src/menu.c': ('4b072b38027330417b8c05a499568e928a605143e5c0a1101e7b2f265111cc82', SDL_CHANGES),
}
REVISION = hashlib.sha256(json.dumps(PATCHES, sort_keys=True).encode()).hexdigest()


def patch(source):
    patched = {}
    for relative, (checksum, changes) in PATCHES.items():
        path = source / relative
        original = path.read_text()
        for before, after in changes:
            original = original.replace(after, before)
        if hashlib.sha256(original.encode()).hexdigest() != checksum:
            raise ValueError('Amp ' + relative + ' differs from the reviewed compatibility patch')
        for before, after in changes:
            if original.count(before) != 1:
                raise ValueError('Amp compatibility patch context is ambiguous')
            original = original.replace(before, after)
        patched[path] = original
    for path, data in patched.items():
        path.write_text(data)
