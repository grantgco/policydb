/**
 * Manual Chart Library — Shared Editor Helpers
 *
 * Common functions used across all 8 manual chart template editors.
 * Everything is namespaced under window.ManualChart.
 *
 * Dependencies: vanilla JS + Chart.js (for export only)
 */

window.ManualChart = {};

// ---------------------------------------------------------------------------
// 1. Marsh Brand Color Palette
// ---------------------------------------------------------------------------

ManualChart.COLORS = {
  midnight:    '#000F47',
  sky:         '#CEECFF',
  blue1000:    '#000F47',
  blue500:     '#82BAFF',
  blue250:     '#CEECFF',
  green1000:   '#2F7500',
  green750:    '#6ABF30',
  green500:    '#B0DC92',
  purple1000:  '#5E017F',
  purple750:   '#8F20DE',
  gold1000:    '#CB7E03',
  gold750:     '#FFBF00',
  gold500:     '#FFD98A',
  red:         '#c8102e',
  teal:        '#0e8c79',
  neutral1000: '#3D3C37',
  neutral750:  '#7B7974',
  neutral500:  '#B9B6B1',
  neutral250:  '#F7F3EE',
  active:      '#0B4BFF',
  white:       '#FFFFFF',
};

/** Data color order for multi-series charts (use these in sequence) */
ManualChart.DATA_COLORS = [
  '#000F47', '#2F7500', '#5E017F', '#CB7E03',
  '#82BAFF', '#6ABF30', '#8F20DE', '#FFBF00',
];

// ---------------------------------------------------------------------------
// 2. Formatters
// ---------------------------------------------------------------------------

/**
 * Format a number as a currency string.
 *   $1.23M  for millions
 *   $500K   for thousands
 *   $1,234  for smaller values
 * Handles negatives with a leading minus sign.
 */
ManualChart.fmtCurrency = function (n) {
  if (n == null || isNaN(n)) return '$0';
  var neg = n < 0;
  var abs = Math.abs(n);
  var str;
  if (abs >= 1e6) {
    str = '$' + (abs / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
  } else if (abs >= 1e3) {
    str = '$' + (abs / 1e3).toFixed(0).replace(/\.?0+$/, '') + 'K';
  } else {
    str = '$' + abs.toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  return neg ? '-' + str : str;
};

/**
 * Format a number as a percentage with sign.
 *   +5.2%  or  -3.1%
 */
ManualChart.fmtPct = function (n) {
  if (n == null || isNaN(n)) return '+0.0%';
  var sign = n >= 0 ? '+' : '';
  return sign + n.toFixed(1) + '%';
};

/**
 * Format a rate to 3 decimal places.
 *   0.450
 */
ManualChart.fmtRate = function (n) {
  if (n == null || isNaN(n)) return '0.000';
  return Number(n).toFixed(3);
};

/**
 * Calculate percent change between current and baseline.
 * Returns a formatted string like +5.2% or -3.1%.
 */
ManualChart.pctChange = function (current, baseline) {
  if (!baseline || isNaN(baseline) || isNaN(current)) return '+0.0%';
  var delta = ((current - baseline) / Math.abs(baseline)) * 100;
  return ManualChart.fmtPct(delta);
};

/**
 * Return a CSS class name based on the sign of a delta value.
 *   'green' for negative or zero (cost savings / favorable)
 *   'red'   for positive (cost increase / unfavorable)
 */
ManualChart.deltaClass = function (delta) {
  return (delta <= 0) ? 'green' : 'red';
};

// ---------------------------------------------------------------------------
// 3. Row Management
// ---------------------------------------------------------------------------

/**
 * Append a new input row to a grid element.
 *
 * @param {HTMLElement} gridEl     - Container element that holds input rows.
 * @param {Function}    templateFn - Function(index) that returns an HTML string
 *                                   for one row.
 * @param {number}      index     - The zero-based index for the new row.
 */
ManualChart.addRow = function (gridEl, templateFn, index) {
  var html = templateFn(index);
  gridEl.insertAdjacentHTML('beforeend', html);
  // Re-index all row labels (elements with class 'row-label' inside .input-row)
  var rows = gridEl.querySelectorAll('.input-row');
  rows.forEach(function (row, i) {
    var label = row.querySelector('.row-label');
    if (label) label.textContent = (i + 1);
  });
};

/**
 * Remove the closest .input-row ancestor of the clicked button.
 *
 * @param {HTMLElement} btn - The remove button that was clicked.
 */
ManualChart.removeRow = function (btn) {
  var row = btn.closest('.input-row');
  if (row) row.remove();
};

// ---------------------------------------------------------------------------
// 4. State Collection / Population
// ---------------------------------------------------------------------------

/**
 * Read all inputs and selects within an editor element into a flat object.
 *
 * - Keyed by the element's `name` attribute (falls back to `id`).
 * - If multiple elements share the same name, their values are collected
 *   into an array.
 * - Checkboxes store a boolean value.
 *
 * @param  {HTMLElement} editorEl - The editor container element.
 * @return {Object}               JSON-serializable data object.
 */
ManualChart.collectAll = function (editorEl) {
  var data = {};
  var els = editorEl.querySelectorAll('input, select, textarea');
  els.forEach(function (el) {
    var key = el.name || el.id;
    if (!key) return;

    var val;
    if (el.type === 'checkbox') {
      val = el.checked;
    } else if (el.type === 'number' || el.type === 'range') {
      val = el.value === '' ? null : Number(el.value);
    } else {
      val = el.value;
    }

    // If we already have a value for this key, convert to array
    if (key in data) {
      if (!Array.isArray(data[key])) {
        data[key] = [data[key]];
      }
      data[key].push(val);
    } else {
      data[key] = val;
    }
  });
  return data;
};

/**
 * Fill inputs and selects within an editor element from a data object.
 * Inverse of collectAll.
 *
 * @param {HTMLElement} editorEl - The editor container element.
 * @param {Object}      data    - Key/value pairs to populate.
 */
ManualChart.populateAll = function (editorEl, data) {
  if (!data) return;

  // Track array indices so we can fill same-name fields in order
  var arrayIdx = {};

  var els = editorEl.querySelectorAll('input, select, textarea');
  els.forEach(function (el) {
    var key = el.name || el.id;
    if (!key || !(key in data)) return;

    var val = data[key];

    // Handle array values — assign sequentially to same-name elements
    if (Array.isArray(val)) {
      if (!(key in arrayIdx)) arrayIdx[key] = 0;
      var i = arrayIdx[key];
      if (i < val.length) {
        val = val[i];
        arrayIdx[key] = i + 1;
      } else {
        return; // no more values for this key
      }
    }

    if (el.type === 'checkbox') {
      el.checked = !!val;
    } else {
      el.value = (val == null) ? '' : val;
    }
  });
};

// ---------------------------------------------------------------------------
// 5. Snapshot API
// ---------------------------------------------------------------------------

/**
 * Save a snapshot (POST).
 *
 * @param  {string} chartType  - Chart type identifier.
 * @param  {string} name       - Human-readable snapshot name.
 * @param  {Object} data       - Serializable data object.
 * @return {Promise}
 */
ManualChart.saveSnapshot = function (chartType, name, data) {
  return fetch('/charts/manual/snapshots/' + encodeURIComponent(chartType), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name, data: data }),
  }).then(function (r) {
    if (!r.ok) throw new Error('Save failed: ' + r.status);
    return r.json();
  });
};

/**
 * Load a single snapshot (GET).
 *
 * @param  {string}        chartType  - Chart type identifier.
 * @param  {string|number} snapshotId - Snapshot ID.
 * @return {Promise}                   Resolves with parsed JSON.
 */
ManualChart.loadSnapshot = function (chartType, snapshotId) {
  var url = '/charts/manual/snapshots/' +
    encodeURIComponent(chartType) + '/' +
    encodeURIComponent(snapshotId);
  return fetch(url).then(function (r) {
    if (!r.ok) throw new Error('Load failed: ' + r.status);
    return r.json();
  });
};

/**
 * List all snapshots for a chart type (GET).
 *
 * @param  {string} chartType - Chart type identifier.
 * @return {Promise}           Resolves with an array of snapshot objects.
 */
ManualChart.listSnapshots = function (chartType) {
  return fetch('/charts/manual/snapshots/' + encodeURIComponent(chartType))
    .then(function (r) {
      if (!r.ok) throw new Error('List failed: ' + r.status);
      return r.json();
    });
};

/**
 * Delete a snapshot (DELETE).
 *
 * @param  {string}        chartType  - Chart type identifier.
 * @param  {string|number} snapshotId - Snapshot ID.
 * @return {Promise}
 */
ManualChart.deleteSnapshot = function (chartType, snapshotId) {
  var url = '/charts/manual/snapshots/' +
    encodeURIComponent(chartType) + '/' +
    encodeURIComponent(snapshotId);
  return fetch(url, { method: 'DELETE' }).then(function (r) {
    if (!r.ok) throw new Error('Delete failed: ' + r.status);
    return r.json();
  });
};

/**
 * Fetch the snapshot list and populate a <select> element with <option>s.
 *
 * @param {HTMLSelectElement} selectEl  - The dropdown to populate.
 * @param {string}            chartType - Chart type identifier.
 */
ManualChart.updateSnapshotDropdown = function (selectEl, chartType) {
  ManualChart.listSnapshots(chartType).then(function (items) {
    // Clear existing options except the first placeholder
    while (selectEl.options.length > 1) {
      selectEl.remove(1);
    }
    // If there is no placeholder, add one
    if (selectEl.options.length === 0) {
      var placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '— Select snapshot —';
      selectEl.appendChild(placeholder);
    }
    items.forEach(function (snap) {
      var opt = document.createElement('option');
      opt.value = snap.id;
      opt.textContent = snap.name || ('Snapshot ' + snap.id);
      selectEl.appendChild(opt);
    });
  }).catch(function (err) {
    console.error('Failed to load snapshots:', err);
  });
};

// ---------------------------------------------------------------------------
// 6. Export
// ---------------------------------------------------------------------------

/**
 * Export a Chart.js chart instance as a PNG download.
 *
 * @param {Object} chartInstance - A Chart.js chart object.
 * @param {string} [filename]   - Download filename (default: 'chart.png').
 */
/**
 * Export size presets — width × height in pixels.
 *   small  — email-friendly (480×270)
 *   medium — slide insert   (960×540)
 *   large  — full-slide     (1920×1080)
 */
ManualChart.EXPORT_SIZES = {
  small:  { w: 480,  h: 270,  label: 'Small (480×270)' },
  medium: { w: 960,  h: 540,  label: 'Medium (960×540)' },
  large:  { w: 1920, h: 1080, label: 'Large (1920×1080)' },
};

/**
 * Export chart as PNG at a specific size.
 *
 * @param {Chart}  chartInstance - The Chart.js instance to export.
 * @param {string} [filename]   - Download filename (default: 'chart.png').
 * @param {string} [size]       - 'small', 'medium', or 'large' (default: 'large').
 */
ManualChart.exportPng = function (chartInstance, filename, size) {
  if (!chartInstance) {
    console.error('exportPng: no chart instance provided');
    return;
  }

  size = size || 'large';
  var preset = ManualChart.EXPORT_SIZES[size] || ManualChart.EXPORT_SIZES.large;

  // Create an offscreen canvas at the target size
  var offCanvas = document.createElement('canvas');
  offCanvas.width = preset.w * 2;   // 2x for retina quality
  offCanvas.height = preset.h * 2;

  // Clone chart config and render to offscreen canvas
  var config = JSON.parse(JSON.stringify(chartInstance.config));
  // Preserve plugin references (JSON.parse strips functions)
  config.plugins = chartInstance.config._config ? chartInstance.config._config.plugins : [];
  config.options = config.options || {};
  config.options.responsive = false;
  config.options.animation = false;

  var offChart = new Chart(offCanvas.getContext('2d'), config);

  // Give it a tick to render, then export
  setTimeout(function () {
    var dataUrl = offCanvas.toDataURL('image/png', 1);
    offChart.destroy();

    var a = document.createElement('a');
    a.href = dataUrl;
    var sizeLabel = size === 'large' ? '' : '_' + size;
    a.download = (filename || 'chart').replace('.png', '') + sizeLabel + '.png';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, 100);
};

/**
 * Show export size picker dropdown near the export button.
 * Each option triggers exportPng with the selected size.
 */
ManualChart.showExportMenu = function (chartInstance, filename, anchorEl) {
  // Remove any existing menu
  var old = document.getElementById('mc-export-menu');
  if (old) { old.remove(); return; }

  var menu = document.createElement('div');
  menu.id = 'mc-export-menu';
  menu.style.cssText = 'position:absolute;right:0;top:100%;margin-top:4px;background:#fff;border:1px solid #B9B6B1;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,0.1);z-index:100;min-width:180px;';

  Object.keys(ManualChart.EXPORT_SIZES).forEach(function (key) {
    var preset = ManualChart.EXPORT_SIZES[key];
    var btn = document.createElement('button');
    btn.textContent = preset.label;
    btn.style.cssText = 'display:block;width:100%;text-align:left;padding:8px 14px;border:none;background:none;font-family:"Noto Sans",sans-serif;font-size:13px;color:#3D3C37;cursor:pointer;';
    btn.onmouseover = function () { btn.style.background = '#F7F3EE'; };
    btn.onmouseout = function () { btn.style.background = 'none'; };
    btn.onclick = function () {
      menu.remove();
      ManualChart.exportPng(chartInstance, filename, key);
    };
    menu.appendChild(btn);
  });

  // Position relative to anchor
  var wrapper = anchorEl.parentElement;
  wrapper.style.position = 'relative';
  wrapper.appendChild(menu);

  // Close on outside click
  setTimeout(function () {
    document.addEventListener('click', function handler(e) {
      if (!menu.contains(e.target) && e.target !== anchorEl) {
        menu.remove();
        document.removeEventListener('click', handler);
      }
    });
  }, 10);
};

// ---------------------------------------------------------------------------
// 7. Toast Notification
// ---------------------------------------------------------------------------

/**
 * Show a small auto-dismissing toast notification at top-right.
 *
 * @param {string} message - Text to display.
 * @param {string} [type]  - 'success' (green) or 'error' (red). Default: success.
 */
ManualChart.toast = function (message, type) {
  type = type || 'success';

  var bg = type === 'error' ? '#c8102e' : '#2F7500';
  var div = document.createElement('div');
  div.textContent = message;
  div.style.cssText = [
    'position:fixed',
    'top:1rem',
    'right:1rem',
    'z-index:9999',
    'padding:0.75rem 1.25rem',
    'border-radius:0.375rem',
    'color:#fff',
    'background:' + bg,
    'font-family:Noto Sans, sans-serif',
    'font-size:0.875rem',
    'box-shadow:0 4px 12px rgba(0,0,0,0.15)',
    'opacity:0',
    'transition:opacity 0.3s ease',
    'pointer-events:none',
  ].join(';');

  document.body.appendChild(div);

  // Fade in
  requestAnimationFrame(function () {
    div.style.opacity = '1';
  });

  // Auto-dismiss after 3 seconds
  setTimeout(function () {
    div.style.opacity = '0';
    setTimeout(function () {
      if (div.parentNode) div.parentNode.removeChild(div);
    }, 300);
  }, 3000);
};
