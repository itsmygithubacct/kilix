"""Translate validated shared choices into native kitty options on each load."""
from pathlib import Path
import sys


def main():
    # Resolve the managed symlink as well as a direct source-tree include.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from kilix_sdk import settings
    finally:
        sys.path.pop(0)
    text, _exists = settings.read_text()
    edge = settings.parse_text(text).get(settings.TAB_BAR_EDGE_KEY, "").lower()
    if edge in settings.TAB_BAR_EDGE_CHOICES:
        print(f"tab_bar_edge {edge}")


if __name__ == "__main__":
    main()
