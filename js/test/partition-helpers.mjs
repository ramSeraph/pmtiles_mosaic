import { tileBoundsE7 } from "@ramseraph/pmtiles-mosaic";
import { mosaic, random, slice } from "./helpers.mjs";

/** Uneven, tile-aligned recursive x/y cuts, with shuffled manifest order. */
export function partitionedMosaic(count, layout = "recursive", seed = 1234567) {
  const rand = random(seed);
  const zoom = 12;
  const width = 2 ** zoom;
  let rectangles;
  if (layout === "stripes") {
    rectangles = Array.from({ length: count }, (_, i) =>
      [Math.floor(i / count * width), 0, Math.floor((i + 1) / count * width), width]);
  } else {
    rectangles = [[0, 0, width, width]];
    while (rectangles.length < count) {
      let selected = 0, largest = -1;
      for (let i = 0; i < rectangles.length; i++) {
        const [x0, y0, x1, y1] = rectangles[i];
        const area = (x1 - x0) * (y1 - y0);
        if (area > largest) { selected = i; largest = area; }
      }
      const a = rectangles[selected];
      const b = [...a];
      const axis = a[2] - a[0] <= 1 ? 1 : a[3] - a[1] <= 1 ? 0 : rand() < 0.5 ? 0 : 1;
      const cut = a[axis] + 1 + Math.floor(rand() * (a[axis + 2] - a[axis] - 1));
      b[axis] = cut;
      a[axis + 2] = cut;
      rectangles.push(b);
    }
  }
  const entries = rectangles.map(([x0, y0, x1, y1], i) => {
    const southwest = tileBoundsE7(zoom, x0, y1 - 1);
    const northeast = tileBoundsE7(zoom, x1 - 1, y0);
    return [`part-${i}.pmtiles`, slice([southwest[0], southwest[1], northeast[2], northeast[3]], zoom, zoom)];
  });
  for (let i = entries.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [entries[i], entries[j]] = [entries[j], entries[i]];
  }
  return mosaic(Object.fromEntries(entries));
}
