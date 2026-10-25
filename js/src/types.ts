export const TILE_FORMATS = Object.freeze(["pbf", "mvt", "png", "jpg", "jpeg", "webp", "avif"] as const);
export type TileFormat = typeof TILE_FORMATS[number];

/** Geographic bounds in degrees multiplied by 10,000,000. */
export type BoundsE7 = readonly [west: number, south: number, east: number, north: number];

export interface SliceHeader {
  readonly min_lon_e7: number;
  readonly min_lat_e7: number;
  readonly max_lon_e7: number;
  readonly max_lat_e7: number;
  readonly min_zoom: number;
  readonly max_zoom: number;
}

export interface MosaicHeader extends SliceHeader {
  readonly center_lon_e7: number;
  readonly center_lat_e7: number;
  readonly center_zoom: number;
  readonly tile_type: 0 | 1 | 2 | 3 | 4 | 5;
  readonly tile_compression: 0 | 1 | 2 | 3 | 4;
}

export interface VectorLayer {
  readonly id: string;
  readonly fields?: Readonly<Record<string, string>>;
  readonly minzoom?: number;
  readonly maxzoom?: number;
  readonly [key: string]: unknown;
}

export interface MosaicMetadata {
  readonly format: TileFormat;
  readonly name?: string;
  readonly description?: string;
  readonly attribution?: string;
  readonly version?: string;
  readonly type?: string;
  readonly vector_layers?: readonly VectorLayer[];
  readonly [key: string]: unknown;
}

export interface MosaicSlice {
  readonly header: SliceHeader;
  readonly [key: string]: unknown;
}

export interface MosaicDocument {
  readonly version: 1;
  readonly header: MosaicHeader;
  readonly metadata: MosaicMetadata;
  readonly slices: Readonly<Record<string, MosaicSlice>>;
}

/** A candidate archive. Bounds are a hint; the archive may not contain the tile. */
export interface ResolvedSlice {
  /** The original key in the mosaic's slices object. */
  readonly key: string;
  /** Resolved URL, storage key, or other caller-defined location. */
  readonly location: string;
  readonly header: SliceHeader;
  /** The complete slice entry, including any application-specific fields. */
  readonly slice: MosaicSlice;
}

/** An index searches the immutable slice array supplied to its factory. */
export interface SliceIndex {
  /**
   * Return indices into that array for every slice intersecting bounds.
   * Order is arbitrary and false positives are allowed. Do not omit matches.
   * Bounds include touching edges. The resolver filters and orders results.
   */
  search(bounds: BoundsE7): readonly number[];
}

/** Builds an index and returns its lookup implementation, once per queried zoom. */
export type SliceIndexFactory = (
  slices: readonly ResolvedSlice[],
  zoom: number,
) => SliceIndex;

export interface ResolverOptions {
  /** Location of the mosaic document, used to resolve relative slice keys. */
  readonly baseUrl?: string | URL;
  /** Overrides URL resolution, e.g. for filesystem or object-storage keys. */
  readonly resolveSlice?: (key: string, slice: MosaicSlice) => string;
  /** Omit for a linear scan. The plugin owns index construction and lookup. */
  readonly createIndex?: SliceIndexFactory;
}

export interface TileJSON {
  readonly tilejson: "3.0.0";
  readonly scheme: "xyz";
  readonly tiles: readonly string[];
  readonly bounds: readonly [number, number, number, number];
  readonly center: readonly [number, number, number];
  readonly minzoom: number;
  readonly maxzoom: number;
  readonly name?: string;
  readonly description?: string;
  readonly attribution?: string;
  readonly version?: string;
  readonly vector_layers?: readonly VectorLayer[];
  readonly [key: string]: unknown;
}

/** Load and parse the mosaic using any transport. Only successful loads are cached. */
export type MosaicLoader = (signal?: AbortSignal) => Promise<unknown>;

/** Return undefined only for an absent tile; throw for storage/network failures. */
export type TileReader<T> = (
  slice: ResolvedSlice,
  z: number,
  x: number,
  y: number,
  signal?: AbortSignal,
) => Promise<T | undefined>;

export interface MosaicClientOptions<T> extends ResolverOptions {
  readonly loadMosaic: MosaicLoader;
  readonly readTile: TileReader<T>;
}
