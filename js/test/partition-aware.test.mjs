import assert from "node:assert/strict";
import test from "node:test";
import { MosaicResolver } from "@ramseraph/pmtiles-mosaic";
import { createPartitionAwareIndex } from "@ramseraph/pmtiles-mosaic/partition-aware";
import { mosaic, random, slice } from "./helpers.mjs";
import { partitionedMosaic } from "./partition-helpers.mjs";

const keys = (slices) => slices.map(s => s.key);

test("partition-aware index matches a scan across large, uneven recursive x/y partitions", () => {
  for (const layout of ["stripes", "recursive"]) {
    for (const count of [1, 2, 17, 100, 250, 500, 1000]) {
      const document = partitionedMosaic(count, layout, 1234 + count);
      const linear = new MosaicResolver(document);
      const indexed = new MosaicResolver(document, { createIndex: createPartitionAwareIndex });
      const rand = random(9876);
      for (let i = 0; i < 512; i++) {
        const tile = [12, Math.floor(rand() * 4096), Math.floor(rand() * 4096)];
        assert.deepEqual(keys(indexed.resolve(...tile)), keys(linear.resolve(...tile)), `${layout}: ${count}: ${tile}`);
      }
    }
  }
});

test("partition-aware index includes touching boundaries, gaps and partial coverage in manifest order", () => {
  const document = mosaic({
    northeast: slice([0, 0, 1800000000, 850511287], 0, 2),
    southwest: slice([-1800000000, -850511287, 0, 0], 0, 2),
    northwest: slice([-1800000000, 100000000, 0, 850511287], 0, 2),
    southeast: slice([100000000, -850511287, 1800000000, -100000000], 0, 2),
  });
  const linear = new MosaicResolver(document);
  const indexed = new MosaicResolver(document, { createIndex: createPartitionAwareIndex });
  for (let z = 0; z <= 3; z++) {
    for (let x = 0; x < 2 ** z; x++) {
      for (let y = 0; y < 2 ** z; y++) {
        assert.deepEqual(keys(indexed.resolve(z, x, y)), keys(linear.resolve(z, x, y)));
      }
    }
  }
  assert.deepEqual(keys(indexed.resolve(0, 0, 0)), Object.keys(document.slices));
});

test("partition-aware index reconstructs separate layouts at each zoom", () => {
  const document = mosaic({
    overview: slice(undefined, 0, 3),
    west: slice([-1800000000, -850511287, 0, 850511287], 4, 5),
    eastSouth: slice([0, -850511287, 1800000000, 0], 4, 5),
    eastNorth: slice([0, 0, 1800000000, 850511287], 4, 5),
    detail: slice(undefined, 6, 8),
  });
  const indexed = new MosaicResolver(document, { createIndex: createPartitionAwareIndex });
  assert.deepEqual(keys(indexed.resolve(0, 0, 0)), ["overview"]);
  assert.deepEqual(keys(indexed.resolve(4, 1, 1)), ["west"]);
  assert.deepEqual(keys(indexed.resolve(5, 30, 30)), ["eastSouth"]);
  assert.deepEqual(keys(indexed.resolve(8, 30, 30)), ["detail"]);
  assert.deepEqual(indexed.resolve(9, 30, 30), []);
  assert.deepEqual(createPartitionAwareIndex([], 0).search([-1, -1, 1, 1]), []);
});

test("partition-aware index rejects overlapping and non-separable layouts without a fallback", () => {
  const overlap = mosaic({ a: slice(), b: slice() });
  // These five rectangles tile a square, but have no full x or y separating cut.
  const pinwheel = mosaic(Object.fromEntries([
    [0, 0, 3, 1], [3, 0, 4, 3], [1, 3, 4, 4], [0, 1, 1, 4], [1, 1, 3, 3],
  ].map((bounds, i) => [String(i), slice(bounds.map(n => n * 10000000))])));
  for (const document of [overlap, pinwheel]) {
    const indexed = new MosaicResolver(document, { createIndex: createPartitionAwareIndex });
    assert.throws(() => indexed.resolve(0, 0, 0), /do not form recursive x\/y partitions.*createLinearIndex/);
    assert.equal(new MosaicResolver(document).resolve(0, 0, 0).length, Object.keys(document.slices).length);
  }
});
