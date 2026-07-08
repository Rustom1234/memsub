"""Make the experiment package and the memsub library importable when running
pytest from this directory or via `pytest experiments/lying-game`."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
