"""Make runtime tests runnable from the runtime directory or repository root."""

from __future__ import annotations

import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
