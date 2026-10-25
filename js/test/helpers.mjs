export function slice(bounds = [-1800000000, -850511287, 1800000000, 850511287], min = 0, max = 26) {
  return { header: {
    min_lon_e7: bounds[0], min_lat_e7: bounds[1],
    max_lon_e7: bounds[2], max_lat_e7: bounds[3], min_zoom: min, max_zoom: max,
  } };
}

export function mosaic(slices = { "map.pmtiles": slice() }) {
  return {
    version: 1,
    header: {
      ...slice().header, center_lon_e7: 0, center_lat_e7: 0, center_zoom: 0,
      tile_type: 1, tile_compression: 2,
    },
    metadata: { format: "pbf", name: "Test map", attribution: "Example attribution" },
    slices,
  };
}

export function random(seed = 1234567) {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 2 ** 32;
  };
}
