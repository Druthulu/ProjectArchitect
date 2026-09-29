"""Lets `python -m unittest tests.<module>` find `helpers` like `discover -s tests` does."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
