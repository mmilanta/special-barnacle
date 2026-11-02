const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const script = readFileSync(join(__dirname, '../src/static/js/recipe_search.js'), 'utf8');

function preview(initialValue = '') {
  function control(value = '') {
    const listeners = {};
    return {
      value, hidden: false, textContent: '', focused: false,
      addEventListener(event, callback) { listeners[event] = callback; },
      fire(event) { listeners[event](); },
      focus() { this.focused = true; },
    };
  }
  function category(texts) {
    const rows = texts.map((text) => ({ dataset: { recipeSearch: text }, hidden: false }));
    return { rows, hidden: false, querySelectorAll() { return rows; } };
  }
  const categories = [
    category(['Torta al caffè Farina uova caffè', 'Biscotti al limone Farina burro limone']),
    category(['Pasta fresca Farina uova']),
    category([]),
  ];
  const input = control(initialValue);
  const clear = control();
  const status = control();
  const window = control();
  const controls = { 'recipe-search': input, 'clear-search': clear, 'search-status': status };
  const document = {
    getElementById(id) { return controls[id]; },
    querySelectorAll() { return categories; },
  };
  runInNewContext(script, { document, window });
  return {
    categories, input, clear, status, window,
    search(value) { input.value = value; input.fire('input'); },
    visible() { return categories.flatMap((group) => group.rows).filter((row) => !row.hidden); },
  };
}

test('empty search shows all recipes and hides empty categories', () => {
  const page = preview();
  assert.equal(page.visible().length, 3);
  assert.equal(page.categories[2].hidden, true);
  assert.equal(page.status.textContent, '');
  assert.equal(page.clear.hidden, true);
});

test('filters immediately by recipe title', () => {
  const page = preview();
  page.search('BISCOTTI');
  assert.equal(page.visible().length, 1);
  assert.equal(page.status.textContent, '');
  assert.equal(page.categories[1].hidden, true);
});

test('finds ingredients across categories', () => {
  const page = preview();
  page.search('uova');
  assert.equal(page.visible().length, 2);
  assert.equal(page.categories[0].hidden, false);
  assert.equal(page.categories[1].hidden, false);
  assert.equal(page.status.textContent, '');
});

test('ignores accents, capitalization, and extra spaces', () => {
  const page = preview();
  page.search('  CAFFE  ');
  assert.equal(page.visible().length, 1);
  assert.match(page.visible()[0].dataset.recipeSearch, /Torta/);
  page.search('CAFÉ');
  assert.equal(page.visible().length, 0); // Different spelling remains different.
});

test('all words must match, even across title and ingredients', () => {
  const page = preview();
  page.search('  uova   fresca  ');
  assert.equal(page.visible().length, 1);
  assert.match(page.visible()[0].dataset.recipeSearch, /Pasta/);
});

test('no matches shows a helpful message and hides every category', () => {
  const page = preview();
  page.search('inesistente');
  assert.equal(page.visible().length, 0);
  assert.ok(page.categories.every((group) => group.hidden));
  assert.equal(page.status.textContent, 'Nessuna ricetta trovata');
  assert.equal(page.status.hidden, false);
});

test('clear restores the list and returns focus to the search box', () => {
  const page = preview();
  page.search('torta');
  page.clear.fire('click');
  assert.equal(page.input.value, '');
  assert.equal(page.input.focused, true);
  assert.equal(page.clear.hidden, true);
  assert.equal(page.visible().length, 3);
  assert.equal(page.status.textContent, '');
  assert.equal(page.status.hidden, true);
});

test('restored browser search values are applied on pageshow', () => {
  const page = preview();
  page.input.value = 'limone';
  page.window.fire('pageshow');
  assert.equal(page.visible().length, 1);
  assert.match(page.visible()[0].dataset.recipeSearch, /Biscotti/);
});
