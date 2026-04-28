"""Top-level pytest configuration.

Ensures the project root is on ``sys.path`` so the ``shared``,
``orchestration``, and ``monitoring`` packages import cleanly when
pytest is invoked from anywhere inside the repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
