"""Solitaire: a foundation move completes a near-win; illegal moves rejected."""
import harness as H
from apps import sol
from apps.sol import Card


def _full(s):
    return [Card(r, s, True) for r in range(1, 14)]


d = H.make_desk()
win = sol.Solitaire(d)
d.wm.add(win)

# ── near-win: three suits home, the fourth at A..Q, its King on the waste ──
win.found = [_full(0), _full(1), _full(2), [Card(r, 3, True) for r in
                                            range(1, 13)]]
win.waste = [Card(13, 3, True)]                 # King of clubs
assert not win.won
assert win.send_to_foundation(win.waste)        # King goes home → win
assert win.won, "foundation move should complete the win"
assert not win.waste
d.wm.modal_top().close()                         # dismiss the "You won!" box

# ── illegal foundation move is rejected (5 onto an empty foundation) ──
win.found = [[], [], [], []]
win.waste = [Card(5, 0, True)]
win.won = False
assert not win.send_to_foundation(win.waste)
assert len(win.waste) == 1                       # untouched

# ── illegal tableau move rejected; the legal counterpart accepted ──
win.tab = [[Card(5, 0, True)] for _ in range(7)]
win.tab[1] = [Card(5, 1, True)]                  # 5♠ onto 5♥ → illegal
assert not win.move_run(win.tab[0], 0, win.tab[1])
assert len(win.tab[0]) == 1
win.tab[1] = [Card(6, 1, True)]                  # 5♠ onto 6♥ → legal
assert win.move_run(win.tab[0], 0, win.tab[1])
assert len(win.tab[1]) == 2 and not win.tab[0]

# ── synthetic events: click stock deals; drag a King onto an empty column ──
win.new_game(seed=1)
gx, gy = win.client_origin()
n = len(win.waste)
H.click(d, gx + win._col_x(0) + 10, gy + win.top_y + 10)
assert len(win.waste) == n + 1, "clicking the stock should deal a card"

win.tab = [[] for _ in range(7)]
win.tab[0] = [Card(13, 0, True)]                 # lone King of spades
x0 = gx + win._col_x(0) + sol.CW // 2
y0 = gy + win.tab_y + 8
x1 = gx + win._col_x(1) + sol.CW // 2
y1 = gy + win.tab_y + 30
H.drag(d, x0, y0, x1, y1)
assert not win.tab[0] and len(win.tab[1]) == 1, "King should drag to empty col"

print("ok")
