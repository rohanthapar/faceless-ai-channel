"""Root conftest.py — adds project root to sys.path so `app` is importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
