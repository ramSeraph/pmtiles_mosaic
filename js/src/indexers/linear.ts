import { intersects } from "../geometry.js";
import type { SliceIndexFactory } from "../types.js";

/** Default index: scans the slices at a zoom without building a search structure. */
export const createLinearIndex: SliceIndexFactory = (slices) => ({
  search(bounds) {
    const found: number[] = [];
    for (let i = 0; i < slices.length; i++) {
      if (intersects(slices[i]!.header, bounds)) found.push(i);
    }
    return found;
  },
});
