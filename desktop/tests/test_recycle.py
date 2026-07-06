"""Recycle Bin library in isolation (desktop/recycle.py)."""
import os
import tempfile

import harness  # noqa: F401  (sets sys.path for desktop modules)
import recycle


def _bin():
    """Point the bin at a fresh temp dir; return (bin_dir, work_dir)."""
    b = tempfile.mkdtemp(prefix="kilix95-recbin-")
    os.environ["KILIX_RECYCLE_DIR"] = b
    return b, tempfile.mkdtemp(prefix="kilix95-recwork-")


def test_send_records_sidecar_and_moves():
    _, work = _bin()
    p = os.path.join(work, "note.txt")
    with open(p, "w") as f:
        f.write("hello")
    tok = recycle.send(p)
    assert not os.path.exists(p)                 # moved out of the source
    items = recycle.items()
    assert len(items) == 1
    it = items[0]
    assert it["token"] == tok
    assert it["orig"] == os.path.abspath(p)
    assert it["name"] == "note.txt"
    assert it["size"] == 5 and not it["is_dir"]


def test_restore_returns_to_origin():
    _, work = _bin()
    p = os.path.join(work, "a.txt")
    with open(p, "w") as f:
        f.write("A")
    tok = recycle.send(p)
    back = recycle.restore(tok)
    assert back == os.path.abspath(p)
    assert os.path.exists(p)
    assert recycle.items() == []


def test_restore_disambiguates_when_occupied():
    _, work = _bin()
    p = os.path.join(work, "a.txt")
    with open(p, "w") as f:
        f.write("old")
    tok = recycle.send(p)
    with open(p, "w") as f:                        # a new file takes the name
        f.write("new")
    back = recycle.restore(tok)
    assert back != os.path.abspath(p)              # landed beside the occupant
    assert os.path.exists(back)
    with open(p) as f:
        assert f.read() == "new"                   # the occupant is untouched


def test_send_dir_recursive_size():
    _, work = _bin()
    d = os.path.join(work, "folder")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "sub", "x.bin"), "wb") as f:
        f.write(b"0123456789")
    recycle.send(d)
    it = recycle.items()[0]
    assert it["is_dir"] and it["size"] == 10
    assert not os.path.exists(d)


def test_purge_and_empty():
    _, work = _bin()
    for n in ("a", "b", "c"):
        p = os.path.join(work, n)
        open(p, "w").close()
        recycle.send(p)
    items = recycle.items()
    assert len(items) == 3
    recycle.purge(items[0]["token"])
    assert len(recycle.items()) == 2
    recycle.empty()
    assert recycle.items() == []


def test_survives_corrupt_sidecar():
    b, work = _bin()
    p = os.path.join(work, "keep.txt")
    with open(p, "w") as f:
        f.write("DATA")
    tok = recycle.send(p)
    with open(os.path.join(b, "files", tok + ".info"), "w") as f:
        f.write("{ not json")                      # clobber the index sidecar
    items = recycle.items()                         # must not raise
    assert len(items) == 1 and items[0]["token"] == tok
    assert items[0]["name"] == tok                  # degraded but listed


def test_missing_bin_regenerates():
    b, _ = _bin()
    import shutil
    shutil.rmtree(b)                                # whole store vanishes
    assert recycle.items() == []                    # rebuilt empty, no crash


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
