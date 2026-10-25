import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import test from "node:test";
import { MosaicClient } from "pmtiles-mosaic";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";
import { createPMTilesReader, PMTilesMosaic } from "pmtiles-mosaic/pmtiles";
import { mosaic, slice } from "./helpers.mjs";

const bytes = readFileSync(new URL("fixtures/sample.pmtiles", import.meta.url));
const decode = (tile) => new TextDecoder().decode(tile.data);

test("PMTiles adapter reads a Python-written archive through a custom range source", async () => {
  let sources = 0;
  const ranges = [];
  const controller = new AbortController();
  const readTile = createPMTilesReader({
    getSource(slice) {
      sources++;
      assert.equal(slice.location, "bucket/sample.pmtiles");
      return {
        getKey: () => slice.location,
        getBytes: async (offset, length, signal) => {
          ranges.push({ offset, length, signal });
          return {
            data: Uint8Array.from(bytes.subarray(offset, offset + length)).buffer,
            cacheControl: "max-age=60", expires: "Thu, 01 Jan 2026 00:00:00 GMT", etag: "fixture-v1",
          };
        },
      };
    },
  });
  const client = new MosaicClient({
    loadMosaic: async () => mosaic({ "sample.pmtiles": slice(undefined, 0, 1) }),
    resolveSlice: (key) => `bucket/${key}`,
    readTile,
    createIndex: createFlatbushIndex,
  });
  const world = await client.getTile(0, 0, 0, controller.signal);
  assert.equal(decode(world), "world tile");
  assert.equal(world.cacheControl, "max-age=60");
  assert.equal(world.expires, "Thu, 01 Jan 2026 00:00:00 GMT");
  assert.equal(decode(await client.getTile(1, 1, 1)), "southeast tile");
  assert.equal(await client.getTile(1, 0, 0), undefined);
  assert.equal(sources, 1);
  assert.equal(ranges.filter(({ offset }) => offset === 0).length, 1);
  assert(ranges.some(({ signal }) => signal === controller.signal));
});

test("PMTilesMosaic loads manifest and HTTP ranges, decompresses payloads, and retries HTTP errors", async (t) => {
  const requests = [];
  let fail = true;
  const server = createServer((request, response) => {
    requests.push({ path: request.url, range: request.headers.range });
    if (request.url === "/maps/map.mosaic.json") {
      if (fail) {
        response.writeHead(503).end("unavailable");
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(mosaic({ "sample.pmtiles": slice(undefined, 0, 1) })));
    } else if (request.url === "/maps/sample.pmtiles") {
      const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers.range ?? "");
      if (!range) return response.writeHead(400).end("range required");
      const start = Number(range[1]);
      const end = Math.min(Number(range[2]), bytes.length - 1);
      response.writeHead(206, {
        "content-range": `bytes ${start}-${end}/${bytes.length}`,
        "content-length": end - start + 1,
        "cache-control": "max-age=120",
        etag: '"fixture-v1"',
      });
      response.end(bytes.subarray(start, end + 1));
    } else {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}/maps/map.mosaic.json`;
  const client = new PMTilesMosaic(url);
  assert.deepEqual(requests, []);
  await assert.rejects(client.getTile(0, 0, 0), /HTTP 503/);
  fail = false;
  assert.equal(decode(await client.getTile(0, 0, 0)), "world tile");
  assert.equal(decode(await client.getTile(1, 1, 1)), "southeast tile");
  assert.equal(await client.getTile(1, 0, 0), undefined);
  assert.equal(requests.filter(({ path }) => path.endsWith(".json")).length, 2);
  assert(requests.some(({ path, range }) => path === "/maps/sample.pmtiles" && range));
});

test("simple PMTiles client can replace both manifest and range fetching", async () => {
  const client = new PMTilesMosaic("custom://bucket/maps/mosaic.json", {
    loadMosaic: async () => mosaic({ "sample.pmtiles": slice(undefined, 0, 1) }),
    getSource: (slice) => ({
      getKey: () => slice.location,
      getBytes: async (offset, length) => ({ data: Uint8Array.from(bytes.subarray(offset, offset + length)).buffer }),
    }),
    createIndex: createFlatbushIndex,
  });
  assert.equal(decode(await client.getTile(0, 0, 0)), "world tile");
  assert.equal((await client.getResolver()).slices[0].location, "custom://bucket/maps/sample.pmtiles");
});
