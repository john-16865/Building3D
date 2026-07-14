#!/usr/bin/env python3
"""Thin wrapper around ``python -m building3d unimate <group>``.

Kept for symmetry with the other ``tools/`` entry points. All real work lives in
``building3d.unimate_pipeline.build_unimate_building``.
"""
from __future__ import annotations

import sys

from building3d.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["unimate", *sys.argv[1:]]))
