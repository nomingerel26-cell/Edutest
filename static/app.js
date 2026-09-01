/* =====================================================================
   EduTest — интерфэйсийн скрипт.
   Гадаад сан ашиглаагүй. JavaScript унтарсан үед ч бүх үндсэн урсгал
   (форм илгээх, тест бөглөх) сервер талдаа ажиллана — энэ файл нь
   зөвхөн тав тухыг сайжруулна.
   ===================================================================== */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  /* ---------------------------------------------------------------
     1. Хажуугийн цэс — гар утасны drawer
     --------------------------------------------------------------- */
  function initDrawer() {
    var toggle = $("[data-nav-toggle]");
    var backdrop = $(".sidebar-backdrop");
    if (!toggle) return;

    function close() {
      document.body.classList.remove("nav-open");
      toggle.setAttribute("aria-expanded", "false");
    }
    toggle.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    if (backdrop) backdrop.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
    $$(".sidebar .nav-link").forEach(function (a) {
      a.addEventListener("click", close);
    });
  }

  /* ---------------------------------------------------------------
     2. Toast — автоматаар арилна
     --------------------------------------------------------------- */
  function dismiss(el) {
    el.classList.add("hide");
    setTimeout(function () { el.remove(); }, 260);
  }

  function initToasts() {
    $$(".toast").forEach(function (t) {
      var btn = $(".close", t);
      if (btn) btn.addEventListener("click", function () { dismiss(t); });
      if (!t.hasAttribute("data-sticky")) {
        setTimeout(function () { dismiss(t); }, 6000);
      }
    });
  }

  // Динамик toast (жишээ нь холбоос хуулсны дараа)
  window.eduToast = function (message, kind) {
    var stack = $(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "success");
    el.setAttribute("role", "status");
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(function () { dismiss(el); }, 3200);
  };

  /* ---------------------------------------------------------------
     3. Нууц үг харуулах / нуух
     --------------------------------------------------------------- */
  function initPasswordToggles() {
    $$("[data-toggle-password]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = document.getElementById(btn.getAttribute("data-toggle-password"));
        if (!input) return;
        var shown = input.type === "text";
        input.type = shown ? "password" : "text";
        btn.setAttribute("aria-label", shown ? "Нууц үгийг харуулах" : "Нууц үгийг нуух");
        $$("svg", btn).forEach(function (svg, i) {
          svg.style.display = (shown ? i === 0 : i === 1) ? "" : "none";
        });
      });
    });
  }

  /* ---------------------------------------------------------------
     4. Холбоос хуулах
     --------------------------------------------------------------- */
  function initCopy() {
    $$("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var value = btn.getAttribute("data-copy");
        var target = btn.getAttribute("data-copy-from");
        if (target) {
          var el = document.getElementById(target);
          if (el) value = el.value || el.textContent;
        }
        function done() { window.eduToast("Холбоос хуулагдлаа.", "success"); }
        function fail() { window.eduToast("Хуулж чадсангүй. Гараар сонгож хуулна уу.", "error"); }

        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(value).then(done, fail);
        } else {
          // HTTP дээр (танхимын дотоод сүлжээ) clipboard API ажиллахгүй тул нөөц арга
          var ta = document.createElement("textarea");
          ta.value = value;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy") ? done() : fail(); } catch (e) { fail(); }
          ta.remove();
        }
      });
    });
  }

  /* ---------------------------------------------------------------
     5. Баталгаажуулах цонх (устгах гэх мэт)
     Форм дээр data-confirm бичвэл <dialog> нээгдэнэ.
     JS байхгүй үед форм энгийнээр илгээгдэнэ.
     --------------------------------------------------------------- */
  function initConfirm() {
    var dlg = $("#confirm-modal");
    if (!dlg || typeof dlg.showModal !== "function") return;

    var titleEl = $("[data-confirm-title]", dlg);
    var bodyEl = $("[data-confirm-body]", dlg);
    var okBtn = $("[data-confirm-ok]", dlg);
    var pending = null;

    $$("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (form.dataset.confirmed === "yes") return;
        e.preventDefault();
        pending = form;
        titleEl.textContent = form.getAttribute("data-confirm-title") || "Баталгаажуулах";
        bodyEl.textContent = form.getAttribute("data-confirm");
        okBtn.textContent = form.getAttribute("data-confirm-ok-label") || "Устгах";
        dlg.showModal();
      });
    });

    okBtn.addEventListener("click", function () {
      dlg.close();
      if (pending) {
        pending.dataset.confirmed = "yes";
        pending.submit();
        pending = null;
      }
    });
    $$("[data-confirm-cancel]", dlg).forEach(function (b) {
      b.addEventListener("click", function () { dlg.close(); pending = null; });
    });
  }

  /* ---------------------------------------------------------------
     6. Тестийн жагсаалт — хайлт ба шүүлтүүр (клиент талд)
     --------------------------------------------------------------- */
  function initTableFilter() {
    var input = $("[data-filter-input]");
    var rows = $$("[data-filter-row]");
    if (!rows.length) return;
    var chips = $$("[data-filter-chip]");
    var emptyMsg = $("[data-filter-empty]");
    var active = { status: "all", kind: "all" };

    function apply() {
      var term = (input ? input.value : "").trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (row) {
        var hay = (row.getAttribute("data-search") || "").toLowerCase();
        var okTerm = !term || hay.indexOf(term) !== -1;
        var okStatus = active.status === "all" || row.getAttribute("data-status") === active.status;
        var okKind = active.kind === "all" || row.getAttribute("data-kind") === active.kind;
        var ok = okTerm && okStatus && okKind;
        row.style.display = ok ? "" : "none";
        if (ok) shown++;
      });
      if (emptyMsg) emptyMsg.style.display = shown ? "none" : "";
    }

    if (input) input.addEventListener("input", apply);
    chips.forEach(function (chip) {
      chip.addEventListener("click", function (e) {
        e.preventDefault();
        var group = chip.getAttribute("data-filter-group");
        active[group] = chip.getAttribute("data-filter-chip");
        chips.filter(function (c) { return c.getAttribute("data-filter-group") === group; })
             .forEach(function (c) { c.classList.remove("active"); });
        chip.classList.add("active");
        apply();
      });
    });
    apply();
  }

  /* ---------------------------------------------------------------
     7. Оюутны тест — сонголтыг ялгах, явцын мөр, илгээхийн өмнөх сануулга
     --------------------------------------------------------------- */
  function initTakeTest() {
    var form = $("[data-take-form]");
    if (!form) return;

    var total = parseInt(form.getAttribute("data-total") || "0", 10);
    var bar = $("[data-progress-bar]");
    var countEl = $("[data-progress-count]");

    function refresh() {
      var answered = 0;
      $$(".q-card", form).forEach(function (card) {
        var type = card.getAttribute("data-qtype") || "single";
        var done = false;

        if (type === "multi") {
          var boxes = $$("input[type=checkbox]", card);
          boxes.forEach(function (b) {
            b.closest(".opt").classList.toggle("selected", b.checked);
          });
          done = boxes.some(function (b) { return b.checked; });
        } else if (type === "match") {
          var selects = $$("select", card);
          done = selects.length > 0 && selects.every(function (s) { return s.value !== ""; });
        } else {
          var checked = $("input[type=radio]:checked", card);
          $$(".opt", card).forEach(function (o) { o.classList.remove("selected"); });
          if (checked) checked.closest(".opt").classList.add("selected");
          done = !!checked;
        }
        if (done) answered++;
      });
      if (bar) bar.style.width = total ? (answered * 100 / total) + "%" : "0%";
      if (countEl) countEl.textContent = answered + " / " + total;
      return answered;
    }

    form.addEventListener("change", refresh);
    refresh();

    var dlg = $("#submit-modal");
    if (dlg && typeof dlg.showModal === "function") {
      var confirmed = false;
      form.addEventListener("submit", function (e) {
        if (confirmed) return;
        e.preventDefault();
        var answered = refresh();
        var warn = $("[data-unanswered]", dlg);
        if (warn) {
          if (answered < total) {
            warn.textContent = "Хариулаагүй " + (total - answered) + " асуулт байна.";
            warn.style.display = "";
          } else {
            warn.style.display = "none";
          }
        }
        dlg.showModal();
      });
      $("[data-submit-ok]", dlg).addEventListener("click", function () {
        confirmed = true;
        dlg.close();
        form.submit();
      });
      $$("[data-submit-cancel]", dlg).forEach(function (b) {
        b.addEventListener("click", function () { dlg.close(); });
      });
    }
  }

  /* ---------------------------------------------------------------
     8. Илгээх товч дээр давхар дарахаас сэргийлэх
     --------------------------------------------------------------- */
  function initSubmitGuard() {
    $$("form[data-guard]").forEach(function (form) {
      form.addEventListener("submit", function () {
        var btn = $("button[type=submit]", form);
        if (btn) {
          btn.disabled = true;
          btn.textContent = btn.getAttribute("data-busy-label") || "Түр хүлээнэ үү…";
        }
      });
    });
  }

  /* ---------------------------------------------------------------
     9. Асуултын төрөл сонгоход холбогдох талбаруудыг харуулах
     JS унтарсан үед бүх бүлэг харагдаж, сервер тал зөв боловсруулна.
     --------------------------------------------------------------- */
  function initQuestionType() {
    var chips = $$("[data-qtype-chip]");
    var panels = $$("[data-qtype-panel]");
    if (!chips.length || !panels.length) return;

    var titleEl = $("[data-options-title]");
    var hintEl = $("[data-options-hint]");
    var COPY = {
      single: ["Хариултын сонголтууд", "Дөрвөн сонголтоо бичээд доор зөв хариултаа заана."],
      multi:  ["Хариултын сонголтууд", "Дөрвөн сонголтоо бичээд доор зөв хариултуудаа тэмдэглэнэ."],
      match:  ["Зүүн талын зүйлс", "Харгалзуулах зүйлсээ бичнэ. Баруун талын хосыг доор оруулна."]
    };

    function apply(type) {
      panels.forEach(function (p) {
        p.style.display = p.getAttribute("data-qtype-panel") === type ? "" : "none";
      });
      chips.forEach(function (c) {
        c.classList.toggle("active", c.getAttribute("data-qtype-chip") === type);
      });
      // Далдалсан талбарын required-ийг салгана, эс бөгөөс форм илгээгдэхгүй.
      panels.forEach(function (p) {
        var hidden = p.style.display === "none";
        $$("input, select, textarea", p).forEach(function (el) {
          el.disabled = hidden;
        });
      });
      // Харгалзуулах хос нь зөвхөн match үед заавал.
      $$('[name^="match_"]').forEach(function (el) {
        el.required = type === "match";
      });
      if (titleEl && COPY[type]) titleEl.textContent = COPY[type][0];
      if (hintEl && COPY[type]) hintEl.textContent = COPY[type][1];
    }

    chips.forEach(function (chip) {
      var input = $("input[type=radio]", chip);
      chip.addEventListener("click", function () {
        if (input) input.checked = true;
        apply(chip.getAttribute("data-qtype-chip"));
      });
      if (input) input.addEventListener("change", function () {
        apply(chip.getAttribute("data-qtype-chip"));
      });
    });

    var checked = $("[data-qtype-chip] input:checked");
    apply(checked ? checked.value : "single");
  }

  /* Олон сонголттой асуултын checkbox-ыг тодруулах */
  function initMultiHighlight() {
    $$('.opt input[type=checkbox]').forEach(function (box) {
      box.addEventListener("change", function () {
        box.closest(".opt").classList.toggle("selected", box.checked);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initQuestionType();
    initMultiHighlight();
    initDrawer();
    initToasts();
    initPasswordToggles();
    initCopy();
    initConfirm();
    initTableFilter();
    initTakeTest();
    initSubmitGuard();
  });
})();
