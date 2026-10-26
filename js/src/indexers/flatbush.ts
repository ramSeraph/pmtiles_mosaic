import Flatbush from "flatbush";
import type { SliceIndexFactory } from "../types.js";

/**
 * Optional plugin owning both Flatbush construction and lookup. Import from
 * pmtiles-mosaic/flatbush and pass as createIndex; install flatbush separately.
 */
export const createFlatbushIndex: SliceIndexFactory = (slices) => {
  if (slices.length === 0) return { search: () => [] };
  const index = new Flatbush(slices.length, 16, Int32Array);
  for (const { header: h } of slices) {
    index.add(h.min_lon_e7, h.min_lat_e7, h.max_lon_e7, h.max_lat_e7);
  }
  index.finish();
  return { search: (bounds) => index.search(...bounds) };
};
