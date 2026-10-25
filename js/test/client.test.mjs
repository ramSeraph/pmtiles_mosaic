import assert from "node:assert/strict";
import test from "node:test";
import { MosaicClient } from "pmtiles-mosaic";
import { mosaic, slice } from "./helpers.mjs";

test("caller supplies all I/O and arbitrary tile values; missing candidates fall through", async () => {
  let loads = 0;
  const reads = [];
  const payload = { bytes: new Uint8Array([1, 2]), custom: "preserved" };
  const controller = new AbortController();
  const client = new MosaicClient({
    loadMosaic: async (signal) => {
      assert.equal(signal, controller.signal);
      loads++;
      return mosaic({ first: slice(), second: slice(), third: slice() });
    },
    resolveSlice: (key) => `bucket/${key}`,
    readTile: async (slice, z, x, y, signal) => {
      assert.equal(signal, controller.signal);
      reads.push([slice.location, z, x, y]);
      return slice.key === "second" ? payload : undefined;
    },
  });
  assert.equal(await client.getTile(0, 0, 0, controller.signal), payload);
  assert.deepEqual(reads, [["bucket/first", 0, 0, 0], ["bucket/second", 0, 0, 0]]);
  assert.equal(await client.getTile(1, 1, 0, controller.signal), payload);
  assert.equal(loads, 1);
});

test("returns undefined for absent tiles and never mistakes falsy tile values for absence", async () => {
  for (const value of [undefined, null, false, 0, ""]) {
    const client = new MosaicClient({ loadMosaic: async () => mosaic(), readTile: async () => value });
    assert.equal(await client.getTile(0, 0, 0), value);
  }
  let reads = 0;
  const client = new MosaicClient({
    loadMosaic: async () => mosaic({ only: slice(undefined, 2, 2) }),
    readTile: async () => { reads++; },
  });
  assert.equal(await client.getTile(0, 0, 0), undefined);
  assert.equal(reads, 0);
});

test("retries unsuccessful manifest loads, including invalid JSON documents", async () => {
  let attempts = 0;
  const client = new MosaicClient({
    loadMosaic: async () => {
      if (++attempts === 1) throw new Error("offline");
      return attempts === 2 ? { version: 99 } : mosaic();
    },
    readTile: async () => "tile",
  });
  await assert.rejects(client.getTile(0, 0, 0), /offline/);
  await assert.rejects(client.getTile(0, 0, 0), /version/);
  assert.equal(await client.getTile(0, 0, 0), "tile");
  assert.equal(await client.getTile(0, 0, 0), "tile");
  assert.equal(attempts, 3);
});

test("reader failures propagate instead of silently falling through", async () => {
  let reads = 0;
  const failure = new Error("access denied");
  const client = new MosaicClient({
    loadMosaic: async () => mosaic({ first: slice(), second: slice() }),
    readTile: async () => { reads++; throw failure; },
  });
  await assert.rejects(client.getTile(0, 0, 0), (error) => error === failure);
  assert.equal(reads, 1);
});

test("invalid coordinates and pre-aborted requests do not start I/O", async () => {
  let loads = 0;
  const client = new MosaicClient({ loadMosaic: async () => { loads++; return mosaic(); }, readTile: async () => 1 });
  await assert.rejects(client.getTile(0, 2, 0), RangeError);
  await assert.rejects(client.getTile(0, 0, 0, AbortSignal.abort()), { name: "AbortError" });
  assert.equal(loads, 0);
});

test("cancellation stops candidate reads and does not cache a cancelled load", async () => {
  const controller = new AbortController();
  let loads = 0;
  let reads = 0;
  const client = new MosaicClient({
    loadMosaic: async () => {
      loads++;
      if (loads === 1) controller.abort();
      return mosaic({ first: slice(), second: slice() });
    },
    readTile: async () => { reads++; return undefined; },
  });
  await assert.rejects(client.getTile(0, 0, 0, controller.signal), { name: "AbortError" });
  assert.equal(reads, 0);
  await client.getTile(0, 0, 0);
  assert.equal(loads, 2);
  assert.equal(reads, 2);

  const controller2 = new AbortController();
  reads = 0;
  const client2 = new MosaicClient({
    loadMosaic: async () => mosaic({ first: slice(), second: slice() }),
    readTile: async () => { reads++; controller2.abort(); },
  });
  await assert.rejects(client2.getTile(0, 0, 0, controller2.signal), { name: "AbortError" });
  assert.equal(reads, 1);
});

test("concurrent initial loads own their I/O and retain the first successful snapshot", async () => {
  const finish = [];
  const client = new MosaicClient({
    loadMosaic: () => new Promise((resolve) => finish.push(resolve)),
    readTile: async (slice) => slice.key,
  });
  const controller = new AbortController();
  const aborted = client.getTile(0, 0, 0, controller.signal);
  const success = client.getTile(0, 0, 0);
  assert.equal(finish.length, 2);
  controller.abort();
  finish[0](mosaic({ cancelled: slice() }));
  finish[1](mosaic({ stable: slice() }));
  await assert.rejects(aborted, { name: "AbortError" });
  assert.equal(await success, "stable");
  assert.equal(await client.getTile(0, 0, 0), "stable");

  const finish2 = [];
  const client2 = new MosaicClient({
    loadMosaic: () => new Promise((resolve) => finish2.push(resolve)), readTile: async (slice) => slice.key,
  });
  const a = client2.getTile(0, 0, 0);
  const b = client2.getTile(0, 0, 0);
  finish2[1](mosaic({ winner: slice() }));
  assert.equal(await b, "winner");
  finish2[0](mosaic({ late: slice() }));
  assert.equal(await a, "winner");
});
