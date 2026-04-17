/**
 * Chart Deck Builder — Shared D3 Utilities
 */
var ChartColors = {
  // Professional presentation palette (10 colors)
  palette: ['#003865','#2563eb','#0891b2','#059669','#d97706','#dc2626','#7c3aed','#db2777','#64748b','#78716c'],
  // Semantic colors
  prior: '#9ca3af',      // Gray for prior term
  current: '#003865',    // Marsh navy for current term
  increase: '#dc2626',   // Red for increases
  decrease: '#059669',   // Green for decreases
  get: function(i) { return this.palette[i % this.palette.length]; }
};

function formatCurrency(value) {
  if (value == null) return '\u2014';
  var abs = Math.abs(value);
  if (abs >= 1e6) return '$' + (value / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return '$' + (value / 1e3).toFixed(0) + 'K';
  return '$' + value.toLocaleString();
}

function formatCurrencyFull(value) {
  if (value == null) return '\u2014';
  return '$' + Math.round(value).toLocaleString();
}

function formatPercent(value) {
  if (value == null) return '\u2014';
  return (value >= 0 ? '+' : '') + value.toFixed(1) + '%';
}

function formatNumber(value) {
  if (value == null) return '\u2014';
  return Math.round(value).toLocaleString();
}

/**
 * Create an SVG element with standard margins.
 * Returns {svg, width, height, g} where g is the inner group translated by margins.
 */
function createChartSvg(containerId, opts) {
  opts = opts || {};
  var margin = opts.margin || {top: 20, right: 30, bottom: 40, left: 60};
  var container = document.getElementById(containerId);
  var fullW = opts.width || container.clientWidth || 896;
  var fullH = opts.height || container.clientHeight || 420;
  var w = fullW - margin.left - margin.right;
  var h = fullH - margin.top - margin.bottom;

  // Clear existing
  d3.select('#' + containerId).select('svg').remove();

  var svg = d3.select('#' + containerId)
    .append('svg')
    .attr('width', fullW)
    .attr('height', fullH)
    .attr('xmlns', 'http://www.w3.org/2000/svg');

  var g = svg.append('g')
    .attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

  return {svg: svg, g: g, width: w, height: h, margin: margin, fullWidth: fullW, fullHeight: fullH};
}

/**
 * Add a legend to the chart.
 * items: [{color, label}, ...]
 */
function addLegend(g, items, x, y, opts) {
  opts = opts || {};
  var direction = opts.direction || 'horizontal';
  var itemWidth = opts.itemWidth || 120;
  var legend = g.append('g').attr('transform', 'translate(' + x + ',' + y + ')');

  items.forEach(function(item, i) {
    var xOff = direction === 'horizontal' ? i * itemWidth : 0;
    var yOff = direction === 'horizontal' ? 0 : i * 20;
    var row = legend.append('g').attr('transform', 'translate(' + xOff + ',' + yOff + ')');
    row.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', item.color);
    row.append('text').attr('x', 16).attr('y', 10).attr('class', 'chart-legend-item').text(item.label);
  });
  return legend;
}

/**
 * Paginated chart navigation controller.
 */
var ChartNav = {
  currentIndex: 0,
  chartIds: [],

  init: function(ids) {
    this.chartIds = ids;
    this.currentIndex = 0;
    this.show(0);
    this.bindKeys();
    this.updateNav();
  },

  show: function(index) {
    if (index < 0 || index >= this.chartIds.length) return;
    this.currentIndex = index;
    // Hide all chart pages
    document.querySelectorAll('.chart-page').forEach(function(el) { el.style.display = 'none'; });
    // Show selected
    var id = this.chartIds[index];
    var el = document.getElementById('chart-' + id);
    if (el) {
      el.style.display = 'block';
      // Lazy init: call render function if not yet rendered
      if (!el.dataset.rendered && window['render_' + id]) {
        window['render_' + id]();
        el.dataset.rendered = '1';
      }
    }
    this.updateNav();
  },

  updateNav: function() {
    var self = this;
    document.querySelectorAll('.chart-nav-item').forEach(function(el, i) {
      el.classList.toggle('active', i === self.currentIndex);
    });
    var prevBtn = document.getElementById('chart-prev');
    var nextBtn = document.getElementById('chart-next');
    if (prevBtn) prevBtn.disabled = this.currentIndex === 0;
    if (nextBtn) nextBtn.disabled = this.currentIndex === this.chartIds.length - 1;
    // Update counter
    var counter = document.getElementById('chart-counter');
    if (counter) counter.textContent = (this.currentIndex + 1) + ' / ' + this.chartIds.length;
  },

  prev: function() { this.show(this.currentIndex - 1); },
  next: function() { this.show(this.currentIndex + 1); },

  bindKeys: function() {
    var self = this;
    document.addEventListener('keydown', function(e) {
      // Don't navigate when typing in inputs or contenteditable fields
      var t = e.target;
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return;
      if (t.isContentEditable) return;
      if (e.key === 'ArrowLeft') self.prev();
      if (e.key === 'ArrowRight') self.next();
    });
  }
};

/**
 * Editable chart-title persistence.
 * Storage key: chart_title.<client_id>.<chart_id>
 * Falls back to "global" when no client scope is present (e.g. manual chart preview).
 */
var ChartTitle = {
  _key: function(chartId) {
    var area = document.getElementById('chart-display-area');
    var clientId = area && area.dataset.clientId ? area.dataset.clientId : 'global';
    return 'chart_title.' + clientId + '.' + chartId;
  },

  applySaved: function() {
    document.querySelectorAll('.chart-title-editable').forEach(function(el) {
      var page = el.closest('.chart-page');
      if (!page) return;
      var chartId = page.dataset.chart;
      try {
        var saved = localStorage.getItem(ChartTitle._key(chartId));
        if (saved && saved.trim()) {
          el.textContent = saved;
          page.dataset.chartTitle = saved;
          ChartTitle._updateSidebar(chartId, saved);
        }
      } catch (e) { /* localStorage unavailable */ }
    });
  },

  _updateSidebar: function(chartId, newTitle) {
    if (!window.ChartNav || !ChartNav.chartIds) return;
    var idx = ChartNav.chartIds.indexOf(chartId);
    if (idx < 0) return;
    var navItem = document.querySelectorAll('.chart-nav-item')[idx];
    if (!navItem) return;
    // Sidebar items have a leading "<n>." span then a trailing text node with the title
    var labelNode = navItem.lastChild;
    if (labelNode && labelNode.nodeType === 3) {
      labelNode.textContent = ' ' + newTitle;
    }
  },

  save: function(el) {
    var page = el.closest('.chart-page');
    if (!page) return;
    var chartId = page.dataset.chart;
    var defaultTitle = el.dataset.defaultTitle || '';
    var newTitle = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!newTitle) {
      newTitle = defaultTitle;
      el.textContent = defaultTitle;
    }
    page.dataset.chartTitle = newTitle;
    try {
      var key = ChartTitle._key(chartId);
      if (newTitle === defaultTitle) {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, newTitle);
      }
    } catch (e) { /* localStorage unavailable */ }
    ChartTitle._updateSidebar(chartId, newTitle);
  }
};
