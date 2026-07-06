"""WordPad rich text: styled runs, style application, .krt round-trip,
run-boundary backspace, plain-.txt export, word/char count and Find."""
import json
import os
import tempfile

import harness as H
from apps.wordpad import WordPad, _ptext, _segments, _font, _plen, MAX_SIZE


def _open(desk):
    w = WordPad(desk)
    desk.wm.add(w)
    return w


def _run_at(rta, pi, off):
    return _segments(rta.paras[pi], off, off + 1)[0][0]


# ── word/char count reflects typed text ─────────────────────────────────────
d = H.make_desk()
w = _open(d)


def typ(s):                              # keep w active (secondary windows steal it)
    d.wm.activate(w)
    H.type_text(d, s)


typ("hello world foo bar")
assert w.n_words == 4, w.n_words
assert w.n_chars == len("hello world foo bar"), w.n_chars
assert _ptext(w.rta.paras[0]) == "hello world foo bar"

# ── Enter splits a paragraph, carrying the run style ────────────────────────
typ("\nsecond line")
assert len(w.rta.paras) == 2, len(w.rta.paras)
assert _ptext(w.rta.paras[1]) == "second line"

# ── select a range and apply bold → that run is bold ────────────────────────
w.modified = False
w.rta.set_doc([])
typ("hello world")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 5)                     # select "hello"
w.rta.toggle("bold")
assert _run_at(w.rta, 0, 0)["bold"] is True
assert _run_at(w.rta, 0, 6)["bold"] is False    # "world" untouched
# the run split at the selection boundary
assert len(w.rta.paras[0]) == 2, w.rta.paras[0]

# ── B/I/U toolbar reflects the selection's state ────────────────────────────
w._refresh_tb()
assert w.b_bold.toggled is True
w.rta.anchor = (0, 6)
w.rta.caret = (0, 11)                    # select "world"
w._refresh_tb()
assert w.b_bold.toggled is False

# ── italic + underline + size + colour on a selection ───────────────────────
w.rta.anchor = (0, 0)
w.rta.caret = (0, 5)
w.rta.toggle("italic")
w.rta.toggle("underline")
w.rta.set_style("size", 18)
w.rta.set_style("color", (255, 0, 0))
r = _run_at(w.rta, 0, 0)
assert r["italic"] and r["underline"] and r["bold"]
assert r["size"] == 18 and tuple(r["color"]) == (255, 0, 0)

# ── typing style at the caret (no selection) applies to new text ────────────
w.rta.anchor = None
w.rta.caret = (0, _plen(w.rta.paras[0]))
w.rta.set_style("bold", True)
w.rta.set_style("color", (0, 0, 255))
typ("X")
rx = _run_at(w.rta, 0, _plen(w.rta.paras[0]) - 1)
assert rx["bold"] is True and tuple(rx["color"]) == (0, 0, 255)

# ── font cache returns the right DejaVu variant ─────────────────────────────
assert _font(True, False, 12) is _font(True, False, 12)     # cached
assert _font(True, False, 12) is not _font(False, True, 12)

# ── .krt round-trip is lossless ─────────────────────────────────────────────
w.modified = False
w.rta.set_doc([])
typ("plain ")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 5)
w.rta.toggle("bold")
w.rta.set_style("color", (0, 128, 0))
w.rta.anchor = None
w.rta.caret = (0, _plen(w.rta.paras[0]))
typ("tail")
before = w.rta.to_obj()

tmp = tempfile.mkdtemp(prefix="wp-")
krt = os.path.join(tmp, "doc.krt")
w._save(path=krt)
assert os.path.exists(krt)
with open(krt) as f:
    on_disk = json.load(f)
assert on_disk == before                       # serialisation stable

w2 = _open(d)
w2._load(krt)
assert w2.rta.to_obj() == before               # styles survived load
r0 = _run_at(w2.rta, 0, 0)
assert r0["bold"] and tuple(r0["color"]) == (0, 128, 0)

# ── backspace across a run boundary keeps styles sane ───────────────────────
w.modified = False
w.rta.set_doc([])
typ("AB")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 1)                      # select "A"
w.rta.toggle("bold")                     # "A" bold, "B" not → two runs
assert len(w.rta.paras[0]) == 2
w.rta.anchor = None
w.rta.caret = (0, 2)                      # caret after "B"
w.rta._backspace()                       # delete "B"
assert _ptext(w.rta.paras[0]) == "A"
assert _run_at(w.rta, 0, 0)["bold"] is True
assert len(w.rta.paras[0]) == 1          # runs stayed normalised
w.rta._backspace()                       # delete "A"
assert _ptext(w.rta.paras[0]) == ""
assert w.rta.paras[0] == []              # empty paragraph, no stray runs

# ── backspace at paragraph start merges paragraphs, keeping styles ──────────
w.rta.set_doc([])
typ("one")
w.rta.set_style("italic", True)
typ("\ntwo")                  # "two" is italic, own paragraph
assert len(w.rta.paras) == 2
w.rta.anchor = None
w.rta.caret = (1, 0)
w.rta._backspace()                       # merge para 2 into para 1
assert len(w.rta.paras) == 1
assert _ptext(w.rta.paras[0]) == "onetwo"
assert _run_at(w.rta, 0, 0)["italic"] is False   # "one" plain
assert _run_at(w.rta, 0, 3)["italic"] is True    # "two" italic

# ── plain .txt export flattens; import is unstyled ──────────────────────────
w.modified = False
w.rta.set_doc([])
typ("first line")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 5)
w.rta.toggle("bold")
w.rta.anchor = None
w.rta.caret = (0, _plen(w.rta.paras[0]))
typ("\nsecond line")
txt = os.path.join(tmp, "doc.txt")
w._save(path=txt)
with open(txt) as f:
    body = f.read()
assert body == "first line\nsecond line", repr(body)

w3 = _open(d)
w3._load(txt)
assert w3.rta.plain_text() == "first line\nsecond line"
for para in w3.rta.paras:                # imported text has no styling
    for run in para:
        assert not (run["bold"] or run["italic"] or run["underline"])

# ── Find selects the match across paragraphs ────────────────────────────────
w.modified = False
w.rta.set_doc([])
typ("alpha\nbeta gamma")
w.rta.caret = (0, 0)
w.rta.anchor = None
assert w.find_next("gamma")
(ar, ac), (br, bc) = w.rta._sel()
assert ar == br == 1
assert _ptext(w.rta.paras[1])[ac:bc] == "gamma"

# ── empty document is robust ────────────────────────────────────────────────
w.rta.set_doc([])
assert w.rta.paras == [[]]
w.rta._backspace()                       # nothing to delete
w.rta._delete()
assert w.rta.plain_text() == ""
words, chars, lines = w.rta.counts()
assert words == 0 and chars == 0 and lines == 1

# ── a render pass does not raise (styled, multi-size, selection) ─────────────
w.rta.set_doc([])
typ("render me please with wrap " * 6)
w.rta.anchor = (0, 3)
w.rta.caret = (0, 40)
w.rta.set_style("size", 24)
w.invalidate()
d.dirty = True
d.render()

# ── malformed .krt degrades to plaintext instead of crashing ────────────────
for bad in ("null", "[1, 2, 3]", '{"paras": [["x"]]}', "not json at all"):
    p = os.path.join(tmp, "bad.krt")
    with open(p, "w") as f:
        f.write(bad)
    wb = _open(d)
    wb._load(p)                                  # must not raise
    assert wb.rta.plain_text() == bad, (bad, wb.rta.plain_text())

# ── .rtf is treated as plaintext, not kilix rich JSON ───────────────────────
w.modified = False
w.rta.set_doc([])
typ("styled ")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 6)
w.rta.toggle("bold")
rtf = os.path.join(tmp, "doc.rtf")
w._save(path=rtf)
with open(rtf) as f:
    body = f.read()
assert body == "styled ", repr(body)            # flattened, not JSON
assert not w._is_rich(rtf) and w._is_rich("x.krt")

# ── bare-path Save keeps styling by defaulting to .krt ──────────────────────
w.modified = False
w.rta.set_doc([])
typ("keepme ")
w.rta.anchor = (0, 0)
w.rta.caret = (0, 6)
w.rta.toggle("bold")
bare = os.path.join(tmp, "document")
w._save(path=bare)
assert w.path == bare + ".krt", w.path
with open(w.path) as f:
    assert json.load(f).get("kilix_rich") == 1   # rich JSON, styling preserved

# ── the font cache is bounded: oversized sizes clamp to one key ──────────────
assert _font(False, False, 10 ** 6) is _font(False, False, MAX_SIZE)
assert _font(False, False, 0) is _font(False, False, 1)

print("ok")
