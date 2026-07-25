"""Route-derived room door points for a building group.

The heavy MapsIndoors route-geometry logic lives in
``tools/derive_science_door_points.py`` (kept there so the standalone CLI and
its unit tests stay put). This module re-exports the reusable entry point under
the ``building3d`` package so the automated pipeline can import it cleanly
regardless of the current working directory.

``tools`` is a regular package (see ``tools/__init__.py``) so that this import
resolves to THIS repo's directory even when an unrelated ``tools`` package is
installed in site-packages -- without that, a regular package there wins over a
namespace one regardless of ``sys.path`` order and the whole pipeline fails to
import.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.derive_science_door_points import derive_group_door_points  # noqa: E402

__all__ = ["derive_group_door_points"]
