# Building3D

Standalone generator for turning University of Auckland MapsIndoors building
data into GLB assets, per-building manifests, and campus-level indexes.

This repository is intentionally separate from UNIMATE. It does not write into
the UNIMATE checkout. Generated exports can be copied into UNIMATE later when
that integration is explicitly requested.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Fetch and process OGGB data:

```bash
.venv/bin/python -m building3d fetch --config configs/oggb.yaml
.venv/bin/python -m building3d process --config configs/oggb.yaml
.venv/bin/python -m building3d validate --config configs/oggb.yaml
```

Build GLB files on a machine with Blender installed:

```bash
.venv/bin/python -m building3d build --config configs/oggb.yaml
.venv/bin/python -m building3d package --config configs/oggb.yaml
```

Discover and generate every supported Auckland MapsIndoors building:

```bash
.venv/bin/python -m building3d discover --config configs/auckland.yaml
.venv/bin/python -m building3d generate-all --config configs/auckland.yaml
.venv/bin/python -m building3d catalog --config configs/auckland.yaml
```

Generate a UNIMATE-ready logical building group such as Science:

```bash
.venv/bin/python -m building3d group science --config configs/auckland.yaml --groups-config configs/auckland_building_groups.yaml
```

## Outputs

Single-building output:

```text
exports/oggb/
  oggb_visual.glb
  oggb_nav.glb
  oggb_manifest.json
  README.md
```

Campus batch output:

```text
exports/auckland/
  index.json
  summary.csv
  ../../docs/auckland-building-catalog.md
  buildings/
    260-sir-owen-g-glenn-building-oggb/
      260-sir-owen-g-glenn-building-oggb_visual.glb
      260-sir-owen-g-glenn-building-oggb_nav.glb
      260-sir-owen-g-glenn-building-oggb_manifest.json
      README.md
```

Grouped UNIMATE-ready output:

```text
exports/auckland/groups/science/
  science_visual.glb
  science_nav.glb
  science_manifest.json
  science_unimate.tscn
  README.md
```

`index.json` records every discovered building with `generated`, `skipped`, or
`failed` status, counts, artifact paths, warnings, errors, and source URLs.

Raw API cache, processed intermediates, GLBs, and debug Blender files are
ignored by default.
