"""Put the eval package's own directory on ``sys.path``.

``metrics``, ``oracle`` and friends are imported by bare name both by
``run.py`` (a script) and by ``test_gates.py`` (collected by pytest), so the
directory has to be importable either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
