"""Reviewed vocabulary delta against the pinned Needle apps parser.

Keep Needle's admission, confirmation and readiness checks intact. Both game
ids enter its ordinary tables before candidate calls and parser caches exist.
Unexpected source versions fail closed instead of receiving a fuzzy patch.
"""
import hashlib

BEFORE = 'c3d768abc1dcd2434e6c07311c124c277c58e4614e33731fecc71de0273fa250'
CHANGES = (
    ('"terminal-lander", "kitty-brokeout", "kilix-land", "tictactoe-tui", "pleb-tower")',
     '"terminal-lander", "kitty-brokeout", "kilix-land", "tictactoe-tui", "pleb-tower", "pleb-plant-grower")'),
    ('    "kilix-land": ["kilix land"],',
     '    "kilix-land": ["kilix land"],\n'
     '    "pleb-plant-grower": ["plant grower", "pleb plant grower", "plant-grower"],'),
    ('    "tictactoe-tui": ["tic tac toe", "tic-tac-toe", "noughts and crosses"],',
     '    "tictactoe-tui": ["tic tac toe", "tic-tac-toe", "noughts and crosses", "tictactoe"],'),
)


def patch_needle(generation):
    path = generation / 'needle/apps.py'
    if not path.is_file():
        raise ValueError('Pinned Needle apps parser is missing')
    data = path.read_text()
    original = data
    for before, after in CHANGES:
        original = original.replace(after, before)
    if hashlib.sha256(original.encode()).hexdigest() != BEFORE:
        raise ValueError('Needle apps source differs from the reviewed vocabulary patch')
    for before, after in CHANGES:
        if original.count(before) != 1:
            raise ValueError('Needle vocabulary patch context is ambiguous')
        original = original.replace(before, after)
    path.write_text(original)
