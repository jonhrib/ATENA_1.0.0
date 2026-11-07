"""Ponto de entrada do ATENA (usado pelo atalho e pelo PyInstaller)."""
import sys

from atena.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
