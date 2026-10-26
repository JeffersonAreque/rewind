// Small behaviors of the old desktop: modal message boxes, hourglass, Esc key, tray clock.

(function () {
  var dialog = document.getElementById("dialog");

  function closeDialog() {
    var focusName = dialog.dataset.focus;
    dialog.remove();
    dialog = null;
    document.querySelectorAll(".title-bar.inactive").forEach(function (bar) {
      bar.classList.remove("inactive");
    });
    var field = focusName && document.querySelector('[name="' + focusName + '"]');
    if (field) {
      field.focus();
      if (field.select) field.select();
    } else {
      var first = document.querySelector("[autofocus]");
      if (first) first.focus();
    }
  }

  if (dialog) {
    document.querySelectorAll(".window:not(.dialog) .title-bar").forEach(function (bar) {
      bar.classList.add("inactive");
    });
    dialog.querySelectorAll("[data-close]").forEach(function (button) {
      button.addEventListener("click", closeDialog);
    });
    dialog.querySelector(".actions button").focus();
  }

  document.addEventListener("keydown", function (event) {
    if (dialog) {
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault();
        closeDialog();
      }
      return;
    }
    if (event.key === "Escape") {
      var cancel = document.querySelector("[data-cancel]");
      if (cancel) cancel.click();
    }
  });

  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      document.body.classList.add("busy");
    });
  });

  var clock = document.getElementById("clock");
  function tick() {
    var now = new Date();
    clock.textContent =
      String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }
  if (clock) {
    tick();
    setInterval(tick, 10000);
  }
})();
