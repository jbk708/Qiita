// Lazy, worker-based metameq validator and PCoA — the Svelte port of kl-logger's
// usePyodide hook. Pyodide runs in a Web Worker (static/pyodide-worker.js) off the
// main thread, one per page, booted on the first analysis; each analysis then
// installs its own packages once (PACKAGE_SETS), so neither pays for the other.
import { PYODIDE_CDN_URL, CONFIG_PRESETS, PACKAGE_SETS, type PackageSet } from './config';
import type { MetameqValidationResult, PyodideLoadState, TypeResolution } from './types';

type Pending = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  resolve: (r: any) => void;
  reject: (e: Error) => void;
};

/** A PERMANOVA of the last PCoA's distances (static/ordination.py). */
export type Permanova = {
  method: string;
  test_statistic_name: string;
  sample_size: number;
  number_of_groups: number;
  excluded: number;
  permutations: number;
  test_statistic?: number;
  p_value?: number;
  /** Why there is no test (one group, or one group per sample). */
  skipped?: string;
};

/** A feature table's samples on their leading principal coordinates
 *  (static/ordination.py). */
export type Pcoa = {
  samples: string[];
  axes: number[][];
  proportion_explained: number[];
  n_features: number;
  n_samples: number;
  /** The rarefaction depth, or null for relative abundance. */
  depth: number | null;
  /** Every sample's total count in the table, rarefied or not; empty for a table
   *  of relative abundances, which cannot be rarefied. */
  depths: number[];
  /** Samples below the rarefaction depth, left out. */
  dropped: string[];
};

class MetameqValidator {
  loadState = $state<PyodideLoadState>('idle');
  loadProgress = $state(0);
  error = $state<string | null>(null);
  /** Package sets installed in the worker. */
  installed = $state<PackageSet[]>([]);

  #worker: Worker | null = null;
  #booted = false;
  #pending = new Map<string, Pending>();
  #readyWaiters: Array<{ resolve: () => void; reject: (e: Error) => void }> = [];
  #configCache: string | null = null;

  get isReady() {
    return this.loadState === 'ready';
  }

  /** Whether `set`'s packages are installed, so its next call starts at once. */
  has(set: PackageSet) {
    return this.installed.includes(set);
  }

  #ensureWorker() {
    if (this.#worker) return;
    const w = new Worker('/pyodide-worker.js');
    w.onmessage = (e) => this.#onMessage(e);
    w.onerror = (err) => this.#fail(`Worker error: ${err.message}`);
    this.#worker = w;
    this.loadState = 'loading-core';
    this.loadProgress = 5;
    this.error = null;
    w.postMessage({
      type: 'init',
      cdnUrl: PYODIDE_CDN_URL,
      sets: PACKAGE_SETS
    });
  }

  #fail(message: string) {
    this.error = message;
    this.loadState = 'error';
    for (const w of this.#readyWaiters) w.reject(new Error(message));
    this.#readyWaiters = [];
  }

  #onMessage(event: MessageEvent) {
    const { type, id, ...data } = event.data;
    if (type === 'progress') {
      this.loadState = data.state;
      this.loadProgress = data.progress;
    } else if (type === 'set-ready') {
      if (!this.installed.includes(data.set)) this.installed = [...this.installed, data.set];
    } else if (type === 'initialized') {
      // The core is up; the requested analysis's packages install next.
      if (data.success) {
        this.#booted = true;
        for (const w of this.#readyWaiters) w.resolve();
        this.#readyWaiters = [];
      }
    } else if (type === 'validation-result' || type === 'pcoa-result' || type === 'permanova-result') {
      this.#pending.get(id)?.resolve(data.result);
      this.#pending.delete(id);
    } else if (type === 'error') {
      if (id) {
        this.#pending.get(id)?.reject(new Error(data.message));
        this.#pending.delete(id);
      } else {
        this.#fail(data.message);
      }
    }
  }

  /** Boots the worker if needed; `set` names the packages the caller's analysis
   *  will install, so progress starts from there rather than the last install. */
  #ready(set: PackageSet): Promise<void> {
    if (!this.has(set)) {
      this.loadState = this.#booted ? 'installing-packages' : 'loading-core';
      this.loadProgress = this.#booted ? 50 : 5;
    }
    if (this.#booted) return Promise.resolve();
    this.#ensureWorker();
    return new Promise((resolve, reject) => this.#readyWaiters.push({ resolve, reject }));
  }

  async #config(): Promise<string> {
    if (this.#configCache) return this.#configCache;
    const r = await fetch(CONFIG_PRESETS.default);
    if (!r.ok) throw new Error(`metameq config failed to load (${r.status})`);
    this.#configCache = await r.text();
    return this.#configCache;
  }

  /** Validate a metadata file against the metameq standards. Lazily boots the
   *  worker + Pyodide on first call. Never throws for *validation* findings —
   *  those come back in the result; it throws only for infra failures.
   *
   *  `resolution` answers the type-resolution modal: which column, or which single
   *  value, supplies each row's host and sample type. Omit it on the first pass —
   *  the result's `needsTypeResolution` says when the modal is needed. */
  async validate(file: File, resolution?: TypeResolution | null): Promise<MetameqValidationResult> {
    await this.#ready('validation');
    const configYaml = await this.#config();
    const fileData = await file.arrayBuffer();
    const id = `v-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      this.#worker!.postMessage({
        type: 'validate',
        id,
        fileData,
        fileName: file.name,
        configYaml,
        resolution: resolution ? JSON.stringify(resolution) : null
      });
    });
  }

  /** Bray-Curtis PCoA of a sparse COO feature table (codes into `samples` and a
   *  feature space of `nFeatures`), with scikit-bio, rarefied to
   *  `depth` (null: relative abundance). Lazily boots the worker on first call. */
  async pcoa(
    table: { samples: string[]; features: string[]; s: Int32Array; f: Int32Array; c: Float64Array },
    depth: number | null = null
  ): Promise<Pcoa> {
    await this.#ready('ordination');
    const id = `p-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      this.#worker!.postMessage({
        type: 'pcoa',
        id,
        samples: table.samples,
        nFeatures: table.features.length,
        s: table.s,
        f: table.f,
        c: table.c,
        depth
      });
    });
  }

  /** PERMANOVA of the last PCoA's distances by `groups` (sample -> value; null
   *  leaves a sample out), as scikit-bio's permanova computes it. */
  async permanova(groups: Record<string, string | null>): Promise<Permanova> {
    await this.#ready('ordination');
    const id = `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      this.#worker!.postMessage({ type: 'permanova', id, groups });
    });
  }
}

export const metameq = new MetameqValidator();
/** The same worker, for analyses: one Pyodide per page. */
export const pyworker = metameq;
