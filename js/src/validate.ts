import type { MosaicDocument } from "./types.js";

function object(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function integer(value: unknown, path: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new TypeError(`${path} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function header(value: unknown, path: string): Record<string, unknown> {
  const h = object(value, path);
  const west = integer(h.min_lon_e7, `${path}.min_lon_e7`, -1800000000, 1800000000);
  const east = integer(h.max_lon_e7, `${path}.max_lon_e7`, -1800000000, 1800000000);
  const south = integer(h.min_lat_e7, `${path}.min_lat_e7`, -900000000, 900000000);
  const north = integer(h.max_lat_e7, `${path}.max_lat_e7`, -900000000, 900000000);
  const min = integer(h.min_zoom, `${path}.min_zoom`, 0, 26);
  const max = integer(h.max_zoom, `${path}.max_zoom`, 0, 26);
  if (west > east || south > north) {
    throw new TypeError(`${path} has inverted bounds`);
  }
  if (min > max) throw new TypeError(`${path} has an inverted zoom range`);
  return h;
}

function freeze(value: unknown): void {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
}

/** Validate and snapshot a version 1 mosaic so indexes cannot become stale through mutation. */
export function parseMosaic(value: unknown): MosaicDocument {
  const data = object(value, "mosaic");
  if (data.version !== 1) throw new TypeError("Unsupported mosaic format: version must be 1");
  const h = header(data.header, "mosaic.header");
  integer(h.center_lon_e7, "mosaic.header.center_lon_e7", -1800000000, 1800000000);
  integer(h.center_lat_e7, "mosaic.header.center_lat_e7", -900000000, 900000000);
  integer(h.center_zoom, "mosaic.header.center_zoom", 0, 26);
  integer(h.tile_type, "mosaic.header.tile_type", 0, 5);
  integer(h.tile_compression, "mosaic.header.tile_compression", 0, 4);

  const metadata = object(data.metadata, "mosaic.metadata");
  if (!["pbf", "mvt", "png", "jpg", "jpeg", "webp", "avif"].includes(metadata.format as string)) {
    throw new TypeError("mosaic.metadata.format must be a supported tile format");
  }
  for (const field of ["name", "description", "attribution", "version", "type"]) {
    if (metadata[field] !== undefined && typeof metadata[field] !== "string") {
      throw new TypeError(`mosaic.metadata.${field} must be a string`);
    }
  }
  if (metadata.vector_layers !== undefined) {
    if (!Array.isArray(metadata.vector_layers)) {
      throw new TypeError("mosaic.metadata.vector_layers must be an array");
    }
    for (const [i, value] of metadata.vector_layers.entries()) {
      const layer = object(value, `mosaic.metadata.vector_layers[${i}]`);
      if (typeof layer.id !== "string") {
        throw new TypeError(`mosaic.metadata.vector_layers[${i}].id must be a string`);
      }
      for (const field of ["minzoom", "maxzoom"]) {
        if (layer[field] !== undefined) {
          integer(layer[field], `mosaic.metadata.vector_layers[${i}].${field}`, 0, 26);
        }
      }
      if (layer.fields !== undefined) {
        const fields = object(layer.fields, `mosaic.metadata.vector_layers[${i}].fields`);
        if (Object.values(fields).some((field) => typeof field !== "string")) {
          throw new TypeError(`mosaic.metadata.vector_layers[${i}].fields values must be strings`);
        }
      }
    }
  }
  for (const [key, value] of Object.entries(object(data.slices, "mosaic.slices"))) {
    if (!key) throw new TypeError("mosaic.slices keys must not be empty");
    const slice = object(value, `mosaic.slices[${JSON.stringify(key)}]`);
    const sh = header(slice.header, `mosaic.slices[${JSON.stringify(key)}].header`);
    if ((sh.min_zoom as number) < (h.min_zoom as number) ||
        (sh.max_zoom as number) > (h.max_zoom as number)) {
      throw new TypeError(`mosaic.slices[${JSON.stringify(key)}] zoom range is outside mosaic.header`);
    }
  }
  const snapshot = structuredClone(data) as unknown as MosaicDocument;
  freeze(snapshot);
  return snapshot;
}
