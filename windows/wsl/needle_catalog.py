"""Reviewed vocabulary delta against the pinned Needle apps parser.

Keep Needle's admission, confirmation and readiness checks intact. Both game
ids enter its ordinary tables before candidate calls and parser caches exist.
Unexpected source versions fail closed instead of receiving a fuzzy patch.
"""
import hashlib

BEFORE = '44b17a291c3cea3946f3e0065fa78d365a7836efa521219785b94295ef7e68ee'
CHANGES = (
    ('"terminal-lander", "kitty-brokeout", "kilix-land")',
     '"terminal-lander", "kitty-brokeout", "kilix-land", "pleb-plant-grower", "tictactoe-tui")'),
    ('    "kilix-land": ["kilix land"],',
     '    "kilix-land": ["kilix land"],\n'
     '    "pleb-plant-grower": ["plant grower", "pleb plant grower", "plant-grower"],\n'
     '    "tictactoe-tui": ["tic-tac-toe", "tic tac toe", "tictactoe", "noughts and crosses"],'),
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
