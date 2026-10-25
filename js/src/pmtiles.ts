import { PMTiles, ResolvedValueCache } from "pmtiles";
import type { Cache, DecompressFunc, RangeResponse, Source } from "pmtiles";
import { MosaicClient } from "./client.js";
import type { MosaicLoader, ResolvedSlice, ResolverOptions, TileReader } from "./types.js";

export interface PMTilesReaderOptions {
  /** Supply a PMTiles Source for custom range reads, or a URL for FetchSource. */
  readonly getSource?: (slice: ResolvedSlice) => Source | string;
  readonly cache?: Cache;
  readonly decompress?: DecompressFunc;
}

/**
 * Optional PMTiles reader. No I/O happens until a tile is requested.
 * Each reader retains archive instances and shares a resolved-value directory cache.
 */
export function createPMTilesReader(options: PMTilesReaderOptions = {}): TileReader<RangeResponse> {
  const cache = options.cache ?? new ResolvedValueCache(100, true);
  const archives = new WeakMap<ResolvedSlice, PMTiles>();
  return (slice, z, x, y, signal) => {
    let archive = archives.get(slice);
    if (!archive) {
      archive = new PMTiles(options.getSource?.(slice) ?? slice.location, cache, options.decompress);
      archives.set(slice, archive);
    }
    return archive.getZxy(z, x, y, signal);
  };
}

export interface PMTilesMosaicOptions extends ResolverOptions, PMTilesReaderOptions {
  /** Override fetching/parsing the manifest while retaining the PMTiles reader. */
  readonly loadMosaic?: MosaicLoader;
}

/** HTTP convenience client; the core entry point has no built-in transport. */
export class PMTilesMosaic extends MosaicClient<RangeResponse> {
  constructor(url: string | URL, options: PMTilesMosaicOptions = {}) {
    const location = url.toString();
    super({
      ...options,
      baseUrl: options.baseUrl ?? location,
      loadMosaic: options.loadMosaic ?? (async (signal) => {
        const response = await fetch(location, signal ? { signal } : {});
        if (!response.ok) {
          throw new Error(`Failed to fetch mosaic ${location}: HTTP ${response.status}`);
        }
        return response.json();
      }),
      readTile: createPMTilesReader(options),
    });
  }
}
