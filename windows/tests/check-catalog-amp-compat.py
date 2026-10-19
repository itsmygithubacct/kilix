"""Compile the pinned Amp export code and verify unavailable MP3 is not WAV."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
import catalog_amp


PROBE = r'''
#include "audio_data.h"
#include <sndfile.h>
#include <unistd.h>
int main(void) {
    AudioData data, loaded;
    audio_data_init(&data);
    audio_data_init(&loaded);
    data.samples = abuf_alloc(1000, 1);
    data.loaded = true;
    for (int i = 0; i < 1000; ++i) data.samples.data[i] = 0.25f;
    if (!ad_export(&data, "probe.wav") || !ad_load(&loaded, "probe.wav") ||
        loaded.samples.frames != 1000) return 10;
    bool expected = false;
#if KILIX_HAVE_SNDFILE_MPEG
    SF_INFO info = {0};
    info.samplerate = data.sr;
    info.channels = 1;
    info.format = SF_FORMAT_MPEG | SF_FORMAT_MPEG_LAYER_III;
    expected = sf_format_check(&info) != 0;
#endif
    if (ad_export(&data, "probe.mp3") != expected) return 11;
    if (!expected && access("probe.mp3", F_OK) == 0) return 12;
#if KILIX_HAVE_SNDFILE_MPEG
    if (expected) {
        SF_INFO encoded = {0};
        SNDFILE *file = sf_open("probe.mp3", SFM_READ, &encoded);
        if (!file || (encoded.format & SF_FORMAT_TYPEMASK) != SF_FORMAT_MPEG)
            return 13;
        sf_close(file);
    }
#endif
    audio_data_destroy(&loaded);
    audio_data_destroy(&data);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='amp-sndfile-check-') as temporary:
        source = Path(temporary)
        shutil.copytree(options.source / 'src', source / 'src')
        catalog_amp.patch(source)
        first = (source / 'src/audio_data.c').read_bytes()
        catalog_amp.patch(source)
        assert first == (source / 'src/audio_data.c').read_bytes()
        probe = source / 'probe.c'
        probe.write_text(PROBE)
        modes = [0]
        if subprocess.call(['pkg-config', '--atleast-version=1.1.0', 'sndfile']) == 0:
            modes.append(1)
        for mode in modes:
            work = source / str(mode)
            work.mkdir()
            subprocess.run(['cc', '-O2', '-D_GNU_SOURCE', '-DKILIX_HAVE_SNDFILE_MPEG=' + str(mode),
                            '-I' + str(source / 'src'), str(probe),
                            *(str(source / 'src' / name) for name in ('audio_data.c', 'common.c', 'dsp.c')),
                            '-lsndfile', '-lm', '-o', str(work / 'probe')], check=True, timeout=30)
            result = subprocess.run([str(work / 'probe')], cwd=work, check=True,
                                    capture_output=True, text=True, timeout=30)
            if not mode:
                assert 'MP3 requires libsndfile 1.1+' in result.stderr
            print('MP3 compile capability ' + str(mode) + ': WAV roundtrip and MP3 format/refusal PASS')


if __name__ == '__main__':
    main()
