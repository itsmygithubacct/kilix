"""Install the checksum-pinned Whistle library without importing upstream code."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile

REVISION = "2ae11323dc000f5e70c49f7403efa6af12ba9e67"
WHEEL = "cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl"
WHEEL_SHA256 = "0e8a3bce4e52968ee14e8e9d47098e65ed06dd5b7c18cef3a7fa1375756736f9"
LIB_SHA256 = "0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030"
LICENSE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
BASE = f"https://huggingface.co/Cactus-Compute/needle3/resolve/{REVISION}"
STAMP = f"needle3={REVISION} library={LIB_SHA256} layout=1\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url, path, maximum):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("download exceeds its size limit")
    path.write_bytes(data)


def install(root, wheel=None, licence=None):
    if sys.platform != "linux" or platform.machine() != "x86_64" or platform.libc_ver()[0] != "glibc":
        raise ValueError("this pinned Whistle build requires Linux x86_64 with glibc")
    root.mkdir(parents=True, exist_ok=True)
    with open(root / '.install.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = root / 'current'
        if current.exists() and not current.is_symlink():
            raise ValueError("managed current path is not a symlink")
        generations = root / 'generations'
        generations.mkdir(exist_ok=True)
        if current.is_symlink() and current.resolve().parent != generations.resolve():
            raise ValueError("managed current link points outside the generation store")
        generation = generations / REVISION
        def valid():
            try:
                return (not generation.is_symlink() and
                        (generation / 'REF').read_text() == STAMP and
                        digest(generation / 'libneedle3.so') == LIB_SHA256 and
                        digest(generation / 'LICENSE') == LICENSE_SHA256)
            except OSError:
                return False
        if not valid():
            if generation.exists() or generation.is_symlink():
                raise ValueError("existing pinned generation differs; move it aside before reinstalling")
            with tempfile.TemporaryDirectory(prefix='.stage-', dir=generations) as tmp:
                stage = Path(tmp)
                archive = stage / WHEEL
                notice = stage / 'LICENSE'
                if wheel:
                    shutil.copyfile(wheel, archive)
                    shutil.copyfile(licence, notice)
                else:
                    fetch(BASE + '/python/' + WHEEL, archive, 2 * 1024 * 1024)
                    fetch(BASE + '/LICENSE', notice, 64 * 1024)
                if digest(archive) != WHEEL_SHA256 or digest(notice) != LICENSE_SHA256:
                    raise ValueError("pinned wheel or licence checksum mismatch")
                with zipfile.ZipFile(archive) as package:
                    # Read one exact member; never extract paths or install a wheel.
                    data = package.read('needle/libneedle3.so')
                if hashlib.sha256(data).hexdigest() != LIB_SHA256:
                    raise ValueError("native library checksum mismatch")
                (stage / 'libneedle3.so').write_bytes(data)
                (stage / 'REF').write_text(STAMP)
                archive.unlink()
                os.rename(stage, generation)
        # A unique temporary link and atomic replacement keep a failed install
        # from damaging a working selection.
        with tempfile.TemporaryDirectory(prefix='.link-', dir=root) as tmp:
            link = Path(tmp) / 'current'
            link.symlink_to(generation.resolve())
            os.replace(link, current)
    return current / 'libneedle3.so'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    query = parser.add_mutually_exclusive_group()
    query.add_argument('--print-ref', action='store_true')
    query.add_argument('--print-path', action='store_true')
    parser.add_argument('--from-wheel', type=Path, help='use an already downloaded pinned wheel')
    parser.add_argument('--licence', type=Path, help='pinned LICENSE, required with --from-wheel')
    args = parser.parse_args(argv)
    if bool(args.from_wheel) != bool(args.licence):
        parser.error('--from-wheel and --licence are required together')
    home = Path(os.environ.get('GPU_TERMINAL_HOME', str(Path.home() / '.local/gpu_terminal')))
    data = Path(os.environ.get('KILIX_DATA_HOME', str(home / 'kilix/data')))
    root = data / 'voice/whistle'
    if args.print_ref:
        print('needle3=' + REVISION)
        return 0
    if args.print_path:
        print(root / 'current/libneedle3.so')
        return 0
    if os.geteuid() == 0:
        parser.error('run this as the desktop user')
    os.umask(0o077)
    try:
        print('kilix whistle: runtime ready at ' + str(install(root, args.from_wheel, args.licence)))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        print(f'kilix whistle: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
