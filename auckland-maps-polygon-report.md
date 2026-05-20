# University of Auckland Maps Polygon Rendering Report

Generated: 2026-05-19  
Subject URL: <https://maps.auckland.ac.nz/auckland/fa64ffa351cb4fe680fa2929/details/9a0ab05b1d0a45fbaf33af00>

## Executive Summary

The University of Auckland maps site is a customized MapsIndoors web application running on Google Maps. It does not draw room, building, or floor polygons manually in Auckland-specific code. Instead, it relies on the MapsIndoors JavaScript SDK to fetch authoritative venue, building, floor, and room geometry from MapsIndoors APIs, then renders that geometry as GeoJSON overlays on top of Google Maps.

The visible indoor floor plan is a separate raster tile layer. Rooms and buildings are interactive vector polygons. The practical model is:

1. Google Maps provides the base map.
2. MapsIndoors adds floor-plan raster tiles with a URL template containing floor, zoom, x, and y placeholders.
3. MapsIndoors fetches GeoJSON polygons for venues, buildings, floors, and locations.
4. The SDK converts those GeoJSON features into Google Maps Data layer features.
5. Auckland-specific Angular code customizes styling, selection, venue behavior, floor switching, and detail routing.

For a 3D building project, the most useful takeaway is to treat the GeoJSON polygons as the source of truth for selectable geometry and use floor-plan tiles or images only as visual reference.

## Evidence Collected

The page is an Angular single-page app titled "MapsIndoors". Its HTML loads:

- `main-es2015.7836ecdaeb4fb60c8d57.js`
- `main-es2015.7836ecdaeb4fb60c8d57.js.map`
- MapsIndoors SDK: `https://app.mapsindoors.com/mapsindoors/js/sdk/nz/4.21.2/mapsindoors.js?apikey=auckland`

The production source map exposes original app source files, including:

- `src/environments/environment.prod.ts`
- `src/app/services/solution.service.ts`
- `src/app/services/google-map.service.ts`
- `src/app/services/maps-indoors.service.ts`
- `src/app/services/location.service.ts`
- `src/app/services/venue.service.ts`
- `src/app/map/map.component.ts`
- `src/app/building/building.component.ts`
- `src/app/room/room.component.ts`

Runtime network inspection showed the important geometry and tile calls:

- `GET https://api-us-east.mapsindoors.com/auckland/api/locations/details/9a0ab05b1d0a45fbaf33af00?v=5`
- `GET https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929`
- `GET https://api-us-east.mapsindoors.com/sync/buildings?solutionId=auckland&v=5`
- `GET https://api-us-east.mapsindoors.com/sync/venues?solutionId=auckland&v=5`
- `GET https://api-us-east.mapsindoors.com/sync/categories?solutionId=auckland&v=5`
- `GET https://api-us-east.mapsindoors.com/sync/derivedgeometry?solutionId=auckland&v=5`
- `GET https://api-us-east.mapsindoors.com/api/solutions/details/auckland?v=5`
- `GET https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/default/l0/z14/x16146/y9998.png`

## System Architecture

### 1. Base Map

The app creates a Google Maps view through the MapsIndoors SDK:

```ts
this.googleMapView = new mapsindoors.mapView.GoogleMapsView({
    element: document.getElementById('gmap'),
    zoom: 17,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
    clickableIcons: false,
    mapId: mapId
});

this.map = this.googleMapView.getMap();
```

This means the University page is not using a custom canvas renderer for the main map. It uses Google Maps as the map engine and MapsIndoors as the indoor-map overlay/data layer.

### 2. MapsIndoors Initialization

The app inserts the MapsIndoors SDK dynamically:

```ts
this.miSdkApiTag = document.createElement('script');
this.miSdkApiTag.setAttribute('src', `${environment.sdkUrl}?apikey=${solutionId}`);
document.body.appendChild(this.miSdkApiTag);
```

For production, the SDK URL is:

```txt
https://app.mapsindoors.com/mapsindoors/js/sdk/nz/4.21.2/mapsindoors.js
```

The solution alias is:

```txt
auckland
```

### 3. Indoor Map Instance

The app creates the MapsIndoors instance with a Google map view and custom display options:

```ts
this.mapsIndoors = new mapsindoors.MapsIndoors({
    mapView: this.googleMapService.googleMapView,
    buildingOutlineOptions: {
        strokeColor: '#009AC7',
        visible: true,
        strokeWeight: 4,
        strokeOpacity: 1
    },
    labelOptions: {
        style: {
            fontFamily: 'Open Sans, Helvetica, sans-serif',
            fontSize: '12px',
            fontWeight: 700,
            color: '#343941',
            strokeWeight: '0px',
            shadowBlur: '0px'
        }
    }
});
```

After initialization, the app hides generic building and venue map elements:

```ts
this.mapsIndoors.setDisplayRule(['MI_BUILDING', 'MI_VENUE'], { visible: false });
```

It also adjusts display rules for each MapsIndoors location type:

```ts
this.mapsIndoors.setDisplayRule(type.name, {
    title: '{{name}}',
    polygonStrokeOpacity: 0
});
```

This is why the map can show clean floor tiles while using separate polygon overlays for selection and interaction.

## Geometry Sources

### Venue Boundary

The selected venue in the URL is:

```txt
fa64ffa351cb4fe680fa2929
```

The venue detail endpoint returns a GeoJSON polygon for City Campus:

```txt
https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929
```

Important fields:

```json
{
  "id": "fa64ffa351cb4fe680fa2929",
  "name": "CITY_CAMPUS",
  "venueInfo": {
    "name": "City Campus",
    "language": "en"
  },
  "defaultFloor": "0",
  "geometry": {
    "type": "Polygon",
    "bbox": [174.7629484, -36.8588328, 174.778417, -36.8462694]
  },
  "tilesUrl": "https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/{style}/l{floor}/z{z}/x{x}/y{y}.png"
}
```

The venue has both:

- A GeoJSON polygon boundary.
- A raster tile URL template for floor-plan imagery.

### Floor Tiles

The venue `tilesUrl` is:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/{style}/l{floor}/z{z}/x{x}/y{y}.png
```

At runtime, the browser requested concrete tiles such as:

```txt
https://tiles.mapsindoors.com/tiles/indoor/Liveli/uoa/collected/202508/default/l0/z14/x16146/y9998.png
```

This proves that the visible floor plan is a tiled raster overlay. It is not a single SVG and not a set of manually drawn DOM polygons.

The MapsIndoors SDK adds this tile layer to Google Maps with an `ImageMapType` equivalent:

```js
new google.maps.ImageMapType({
    getTileUrl: function(tileCoord, zoom) {
        return template
            .replace("{z}", zoom)
            .replace("{x}", tileCoord.x)
            .replace("{y}", tileCoord.y);
    },
    tileSize: new google.maps.Size(256, 256),
    name: "MapsIndoorsTiles"
});
```

The SDK then pushes that image map type into:

```js
map.overlayMapTypes
```

### Building Geometry

Building data is available from the sync endpoint:

```txt
https://api-us-east.mapsindoors.com/sync/buildings?solutionId=auckland&v=5
```

The selected room is in building `423`, the Conference Centre. The building detail endpoint is:

```txt
https://api-us-east.mapsindoors.com/auckland/api/buildings/details/cc1bef4f845c43fe994bf846?v=3
```

Observed building fields:

```json
{
  "id": "cc1bef4f845c43fe994bf846",
  "administrativeId": "423",
  "externalId": "B423 ",
  "venueId": "fa64ffa351cb4fe680fa2929",
  "buildingInfo": {
    "name": "Conference Centre",
    "language": "en"
  },
  "defaultFloor": 0,
  "geometry": {
    "type": "Polygon",
    "bbox": [174.7690687, -36.8540496, 174.7696897, -36.85343]
  },
  "floors": {
    "0": {
      "name": "3",
      "geometry": {
        "type": "Polygon"
      }
    },
    "-10": {
      "name": "2",
      "geometry": {
        "type": "Polygon"
      }
    },
    "-20": {
      "name": "1",
      "geometry": {
        "type": "Polygon"
      }
    }
  }
}
```

The sync payload can contain floor geometry as `MultiPolygon`; the detail endpoint can return normalized `Polygon` floor geometry. In both cases, the geometry is GeoJSON-like and uses longitude/latitude coordinate pairs.

Important detail: internal floor IDs are not always the same as the visible floor labels. In this building:

```txt
internal floor 0   -> display floor 3
internal floor -10 -> display floor 2
internal floor -20 -> display floor 1
```

The selected room has:

```json
{
  "floor": "0",
  "floorName": "3"
}
```

So any clone or importer must preserve both the internal floor index and the display floor name.

### Room Geometry

The selected room/location ID is:

```txt
9a0ab05b1d0a45fbaf33af00
```

Its endpoint is:

```txt
https://api-us-east.mapsindoors.com/auckland/api/locations/details/9a0ab05b1d0a45fbaf33af00?v=5
```

Observed response shape:

```json
{
  "id": "9a0ab05b1d0a45fbaf33af00",
  "type": "Feature",
  "geometry": {
    "type": "Polygon",
    "bbox": [174.7693353, -36.853831, 174.7694989, -36.8536979],
    "coordinates": [
      [
        [174.7694664, -36.8537023],
        [174.7694598, -36.8536979],
        [174.7693539, -36.8537062],
        [174.7693353, -36.8537241],
        [174.7693439, -36.8538047],
        [174.7693516, -36.8538098],
        [174.7693363, -36.8538245],
        [174.769346, -36.853831],
        [174.7694731, -36.853821],
        [174.7694989, -36.8537962],
        [174.7694906, -36.8537184],
        [174.7694664, -36.8537023]
      ]
    ]
  },
  "properties": {
    "name": "Lecture theatre",
    "floor": "0",
    "floorName": "3",
    "building": "423",
    "venue": "CITY_CAMPUS",
    "type": "Lecture theatres",
    "locationType": "room",
    "anchor": {
      "type": "Point",
      "coordinates": [174.7694156, -36.8537641]
    },
    "externalId": "423-348"
  }
}
```

This is the exact polygon used for the room in the supplied URL. It is a standard GeoJSON-style polygon with coordinates in `[longitude, latitude]` order.

## How Polygons Are Drawn

### Data Fetching

The MapsIndoors SDK fetches:

- App config.
- Solution details.
- Venue records.
- Building records.
- Category records.
- Selected location details.
- Building/floor geometry.

Room lookup in Auckland app code goes through:

```ts
mapsindoors.services.LocationsService.getLocation(locationId)
```

Search/list lookup goes through:

```ts
mapsindoors.services.LocationsService.getLocations(parameters)
```

Venue lookup goes through:

```ts
mapsindoors.services.VenuesService.getVenues()
mapsindoors.services.VenuesService.getVenue(venueId)
mapsindoors.services.VenuesService.getBuilding(buildingId)
```

The MapsIndoors SDK internally combines Client API location data with its local synced geometry/search index.

### Vector Feature Creation

The SDK builds display features from the location/building GeoJSON. For polygons, the SDK creates features with:

- Original location ID.
- Geometry.
- Feature class.
- Sort key or z-index.
- Fill color.
- Fill opacity.
- Stroke color.
- Stroke opacity.
- Stroke width.
- Clickability.

The relevant observed SDK behavior is equivalent to:

```js
{
  type: "Feature",
  id: `Polygon.${location.id}`,
  geometry: location.geometry,
  properties: {
    clickable: displayRule.clickable !== false,
    originalId: location.id,
    featureClass: "POLYGON",
    sortKey: location.properties.zIndex || 0,
    type: location.properties.type,
    fillColor: displayRule.polygonFillColor,
    fillOpacity: displayRule.polygonFillOpacity,
    strokeColor: displayRule.polygonStrokeColor || displayRule.polygonFillColor || "#000",
    strokeOpacity: displayRule.polygonStrokeOpacity,
    strokeWidth: displayRule.polygonStrokeWeight
  }
}
```

### Google Maps Rendering

For Google Maps, the MapsIndoors SDK uses the Google Maps Data layer:

```js
map.data.addGeoJson(feature);
map.data.setStyle(styleFunction);
```

The style function maps feature properties to Google Maps style values:

```js
{
  fillColor: featureFillColor,
  fillOpacity: featureFillOpacity,
  strokeColor: featureStrokeColor,
  strokeOpacity: featureStrokeOpacity,
  strokeWeight: featureStrokeWidth,
  clickable: featureClickable,
  zIndex: featureZIndex
}
```

Labels are handled separately. The SDK creates custom label overlays rather than relying only on polygon labels.

### Raster Tile Rendering

The floor tiles are not part of the Data layer. They are added as a map overlay type:

```js
map.overlayMapTypes.push(new google.maps.ImageMapType(...));
```

When the floor changes, MapsIndoors changes the tile URL from, for example:

```txt
l0
```

to another floor path such as:

```txt
l-10
```

depending on the internal MapsIndoors floor ID.

## How Selection Highlighting Works

Auckland's app has custom room highlight behavior in `LocationService`.

Highlight settings come from environment values:

```ts
polygonStrokeColour: '#009AC7'
polygonFillColour: '#00A9DB'
```

The service creates these highlight options:

```ts
private polygonHighlightOptions = {
    strokeColor: environment.polygonStrokeColour,
    strokeOpacity: 1,
    strokeWeight: 2,
    fillColor: environment.polygonFillColour,
    fillOpacity: 0.3
};
```

When a location is selected:

```ts
if (formattedLocation.geometry.type.toLowerCase() === 'polygon') {
    this.highlightLocationPolygon(formattedLocation.id);
}
```

The highlight is applied through `setDisplayRule`:

```ts
this.mapsIndoorsService.mapsIndoors.setDisplayRule(locationId, {
    visible: true,
    zoomFrom: 0,
    zoomTo: 22,
    zIndex: 1000,
    polygonFillColor: this.polygonHighlightOptions.fillColor,
    polygonFillOpacity: this.polygonHighlightOptions.fillOpacity,
    polygonStrokeColor: this.polygonHighlightOptions.strokeColor,
    polygonStrokeOpacity: this.polygonHighlightOptions.strokeOpacity,
    polygonStrokeWeight: this.polygonHighlightOptions.strokeWeight,
    polygonVisible: true
});
```

To clear the highlight:

```ts
this.mapsIndoorsService.mapsIndoors.setDisplayRule(this.highlightedLocationId, null);
```

This means the original geometry stays the same. Highlighting is only a display-rule override.

## How Floor Switching Works

The app exposes a MapsIndoors floor selector:

```ts
new mapsindoors.FloorSelector(floorSelectorDiv, this.mapsIndoors);
```

Changing floors goes through:

```ts
this.mapsIndoors.setFloor(floor);
```

When a room is selected, the app automatically switches to the room's internal floor:

```ts
this.mapsIndoorsService.setFloor(formattedLocation.properties.floor);
```

The app listens for `floor_changed` events. If the current floor no longer matches the selected room's floor, it hides the info window and clears the polygon highlight. If the floor matches again, it reopens the info window and reapplies the highlight.

```ts
this.mapsIndoorsService.mapsIndoors.addListener('floor_changed', () => {
    const locationFloor = this.location.properties.floor;

    if (locationFloor !== this.mapsIndoorsService.mapsIndoors.getFloor()) {
        this.googleMapService.closeInfoWindow();
        this.locationService.clearLocationPolygonHighlight();
    } else {
        this.googleMapService.openInfoWindow();
        this.locationService.highlightLocationPolygon(this.location.id);
    }
});
```

This is important for 3D reconstruction: a room polygon should only be active/selectable on the matching floor.

## Coordinate Model

All observed polygons use longitude/latitude coordinates:

```txt
[longitude, latitude]
```

Example:

```json
[174.7694664, -36.8537023]
```

For Google Maps APIs, coordinates are converted into:

```ts
new google.maps.LatLng(latitude, longitude)
```

For a 3D engine, convert lon/lat to a local projected coordinate system before triangulation or extrusion. Do not treat longitude and latitude as meter-scale x/y values directly.

Recommended conversion pipeline:

1. Pick an origin near the building, such as the venue or building anchor.
2. Convert each `[lon, lat]` point to local meters using Web Mercator, UTM, or another local projection.
3. Normalize coordinates so the origin is `(0, 0)`.
4. Preserve source IDs and floor IDs.
5. Triangulate room/floor polygons.
6. Extrude or place them at floor-specific heights.

## Data Model Summary

### Venue

Represents a campus or major site.

Key fields:

- `id`
- `name`
- `venueInfo.name`
- `defaultFloor`
- `geometry`
- `tilesUrl`
- `floorNames`
- `locationBounds`

Use venue geometry for campus-level bounds and map fitting.

### Building

Represents a physical building.

Key fields:

- `id`
- `administrativeId`
- `externalId`
- `venueId`
- `buildingInfo.name`
- `defaultFloor`
- `geometry`
- `floors`

Use building geometry for footprint and per-floor geometry for floor slabs or floor-level outlines.

### Floor

Nested under building records.

Key fields:

- floor key, such as `0`, `-10`, `30`
- `name`, such as `3`, `2`, `1`
- `geometry`

Use the floor key as the internal logic key. Use `name` only for display.

### Location / Room

Represents a room, POI, service, or area.

Key fields:

- `id`
- `geometry`
- `properties.name`
- `properties.floor`
- `properties.floorName`
- `properties.building`
- `properties.venue`
- `properties.locationType`
- `properties.anchor`
- `properties.externalId`

Use room/location geometry for interaction, selection, room footprint, and label placement.

## Recreating This Approach

### If Building A 2D Map

Use this layer stack:

1. Base map.
2. Floor raster tile layer.
3. Building/floor polygon layer.
4. Room polygon layer.
5. Label/icon layer.
6. Selection/highlight layer.

Recommended implementation:

- Store room and building polygons as GeoJSON.
- Render raster floor tiles as a tile layer.
- Render polygons as vector features.
- Style by type, state, and floor.
- Use a selected-room display override rather than mutating source geometry.

### If Building A 3D Map

Use this layer stack:

1. Convert GeoJSON room/floor/building rings into local meter coordinates.
2. Generate floor slabs from building floor geometry.
3. Generate room meshes from room polygons.
4. Use floor index to assign vertical height.
5. Use tile imagery as optional floor texture/reference.
6. Keep room IDs attached to meshes for hit testing.

Recommended 3D data structure:

```ts
type IndoorFeature = {
  id: string;
  kind: 'venue' | 'building' | 'floor' | 'room';
  venueId: string;
  buildingId?: string;
  floorId?: string;
  displayFloorName?: string;
  externalId?: string;
  name?: string;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  anchor?: GeoJSON.Point;
};
```

Recommended mesh pipeline:

```txt
GeoJSON Polygon
  -> project lon/lat to local meters
  -> clean rings
  -> detect holes
  -> triangulate
  -> create mesh
  -> assign material
  -> attach metadata
```

## Important Implementation Notes

1. Do not use the floor tiles as the geometry source.

   The tiles are images. They are good for visual detail but poor for hit testing, editing, room selection, or 3D extrusion.

2. Preserve MapsIndoors floor IDs.

   The visible floor label can differ from the internal floor key. Building 423 is a clear example: internal floor `0` is display floor `3`.

3. Preserve room anchors.

   Room polygons may be irregular. The `properties.anchor` point is useful for labels, info windows, route endpoints, and camera targeting.

4. Use `bbox` for fast filtering.

   Every observed major geometry record includes a bounding box. Use it for map fitting, viewport filtering, and culling before expensive polygon checks.

5. Keep vector and raster layers independent.

   Auckland's map works because it does not rely on raster tiles for interactivity. It uses vector GeoJSON overlays for clickable geometry.

6. Treat display rules as render state.

   Highlighting is applied through display-rule overrides. The underlying room geometry is not modified.

## Minimal Pseudocode

### Fetch Room

```ts
async function fetchRoom(locationId: string) {
  const url = `https://api-us-east.mapsindoors.com/auckland/api/locations/details/${locationId}?v=5`;
  const room = await fetch(url).then(r => r.json());

  return {
    id: room.id,
    name: room.properties.name,
    floorId: room.properties.floor,
    floorName: room.properties.floorName,
    buildingId: room.properties.building,
    venueId: room.properties.venue,
    geometry: room.geometry,
    anchor: room.properties.anchor
  };
}
```

### Fetch Building And Floors

```ts
async function fetchBuilding(buildingGuid: string) {
  const url = `https://api-us-east.mapsindoors.com/auckland/api/buildings/details/${buildingGuid}?v=3`;
  const building = await fetch(url).then(r => r.json());

  return {
    id: building.id,
    administrativeId: building.administrativeId,
    name: building.buildingInfo?.name,
    venueId: building.venueId,
    footprint: building.geometry,
    floors: Object.entries(building.floors).map(([floorId, floor]) => ({
      floorId,
      floorName: floor.name,
      geometry: floor.geometry
    }))
  };
}
```

### Convert Lon/Lat To Local Coordinates

```ts
function lonLatToMeters(lon: number, lat: number, originLon: number, originLat: number) {
  const earthRadius = 6378137;
  const degToRad = Math.PI / 180;

  const x = earthRadius * (lon - originLon) * degToRad * Math.cos(originLat * degToRad);
  const y = earthRadius * (lat - originLat) * degToRad;

  return { x, y };
}
```

This is an equirectangular local approximation. It is usually acceptable for a single building or small campus area. For higher accuracy, use a proper projection library.

### Render Room Polygon In A 2D Engine

```ts
function renderRoom(ctx, room, project) {
  const ring = room.geometry.coordinates[0];
  ctx.beginPath();

  ring.forEach(([lon, lat], index) => {
    const p = project(lon, lat);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });

  ctx.closePath();
  ctx.fillStyle = 'rgba(0, 169, 219, 0.3)';
  ctx.strokeStyle = '#009AC7';
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
}
```

## Conclusion

The Auckland maps site uses MapsIndoors as the indoor map data and rendering layer. The drawing approach is hybrid:

- Raster floor tiles draw the visual floor plan.
- GeoJSON polygons define venues, buildings, floors, and rooms.
- Google Maps Data layer renders interactive room/building polygons.
- MapsIndoors display rules control polygon visibility, fill, stroke, labels, and selection state.
- Auckland-specific Angular code controls floor selection, room details, routing, and highlight behavior.

For a Building3D implementation, copy the data model rather than the exact UI:

1. Import room, building, floor, and venue polygons as GeoJSON.
2. Convert geographic coordinates to local 3D coordinates.
3. Use floor IDs for vertical stacking and visibility.
4. Use raster tiles or floor images as visual references.
5. Use vector meshes/polygons for interaction, selection, labels, and routing.

## Source Links

- Subject page: <https://maps.auckland.ac.nz/auckland/fa64ffa351cb4fe680fa2929/details/9a0ab05b1d0a45fbaf33af00>
- Selected room API: <https://api-us-east.mapsindoors.com/auckland/api/locations/details/9a0ab05b1d0a45fbaf33af00?v=5>
- City Campus venue API: <https://api-us-east.mapsindoors.com/auckland/api/venues/details/fa64ffa351cb4fe680fa2929>
- Auckland buildings sync API: <https://api-us-east.mapsindoors.com/sync/buildings?solutionId=auckland&v=5>
- Auckland venues sync API: <https://api-us-east.mapsindoors.com/sync/venues?solutionId=auckland&v=5>
- Auckland solution API: <https://api-us-east.mapsindoors.com/api/solutions/details/auckland?v=5>
- MapsIndoors display rules: <https://docs.mapsindoors.com/products/cms/display-rules>
- MapsIndoors JavaScript SDK docs: <https://app.mapsindoors.com/mapsindoors/js/sdk/latest/docs/mapsindoors.MapsIndoors.html>
- Google Maps Data layer: <https://developers.google.com/maps/documentation/javascript/datalayer>
- Google Maps custom map types and tile overlays: <https://developers.google.com/maps/documentation/javascript/maptypes>
