// The Dock — keyboard navigation, copy-to-clipboard, recents,
// and Outlook search-trigger for the /dock page.
// Depends on: showToast(msg, tone) defined inline in dock.html.

const RECENTS_KEY = 'dock:recents';
const MAX_RECENTS = 10;

function getRecents() {
  try {
    return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]');
  } catch {
    return [];
  }
}

function setRecents(list) {
  localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, MAX_RECENTS)));
}

function iconFor(type) {
  if (type === 'clients') return '🏢';
  if (type === 'policies') return '📄';
  if (type === 'issues') return '⚠️';
  if (type === 'programs') return '🗂️';
  return '•';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function renderRecents() {
  const list = getRecents();
  const ul = document.getElementById('recents');
  const label = document.getElementById('recents-label');
  if (!ul || !label) return;
  ul.innerHTML = '';
  if (!list.length) {
    label.style.display = 'none';
    return;
  }
  label.style.display = '';
  for (const r of list) {
    const li = document.createElement('li');
    li.className = 'dock-row';
    li.setAttribute('role', 'option');
    li.dataset.ref = r.ref;
    li.dataset.display = r.display;
    li.dataset.type = r.type;
    li.dataset.url = r.url;
    li.dataset.entityType = r.entityType || '';
    li.dataset.entityId = r.entityId || '';
    li.innerHTML = `
      <div class="dock-row-main">
        <span class="dock-type-badge">${iconFor(r.type)}</span>
        <span class="dock-name">${escapeHtml(r.display)}</span>
        <span class="dock-tag">[PDB:${escapeHtml(r.ref)}]</span>
      </div>`;
    li.addEventListener('click', () => dockCopy(li));
    ul.appendChild(li);
  }
}

async function dockCopy(row) {
  const ref = row.dataset.ref;
  if (!ref) return;
  const display = row.dataset.display || '';
  const type = row.dataset.type || '';
  const url = row.dataset.url || '';
  const entityType = row.dataset.entityType || '';
  const entityId = row.dataset.entityId || '';
  const wrapped = `[PDB:${ref}]`;

  try {
    await navigator.clipboard.writeText(wrapped);
  } catch {
    // Fallback for non-secure contexts.
    const ta = document.createElement('textarea');
    ta.value = wrapped;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
  }

  row.classList.add('flash-green');
  if (typeof showToast === 'function') {
    showToast(`${wrapped} copied`, 'ok');
  }

  // Update recents (dedup by ref, most recent first).
  const recents = getRecents().filter((r) => r.ref !== ref);
  recents.unshift({ ref, display, type, url, entityType, entityId });
  setRecents(recents);
  renderRecents();

  // Clear search + refocus shortly after the flash.
  setTimeout(() => {
    row.classList.remove('flash-green');
    const q = document.getElementById('q');
    const results = document.getElementById('results');
    if (q) { q.value = ''; q.focus(); }
    if (results) results.innerHTML = '';
  }, 400);
}

function moveSelection(delta) {
  const rows = Array.from(document.querySelectorAll('#results .dock-row'));
  if (!rows.length) return;
  let idx = rows.findIndex((r) => r.classList.contains('selected'));
  rows.forEach((r) => r.classList.remove('selected'));
  idx = Math.max(0, Math.min(rows.length - 1, idx + delta));
  if (idx < 0) idx = 0;
  rows[idx].classList.add('selected');
  rows[idx].scrollIntoView({ block: 'nearest' });
}

async function searchOutlookForRecord(btn, mode) {
  mode = mode || 'wide';
  const entityType = btn.dataset.entityType;
  const entityId = btn.dataset.entityId;
  if (!entityType || !entityId) return;
  btn.disabled = true;
  try {
    const resp = await fetch('/outlook/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entity_type: entityType, entity_id: entityId, mode }),
    });
    if (!resp.ok) {
      if (typeof showToast === 'function') showToast('Search failed — HTTP ' + resp.status, 'error');
      return;
    }
    const body = await resp.json();
    let msg = body.message || '';
    if (body.status === 'searched') {
      const count = (body.tokens || []).length;
      msg = `Searched Outlook for ${count} related tag${count === 1 ? '' : 's'}.`;
    }
    if (body.truncated) {
      msg += ` Showing ${(body.tokens || []).length} of ${body.total_available}.`;
    }
    const tone = body.status === 'searched' ? 'ok'
               : body.status === 'clipboard_only' ? 'warn'
               : 'error';
    if (typeof showToast === 'function') showToast(msg, tone);
  } catch (e) {
    if (typeof showToast === 'function') showToast('Search failed — ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}
window.searchOutlookForRecord = searchOutlookForRecord;

// Keyboard handling.
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === 'Enter') {
    const selected = document.querySelector('#results .dock-row.selected')
      || document.querySelector('#results .dock-row');
    if (selected) {
      e.preventDefault();
      dockCopy(selected);
    }
  } else if (e.key === 'Escape') {
    const q = document.getElementById('q');
    const results = document.getElementById('results');
    if (q) { q.value = ''; q.focus(); }
    if (results) results.innerHTML = '';
  }
});

// Refocus search when the window regains focus (coming back from Outlook).
window.addEventListener('focus', () => {
  const q = document.getElementById('q');
  if (q) q.focus();
});

// After HTMX swaps results, preselect the first row.
document.body.addEventListener('htmx:afterSwap', (e) => {
  if (e.target && e.target.id === 'results') {
    const first = document.querySelector('#results .dock-row');
    if (first) first.classList.add('selected');
  }
});

// Row click delegation (for HTMX-swapped rows).
document.body.addEventListener('click', (e) => {
  const row = e.target.closest('#results .dock-row');
  if (row && !e.target.closest('.dock-action, .dock-open')) {
    dockCopy(row);
  }
});

// Initial render of recents list.
document.addEventListener('DOMContentLoaded', renderRecents);
