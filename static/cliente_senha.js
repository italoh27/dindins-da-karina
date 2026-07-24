document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-toggle-password]");
  if (!button) return;

  const wrapper = button.closest(".senha-input-wrap");
  const input = wrapper?.querySelector("input");
  if (!input) return;

  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  button.textContent = showing ? "Mostrar" : "Ocultar";
  button.setAttribute("aria-pressed", String(!showing));
  button.setAttribute("aria-label", showing ? "Mostrar senha" : "Ocultar senha");
});
