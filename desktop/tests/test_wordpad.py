"""WordPad: word/char count, Find selection, word-wrap toggle is lossless."""
import harness as H
from apps.wordpad import WordPad


def _open(desk):
    w = WordPad(desk)
    desk.wm.add(w)
    return w


# ── word/char count reflects typed text ─────────────────────────────────────
d = H.make_desk()
w = _open(d)
H.type_text(d, "hello world foo bar")
assert w.n_words == 4, w.n_words
assert w.n_chars == len("hello world foo bar"), w.n_chars

# ── Find selects the match ──────────────────────────────────────────────────
assert w.find_next("world")
sel = w.ta._sel()
(ar, ac), (br, bc) = sel
assert ar == br and w.ta.lines[ar][ac:bc] == "world", sel

# ── word-wrap toggle changes layout without data loss ───────────────────────
w.modified = False; w._new()
long = "lorem ipsum dolor " * 20
H.type_text(d, long)
assert len(w.ta.lines) == 1                    # unwrapped: one physical line
before_words = w.n_words
canon = w._text()

w._toggle_wrap()
assert w.wrap
assert len(w.ta.lines) > 1, len(w.ta.lines)    # layout reflowed
assert w._text() == canon                       # no data loss
assert w.n_words == before_words

w._toggle_wrap()
assert not w.wrap
assert w.ta.text() == canon
assert len(w.ta.lines) == 1

# ── Replace All edits the document and marks it modified ─────────────────────
w.modified = False; w._new()
H.type_text(d, "a b a c a")
assert w.replace_all("a", "X") == 3
assert w.ta.text() == "X b X c X"
assert w.modified

# ── font-size change stays lossless under wrap ──────────────────────────────
w.modified = False; w._new()
H.type_text(d, long)
w._toggle_wrap()
w._set_size("16")
assert w._text() == canon

# ── Go To line moves the cursor ─────────────────────────────────────────────
w.modified = False; w._new()
H.type_text(d, "one\ntwo\nthree")
w.goto_line(3)
assert w.ta.cr == 2, w.ta.cr

print("ok")
