"""Load the pinned Ralph module without initializing the terminal engine."""
import os
from pathlib import Path
import sys


def main():
    home = Path(__file__).resolve().parents[1]
    module = Path(os.environ.get("KILIX_RALPH_HOME", home / "third_party/kilix-ralph"))
    if not (module / "kilix_ralph/__main__.py").is_file():
        print("kilix ralph: module unavailable; initialize third_party/kilix-ralph "
              "or set KILIX_RALPH_HOME to a module checkout", file=sys.stderr)
        return 1
    sys.path.insert(0, str(module))
    from kilix_ralph.__main__ import main as ralph_main
    return ralph_main()


if __name__ == "__main__":
    sys.exit(main())
