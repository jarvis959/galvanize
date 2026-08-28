"""Allow ``python -m galvanize`` as an alias for the CLI entry point."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
