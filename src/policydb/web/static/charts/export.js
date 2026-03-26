/**
 * Chart Deck Builder — Export Logic
 * SVG → html2canvas → PNG
 * HTML → html2canvas → PNG
 * All → JSZip → ZIP download
 */

function exportSvgToPng(chartPageEl, filename) {
  var svgEl = chartPageEl.querySelector('svg');
  if (!svgEl) { alert('No SVG found to export'); return Promise.reject('No SVG'); }

  // Clone the chart page for clean export
  var clone = chartPageEl.cloneNode(true);
  // Remove export buttons from clone
  clone.querySelectorAll('.no-print, .chart-export-btn').forEach(function(el) { el.remove(); });

  return new Promise(function(resolve, reject) {
    // Create a temporary container off-screen
    var container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-9999px;top:0;width:960px;height:540px;background:white;';
    document.body.appendChild(container);
    container.appendChild(clone);

    // Use html2canvas on the entire chart page (handles both SVG text and HTML elements)
    html2canvas(container, {
      width: 960,
      height: 540,
      scale: 2,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false
    }).then(function(canvas) {
      document.body.removeChild(container);
      canvas.toBlob(function(blob) {
        if (filename) {
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = filename;
          a.click();
          URL.revokeObjectURL(a.href);
        }
        resolve(blob);
      }, 'image/png');
    }).catch(function(err) {
      document.body.removeChild(container);
      reject(err);
    });
  });
}

function exportHtmlToPng(chartPageEl, filename) {
  var clone = chartPageEl.cloneNode(true);
  clone.querySelectorAll('.no-print, .chart-export-btn').forEach(function(el) { el.remove(); });

  return new Promise(function(resolve, reject) {
    var container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-9999px;top:0;width:960px;height:540px;background:white;';
    document.body.appendChild(container);
    container.appendChild(clone);

    html2canvas(container, {
      width: 960,
      height: 540,
      scale: 2,
      backgroundColor: '#ffffff',
      logging: false
    }).then(function(canvas) {
      document.body.removeChild(container);
      canvas.toBlob(function(blob) {
        if (filename) {
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = filename;
          a.click();
          URL.revokeObjectURL(a.href);
        }
        resolve(blob);
      }, 'image/png');
    }).catch(function(err) {
      document.body.removeChild(container);
      reject(err);
    });
  });
}

function exportChartToPng(chartId, filename) {
  var el = document.getElementById('chart-' + chartId);
  if (!el) return Promise.reject('Chart not found');
  var type = el.dataset.chartType || 'html';
  if (type === 'd3') return exportSvgToPng(el, filename);
  return exportHtmlToPng(el, filename);
}

function exportAllToZip(chartIds, clientName) {
  var statusEl = document.getElementById('export-status');
  if (statusEl) statusEl.textContent = 'Exporting...';

  var zip = new JSZip();
  var promises = chartIds.map(function(id, i) {
    var el = document.getElementById('chart-' + id);
    if (!el) return Promise.resolve();
    // Make sure chart is rendered
    if (!el.dataset.rendered && window['render_' + id]) {
      el.style.display = 'block';
      window['render_' + id]();
      el.dataset.rendered = '1';
    }
    // Show it temporarily for export
    var wasHidden = el.style.display === 'none';
    el.style.display = 'block';

    var type = el.dataset.chartType || 'html';
    var fn = type === 'd3' ? exportSvgToPng : exportHtmlToPng;
    return fn(el, null).then(function(blob) {
      if (wasHidden) el.style.display = 'none';
      if (blob) {
        var num = String(i + 1).padStart(2, '0');
        var title = el.dataset.chartTitle || id;
        zip.file(num + '_' + title.replace(/[^a-zA-Z0-9]/g, '_') + '.png', blob);
      }
      if (statusEl) statusEl.textContent = 'Exported ' + (i + 1) + ' of ' + chartIds.length + '...';
    });
  });

  Promise.all(promises).then(function() {
    return zip.generateAsync({type: 'blob'});
  }).then(function(content) {
    var safeName = (clientName || 'charts').replace(/[^a-zA-Z0-9]/g, '_');
    var a = document.createElement('a');
    a.href = URL.createObjectURL(content);
    a.download = safeName + '_renewal_recap.zip';
    a.click();
    URL.revokeObjectURL(a.href);
    if (statusEl) statusEl.textContent = 'Done!';
    setTimeout(function() { if (statusEl) statusEl.textContent = ''; }, 2000);
  });
}
