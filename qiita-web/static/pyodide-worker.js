/**
 * Pyodide Web Worker
 * Runs Python code in a separate thread to avoid blocking the UI
 */

let pyodide = null;
// What each analysis installs on first use (PACKAGE_SETS in src/lib/pyodide/config.ts).
let packageSets = {};
// One install promise per set, so a set installs once however many calls race for
// it; installs run one after another, since loadPackage calls should not overlap.
const installs = {};
let installQueue = Promise.resolve();

// Send message to main thread
const sendMessage = (type, payload) => {
  self.postMessage({ type, ...payload });
};

// Boot the Pyodide core only; each analysis adds its packages (`ensure`).
const initializePyodide = async ({ cdnUrl, sets }) => {
  packageSets = sets ?? {};
  try {
    sendMessage('progress', { state: 'loading-core', progress: 10 });
    importScripts(`${cdnUrl}pyodide.js`);
    sendMessage('progress', { state: 'loading-core', progress: 20 });
    pyodide = await loadPyodide({ indexURL: cdnUrl });
    sendMessage('progress', { state: 'loading-core', progress: 50 });
    sendMessage('initialized', { success: true });
  } catch (err) {
    sendMessage('error', { message: err.message || 'Failed to initialize Pyodide' });
  }
};

const install = async (name) => {
  const set = packageSets[name];
  if (!set) throw new Error(`unknown package set ${JSON.stringify(name)}`);
  sendMessage('progress', { state: 'installing-packages', progress: 60, set: name });
  // Wheel URLs install as given, with no resolver: every requirement is listed.
  const wheels = set.wheels.map((w) => new URL(w, self.location.origin).href);
  await pyodide.loadPackage([...set.pyodide, ...wheels]);
  sendMessage('progress', { state: 'installing-packages', progress: 90, set: name });
  for (const path of set.code) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path} failed to load (${response.status})`);
    await pyodide.runPythonAsync(await response.text());
  }
  sendMessage('set-ready', { set: name });
  sendMessage('progress', { state: 'ready', progress: 100 });
};

const ensure = (name) => {
  if (!pyodide) throw new Error('Pyodide not initialized');
  if (!installs[name]) {
    const p = installQueue.then(() => install(name));
    // A failed install is not cached: the next call tries again.
    installs[name] = p.catch((err) => {
      delete installs[name];
      throw err;
    });
    installQueue = p.catch(() => {});
  }
  return installs[name];
};

// Write file to Pyodide filesystem
const writeFileToPyodide = (filename, data) => {
  try {
    pyodide.FS.mkdir('/tmp');
  } catch {
    // Directory may already exist
  }

  if (data instanceof ArrayBuffer) {
    pyodide.FS.writeFile(`/tmp/${filename}`, new Uint8Array(data));
  } else if (typeof data === 'string') {
    pyodide.FS.writeFile(`/tmp/${filename}`, data);
  }
};

// Run validation. `resolution` (optional) is the type-resolution modal's answer,
// a JSON string shaped {"host": {"column"|"value": ...}, "sample": {...}}.
const runValidation = async (fileData, fileName, configYaml, resolution) => {
  await ensure('validation');

  let fileExt = '.' + (fileName.split('.').pop()?.toLowerCase() || 'csv');
  if (fileExt === '.tsv') fileExt = '.txt';
  const dataFilename = `data_${Date.now()}${fileExt}`;
  const configFilename = `config_${Date.now()}.yml`;

  try {
    // Write files to Pyodide filesystem
    writeFileToPyodide(dataFilename, fileData);
    writeFileToPyodide(configFilename, configYaml);

    // Pass the resolution (if any) as a parsed dict, without string-splicing it
    // into the Python source.
    pyodide.globals.set('_resolution_json', resolution ?? 'null');

    // Run validation
    const resultProxy = await pyodide.runPythonAsync(
      `import json as _json\n` +
        `_r = run_metameq_validation('/tmp/${dataFilename}', '/tmp/${configFilename}', '${fileExt}', _json.loads(_resolution_json))\n` +
        `_r["review"] = review(_r, '/tmp/${dataFilename}', '/tmp/${configFilename}', '${fileExt}')\n` +
        `_r`
    );

    // Convert Python dict to JS object
    const result = resultProxy.toJs({ dict_converter: Object.fromEntries });
    resultProxy.destroy();

    // Clean up temp files
    try {
      pyodide.FS.unlink(`/tmp/${dataFilename}`);
      pyodide.FS.unlink(`/tmp/${configFilename}`);
    } catch {
      // Ignore cleanup errors
    }

    // Convert nested Maps to objects
    const convertMaps = (obj) => {
      if (obj instanceof Map) {
        const converted = {};
        for (const [key, value] of obj) {
          converted[key] = convertMaps(value);
        }
        return converted;
      }
      if (Array.isArray(obj)) {
        return obj.map(convertMaps);
      }
      if (obj && typeof obj === 'object') {
        const converted = {};
        for (const [key, value] of Object.entries(obj)) {
          converted[key] = convertMaps(value);
        }
        return converted;
      }
      return obj;
    };

    return convertMaps(result);
  } catch (err) {
    // Try to clean up on error
    try {
      pyodide.FS.unlink(`/tmp/${dataFilename}`);
      pyodide.FS.unlink(`/tmp/${configFilename}`);
    } catch {
      // Ignore cleanup errors
    }
    throw err;
  }
};

// PCoA and PERMANOVA (static/ordination.py), on scikit-bio.
let ordinationRan = false;
const runPcoa = async ({ samples, nFeatures, s, f, c, depth }) => {
  await ensure('ordination');
  // `undefined`, not `null`: Pyodide 0.28+ passes a JS null into Python as JsNull,
  // which is not None; undefined still arrives as None.
  const args = { samples, n_features: nFeatures, s, f, c, depth: depth ?? undefined };
  for (const [k, v] of Object.entries(args)) pyodide.globals.set(`_pcoa_${k}`, v);
  const proxy = await pyodide.runPythonAsync(
    'pcoa_coo(list(_pcoa_samples), _pcoa_n_features, _pcoa_s.to_py(), _pcoa_f.to_py(),' +
      ' _pcoa_c.to_py(), _pcoa_depth)'
  );
  const result = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  ordinationRan = true;
  for (const k of Object.keys(args)) pyodide.globals.delete(`_pcoa_${k}`);
  return result;
};

// PERMANOVA over the last PCoA's distances; `groups` maps sample name -> value.
const runPermanova = async (groups) => {
  if (!ordinationRan) throw new Error('no PCoA has been computed');
  // Missing values are left out, not sent as null: Pyodide 0.28+ would pass a JS
  // null as JsNull (not None), grouping the missing samples together. An absent
  // sample is what permanova() leaves out and counts.
  const present = Object.fromEntries(Object.entries(groups).filter(([, v]) => v != null));
  pyodide.globals.set('_perm_groups', pyodide.toPy(present));
  const proxy = await pyodide.runPythonAsync('permanova(_perm_groups)');
  const result = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  pyodide.globals.delete('_perm_groups');
  return result;
};

// Handle messages from main thread
self.onmessage = async (event) => {
  const { type, id, ...data } = event.data;

  try {
    switch (type) {
      case 'init':
        await initializePyodide({ cdnUrl: data.cdnUrl, sets: data.sets });
        break;

      case 'validate':
        const result = await runValidation(data.fileData, data.fileName, data.configYaml, data.resolution);
        sendMessage('validation-result', { id, result });
        break;

      case 'permanova':
        sendMessage('permanova-result', { id, result: await runPermanova(data.groups) });
        break;

      case 'pcoa':
        sendMessage('pcoa-result', { id, result: await runPcoa(data) });
        break;

      default:
        sendMessage('error', { id, message: `Unknown message type: ${type}` });
    }
  } catch (err) {
    sendMessage('error', { id, message: err.message || 'Worker error' });
  }
};
