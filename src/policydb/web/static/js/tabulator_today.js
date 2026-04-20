/* Shared Tabulator column renderers and base config for Today and Plan Week.

   Usage:
     const table = buildTodayTable({
       selector: "#today-grid",
       rows: JSON.parse(el.dataset.rows),
       nudgeDays: Number(el.dataset.nudgeDays) || 10,
     });
*/

(function (global) {
  const priorityColorMap = { 3: "overdue", 2: "today", 1: "tomorrow", 0: "later" };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function priorityBarFormatter(cell) {
    const row = cell.getRow().getData();
    const cls = priorityColorMap[row.priority] || "later";
    const notch = row.waiting_days != null && row.waiting_days > (cell.getTable().nudgeDays || 10)
      ? ' <span class="priority-notch amber"></span>'
      : "";
    const pulseClass = cls === "overdue" ? " priority-bar-pulse" : "";
    return `<div class="priority-bar ${cls}${pulseClass}">${notch}</div>`;
  }

  function kindChipFormatter(cell) {
    const kind = cell.getValue() || "followup";
    const label = { followup: "Task", issue: "Issue" }[kind] || kind;
    return `<span class="kind-chip kind-${kind}">${label}</span>`;
  }

  function subjectFormatter(cell) {
    const row = cell.getRow().getData();
    const subj = escapeHtml(cell.getValue() || "");
    const rawCtx = row.details || (row.client_id == null ? "Standalone task" : "");
    const ctx = escapeHtml(rawCtx);
    // Ref-pill replacement runs on already-escaped text. Tokens like POL-123 only
    // contain [A-Z0-9-], which survives escape unchanged.
    const ctxHtml = ctx.replace(
      /\b(POL-\d+|CN-\d+|ISS-\d+)\b/g,
      (m) => `<span class="ref-pill">${m}</span>`
    );
    return `<div class="subj">${subj}</div><div class="ctx-line">${ctxHtml}</div>`;
  }

  function clientPolicyFormatter(cell) {
    const row = cell.getRow().getData();
    if (!row.client_id && !row.policy_uid) {
      return '<em class="muted">Standalone</em>';
    }
    const policy = row.policy_uid
      ? `<span class="ref-pill">${escapeHtml(row.policy_uid)}</span> `
      : "";
    const client = row.client_name
      ? `<a href="/clients/${encodeURIComponent(row.client_id)}">${escapeHtml(row.client_name)}</a>`
      : "";
    return policy + client;
  }

  function dueFormatter(cell) {
    const row = cell.getRow().getData();
    if (!row.follow_up_date) return "—";
    const d = new Date(row.follow_up_date + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.floor((d - today) / 86400000);
    if (days < 0) return `<span class="due red">${-days}d overdue</span>`;
    if (days === 0) return '<span class="due">today</span>';
    if (days === 1) return '<span class="due">tomorrow</span>';
    return d.toLocaleDateString(undefined, { weekday: "short" });
  }

  function lastFormatter(cell) {
    const ts = cell.getValue();
    if (!ts) return "—";
    const d = new Date(ts);
    const diff = Math.floor((Date.now() - d.getTime()) / 86400000);
    return diff === 0 ? "today" : `${diff}d`;
  }

  function contactFormatter(cell) {
    return escapeHtml(cell.getValue() || "—");
  }

  function completeCheckboxFormatter() {
    return `
      <button class="today-check" aria-label="Complete task">
        <svg viewBox="0 0 16 16" width="14" height="14">
          <rect x="1.25" y="1.25" width="13.5" height="13.5" rx="2.5" class="box" />
          <path d="M4 8.5 L7 11.5 L12.5 5" class="tick" />
        </svg>
      </button>`;
  }

  function actionsFormatter() {
    return '<button class="mini-btn actions-btn" aria-label="Row actions">•••</button>';
  }

  function buildTodayTable({ selector, rows, nudgeDays = 10, onCompleted, onSnooze }) {
    const table = new Tabulator(selector, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "100%",
      placeholder: "No tasks match your filters.",
      initialSort: [
        { column: "priority", dir: "desc" },
        { column: "follow_up_date", dir: "asc" },
        { column: "id", dir: "asc" },
      ],
      columns: [
        { title: "", field: "_check", width: 40, hozAlign: "center",
          formatter: completeCheckboxFormatter, cellClick: (e, cell) => onCompleted?.(cell.getRow().getData()) },
        { title: "", field: "_priority", width: 4, formatter: priorityBarFormatter, headerSort: false },
        { title: "Kind", field: "kind", width: 72, formatter: kindChipFormatter },
        { title: "Subject / Context", field: "subject", formatter: subjectFormatter, minWidth: 320 },
        { title: "Client · Policy", field: "client_name", width: 180, formatter: clientPolicyFormatter },
        { title: "Contact", field: "contact_person", width: 140, formatter: contactFormatter },
        { title: "Last", field: "last_activity_at", width: 90, formatter: lastFormatter },
        { title: "Due", field: "follow_up_date", width: 90, formatter: dueFormatter },
        { title: "", field: "_actions", width: 40, formatter: actionsFormatter, headerSort: false },
      ],
    });
    table.nudgeDays = nudgeDays;
    return table;
  }

  global.buildTodayTable = buildTodayTable;
})(window);
