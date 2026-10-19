"""Reviewed compatibility delta for Amp's pinned libsndfile export code."""
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
REVISION = hashlib.sha256((BEFORE + json.dumps(CHANGES)).encode()).hexdigest()


def patch(source):
    path = source / 'src/audio_data.c'
    original = path.read_text()
    for before, after in CHANGES:
        original = original.replace(after, before)
    if hashlib.sha256(original.encode()).hexdigest() != BEFORE:
        raise ValueError('Amp audio export source differs from the reviewed compatibility patch')
    for before, after in CHANGES:
        if original.count(before) != 1:
            raise ValueError('Amp compatibility patch context is ambiguous')
        original = original.replace(before, after)
    path.write_text(original)
