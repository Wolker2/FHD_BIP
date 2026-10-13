"""PyInstaller entry point.

Keeping the launcher outside the package makes both direct execution and the
frozen Windows executable use normal absolute package imports.
"""

from fhd_bip.main import main

if __name__ == "__main__":
    main()
