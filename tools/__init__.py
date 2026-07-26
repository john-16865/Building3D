"""Standalone helper CLIs that the ``building3d`` package also imports from.

This file exists to make ``tools`` a REGULAR package. Without it the directory
is only a namespace package, and Python resolves any regular ``tools`` package
installed in site-packages ahead of it no matter where this repo sits on
``sys.path`` -- which made ``from tools.derive_science_door_points import ...``
fail in ``building3d/campus_paths.py``, ``building3d/room_door_derivation.py``
and three test modules, taking the whole ``unimate`` pipeline down with it.
"""
