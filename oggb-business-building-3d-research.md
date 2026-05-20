# 3D Model Research: University of Auckland Business School / Sir Owen G Glenn Building

Generated: 2026-05-19  
Target building: Sir Owen G Glenn Building OGGB, University of Auckland Business School  
MapsIndoors building administrative ID: `260`  
MapsIndoors building ID: `b2fc3c66e2ca44a2b5c924f4`  
Venue: City Campus  
Venue ID: `fa64ffa351cb4fe680fa2929`

## Executive Summary

The University of Auckland Business School building in the campus map is the Sir Owen G Glenn Building, commonly OGGB. In the MapsIndoors dataset, it is building `260`, with full building footprint geometry, per-floor outlines, and 1,111 room polygons available through public map API responses.

The map data is enough to make a useful 3D indoor/exterior massing model:

- Accurate georeferenced building footprint.
- Accurate per-floor plan outlines.
- Accurate room polygons by floor.
- Room names, room numbers, room types, anchors, and internal floor IDs.
- Raster floor-plan tiles for every mapped OGGB floor.
- Stairs/elevators as polygons for vertical circulation.
- Parking floors and basement levels.

The deeper public-source pass adds enough information to improve the exterior model beyond a generic extrusion:

- The project team and structural role are partially recoverable: Beca is publicly named as project manager, surveyor, and structural engineer in a University of Auckland news item.
- The building form is publicly described as a `13-level tower and podium` with five car-park levels, two teaching levels, and five office/postgraduate levels.
- Facade construction is recoverable at concept/material level: double-layered facade, stainless steel/glass/aluminium panels, striped glass sunscreens, aerofoil sunscreens, conical glass walls, exposed structural-steel sunscreen fingers, and cantilevered ends up to six metres.
- LINZ 1m DSM and DEM data from 2013 can be sampled to estimate real roof/surface heights above terrain.
- OpenStreetMap contains a usable independent footprint and level tags for cross-checking, but its level count conflicts with other sources and should be treated as secondary.
- Public Level 0 and Level 1 floor-plan images exist and are useful for entrance, foyer, plaza, auditorium, and labelled teaching-room placement.

The map data is not enough by itself to make an architecturally complete 3D model:

- No original BIM/CAD/revit or construction drawings found in public sources.
- No authoritative room-by-room ceiling heights.
- No authoritative floor-to-floor heights; only indirect estimates from public text, floor count, and LINZ surface data.
- No wall heights except generic MapsIndoors 3D display-rule concepts.
- No exact roof geometry, skylight framing dimensions, facade panel schedule, or mullion layout as machine-readable data.
- No door swing geometry.
- No structural columns unless represented as map polygons.
- No complete material schedule, but public sources give enough facade/material classes for a believable model.
- No complete vertical shaft semantics beyond room type names such as `Elevator` and `Stairs`.

Important target note: the original map URL in the request resolves through MapsIndoors to location `9a0ab05b1d0a45fbaf33af00`, which is room `423-348` in building `423` (`Conference Centre`), not OGGB. This report continues to target the Business School / OGGB because that is the building requested for the 3D model. If the actual target is the linked room/building, use building `423` instead of `260`.

For a 3D implementation, use the MapsIndoors GeoJSON polygons as the 2D floor-plan source of truth, project them into local meter coordinates, triangulate them into meshes, and assign one z-level per MapsIndoors floor ID. Use raster floor tiles as optional floor textures or reference images, not as hit-test geometry.

## Official Building Context

The University describes the Sir Owen G Glenn Building as the home of the University of Auckland Business School. Official University pages state that it is situated at 12 Grafton Road, Auckland, and is on the city campus.

Useful official facts for modelling context:

- The building is at `12 Grafton Road, Auckland`.
- It is the home of the University of Auckland Business School.
- The University page says the building covers over `74,000 square metres`.
- The building contains a `26m-high atrium`.
- Teaching facilities are primarily on Level 0.
- It has five levels of car parking and more than 1,000 car parks.
- The main teaching facilities include large lecture theatres, case rooms, computer labs, and a financial trading room.

Important modelling note: the official `74,000 square metres` figure is a whole-building/facility scale statement. The areas computed from the MapsIndoors polygons below are plan-view polygon areas derived from map geometry; they should not be treated as a validated gross floor area schedule.

### Deeper Recovered Building Facts

Publicly recoverable facts that materially improve the 3D model:

| Topic | Recovered information | Modelling use |
|---|---|---|
| Architect | Francis-Jones Morehen Thorp / FJMT, with Archimedia | Use FJMT/FJC project imagery and design text as facade/form reference |
| Completion/opening period | Completed/opened around 2007-2008 depending source wording | Use 2008 public imagery and 2013 LiDAR as post-completion references |
| Structural/project team | Beca publicly named as project manager, surveyor, and structural engineer | Useful metadata; not enough for structural-detail modelling |
| Overall form | Boomerang-shaped tower, full-height atrium, expansive podium, large public square | Model as podium plus two curving/sinuous arms around atrium/plaza |
| Levels | Public UoA news describes a 13-level tower/podium: five car-park levels, two teaching levels, five office/postgraduate levels | Reconciles well with MapsIndoors floors `-5` through `7` |
| Visible/storey description | Building Today describes a distinctive six-storey stainless-steel/glass structure | Use this for above-ground visible massing, not as total internal level count |
| Occupancy capacity | Sources state around 2,500-2,600 students | Scale/circulation context only |
| Atrium | Official pages state a 26m-high atrium | Use as primary atrium height constraint |
| Cantilevers | Public UoA news says building ends are cantilevered up to six metres | Add cantilevered upper-arm ends rather than vertical block extrusion |
| Facade | Double-layered facade; stainless steel/glass/aluminium panels; striped glass sunscreens; aerofoil sunscreens; conical glass walls; exposed structural-steel sunscreen fingers | Add a light double-skin facade layer with horizontal metallic stripe/sunscreen texture |
| Facade quantity | Trends facade article gives about 12,000 sq m of facade elements, 2,500 interlocking panels, 767 panel configurations, and 182 sunscreen configurations | Use to justify varied repeating facade modules rather than one flat texture |
| Atrium glass/structure | Low-E toughened/laminated clear safety glass, metallic-strip sunscreens, large tension truss, suspended social bridges | Model atrium as transparent roof/wall system with truss rods and bridge plates |
| Main public spaces | Forecourt/open square, central atrium, Level 0 teaching podium, Level 1 foyer/plaza/cafe/auditorium link | Use public Level 0/1 floor-plan images to place entrances and public circulation |

### Recovered Height Evidence

The most useful height source found is the public LINZ Auckland LiDAR dataset:

- DSM layer: `Auckland LiDAR 1m DSM (2013)`, layer `53406`.
- DEM layer: `Auckland LiDAR 1m DEM (2013)`, layer `53405`.
- Capture period: July-November 2013.
- Resolution: 1m grid.
- Public metadata says the survey used more than 1.5 points per square metre.
- Public metadata gives vertical accuracy specification `+/-0.2m` at 95% confidence and horizontal accuracy `+/-0.6m` at 95% confidence.
- Vertical datum in the layer metadata is NZVD2009.
- License: Creative Commons Attribution 4.0 International.

I sampled the LINZ DSM minus DEM over the OpenStreetMap OGGB footprint (`way 23894203`) on a rough `0.00010` degree grid. This is not a stamped survey, but it is far stronger than a generic storey-height guess.

Sampling result:

| Metric | Surface minus ground |
|---|---:|
| Sample points inside OSM footprint | 43 |
| Valid raster samples | 41 |
| Median across all valid samples | 25.46m |
| 90th percentile across all valid samples | 31.88m |
| Maximum sampled difference | 38.16m |
| Median for points over 5m above ground | 26.25m |
| 90th percentile for points over 5m above ground | 31.88m |

Representative point samples:

| Point | Lon | Lat | DSM | DEM | DSM-DEM |
|---|---:|---:|---:|---:|---:|
| Anchor / open-atrium-floor-like point | 174.7713600 | -36.8529200 | 29.58 | 29.58 | 0.00m |
| Northwest upper slab | 174.7710500 | -36.8527500 | 52.31 | 29.70 | 22.61m |
| West slab | 174.7709500 | -36.8532000 | 46.00 | 26.52 | 19.48m |
| South/sloping-ground slab | 174.7711200 | -36.8535500 | 52.40 | 21.04 | 31.36m |
| East auditorium / podium area | 174.7718200 | -36.8529500 | 39.72 | 29.23 | 10.49m |
| Highest sampled grid point | 174.7716412 | -36.8532183 | 52.84 | 14.68 | 38.16m |

Interpretation for modelling:

- Do not use `38.16m` as a uniform extrusion height. The site slopes sharply into Grafton Gully, so a high DSM-DEM value can represent a roof/upper mass over much lower local ground.
- Use about `26m` as the atrium vertical datum because that is explicitly stated by official University pages.
- Use `30m-32m` as a plausible upper visible roof/massing target for the main occupied above-ground building relative to nearby upper ground.
- Allow localized maximum height up to about `38m` relative to the lowest nearby terrain when modelling the south/gully-facing side.
- If the model uses a flat local origin, keep a terrain mesh or stepped podium; otherwise the basement and podium geometry will look wrong.

### OpenStreetMap Cross-Check

OpenStreetMap has a mapped OGGB building footprint:

```txt
OSM way: 23894203
name: Owen G. Glenn Building
ref: 260
building: university
building:levels: 11
building:levels:underground: 4
parking: multi-storey
start_date: 2007
wikidata: Q7114483
```

Use OSM as an independent exterior footprint/metadata check, not as the primary source. Its `building:levels=11` and `building:levels:underground=4` do not exactly match the University News `13-level tower and podium` wording or MapsIndoors floors `-5` through `7`. The most defensible reconciliation is:

- MapsIndoors gives operational mapped levels: `-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7`.
- UoA News gives architectural/programmatic level grouping: five car-park, two teaching, five office/postgraduate, plus tower/podium description.
- Building Today's six-storey wording describes the visible stainless-steel/glass structure, not the whole internal floor stack including basements/podium.
- OSM is helpful, but not authoritative enough to override UoA/MapsIndoors.

### Public Plan And Image References

Public floor-plan and reference images found:

| Reference | URL | Useful for |
|---|---|---|
| Island Invasives Level 0 plan image | <https://www.islandinvasives.org/files/2025/07/OGGB-Level-0_16112018.jpg> | Lecture theatres, case rooms, Level 0 foyer, vertical circulation, Grafton Road orientation |
| Island Invasives Level 1 plan image | <https://www.islandinvasives.org/files/2025/07/OGGB-Level-1_2018.jpg> | Main entrance, Level 1 foyer, cafe area, plaza, F&P auditorium lobby, stairs to Level 0 |
| security.ac.nz Level 0 PDF | <https://security.ac.nz/OGGB_Level0.pdf> | Independent Level 0 event-room reference |
| River blog Level 0/1 references | <https://river.blogs.auckland.ac.nz/research/geomorph_workshop/> | Confirms public Level 0/1 plan images and campus-map context |
| FJC/FJMT exterior image | <https://fjcstudio.com/wp-content/uploads/2016/08/Owen-6-scaled.jpg> | Curved/ribbon exterior facade and plaza relationship |
| FJC/FJMT atrium image | <https://fjcstudio.com/wp-content/uploads/2016/08/Owen-Atrium-scaled.jpg> | Atrium glazing, truss rods, bridges, stair/void proportions |
| FJC/FJMT concept sketch | <https://fjcstudio.com/wp-content/uploads/2016/08/Owen-Sketch.jpg> | Sloping-site podium, suspended upper forms, section/massing logic |
| Island Invasives venue image set | <https://www.islandinvasives.org/venue/> | Exterior, atrium, auditorium, foyer, case-room reference photos |

Use these images as modelling references. Do not redistribute them as packaged textures unless you verify their licensing/permission.

## Source Data Overview

### Main Live APIs

Building detail:

```txt
https://api-us-east.mapsindoors.com/auckland/api/buildings/details/b2fc3c66e2ca44a2b5c924f4?v=3
```

All Auckland buildings:

```txt
https://api-us-east.mapsindoors.com/sync/buildings?solutionId=auckland&v=5
```

OGGB locations, first page:

```txt
https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=0&v=5
```

OGGB locations, second page:

```txt
https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=1000&v=5
```

City Campus venue:

```txt
https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929
```

Solution details and display types:

```txt
https://api-us-east.mapsindoors.com/api/solutions/details/auckland?v=5
```

Venue/floor tiles:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/{style}/l{floor}/z{z}/x{x}/y{y}.png
```

Building image:

```txt
https://media.mapsindoors.com/a5000c81544b4013b02f0c45/media/B260.jpg
```

### API Pagination

The locations endpoint accepts `take` and `skip`. For building `260`, `take=1000` returns two pages:

| Page | URL params | Count |
|---|---:|---:|
| 1 | `take=1000&skip=0` | 1000 |
| 2 | `take=1000&skip=1000` | 112 |
| Total |  | 1112 |

The returned 1,112 features are:

| Geometry / feature kind | Count |
|---|---:|
| Building point feature | 1 |
| Room/location polygons | 1111 |
| Polygons with holes/multiple rings | 12 |
| Features with `externalId` | 1056 |
| Features without `externalId` | 55 |

## Building Metadata

Observed MapsIndoors building record:

```json
{
  "id": "b2fc3c66e2ca44a2b5c924f4",
  "administrativeId": "260",
  "externalId": "B260 ",
  "name": "Sir Owen G Glenn Building OGGB",
  "venueId": "fa64ffa351cb4fe680fa2929",
  "defaultFloor": 0,
  "anchor": {
    "type": "Point",
    "coordinates": [174.771359951698, -36.8529245870962]
  },
  "geometry": {
    "type": "Polygon",
    "bbox": [174.7707898, -36.8537341, 174.7721448, -36.8522559]
  }
}
```

Approximate plan statistics computed by projecting lon/lat to a local meter plane around the building anchor:

| Item | Value |
|---|---:|
| Footprint area | ~11,174.6 m2 |
| Footprint perimeter | ~546.7 m |
| Footprint rings | 1 |
| Footprint points | 146 |
| Longitude span | 174.7707898 to 174.7721448 |
| Latitude span | -36.8537341 to -36.8522559 |

The projection used for these computed stats was a local equirectangular approximation. It is acceptable for planning a building-scale model, but for production-grade geospatial accuracy use a real projection library or EPSG transform.

## Floor Model

### Internal Floor IDs

MapsIndoors floor IDs are numeric strings. They are not meters and should not be used directly as z-heights.

For OGGB:

| Internal floor ID | Display name | Has floor geometry | Has location polygons |
|---:|---:|---:|---:|
| `-50` | `-5` | Yes | Yes |
| `-40` | `-4` | Yes | Yes |
| `-30` | `-3` | Yes | Yes |
| `-20` | `-2` | Yes | Yes |
| `-10` | `-1` | Yes | Yes |
| `0` | `0` | Yes | Yes |
| `10` | `1` | Yes | Yes |
| `20` | `2` | Yes | Yes |
| `30` | `3` | Yes | Yes |
| `40` | `4` | Yes | Yes |
| `50` | `5` | Yes | Yes |
| `60` | `6` | Yes | Yes |
| `70` | `7` | Yes | No location records found in `building=260` query |

This means the building detail endpoint describes 13 floor outlines, while the location endpoint returned room/location polygons on 12 of those floors. Floor `70`/display Level `7` has an outline but no room polygons in the tested location query.

### Floor Outline Geometry

Approximate floor outline stats:

| Floor ID | Display | Area m2 | Perimeter m | Points |
|---:|---:|---:|---:|---:|
| `-50` | `-5` | 5001.8 | 328.6 | 19 |
| `-40` | `-4` | 9358.6 | 395.0 | 18 |
| `-30` | `-3` | 9358.9 | 395.1 | 17 |
| `-20` | `-2` | 9142.6 | 431.4 | 36 |
| `-10` | `-1` | 9646.7 | 535.8 | 65 |
| `0` | `0` | 9845.0 | 533.2 | 115 |
| `10` | `1` | 5043.2 | 542.2 | 174 |
| `20` | `2` | 3450.5 | 400.9 | 74 |
| `30` | `3` | 3557.8 | 438.9 | 101 |
| `40` | `4` | 3476.5 | 436.9 | 146 |
| `50` | `5` | 3025.5 | 433.5 | 134 |
| `60` | `6` | 3016.3 | 433.7 | 126 |
| `70` | `7` | 619.6 | 253.9 | 67 |

These floor polygons are the best source for floor slabs, broad building massing, and clipping room meshes.

### Room Polygon Counts By Floor

| Floor ID | Display | Location count | Approx polygon area m2 | Dominant types |
|---:|---:|---:|---:|---|
| `-50` | `-5` | 12 | 5001.8 | Non Usable Area, Elevator, Stairs |
| `-40` | `-4` | 23 | 9358.6 | Non Usable Area, Stairs, Elevator |
| `-30` | `-3` | 21 | 9358.9 | Non Usable Area, Unclassified Facilities |
| `-20` | `-2` | 41 | 9142.6 | Non Usable Area, Unclassified Facilities |
| `-10` | `-1` | 92 | 9593.1 | Non Usable Area, Unclassified Facilities, Ancillary Area |
| `0` | `0` | 195 | 9370.4 | Non Usable Area, Ancillary Area, Nonasgn Rentable |
| `10` | `1` | 110 | 4185.2 | Non Usable Area, Office Accommodation, General Facility |
| `20` | `2` | 96 | 3044.8 | General Facility, Non Usable Area, Nonasgn Rentable |
| `30` | `3` | 132 | 3268.6 | Office Accommodation, General Facility, Non Usable Area |
| `40` | `4` | 146 | 3232.3 | Office Accommodation |
| `50` | `5` | 129 | 2769.4 | Office Accommodation |
| `60` | `6` | 115 | 2780.2 | Office Accommodation |

## Location / Room Type Summary

The 1,112 features in building `260` break down by type as follows:

| Type | Count |
|---|---:|
| Office Accommodation | 286 |
| Non Usable Area | 228 |
| Ancillary Area | 93 |
| General Facility | 92 |
| Nonasgn Rentable | 82 |
| Stairs | 64 |
| Elevator | 62 |
| Unclassified Facilities | 61 |
| Teaching spaces | 27 |
| Male | 24 |
| Female | 24 |
| Accessible | 16 |
| Computer labs | 16 |
| Information Serv | 13 |
| Shower | 9 |
| Lecture theatres | 5 |
| Unisex | 2 |
| Lab-Media | 2 |
| Building | 1 |
| Laboratories | 1 |
| Cafes and Restaurants | 1 |
| Bike Store | 1 |
| Gender Diverse | 1 |
| Parking For Vehicles | 1 |

For 3D modelling, the most structurally useful types are:

- `Stairs`
- `Elevator`
- `Non Usable Area`
- `Ancillary Area`
- `Parking For Vehicles`
- `Office Accommodation`
- `Teaching spaces`
- `Lecture theatres`
- `Computer labs`

These are enough to build semantic room layers, color-coded occupancy zones, clickable rooms, and vertical circulation markers.

## Important OGGB Rooms And Spaces

### Lecture Theatres

| Floor | External ID | Name | Approx area m2 |
|---:|---|---|---:|
| 0 | `260-073` | Lecture theatre | 314.3 |
| 1 | `260-115` | Fisher & Paykel Appliances Auditorium | 559.3 |
| 0 | `260-092` | Lecture theatre | 281.4 |
| 0 | `260-098` | Lecture theatre | 560.3 |
| 0 | `260-051` | Lecture theatre | 174.2 |

### Teaching Spaces

There are 27 `Teaching spaces` polygons. Examples:

| Floor | External ID | Name | Approx area m2 |
|---:|---|---|---:|
| 0 | `260-005` | Case Study | 143.1 |
| 1 | `260-154` | Tuakana | 43.4 |
| 3 | `260-319` | Seminar Room | 41.0 |
| 3 | `260-323` | Seminar Room | 45.8 |
| 2 | `260-223` | Case Study | 132.3 |
| 2 | `260-215` | Seminar Room | 125.5 |
| 0 | `260-040` | Seminar Room | 251.7 |
| 3 | `260-325` | Case Study | 129.5 |

### Computer Labs

There are 16 `Computer labs` polygons. Examples:

| Floor | External ID | Name | Approx area m2 |
|---:|---|---|---:|
| 0 | `260-008` | Computer Lab-Business&Economics | 89.6 |
| 4 | `260-477` | Computer Lab-Business&Economics | 139.0 |
| 4 | `260-4105` | Computer Lab-Business&Economics | 185.8 |
| 2 | `260-230` | Computer Lab-Business&Economics | 395.0 |
| 2 | `260-240` | Computer Lab-Business&Economics | 132.7 |
| 0 | `260-004` | Computer Lab - School Bus & Econ | 79.4 |

### Food / Cafe

| Floor | External ID | Name | Approx area m2 |
|---:|---|---|---:|
| 1 | `260-109` | The Exchange | 34.8 |

### Parking

| Floor | External ID | Name | Approx area m2 |
|---:|---|---|---:|
| -1 | `260-P151` | B260 OGGB Carpark | 4856.4 |

Important caveat: the University states the building has five levels of car parking. The map locations include a named `Parking For Vehicles` polygon on `-10`, while other basement floors include large `Non Usable Area` polygons with IDs like `260-P201`, `260-P301`, `260-P401`, and `260-P501`. For a 3D model, treat the basement parking levels as floors `-50` through `-10`, but classify only `260-P151` as explicitly typed parking unless you add your own semantic labels.

### Vertical Circulation

| Type | Count |
|---|---:|
| Elevator | 62 |
| Stairs | 64 |

These can be used to connect floors in the 3D model. The raw data gives each stair/elevator as a room polygon per floor, not as a single vertical shaft object. You will need to group them by external ID patterns and XY overlap if you want continuous vertical objects.

Examples:

```txt
260-000E1, 260-100E1, 260-200E1, ...
260-000S1, 260-100S1, 260-200S1, ...
```

Recommended grouping heuristic:

1. Group by normalized external ID suffix where possible, such as `E1`, `S1`, `E8`, `S6`.
2. For features without external IDs, group by nearest centroid and same type.
3. Validate each group visually because stair naming is not perfectly uniform.

## Raster Floor Tiles

The City Campus venue exposes this tile template:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/{style}/l{floor}/z{z}/x{x}/y{y}.png
```

For OGGB, all building floors responded with valid PNG tiles at the tested center tile and zoom `18`.

Example center tile for floor `0`:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/default/l0/z18/x258336/y159975.png
```

The tile image is a normal 256 by 256 PNG.

### Tile Ranges Covering The OGGB Footprint

The following tile ranges were computed from the OGGB building bbox:

```txt
west  = 174.7707898
south = -36.8537341
east  = 174.7721448
north = -36.8522559
```

| Zoom | x range | y range | Tile count |
|---:|---|---|---:|
| 14 | 16146-16146 | 9998-9998 | 1 |
| 15 | 32292-32292 | 19996-19997 | 2 |
| 16 | 64584-64584 | 39993-39994 | 2 |
| 17 | 129168-129168 | 79987-79988 | 2 |
| 18 | 258336-258337 | 159975-159976 | 4 |
| 19 | 516672-516674 | 319950-319952 | 9 |
| 20 | 1033344-1033348 | 639900-639905 | 30 |
| 21 | 2066689-2066697 | 1279800-1279811 | 108 |
| 22 | 4133379-4133395 | 2559601-2559623 | 391 |

Recommended tile zooms:

- Use `z18` or `z19` for quick previews.
- Use `z20` for a good balance of quality and request count.
- Use `z21` or `z22` only if you need high-resolution floor texture detail and can handle the larger tile mosaic.

For `z20`, each floor needs about 30 tiles over the OGGB bbox. Across 13 floor outlines, that is around 390 tiles, which is manageable.

## Required Data For A 3D Model

### Data Available From The Map

| Need | Available? | Source |
|---|---:|---|
| Building footprint | Yes | Building detail `geometry` |
| Per-floor outlines | Yes | Building detail `floors[*].geometry` |
| Room polygons | Yes | Locations endpoint `building=260` |
| Room IDs / names | Yes | Location `id`, `properties.name`, `properties.externalId` |
| Room types | Yes | `properties.type` |
| Floor IDs and display names | Yes | `properties.floor`, `properties.floorName`, building `floors` |
| Room label positions | Yes | `properties.anchor` |
| Campus/venue bounds | Yes | Venue detail |
| Floor-plan imagery | Yes | Venue `tilesUrl` |
| Building image | Yes | `imageURL` on building POI |
| Stairs/elevators | Yes, as per-floor polygons | Location type `Stairs`, `Elevator` |
| Parking levels | Partly | Basement floor geometry plus one typed parking polygon |

### Data Missing Or Inferred

| Need | Available? | Recommended fallback |
|---|---:|---|
| Floor-to-floor height | Inferred only | Use 3.6m to 4.2m for normal floors, then calibrate the roof cap against LINZ DSM/DEM samples and the 26m atrium statement |
| Basement height | No | Use 3.0m to 3.3m unless better data is found |
| Atrium void geometry | Partly inferable | Use `Non Usable Area`, floor holes, and floor-plan tiles; verify manually |
| Wall thickness | No | Use visual wall extraction from tiles or buffer room polygons |
| Door positions | No | Extract manually from floor tiles or ignore for first massing model |
| Window/facade geometry | Partly inferable | Use FJC/FJMT photos, Trends facade descriptions, public venue photos, and manual modelling |
| Roof structure | Partly inferable | Use FJC/FJMT atrium imagery, LINZ DSM high points, and visual reference; exact roof framing remains missing |
| Materials | Partly inferable | Use stainless steel, glass, aluminium, Low-E/tinted glass, metallic striped sunscreens, grey podium/atrium finishes |
| Terrain / sloping site | Yes, indirectly | Use LINZ DEM or a simplified stepped terrain mesh; do not place the whole building on a flat plane |
| Legal permission to redistribute | Not confirmed | Check MapsIndoors/UoA usage rights before publishing assets |

## Recommended 3D Modelling Pipeline

### 1. Fetch And Cache Data

Fetch:

- Building detail.
- All locations pages for `building=260`.
- Venue detail.
- Optional solution types.
- Optional floor tiles.

Minimal fetch commands:

```bash
curl -L 'https://api-us-east.mapsindoors.com/auckland/api/buildings/details/b2fc3c66e2ca44a2b5c924f4?v=3' \
  -o oggb-building.json

curl -L 'https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=0&v=5' \
  -o oggb-locations-0000.json

curl -L 'https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=1000&v=5' \
  -o oggb-locations-1000.json

curl -L 'https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929' \
  -o city-campus-venue.json
```

### 2. Normalize Feature Records

Create a clean local schema:

```ts
type IndoorPolygon = {
  id: string;
  source: 'mapsindoors';
  kind: 'building' | 'floor' | 'room';
  buildingId: string;
  buildingAdministrativeId: string;
  floorId?: string;
  floorName?: string;
  name?: string;
  externalId?: string | null;
  type?: string;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  anchor?: GeoJSON.Point;
  status?: number;
};
```

### 3. Project Coordinates Into Local Meters

Source coordinates are `[longitude, latitude]`.

For building-scale modelling:

```ts
function lonLatToMeters(lon: number, lat: number, originLon: number, originLat: number) {
  const earthRadius = 6378137;
  const degToRad = Math.PI / 180;

  const x = earthRadius * (lon - originLon) * degToRad * Math.cos(originLat * degToRad);
  const y = earthRadius * (lat - originLat) * degToRad;

  return { x, y };
}
```

Recommended origin:

```txt
origin lon = 174.771359951698
origin lat = -36.8529245870962
```

This is the OGGB building anchor.

### 4. Build Floors

For each `building.floors[floorId]`:

1. Project the floor polygon to local meters.
2. Triangulate the polygon.
3. Put it at a z-height assigned from `floorId`.
4. Add a thin slab thickness.
5. Store `floorId` and `floorName` as metadata.

Do not use floor ID directly as meters. Suggested first-pass z mapping:

```ts
const floorOrder = [-50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50, 60, 70];

const zByFloor = {
  '-50': -15.0,
  '-40': -12.0,
  '-30': -9.0,
  '-20': -6.0,
  '-10': -3.0,
  '0': 0.0,
  '10': 4.2,
  '20': 8.4,
  '30': 12.6,
  '40': 16.8,
  '50': 21.0,
  '60': 25.2,
  '70': 29.4
};
```

These are modelling defaults, not authoritative heights. The deeper pass suggests calibrating the above-ground stack so the atrium is about `26m` high and the main visible upper mass lands around `30m-32m` above nearby upper ground, with the sloping south/gully side allowed to read as high as about `38m` above the lowest adjacent terrain.

### 5. Build Room Meshes

For each location polygon:

1. Skip the one building point feature.
2. Keep only `geometry.type === "Polygon"` or `MultiPolygon`.
3. Project rings into local meters.
4. Triangulate.
5. Place on the z-height for `properties.floor`.
6. Assign material by `properties.type`.
7. Attach metadata: `id`, `externalId`, `name`, `floor`, `floorName`, `type`.

Recommended visual materials:

| Type group | Material |
|---|---|
| Lecture theatres / Teaching spaces / Computer labs | Blue |
| Office Accommodation | Neutral gray |
| Stairs | Yellow/orange |
| Elevator | Purple |
| Toilets / showers / accessible | Green |
| Cafes and Restaurants | Warm red |
| Parking | Dark gray |
| Non Usable Area | Low-opacity dark or hidden |
| Ancillary / General / Unclassified | Light neutral |

### 6. Build Vertical Circulation

Stairs/elevators are separate polygons on each floor. To make actual vertical objects:

1. Compute centroid for each stair/elevator polygon.
2. Group same-type polygons by nearest XY centroid across adjacent floors.
3. Use external IDs where available.
4. Create a vertical shaft mesh spanning the group z-range.
5. Keep individual per-floor polygons for interaction.

### 7. Add Floor Textures

For each floor:

1. Compute tile range for desired zoom, e.g. `z20`.
2. Download tiles using venue `tilesUrl`.
3. Stitch tiles into a mosaic.
4. Convert tile bounds to local meters.
5. Use the stitched image as a floor texture or transparent overlay.

Floor tile URL example:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/default/l0/z20/x1033344/y639900.png
```

Tile coordinate formula:

```ts
function lonLatToTile(lon: number, lat: number, z: number) {
  const latRad = lat * Math.PI / 180;
  const n = 2 ** z;

  return {
    x: Math.floor((lon + 180) / 360 * n),
    y: Math.floor(
      (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n
    )
  };
}
```

### 8. Add Exterior Massing

The map footprint can generate a simple exterior shell:

1. Use building footprint polygon.
2. Extrude from basement depth to roof height.
3. Clip/subtract or visually differentiate floor slabs.
4. Add approximate atrium void manually.
5. Add facade detail from reference imagery, not MapsIndoors geometry.

The map data does not contain enough facade or roof information for a high-fidelity architectural model.

## Suggested File Outputs For Your Project

Recommended local data outputs:

```txt
data/oggb/source/building.json
data/oggb/source/locations-page-0000.json
data/oggb/source/locations-page-1000.json
data/oggb/source/venue.json
data/oggb/processed/floors.geojson
data/oggb/processed/rooms.geojson
data/oggb/processed/vertical-circulation.geojson
data/oggb/processed/model-manifest.json
assets/oggb/tiles/{floor}/{z}/{x}-{y}.png
```

Recommended 3D manifest:

```json
{
  "building": {
    "id": "b2fc3c66e2ca44a2b5c924f4",
    "administrativeId": "260",
    "name": "Sir Owen G Glenn Building OGGB",
    "origin": [174.771359951698, -36.8529245870962]
  },
  "floors": [
    { "floorId": "-50", "floorName": "-5", "z": -15.0 },
    { "floorId": "-40", "floorName": "-4", "z": -12.0 },
    { "floorId": "-30", "floorName": "-3", "z": -9.0 },
    { "floorId": "-20", "floorName": "-2", "z": -6.0 },
    { "floorId": "-10", "floorName": "-1", "z": -3.0 },
    { "floorId": "0", "floorName": "0", "z": 0.0 },
    { "floorId": "10", "floorName": "1", "z": 4.2 },
    { "floorId": "20", "floorName": "2", "z": 8.4 },
    { "floorId": "30", "floorName": "3", "z": 12.6 },
    { "floorId": "40", "floorName": "4", "z": 16.8 },
    { "floorId": "50", "floorName": "5", "z": 21.0 },
    { "floorId": "60", "floorName": "6", "z": 25.2 },
    { "floorId": "70", "floorName": "7", "z": 29.4 }
  ]
}
```

## Validation Checklist

Before generating final meshes:

- Confirm all 1,112 building locations are fetched.
- Confirm all 13 floor outlines are present.
- Confirm all source coordinates are `[lon, lat]`, not `[lat, lon]`.
- Confirm polygons are closed; close them if the first and last coordinate differ.
- Preserve holes for the 12 multi-ring polygons.
- Confirm floor `70` has outline but no room polygons.
- Confirm basement floors use negative floor IDs.
- Confirm `floorId` and display `floorName` are both kept.
- Confirm tile mosaics line up with projected floor geometry.
- Confirm room mesh metadata includes `externalId` for lookup.
- Treat official building area and map-derived polygon areas as different measurements.
- Confirm the source target: the original supplied map URL points to building `423`, while this report targets OGGB building `260`.
- Confirm the exterior footprint source: MapsIndoors for indoor/floor geometry, OSM way `23894203` only as a cross-check.
- Confirm LINZ DSM/DEM sampling is documented if using terrain-calibrated heights.
- Keep the terrain slope or podium stepping; otherwise the recovered height estimates will not visually align.
- Check usage rights before redistributing cached map tiles or extracted geometry.

## Recommended Next Build Step

The next practical step is to create a data extraction script that writes:

1. `building.json`
2. `rooms.geojson`
3. `floors.geojson`
4. `model-manifest.json`
5. Optional stitched floor tile mosaics

After that, build a simple viewer:

1. Render floor slabs only.
2. Add room polygons on one floor.
3. Add floor switching.
4. Add stairs/elevators as vertical columns.
5. Add room click/select metadata.
6. Add raster tile texture overlay.
7. Add exterior shell.

This order keeps the work testable and prevents spending time on facade detail before the geometry pipeline is correct.

## Sources

- Subject map app: <https://maps.auckland.ac.nz/auckland/fa64ffa351cb4fe680fa2929/details/9a0ab05b1d0a45fbaf33af00>
- OGGB building detail API: <https://api-us-east.mapsindoors.com/auckland/api/buildings/details/b2fc3c66e2ca44a2b5c924f4?v=3>
- OGGB locations API page 1: <https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=0&v=5>
- OGGB locations API page 2: <https://api-us-east.mapsindoors.com/auckland/api/locations?building=260&take=1000&skip=1000&v=5>
- City Campus venue API: <https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929>
- Auckland buildings sync API: <https://api-us-east.mapsindoors.com/sync/buildings?solutionId=auckland&v=5>
- Auckland solution details API: <https://api-us-east.mapsindoors.com/api/solutions/details/auckland?v=5>
- University of Auckland OGGB facilities page: <https://www.auckland.ac.nz/en/business/current-students/facilities-resources/facilities-oggb.html>
- University of Auckland Sir Owen G Glenn Building overview: <https://www.auckland.ac.nz/en/business/about-business-school/our-faculty/oggb.html>
- University of Auckland Business School maps/location page: <https://www.auckland.ac.nz/en/business/about-business-school/our-faculty/maps-and-location.html>
- University of Auckland News, 12 June 2009, OGGB ACENZ finalist note: <https://www.auckland.ac.nz/assets/about-us/about-the-university/the-university/official-publications/uninews/2009/uoanews-issue10-2009.pdf>
- University of Auckland Estate Strategy 2030 PDF: <https://www.auckland.ac.nz/content/dam/uoa/auckland/about-us/about-the-university/the-university/official-publications/estate-strategy/te-rautaki-tuapapa-uoa-estate-strategy-2030.pdf>
- FJC/FJMT Owen G. Glenn Business School project page: <https://fjcstudio.com/projects/owen-g-glenn-business-school/>
- Trends Business Class article: <https://trendsideas.com/stories/business-class>
- Trends facade article: <https://trendsideas.com/stories/big-wide-world-1>
- Building Today 10-year article: <https://buildingtoday.co.nz/2018/10/11/landmark-auckland-university-business-school-building-turns-10/>
- Island Invasives venue page with OGGB images and floor plans: <https://www.islandinvasives.org/venue/>
- security.ac.nz Level 0 floor-plan PDF: <https://security.ac.nz/OGGB_Level0.pdf>
- River blog workshop page with Level 0/1 OGGB plan references: <https://river.blogs.auckland.ac.nz/research/geomorph_workshop/>
- OpenStreetMap API / Overpass data for way `23894203`: <https://www.openstreetmap.org/way/23894203>
- Wikidata entity for Owen G. Glenn Building: <https://www.wikidata.org/wiki/Q7114483>
- LINZ Auckland LiDAR 1m DSM (2013), layer `53406`: <https://data.linz.govt.nz/layer/53406-auckland-lidar-1m-dsm-2013/>
- LINZ Auckland LiDAR 1m DEM (2013), layer `53405`: <https://data.linz.govt.nz/layer/53405-auckland-lidar-1m-dem-2013/>
- MapsIndoors Google Maps display tutorial: <https://docs.mapsindoors.com/sdks-and-frameworks/web/tutorial/using-google-maps/display-a-map>
- MapsIndoors search documentation: <https://docs.mapsindoors.com/sdks-and-frameworks/web/search/searching>
- MapsIndoors display rules documentation: <https://docs.mapsindoors.com/sdks-and-frameworks/web/display-rules-in-practice>
- MapsIndoors 3D maps documentation: <https://docs.mapsindoors.com/sdks-and-frameworks/web/map-visualization/3d-maps>
- MapsIndoors 3D map management documentation: <https://docs.mapsindoors.com/sdks-and-frameworks/web/map-visualization/3d-maps/managing-your-3d-maps>
- Google Maps Data layer documentation: <https://developers.google.com/maps/documentation/javascript/datalayer>
- Google Maps map types / tile overlay documentation: <https://developers.google.com/maps/documentation/javascript/maptypes>
