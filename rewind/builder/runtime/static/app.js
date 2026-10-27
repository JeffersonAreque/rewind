// Row selection, message dialogs and toasts. The pages work without it, except row selection.
document.addEventListener("DOMContentLoaded", () => {
  // Selectable tables: a click (or Enter / Space) picks the row and enables the buttons that need one.
  document.querySelectorAll("table.selectable").forEach((table) => {
    const select = (row) => {
      table.querySelectorAll("tbody tr").forEach((r) => r.setAttribute("aria-selected", String(r === row)));
      document.querySelectorAll("[data-selection]").forEach((input) => { input.value = row.dataset.id; });
      document.querySelectorAll("[data-requires-selection]").forEach((button) => { button.disabled = false; });
    };
    table.querySelectorAll("tbody tr[data-id]").forEach((row) => {
      row.addEventListener("click", () => select(row));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(row);
        }
      });
    });
  });

  // Dialogs close with their button or Esc and give focus back to the field in error.
  document.querySelectorAll("dialog.dialog").forEach((dialog) => {
    const close = () => {
      dialog.remove();
      const button = dialog.querySelector("[data-focus]");
      if (button) document.getElementById(button.dataset.focus)?.focus();
    };
    dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
    dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  });

  // Toasts leave on their own.
  document.querySelectorAll(".toast").forEach((toast) => setTimeout(() => toast.classList.add("hide"), 6000));
});
