/**
 * Type definitions for Pyodide and METAMEQ bridge
 */

// Pyodide types (minimal definitions for our use case)
export interface PyodideInterface {
  loadPackage: (packages: string | string[]) => Promise<void>;
  runPythonAsync: (code: string) => Promise<unknown>;
  globals: PyProxy;
  toPy: (value: unknown) => PyProxy;
  FS: {
    writeFile: (path: string, data: Uint8Array | string) => void;
    readFile: (path: string, opts?: { encoding: string }) => string | Uint8Array;
    mkdir: (path: string) => void;
    unlink: (path: string) => void;
  };
}

export interface PyProxy {
  toJs: (options?: { dict_converter?: typeof Object.fromEntries }) => unknown;
  get: (key: string) => unknown;
  set: (key: string, value: unknown) => void;
  destroy: () => void;
}

export interface MicropipInterface {
  install: (packages: string | string[]) => Promise<void>;
}

// Load state for Pyodide initialization
export type PyodideLoadState =
  | 'idle'
  | 'loading-core'
  | 'installing-packages'
  | 'ready'
  | 'error';

// How one axis (host or sample type) is resolved: read an existing column, or
// apply one value to every row. The type-resolution modal produces this.
export type AxisResolution = { column: string } | { value: string };
export interface TypeResolution {
  host?: AxisResolution;
  sample?: AxisResolution;
}

// How an axis was resolved on a run, for display. `mode` 'present'/'inferred' is
// automatic; 'column'/'value' is the modal's answer.
export interface ResolvedAxisContext {
  mode: 'present' | 'inferred' | 'column' | 'value';
  column?: string;
  value?: string;
}

// The config's host/sample type vocabulary, for the resolution modal's dropdowns.
export interface TypeTaxonomy {
  hostTypes: string[];
  sampleTypesByHost: Record<string, string[]>;
  hostOfSampleType: Record<string, string[]>;
  hostColumn: string;
  sampleColumn: string;
}

// METAMEQ validation result types
export interface MetameqValidationResult {
  success: boolean;
  processed: boolean;  // True if metameq actually ran and processed data
  errors: { message: string; severity: string }[];  // Only for Python exceptions
  extendedMetadata: Record<string, unknown>[] | null;
  validationErrors: Record<string, unknown>[] | null;  // Combined validation errors + QC failures
  originalColumns: string[] | null;  // Columns from input before metameq extension
  // Host/sample type could not be resolved (from the modal or inference): the
  // caller prompts with `taxonomy` + `originalColumns` and re-validates.
  needsTypeResolution: boolean;
  unresolved: ('host' | 'sample')[];
  taxonomy: TypeTaxonomy | null;
  resolvedContext: { host: ResolvedAxisContext | null; sample: ResolvedAxisContext | null } | null;
  stdout?: string;
  stderr?: string;
  summary: {
    totalRows: number;
    validRows: number;
    errorCount: number;
  };
}

declare global {
  interface Window {
    loadPyodide?: (options?: { indexURL?: string }) => Promise<PyodideInterface>;
  }
}
