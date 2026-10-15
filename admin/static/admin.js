(function () {
  var toggle = document.querySelector("[data-nav-toggle]");
  var nav = document.getElementById("app-nav");
  var backdrop = document.querySelector("[data-nav-close]");

  function setNavOpen(open) {
    if (!nav || !toggle) {
      return;
    }
    nav.classList.toggle("is-open", open);
    if (backdrop) {
      backdrop.classList.toggle("is-open", open);
    }
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      setNavOpen(!nav.classList.contains("is-open"));
    });
    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (window.matchMedia("(max-width: 900px)").matches) {
          setNavOpen(false);
        }
      });
    });
  }
  if (backdrop) {
    backdrop.addEventListener("click", function () {
      setNavOpen(false);
    });
  }

  var search = document.getElementById("feature-search");
  if (search) {
    var groups = document.querySelectorAll("[data-feature-group]");
    var empty = document.getElementById("feature-search-empty");
    search.addEventListener("input", function () {
      var query = search.value.trim().toLowerCase();
      var visible = 0;
      groups.forEach(function (group) {
        var count = 0;
        group.querySelectorAll("[data-feature-search]").forEach(function (item) {
          var haystack = item.getAttribute("data-feature-search") || "";
          var matched = !query || haystack.toLowerCase().indexOf(query) !== -1;
          item.hidden = !matched;
          if (matched) {
            count += 1;
          }
        });
        group.hidden = count === 0;
        visible += count;
      });
      if (empty) {
        empty.hidden = visible !== 0;
      }
    });
  }

  document.querySelectorAll("details.form-section").forEach(function (details) {
    if (!details.open && sectionHasContent(details)) {
      details.open = true;
    }
  });

  var scheduleType = document.querySelector('select[name="schedule_type"]');
  if (scheduleType) {
    var refreshSchedule = function () {
      var type = scheduleType.value;
      document.querySelectorAll("[data-schedule-for]").forEach(function (node) {
        var allowed = node.getAttribute("data-schedule-for").split(/\s+/);
        node.hidden = allowed.indexOf(type) === -1;
      });
    };
    scheduleType.addEventListener("change", refreshSchedule);
    refreshSchedule();
  }

  function sectionHasContent(root) {
    var fields = root.querySelectorAll("input, textarea, select");
    for (var i = 0; i < fields.length; i += 1) {
      var field = fields[i];
      if (field.disabled || field.type === "hidden" || field.type === "file" || field.type === "number" || field.type === "time") {
        continue;
      }
      if (field.type === "checkbox" || field.type === "radio") {
        if (field.name !== "enabled" && field.checked) {
          return true;
        }
        continue;
      }
      var value = String(field.value || "").trim();
      if (value && value !== "none" && value !== "0") {
        return true;
      }
    }
    return false;
  }
})();
