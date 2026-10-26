import { MosaicClient, MosaicResolver, createPMTilesReader, PMTilesMosaic, type SliceIndexFactory, type MosaicDocument, type TileFormat } from "@ramseraph/pmtiles-mosaic";
import { Compression, TileType } from "pmtiles";
import { createFlatbushIndex } from "@ramseraph/pmtiles-mosaic/flatbush";
import { createPartitionAwareIndex } from "@ramseraph/pmtiles-mosaic/partition-aware";
import { createLinearIndex } from "@ramseraph/pmtiles-mosaic/linear";

declare const document: MosaicDocument;
const tileType: TileType = document.header.tile_type;
const compression: Compression = document.header.tile_compression;
const formats: readonly TileFormat[] = ["mvt", "png", "jpg", "webp", "avif", "mlt"];
// @ts-expect-error Formats remain a literal union derived from PMTiles, not arbitrary strings.
const invalidFormat: TileFormat = "gif";
// @ts-expect-error JPEG tiles use the jpg extension, not jpeg.
const removedJpegAlias: TileFormat = "jpeg";
// @ts-expect-error MVT tiles use the mvt extension; the pbf alias is unsupported.
const removedPbfAlias: TileFormat = "pbf";
// @ts-expect-error Unsupported PMTiles enum values cannot appear in a typed header.
const invalidType: MosaicDocument["header"]["tile_type"] = 99;
const custom: SliceIndexFactory = (slices, zoom) => ({
  search: (bounds) => slices.flatMap((slice, i) =>
    slice.header.min_zoom <= zoom && slice.header.max_lon_e7 >= bounds[0] ? [i] : []),
});
const resolver = new MosaicResolver(document, { createIndex: custom });
const indexed = new MosaicResolver(document, { createIndex: createFlatbushIndex });
const partitioned = new MosaicResolver(document, { createIndex: createPartitionAwareIndex });
const linear = new MosaicResolver(document, { createIndex: createLinearIndex });
const key: string | undefined = indexed.resolve(0, 0, 0)[0]?.key;
const client = new MosaicClient({
  loadMosaic: async () => document,
  readTile: async () => ({ payload: new Uint8Array(), tag: "custom" }),
});
const value: Uint8Array | undefined = (await client.getTile(0, 0, 0))?.payload;
const mime: string = await client.getMimeType();
const tileJson = await client.getTileJson(["/tiles/{z}/{x}/{y}.mvt"]);
const urls: readonly string[] = tileJson.tiles;
// @ts-expect-error TileJSON only exposes selected serving fields, not arbitrary archive metadata.
tileJson.generator_options;
const pmtiles = new MosaicClient({ loadMosaic: async () => document, readTile: createPMTilesReader() });
const bytes: ArrayBuffer | undefined = (await pmtiles.getTile(0, 0, 0))?.data;
const simple = new PMTilesMosaic("https://example.org/map.mosaic.json", {
  createIndex: createFlatbushIndex,
  loadMosaic: async () => document,
});
const simpleBytes: ArrayBuffer | undefined = (await simple.getTile(0, 0, 0))?.data;
void [resolver, partitioned, linear, key, value, mime, urls, bytes, simpleBytes, tileType, compression, formats, invalidFormat, removedJpegAlias, removedPbfAlias, invalidType];
