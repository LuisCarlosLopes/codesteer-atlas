(function () {
  const toggle = document.querySelector(".menu-toggle");
  const navigation = document.getElementById("navigation");

  function closeMenu() {
    navigation.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
  }

  toggle.addEventListener("click", () => {
    const open = navigation.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const wasOpen = toggle.getAttribute("aria-expanded") === "true";
      closeMenu();
      if (wasOpen) toggle.focus();
    }
  });

  // @MindContract: preserva links antigos para as seções da documentação técnica.
  const documentationSections = new Set([
    "mcp",
    "como-funciona",
    "indexacao",
    "codigo-ast",
    "texto-docs",
    "grafo",
    "busca-hibrida",
    "jev",
    "tools",
    "config",
  ]);
  function followDocumentationLink() {
    if (
      /(?:^|\/)index\.html$|\/$/.test(window.location.pathname) &&
      documentationSections.has(window.location.hash.slice(1))
    ) {
      window.location.replace("documentacao.html" + window.location.hash);
    }
  }
  followDocumentationLink();
  window.addEventListener("hashchange", followDocumentationLink);
})();
