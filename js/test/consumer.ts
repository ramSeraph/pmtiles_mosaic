import { MosaicClient, MosaicResolver, type SliceIndexFactory, type MosaicDocument } from "pmtiles-mosaic";
import { createFlatbushIndex } from "pmtiles-mosaic/flatbush";
import { createPMTilesReader, PMTilesMosaic } from "pmtiles-mosaic/pmtiles";

declare const document: MosaicDocument;
const custom: SliceIndexFactory = (slices, zoom) => ({
  search: (bounds) => slices.flatMap((slice, i) =>
    slice.header.min_zoom <= zoom && slice.header.max_lon_e7 >= bounds[0] ? [i] : []),
});
const resolver = new MosaicResolver(document, { createIndex: custom });
const indexed = new MosaicResolver(document, { createIndex: createFlatbushIndex });
const key: string | undefined = indexed.resolve(0, 0, 0)[0]?.key;
const client = new MosaicClient({
  loadMosaic: async () => document,
  readTile: async () => ({ payload: new Uint8Array(), tag: "custom" }),
});
const value: Uint8Array | undefined = (await client.getTile(0, 0, 0))?.payload;
const pmtiles = new MosaicClient({ loadMosaic: async () => document, readTile: createPMTilesReader() });
const bytes: ArrayBuffer | undefined = (await pmtiles.getTile(0, 0, 0))?.data;
const simple = new PMTilesMosaic("https://example.org/map.mosaic.json", {
  createIndex: createFlatbushIndex,
  loadMosaic: async () => document,
});
const simpleBytes: ArrayBuffer | undefined = (await simple.getTile(0, 0, 0))?.data;
void [resolver, key, value, bytes, simpleBytes];
