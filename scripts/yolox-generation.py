#!/usr/bin/env python3
"""Prepare a path-bound YOLOX generation before changing the selected runtime."""
import ctypes
import fcntl
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


class Interrupted(Exception):
    pass


def read_config(path):
    if not os.path.lexists(path):
        return None
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError('configuration must be a bounded regular file')
        raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('configuration is too large')
        return raw


def rename_flags(source, destination, flags):
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100,os.fsencode(source),-100,os.fsencode(destination),flags):
        error = ctypes.get_errno()
        raise OSError(error,os.strerror(error))


def old_pointer(base):
    pointer = base / '.current'
    if not os.path.lexists(pointer):
        return None
    if not pointer.is_symlink():
        raise ValueError('runtime generation selection is not a symlink')
    target = os.readlink(pointer)
    parts = Path(target).parts
    if len(parts) != 2 or parts[0] != '.generations' or not parts[1].startswith('gen-'):
        raise ValueError('invalid runtime generation selection')
    generation = base / target
    if generation.is_symlink() or not generation.is_dir() or generation.resolve() != generation:
        raise ValueError('runtime generation selection is missing or unsafe')
    return target


def new_config(raw, base, old, wrapper):
    lines = (raw or b'').splitlines(keepends=True)
    selected = [line for line in lines if line.startswith(b'KILIX_OBJECT_DETECTOR=')]
    if len(selected) > 1:
        raise ValueError('multiple detector selections; preserve and resolve them first')
    managed = {str(base/'bin/kilix-yolox-detect')}
    if old is not None:
        managed.add(str(base/old/'bin/kilix-yolox-detect'))
    explicit = os.environ.get('KILIX_OBJECT_DETECTOR')
    if explicit and explicit not in managed:
        return None
    if selected:
        value = selected[0].split(b'=', 1)[1].rstrip(b'\r\n').decode('utf-8')
        if value not in managed:
            return None  # Explicitly preserve a custom or different detector.
    kept = [line for line in lines if not line.startswith(b'KILIX_OBJECT_DETECTOR=')]
    if kept and not kept[-1].endswith(b'\n'):
        kept[-1] += b'\n'
    kept.append(('KILIX_OBJECT_DETECTOR='+str(wrapper)+'\n').encode())
    return b''.join(kept)


def make_link(base, target):
    name = base / ('.current-new-'+secrets.token_hex(8))
    os.symlink(target, name)
    try:
        os.replace(name, base/'.current')
    finally:
        if os.path.lexists(name):
            name.unlink()


def main():
    if len(sys.argv) != 8:
        raise ValueError('expected installer, host, base, source, config, model, size')
    installer, host, base, source, config = map(Path, sys.argv[1:6])
    model, size = sys.argv[6:8]
    if not base.is_absolute() or base.resolve() != base or base in (Path('/'), Path.home()):
        raise ValueError('invalid runtime base')
    if not source.is_absolute() or source.resolve() != source:
        raise ValueError('source path must be an absolute normalized path')
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    generations = base/'.generations'
    if os.path.lexists(generations) and (generations.is_symlink() or not generations.is_dir()):
        raise ValueError('runtime generations path is unsafe')
    generations.mkdir(mode=0o700, exist_ok=True)
    lock_path = base/'.generation.lock'
    fd = os.open(lock_path, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError('runtime generation lock is not a regular file')
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        return prepare(installer,host,base,generations,source,config,model,size)


def prepare(installer, host, base, generations, source, config, model, size):
    original = old_pointer(base)
    before = read_config(config)
    generation = Path(tempfile.mkdtemp(prefix='gen-',dir=generations))
    marker = generation/'.cleanup-uncertain'
    child = None
    received = None
    spawning = False
    committed = False
    exposed = False
    setting_tmp = None
    keep_setting_tmp = False
    def interrupted(number, _frame):
        nonlocal received
        if committed:
            return
        if received is None:
            received = number
        if child is not None:
            try:
                os.kill(child.pid, number)
            except ProcessLookupError:
                marker.touch()
        elif not spawning:
            raise Interrupted()
    previous = {sig:signal.signal(sig,interrupted) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        dest = generation/'source'
        if os.path.lexists(source):
            if source.is_symlink() or not source.is_dir() or (source/'.git').is_symlink() or (source/'.git').is_file():
                raise ValueError('source snapshot requires a standalone directory')
            if source.resolve() == generation or source.resolve() in generation.parents:
                raise ValueError('source overlaps the runtime generation')
            if os.path.lexists(source/'.git/objects/info/alternates') or (source/'.git/shallow').exists():
                raise ValueError('source snapshot cannot use Git alternates or shallow history')
            shutil.copytree(source,dest,symlinks=True)
            for path in dest.rglob('*'):
                if path.is_symlink():
                    target = Path(os.readlink(path))
                    try:
                        safe = not target.is_absolute() and path.resolve().is_relative_to(dest)
                    except RuntimeError:
                        safe = False
                    if not safe:
                        raise ValueError('source snapshot has an escaping symlink')
        supervisor = host/'scripts/detector-carrier-supervisor.py'
        if supervisor.is_symlink() or not supervisor.is_file():
            raise ValueError('owned process supervisor is unavailable')
        env = dict(os.environ, KILIX_HOME=str(host),KILIX_YOLOX_DIR=str(generation),
                   KILIX_YOLOX_SRC=str(dest),KILIX_YOLOX_MODEL=model,KILIX_YOLOX_SIZE=size,
                   KILIX_YOLOX_INTERNAL='1',KILIX_YOLOX_PARENT=str(base),KILIX_YOLOX_ASSUME_YES='1',
                   KILIX_USER_CONFIG_DIRECTORY=str(generation/'.stage-config'))
        count = env.get('GIT_CONFIG_COUNT','0')
        if not count.isascii() or not count.isdecimal() or int(count) > 1000:
            raise ValueError('invalid inherited Git configuration count')
        index = int(count)
        env['GIT_CONFIG_KEY_'+str(index)] = 'core.hooksPath'
        env['GIT_CONFIG_VALUE_'+str(index)] = os.devnull
        env['GIT_CONFIG_COUNT'] = str(index+1)
        def run(action):
            nonlocal child, spawning
            if received is not None:
                raise Interrupted()
            spawning = True
            try:
                child = subprocess.Popen([sys.executable,'-I','-B',str(supervisor),str(marker),
                                          str(installer),action,'--yes'],env=env,stdout=subprocess.DEVNULL)
            finally:
                spawning = False
            if received is not None:
                try:
                    os.kill(child.pid,received)
                except ProcessLookupError:
                    marker.touch()
            while child.poll() is None:
                time.sleep(.05)
            status = child.wait()
            child = None
            if status == 125 or status < 0:
                marker.touch()
            if received is not None:
                raise Interrupted()
            if status != 0:
                raise ValueError('runtime generation '+action+' failed')
        run('--install')
        run('--check')
        wrapper = generation/'bin/kilix-yolox-detect'
        if wrapper.is_symlink() or not wrapper.is_file() or not os.access(wrapper,os.X_OK):
            raise ValueError('prepared detector wrapper is unavailable')
        after = read_config(config)
        if before != after or old_pointer(base) != original:
            raise ValueError('runtime selection changed during preparation')
        setting = new_config(before,base,original,wrapper)
        if setting is not None:
            config.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix='.kilix.env-',dir=config.parent)
            setting_tmp = Path(name)
            with os.fdopen(descriptor,'wb') as out:
                os.fchmod(out.fileno(),0o600)
                out.write(setting)
        target = str(generation.relative_to(base))
        switched = False
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT,signal.SIGTERM})
        try:
            if read_config(config) != before or old_pointer(base) != original:
                raise ValueError('runtime selection changed before activation')
            make_link(base,target)
            switched = True
            exposed = True
            if setting_tmp is not None:
                if before is None:
                    # Never replace a configuration that appeared concurrently.
                    rename_flags(setting_tmp,config,1)  # RENAME_NOREPLACE
                else:
                    # Inspect the displaced file to detect a concurrent writer
                    # between our last read and the atomic exchange.
                    rename_flags(setting_tmp,config,2)  # RENAME_EXCHANGE
                    try:
                        if read_config(setting_tmp) != before:
                            raise ValueError('runtime selection changed during activation')
                    except BaseException:
                        try:
                            can_restore = read_config(config) == setting
                        except (OSError,ValueError):
                            can_restore = False
                        if can_restore:
                            rename_flags(setting_tmp,config,2)
                        else:
                            keep_setting_tmp = True
                            marker.touch()
                            print('kilix yolox: displaced configuration retained at '+str(setting_tmp),file=sys.stderr)
                        raise
            committed = True
        except BaseException:
            if switched:
                try:
                    still_selected = old_pointer(base) == target
                except (OSError,ValueError):
                    still_selected = False
                if still_selected:
                    if original is None:
                        (base/'.current').unlink()
                    else:
                        make_link(base,original)
                else:
                    print('kilix yolox: concurrent runtime selection preserved',file=sys.stderr)
            raise
        finally:
            if setting_tmp is not None and setting_tmp.exists() and not keep_setting_tmp:
                try:
                    setting_tmp.unlink()
                except OSError as error:
                    print('kilix yolox: could not remove temporary configuration: '+str(error),file=sys.stderr)
            signal.pthread_sigmask(signal.SIG_SETMASK,blocked)
        if setting is None:
            print('kilix yolox: preserved existing detector selection',file=sys.stderr)
        print(wrapper)
        return 0
    except Interrupted:
        return 128 + (received or signal.SIGTERM)
    finally:
        for sig in previous:
            signal.signal(sig,signal.SIG_IGN)
        if child is not None:
            try:
                child.terminate()
                status = child.wait(timeout=10)
                if status == 125 or status < 0:
                    marker.touch()
                child = None
            except (OSError,subprocess.SubprocessError):
                marker.touch()
        if not committed:
            if child is not None or marker.exists() or exposed:
                print('kilix yolox: cleanup uncertain; preserving '+str(generation),file=sys.stderr)
            else:
                try:
                    shutil.rmtree(generation)
                except OSError as error:
                    print('kilix yolox: could not remove unselected generation: '+str(error),file=sys.stderr)
        if setting_tmp is not None and setting_tmp.exists() and not keep_setting_tmp:
            try:
                setting_tmp.unlink()
            except OSError as error:
                print('kilix yolox: could not remove temporary configuration: '+str(error),file=sys.stderr)
        for sig,handler in previous.items():
            signal.signal(sig,handler)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('kilix yolox: '+str(error),file=sys.stderr)
        raise SystemExit(1)
