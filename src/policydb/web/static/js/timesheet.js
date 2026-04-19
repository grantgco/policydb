/* Timesheet review — client-side glue.
   Handles flashCell feedback on contenteditable PATCHes,
   inline day-total refresh from the PATCH JSON response,
   and range-popover open/close + cascade for the add-activity form.
*/
(function () {
  "use strict";

  // Guard against double-binding when _panel.html is HTMX-swapped and its inline
  // loader fires a second <script src="timesheet.js"> tag. Handlers must bind once.
  if (window._tsLoaded) return;
  window._tsLoaded = true;

  function flash(el) {
    if (typeof window.flashCell === "function") {
      window.flashCell(el);
    } else {
      el.style.transition = "background-color .3s ease";
      el.style.backgroundColor = "#d1fae5";
      setTimeout(function () {
        el.style.backgroundColor = "";
        setTimeout(function () { el.style.transition = ""; }, 300);
      }, 800);
    }
  }

  // Intercept timesheet PATCH responses and wire up UI feedback.
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var xhr = evt.detail.xhr;
    var path = evt.detail.requestConfig && evt.detail.requestConfig.path;
    if (!path || path.indexOf("/timesheet/activity/") !== 0) return;
    if (evt.detail.requestConfig.verb !== "patch") return;
    if (!xhr || xhr.status !== 200) return;

    var data;
    try { data = JSON.parse(xhr.responseText); } catch (e) { return; }
    if (!data || !data.ok) return;

    var target = evt.detail.elt;
    if (!target) return;

    // Update hours cell display to the server-rounded value.
    if (target.classList.contains("ts-hours") && typeof data.formatted === "string") {
      target.innerText = data.formatted;
    }

    // Update the day-card total. Day card holds a .day-tot span in its header.
    var card = target.closest(".day-card");
    if (card && typeof data.total_hours === "number") {
      var tot = card.querySelector(".day-tot");
      if (tot) {
        var h = Number(data.total_hours);
        tot.textContent = (Math.round(h * 10) / 10).toFixed(1) + "h";
      }
    }

    flash(target);
  });

  // Range popover toggling.
  document.body.addEventListener("click", function (evt) {
    var trigger = evt.target.closest("[data-range-trigger]");
    if (trigger) {
      evt.preventDefault();
      var pop = document.querySelector("[data-range-popover]");
      if (pop) {
        pop.dataset.open = pop.dataset.open === "1" ? "0" : "1";
      }
      return;
    }
    // Outside-click close.
    var open = document.querySelector('[data-range-popover][data-open="1"]');
    if (open && !evt.target.closest("[data-range-popover]") &&
               !evt.target.closest("[data-range-trigger]")) {
      open.dataset.open = "0";
    }
  });
  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    var open = document.querySelector('[data-range-popover][data-open="1"]');
    if (open) open.dataset.open = "0";
  });

  // Add-activity form cascade: when the client changes, reset policy/project/issue inputs
  // and refetch the option lists.
  function setDatalist(dlId, options) {
    var dl = document.getElementById(dlId);
    if (!dl) return;
    dl.innerHTML = "";
    options.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o.label;
      opt.dataset.id = o.id;
      dl.appendChild(opt);
    });
  }

  function refreshCascade(form, clientId) {
    ["policy", "project", "issue"].forEach(function (kind) {
      var input = form.querySelector('[data-cascade="' + kind + '"]');
      var hid   = form.querySelector('[data-cascade-id="' + kind + '"]');
      if (input) input.value = "";
      if (hid)   hid.value = "";
      setDatalist("ts-options-" + kind, []);
    });
    if (!clientId) return;
    fetch("/timesheet/options/all?client_id=" + encodeURIComponent(clientId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setDatalist("ts-options-policy",  data.policies  || []);
        setDatalist("ts-options-project", data.projects || []);
        setDatalist("ts-options-issue",   data.issues   || []);
      });
  }

  function resolveId(form, kind) {
    var input = form.querySelector('[data-cascade="' + kind + '"]');
    var hid   = form.querySelector('[data-cascade-id="' + kind + '"]');
    if (!input || !hid) return;
    var dl = document.getElementById("ts-options-" + kind);
    if (!dl) return;
    hid.value = "";
    var match = Array.prototype.find.call(dl.options, function (opt) {
      return opt.value === input.value;
    });
    if (match) hid.value = match.dataset.id || "";
  }

  document.body.addEventListener("input", function (evt) {
    var form = evt.target.closest(".add-activity-form");
    if (!form) return;

    if (evt.target.matches('[data-cascade="client"]')) {
      // Find the matching client id from the client datalist.
      var dl = document.getElementById("ts-options-client");
      var hid = form.querySelector('[data-cascade-id="client"]');
      if (!dl || !hid) return;
      var match = Array.prototype.find.call(dl.options, function (opt) {
        return opt.value === evt.target.value;
      });
      hid.value = match ? (match.dataset.id || "") : "";
      refreshCascade(form, hid.value);
      return;
    }

    ["policy", "project", "issue"].forEach(function (kind) {
      if (evt.target.matches('[data-cascade="' + kind + '"]')) {
        resolveId(form, kind);
      }
    });
  });

  // Range popover — presets + apply.
  function isoMonday(d) {
    var wd = d.getDay(); // Sunday = 0
    var diff = (wd === 0 ? -6 : 1 - wd);
    var out = new Date(d); out.setDate(d.getDate() + diff);
    return out;
  }
  function iso(d) { return d.toISOString().slice(0, 10); }

  document.body.addEventListener("click", function (evt) {
    var preset = evt.target.closest(".ts-preset");
    if (preset) {
      var pop = preset.closest("[data-range-popover]");
      if (!pop) return;
      var startInput = pop.querySelector(".ts-range-start");
      var endInput   = pop.querySelector(".ts-range-end");
      var today = new Date();
      var s, e;
      switch (preset.dataset.preset) {
        case "this-week":
          s = isoMonday(today);
          e = new Date(s); e.setDate(s.getDate() + 6);
          break;
        case "last-week":
          s = isoMonday(today); s.setDate(s.getDate() - 7);
          e = new Date(s); e.setDate(s.getDate() + 6);
          break;
        case "mtd":
          s = new Date(today.getFullYear(), today.getMonth(), 1);
          e = today;
          break;
        case "last-30":
          s = new Date(today); s.setDate(today.getDate() - 30);
          e = today;
          break;
        default: return;
      }
      startInput.value = iso(s);
      endInput.value   = iso(e);
      pop.querySelectorAll(".ts-preset").forEach(function (p) { p.classList.remove("active"); });
      preset.classList.add("active");
      return;
    }

    var apply = evt.target.closest(".ts-range-apply");
    if (apply) {
      var pop2 = apply.closest("[data-range-popover]");
      if (!pop2) return;
      var s = pop2.querySelector(".ts-range-start").value;
      var e = pop2.querySelector(".ts-range-end").value;
      if (!s || !e) return;
      pop2.dataset.open = "0";
      htmx.ajax("GET",
        "/timesheet/panel?kind=range&start=" + encodeURIComponent(s) +
        "&end=" + encodeURIComponent(e),
        "#timesheet-panel");
      return;
    }

    var cancel = evt.target.closest(".ts-range-cancel");
    if (cancel) {
      var pop3 = cancel.closest("[data-range-popover]");
      if (pop3) pop3.dataset.open = "0";
    }
  });
})();
