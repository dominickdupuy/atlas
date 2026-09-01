/*
 * atlas ops board client — implementation of the "Atlas Board" design canvas.
 *
 * Output only (D11): no handlers, no navigation, nothing focusable. The
 * design's DCLogic class is translated to plain DOM here — D17 rules out a
 * build step or a Node toolchain on the Pi, and the canvas runtime
 * (support.js) is React-based authoring tooling, not something to ship.
 *
 * Two behaviours carry over from the design: a fixed 1920x1080 canvas scaled
 * to the viewport by fit(), and a timer that drives the clock and the "now"
 * line. Everything else is driven by one /api/status poll.
 *
 * The rule that shapes the rest: a board that has stopped updating must never
 * look like one that has not.
 */

(function () {
  "use strict";

  var POLL_INTERVAL_MS = 10000;
  var FETCH_TIMEOUT_MS = 8000;
  // Two and a half polls: one dropped request is a blip, three is a problem.
  var STALE_AFTER_MS = 25000;

  var BOARD_W = 1920;
  var BOARD_H = 1080;
  // The canvas keeps the design's HEIGHT, which is what fixes its type scale,
  // and takes its width from the display's aspect ratio. On a 16:9 panel that
  // reproduces the 1920x1080 artboard exactly; on this host's 4:3 1024x768 it
  // becomes 1440x1080, which fills the screen instead of letterboxing away a
  // third of it and shrinking every label by the same third.
  var BOARD_MIN_W = 1280;
  var BOARD_MAX_W = 2560;
  var MIN_EVENT_PCT = 2.4;
  // A 50-minute class is 5.2% of a 06:00-22:00 band, which is not enough
  // height for a two-line block: the room number clipped. Above ~1h10 it
  // fits; below it, label and detail share one line and ellipsis.
  var TALL_EVENT_PCT = 7.0;

  var lastSuccessAt = null;
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

  function hhmm(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function clockTime(date) {
    return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
  }

  function humanAge(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m " + pad(s % 60) + "s";
    return Math.floor(m / 60) + "h " + pad(m % 60) + "m";
  }

  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 1) return Math.round(seconds * 1000) + "ms";
    if (seconds < 60) return seconds.toFixed(1) + "s";
    return Math.floor(seconds / 60) + "m" + pad(Math.round(seconds % 60)) + "s";
  }

  function uptime(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    var d = Math.floor(seconds / 86400);
    var h = Math.floor((seconds % 86400) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return d + "d " + h + "h";
    if (h > 0) return h + "h " + m + "m";
    return m + "m";
  }

  function gib(bytes) {
    return bytes ? (bytes / 1073741824).toFixed(1) + "G" : "—";
  }

  function pct(v) {
    return v === null || v === undefined ? "—" : Math.round(v) + "%";
  }

  function level(v, warn, crit) {
    if (v === null || v === undefined) return "";
    if (v >= crit) return "crit";
    if (v >= warn) return "warn";
    return "";
  }

  function fahrenheit(c) {
    return c === null || c === undefined ? null : Math.round((c * 9) / 5 + 32);
  }

  function hourLabel(hour) {
    if (hour === 12) return "NOON";
    var h12 = hour % 12 || 12;
    return h12 + (hour < 12 ? " AM" : " PM");
  }

  // --- header -------------------------------------------------------------

  function renderHeader(s) {
    var svc = s.service || {};
    text($("build"), "v" + (svc.version || "?") + " · " + (svc.revision || "?"));

    var mode = svc.display_mode || "OPS";
    var pill = $("mode-pill");
    pill.setAttribute("data-mode", mode);
    var label = mode.replace(/_/g, " ");
    if (mode === "APPROVAL_PENDING" && s.approvals_total) {
      label = s.approvals_total + " AWAITING APPROVAL";
    }
    text($("mode-text"), label);
  }

  function renderClock() {
    var d = new Date();
    var h12 = d.getHours() % 12 || 12;
    text($("clock"), h12 + ":" + pad(d.getMinutes()));
    text($("meridiem"), d.getHours() < 12 ? "AM" : "PM");
    text(
      $("datestr"),
      d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })
    );
  }

  // --- attention ----------------------------------------------------------

  /*
   * The design has no alerts panel; D11 says failures are the reason the
   * screen exists. Resolved by making the healthy board exactly the design,
   * and giving anything that needs a human its own band above the fold.
   */
  function renderAttention(s) {
    var band = $("attention");
    var list = $("attention-list");
    clear(list);

    var items = [];
    (s.alerts || []).forEach(function (a) {
      items.push({
        severity: a.severity,
        summary: a.summary,
        detail: a.detail || "",
        at: a.at ? hhmm(a.at) : "",
      });
    });
    (s.approvals || []).forEach(function (a) {
      items.push({
        severity: "approval",
        summary: "approve on phone",
        detail: a.summary + "  ·  " + a.job_id,
        at: "expires " + hhmm(a.expires_at),
      });
    });

    if (!items.length) {
      band.hidden = true;
      return;
    }

    var worst = items.some(function (i) {
      return i.severity === "critical";
    })
      ? "critical"
      : "warning";
    band.setAttribute("data-severity", worst);

    items.slice(0, 4).forEach(function (item) {
      var li = el("li");
      li.setAttribute("data-severity", item.severity);
      li.appendChild(el("span", "att-summary", item.summary));
      li.appendChild(el("span", "att-detail", item.detail));
      li.appendChild(el("span", "att-at", item.at));
      list.appendChild(li);
    });

    var hidden = items.length - Math.min(4, items.length);
    var extra = (s.alerts_total || 0) - (s.alerts || []).length + hidden;
    text($("attention-more"), extra > 0 ? "+ " + extra + " more" : "");
    text($("attention-title"), "ATTENTION · " + items.length);
    band.hidden = false;
  }

  // --- timeline -----------------------------------------------------------

  function renderTimeline(s) {
    var startHour = s.timeline_start_hour;
    var endHour = s.timeline_end_hour;
    var hours = endHour - startHour;
    var span = hours * 60;
    var hourPct = 100 / hours;

    var cal = s.calendar || {};
    text($("timeline-title"), cal.configured ? "CALENDAR" : "SCHEDULE");
    var source = cal.detail || "atlas jobs only";
    if (cal.configured && cal.synced_at) {
      // Say when, not just what: a published feed can lag by hours, and the
      // board should never imply it is live when it is not.
      source = cal.event_count + " events · synced " + hhmm(cal.synced_at);
      if (cal.error) source += " · refresh failing";
    }
    text($("timeline-source"), source);
    text(
      $("legend-note"),
      cal.configured ? "read-only feed · atlas cannot write to it" : "calendar not connected"
    );

    // hour gutter
    var gutter = $("gutter");
    clear(gutter);
    for (var h = startHour; h < endHour; h++) {
      var slot = el("div", "hour");
      if (h === 12) slot.setAttribute("data-noon", "true");
      slot.appendChild(el("span", null, hourLabel(h)));
      gutter.appendChild(slot);
    }

    // day headers
    var days = s.timeline_days || [];
    var daybar = $("daybar");
    clear(daybar);
    days.forEach(function (day) {
      var box = el("div", "day");
      box.setAttribute("data-today", day.is_today ? "true" : "false");
      var left = el("div");
      left.style.display = "flex";
      left.style.alignItems = "baseline";
      left.appendChild(el("span", "day-name", day.label));
      if (day.is_today) left.appendChild(el("span", "day-today", "TODAY"));
      box.appendChild(left);
      box.appendChild(
        el("span", "day-count", day.entry_count + (day.entry_count === 1 ? " item" : " items"))
      );
      daybar.appendChild(box);
    });

    // columns
    var cols = $("cols");
    clear(cols);
    var byDay = {};
    (s.timeline || []).forEach(function (e) {
      (byDay[e.day_offset] = byDay[e.day_offset] || []).push(e);
    });

    days.forEach(function (day) {
      var col = el("div", "col");
      col.setAttribute("data-today", day.is_today ? "true" : "false");
      col.style.setProperty("--hour-pct", hourPct + "%");

      var placed = layout(byDay[day.day_offset] || [], startHour, span);
      placed.forEach(function (p) {
        var e = p.entry;
        var top = p.top;
        var height = p.height;

        var box = el("div", p.band ? "ev band" : "ev");
        // Two jobs firing at 07:00 must not print on top of each other.
        box.style.left = 2 + p.lane * (96 / p.lanes) + "%";
        box.style.right = "auto";
        box.style.width = 96 / p.lanes - (p.lanes > 1 ? 1 : 0) + "%";
        box.setAttribute("data-kind", e.kind);
        if (e.status) box.setAttribute("data-status", e.status);
        box.style.top = top + "%";
        box.style.height = height + "%";
        if (height >= TALL_EVENT_PCT && !p.band) box.className = "ev tall";

        box.appendChild(el("span", "ev-label", e.label));
        if (e.detail) box.appendChild(el("span", "ev-detail", e.detail));
        col.appendChild(box);
      });

      if (day.is_today) {
        var now = new Date();
        var mins = now.getHours() * 60 + now.getMinutes();
        var y = ((mins - startHour * 60) / span) * 100;
        if (y >= 0 && y <= 100) {
          var line = el("div", "nowline");
          line.id = "nowline";
          line.style.top = y + "%";
          line.appendChild(el("span"));
          col.appendChild(line);
        }
      }
      cols.appendChild(col);
    });
  }

  /*
   * Greedy interval packing: an entry goes in the first lane whose previous
   * occupant has already ended. Lane count is per day, so a day with no
   * overlaps still uses the full column width.
   */
  function geometry(entries, startHour, span) {
    return entries
      .map(function (e) {
        var top = ((e.start_minutes - startHour * 60) / span) * 100;
        var height = MIN_EVENT_PCT;
        if (e.end_minutes !== null && e.end_minutes !== undefined) {
          height = Math.max(MIN_EVENT_PCT, ((e.end_minutes - e.start_minutes) / span) * 100);
        }
        return { entry: e, top: top, height: height };
      })
      .filter(function (p) {
        return p.top + p.height >= 0 && p.top <= 100;
      })
      .map(function (p) {
        p.top = Math.max(0, Math.min(100 - MIN_EVENT_PCT, p.top));
        p.height = Math.min(p.height, 100 - p.top);
        return p;
      })
      .sort(function (a, b) {
        return a.top - b.top || b.height - a.height;
      });
  }

  function layout(entries, startHour, span) {
    var all = geometry(entries, startHour, span);
    // A recurring band ("17 runs, all completed") is a range backdrop, not a
    // competitor for space. Laning it against a 07:00 point event squeezed an
    // all-day band into half the column for no reason.
    var bands = all.filter(function (p) {
      return p.entry.count > 1;
    });
    var items = all.filter(function (p) {
      return p.entry.count <= 1;
    });

    var laneEnds = [];
    items.forEach(function (p) {
      var lane = 0;
      while (lane < laneEnds.length && laneEnds[lane] > p.top + 0.01) {
        lane++;
      }
      laneEnds[lane] = p.top + p.height;
      p.lane = lane;
    });
    var lanes = Math.max(1, laneEnds.length);
    items.forEach(function (p) {
      p.lanes = lanes;
      p.band = false;
    });
    bands.forEach(function (p) {
      p.lane = 0;
      p.lanes = 1;
      p.band = true;
    });
    return bands.concat(items);
  }

  function moveNowLine(s) {
    var line = $("nowline");
    if (!line || !s) return;
    var span = (s.timeline_end_hour - s.timeline_start_hour) * 60;
    var now = new Date();
    var mins = now.getHours() * 60 + now.getMinutes();
    var y = ((mins - s.timeline_start_hour * 60) / span) * 100;
    line.style.display = y >= 0 && y <= 100 ? "block" : "none";
    line.style.top = Math.max(0, Math.min(100, y)) + "%";
  }

  // --- weather ------------------------------------------------------------

  function renderWeather(s) {
    var wx = s.weather || {};
    var body = $("weather-body");
    clear(body);
    text($("weather-place"), wx.available ? "current" : "unavailable");

    if (!wx.available) {
      // Never a fabricated number: the dev profile's StubWeather returns a
      // canned 21C, and a canned temperature on a wall display is a lie.
      var box = el("div", "wx-unavailable");
      box.appendChild(el("strong", null, "Not connected"));
      box.appendChild(document.createTextNode(wx.detail || "no weather source configured"));
      body.appendChild(box);
      return;
    }

    var main = el("div", "wx-main");
    var temp = el("div", "wx-temp");
    temp.appendChild(el("span", "wx-deg", fahrenheit(wx.temperature_c)));
    temp.appendChild(el("span", "wx-unit", "°F"));
    main.appendChild(temp);

    var right = el("div", "wx-right");
    right.appendChild(el("span", "wx-summary", wx.summary || "—"));
    right.appendChild(
      el("span", "wx-hilo", "H " + fahrenheit(wx.high_c) + "°  L " + fahrenheit(wx.low_c) + "°")
    );
    main.appendChild(right);
    body.appendChild(main);

    var foot = el("div", "wx-hilo");
    foot.style.display = "flex";
    foot.style.justifyContent = "space-between";
    foot.appendChild(el("span", null, "Precip " + (wx.precipitation_chance_pct ?? "—") + "%"));
    body.appendChild(foot);
  }

  // --- runs ---------------------------------------------------------------

  function renderRuns(s) {
    var box = $("runs");
    clear(box);
    var runs = s.runs || [];
    text($("runs-source"), "last " + runs.length + " · polled 10s");

    if (!runs.length) {
      box.appendChild(el("p", "empty", "no runs recorded"));
      return;
    }

    runs.forEach(function (run) {
      var row = el("div", "run");
      row.setAttribute("data-state", run.state);
      row.appendChild(el("span", "run-job", run.job_id));
      row.appendChild(el("span", "run-state", run.state.replace(/_/g, " ").toUpperCase()));

      var detail = run.error
        ? run.error
        : "tier " + run.tier + " · " + run.mode + " · " + duration(run.duration_seconds);
      row.appendChild(el("span", "run-detail", detail));
      row.appendChild(el("span", "run-at", hhmm(run.finished_at || run.started_at)));
      box.appendChild(row);
    });

    trimToFit(box, runs.length);
  }

  /*
   * The rail's height changes with whether the attention band is showing, so
   * how many rows fit is not knowable server-side. Drop whole rows until the
   * list fits and report the count: a row sliced in half reads as a broken
   * board, and D11 rules out scrolling to reach the rest.
   */
  function trimToFit(box, total) {
    var note = null;
    var guard = 0;
    while (box.scrollHeight > box.clientHeight && box.children.length > 1 && guard++ < 50) {
      if (note) box.removeChild(note);
      var last = box.children[box.children.length - 1];
      box.removeChild(last);
      var hidden = total - box.children.length;
      note = el("p", "empty", "+ " + hidden + " more");
      box.appendChild(note);
    }
  }

  // --- system -------------------------------------------------------------

  function sysrow(grid, key, value, lvl) {
    var row = el("div", "sysrow");
    row.appendChild(el("span", "sys-key", key));
    var v = el("span", "sys-val", value);
    if (lvl) v.setAttribute("data-level", lvl);
    row.appendChild(v);
    grid.appendChild(row);
  }

  function renderSystem(s) {
    var grid = $("sysgrid");
    clear(grid);
    var svc = s.service || {};
    var sys = s.system || {};
    var wifi = sys.wifi || {};
    var containers = {};
    (s.containers || []).forEach(function (c) {
      containers[c.name] = c;
    });

    text(
      $("system-source"),
      "profile: " + (svc.profile || "?") + " · up " + uptime(svc.uptime_seconds)
    );

    sysrow(grid, "scheduler", svc.scheduler_paused ? "paused" : "running",
      svc.scheduler_paused ? "crit" : "ok");
    sysrow(grid, "jobs enabled", (svc.jobs_enabled || 0) + " of " + (svc.jobs_total || 0));

    ["homeassistant", "mosquitto"].forEach(function (name) {
      var c = containers[name];
      sysrow(grid, name, c ? (c.reachable ? "reachable" : "DOWN") : "—",
        c ? (c.reachable ? "ok" : "crit") : "");
    });

    sysrow(grid, "cpu temp",
      sys.cpu_temp_c === null || sys.cpu_temp_c === undefined
        ? "—" : sys.cpu_temp_c.toFixed(1) + "°C",
      level(sys.cpu_temp_c, 70, 80));
    sysrow(grid, "load",
      sys.load_1 === null || sys.load_1 === undefined ? "—" : sys.load_1.toFixed(2),
      level(sys.load_1, 4, 8));
    sysrow(grid, "memory",
      pct(sys.mem_used_percent) + " of " + gib(sys.mem_total_bytes),
      level(sys.mem_used_percent, 80, 92));
    sysrow(grid, "disk",
      pct(sys.disk_used_percent) + " of " + gib(sys.disk_total_bytes),
      level(sys.disk_used_percent, 80, 90));
    sysrow(grid, "wi-fi",
      wifi.signal_dbm === null || wifi.signal_dbm === undefined
        ? "—" : Math.round(wifi.signal_dbm) + " dBm",
      level(wifi.signal_dbm === null ? null : -wifi.signal_dbm, 67, 75));
    sysrow(grid, "host uptime", uptime(sys.uptime_seconds));
  }

  // --- staleness ----------------------------------------------------------

  function refreshStaleness() {
    var banner = $("stale");
    var title = $("stale-title");
    var detail = $("stale-detail");

    if (lastSuccessAt === null) {
      document.body.setAttribute("data-stale", "true");
      banner.hidden = false;
      text(title, "NO DATA");
      text(detail, "never reached the atlas API since this page loaded");
      return;
    }

    var age = Date.now() - lastSuccessAt;
    if (age > STALE_AFTER_MS) {
      document.body.setAttribute("data-stale", "true");
      banner.hidden = false;
      text(title, "STALE DATA");
      text(
        detail,
        "last successful update " +
          humanAge(age) +
          " ago (" +
          clockTime(new Date(lastSuccessAt)) +
          ") — the API is not responding"
      );
    } else {
      document.body.setAttribute("data-stale", "false");
      banner.hidden = true;
    }
  }

  // --- canvas scaling (the design's fit()) --------------------------------

  function fit() {
    var wrap = $("wrap");
    var board = $("board");
    if (!wrap || !board) return;
    var vw = wrap.clientWidth;
    var vh = wrap.clientHeight;
    if (!vw || !vh) return;

    var width = Math.round((BOARD_H * vw) / vh);
    width = Math.max(BOARD_MIN_W, Math.min(BOARD_MAX_W, width));
    board.style.width = width + "px";
    board.style.height = BOARD_H + "px";

    board.style.transform = "scale(" + Math.min(vw / width, vh / BOARD_H) + ")";
  }

  // --- render + poll ------------------------------------------------------

  function render(s) {
    renderHeader(s);
    renderAttention(s);
    renderTimeline(s);
    renderWeather(s);
    renderRuns(s);
    renderSystem(s);
  }

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
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (snapshot) {
        lastSnapshot = snapshot;
        lastSuccessAt = Date.now();
        render(snapshot);
      })
      .catch(function () {
        // Keep the last good data on screen. Blanking would destroy the
        // failure information that is the reason the display exists; the
        // banner says it is no longer current.
        if (lastSnapshot === null) lastSuccessAt = null;
      })
      .then(function () {
        clearTimeout(timer);
        refreshStaleness();
      });
  }

  window.addEventListener("resize", fit);
  fit();
  renderClock();
  refreshStaleness();
  poll();

  setInterval(poll, POLL_INTERVAL_MS);
  setInterval(function () {
    renderClock();
    moveNowLine(lastSnapshot);
    // Independent of the poll: the age must keep climbing while requests fail.
    refreshStaleness();
  }, 1000);
})();
