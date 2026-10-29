// Message box, F-keys, row selection and the hourglass of the old program.

(function () {
  var dialog = document.getElementById("dialog");

  function closeDialog() {
    var focusName = dialog.dataset.focus;
    dialog.remove();
    dialog = null;
    var field = focusName && document.querySelector('[name="' + focusName + '"]');
    if (field) field.focus();
    else if (document.querySelector("[autofocus]")) document.querySelector("[autofocus]").focus();
  }

  if (dialog) {
    dialog.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", closeDialog); });
    dialog.querySelector(".actions button").focus();
  }

  document.addEventListener("keydown", function (event) {
    if (dialog) {
      if (event.key === "Enter" || event.key === "Escape") { event.preventDefault(); closeDialog(); }
      return;
    }
    var key = document.querySelector('[data-key="' + event.key + '"]');
    if (key) { event.preventDefault(); key.click(); }
    if (event.key === "Escape" && document.querySelector("[data-cancel]")) document.querySelector("[data-cancel]").click();
  });

  // A click picks a slot and enables RESERVAR; a double click opens it.
  document.querySelectorAll("table.interactive").forEach(function (table) {
    var input = document.getElementById(table.dataset.selectInput);
    var button = document.getElementById(table.dataset.selectButton);
    table.querySelectorAll("tbody tr[data-id]").forEach(function (row) {
      row.addEventListener("click", function () {
        table.querySelectorAll("tr.highlighted").forEach(function (r) { r.classList.remove("highlighted"); });
        row.classList.add("highlighted");
        input.value = row.dataset.id;
        button.disabled = false;
      });
      row.addEventListener("dblclick", function () { button.click(); });
    });
  });

  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () { document.body.classList.add("busy"); });
  });
})();
