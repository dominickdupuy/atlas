/*
 * atlas ops board client — implementation of the "Atlas Board" design canvas.
 *
 * Output only (D11) in the sense that matters: no button, form, input or
 * link exists, nothing is focusable, and no workflow requires acting on the
 * screen. There is now exactly one handler — a keydown listener for the desk
 * dial, which pans the calendar between days. It is an input device sitting
 * next to the board, not on it, and it can only change which days are drawn:
 * nothing it does has a consequence, and the view returns to today on its own
 * (DAY_VIEW_IDLE_MS) so an abandoned board never keeps showing a stale day.
 * Nothing labels the panned state — the day columns carry their own dates, and
 * TODAY appears on no column once you have panned away from it.
 *
 * The header draws that dial (see "the drawn desk dial" below) so a press has
 * somewhere to land: keys light, the ring turns. The drawing is output like
 * everything else here — it mirrors the hardware and cannot be operated.
 *
 * The design's DCLogic class is translated to plain DOM here — D17 rules out a
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

  // The desk dial (LifeSmart ColoPlay) sends these as Shift+Alt+<digit>,
  // one keypress per detent. Swap the two if the dial pans the wrong way.
  var DIAL_BACK = "Digit5";
  var DIAL_FORWARD = "Digit6";
  // Its four corner keys, under the same Shift+Alt prefix. The device numbers
  // them in reading order — top-left, top-right, bottom-left, bottom-right —
  // not clockwise, which is what the drawn keys' data-key attributes carry.
  // If a key turns out to send something else, press it: an unmapped Shift+Alt
  // combination prints its code on the drawn dial's screen, which is where
  // the value to put in this list comes from.
  var DECK_KEY_CODES = ["Digit1", "Digit2", "Digit3", "Digit4"];
  // The top-left key selects the screen already on the wall, so it stays lit.
  var DECK_HOME_KEY = 1;
  // Long enough to register as a press from across the desk, short enough
  // that a fast series of presses still shows every one of them.
  var DECK_PRESS_MS = 260;
  // How long a key's own report holds the dial screen before the display mode
  // takes it back.
  var DECK_SCREEN_MS = 1600;
  // Degrees of ring per detent. A third of a turn: visible at 76px, and never
  // so far that two quick detents look like one.
  var DECK_DETENT_DEG = 34;
  // Long enough to read a day you panned to, short enough that a board left
  // alone is showing today again before anyone next glances at it.
  var DAY_VIEW_IDLE_MS = 45000;
  // The health screen (spec §7). The dial scrolls it; it snaps back to the
  // top a minute after the last detent, and hands the wall back to the ops
  // board after ten minutes, so a screen left on health is not what anyone
  // finds tomorrow morning.
  var HEALTH_SCROLL_PX = 140;
  var HEALTH_SNAP_MS = 60000;
  var HEALTH_RETURN_MS = 600000;

  var lastSuccessAt = null;
  var lastSnapshot = null;
  // Offset of the leftmost day column. 0 is today; the dial moves it within
  // whatever range the server served.
  var dayView = 0;
  var dayViewTimer = null;
  var screenName = "ops";
  var healthSnapTimer = null;
  var healthReturnTimer = null;
  // The drawn dial's own state: which of its screen the mode wants, whose
  // message is holding that screen, and how far the ring has been turned.
  var deckModeLabel = "INIT";
  var deckScreenTimer = null;
  var deckDialTimer = null;
  var deckRingAngle = 0;

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
    $("deck").setAttribute("data-mode", mode);
    var label = mode.replace(/_/g, " ");
    if (mode === "APPROVAL_PENDING") {
      // No wording survives a 27px face at a legible size, and the amber fill
      // and the pulse already say "approval" from across the room. So the
      // screen carries the one thing they cannot: how many.
      label = s.approvals_total ? String(s.approvals_total) : "!";
    }
    // While the health screen is up the dial's face says so; the display mode
    // takes the face back when the board returns to ops.
    if (screenName === "health") label = "HEALTH";
    deckModeLabel = label;
    // A key's report owns the screen while it lasts; a poll landing under it
    // must not blank it out mid-message.
    if (deckScreenTimer === null) deckScreenText(label);
  }

  // --- the drawn desk dial ------------------------------------------------

  /*
   * The header's model of the LifeSmart ColoPlay standing beside the monitor.
   * It is a mirror: it lights up because the hardware was pressed, and it
   * cannot be pressed itself. Its whole job is to answer, from across the
   * room, the question the hardware cannot — did that press arrive?
   */

  /*
   * Size the screen's type to what is going on it. The face is a 27px circle
   * with no room for an ellipsis, so the choice is between smaller type and
   * a clipped word, and a clipped word says nothing. Lines wrap on their own;
   * what has to fit across the face is the longest single word. The smallest
   * step is below reading size and exists only for the key-code diagnostic,
   * which is read from a foot away and never during normal operation.
   */
  function deckScreenText(label) {
    var node = $("mode-text");
    text(node, label);
    var longest = 0;
    label.split(/\s+/).forEach(function (word) {
      if (word.length > longest) longest = word.length;
    });
    node.setAttribute("data-fit", longest <= 3 ? "full" : longest <= 6 ? "tight" : "min");
  }

  /* Lend the screen to a message, then hand it back to the display mode. */
  function deckSay(label) {
    if (deckScreenTimer !== null) clearTimeout(deckScreenTimer);
    deckScreenText(label);
    deckScreenTimer = setTimeout(function () {
      deckScreenTimer = null;
      deckScreenText(deckModeLabel);
    }, DECK_SCREEN_MS);
  }

  function deckPressKey(key) {
    var node = document.querySelector('.deck-key[data-key="' + key + '"]');
    if (node === null) return;
    node.setAttribute("data-press", "true");
    setTimeout(function () {
      node.removeAttribute("data-press");
    }, DECK_PRESS_MS);
  }

  function deckTurn(step) {
    var dial = $("deck-dial");
    deckRingAngle += step * DECK_DETENT_DEG;
    $("deck-ring").style.transform = "rotate(" + deckRingAngle + "deg)";
    dial.setAttribute("data-turn", "true");
    if (deckDialTimer !== null) clearTimeout(deckDialTimer);
    deckDialTimer = setTimeout(function () {
      deckDialTimer = null;
      dial.removeAttribute("data-turn");
    }, DECK_PRESS_MS);
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

  // --- day panning --------------------------------------------------------

  function dayBounds(allDays, visibleCount) {
    if (!allDays.length) return { min: 0, max: 0 };
    var first = allDays[0].day_offset;
    var last = allDays[allDays.length - 1].day_offset;
    // The leftmost column can go no further right than the last full window,
    // so the rightmost reachable day is exactly the last one served.
    return { min: first, max: Math.max(first, last - visibleCount + 1) };
  }

  function armDayViewReset() {
    if (dayViewTimer !== null) clearTimeout(dayViewTimer);
    dayViewTimer = setTimeout(function () {
      dayViewTimer = null;
      if (dayView === 0) return;
      dayView = 0;
      if (lastSnapshot !== null) renderTimeline(lastSnapshot);
    }, DAY_VIEW_IDLE_MS);
  }

  function shiftDays(step) {
    if (lastSnapshot === null) return;
    var visibleCount = lastSnapshot.timeline_visible_days || 3;
    var bounds = dayBounds(lastSnapshot.timeline_days || [], visibleCount);
    var next = Math.min(bounds.max, Math.max(bounds.min, dayView + step));
    if (next !== dayView) {
      dayView = next;
      renderTimeline(lastSnapshot);
    }
    // Re-arm even when the dial hit the end stop: the user is still there.
    armDayViewReset();
  }

  /* A compact rendering of a keystroke, for the dial's 48px screen: modifier
     initials, then the code with its Digit/Key/Numpad prefix dropped. Shift+
     Alt+Digit3 reads "AS 3". */
  function describeKey(e) {
    var mods = "";
    if (e.ctrlKey) mods += "C";
    if (e.altKey) mods += "A";
    if (e.shiftKey) mods += "S";
    if (e.metaKey) mods += "M";
    var code = (e.code || e.key || "?").replace(/^(Digit|Key|Numpad|Arrow)/, "");
    return mods === "" ? code : mods + " " + code;
  }

  function onDialKey(e) {
    var prefixed = e.shiftKey && e.altKey && !e.ctrlKey && !e.metaKey;

    if (prefixed) {
      var step = e.code === DIAL_BACK ? -1 : e.code === DIAL_FORWARD ? 1 : 0;
      if (step !== 0) {
        e.preventDefault();
        deckTurn(step);
        if (screenName === "health") scrollHealth(step);
        else shiftDays(step);
        return;
      }

      var key = DECK_KEY_CODES.indexOf(e.code) + 1;
      if (key > 0) {
        e.preventDefault();
        deckPressKey(key);
        // Two screens exist now. The other two keys still say they arrived
        // and nothing more, rather than pretending to switch to something
        // that does not exist yet.
        if (key === DECK_HOME_KEY) setScreen("ops");
        else if (key === 2) setScreen("health");
        else deckSay("K" + key);
        return;
      }
    }

    // Everything else lands here. The dial is the only input device in this
    // room, so a key this file does not recognise is one of its keys sending
    // something not listed above — and a key that reports nothing is
    // indistinguishable from a key that is not bound on the device at all.
    // Printing the keystroke on the dial's own screen collapses that
    // difference: what appears is what to map, and nothing appearing means
    // the press never left the hardware.
    //
    // Held modifiers arrive as keydowns of their own; reporting those would
    // bury the keystroke they belong to under "SHIFTLEFT".
    if (/^(Shift|Alt|Control|Meta|CapsLock)/.test(e.code || "")) return;
    // The reload keys stay the browser's: this board is refreshed by hand
    // often enough that swallowing them would cost more than it explains.
    if (e.code === "F5" || (e.ctrlKey && e.code === "KeyR")) return;
    deckSay(describeKey(e));
  }

  function setScreen(name) {
    if (screenName === name) {
      // Pressing the key for the screen already up restarts its clock rather
      // than doing nothing: the person is standing there, reading it.
      if (name === "health") armHealthTimers();
      return;
    }
    screenName = name;
    var health = name === "health";
    $("main").hidden = health;
    $("main-health").hidden = !health;
    [1, 2].forEach(function (key) {
      var node = document.querySelector('.deck-key[data-key="' + key + '"]');
      if (node === null) return;
      if ((key === 2) === health) node.setAttribute("data-active", "true");
      else node.removeAttribute("data-active");
    });
    if (health) {
      $("main-health").scrollTop = 0;
      // Arm the return-to-ops timer before rendering: renderHealth can throw
      // on a malformed document (a schema-1 body missing a region an older
      // analysis version never sent), and the screen has already switched by
      // this point. Arming first guarantees a way back to ops even then,
      // instead of leaving the wall on a blank screen with no timer running.
      armHealthTimers();
      if (lastSnapshot !== null) renderHealth(lastSnapshot);
    } else {
      if (healthSnapTimer !== null) clearTimeout(healthSnapTimer);
      if (healthReturnTimer !== null) clearTimeout(healthReturnTimer);
      healthSnapTimer = null;
      healthReturnTimer = null;
    }
    deckSay(health ? "HEALTH" : "OPS");
  }

  function armHealthTimers() {
    if (healthReturnTimer !== null) clearTimeout(healthReturnTimer);
    healthReturnTimer = setTimeout(function () {
      healthReturnTimer = null;
      setScreen("ops");
    }, HEALTH_RETURN_MS);
  }

  function armHealthSnap() {
    if (healthSnapTimer !== null) clearTimeout(healthSnapTimer);
    healthSnapTimer = setTimeout(function () {
      healthSnapTimer = null;
      $("main-health").scrollTop = 0;
    }, HEALTH_SNAP_MS);
  }

  function scrollHealth(step) {
    var region = $("main-health");
    var limit = region.scrollHeight - region.clientHeight;
    region.scrollTop = Math.max(0, Math.min(limit, region.scrollTop + step * HEALTH_SCROLL_PX));
    armHealthSnap();
    armHealthTimers();
  }

  function renderTimeline(s) {
    var startHour = s.timeline_start_hour;
    var endHour = s.timeline_end_hour;
    var hours = endHour - startHour;
    var span = hours * 60;
    var hourPct = 100 / hours;

    // Which days are on screen. The server sends a fortnight either side of
    // today; the board draws a window of it, positioned by the dial.
    var allDays = s.timeline_days || [];
    var visibleCount = s.timeline_visible_days || 3;
    var bounds = dayBounds(allDays, visibleCount);
    dayView = Math.min(bounds.max, Math.max(bounds.min, dayView));
    var days = allDays.filter(function (day) {
      return day.day_offset >= dayView && day.day_offset < dayView + visibleCount;
    });

    var cal = s.calendar || {};
    text($("timeline-title"), cal.configured ? "CALENDAR" : "SCHEDULE");
    var source = cal.detail || "atlas jobs only";
    if (cal.configured && cal.synced_at) {
      // Say when, not just what: a published feed can lag by hours, and the
      // board should never imply it is live when it is not.
      //
      // Count the days on screen, not the whole fetched range. The snapshot
      // carries a fortnight so the dial can pan without refetching, and
      // printing that total beside three columns would overstate the day.
      var shown = days.reduce(function (total, day) {
        return total + day.entry_count;
      }, 0);
      source = shown + (shown === 1 ? " event · synced " : " events · synced ") +
        hhmm(cal.synced_at);
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
    // A recurring band is a range backdrop, not a competitor for space.
    var bands = all.filter(function (p) {
      return p.entry.count > 1;
    });
    var items = all.filter(function (p) {
      return p.entry.count <= 1;
    });

    /*
     * Width is decided per CLUSTER of overlapping events, not per day.
     * Counting lanes across the whole day meant one 1pm conflict squeezed
     * every other event that day into half a column for no reason: a lecture
     * alone at 9am has nothing to share with.
     *
     * A cluster is a run of events joined transitively by overlap — A meets
     * B, B meets C, so all three share — which is what makes the widths line
     * up instead of stepping mid-block.
     */
    var EPSILON = 0.01;
    var clusters = [];
    var current = [];
    var clusterEnd = -Infinity;

    items.forEach(function (p) {
      if (current.length && p.top >= clusterEnd - EPSILON) {
        clusters.push(current);
        current = [];
        clusterEnd = -Infinity;
      }
      current.push(p);
      clusterEnd = Math.max(clusterEnd, p.top + p.height);
    });
    if (current.length) clusters.push(current);

    clusters.forEach(function (cluster) {
      var laneEnds = [];
      cluster.forEach(function (p) {
        var lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > p.top + EPSILON) {
          lane++;
        }
        laneEnds[lane] = p.top + p.height;
        p.lane = lane;
      });
      var lanes = Math.max(1, laneEnds.length);
      cluster.forEach(function (p) {
        p.lanes = lanes;
        p.band = false;
      });
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

  function uvBand(uv) {
    if (uv === null || uv === undefined) return ["", ""];
    if (uv >= 11) return ["extreme", "extreme"];
    if (uv >= 8) return ["very-high", "very high"];
    if (uv >= 6) return ["high", "high"];
    if (uv >= 3) return ["moderate", "moderate"];
    return ["low", "low"];
  }

  function metric(label, value, small, uvLevel) {
    var box = el("div", "wx-metric");
    box.appendChild(el("dt", null, label));
    var dd = el("dd", null, value);
    if (small) {
      dd.appendChild(el("small", null, small));
    }
    if (uvLevel) dd.setAttribute("data-uv", uvLevel);
    box.appendChild(dd);
    return box;
  }

  var UV_SCALE_MAX = 12;

  function svgEl(tag, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  /*
   * Two series on one hourly grid: rain chance as bars against the right
   * axis (0-100%), UV as a line against the left axis (0-12).
   *
   * They share an x-axis because the question is a single one — "when today
   * is it going to rain on me, and when will the sun be worst" — and reading
   * that off two separate charts means doing the alignment in your head from
   * across a room.
   */
  function conditionsChart(hourly, isToday) {
    var wrap = el("div");
    var visible = hourly.filter(function (h) {
      return h.hour >= 6 && h.hour <= 22;
    });
    if (!visible.length) return wrap;

    var chart = el("div", "wx-chart");

    // Titles live in their own row so the ticks can span the exact height of
    // the plot: a "12" that does not line up with the top gridline is
    // decoration, not an axis.
    chart.appendChild(el("span", "wx-ax-title wx-title-left", "UV"));
    chart.appendChild(el("span", null, ""));
    chart.appendChild(el("span", "wx-ax-title wx-title-right", "RAIN"));

    var left = el("div", "wx-ax wx-ax-left");
    [12, 8, 4, 0].forEach(function (tick) {
      left.appendChild(el("span", null, tick));
    });
    chart.appendChild(left);

    var plot = el("div", "wx-plot");
    var bars = el("div", "wx-bars");
    visible.forEach(function (h) {
      var bar = el("div", "wx-bar");
      bar.setAttribute("data-wet", h.millimetres > 0.05 ? "true" : "false");
      var fill = el("i");
      fill.style.height = Math.max(1.5, h.probability_pct) + "%";
      bar.appendChild(fill);
      bars.appendChild(bar);
    });
    plot.appendChild(bars);

    // preserveAspectRatio=none lets the polyline stretch to the plot box;
    // non-scaling-stroke keeps the line from stretching with it.
    var svg = svgEl("svg", {
      class: "wx-uv",
      viewBox: "0 0 100 100",
      preserveAspectRatio: "none",
    });
    var step = 100 / visible.length;
    var points = visible
      .map(function (h, index) {
        var x = index * step + step / 2;
        var uv = Math.max(0, Math.min(UV_SCALE_MAX, h.uv_index || 0));
        var y = 100 - (uv / UV_SCALE_MAX) * 100;
        return x.toFixed(2) + "," + y.toFixed(2);
      })
      .join(" ");
    svg.appendChild(
      svgEl("polyline", { points: points, class: "wx-uv-line", "vector-effect": "non-scaling-stroke" })
    );
    plot.appendChild(svg);

    if (isToday) {
      // Same red as the calendar's now-line, for the same reason: without a
      // reference for "now", a 17-hour curve is hard to read against the
      // moment you are standing in.
      var marker = nowMarker(visible);
      if (marker) plot.appendChild(marker);
    }
    chart.appendChild(plot);

    var right = el("div", "wx-ax wx-ax-right");
    ["100%", "50%", "0"].forEach(function (tick) {
      right.appendChild(el("span", null, tick));
    });
    chart.appendChild(right);

    var axis = el("div", "wx-axis");
    ["6 AM", "NOON", "6 PM", "10 PM"].forEach(function (label) {
      axis.appendChild(el("span", null, label));
    });
    chart.appendChild(axis);

    wrap.appendChild(chart);
    return wrap;
  }

  /*
   * Horizontal position of "now" inside the chart's hour range. The plot
   * spans from the first visible hour to the end of the last, since each bar
   * covers a whole hour slot.
   */
  function nowFraction(visible) {
    if (!visible.length) return null;
    var first = visible[0].hour;
    var last = visible[visible.length - 1].hour + 1;
    var now = new Date();
    var hours = now.getHours() + now.getMinutes() / 60;
    var fraction = ((hours - first) / (last - first)) * 100;
    return fraction < 0 || fraction > 100 ? null : fraction;
  }

  function nowMarker(visible) {
    var fraction = nowFraction(visible);
    if (fraction === null) return null;

    // Line only: the header clock already gives the time, and repeating it
    // here just adds ink to the chart it is meant to clarify.
    var line = el("div", "wx-now-line");
    line.id = "wx-nowline";
    line.style.left = fraction + "%";
    return line;
  }

  function moveWeatherNow(s) {
    var line = $("wx-nowline");
    if (!line || !s || !s.weather || !s.weather.days || !s.weather.days.length) return;
    var visible = (s.weather.days[0].hourly || []).filter(function (h) {
      return h.hour >= 6 && h.hour <= 22;
    });
    var fraction = nowFraction(visible);
    if (fraction === null) {
      line.hidden = true;
      return;
    }
    line.hidden = false;
    line.style.left = fraction + "%";
  }

  function weatherDay(day) {
    var box = el("div", "wx-day");

    var head = el("div", "wx-day-head");
    head.appendChild(el("span", "wx-day-label", day.label.toUpperCase()));
    head.appendChild(el("span", "wx-day-summary", day.summary));
    var hilo = el("span", "wx-hilo");
    hilo.appendChild(el("span", null, fahrenheit(day.high_c) + "°"));
    hilo.appendChild(el("span", "low", "  " + fahrenheit(day.low_c) + "°"));
    head.appendChild(hilo);
    box.appendChild(head);

    var metrics = el("div", "wx-metrics");
    var band = uvBand(day.uv_index_max);
    metrics.appendChild(
      metric(
        "uv",
        day.uv_index_max === null || day.uv_index_max === undefined
          ? "—"
          : day.uv_index_max.toFixed(1),
        band[1],
        band[0]
      )
    );
    metrics.appendChild(metric("precip", day.precipitation_probability_pct + "%"));
    metrics.appendChild(metric("rain", day.precipitation_mm.toFixed(1), "mm"));
    box.appendChild(metrics);

    if (day.hourly && day.hourly.length) {
      box.appendChild(conditionsChart(day.hourly, day.label === "Today"));
    }
    return box;
  }

  function renderWeather(s) {
    var wx = s.weather || {};
    var body = $("weather-body");
    clear(body);
    text($("weather-place"), wx.synced_at ? "synced " + hhmm(wx.synced_at) : "unavailable");

    if (!wx.available) {
      var box = el("div", "wx-unavailable");
      box.appendChild(el("strong", null, "Not connected"));
      box.appendChild(document.createTextNode(wx.detail || "no weather source configured"));
      body.appendChild(box);
      return;
    }

    var now = el("div", "wx-now");
    var temp = el("div", "wx-temp");
    temp.appendChild(el("span", "wx-deg", fahrenheit(wx.current_c)));
    temp.appendChild(el("span", "wx-unit", "°F"));
    now.appendChild(temp);
    var right = el("div", "wx-now-right");
    right.appendChild(el("span", "wx-summary", wx.current_summary || "—"));
    right.appendChild(el("span", "wx-place", "Gainesville, FL"));
    if (wx.detail) right.appendChild(el("span", "wx-place", "refresh failing"));
    now.appendChild(right);
    body.appendChild(now);

    (wx.days || []).forEach(function (day) {
      body.appendChild(weatherDay(day));
    });
  }

  // --- runs ---------------------------------------------------------------

  function runWhen(iso) {
    if (!iso) return "—";
    var at = new Date(iso);
    var today = new Date();
    var prefix = at.toDateString() === today.toDateString()
      ? "" : at.toLocaleDateString([], { weekday: "short" }) + " ";
    return prefix + hhmm(iso);
  }

  function renderRuns(s) {
    var box = $("runs");
    clear(box);
    var timeline = s.run_timeline || {};
    var pending = timeline.pending || [];
    var history = timeline.history || [];
    var rows = pending.concat(history);
    var pendingTotal = timeline.pending_total || 0;
    var historyTotal = timeline.history_total || 0;
    var total = pendingTotal + historyTotal;
    var note = $("runs-note");
    note.hidden = !s.runs_note || total > 0;
    if (s.runs_note) text(note, s.runs_note);

    text($("runs-source"), total
      ? pendingTotal + " pending · " + historyTotal + " recent" : "idle");

    if (!rows.length) {
      box.appendChild(el("p", "empty", "nothing queued or run yet"));
      return;
    }

    rows.forEach(function (run) {
      var row = el("div", "run");
      row.setAttribute("data-state", run.state);
      var job = el("span", "run-job", run.name);
      job.appendChild(el("span", "run-origin", run.origin));
      row.appendChild(job);
      row.appendChild(el("span", "run-state", run.state.replace(/_/g, " ").toUpperCase()));
      row.appendChild(el("span", "run-detail", run.detail));
      row.appendChild(el("span", "run-at", runWhen(run.when)));
      box.appendChild(row);
    });

    trimToFit(box, total);
  }

  // Count both server-capped entries and whole rows removed to fit this screen.
  function trimToFit(box, total) {
    var note = el("p", "empty", "");
    var guard = 0;
    for (;;) {
      var rows = box.querySelectorAll(".run");
      if (total - rows.length > 0) {
        text(note, "+ " + (total - rows.length) + " more");
        box.appendChild(note); // appendChild moves it back to the end
      } else if (note.parentNode === box) {
        box.removeChild(note);
      }
      // Keeping the last row is the point: a panel showing nothing but
      // "+ 6 more" tells you less than one run and an honest count.
      if (box.scrollHeight <= box.clientHeight || rows.length <= 1 || guard++ >= 50) return;
      box.removeChild(rows[rows.length - 1]);
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

    text(
      $("system-source"),
      "profile: " + (svc.profile || "?") + " · up " + uptime(svc.uptime_seconds)
    );

    sysrow(grid, "cpu temp",
      sys.cpu_temp_c === null || sys.cpu_temp_c === undefined
        ? "—" : sys.cpu_temp_c.toFixed(1) + "°C",
      level(sys.cpu_temp_c, 70, 80));
    // 100% is every core busy, not a ceiling: the thresholds are the old
    // load-of-4 and load-of-8 warnings restated for this machine's cores.
    sysrow(grid, "load", pct(sys.load_percent), level(sys.load_percent, 100, 200));
    sysrow(grid, "memory",
      pct(sys.mem_used_percent) + " of " + gib(sys.mem_total_bytes),
      level(sys.mem_used_percent, 80, 92));
    sysrow(grid, "disk",
      pct(sys.disk_used_percent) + " of " + gib(sys.disk_total_bytes),
      level(sys.disk_used_percent, 80, 90));
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

  // --- health screen: drawing ---------------------------------------------

  /* Every chart on this screen is hand-drawn SVG on the canvas's own pixel
     grid, exactly like the weather chart: no library reaches this Pi, and a
     fixed viewBox keeps the type scale identical to the rest of the board. */
  function healthSvg(width, height) {
    return svgEl("svg", {
      class: "health-chart",
      viewBox: "0 0 " + width + " " + height,
      width: String(width),
      height: String(height),
      preserveAspectRatio: "none",
    });
  }

  function num(value, digits, fallback) {
    if (value === null || value === undefined || isNaN(value)) return fallback || "—";
    return Number(value).toFixed(digits === undefined ? 0 : digits);
  }

  /* Scale a value that may be absent, checking BEFORE the arithmetic.
     num() catches null, but only if it is handed the null. JavaScript
     coerces `null / 60` and `null * 100` to 0, so scaling first turns
     "no data" into a confident "0.0 h" or "0 %" — a fabricated number
     presented as a real one, on a screen whose whole worth is that you
     can trust it at a glance. */
  function scaled(value, factor, digits) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    return num(value * factor, digits);
  }

  /* The last `days` calendar dates ending at the document's own timestamp, as
     YYYY-MM-DD. The night lists carry only nights the watch was worn, so the
     charts need the calendar to put a gap where a night is missing — which is
     45% of them, and the single most important caveat on this screen. */
  function dayKeys(endIso, days) {
    var end = new Date(endIso);
    var keys = [];
    for (var i = days - 1; i >= 0; i--) {
      var day = new Date(end.getTime() - i * 86400000);
      keys.push(
        day.getFullYear() + "-" + pad(day.getMonth() + 1) + "-" + pad(day.getDate())
      );
    }
    return keys;
  }

  /* A line over a fixed number of slots, with gaps where a value is missing.
     `options`: {width, height, values (array of number|null), min, max, band}
     where `band` is an optional {low, high} shaded range. */
  function sparkline(options) {
    var w = options.width;
    var h = options.height;
    var svg = healthSvg(w, h);
    var values = options.values;
    var min = options.min;
    var max = options.max;
    var span = max - min || 1;
    var step = values.length > 1 ? w / (values.length - 1) : w;

    function y(value) {
      return h - ((value - min) / span) * h;
    }

    if (options.band) {
      svg.appendChild(
        svgEl("rect", {
          x: "0",
          y: String(y(options.band.high)),
          width: String(w),
          height: String(Math.max(1, y(options.band.low) - y(options.band.high))),
          class: "health-band",
        })
      );
    }

    var run = [];
    values.forEach(function (value, index) {
      if (value === null || value === undefined) {
        if (run.length > 1) {
          svg.appendChild(
            svgEl("polyline", {
              points: run.join(" "),
              class: "health-line",
              "vector-effect": "non-scaling-stroke",
            })
          );
        }
        run = [];
        return;
      }
      run.push(index * step + "," + y(value));
    });
    if (run.length > 1) {
      svg.appendChild(
        svgEl("polyline", {
          points: run.join(" "),
          class: "health-line",
          "vector-effect": "non-scaling-stroke",
        })
      );
    }
    return svg;
  }

  function tileNode(title, value, arrow, state, lines) {
    var node = el("div", "health-tile");
    node.setAttribute("data-state", state || "OK");
    node.appendChild(el("div", "health-tile-title", title));
    var head = el("div", "health-tile-value", value);
    if (arrow && arrow !== "flat") {
      var mark = el("span", "health-arrow");
      mark.setAttribute("data-arrow", arrow);
      head.appendChild(mark);
    }
    node.appendChild(head);
    (lines || []).forEach(function (line) {
      node.appendChild(el("div", "health-tile-line", line));
    });
    return node;
  }

  function renderHealthTiles(doc) {
    var host = $("health-tiles");
    clear(host);

    // The STATUS tile is the only tile with chips, and the only one that
    // never shows a raw number other than its two drivers (spec §4.8.6).
    var status = el("div", "health-tile health-tile-status");
    status.setAttribute("data-state", doc.status.overall);
    status.appendChild(el("div", "health-tile-title", "STATUS"));
    status.appendChild(el("div", "health-tile-value", doc.status.overall));
    var chips = el("div", "health-chips");
    ["sleep", "heart", "fitness", "weight"].forEach(function (domain) {
      var state = doc.status.domains[domain] || "NO DATA";
      var chip = el(
        "span",
        "health-state",
        domain.charAt(0).toUpperCase() + domain.slice(1) + " " + state
      );
      chip.setAttribute("data-state", state);
      chips.appendChild(chip);
    });
    status.appendChild(chips);
    status.appendChild(el("div", "health-drivers", (doc.status.drivers || []).join(" · ")));
    host.appendChild(status);

    (doc.tiles || []).forEach(function (tile) {
      host.appendChild(tileNode(tile.title, tile.value, tile.arrow, tile.state, tile.lines));
    });
  }

  function renderHealthAction(doc) {
    var host = $("health-action");
    clear(host);
    host.appendChild(el("div", "health-action-line", "> " + doc.action));
    (doc.deviations || []).forEach(function (row) {
      var line = el("div", "health-dev");
      line.appendChild(el("span", null, row.label));
      line.appendChild(el("span", null, row.value));
      line.appendChild(el("span", null, row.vs_avg));
      var mark = el("span", "health-arrow");
      mark.setAttribute("data-arrow", row.arrow || "flat");
      line.appendChild(mark);
      var state = el("span", "health-state", row.state);
      state.setAttribute("data-state", row.state);
      line.appendChild(state);
      host.appendChild(line);
    });
    var coverage = doc.coverage || {};
    host.appendChild(
      el(
        "div",
        "health-note",
        "watch worn " +
          num(coverage.nights_with_data_7, 0, "0") +
          "/7 nights · " +
          num(coverage.nights_with_data_60, 0, "0") +
          "/60"
      )
    );
  }

  /* Stacked deep/REM/core per night on a calendar axis, so the 45% of nights
     without the watch appear as gaps rather than being silently closed up.
     A hollow marker sits on the baseline for each of those nights. */
  function sleepBars(doc) {
    var width = 860;
    var height = 300;
    var svg = healthSvg(width, height);
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (doc.sleep.nights || []).forEach(function (night) {
      byDate[night.date] = night;
    });
    var top = 600; // 10 h; every recorded night fits, and the goal line lands high
    var slot = width / keys.length;
    var barWidth = Math.max(4, slot - 3);

    function y(minutes) {
      return height - (Math.min(minutes, top) / top) * height;
    }

    var goal = doc.sleep.goal_h * 60;
    svg.appendChild(
      svgEl("line", {
        x1: "0", x2: String(width), y1: String(y(goal)), y2: String(y(goal)),
        class: "health-goal", "vector-effect": "non-scaling-stroke",
      })
    );

    keys.forEach(function (key, index) {
      var x = index * slot;
      var night = byDate[key];
      if (!night) {
        svg.appendChild(
          svgEl("circle", {
            cx: String(x + barWidth / 2), cy: String(height - 5), r: "3",
            class: "health-missing",
          })
        );
        return;
      }
      var parts = [
        { minutes: night.deep_min, cls: "health-deep" },
        { minutes: night.core_min, cls: "health-core" },
        { minutes: night.rem_min, cls: "health-rem" },
      ];
      var base = 0;
      parts.forEach(function (part) {
        var minutes = part.minutes || 0;
        if (minutes <= 0) return;
        svg.appendChild(
          svgEl("rect", {
            x: String(x), width: String(barWidth),
            y: String(y(base + minutes)),
            height: String(Math.max(1, y(base) - y(base + minutes))),
            class: part.cls, "data-state": night.state,
          })
        );
        base += minutes;
      });
    });

    // The seven-night mean, on the same axis, over nights with data.
    var means = [];
    var window = [];
    keys.forEach(function (key) {
      var night = byDate[key];
      if (night) {
        window.push(night.asleep_min);
        if (window.length > 7) window.shift();
      }
      if (window.length === 7) {
        var total = 0;
        window.forEach(function (value) {
          total += value;
        });
        means.push(total / 7);
      } else {
        means.push(null);
      }
    });
    var points = [];
    means.forEach(function (value, index) {
      if (value === null) return;
      points.push(index * slot + slot / 2 + "," + y(value));
    });
    if (points.length > 1) {
      svg.appendChild(
        svgEl("polyline", {
          points: points.join(" "), class: "health-mean",
          "vector-effect": "non-scaling-stroke",
        })
      );
    }
    return svg;
  }

  function scoreBlock(doc) {
    var box = el("div", "health-col");
    var score = doc.sleep.score;
    box.appendChild(el("div", "health-sub", "SLEEP SCORE"));
    if (!score) {
      box.appendChild(el("div", "health-note", "not enough nights"));
      return box;
    }
    box.appendChild(el("div", "health-tile-value", num(score.total, 0)));
    [
      ["duration", 50],
      ["consistency", 30],
      ["interruptions", 20],
    ].forEach(function (pair) {
      var name = pair[0];
      var line = el(
        "div",
        "health-note",
        name + " " + num(score[name], 0) + "/" + pair[1] + (score.lever === name ? "  <- lever" : "")
      );
      box.appendChild(line);
    });
    box.appendChild(
      el("div", "health-note", "bed by " + (doc.sleep.target_bedtime || "—"))
    );
    return box;
  }

  function heartSparks(doc) {
    var box = el("div", "health-col");
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (doc.heart.nights || []).forEach(function (night) {
      byDate[night.date] = night;
    });

    function series(field) {
      return keys.map(function (key) {
        var night = byDate[key];
        return night && night[field] !== null ? night[field] : null;
      });
    }

    var hr = series("sleep_avg_hr");
    var hrMean = doc.heart.sleep_hr_mean_60d;
    var hrSd = doc.heart.sleep_hr_sd_60d || 0;
    box.appendChild(
      el("div", "health-sub", "SLEEPING HR · 60 NIGHTS · avg " + num(hrMean, 0))
    );
    box.appendChild(
      sparkline({
        width: 440, height: 90, values: hr,
        min: 35, max: 70,
        band: hrMean === null ? null : { low: hrMean - hrSd, high: hrMean + hrSd },
      })
    );

    var hrv = series("hrv");
    var hrvMean = doc.heart.hrv_mean_60d;
    var hrvSd = doc.heart.hrv_7n_sd || 0;
    box.appendChild(el("div", "health-sub", "HRV · 60 NIGHTS · avg " + num(hrvMean, 0) + " ms"));
    box.appendChild(
      sparkline({
        width: 440, height: 90, values: hrv,
        min: 20, max: 140,
        band: hrvMean === null ? null : { low: hrvMean - hrvSd, high: hrvMean + hrvSd },
      })
    );

    var last4 = (doc.heart.hrr_last4 || [])
      .map(function (value) {
        return num(value, 0);
      })
      .join(" ");
    box.appendChild(
      el(
        "div",
        "health-note",
        "RESTING " + num(doc.heart.rhr_latest, 0) +
          " (60d " + num(doc.heart.rhr_60d, 0) + ")" +
          " · RESP " + num(doc.heart.resp_latest, 1) +
          " · HRR last 4: " + (last4 || "—")
      )
    );
    return box;
  }

  function renderHealthSleep(doc) {
    var host = $("health-sleep-body");
    clear(host);
    var left = el("div", "health-col");
    left.appendChild(sleepBars(doc));
    left.appendChild(
      el(
        "div",
        "health-note",
        "deep · core · REM per night, goal " + num(doc.sleep.goal_h, 1) +
          " h, 7-night mean; hollow marker = watch not worn"
      )
    );
    host.appendChild(left);
    var right = el("div", "health-col");
    right.appendChild(scoreBlock(doc));
    right.appendChild(heartSparks(doc));
    host.appendChild(right);
    text(
      $("health-sleep-source"),
      "7n " + scaled(doc.sleep.mean_7n_min, 1 / 60, 1) + " h · 60d " +
        scaled(doc.sleep.mean_60d_min, 1 / 60, 1) + " h · debt " +
        num(doc.sleep.debt_14n_h, 1) + " h"
    );
  }

  function loadBars(weekly) {
    var width = 860;
    var height = 120;
    var svg = healthSvg(width, height);
    var top = 1;
    weekly.forEach(function (week) {
      if (week.trimp > top) top = week.trimp;
    });
    var slot = width / Math.max(1, weekly.length);
    weekly.forEach(function (week, index) {
      var barHeight = (week.trimp / top) * (height - 18);
      svg.appendChild(
        svgEl("rect", {
          x: String(index * slot + 6), width: String(slot - 12),
          y: String(height - 18 - barHeight), height: String(Math.max(1, barHeight)),
          class: "health-load",
        })
      );
    });
    return svg;
  }

  function zoneBar(zones) {
    var width = 120;
    var height = 10;
    var svg = healthSvg(width, height);
    var total = 0;
    zones.forEach(function (value) {
      total += value;
    });
    if (total <= 0) return svg;
    var x = 0;
    zones.forEach(function (value, index) {
      var span = (value / total) * width;
      if (span <= 0) return;
      svg.appendChild(
        svgEl("rect", {
          x: String(x), y: "0", width: String(span), height: String(height),
          class: "health-zone", "data-zone": String(index + 1),
        })
      );
      x += span;
    });
    return svg;
  }

  function renderHealthExercise(doc) {
    var host = $("health-exercise-body");
    clear(host);
    var ex = doc.exercise;
    var capacity = ex.capacity || {};
    var consistency = ex.consistency || {};
    var load = ex.load || {};

    var left = el("div", "health-col");
    left.appendChild(
      el(
        "div",
        "health-sub",
        "CAPACITY " + (capacity.capacity_z === null ? "—" : "z " + num(capacity.capacity_z, 1))
      )
    );
    if (capacity.metric === "speed_at_hr") {
      left.appendChild(
        el(
          "div",
          "health-note",
          "SPEED @" + ex.fixed_hr_band + ": " +
            num(capacity.speed_at_hr_latest, 2) + " m/s · vs prev 5 " +
            num(capacity.speed_at_hr_vs_avg_pct, 0) + " % · trend " +
            (capacity.speed_at_hr_trend || "—") + " (n " + capacity.speed_at_hr_n_of_10 + " of 10)"
        )
      );
    } else {
      left.appendChild(
        el(
          "div",
          "health-note",
          "EF (too few speed samples): " + num(capacity.ef_latest, 4) +
            " · vs prev 5 " + num(capacity.ef_vs_avg_pct, 0) + " %"
        )
      );
    }
    left.appendChild(
      el(
        "div",
        "health-note",
        "HR RECOVERY " + num(capacity.hrr_latest, 0) + " (mean " + num(capacity.hrr_mean, 1) +
          ", SD " + num(capacity.hrr_sd, 1) + ", n " + capacity.hrr_n + ")"
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "CONSISTENCY runs/wk " + num(consistency.runs_per_week_4w, 1) +
          " (tgt " + consistency.runs_per_week_target + ") · strength/wk " +
          num(consistency.strength_per_week_4w, 1) + " (tgt " +
          consistency.strength_per_week_target + ") · this week " +
          consistency.runs_this_week + "/" + consistency.strength_this_week
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "EASY SHARE 60d " + scaled(ex.easy_share_60d, 100, 0) + " % (tgt " +
          scaled(ex.easy_share_target, 100, 0) + " %)"
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "ZONES (HRR, max " + ex.hr_max + ", rest " + num(ex.rhr_anchor, 0) + "): Z2 " +
          num(ex.zone_edges[0], 0) + "-" + num(ex.zone_edges[1], 0) + " · Z3 " +
          num(ex.zone_edges[1], 0) + "-" + num(ex.zone_edges[2], 0) + " · Z4 " +
          num(ex.zone_edges[2], 0) + "-" + num(ex.zone_edges[3], 0) + " · Z5 " +
          num(ex.zone_edges[3], 0) + "+"
      )
    );
    left.appendChild(el("div", "health-sub", "LOAD · 8 WEEKS (Edwards TRIMP)"));
    left.appendChild(loadBars(ex.weekly || []));
    var acwrNote = el(
      "div",
      "health-note",
      "this week " + num(load.load_week, 0) + " · 4-wk avg " + num(load.load_4w_avg, 0) +
        " · " +
        (load.acwr === null
          ? "ACWR hidden (needs 8 workouts in 28 d, have " + load.workouts_28d + ")"
          : "ACWR " + num(load.acwr, 2) + " (caution band, not a rule)")
    );
    /* Spec 4.7: 0.8 to 1.3 neutral, above 1.5 red. The band colours this one
       line and nothing else — no chip, no domain state, no push. The ACWR
       "sweet spot" is contested enough that the spec calls it a caution
       rather than a rule, and a metric that cannot be trusted to raise an
       alarm should not be allowed to raise one. */
    if (load.acwr !== null && load.acwr > 1.5) {
      acwrNote.setAttribute("data-state", "ALERT");
    }
    left.appendChild(acwrNote);
    host.appendChild(left);

    var right = el("div", "health-col");
    right.appendChild(el("div", "health-sub", "RUNS · LAST 8"));
    var rows = el("div", "health-rows");
    (ex.runs || []).forEach(function (run) {
      var row = el("div", "health-row");
      row.setAttribute("data-state", run.state);
      row.appendChild(el("span", null, run.date));
      row.appendChild(el("span", null, num(run.miles, 2) + " mi"));
      row.appendChild(el("span", null, num(run.pace_min_mi, 1) + "/mi"));
      row.appendChild(el("span", null, num(run.avg_hr, 0) + "/" + num(run.max_hr, 0)));
      row.appendChild(
        el("span", null, run.hrr === null ? "—" : "hrr " + num(run.hrr, 0))
      );
      row.appendChild(zoneBar(run.zones || [0, 0, 0, 0, 0]));
      rows.appendChild(row);
    });
    right.appendChild(rows);
    right.appendChild(
      el(
        "div",
        "health-note",
        ex.vdot === null
          ? "VO2 max: none from Apple, and no run long enough to estimate"
          : "VO2 est. (VDOT) " + num(ex.vdot, 1) + " — est. from a training run, reads low"
      )
    );
    host.appendChild(right);
    text(
      $("health-exercise-source"),
      "durability " + num(capacity.durability_28d_min, 0) + " min in 28 d · " +
        num(capacity.durability_60d_min, 0) + " min in 60 d"
    );
  }

  function renderHealthWeight(doc) {
    var host = $("health-weight-body");
    clear(host);
    var weight = doc.weight;
    var width = 1180;
    var height = 160;
    var svg = healthSvg(width, height);
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (weight.readings || []).forEach(function (reading) {
      byDate[reading.date] = reading.kg;
    });

    var values = [];
    keys.forEach(function (key) {
      if (byDate[key] !== undefined) values.push(byDate[key]);
    });
    if (values.length === 0) {
      host.appendChild(
        el(
          "div",
          "health-note",
          "No readings yet. Last known " + num(weight.last_kg, 1) + " kg on " +
            (weight.last_date || "—") + " — the scale writes daily once it is in use."
        )
      );
      text($("health-weight-source"), "weigh-ins 0/7");
      return;
    }

    var low = Math.min.apply(null, values) - 1;
    var high = Math.max.apply(null, values) + 1;
    var slot = width / keys.length;

    function y(kg) {
      return height - ((kg - low) / (high - low || 1)) * height;
    }

    keys.forEach(function (key, index) {
      var kg = byDate[key];
      if (kg === undefined) return;
      svg.appendChild(
        svgEl("circle", {
          cx: String(index * slot + slot / 2), cy: String(y(kg)), r: "3",
          class: "health-dot",
        })
      );
    });
    [
      { value: weight.weight_7d, cls: "health-mean" },
      { value: weight.weight_28d, cls: "health-goal" },
    ].forEach(function (line) {
      if (line.value === null || line.value === undefined) return;
      svg.appendChild(
        svgEl("line", {
          x1: "0", x2: String(width), y1: String(y(line.value)), y2: String(y(line.value)),
          class: line.cls, "vector-effect": "non-scaling-stroke",
        })
      );
    });
    host.appendChild(svg);
    host.appendChild(
      el(
        "div",
        "health-note",
        "7d " + num(weight.weight_7d, 1) + " kg · 28d " + num(weight.weight_28d, 1) +
          " kg · week " + num(weight.week_change, 1) + " kg · BMI " + num(weight.bmi, 1) +
          (weight.bmi_stale ? " (stale)" : "") + " · weigh-ins " + weight.weigh_ins_7d + "/7"
      )
    );
    text($("health-weight-source"), "weigh-ins " + weight.weigh_ins_7d + "/7");
  }

  /* One thin bar per night from bedtime to wake on a 21:00-10:00 axis. The
     bedtime scale in the document is the spec's shifted one (00:40 = 24.67),
     so 21:00 is 21 and 10:00 is 34. */
  function bedtimeStrip(doc) {
    var width = 860;
    var height = 200;
    var axisLow = 21;
    var axisHigh = 34;
    var svg = healthSvg(width, height);
    var nights = doc.sleep.nights || [];
    var rowHeight = nights.length ? Math.min(6, height / nights.length) : 6;

    function x(hour) {
      return ((Math.max(axisLow, Math.min(axisHigh, hour)) - axisLow) / (axisHigh - axisLow)) * width;
    }

    nights.forEach(function (night, index) {
      var wake = night.waketime < 12 ? night.waketime + 24 : night.waketime;
      var y = index * (height / Math.max(1, nights.length));
      svg.appendChild(
        svgEl("rect", {
          x: String(x(night.bedtime)), y: String(y),
          width: String(Math.max(2, x(wake) - x(night.bedtime))),
          height: String(Math.max(2, rowHeight - 1)),
          class: "health-core", "data-state": night.state,
        })
      );
    });
    [
      { hour: doc.sleep.median_bedtime, cls: "health-mean" },
      {
        hour: doc.sleep.target_bedtime
          ? Number(doc.sleep.target_bedtime.slice(0, 2)) +
            Number(doc.sleep.target_bedtime.slice(3)) / 60
          : null,
        cls: "health-goal",
      },
    ].forEach(function (line) {
      if (line.hour === null || line.hour === undefined) return;
      var hour = line.hour < 12 ? line.hour + 24 : line.hour;
      svg.appendChild(
        svgEl("line", {
          x1: String(x(hour)), x2: String(x(hour)), y1: "0", y2: String(height),
          class: line.cls, "vector-effect": "non-scaling-stroke",
        })
      );
    });
    return svg;
  }

  function renderHealthDetail(doc) {
    var host = $("health-detail-body");
    clear(host);
    var left = el("div", "health-col");
    left.appendChild(el("div", "health-sub", "BEDTIME · WAKE (21:00 to 10:00)"));
    left.appendChild(bedtimeStrip(doc));
    left.appendChild(
      el(
        "div",
        "health-note",
        "median bedtime line, target bedtime dashed · SD " +
          num(doc.sleep.bedtime_sd_min, 0) + " min"
      )
    );
    host.appendChild(left);

    var right = el("div", "health-col");
    right.appendChild(
      el(
        "div",
        "health-sub",
        "SOCIAL JET LAG " +
          (doc.sleep.social_jet_lag_h === null
            ? "— (needs 3 free and 3 work nights)"
            : num(doc.sleep.social_jet_lag_h, 1) + " h")
      )
    );
    right.appendChild(el("div", "health-sub", "HR-MIN TIMING · per night"));
    right.appendChild(
      sparkline({
        width: 440, height: 80,
        values: (doc.sleep.nights || []).map(function (night) {
          return night.hr_min_frac;
        }),
        min: 0, max: 1, band: { low: 0.5, high: 0.7 },
      })
    );
    right.appendChild(
      el("div", "health-note", "band = mid; above it is late (a heuristic, never an alert)")
    );
    var naps = 0;
    (doc.sleep.nights || []).forEach(function (night) {
      naps += night.naps_min || 0;
    });
    right.appendChild(el("div", "health-note", "NAPS " + num(naps, 0) + " min over 60 days"));
    host.appendChild(right);
    text($("health-detail-source"), "nights with the watch only");
  }

  function renderHealthFooter(doc) {
    var coverage = doc.coverage || {};
    text(
      $("health-footer"),
      "nights with data " + coverage.nights_with_data_7 + "/7, " +
        coverage.nights_with_data_60 + "/60 · weigh-ins " + coverage.weigh_ins_7d +
        "/7 · last import " + (coverage.last_import_at || "—") +
        " · last HR " + (coverage.last_hr_sample_at || "—") +
        " · last weight " + (coverage.last_weight_at || "—")
    );
  }

  var HEALTH_REGION_IDS = [
    "health-tiles", "health-action", "health-sleep", "health-exercise",
    "health-weight", "health-detail", "health-footer",
  ];

  function renderHealth(s) {
    var note = $("health-unavailable");
    try {
      var health = s.health || { available: false, detail: "no health data" };
      var doc = health.available ? health.document : null;
      if (doc === null) {
        note.hidden = false;
        text(note, "No health board: " + (health.detail || "unknown"));
      } else if (health.stale) {
        // The document still renders below at full detail; this banner is the
        // thing that keeps a week-old document from reading as today's at a
        // glance — the STATUS tile, domain chips, action line and deviation
        // list all sit above the sleep panel's own staleness note.
        note.hidden = false;
        text(note, "Stale health data: " + (health.detail || "unknown"));
      } else {
        note.hidden = true;
      }
      HEALTH_REGION_IDS.forEach(function (id) {
        $(id).hidden = doc === null;
      });
      if (doc === null) return;

      renderHealthTiles(doc);
      renderHealthAction(doc);
      renderHealthSleep(doc);
      renderHealthExercise(doc);
      renderHealthWeight(doc);
      renderHealthDetail(doc);
      renderHealthFooter(doc);
      if (health.stale) {
        text(
          $("health-sleep-source"),
          health.detail + " — numbers below are not today's"
        );
      }
    } catch (err) {
      // The screen has already switched to health by the time this runs
      // (setScreen arms the return-to-ops timer first), so a malformed
      // document must still leave something legible rather than a blank
      // region with nothing on it and nothing counting down.
      HEALTH_REGION_IDS.forEach(function (id) {
        $(id).hidden = true;
      });
      note.hidden = false;
      text(note, "Health screen error — the board will return to ops shortly.");
    }
  }

  // --- render + poll ------------------------------------------------------

  function render(s) {
    renderHeader(s);
    renderAttention(s);
    renderTimeline(s);
    renderWeather(s);
    renderRuns(s);
    renderSystem(s);
    if (screenName === "health") renderHealth(s);
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
        var loadedAssets = document.documentElement.getAttribute("data-asset-version");
        var currentAssets = snapshot.service && snapshot.service.asset_version;
        if (loadedAssets && currentAssets && loadedAssets !== currentAssets) {
          window.location.reload();
          return;
        }
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
  window.addEventListener("keydown", onDialKey);
  fit();
  renderClock();
  refreshStaleness();
  poll();

  setInterval(poll, POLL_INTERVAL_MS);
  setInterval(function () {
    renderClock();
    moveNowLine(lastSnapshot);
    moveWeatherNow(lastSnapshot);
    // Independent of the poll: the age must keep climbing while requests fail.
    refreshStaleness();
  }, 1000);
})();
