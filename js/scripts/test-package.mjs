import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const scratch = mkdtempSync(join(tmpdir(), "pmtiles-mosaic-package-"));
const run = (command, args, cwd = scratch) => execFileSync(command, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
try {
  const [archive] = JSON.parse(run("npm", ["pack", "--json", "--ignore-scripts", "--pack-destination", scratch], root));
  const files = archive.files.map(({ path }) => path);
  for (const file of ["dist/index.js", "dist/index.d.ts", "dist/flatbush.js", "dist/pmtiles.js", "LICENSE", "README.md"]) {
    assert(files.includes(file), `Tarball must contain ${file}`);
  }
  assert(!files.some((file) => /^(node_modules|test|bench|scripts)\//.test(file)));
  writeFileSync(join(scratch, "package.json"), JSON.stringify({ private: true, type: "module" }));
  run("npm", ["install", "--offline", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund", "--package-lock=false", join(scratch, archive.filename)]);
  const installed = JSON.parse(readFileSync(join(scratch, "node_modules/pmtiles-mosaic/package.json"), "utf8"));
  assert.equal(installed.dependencies, undefined);
  writeFileSync(join(scratch, "consumer.mjs"), `
    import assert from 'node:assert/strict';
    import { MosaicResolver, MosaicClient } from 'pmtiles-mosaic';
    for (const dependency of ['flatbush', 'flatqueue', 'pmtiles', 'fflate']) {
      await assert.rejects(import(dependency), { code: 'ERR_MODULE_NOT_FOUND' });
    }
    globalThis.fetch = () => { throw new Error('core must not fetch'); };
    const document = {
      version: 1, metadata: { format: 'png' }, slices: {},
      header: { min_lon_e7: -1800000000, min_lat_e7: -850511287, max_lon_e7: 1800000000,
        max_lat_e7: 850511287, min_zoom: 0, max_zoom: 1, center_lon_e7: 0, center_lat_e7: 0,
        center_zoom: 0, tile_type: 2, tile_compression: 1 }
    };
    assert.deepEqual(new MosaicResolver(document).resolve(0, 0, 0), []);
    const client = new MosaicClient({ loadMosaic: async () => document, readTile: async () => 0 });
    assert.equal(await client.getTile(0, 0, 0), undefined);
  `);
  run(process.execPath, ["consumer.mjs"]);
  writeFileSync(join(scratch, "consumer.ts"), `
    import { MosaicClient, MosaicResolver, type SliceIndexFactory } from 'pmtiles-mosaic';
    const factory: SliceIndexFactory = () => ({ search: () => [] });
    declare const document: unknown;
    const resolver = new MosaicResolver(document, { createIndex: factory });
    const client = new MosaicClient({ loadMosaic: async () => document, readTile: async () => new Uint8Array() });
    const tile: Uint8Array | undefined = await client.getTile(0, 0, 0);
    void [resolver, tile];
  `);
  run(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "--strict", "--module", "NodeNext", "--target", "ES2022", "consumer.ts"]);
  console.log(`Packed ${archive.filename}: ${archive.size} bytes; core runtime and declarations work without optional peers.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  throw error;
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
