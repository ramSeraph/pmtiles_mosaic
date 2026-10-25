import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { MosaicResolver, tileBoundsE7 } from "pmtiles-mosaic";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";
import { mosaic, random, slice } from "../test/helpers.mjs";

// Reproducible full-resolver timings, including ordering and exact bounds checks.
// Construction includes manifest validation, snapshotting, zoom grouping, and the
// first lazy index build. The overlap case illustrates why there is no universal threshold.
const counts = [8, 32, 128, 512, 2048];
const rand = random();
const queries = Array.from({ length: 2048 }, () => [12, Math.floor(rand() * 4096), Math.floor(rand() * 4096)]);
const median = (values) => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
let checksum = 0;

function makeDocument(count, layout) {
  const entries = {};
  for (let i = 0; i < count; i++) {
    let bounds;
    if (layout === "stripes") {
      bounds = [Math.trunc(-1800000000 + i / count * 3600000000), -850511287,
        Math.trunc(-1800000000 + (i + 1) / count * 3600000000), 850511287];
    } else if (layout === "grid") {
      const z = Math.ceil(Math.log2(Math.sqrt(count)));
      bounds = tileBoundsE7(z, i % (2 ** z), Math.floor(i / 2 ** z));
    } else {
      bounds = [-1800000000, -850511287, 1800000000, 850511287];
    }
    entries[`part-${i}.pmtiles`] = slice(bounds, 12, 12);
  }
  return mosaic(entries);
}

function measure(document, createIndex) {
  const construction = [];
  let resolver;
  for (let i = 0; i < 7; i++) {
    const start = performance.now();
    resolver = new MosaicResolver(document, createIndex ? { createIndex } : {});
    resolver.resolve(...queries[0]);
    construction.push(performance.now() - start);
  }
  for (const query of queries) checksum += resolver.resolve(...query).length;
  const times = [];
  for (let i = 0; i < 5; i++) {
    const start = performance.now();
    for (const query of queries) checksum += resolver.resolve(...query).length;
    times.push((performance.now() - start) * 1000 / queries.length);
  }
  return { resolver, build: median(construction), query: median(times) };
}

console.log(`${process.version} ${process.platform}/${process.arch}; 2,048 seeded z12 queries, median of 5 runs`);
console.log("layout   slices  linear build ms  flatbush build ms  linear us/query  flatbush us/query  speedup");
for (const layout of ["stripes", "grid", "overlap"]) {
  for (const count of counts) {
    const document = makeDocument(count, layout);
    const linear = measure(document);
    const indexed = measure(document, createFlatbushIndex);
    for (const query of queries.slice(0, 100)) {
      assert.deepEqual(indexed.resolver.resolve(...query).map((s) => s.key), linear.resolver.resolve(...query).map((s) => s.key));
    }
    console.log(`${layout.padEnd(8)} ${String(count).padStart(6)} ${linear.build.toFixed(3).padStart(16)} ${indexed.build.toFixed(3).padStart(18)} ${linear.query.toFixed(3).padStart(16)} ${indexed.query.toFixed(3).padStart(18)} ${(linear.query / indexed.query).toFixed(2).padStart(7)}x`);
  }
}
console.log(`checksum: ${checksum}`);
