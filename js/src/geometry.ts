import type { BoundsE7, SliceHeader } from "./types.js";

export function validateTile(z: number, x: number, y: number): void {
  if (!Number.isInteger(z) || z < 0 || z > 26 ||
      !Number.isInteger(x) || !Number.isInteger(y) ||
      x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) {
    throw new RangeError("Expected integer XYZ coordinates with 0 <= z <= 26 and 0 <= x,y < 2^z");
  }
}

/** Web Mercator XYZ bounds, truncated to E7 like the Python mosaic writers. */
export function tileBoundsE7(z: number, x: number, y: number): BoundsE7 {
  validateTile(z, x, y);
  const scale = 2 ** z;
  const latitude = (row: number): number =>
    Math.atan(Math.sinh(Math.PI * (1 - 2 * row / scale))) * 180 / Math.PI;
  return [
    Math.trunc((x / scale * 360 - 180) * 1e7),
    Math.trunc(latitude(y + 1) * 1e7),
    Math.trunc(((x + 1) / scale * 360 - 180) * 1e7),
    Math.trunc(latitude(y) * 1e7),
  ];
}

export function intersects(header: SliceHeader, bounds: BoundsE7): boolean {
  return header.min_lon_e7 <= bounds[2] && header.max_lon_e7 >= bounds[0] &&
    header.min_lat_e7 <= bounds[3] && header.max_lat_e7 >= bounds[1];
}
