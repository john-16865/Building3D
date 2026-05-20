# Science Centre Door Research

## Summary

I did not find a published semantic door/entrance feature layer in the cached Science Centre MapsIndoors location data. The useful source was the live MapsIndoors directions graph used by the University of Auckland map.

The resulting dataset is therefore route-derived:

- External building entry points come from route steps where MapsIndoors changes from outside routing to `InsideBuilding`.
- Room door/entry points come from intersecting the final route segment with the destination room polygon boundary.
- Non-routed service/non-usable records use a geometry fallback against the nearest routeable circulation, stair, or lift polygon.

## Generated Files

The full coordinate outputs are local generated artifacts:

- `exports/auckland/groups/science/science_room_door_points_route_derived.json`
- `exports/auckland/groups/science/science_room_door_points_route_derived.csv`
- `exports/auckland/groups/science/science_external_entry_points_route_derived.json`
- `exports/auckland/groups/science/science_external_entry_points_route_derived.csv`
- `exports/auckland/groups/science/science_door_research.md`

The route response cache is at:

- `exports/auckland/groups/science/door_route_cache/`

## Result Counts

- Science room/location polygons processed: `1902`
- Door points generated: `1902`
- Missing door coordinates: `0`
- High-confidence route-derived points: `1855`
- Medium-confidence points: `3`
- Low-confidence geometry/route fallbacks: `44`
- Exact polygon-boundary validation failures: `0`
- External Science entry clusters found: `6`

Door source breakdown:

| Source | Count | Meaning |
| --- | ---: | --- |
| `route_boundary_intersection` | 1854 | Final MapsIndoors route segment crossed the room polygon boundary. |
| `route_endpoint_on_boundary` | 1 | Route endpoint landed on the room boundary. |
| `nearest_boundary_to_route_endpoint` | 25 | Route worked, but did not cross the boundary cleanly; nearest boundary point was used. |
| `geometry_shared_boundary_midpoint` | 1 | No route; shared boundary with routeable geometry was used. |
| `geometry_nearest_routeable_boundary` | 21 | No route; nearest boundary to routeable/circulation geometry was used. |

## External Science Entries

These are the six clustered outside-to-inside route transition points found from the MapsIndoors `CITY_CAMPUS_Graph`.

| Entry | Longitude | Latitude | Floor | Source zLevel | Local x/z | Support | Target buildings |
| --- | ---: | ---: | --- | ---: | --- | ---: | --- |
| `science_entry_001` | `174.76818825` | `-36.852228925` | `G` | `0.0` | `-18.067520, 119.648002` | `235` | `301, 302, 303, 303S, 305` |
| `science_entry_002` | `174.76871095` | `-36.853322225` | `G` | `0.0` | `28.492160, -2.058154` | `177` | `301, 302, 303, 303S, 305` |
| `science_entry_003` | `174.768948475` | `-36.8531057` | `G` | `0.0` | `49.649781, 22.045409` | `45` | `303` |
| `science_entry_004` | `174.7694289` | `-36.8528096` | `G` | `0.0` | `92.443801, 55.007261` | `10` | `301, 302` |
| `science_entry_005` | `174.7688925` | `-36.8531897` | `G` | `0.0` | `44.663788, 12.694529` | `3` | `301, 302` |
| `science_entry_006` | `174.7687837` | `-36.8531974` | `G` | `0.0` | `34.972391, 11.837365` | `2` | `302` |

## Example Room Points

| Room | Longitude | Latitude | Source | Confidence |
| --- | ---: | ---: | --- | --- |
| `303S-175` | `174.76779296972478` | `-36.85285813027524` | `route_boundary_intersection` | `high` |
| `301-224` | `174.7683193783931` | `-36.85284432237857` | `route_boundary_intersection` | `high` |
| `301-B038` | `174.7682116` | `-36.8527918` | `geometry_nearest_routeable_boundary` | `low` |
| `303S-365` | `174.76770921383581` | `-36.85284344830611` | `geometry_nearest_routeable_boundary` | `low` |

## Research Sources

- UoA map client: `https://maps.auckland.ac.nz/auckland/fa64ffa351cb4fe680fa2929/details/9a0ab05b1d0a45fbaf33af00`
- MapsIndoors SDK used by the client: `https://app.mapsindoors.com/mapsindoors/js/sdk/nz/4.21.2/mapsindoors.js`
- Directions graph discovered by `directions/contains`: `CITY_CAMPUS_Graph`
- Graph details endpoint: `https://api.mapsindoors.com/auckland/api/directions/details/CITY_CAMPUS_Graph?lr=en`
- Route endpoint pattern: `https://api.mapsindoors.com/auckland/api/directions/CITY_CAMPUS_Graph?origin=<lat,lng,floor>&destination=<lat,lng,floor>&mode=WALKING&lr=en`

## Limitation

These are exact points from the available map geometry and routing graph, but they are not official architectural door objects. If UoA provides BIM, CAD, or a door schedule later, that should replace the route-derived fallback records.
