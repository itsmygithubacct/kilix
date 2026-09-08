"""Bind a multiplexer build to its source and installed shared codec package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys


def command(argv):
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
                       GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
    return subprocess.check_output(argv, env=environment, stderr=subprocess.PIPE,
                                   timeout=15, text=True).strip()


def data(path, maximum=32*1024**2):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o022 or not 0 <= info.st_size <= maximum):
            raise ValueError('shared native package contains an unsafe file')
        result = bytearray()
        while len(result) < info.st_size:
            block = os.read(descriptor, min(1024**2, info.st_size-len(result)))
            if not block:
                raise ValueError('shared native package file ended early')
            result.extend(block)
        after = os.fstat(descriptor)
        if os.read(descriptor, 1) or any(getattr(after,k) != getattr(info,k) for k in
                                      ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode')):
            raise ValueError('shared native package changed during inspection')
        return bytes(result)
    finally:
        os.close(descriptor)


def package_plan(prefix, cflags, libs):
    prefix = Path(prefix)
    if not prefix.is_absolute() or prefix != prefix.resolve(strict=True):
        raise ValueError('shared native prefix must be canonical')
    record_bytes = data(prefix/'share/doc/kilix-encodec/native-package.json', 2*1024**2)
    record = json.loads(record_bytes)
    if (record['schema'] != 'kilix.encodec.native-package/v1'
            or any(record['build'].get(k) != v for k,v in {'ONNX':1,'CONTENT':1,'PREFIX':'/usr'}.items())
            or not all(re.fullmatch('[0-9a-f]{40}', record[k]) for k in ('source_commit','content_commit'))):
        raise ValueError('shared native package has no enabled source binding')
    members = record['files']
    if not isinstance(members,dict) or not 10 <= len(members) <= 64:
        raise ValueError('shared native package population differs')
    required = {'usr/lib/libkilix-encodec.so.0','usr/lib/pkgconfig/kilix-encodec.pc',
                'usr/include/kilix_encodec.h','usr/include/kilix_encodec_content.h',
                'usr/share/doc/kilix-encodec/content_bundle.receipt.json'}
    if not required <= set(members):
        raise ValueError('shared native package is incomplete')
    for name, entry in members.items():
        relative = Path(name)
        if (relative.as_posix() != name or relative.parts[:1] != ('usr',)
                or '..' in relative.parts or len(relative.parts)<2):
            raise ValueError('unsafe native package member name')
        path = prefix.joinpath(*relative.parts[1:])
        if path.parent != path.parent.resolve(strict=True):
            raise ValueError('native package member parent is a link')
        if 'link' in entry:
            if name != 'usr/lib/libkilix-encodec.so' or entry != {'link':'libkilix-encodec.so.0'} or os.readlink(path) != entry['link']:
                raise ValueError('native linker name differs')
        else:
            value = data(path)
            if len(value) != entry['bytes'] or hashlib.sha256(value).hexdigest() != entry['sha256']:
                raise ValueError('shared native package member differs')
    embedded = json.loads(data(prefix/'share/doc/kilix-encodec/content_bundle.receipt.json',2*1024**2))
    if embedded['content_commit'] != record['content_commit'] or embedded['bundle_sha256'] != record['content_bundle_sha256']:
        raise ValueError('embedded Content receipt differs from the native package')
    if any('\n' in value or '\r' in value for value in (cflags,libs)):
        raise ValueError('invalid native package compiler flags')
    return {'package_sha256':hashlib.sha256(record_bytes).hexdigest(),
            'source_commit':record['source_commit'], 'content_commit':record['content_commit'],
            'cflags':cflags,'libs':libs}


def plan(source, expected):
    if not re.fullmatch('[0-9a-f]{40}', expected):
        raise ValueError('set an exact KILIX_MULTIPLEXER_COMMIT with an explicit source override')
    head = command(['/usr/bin/git','-C',source,'rev-parse','HEAD'])
    if head != expected or command(['/usr/bin/git','-C',source,'status','--porcelain','--untracked-files=all']):
        raise ValueError('multiplexer source must be clean at its selected commit')
    pc = '/usr/bin/pkg-config'
    command([pc,'--exists','samplerate','openssl','kilix-encodec'])
    prefix = command([pc,'--define-prefix','--variable=prefix','kilix-encodec'])
    cflags = command([pc,'--define-prefix','--cflags','kilix-encodec'])
    libs = command([pc,'--define-prefix','--libs','kilix-encodec'])
    result = package_plan(prefix,cflags,libs)
    cc = os.environ.get('CC','cc')
    if len(shlex.split(cc)) != 1:
        raise ValueError('CC must name one provisioned compiler executable')
    compiler = shutil.which(cc)
    if compiler is None:
        raise ValueError('C compiler is absent')
    result.update(multiplexer_commit=head,compiler_sha256=hashlib.sha256(data(Path(compiler).resolve())).hexdigest(),
                  flags={key:os.environ.get(key,'') for key in ('CC','CFLAGS','CPPFLAGS','LDFLAGS','LDLIBS')},
                  ENCODEC=1)
    return hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest(),cflags,libs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--binary',action='append',default=[])
    args=parser.parse_args()
    try:
        selected=plan(args.source,args.commit)
        for binary in args.binary:
            dynamic=command(['/usr/bin/readelf','-d',binary])
            symbols=command(['/usr/bin/readelf','--dyn-syms','--wide',binary])
            if 'Shared library: [libkilix-encodec.so.0]' not in dynamic or 'kenc_installed_assets_open' not in symbols:
                raise ValueError('selected multiplexer source did not link installed EnCodec admission; update the host-selected source closure')
        print('\n'.join(selected))
        return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        print('kilix remote: shared EnCodec build prerequisites are unavailable: '+str(error),file=sys.stderr)
        print('Install the source-bound libkilix-encodec package with ORT API21, libsamplerate0-dev and libssl-dev through explicit system setup; then retry.',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
