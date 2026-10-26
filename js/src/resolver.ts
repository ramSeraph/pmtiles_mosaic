import { TileType } from "pmtiles";
import { intersects, tileBoundsE7 } from "./geometry.js";
import { createLinearIndex } from "./indexers/linear.js";
import { parseMosaic } from "./validate.js";
import type {
  MosaicDocument, ResolvedSlice, ResolverOptions, SliceIndex, SliceIndexFactory, TileJSON,
} from "./types.js";

const MIME_TYPES: Readonly<Record<TileType, string>> = {
  [TileType.Unknown]: "application/octet-stream",
  [TileType.Mvt]: "application/vnd.mapbox-vector-tile",
  [TileType.Png]: "image/png",
  [TileType.Jpeg]: "image/jpeg",
  [TileType.Webp]: "image/webp",
  [TileType.Avif]: "image/avif",
  [TileType.Mlt]: "application/vnd.maplibre-vector-tile",
};

interface ZoomState {
  readonly slices: readonly ResolvedSlice[];
  index?: SliceIndex;
}

function resolveLocation(key: string, baseUrl?: string | URL): string {
  // Without a base, leave storage keys and paths entirely under caller control.
  if (baseUrl === undefined) return key;
  const base = new URL(baseUrl.toString(), "mosaic-relative:///");
  const resolved = new URL(key, base);
  if (resolved.protocol === "mosaic-relative:") {
    return resolved.pathname + resolved.search + resolved.hash;
  }
  return resolved.href;
}

/** Pure mosaic resolution; never fetches the manifest or any tile data. */
export class MosaicResolver {
  readonly document: MosaicDocument;
  readonly slices: readonly ResolvedSlice[];
  private readonly byZoom = new Map<number, ZoomState>();
  private readonly createIndex: SliceIndexFactory;

  /** Validate and snapshot parsed input before constructing the resolver. */
  constructor(document: unknown, options: ResolverOptions = {}) {
    this.document = parseMosaic(document);
    this.createIndex = options.createIndex ?? createLinearIndex;
    this.slices = Object.freeze(Object.entries(this.document.slices).map(([key, slice]) =>
      Object.freeze({
        key,
        location: options.resolveSlice
          ? options.resolveSlice(key, slice)
          : resolveLocation(key, options.baseUrl),
        header: slice.header,
        slice,
      }),
    ));
    const groups = new Map<number, ResolvedSlice[]>();
    for (const slice of this.slices) {
      for (let z = slice.header.min_zoom; z <= slice.header.max_zoom; z++) {
        let group = groups.get(z);
        if (!group) groups.set(z, group = []);
        group.push(slice);
      }
    }
    for (const [z, slices] of groups) this.byZoom.set(z, { slices: Object.freeze(slices) });
  }

  /** Candidates in manifest order; a bounding box does not guarantee a tile exists. */
  resolve(z: number, x: number, y: number): readonly ResolvedSlice[] {
    const bounds = tileBoundsE7(z, x, y);
    const state = this.byZoom.get(z);
    if (!state) return [];
    state.index ??= this.createIndex(state.slices, z);
    const indices = [...new Set(state.index.search(bounds))].sort((a, b) => a - b);
    const found: ResolvedSlice[] = [];
    for (const i of indices) {
      if (!Number.isInteger(i) || i < 0 || i >= state.slices.length) {
        throw new RangeError(`Slice index returned invalid item index ${i}`);
      }
      const slice = state.slices[i]!;
      if (intersects(slice.header, bounds)) found.push(slice);
    }
    return found;
  }

  get mimeType(): string {
    return MIME_TYPES[this.document.header.tile_type] ?? MIME_TYPES[TileType.Unknown];
  }

  /**
   * Build TileJSON using caller-provided URL templates and selected serving
   * metadata. Complete archive metadata remains available on document.metadata.
   */
  getTileJson(tiles: readonly string[]): TileJSON {
    const { header: h, metadata } = this.document;
    return {
      ...(metadata.name !== undefined && { name: metadata.name }),
      ...(metadata.description !== undefined && { description: metadata.description }),
      ...(metadata.attribution !== undefined && { attribution: metadata.attribution }),
      ...(metadata.version !== undefined && { version: metadata.version }),
      ...(metadata.vector_layers !== undefined && { vector_layers: metadata.vector_layers }),
      tilejson: "3.0.0",
      scheme: "xyz",
      tiles: [...tiles],
      bounds: [h.min_lon_e7 / 1e7, h.min_lat_e7 / 1e7, h.max_lon_e7 / 1e7, h.max_lat_e7 / 1e7],
      center: [h.center_lon_e7 / 1e7, h.center_lat_e7 / 1e7, h.center_zoom],
      minzoom: h.min_zoom,
      maxzoom: h.max_zoom,
    };
  }
}
