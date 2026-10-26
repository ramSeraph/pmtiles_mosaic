import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { MosaicResolver, tileBoundsE7 } from "@ramseraph/pmtiles-mosaic";
import { createLinearIndex } from "@ramseraph/pmtiles-mosaic/linear";
import { createFlatbushIndex } from "@ramseraph/pmtiles-mosaic/flatbush";
import { createPartitionAwareIndex } from "@ramseraph/pmtiles-mosaic/partition-aware";
import { random } from "../test/helpers.mjs";
import { partitionedMosaic } from "../test/partition-helpers.mjs";

// Counts are slices at ONE zoom. Recursive cases mix uneven x/y subdivisions.
// Lookup includes tile bounds, ordering and filtering. Factory timing isolates
// index construction from the shared manifest validation and snapshotting.
const counts = [8, 16, 32, 100, 250, 500, 1000];
const factories = { linear: createLinearIndex, flatbush: createFlatbushIndex, "partition-aware": createPartitionAwareIndex };
const names = Object.keys(factories);
const rand = random();
const queries = Array.from({ length: 4096 }, () => [12, Math.floor(rand() * 4096), Math.floor(rand() * 4096)]);
const firstBounds = tileBoundsE7(...queries[0]);
const rounds = 7;
const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
let checksum = 0;

function queryBatch(resolver, count) {
  const start = performance.now();
  let found = 0;
  for (let i = 0; i < count; i++) {
    const q = queries[i % queries.length];
    found += resolver.resolve(q[0], q[1], q[2]).length;
  }
  const elapsed = performance.now() - start;
  checksum += found;
  return elapsed;
}

function buildBatch(factory, slices, count) {
  let index;
  const start = performance.now();
  for (let i = 0; i < count; i++) index = factory(slices, 12);
  const elapsed = performance.now() - start;
  checksum += index.search(firstBounds).length;
  return elapsed;
}

function measure(document) {
  const resolvers = Object.fromEntries(names.map(name => [name, new MosaicResolver(document, { createIndex: factories[name] })]));
  for (const query of queries) {
    const expected = resolvers.linear.resolve(...query).map(s => s.key);
    for (const name of ["flatbush", "partition-aware"]) {
      assert.deepEqual(resolvers[name].resolve(...query).map(s => s.key), expected);
    }
  }
  const slices = resolvers.linear.slices;
  const queryCounts = {}, buildCounts = {};
  const queryTimes = {}, buildTimes = {};
  for (const name of names) {
    queryBatch(resolvers[name], 32768);
    buildBatch(factories[name], slices, 128);
    const qPilot = queryBatch(resolvers[name], 8192);
    const bPilot = buildBatch(factories[name], slices, 128);
    queryCounts[name] = Math.max(4096, Math.min(524288, Math.ceil(20 / qPilot) * 8192));
    buildCounts[name] = Math.max(1, Math.min(65536, Math.ceil(10 / bPilot * 128)));
    queryTimes[name] = [];
    buildTimes[name] = [];
  }
  for (let i = 0; i < rounds; i++) {
    const order = [...names.slice(i % names.length), ...names.slice(0, i % names.length)];
    for (const name of order) {
      queryTimes[name].push(queryBatch(resolvers[name], queryCounts[name]) * 1000 / queryCounts[name]);
      buildTimes[name].push(buildBatch(factories[name], slices, buildCounts[name]) * 1000 / buildCounts[name]);
    }
  }
  const queryUs = Object.fromEntries(names.map(name => [name, median(queryTimes[name])]));
  const buildUs = Object.fromEntries(names.map(name => [name, median(buildTimes[name])]));
  const amortize = (against) => queryUs["partition-aware"] < queryUs[against]
    ? Math.max(1, Math.ceil((buildUs["partition-aware"] - buildUs[against]) / (queryUs[against] - queryUs["partition-aware"])))
    : null;
  return { queryUs, buildUs, queryTimes, buildTimes, queryCounts, buildCounts,
    breakEvenVsLinear: amortize("linear"), breakEvenVsFlatbush: amortize("flatbush") };
}

console.log(`${process.version} ${process.platform}/${process.arch}; 4,096 seeded z12 queries; median of ${rounds} alternating runs`);
console.log("layout     slices | lookup us: linear flatbush partition-aware | build us: linear flatbush partition-aware | partition-aware vs flatbush");
const rows = [];
for (const layout of ["stripes", "recursive"]) {
  for (const count of counts) {
    const row = { layout, count, ...measure(partitionedMosaic(count, layout)) };
    rows.push(row);
    const q = names.map(name => row.queryUs[name].toFixed(3).padStart(8)).join(" ");
    const b = names.map(name => row.buildUs[name].toFixed(2).padStart(8)).join(" ");
    console.log(`${layout.padEnd(10)} ${String(count).padStart(6)} | ${q} | ${b} | ${(row.queryUs.flatbush / row.queryUs["partition-aware"]).toFixed(2)}x`);
  }
}
console.log(`checksum: ${checksum}`);
const jsonIndex = process.argv.indexOf("--json");
if (jsonIndex !== -1) {
  const path = process.argv[jsonIndex + 1];
  if (!path) throw new Error("--json requires an output path");
  writeFileSync(path, JSON.stringify({ runtime: process.version, platform: `${process.platform}/${process.arch}`, rounds, rows }, null, 2));
}
