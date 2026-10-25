import { validateTile } from "./geometry.js";
import { MosaicResolver } from "./resolver.js";
import type { MosaicClientOptions } from "./types.js";

/** Optional orchestration over caller-owned manifest loading and tile reading. */
export class MosaicClient<T> {
  private resolver?: MosaicResolver;
  private readonly options: MosaicClientOptions<T>;

  constructor(options: MosaicClientOptions<T>) {
    this.options = { ...options };
  }

  /**
   * Retains the first successfully loaded snapshot. Pending loads are not shared
   * across requests: cancellation and request-scoped I/O remain caller-owned.
   */
  async getResolver(signal?: AbortSignal): Promise<MosaicResolver> {
    signal?.throwIfAborted();
    if (this.resolver) return this.resolver;
    const document = await this.options.loadMosaic(signal);
    signal?.throwIfAborted();
    this.resolver ??= new MosaicResolver(document, this.options);
    return this.resolver;
  }

  /** Returns the first existing tile in manifest order, or undefined if absent. */
  async getTile(z: number, x: number, y: number, signal?: AbortSignal): Promise<T | undefined> {
    validateTile(z, x, y);
    const resolver = await this.getResolver(signal);
    for (const slice of resolver.resolve(z, x, y)) {
      signal?.throwIfAborted();
      const tile = await this.options.readTile(slice, z, x, y, signal);
      signal?.throwIfAborted();
      if (tile !== undefined) return tile;
    }
    return undefined;
  }
}
