/*
 * Ops board client (D11: output only).
 *
 * Polls /api/status on a timer and repaints. There is no interaction here
 * on purpose — no handlers, no navigation, nothing focusable.
 *
 * The design rule that drives the rest of this file: a board that has
 * stopped updating must never look like one that is up to date. The clock
 * ticks every second off the LAST SUCCESSFUL poll, so a frozen backend
 * shows a visibly ageing timestamp within a second or two of the failure,
 * rather than a plausible-looking screen full of yesterday's truth.
 */

(function () {
  "use strict";

  var POLL_INTERVAL_MS = 10000;
  var FETCH_TIMEOUT_MS = 8000;
  // Two and a half polls: one dropped request is a blip, three is a problem.
  var STALE_AFTER_MS = 25000;

  var lastSuccessAt = null; // epoch ms of the last good poll
  var lastSnapshot = null;

  function $(id) {
    return document.getElementById(id);
  }

  function text(node, value) {
    node.textContent = value === null || value === undefined ? "—" : String(value);
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function el(tag, className, content) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (content !== undefined && content !== null) {
      node.textContent = String(content);
    }
    return node;
  }

  // --- formatting ---------------------------------------------------------

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function clockTime(date) {
    return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
  }

  function shortTime(iso) {
    if (!iso) {
      return "—";
    }
    var date = new Date(iso);
    if (isNaN(date.getTime())) {
      return "—";
    }
    return pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function duration(seconds) {
    if (seconds === null || seconds === undefined) {
      return "—";
    }
    if (seconds < 1) {
      return Math.round(seconds * 1000) + "ms";
    }
    if (seconds < 60) {
      return seconds.toFixed(1) + "s";
    }
    return Math.floor(seconds / 60) + "m" + pad(Math.round(seconds % 60)) + "s";
  }

  function humanAge(ms) {
    var seconds = Math.max(0, Math.round(ms / 1000));
    if (seconds < 60) {
      return seconds + "s";
    }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + "m " + pad(seconds % 60) + "s";
    }
    var hours = Math.floor(minutes / 60);
    return hours + "h " + pad(minutes % 60) + "m";
  }

  function uptime(seconds) {
    if (seconds === null || seconds === undefined) {
      return "—";
    }
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) {
      return days + "d " + hours + "h";
    }
    if (hours > 0) {
      return hours + "h " + minutes + "m";
    }
    return minutes + "m";
  }

  function gib(bytes) {
    if (!bytes) {
      return "—";
    }
    return (bytes / 1073741824).toFixed(1) + "G";
  }

  function latency(ms) {
    if (ms === null || ms === undefined) {
      return "";
    }
    // Loopback probes land well under a millisecond; rounding them to "0ms"
    // reads as broken rather than fast.
    return (ms < 10 ? ms.toFixed(1) : String(Math.round(ms))) + "ms";
  }

  function percent(value) {
    return value === null || value === undefined ? "—" : Math.round(value) + "%";
  }

  function levelFor(value, warn, crit) {
    if (value === null || value === undefined) {
      return "";
    }
    if (value >= crit) {
      return "crit";
    }
    if (value >= warn) {
      return "warn";
    }
    return "";
  }

  // --- panels -------------------------------------------------------------

  function renderAlerts(snapshot) {
    var list = $("alerts");
    var empty = $("alerts-empty");
    var more = $("alerts-more");
    clear(list);

    var alerts = snapshot.alerts || [];
    empty.hidden = alerts.length > 0;

    alerts.forEach(function (alert) {
      var item = el("li");
      item.setAttribute("data-severity", alert.severity);
      item.appendChild(el("span", "alert-summary", alert.summary));
      item.appendChild(el("span", "alert-detail", alert.detail || ""));
      item.appendChild(el("span", "alert-at", alert.at ? shortTime(alert.at) : ""));
      list.appendChild(item);
    });

    var hidden = (snapshot.alerts_total || 0) - alerts.length;
    more.hidden = hidden <= 0;
    if (hidden > 0) {
      text(more, "+ " + hidden + " more not shown");
    }
  }

  function renderApprovals(snapshot) {
    var list = $("approvals");
    var empty = $("approvals-empty");
    var more = $("approvals-more");
    clear(list);

    var approvals = snapshot.approvals || [];
    var total = snapshot.approvals_total || 0;
    text($("approvals-count"), total);
    empty.hidden = approvals.length > 0;

    approvals.forEach(function (approval) {
      var item = el("li");
      item.appendChild(el("span", "approval-summary", approval.summary));
      item.appendChild(
        el("span", "approval-meta", approval.job_id + " · expires " + shortTime(approval.expires_at))
      );
      list.appendChild(item);
    });

    var hidden = total - approvals.length;
    more.hidden = hidden <= 0;
    if (hidden > 0) {
      text(more, "+ " + hidden + " more waiting");
    }
  }

  function renderAuthority(snapshot) {
    var block = $("mode-block");
    var counts = (snapshot.modes && snapshot.modes.counts) || {};
    var writeCapable = (snapshot.modes && snapshot.modes.write_capable) || [];

    // D8 is per job, not a global switch. The safety-relevant fact is how
    // many jobs may mutate without asking, so that is the headline.
    block.setAttribute("data-write", writeCapable.length > 0 ? "true" : "false");
    if (writeCapable.length > 0) {
      text($("mode-headline"), writeCapable.length + " AUTO-WRITE");
      text($("mode-detail"), writeCapable.join(", "));
    } else {
      text($("mode-headline"), "APPROVAL REQUIRED");
      text(
        $("mode-detail"),
        "no job writes unattended · " +
          (counts.read || 0) +
          " read / " +
          (counts.propose || 0) +
          " propose"
      );
    }

    var service = snapshot.service || {};
    var budget = snapshot.budget || {};
    text(
      $("service-line"),
      service.profile +
        " · " +
        service.jobs_enabled +
        "/" +
        service.jobs_total +
        " jobs · up " +
        uptime(service.uptime_seconds) +
        (service.scheduler_paused ? " · PAUSED" : "") +
        " · " +
        (budget.spent || "?") +
        "/" +
        (budget.ceiling || "?")
    );
  }

  function renderRuns(snapshot) {
    var body = $("runs");
    var empty = $("runs-empty");
    clear(body);

    var runs = snapshot.runs || [];
    empty.hidden = runs.length > 0;

    runs.forEach(function (run) {
      var row = el("tr");
      row.setAttribute("data-state", run.state);
      row.appendChild(el("td", "job", run.job_id));
      row.appendChild(el("td", "tier", "T" + run.tier));

      var stateCell = el("td");
      stateCell.appendChild(el("span", "state", run.state.replace(/_/g, " ")));
      row.appendChild(stateCell);

      row.appendChild(el("td", "duration", duration(run.duration_seconds)));
      row.appendChild(el("td", "when", shortTime(run.finished_at || run.started_at)));
      body.appendChild(row);
    });
  }

  function renderContainers(snapshot) {
    var list = $("containers");
    clear(list);

    (snapshot.containers || []).forEach(function (container) {
      var item = el("li");
      item.setAttribute("data-up", container.reachable ? "true" : "false");
      item.appendChild(el("span", "dot"));
      item.appendChild(el("span", "container-name", container.name));
      item.appendChild(
        el(
          "span",
          "container-detail",
          container.reachable
            ? latency(container.latency_ms) + " " + container.endpoint
            : container.detail || "down"
        )
      );
      list.appendChild(item);
    });
  }

  function metric(label, value, level) {
    var wrapper = el("div", "metric");
    wrapper.appendChild(el("dt", null, label));
    var dd = el("dd", null, value);
    if (level) {
      dd.setAttribute("data-level", level);
    }
    wrapper.appendChild(dd);
    return wrapper;
  }

  function renderSystem(snapshot) {
    var container = $("system");
    clear(container);

    var system = snapshot.system || {};
    var wifi = system.wifi || {};

    container.appendChild(
      metric(
        "cpu temp",
        system.cpu_temp_c === null || system.cpu_temp_c === undefined
          ? "—"
          : system.cpu_temp_c.toFixed(1) + "°",
        levelFor(system.cpu_temp_c, 70, 80)
      )
    );
    container.appendChild(
      metric(
        "load",
        system.load_1 === null || system.load_1 === undefined ? "—" : system.load_1.toFixed(2),
        levelFor(system.load_1, 4, 8)
      )
    );
    container.appendChild(
      metric(
        "ram",
        percent(system.mem_used_percent) + " of " + gib(system.mem_total_bytes),
        levelFor(system.mem_used_percent, 80, 92)
      )
    );
    container.appendChild(
      metric(
        "disk",
        percent(system.disk_used_percent) + " of " + gib(system.disk_total_bytes),
        levelFor(system.disk_used_percent, 80, 90)
      )
    );
    container.appendChild(metric("uptime", uptime(system.uptime_seconds)));
    container.appendChild(
      metric(
        "wi-fi",
        wifi.signal_dbm === null || wifi.signal_dbm === undefined
          ? "—"
          : Math.round(wifi.signal_dbm) + " dBm",
        // -67 dBm is the usual floor for reliable streaming; -75 is trouble.
        levelFor(wifi.signal_dbm === null ? null : -wifi.signal_dbm, 67, 75)
      )
    );
  }

  function render(snapshot) {
    text($("revision"), (snapshot.service && snapshot.service.revision) || "");
    var mode = (snapshot.service && snapshot.service.display_mode) || "OPS";
    var state = $("display-mode");
    text(state, mode.replace(/_/g, " "));
    state.setAttribute("data-mode", mode);

    renderAlerts(snapshot);
    renderApprovals(snapshot);
    renderAuthority(snapshot);
    renderRuns(snapshot);
    renderContainers(snapshot);
    renderSystem(snapshot);
  }

  // --- staleness ----------------------------------------------------------

  function refreshStaleness() {
    var banner = $("staleness");
    var detail = $("staleness-detail");
    var title = $("staleness-title");

    if (lastSuccessAt === null) {
      document.body.setAttribute("data-stale", "true");
      banner.hidden = false;
      text(title, "NO DATA");
      text(detail, "never reached the atlas API since this page loaded");
      text($("last-updated"), "never");
      return;
    }

    var age = Date.now() - lastSuccessAt;
    text($("last-updated"), clockTime(new Date(lastSuccessAt)));

    if (age > STALE_AFTER_MS) {
      document.body.setAttribute("data-stale", "true");
      banner.hidden = false;
      text(title, "STALE DATA");
      text(detail, "last successful update " + humanAge(age) + " ago — the API is not responding");
    } else {
      document.body.setAttribute("data-stale", "false");
      banner.hidden = true;
    }
  }

  // --- polling ------------------------------------------------------------

  function poll() {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, FETCH_TIMEOUT_MS);

    fetch("/api/status", {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (snapshot) {
        lastSnapshot = snapshot;
        lastSuccessAt = Date.now();
        render(snapshot);
      })
      .catch(function () {
        // Deliberately keep the last good data on screen. Blanking the
        // board would destroy the failure information that is the whole
        // reason for the display; the banner says it is no longer current.
        if (lastSnapshot === null) {
          lastSuccessAt = null;
        }
      })
      .then(function () {
        clearTimeout(timer);
        refreshStaleness();
      });
  }

  refreshStaleness();
  poll();
  setInterval(poll, POLL_INTERVAL_MS);
  // Independent of the poll: the age must keep climbing while requests fail.
  setInterval(refreshStaleness, 1000);
})();
