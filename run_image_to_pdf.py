"""PyInstaller entry point (a plain script, so relative imports resolve)."""

import sys

from image_to_pdf.app import main

if __name__ == "__main__":
    sys.exit(main())
