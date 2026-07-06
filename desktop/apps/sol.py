"""kilix desktop — Solitaire (Klondike). Deal-1 stock, drag runs, foundations.

The whole board is custom-drawn in draw_client; input is handled in on_mouse
with the desk's press-capture (mouse_owner) carrying every move/release of a
card drag back here. Cards, suits and backs are original pixel art.
"""
import random

import theme as T
import widgets as W
import wm

SUITS = ("spade", "heart", "diamond", "club")   # 0 3 black · 1 2 red
RANKS = ("", "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RED = (170, 0, 0)
BLACK = (0, 0, 0)
FELT = (0, 120, 64)
FELT_D = (0, 80, 44)

CW, CH = 48, 66
GAPX = 10
MARGIN = 14
FAN_DOWN = 5
FAN_UP = 16
STEP = CW + GAPX


def _red(c):
    return c.suit in (1, 2)


class Card:
    __slots__ = ("rank", "suit", "up")

    def __init__(self, rank, suit, up=False):
        self.rank, self.suit, self.up = rank, suit, up


# ── suit pips (drawn in code) ───────────────────────────────────────────────
def _circ(d, cx, cy, r, col):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)


def _heart(d, cx, cy, r, col):
    d.ellipse([cx - r, cy - r, cx, cy], fill=col)
    d.ellipse([cx, cy - r, cx + r, cy], fill=col)
    d.polygon([(cx - r, cy - r * 0.35), (cx + r, cy - r * 0.35),
               (cx, cy + r)], fill=col)


def _diamond(d, cx, cy, r, col):
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
              fill=col)


def _spade(d, cx, cy, r, col):
    d.polygon([(cx, cy - r), (cx - r, cy + r * 0.4), (cx + r, cy + r * 0.4)],
              fill=col)
    d.ellipse([cx - r, cy - r * 0.1, cx, cy + r * 0.9], fill=col)
    d.ellipse([cx, cy - r * 0.1, cx + r, cy + r * 0.9], fill=col)
    d.polygon([(cx - r * 0.5, cy + r), (cx + r * 0.5, cy + r),
               (cx, cy + r * 0.15)], fill=col)


def _club(d, cx, cy, r, col):
    rr = r * 0.6
    _circ(d, cx, cy - r * 0.4, rr, col)
    _circ(d, cx - r * 0.55, cy + r * 0.3, rr, col)
    _circ(d, cx + r * 0.55, cy + r * 0.3, rr, col)
    d.polygon([(cx - r * 0.35, cy + r), (cx + r * 0.35, cy + r),
               (cx, cy + r * 0.1)], fill=col)


_PIP = (_spade, _heart, _diamond, _club)


class Solitaire(wm.Window):
    def __init__(self, desk, arg=None):
        super().__init__(desk, "Solitaire", 432, 456, icon="cards")
        self.min_w = self.min_h = 432
        cw, _ = self.client_size()
        self.menubar = self.add(W.MenuBar(cw, [("Game", self._game_menu),
                                               ("Help", self._help_menu)]))
        self.top_y = T.MENU_H + 14
        self.tab_y = self.top_y + CH + 18
        self.stock = []           # face-down deck
        self.waste = []           # dealt cards (face up)
        self.found = [[], [], [], []]
        self.tab = [[], [], [], [], [], [], []]
        self.drag = None          # active pick-up
        self.won = False
        self.new_game()

    def on_resize(self):
        self.menubar.w = self.client_size()[0]

    # ── geometry ────────────────────────────────────────────────────────────
    @staticmethod
    def _col_x(i):
        return MARGIN + i * STEP

    def _card_y(self, pile, j):
        y = self.tab_y
        for k in range(j):
            y += FAN_UP if pile[k].up else FAN_DOWN
        return y

    def _tab_hit(self, i, py):
        pile = self.tab[i]
        if not pile:
            return None
        hit = None
        for j in range(len(pile)):
            top = self._card_y(pile, j)
            bot = (self._card_y(pile, j + 1) if j + 1 < len(pile)
                   else top + CH)
            if top <= py < bot:
                hit = j
        return hit

    # ── deck ────────────────────────────────────────────────────────────────
    def new_game(self, seed=None):
        deck = [Card(r, s) for s in range(4) for r in range(1, 14)]
        random.Random(seed).shuffle(deck)
        self.found = [[], [], [], []]
        self.tab = [[] for _ in range(7)]
        for i in range(7):
            for j in range(i + 1):
                c = deck.pop()
                c.up = (j == i)
                self.tab[i].append(c)
        self.stock = deck
        self.waste = []
        self.drag = None
        self.won = False
        self.invalidate()

    # ── rules ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _can_found(card, f):
        if not f:
            return card.rank == 1
        return f[-1].suit == card.suit and card.rank == f[-1].rank + 1

    @staticmethod
    def _can_stack(card, onto):
        if onto is None:
            return card.rank == 13
        return (onto.up and _red(onto) != _red(card)
                and onto.rank == card.rank + 1)

    @staticmethod
    def _valid_run(run):
        for a, b in zip(run, run[1:]):
            if not (a.up and b.up and _red(a) != _red(b)
                    and a.rank == b.rank + 1):
                return False
        return bool(run) and run[0].up

    def _after_take(self, src):
        if src in self.tab and src and not src[-1].up:
            src[-1].up = True

    def _check_win(self):
        if all(len(f) == 13 for f in self.found) and not self.won:
            self.won = True
            wm.msgbox(self.desk, "Solitaire", "You won!\nDeal a new game?",
                      icon="cards", buttons=("New Game", "Close"),
                      cb=lambda a: self.new_game() if a == "New Game"
                      else None)

    def send_to_foundation(self, src):
        """Top card of src → a matching foundation. Returns True if moved."""
        if not src or not src[-1].up:
            return False
        card = src[-1]
        for f in self.found:
            if self._can_found(card, f):
                f.append(src.pop())
                self._after_take(src)
                self.invalidate()
                self._check_win()
                return True
        return False

    def move_run(self, src, k, dst):
        """Move src[k:] onto tableau list dst if legal. Returns True if moved."""
        run = src[k:]
        if not self._valid_run(run):
            return False
        if not self._can_stack(run[0], dst[-1] if dst else None):
            return False
        dst.extend(run)
        del src[k:]
        self._after_take(src)
        self.invalidate()
        return True

    def deal_stock(self):
        if self.stock:
            c = self.stock.pop()
            c.up = True
            self.waste.append(c)
        elif self.waste:
            self.stock = [Card(c.rank, c.suit) for c in reversed(self.waste)]
            self.waste = []
        self.invalidate()

    # ── input ─────────────────────────────────────────────────────────────────
    def on_mouse(self, gev):
        cox, coy = self.client_origin()
        ev = gev.at(cox, coy)
        cw, ch = self.client_size()
        if self.drag is not None:
            if ev.move:
                self.drag["x"] = ev.x - self.drag["gx"]
                self.drag["y"] = ev.y - self.drag["gy"]
                self.invalidate()
            elif not ev.press and not ev.wheel:
                self._drop(ev.x, ev.y)
            return
        inside = 0 <= ev.x < cw and 0 <= ev.y < ch
        if gev.press and inside and ev.y >= T.MENU_H:
            self._board_press(ev)
            return
        super().on_mouse(gev)

    def _board_press(self, ev):
        x, y = ev.x, ev.y
        # stock
        if (self._col_x(0) <= x < self._col_x(0) + CW
                and self.top_y <= y < self.top_y + CH):
            self.deal_stock()
            return
        # waste (top card)
        if (self._col_x(1) <= x < self._col_x(1) + CW
                and self.top_y <= y < self.top_y + CH and self.waste):
            if ev.clicks >= 2 and self.send_to_foundation(self.waste):
                return
            self._pick(self.waste, len(self.waste) - 1,
                       self._col_x(1), self.top_y, x, y)
            return
        # foundations (drag a card back off)
        for fi in range(4):
            fx = self._col_x(3 + fi)
            if (fx <= x < fx + CW and self.top_y <= y < self.top_y + CH
                    and self.found[fi]):
                self._pick(self.found[fi], len(self.found[fi]) - 1,
                           fx, self.top_y, x, y)
                return
        # tableau
        for i in range(7):
            tx = self._col_x(i)
            if not (tx <= x < tx + CW):
                continue
            j = self._tab_hit(i, y)
            if j is None or not self.tab[i][j].up:
                return
            if (ev.clicks >= 2 and j == len(self.tab[i]) - 1
                    and self.send_to_foundation(self.tab[i])):
                return
            if self._valid_run(self.tab[i][j:]):
                self._pick(self.tab[i], j, tx, self._card_y(self.tab[i], j),
                           x, y)
            return

    def _pick(self, src, k, ox, oy, x, y):
        self.drag = {"src": src, "k": k, "gx": x - ox, "gy": y - oy,
                     "x": ox, "y": oy}
        self.invalidate()

    def _drop(self, x, y):
        d = self.drag
        self.drag = None
        src, k = d["src"], d["k"]
        px = d["x"] + CW // 2               # dropped card's center x
        py = d["y"]
        col = max(0, min(6, round((px - MARGIN) / STEP)))
        if py < self.tab_y - 6:             # top row → foundation only
            if col >= 3 and k == len(src) - 1:
                self.send_to_foundation_at(src, col - 3)
        else:
            self.move_run(src, k, self.tab[col])
        self.invalidate()

    def send_to_foundation_at(self, src, fi):
        card = src[-1]
        if self._can_found(card, self.found[fi]):
            self.found[fi].append(src.pop())
            self._after_take(src)
            self._check_win()
            return True
        return False

    # ── drawing ─────────────────────────────────────────────────────────────
    def draw_client(self, d, img):
        cw, ch = self.client_size()
        d.rectangle([0, T.MENU_H, cw - 1, ch - 1], fill=FELT)
        self._slot(d, self._col_x(0), self.top_y)      # stock frame
        if self.stock:
            self._back(d, self._col_x(0), self.top_y)
        else:
            d.ellipse([self._col_x(0) + 16, self.top_y + 24,
                       self._col_x(0) + 32, self.top_y + 40], outline=FELT_D)
        self._slot(d, self._col_x(1), self.top_y)      # waste
        if self.waste:
            self._face(d, self._col_x(1), self.top_y, self.waste[-1])
        for fi in range(4):                            # foundations
            fx = self._col_x(3 + fi)
            self._slot(d, fx, self.top_y)
            if self.found[fi]:
                self._face(d, fx, self.top_y, self.found[fi][-1])
            else:
                _PIP[fi](d, fx + CW // 2, self.top_y + CH // 2, 11, FELT_D)
        for i in range(7):                             # tableau
            tx = self._col_x(i)
            if not self.tab[i]:
                self._slot(d, tx, self.tab_y)
            skip = (self.drag["src"], self.drag["k"]) if self.drag else None
            for j, c in enumerate(self.tab[i]):
                if skip and skip[0] is self.tab[i] and j >= skip[1]:
                    break
                self._card(d, tx, self._card_y(self.tab[i], j), c)
        if self.drag:                                  # floating pick-up
            dx, dy = self.drag["x"], self.drag["y"]
            for n, c in enumerate(self.drag["src"][self.drag["k"]:]):
                self._face(d, dx, dy + n * FAN_UP, c)

    def _slot(self, d, x, y):
        d.rectangle([x, y, x + CW - 1, y + CH - 1], outline=FELT_D)
        self._round(d, x, y)

    def _round(self, d, x, y):
        for cx, cy in ((x, y), (x + CW - 1, y),
                       (x, y + CH - 1), (x + CW - 1, y + CH - 1)):
            d.point((cx, cy), fill=FELT)

    def _card(self, d, x, y, c):
        if c.up:
            self._face(d, x, y, c)
        else:
            self._back(d, x, y)

    def _back(self, d, x, y):
        d.rectangle([x, y, x + CW - 1, y + CH - 1], fill=(0, 0, 150),
                    outline=BLACK)
        d.rectangle([x + 3, y + 3, x + CW - 4, y + CH - 4], outline=(120, 160,
                                                                      255))
        for gy in range(y + 6, y + CH - 5, 6):
            for gx in range(x + 6, x + CW - 5, 6):
                d.point((gx, gy), fill=(120, 160, 255))
        self._round(d, x, y)

    def _face(self, d, x, y, c):
        d.rectangle([x, y, x + CW - 1, y + CH - 1], fill=T.WINDOW_BG,
                    outline=BLACK)
        self._round(d, x, y)
        col = RED if _red(c) else BLACK
        r = RANKS[c.rank]
        d.text((x + 3, y + 2), r, font=T.FONT, fill=col)
        _PIP[c.suit](d, x + 6, y + 20, 3, col)
        _PIP[c.suit](d, x + CW // 2, y + CH // 2 + 3, 11, col)
        rw = T.text_w(T.FONT, r)
        d.text((x + CW - 4 - rw, y + CH - 15), r, font=T.FONT, fill=col)

    # ── menus ─────────────────────────────────────────────────────────────────
    def _game_menu(self):
        MI, sep = W.MenuItem, W.sep
        return [MI("New Game", action=lambda: self.new_game()),
                sep(),
                MI("Close", action=self.request_close)]

    def _help_menu(self):
        return [W.MenuItem(
            "About Solitaire…", icon="cards",
            action=lambda: wm.msgbox(
                self.desk, "About Solitaire",
                "kilix 95 Solitaire\nKlondike, deal one.\n"
                "Click the stock to deal; drag runs between piles;\n"
                "double-click to send a card home.", icon="cards"))]
