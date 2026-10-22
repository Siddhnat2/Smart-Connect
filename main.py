"""Launcher: python main.py   (the same app is used by the client and the technician)"""
import sys
from rsupport.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
