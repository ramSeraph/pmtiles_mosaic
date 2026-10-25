import { TILE_FORMATS } from "./types.js";
import type { MosaicDocument, MosaicHeader, MosaicMetadata, SliceHeader, VectorLayer } from "./types.js";

const MAX_LONGITUDE_E7 = 1_800_000_000;
const MAX_LATITUDE_E7 = 900_000_000;
const MAX_ZOOM = 26;

/** Validate and snapshot a version 1 mosaic so indexes cannot become stale through mutation. */
export function parseMosaic(value: unknown): MosaicDocument {
  assertMosaic(value);
  const snapshot = structuredClone(value);
  deepFreeze(snapshot);
  return snapshot;
}

function assertMosaic(value: unknown): asserts value is MosaicDocument {
  assertObject(value, "mosaic");
  if (value.version !== 1) throw new TypeError("Unsupported mosaic format: version must be 1");

  const { header, metadata, slices } = value;
  assertMosaicHeader(header, "mosaic.header");
  assertMetadata(metadata, "mosaic.metadata");
  assertObject(slices, "mosaic.slices");

  for (const [key, slice] of Object.entries(slices)) {
    if (!key) throw new TypeError("mosaic.slices keys must not be empty");
    const path = `mosaic.slices[${JSON.stringify(key)}]`;
    assertObject(slice, path);
    assertSliceHeader(slice.header, `${path}.header`);
    if (slice.header.min_zoom < header.min_zoom || slice.header.max_zoom > header.max_zoom) {
      throw new TypeError(`${path} zoom range is outside mosaic.header`);
    }
  }
}

function assertSliceHeader(
  value: unknown,
  path: string,
): asserts value is SliceHeader & Record<string, unknown> {
  assertObject(value, path);
  const west = integer(value.min_lon_e7, `${path}.min_lon_e7`, -MAX_LONGITUDE_E7, MAX_LONGITUDE_E7);
  const east = integer(value.max_lon_e7, `${path}.max_lon_e7`, -MAX_LONGITUDE_E7, MAX_LONGITUDE_E7);
  const south = integer(value.min_lat_e7, `${path}.min_lat_e7`, -MAX_LATITUDE_E7, MAX_LATITUDE_E7);
  const north = integer(value.max_lat_e7, `${path}.max_lat_e7`, -MAX_LATITUDE_E7, MAX_LATITUDE_E7);
  const minZoom = integer(value.min_zoom, `${path}.min_zoom`, 0, MAX_ZOOM);
  const maxZoom = integer(value.max_zoom, `${path}.max_zoom`, 0, MAX_ZOOM);

  if (west > east || south > north) throw new TypeError(`${path} has inverted bounds`);
  if (minZoom > maxZoom) throw new TypeError(`${path} has an inverted zoom range`);
}

function assertMosaicHeader(value: unknown, path: string): asserts value is MosaicHeader {
  assertSliceHeader(value, path);
  integer(value.center_lon_e7, `${path}.center_lon_e7`, -MAX_LONGITUDE_E7, MAX_LONGITUDE_E7);
  integer(value.center_lat_e7, `${path}.center_lat_e7`, -MAX_LATITUDE_E7, MAX_LATITUDE_E7);
  integer(value.center_zoom, `${path}.center_zoom`, 0, MAX_ZOOM);
  integer(value.tile_type, `${path}.tile_type`, 0, 5);
  integer(value.tile_compression, `${path}.tile_compression`, 0, 4);
}

function assertMetadata(value: unknown, path: string): asserts value is MosaicMetadata {
  assertObject(value, path);
  if (!TILE_FORMATS.some((format) => format === value.format)) {
    throw new TypeError(`${path}.format must be a supported tile format`);
  }
  for (const key of ["name", "description", "attribution", "version", "type"]) {
    if (value[key] !== undefined && typeof value[key] !== "string") {
      throw new TypeError(`${path}.${key} must be a string`);
    }
  }

  if (value.vector_layers === undefined) return;
  if (!Array.isArray(value.vector_layers)) {
    throw new TypeError(`${path}.vector_layers must be an array`);
  }
  for (const [index, layer] of value.vector_layers.entries()) {
    assertVectorLayer(layer, `${path}.vector_layers[${index}]`);
  }
}

function assertVectorLayer(value: unknown, path: string): asserts value is VectorLayer {
  assertObject(value, path);
  if (typeof value.id !== "string") throw new TypeError(`${path}.id must be a string`);
  for (const key of ["minzoom", "maxzoom"]) {
    if (value[key] !== undefined) {
      integer(value[key], `${path}.${key}`, 0, MAX_ZOOM);
    }
  }

  if (value.fields === undefined) return;
  assertObject(value.fields, `${path}.fields`);
  if (Object.values(value.fields).some((field) => typeof field !== "string")) {
    throw new TypeError(`${path}.fields values must be strings`);
  }
}

function assertObject(value: unknown, path: string): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${path} must be an object`);
  }
}

function integer(value: unknown, path: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new TypeError(`${path} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function deepFreeze(value: unknown): void {
  if (value === null || typeof value !== "object" || Object.isFrozen(value)) return;
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
}
