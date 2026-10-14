"""Explicit immutable managed Voice + canonical lease generation closure."""
from pathlib import Path
import re

def arguments(parser):
    for component in ('voice', 'lease'):
        parser.add_argument('--' + component + '-source', type=Path)
        parser.add_argument('--' + component + '-ref', help='explicit reviewed full source commit')

def selected(args, staging):
    fields = [getattr(args, name, None) for name in ('voice_source','voice_ref','lease_source','lease_ref')]
    if not any(value is not None for value in fields):
        return None
    if not all(value is not None for value in fields):
        raise ValueError('complete explicit Voice and canonical lease source/ref closure required')
    voice, voice_ref, lease, lease_ref = fields
    for source, revision in ((voice,voice_ref),(lease,lease_ref)):
        if not source.is_absolute() or not re.fullmatch('[0-9a-f]{40}',revision):
            raise ValueError('absolute closure source and full commit required')
        staging.exact_source(source, revision)
    return {'voice':{'source':str(voice),'commit':voice_ref},
            'lease':{'source':str(lease),'commit':lease_ref}}

def stage(selection, generation, staging, environment, python):
    if selection is None:
        return None
    shared = generation / 'shared'
    shared.mkdir(mode=0o700)
    staging.stage_package(Path(selection['voice']['source']), selection['voice']['commit'],
        'voicelib', generation / 'lib/voicelib', environment)
    version = staging.owned_run(None,['git','-C',selection['voice']['source'],'show',
        selection['voice']['commit']+':VERSION'],environment,timeout=10,capture=True)
    (generation/'lib/VERSION').write_bytes(version)
    (generation/'lib/VERSION').chmod(0o600)
    staging.stage_package(Path(selection['lease']['source']), selection['lease']['commit'],
        'components/kilix-device-lease/src/kilix_device_lease',shared / 'kilix_device_lease',environment)
    # -I -S: only the actual immutable staged populations, no ambient packages.
    code = ('import sys;sys.path[:0]=sys.argv[1:3];'
        'from voicelib import audio,microphone,device_leases;import kilix_device_lease as canonical;'
        'from pathlib import Path;'
        'assert device_leases.acquire is canonical.acquire;'
        'assert canonical.VERSION=="kilix.device-lease/v1";'
        'assert not Path(canonical.__file__).resolve().is_relative_to(Path(audio.__file__).resolve().parent.parent);'
        'assert callable(microphone.CaptureControl) and callable(microphone.CaptureOwner);'
        'assert callable(audio.MicCapture);print("shared staged imports verified")')
    staging.owned_run(None,[python,'-I','-S','-B','-c',code,generation/'lib',shared],environment,timeout=30)
    for row in selection.values():
        staging.exact_source(Path(row['source']),row['commit'])
    return {'sources':{key:row['commit'] for key,row in selection.items()},
            'files':staging.package_files(shared)}
