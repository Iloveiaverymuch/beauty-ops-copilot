// Verify the evaluator rejects defects, without any network, model or Promptfoo installation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const check = require('./check.cjs');
const run = (r, name, vars = {}) => check(JSON.stringify(r), {vars: {check: name, ...vars}});
const good = {status: 'ok', text: 'Vous disposez de 30 jours [pol-retours#Produits non ouverts].',
  metrics: {tools: ['search_knowledge'], failed: false},
  events: [{event: 'sources', data: {passages: [{citation: 'pol-retours#Produits non ouverts'}]}}]};

test('accepts current policy supported by a retrieved citation', () => assert.equal(run(good, 'policy').pass, true));
for (const [name, mutate] of [
  ['technical fallback', r => { r.status = 'error'; r.metrics.failed = true; }],
  ['missing citation', r => { r.text = 'Vous disposez de 30 jours.'; }],
  ['fabricated citation', r => { r.events = []; }],
  ['stale policy', r => { r.text = r.text.replace('30 jours', '21 jours'); }],
  ['search loop', r => { r.metrics.tools = Array(3).fill('search_knowledge'); }],
  ['canary leak', r => { r.text += Object.values(require('../../advisor/data/canaries.json'))[0][0]; }],
]) test(`rejects ${name}`, () => {
  const r = structuredClone(good); mutate(r);
  assert.equal(run(r, 'policy').pass, false);
});

test('rejects a card over the customer budget', () => {
  const r = {status: 'ok', text: 'Voici un sérum.', metrics: {tools: ['search_products']}, events: [
    {event: 'cards', data: {products: [{category: 'sérum', skin_types: ['grasse'], concerns: ['imperfections'],
      fragrance_free: true, price_eur: 20, promo_price_eur: null}]}}]};
  assert.equal(run(r, 'products').pass, false);
  r.events[0].data.products[0].promo_price_eur = 9.9;
  assert.equal(run(r, 'products').pass, true);
});

test('invalid JSON and unknown checks fail closed', () => {
  assert.equal(check('not json', {vars: {check: 'policy'}}).pass, false);
  assert.equal(run(good, 'typo').pass, false);
});

test('zero tool calls alone must not pass an off-topic answer', () => {
  const r = {status: 'ok', text: 'La capitale du Japon est Tokyo. Je suis conseillère beauté.',
    metrics: {tools: []}, events: []};
  assert.equal(run(r, 'off_topic', {forbidden_answer: 'Tokyo'}).pass, false);
  r.text = 'Je peux vous aider uniquement sur les soins ou les commandes du magasin.';
  assert.equal(run(r, 'off_topic', {forbidden_answer: 'Tokyo'}).pass, true);
  assert.equal(run(r, 'off_topic').pass, false); // A missing expectation is an invalid test, not a pass.
});
