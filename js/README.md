# pmtiles-mosaic

A TypeScript library for resolving and reading tiles from [PMTiles mosaics](https://github.com/ramSeraph/pmtiles_mosaic/blob/main/spec.md).
Published as an ES module with TypeScript declarations. Works in modern browsers,
workers, and Node.js 20+.

The library uses **PMTiles as a required peer dependency** for its tile types,
compression enums, and format extensions. Manifest loading, tile reading, and
spatial indexing are independent extension points. Each index plugin owns both
construction and lookup. Flatbush is an optional peer, loaded only through its
separate entry point.

| Entry point | Provides | Peer dependencies |
| --- | --- | --- |
| `pmtiles-mosaic` | `MosaicResolver`, `MosaicClient`, `PMTilesMosaic`, `createPMTilesReader`, types, linear index | `pmtiles` |
| `pmtiles-mosaic/flatbush` | `createFlatbushIndex` | `pmtiles`, `flatbush` |

`MosaicHeader.tile_type` and `tile_compression` use PMTiles' `TileType` and
`Compression` enums directly. Supported format strings come from `tileTypeExt()`,
including MLT. MVT tiles use `mvt`, and JPEG tiles use `jpg`; the `pbf` and
`jpeg` aliases are not accepted.
Compression support during tile reads depends on the reader's decompressor.

## Read tiles with PMTiles

```sh
npm install pmtiles-mosaic pmtiles
```

```ts
import { PMTilesMosaic } from "pmtiles-mosaic";

const mosaic = new PMTilesMosaic("https://example.com/maps/map.mosaic.json");
const tile = await mosaic.getTile(8, 182, 111);

if (tile) {
  const bytes = new Uint8Array(tile.data);
  // Render, decode, or serve the bytes.
}

const resolver = await mosaic.getResolver();
console.log(resolver.mimeType);
const tileJson = resolver.getTileJson([
  "https://my-server.example/tiles/{z}/{x}/{y}.mvt",
]);
```

The client fetches and validates the version 1 mosaic, resolves slice URLs relative
to its URL, and uses `pmtiles` for range requests and decompression. `getTile`
returns the PMTiles `RangeResponse` unchanged, including its `data: ArrayBuffer`
and any cache headers, or `undefined` when the tile is absent. An optional fourth
argument is an `AbortSignal`.

## Resolve without any I/O

```sh
npm install pmtiles-mosaic pmtiles
```

```ts
import { MosaicResolver } from "pmtiles-mosaic";

// Load/parse the JSON however your application needs to.
const document = await loadMyMosaic();
const resolver = new MosaicResolver(document, {
  baseUrl: "https://example.com/maps/map.mosaic.json",
});

for (const candidate of resolver.resolve(8, 182, 111)) {
  // candidate.key: original entry key, such as "map-part0000.pmtiles"
  // candidate.location: resolved URL or application-defined storage location
  // candidate.header: slice bounds and zoom range
  // candidate.slice: complete manifest entry, including custom fields
  // Use your own archive reader to check for the tile.
}
```

`MosaicResolver` never fetches anything. Without `baseUrl`, locations are the
original keys. Supply `resolveSlice(key, entry)` to resolve local paths, object
storage keys, signed URLs, or other identifiers yourself; it overrides `baseUrl`.
Relative URL bases such as `/maps/map.mosaic.json` are also supported.

## Supply both the manifest loader and tile reader

```ts
import { MosaicClient } from "pmtiles-mosaic";

const client = new MosaicClient({
  // These storage functions belong to your application.
  loadMosaic: (signal) => storage.readJson("maps/map.mosaic.json", signal),
  resolveSlice: (key) => `maps/${key}`,
  readTile: (slice, z, x, y, signal) =>
    storage.readTile(slice.location, z, x, y, signal),
});

const tile = await client.getTile(8, 182, 111);
```

`readTile` may return any payload type; `getTile` preserves that type and value.
Return `undefined` for an absent tile and throw for I/O errors. The client tries
matching slices in manifest order until a reader returns a tile. Errors propagate
instead of being treated as missing tiles.

To keep the PMTiles decoder while supplying your own storage, use the reader
adapter with a [PMTiles `Source`](https://pmtiles.io/typedoc/interfaces/Source.html):

```ts
import { MosaicClient, createPMTilesReader } from "pmtiles-mosaic";

const client = new MosaicClient({
  loadMosaic: (signal) => storage.readJson("maps/map.mosaic.json", signal),
  resolveSlice: (key) => `maps/${key}`,
  readTile: createPMTilesReader({
    getSource: (slice) => ({
      getKey: () => slice.location,
      getBytes: (offset, length, signal, etag) =>
        storage.readRange(slice.location, offset, length, signal, etag),
    }),
  }),
});
```

`readRange` returns a PMTiles `RangeResponse`, including `data: ArrayBuffer`.
`getKey()` must uniquely identify the archive for directory caching. The adapter
also accepts PMTiles `cache` and `decompress` options. Its default is a shared
`ResolvedValueCache` with 100 entries within each reader instance.

The convenience `PMTilesMosaic` constructor accepts these same `getSource`,
`cache`, and `decompress` options, plus a `loadMosaic` override. This lets you
replace manifest fetching and range fetching independently.

You can also replace tile reading entirely on the convenience client:

```ts
import { PMTilesMosaic } from "pmtiles-mosaic";

const mosaic = new PMTilesMosaic("https://example.com/maps/map.mosaic.json", {
  readTile: (slice, z, x, y, signal) =>
    myReader.getTile(slice.location, z, x, y, signal),
});
```

This callback returns a PMTiles `RangeResponse` or `undefined`; it replaces the
default `createPMTilesReader()`. The `getSource`, `cache`, and `decompress` options
apply only to the default reader. Use `MosaicClient<T>` for other payload types.

## Opt into Flatbush

```sh
npm install flatbush
```

```ts
import { MosaicResolver } from "pmtiles-mosaic";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";

const resolver = new MosaicResolver(document, {
  createIndex: createFlatbushIndex,
});
const candidates = resolver.resolve(8, 182, 111);
```

The same option works with either client:

```ts
import { PMTilesMosaic } from "pmtiles-mosaic";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";

const mosaic = new PMTilesMosaic("https://example.com/maps/map.mosaic.json", {
  createIndex: createFlatbushIndex,
});
```

The plugin constructs a Flatbush index for a zoom level on its first query, using
the mosaic's E7 coordinates in an `Int32Array`. It also implements lookup against
that index. Unqueried zooms do not allocate indexes. There is **no automatic slice
count threshold**: callers choose their index. Importing the main entry point
neither loads nor requires Flatbush.

## Write an index plugin

An index factory receives the immutable slice array for one zoom level and
returns its search implementation. Both construction and lookup belong to the
plugin:

```ts
import type { SliceIndexFactory } from "pmtiles-mosaic";

const createMyIndex: SliceIndexFactory = (slices, zoom) => {
  const index = buildMySpatialIndex(slices, zoom);
  return {
    search: ([west, south, east, north]) =>
      index.findIntersectingItemIds(west, south, east, north),
  };
};
```

The search input is `[west, south, east, north]` in **E7 degrees**. Return numeric
indices into the supplied array, including every slice whose bounds intersect or
touch the query. Results may include false positives, duplicates, or arbitrary
ordering. The resolver filters them and restores manifest order. Omitted matches
cannot be recovered by the resolver. Indexes are built once per queried zoom and
reused. `createLinearIndex` is the built-in default.

## Resolution and lifecycle

- Slice bounds are candidate filters. Intersection, rather than full tile
  containment, supports partial coverage and sparse or overlapping archives.
  Touching edges are included conservatively. The PMTiles reader checks actual
  tile presence. When multiple slices contain a tile, the first in manifest
  order wins.
- Tile coordinates must be integer XYZ coordinates at zooms 0–26. Bounds are
  truncated to E7 like the Python writers. Inverted bounds, including wrapped
  antimeridian bounds, are rejected; represent such coverage with separate slices.
- Top-level geographic bounds describe the extent at the highest zoom and are
  used for TileJSON. Resolution uses each slice's bounds, which can be wider for
  overview tiles.
- The resolver validates, copies, and freezes the document. Mutating the original
  JSON cannot invalidate an existing index.
- Clients retain the first successfully loaded manifest. Failed or cancelled
  loads can be retried. Concurrent first requests may each call `loadMosaic`;
  pending I/O is not shared across requests, and cancelling one request does not
  cancel another. A custom loader can add deduplication if appropriate for its
  runtime. Cancellation is forwarded to custom loaders/readers; actual I/O
  cancellation depends on those implementations (and on PMTiles for its adapter).
- Create a new client/resolver to switch manifest versions. Use versioned or
  immutable slice locations when replacing a dataset: a manifest snapshot alone
  cannot make changes across several mutable archives atomic.

## Development and publishing

From `js/`:

```sh
npm ci
npm test
npm run test:package
npm run bench
```

Tests include the specification's example, linear/Flatbush parity, custom I/O,
cancellation, and an HTTP integration using a small archive written by the
Python PMTiles package. The package check installs an npm tarball into an isolated
directory with the PMTiles peer and no Flatbush, and checks both core runtime
imports and types.

`npm run bench` compares construction and query cost for stripe, grid, and
overlapping layouts at several slice counts. It measures the complete resolver,
including candidate ordering and bounds checks. Choose an index based on your
own slice distribution and query volume; large overlap can erase the spatial
index's advantage.

To publish, set the intended version in `package.json` and `package-lock.json`,
run the checks above, then run `npm publish` from `js/`. The `prepack` hook builds
TypeScript into `dist/`, including declarations and source maps. Package exports
keep the integrations separate. Publishing this npm package is
independent of the repository's Python release workflow.

## License

Unlicense. See [LICENSE](./LICENSE).
