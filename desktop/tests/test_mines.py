"""Minesweeper: flood reveal count, flag cycle, lose/reset, first-click safety."""
import harness as H
from apps import mines

CELL, FR = mines.CELL, mines.FR


def _cell_xy(w, r, c):
    gx, gy = w.client_origin()
    return (gx + w.field.x + FR + c * CELL + 8,
            gy + w.field.y + FR + r * CELL + 8)


d = H.make_desk()
w = mines.Mines(d)
d.wm.add(w)
assert (w.rows, w.cols, w.n_mines) == (9, 9, 10)

# ── seeded board: one mine at (0,0); a synthetic click floods all 80 safe cells
w.mines = {(0, 0)}
w._compute_counts()
w.placed = True
H.click(d, *_cell_xy(w, 8, 8))
assert w.revealed == 80, w.revealed
assert w.won and not w.dead
assert not w.shown[0][0]                       # the mine stays hidden
assert w.face_key() == "cool"

# ── flag cycle: hidden -> flag -> question -> hidden; reveal blocked while flagged
w.new_game()
w.mines = {(4, 4)}
w._compute_counts()
w.placed = True
assert w.mark[2][2] == 0
w.cycle_mark(2, 2)
assert w.mark[2][2] == 1
w.reveal(2, 2)                                 # a flagged cell cannot be opened
assert not w.shown[2][2] and w.revealed == 0
w.cycle_mark(2, 2)
assert w.mark[2][2] == 2                        # question
w.cycle_mark(2, 2)
assert w.mark[2][2] == 0

# ── losing on a mine sets dead + dead face; New resets everything
w.reveal(4, 4)
assert w.dead and not w.won
assert w.exploded == (4, 4)
assert w.face_key() == "dead"

H.click(d, w.client_origin()[0] + w.face.x + 13,
        w.client_origin()[1] + w.face.y + 13)   # the smiley resets the game
assert not w.dead and not w.won
assert w.revealed == 0 and not w.placed
assert w.face_key() == "smile"

# ── first click is never a mine, even across many fresh boards
for _ in range(30):
    w.new_game()
    w.reveal(3, 3)
    assert (3, 3) not in w.mines
    assert w.shown[3][3]

# ── difficulty switch resizes the field and starts a fresh game
w._set_diff("Expert")
assert (w.rows, w.cols, w.n_mines) == (16, 30, 99)
assert w.field.w == 30 * CELL + 2 * FR
assert w.revealed == 0 and not w.placed

# ── the timer tick hook is registered and unhooks on close
assert w._tick in d.tick_hooks
w.close()
assert w._tick not in d.tick_hooks

print("ok")
