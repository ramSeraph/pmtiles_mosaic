import type { BoundsE7, ResolvedSlice, SliceIndexFactory } from "./types.js";

type Axis = 0 | 1;
// Leaves store the slice's original index so manifest ordering stays caller-owned.
type Node = number | {
  readonly axis: Axis;
  readonly leftMax: number;
  readonly rightMin: number;
  readonly left: Node;
  readonly right: Node;
};

interface Cut {
  readonly axis: Axis;
  readonly position: number;
  readonly imbalance: number;
  readonly leftMax: number;
  readonly rightMin: number;
}

/**
 * Optional partition-aware index for recursive, axis-aligned slice bounds.
 * Import from pmtiles-mosaic/partition-aware and pass as createIndex.
 *
 * Reconstructs x/y cuts from the slices at each queried zoom. Adjacent bounds
 * may touch; searches include both sides of a touched boundary. Overlapping or
 * otherwise inseparable layouts throw during index construction: choose
 * createLinearIndex or another index for those mosaics. There is no fallback.
 * Requires no partition metadata or additional dependency.
 */
export const createPartitionAwareIndex: SliceIndexFactory = (slices) => {
  if (slices.length === 0) return { search: () => [] };

  const min = (id: number, axis: Axis): number => axis === 0
    ? slices[id]!.header.min_lon_e7 : slices[id]!.header.min_lat_e7;
  const max = (id: number, axis: Axis): number => axis === 0
    ? slices[id]!.header.max_lon_e7 : slices[id]!.header.max_lat_e7;
  const ids = slices.map((_, i) => i);
  const byX = [...ids].sort((a, b) => min(a, 0) - min(b, 0) || a - b);
  const byY = [...ids].sort((a, b) => min(a, 1) - min(b, 1) || a - b);
  const membership = new Uint32Array(slices.length);
  let marker = 0;

  function findCut(order: readonly number[], axis: Axis): Cut | undefined {
    let leftMax = -Infinity;
    let best: Cut | undefined;
    for (let i = 0; i < order.length - 1; i++) {
      // A cut must clear every rectangle on its left, not just the last one.
      leftMax = Math.max(leftMax, max(order[i]!, axis));
      const rightMin = min(order[i + 1]!, axis);
      if (leftMax > rightMin) continue;
      const position = i + 1;
      const imbalance = Math.abs(order.length - 2 * position);
      if (!best || imbalance < best.imbalance) {
        best = { axis, position, imbalance, leftMax, rightMin };
      }
    }
    return best;
  }

  function build(x: readonly number[], y: readonly number[]): Node {
    if (x.length === 1) return x[0]!;
    const cutX = findCut(x, 0);
    const cutY = findCut(y, 1);
    const cut = cutX && (!cutY || cutX.imbalance <= cutY.imbalance) ? cutX : cutY;
    if (!cut) {
      throw new Error("Slice bounds do not form recursive x/y partitions; use createLinearIndex or another index");
    }
    const ordered = cut.axis === 0 ? x : y;
    const other = cut.axis === 0 ? y : x;
    const left = ordered.slice(0, cut.position);
    const right = ordered.slice(cut.position);
    const currentMarker = ++marker;
    for (const id of left) membership[id] = currentMarker;
    const otherLeft: number[] = [];
    const otherRight: number[] = [];
    // Preserve the other axis's ordering instead of sorting again at each node.
    for (const id of other) {
      (membership[id] === currentMarker ? otherLeft : otherRight).push(id);
    }
    return {
      axis: cut.axis, leftMax: cut.leftMax, rightMin: cut.rightMin,
      left: cut.axis === 0 ? build(left, otherLeft) : build(otherLeft, left),
      right: cut.axis === 0 ? build(right, otherRight) : build(otherRight, right),
    };
  }

  const root = build(byX, byY);
  const extent = sliceExtent(slices);
  return {
    search(bounds) {
      if (bounds[0] > extent[2] || bounds[2] < extent[0] ||
          bounds[1] > extent[3] || bounds[3] < extent[1]) return [];
      const found: number[] = [];
      const pending: Node[] = [];
      let node: Node | undefined = root;
      while (node !== undefined) {
        if (typeof node === "number") {
          found.push(node);
          node = pending.pop();
        } else if (bounds[node.axis] <= node.leftMax) {
          if (bounds[node.axis + 2]! >= node.rightMin) pending.push(node.right);
          node = node.left;
        } else if (bounds[node.axis + 2]! >= node.rightMin) {
          node = node.right;
        } else {
          node = pending.pop();
        }
      }
      return found;
    },
  };
};

function sliceExtent(slices: readonly ResolvedSlice[]): BoundsE7 {
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
  for (const { header: h } of slices) {
    west = Math.min(west, h.min_lon_e7);
    south = Math.min(south, h.min_lat_e7);
    east = Math.max(east, h.max_lon_e7);
    north = Math.max(north, h.max_lat_e7);
  }
  return [west, south, east, north];
}
