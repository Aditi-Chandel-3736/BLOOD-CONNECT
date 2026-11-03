/* ════════════════════════════════════════════════════════
   BloodConnect — page behaviour
   Everything here IMPROVES the experience (instant feedback while
   typing, animations). The server re-checks every value anyway, so
   nothing breaks if JavaScript is turned off.
   ════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* ── Mobile navigation ───────────────────── */
  var nav = document.querySelector(".nav");
  var navToggle = document.querySelector(".nav-toggle");
  if (nav && navToggle) {
    navToggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* ── Dismiss flash messages ──────────────── */
  $all(".flash-close").forEach(function (btn) {
    btn.addEventListener("click", function () { btn.closest(".flash").remove(); });
  });

  /* ── Field error helper ──────────────────── */
  function setError(input, message) {
    var box = document.getElementById(input.id + "-error");
    input.setCustomValidity(message || "");
    input.classList.toggle("is-invalid", !!message);
    input.classList.toggle("is-valid", !message && input.value !== "");
    if (box) box.textContent = message || "";
  }

  /* ════════════════════════════════════════════════════════
     PHONE NUMBER: digits only, never more than 10
     - typing letters/spaces does nothing
     - pasting "+91 98765-43210" becomes "9876543210"
     ════════════════════════════════════════════════════════ */
  function cleanPhone(raw) {
    var digits = String(raw || "").replace(/\D/g, "");
    if (digits.length === 12 && digits.indexOf("91") === 0) digits = digits.slice(2);  // +91 prefix
    else if (digits.length === 11 && digits.charAt(0) === "0") digits = digits.slice(1); // 0 prefix
    return digits;
  }
  function phoneMessage(v) {
    if (!v) return "";
    if (v.length < 10) return "Phone number must be exactly 10 digits — " + (10 - v.length) + " more needed.";
    if (!/^[6-9]/.test(v)) return "Enter a valid Indian mobile number starting with 6, 7, 8 or 9.";
    return "";
  }
  $all("input[data-phone]").forEach(function (input) {
    var counter = document.getElementById(input.id + "-count");
    // The HTML maxlength="10" counts spaces and "+91" too, which would cut
    // autofilled numbers like "98765 43210". JavaScript limits DIGITS instead.
    input.removeAttribute("maxlength");
    function update(showErrors, rawOverride) {
      var digits = cleanPhone(rawOverride !== undefined ? rawOverride : input.value);
      var tooLong = digits.length > 10;
      digits = digits.slice(0, 10);
      if (input.value !== digits) input.value = digits;
      if (counter) counter.textContent = digits.length + "/10";
      var msg = phoneMessage(digits);
      // While typing, only complain once 10 digits are in (or on blur/paste)
      setError(input, (showErrors || digits.length === 10) ? msg : "");
      input.setCustomValidity(msg);           // always blocks submit if invalid
      if (tooLong) {
        var box = document.getElementById(input.id + "-error");
        if (box) box.textContent = "Only 10 digits are allowed — extra digits were removed.";
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.key.length > 1) return; // allow shortcuts, arrows, backspace
      if (!/\d/.test(e.key)) { e.preventDefault(); return; }
      var selected = input.selectionEnd - input.selectionStart;
      if (input.value.length >= 10 && selected === 0) e.preventDefault();
    });
    input.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text");
      e.preventDefault();
      var start = input.selectionStart, end = input.selectionEnd;
      var merged = input.value.slice(0, start) + cleanPhone(text) + input.value.slice(end);
      update(true, merged.replace(/\D/g, ""));
    });
    input.addEventListener("input", function () { update(false); });
    input.addEventListener("blur", function () { update(true); });
    if (input.value) update(false);
  });

  /* ── Show / hide password ────────────────── */
  $all("[data-toggle-password]").forEach(function (btn) {
    var input = document.getElementById(btn.getAttribute("data-toggle-password"));
    if (!input) return;
    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "Hide" : "Show";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", show ? "true" : "false");
    });
  });

  /* ── Password strength meter + rule checklist ── */
  var RULES = [
    function (p) { return p.length >= 8; },
    function (p) { return /[A-Z]/.test(p); },
    function (p) { return /[a-z]/.test(p); },
    function (p) { return /\d/.test(p); },
    function (p) { return /[^A-Za-z0-9\s]/.test(p); }
  ];
  // index = number of rules passed (0-5), +1 bonus for 12+ characters when all pass
  var LEVELS = [
    { label: "Very weak", color: "#E53E3E" }, { label: "Very weak", color: "#E53E3E" },
    { label: "Weak", color: "#E53E3E" }, { label: "Fair", color: "#DD6B20" },
    { label: "Almost there", color: "#D69E2E" }, { label: "Strong", color: "#38A169" },
    { label: "Very strong", color: "#276749" }
  ];
  $all("input[data-strength]").forEach(function (input) {
    var meter = document.getElementById(input.id + "-meter");
    var label = document.getElementById(input.id + "-label");
    var items = $all("#" + input.id + "-rules li");
    input.addEventListener("input", function () {
      var p = input.value, passed = 0;
      RULES.forEach(function (rule, i) {
        var ok = !!rule(p);
        if (ok) passed++;
        if (items[i]) items[i].classList.toggle("ok", ok);
      });
      var score = passed + (passed === 5 && p.length >= 12 ? 1 : 0);
      var level = p ? LEVELS[score] : null;
      if (meter) { meter.style.width = p ? (score / 6 * 100) + "%" : "0"; meter.style.background = level ? level.color : ""; }
      if (label) { label.textContent = level ? "Strength: " + level.label : ""; label.style.color = level ? level.color : ""; }
      setError(input, p && passed < 5 ? "Your password does not meet all the requirements yet." : "");
      var confirm = document.querySelector('input[data-match="' + input.id + '"]');
      if (confirm && confirm.value) confirm.dispatchEvent(new Event("input"));
    });
  });

  /* ── Confirm password must match ─────────── */
  $all("input[data-match]").forEach(function (input) {
    var original = document.getElementById(input.getAttribute("data-match"));
    input.addEventListener("input", function () {
      setError(input, input.value && original && input.value !== original.value ? "Passwords do not match." : "");
    });
  });

  /* ── Donor-only fields (registration / profile) ── */
  $all("[data-role-choice]").forEach(function (group) {
    function apply() {
      var checked = group.querySelector("input:checked");
      var isDonor = !checked || checked.value === "donor";
      $all("[data-donor-only]").forEach(function (el) {
        el.hidden = !isDonor;
        $all("input, select", el).forEach(function (f) { f.required = isDonor; f.disabled = !isDonor; });
      });
    }
    $all("input", group).forEach(function (r) { r.addEventListener("change", apply); });
    apply();
  });

  /* ── Character counters for text areas ───── */
  $all("textarea[maxlength]").forEach(function (ta) {
    var out = document.getElementById(ta.id + "-count");
    if (!out) return;
    function upd() { out.textContent = ta.value.length + "/" + ta.getAttribute("maxlength"); }
    ta.addEventListener("input", upd); upd();
  });

  /* ════════════════════════════════════════════════════════
     PREVENT DOUBLE SUBMISSION
     After the first click the button is disabled and shows a
     spinner, so a slow connection cannot create duplicates.
     ════════════════════════════════════════════════════════ */
  $all("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) { e.preventDefault(); return; }
      if (form.method.toLowerCase() !== "post") return;
      if (form.dataset.submitting === "1") { e.preventDefault(); return; }
      form.dataset.submitting = "1";
      $all('button[type="submit"], button:not([type])', form).forEach(function (btn) {
        btn.disabled = true;
        btn.classList.add("is-loading");
        btn.dataset.label = btn.innerHTML;
        btn.innerHTML = '<span class="spinner" aria-hidden="true"></span> ' + (btn.getAttribute("data-loading") || "Please wait…");
      });
    });
  });
  // Back/forward cache: re-enable buttons when the user comes back to the page
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    $all("form").forEach(function (form) {
      form.dataset.submitting = "";
      $all("button.is-loading", form).forEach(function (btn) {
        btn.disabled = false; btn.classList.remove("is-loading");
        if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
      });
    });
  });

  /* ── Animated counters (real database numbers) ── */
  var counters = $all("[data-count-to]");
  function runCounter(el) {
    var target = parseInt(el.getAttribute("data-count-to"), 10) || 0;
    if (reduceMotion || target === 0) { el.textContent = target; return; }
    var start = null, duration = 1100;
    function step(ts) {
      if (!start) start = ts;
      var t = Math.min((ts - start) / duration, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ── Reveal on scroll ────────────────────── */
  var revealables = $all(".reveal");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("in");
        $all("[data-count-to]", entry.target).forEach(runCounter);
        if (entry.target.hasAttribute("data-count-to")) runCounter(entry.target);
        io.unobserve(entry.target);
      });
    }, { threshold: 0.15 });
    revealables.forEach(function (el) { io.observe(el); });
    counters.forEach(function (el) { if (!el.closest(".reveal")) io.observe(el); });
  } else {
    revealables.forEach(function (el) { el.classList.add("in"); });
    counters.forEach(runCounter);
  }

  /* ── "Did you know?" facts carousel ──────── */
  $all("[data-facts]").forEach(function (root) {
    var facts = $all(".fact", root);
    var dotsBox = root.querySelector(".fact-dots");
    var index = 0, timer = null;
    if (!facts.length) return;
    var dots = facts.map(function (_, i) {
      var d = document.createElement("button");
      d.type = "button"; d.className = "fact-dot"; d.setAttribute("aria-label", "Show fact " + (i + 1));
      d.addEventListener("click", function () { show(i); restart(); });
      dotsBox.appendChild(d);
      return d;
    });
    function show(i) {
      index = (i + facts.length) % facts.length;
      facts.forEach(function (f, j) { f.classList.toggle("active", j === index); f.setAttribute("aria-hidden", j === index ? "false" : "true"); });
      dots.forEach(function (d, j) { d.classList.toggle("active", j === index); });
    }
    function restart() {
      clearInterval(timer);
      if (!reduceMotion) timer = setInterval(function () { show(index + 1); }, 7000);
    }
    root.querySelector("[data-prev]").addEventListener("click", function () { show(index - 1); restart(); });
    root.querySelector("[data-next]").addEventListener("click", function () { show(index + 1); restart(); });
    root.addEventListener("mouseenter", function () { clearInterval(timer); });
    root.addEventListener("mouseleave", restart);
    root.addEventListener("focusin", function () { clearInterval(timer); });
    show(0); restart();
  });

  /* ── Blood compatibility explorer ────────── */
  $all("[data-compat]").forEach(function (root) {
    var data;
    try { data = JSON.parse(root.getAttribute("data-compat")); } catch (e) { return; }
    var giveBox = root.querySelector("[data-give]");
    var getBox = root.querySelector("[data-get]");
    var nameBox = $all("[data-selected]", root);
    var buttons = $all(".compat-picker button", root);
    function pills(list) {
      return list.map(function (g, i) { return '<span class="blood-pill" style="animation-delay:' + (i * 40) + 'ms">' + g + "</span>"; }).join("");
    }
    function select(group) {
      buttons.forEach(function (b) { b.classList.toggle("active", b.textContent === group); b.setAttribute("aria-pressed", b.textContent === group ? "true" : "false"); });
      nameBox.forEach(function (n) { n.textContent = group; });
      giveBox.innerHTML = pills(data.donate_to[group] || []);
      getBox.innerHTML = pills(data.receive_from[group] || []);
    }
    buttons.forEach(function (b) { b.addEventListener("click", function () { select(b.textContent); }); });
    select(buttons[0] ? buttons[0].textContent : "O+");
  });

  /* ── Tabs (admin dashboard) ──────────────── */
  $all("[data-tabs]").forEach(function (root) {
    var tabs = $all("[data-tab]", root);
    function activate(name) {
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-tab") === name;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
        var panel = document.getElementById("panel-" + t.getAttribute("data-tab"));
        if (panel) panel.hidden = !on;
      });
    }
    tabs.forEach(function (t) { t.addEventListener("click", function () { activate(t.getAttribute("data-tab")); }); });
    activate(root.getAttribute("data-tabs") || (tabs[0] && tabs[0].getAttribute("data-tab")));
  });

  /* ── Thank-you celebration (falling hearts & drops) ── */
  if (document.querySelector("[data-celebrate]") && !reduceMotion) {
    var layer = document.createElement("div");
    layer.className = "celebrate-layer";
    layer.setAttribute("aria-hidden", "true");
    var symbols = ["❤️", "🩸", "💖", "✨"];
    for (var i = 0; i < 28; i++) {
      var s = document.createElement("span");
      s.textContent = symbols[i % symbols.length];
      s.style.left = Math.random() * 100 + "vw";
      s.style.animationDuration = (2.6 + Math.random() * 2.2) + "s";
      s.style.animationDelay = (Math.random() * 0.9) + "s";
      s.style.fontSize = (16 + Math.random() * 16) + "px";
      layer.appendChild(s);
    }
    document.body.appendChild(layer);
    setTimeout(function () { layer.remove(); }, 6000);
  }
})();
