document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    const label = button.textContent;
    button.textContent = "已复制";
    setTimeout(() => { button.textContent = label; }, 1400);
  } catch {
    let field = button.parentElement.querySelector("input");
    if (!field) {
      field = document.createElement("input");
      field.className = "command";
      field.readOnly = true;
      field.value = button.dataset.copy;
      field.setAttribute("aria-label", "QQ 指令");
      button.before(field);
    }
    field.focus();
    field.select();
  }
});
