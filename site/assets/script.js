(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- scroll reveals ---------- */
  function initReveals() {
    var items = document.querySelectorAll(".reveal");
    if (reduceMotion || !("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("in-view"); });
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var el = entry.target;
            var delay = parseInt(el.getAttribute("data-delay") || "0", 10);
            setTimeout(function () { el.classList.add("in-view"); }, delay);
            io.unobserve(el);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -60px 0px" }
    );
    items.forEach(function (el) { io.observe(el); });
  }

  function staggerReveals(container, stepMs) {
    var kids = container.querySelectorAll(".reveal");
    kids.forEach(function (el, i) { el.setAttribute("data-delay", String(i * stepMs)); });
  }

  /* ---------- demo tour ---------- */
  function initTour() {
    var tour = document.querySelector("[data-tour]");
    if (!tour) return;
    var buttons = tour.querySelectorAll(".tour-step-btn");
    var slides = tour.querySelectorAll(".tour-slide");
    function activate(target) {
      buttons.forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-step") === target); });
      slides.forEach(function (s) { s.classList.toggle("active", s.getAttribute("data-step") === target); });
    }
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () { activate(btn.getAttribute("data-step")); });
    });
    // deep link: #tour-step-3
    var m = /tour-step-(\d)/.exec(location.hash);
    if (m) activate(m[1]);
  }

  /* ---------- terminal replay ---------- */
  function initTerminal() {
    var term = document.querySelector("[data-terminal]");
    if (!term) return;
    var lines = JSON.parse(term.getAttribute("data-lines"));
    term.innerHTML = "";

    if (reduceMotion) {
      lines.forEach(function (l) { appendLine(term, l, true); });
      return;
    }

    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            io.disconnect();
            playLines(term, lines, 0);
          }
        });
      },
      { threshold: 0.3 }
    );
    io.observe(term);
  }

  function appendLine(term, l, instant) {
    var div = document.createElement("div");
    div.className = "line " + (l.type === "prompt" ? "prompt" : "out");
    div.textContent = (l.type === "prompt" ? "$ " : "") + l.text;
    term.appendChild(div);
  }

  function playLines(term, lines, idx) {
    if (idx >= lines.length) {
      var cursor = document.createElement("span");
      cursor.className = "cursor";
      term.appendChild(cursor);
      return;
    }
    var l = lines[idx];
    var div = document.createElement("div");
    div.className = "line " + (l.type === "prompt" ? "prompt" : "out");
    term.appendChild(div);

    if (l.type === "prompt") {
      typeText(div, "$ " + l.text, function () {
        setTimeout(function () { playLines(term, lines, idx + 1); }, 220);
      });
    } else {
      div.textContent = l.text;
      setTimeout(function () { playLines(term, lines, idx + 1); }, 90);
    }
  }

  function typeText(el, text, done) {
    var i = 0;
    var speed = 14;
    (function step() {
      el.textContent = text.slice(0, i);
      i++;
      if (i <= text.length) {
        setTimeout(step, speed);
      } else {
        done();
      }
    })();
  }

  /* ---------- init ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-stagger]").forEach(function (c) {
      staggerReveals(c, parseInt(c.getAttribute("data-stagger"), 10) || 90);
    });
    initReveals();
    initTour();
    initTerminal();
  });
})();
