(() => {
  const input = document.getElementById("recipe-search");
  const clear = document.getElementById("clear-search");
  const status = document.getElementById("search-status");
  const normalize = (text) => text.normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("it");
  const categories = Array.from(document.querySelectorAll("[data-recipe-category]"), (element) => ({
    element,
    recipes: Array.from(element.querySelectorAll("[data-recipe-search]"), (row) => ({
      row,
      text: normalize(row.dataset.recipeSearch),
    })),
  }));

  function filterRecipes() {
    const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    let total = 0;
    for (const category of categories) {
      let count = 0;
      for (const recipe of category.recipes) {
        const matches = terms.every((term) => recipe.text.includes(term));
        recipe.row.hidden = !matches;
        if (matches) count++;
      }
      category.element.hidden = count === 0;
      total += count;
    }
    clear.hidden = input.value.length === 0;
    const noResults = terms.length > 0 && total === 0;
    status.hidden = !noResults;
    status.textContent = noResults
      ? "Nessuna ricetta trovata"
      : "";
  }

  input.addEventListener("input", filterRecipes);
  // Also update when navigating back to a page whose input value was restored.
  window.addEventListener("pageshow", filterRecipes);
  clear.addEventListener("click", () => {
    input.value = "";
    filterRecipes();
    input.focus();
  });
  filterRecipes();
})();
