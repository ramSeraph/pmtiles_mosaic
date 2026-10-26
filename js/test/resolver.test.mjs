import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { MosaicResolver, parseMosaic, tileBoundsE7, TILE_FORMATS } from "pmtiles-mosaic";
import { Compression, TileType } from "pmtiles";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";
import { mosaic, random, slice } from "./helpers.mjs";

const keys = (slices) => slices.map(({ key }) => key);

test("reads the specification's example, resolves URLs and produces TileJSON", () => {
  const spec = readFileSync(new URL("../../spec.md", import.meta.url), "utf8");
  const document = JSON.parse(spec.match(/```json\n([\s\S]+?)\n```/)[1]);
  const resolver = new MosaicResolver(document, { baseUrl: "https://example.org/maps/map.mosaic.json?token=1" });
  assert.deepEqual(keys(resolver.resolve(0, 0, 0)), ["my-map-part0000.pmtiles"]);
  assert.deepEqual(keys(resolver.resolve(10, 900, 500)), ["my-map-part0002.pmtiles"]);
  assert.equal(resolver.slices[0].location, "https://example.org/maps/my-map-part0000.pmtiles");
  assert.equal(resolver.mimeType, "application/vnd.mapbox-vector-tile");
  const json = resolver.getTileJson(["https://example.org/tiles/{z}/{x}/{y}.mvt"]);
  assert.equal(json.tilejson, "3.0.0");
  assert.equal(json.scheme, "xyz");
  assert.deepEqual(json.bounds, [-180, -85.0511287, 180, 85.0511287]);
  assert.deepEqual(json.center, [0, 0, 7]);
  assert.equal(json.minzoom, 0);
  assert.equal(json.maxzoom, 14);
  assert.deepEqual(json.vector_layers, document.metadata.vector_layers);
  assert.deepEqual(json.tiles, ["https://example.org/tiles/{z}/{x}/{y}.mvt"]);
});

test("TileJSON selects serving metadata and leaves archive details on the document", () => {
  const document = mosaic();
  Object.assign(document.metadata, {
    description: "Example map", version: "1", vector_layers: [{ id: "roads" }],
    generator: "tippecanoe", generator_options: "--many-options",
    tilestats: { layers: [{ attributes: ["large statistics"] }] },
  });
  const resolver = new MosaicResolver(document);
  const json = resolver.getTileJson(["/tiles/{z}/{x}/{y}.mvt"]);
  assert.deepEqual(Object.keys(json).sort(), [
    "attribution", "bounds", "center", "description", "maxzoom", "minzoom",
    "name", "scheme", "tilejson", "tiles", "vector_layers", "version",
  ].sort());
  assert.equal(json.name, document.metadata.name);
  assert.equal(json.attribution, document.metadata.attribution);
  assert.deepEqual(json.vector_layers, document.metadata.vector_layers);
  assert.deepEqual(resolver.document.metadata.tilestats, document.metadata.tilestats);
  const minimal = mosaic();
  minimal.metadata = { format: "mvt" };
  assert(!("name" in new MosaicResolver(minimal).getTileJson([])));
});

test("uses intersection, including partial coverage and touching E7 boundaries", () => {
  const document = mosaic({
    small: slice([100000000, 100000000, 200000000, 200000000], 0, 1),
    boundary: slice([0, 0, 0, 0], 0, 1),
    west: slice([-1700000000, 100000000, -1600000000, 200000000], 1, 1),
  });
  const resolver = new MosaicResolver(document);
  assert.deepEqual(keys(resolver.resolve(0, 0, 0)), ["small", "boundary"]);
  assert.deepEqual(keys(resolver.resolve(1, 1, 0)), ["small", "boundary"]);
  assert.deepEqual(keys(resolver.resolve(1, 0, 0)), ["boundary", "west"]);
  assert.deepEqual(resolver.resolve(2, 0, 0), []);
  assert.deepEqual(tileBoundsE7(0, 0, 0), [-1800000000, -850511287, 1800000000, 850511287]);
  assert.deepEqual(tileBoundsE7(1, 1, 1), [0, -850511287, 1800000000, 0]);
});

test("does not cull overview tiles by the tighter top-level max-zoom bounds", () => {
  const document = mosaic();
  Object.assign(document.header, slice([700000000, 100000000, 800000000, 200000000]).header);
  assert.equal(new MosaicResolver(document).resolve(1, 0, 0).length, 1);
});

test("supports caller-owned location resolution and special object keys", () => {
  const entries = Object.fromEntries(["../a.pmtiles", "https://cdn.example/a.pmtiles", "__proto__"].map((key) => [key, slice()]));
  const document = mosaic(entries);
  const resolver = new MosaicResolver(document, { baseUrl: "https://example.org/maps/m.json" });
  assert.deepEqual(resolver.slices.map(({ location }) => location), [
    "https://example.org/a.pmtiles", "https://cdn.example/a.pmtiles", "https://example.org/maps/__proto__",
  ]);
  assert.equal(new MosaicResolver(document).slices[0].location, "../a.pmtiles");
  assert.equal(new MosaicResolver(document, { baseUrl: "/maps/m.json" }).slices[0].location, "/a.pmtiles");
  assert.equal(new MosaicResolver(document, { baseUrl: "s3://bucket/maps/m.json" }).slices[0].location, "s3://bucket/a.pmtiles");
  const custom = new MosaicResolver(document, {
    resolveSlice(key, entry) {
      assert.equal(entry.header.min_zoom, 0);
      return `storage:${key}`;
    },
  });
  assert.deepEqual(keys(custom.resolve(0, 0, 0)), Object.keys(entries));
  assert.equal(custom.slices[2].location, "storage:__proto__");
});

test("a plugin owns lazy per-zoom construction and lookup; output order is normalized", () => {
  const built = [];
  let searches = 0;
  const resolver = new MosaicResolver(mosaic({
    first: slice(), second: slice(), distant: slice([-1700000000, -800000000, -1600000000, -700000000]),
  }), {
    createIndex(slices, zoom) {
      built.push(zoom);
      assert(Object.isFrozen(slices));
      return { search(bounds) {
        searches++;
        assert.equal(bounds.length, 4);
        return [2, 1, 0, 1]; // Broad-phase false positives, duplicate IDs, arbitrary order.
      } };
    },
  });
  assert.deepEqual(built, []);
  assert.deepEqual(keys(resolver.resolve(2, 2, 0)), ["first", "second"]);
  assert.deepEqual(keys(resolver.resolve(2, 2, 1)), ["first", "second"]);
  resolver.resolve(1, 1, 0);
  assert.deepEqual(built, [2, 1]);
  assert.equal(searches, 3);
  assert.throws(() => new MosaicResolver(mosaic(), {
    createIndex: () => ({ search: () => [99] }),
  }).resolve(0, 0, 0), /invalid item index/);
});

test("indexes an immutable snapshot without freezing the caller's objects", () => {
  const document = mosaic();
  document.metadata.vector_layers = [{ id: "roads", fields: { name: "String" } }];
  const resolver = new MosaicResolver(document);
  document.slices["map.pmtiles"].header.min_zoom = 12;
  document.metadata.vector_layers[0].id = "changed";
  assert.equal(resolver.resolve(0, 0, 0).length, 1);
  assert.equal(resolver.document.metadata.vector_layers[0].id, "roads");
  assert(Object.isFrozen(resolver.slices[0].header));
  assert(Object.isFrozen(resolver.document.metadata.vector_layers[0].fields));
  assert(!Object.isFrozen(document));
});

test("validates manifests, XYZ coordinates and known tile MIME types", () => {
  for (const [modify, pattern] of [
    [(d) => { d.version = 2; }, /version must be 1/],
    [(d) => { delete d.header; }, /header must be an object/],
    [(d) => { d.header.min_zoom = 27; }, /min_zoom/],
    [(d) => { d.header.max_zoom = -1; }, /max_zoom/],
    [(d) => { d.header.min_lon_e7 = 1800000001; }, /min_lon_e7/],
    [(d) => { d.header.min_lat_e7 = 900000000; }, /inverted bounds/],
    [(d) => { d.header.tile_type = 9; }, /tile_type/],
    [(d) => { d.metadata.format = "gif"; }, /format/],
    [(d) => { d.metadata.format = "jpeg"; }, /format/],
    [(d) => { d.metadata.format = "pbf"; }, /format/],
    [(d) => { d.metadata.vector_layers = [{}]; }, /\.id/],
    [(d) => { d.slices = []; }, /slices must be an object/],
    [(d) => { d.slices.bad = { header: {} }; }, /min_lon_e7/],
    [(d) => { d.header.max_zoom = 2; }, /zoom range is outside/],
  ]) {
    const document = mosaic();
    modify(document);
    assert.throws(() => parseMosaic(document), pattern);
  }
  const resolver = new MosaicResolver(mosaic());
  for (const tile of [[-1, 0, 0], [27, 0, 0], [1.5, 0, 0], [0, 1, 0], [0, 0, -1], [2, NaN, 0], [1, 0, Infinity]]) {
    assert.throws(() => resolver.resolve(...tile), RangeError);
  }
  assert.equal(resolver.resolve(26, 2 ** 26 - 1, 2 ** 26 - 1).length, 1);
  for (const [type, mime] of ["application/octet-stream", "application/vnd.mapbox-vector-tile", "image/png", "image/jpeg", "image/webp", "image/avif"].entries()) {
    const document = mosaic();
    document.header.tile_type = type;
    assert.equal(new MosaicResolver(document).mimeType, mime);
  }
});

test("accepts canonical PMTiles formats and compression enums, including MLT", () => {
  const formats = [
    [TileType.Mvt, "mvt"], [TileType.Png, "png"],
    [TileType.Jpeg, "jpg"], [TileType.Webp, "webp"],
    [TileType.Avif, "avif"], [TileType.Mlt, "mlt"],
  ];
  assert.deepEqual([...TILE_FORMATS].sort(), formats.map(([, format]) => format).sort());
  for (const [tileType, format] of formats) {
    for (const compression of Object.values(Compression).filter((value) => typeof value === "number")) {
      const document = mosaic();
      document.header.tile_type = tileType;
      document.header.tile_compression = compression;
      document.metadata.format = format;
      const resolver = new MosaicResolver(document);
      assert.equal(resolver.document.header.tile_type, tileType);
      assert.equal(resolver.document.header.tile_compression, compression);
      if (tileType === TileType.Mlt) {
        assert.equal(resolver.mimeType, "application/vnd.maplibre-vector-tile");
      }
    }
  }
  for (const field of ["tile_type", "tile_compression"]) {
    for (const invalid of ["Unknown", "1", -1, 1.5, 99, NaN, Infinity]) {
      const document = mosaic();
      document.header[field] = invalid;
      assert.throws(() => parseMosaic(document), new RegExp(field));
    }
  }
});

test("Flatbush and linear resolution agree over overlapping bounds and zoom ranges", () => {
  const rand = random();
  const entries = {};
  for (let i = 0; i < 160; i++) {
    const west = Math.trunc((rand() * 320 - 180) * 1e7);
    const south = Math.trunc((rand() * 140 - 85) * 1e7);
    const minZoom = Math.floor(rand() * 4);
    entries[`part-${i}`] = slice([west, south, west + 400000000, south + 300000000], minZoom, minZoom + 3);
  }
  entries.world = slice();
  entries.point = slice([0, 0, 0, 0]);
  const linear = new MosaicResolver(mosaic(entries));
  const indexed = new MosaicResolver(mosaic(entries), { createIndex: createFlatbushIndex });
  for (let z = 0; z <= 6; z++) {
    for (let x = 0; x < 2 ** z; x++) {
      for (let y = 0; y < 2 ** z; y++) {
        assert.deepEqual(keys(indexed.resolve(z, x, y)), keys(linear.resolve(z, x, y)), `tile ${z}/${x}/${y}`);
      }
    }
  }
  for (const [x, y] of [[0, 0], [2 ** 26 - 1, 2 ** 26 - 1], [2 ** 25, 2 ** 25]]) {
    assert.deepEqual(keys(indexed.resolve(26, x, y)), keys(linear.resolve(26, x, y)));
  }
  assert.deepEqual(new MosaicResolver(mosaic({}), { createIndex: createFlatbushIndex }).resolve(0, 0, 0), []);
  assert.deepEqual(createFlatbushIndex([], 0).search([-1, -1, 1, 1]), []);
});
