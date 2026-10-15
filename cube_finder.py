"""Cube Finder launcher: python cube_finder.py [options] (see README.md, or --help).

The program lives in the cubefinder/ package; this file only starts it, so shortcuts, Cube Finder.bat and
cube_finder.sh keep working.
"""
from cubefinder.main import main

if __name__ == "__main__":
    main()
