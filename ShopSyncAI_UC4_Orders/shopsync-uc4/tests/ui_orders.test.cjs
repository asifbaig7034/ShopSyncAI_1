const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const attemptKey = 'shopsync_pending_draft';
const originalData = {
  phone: '923001234567', name: 'Demo customer', delivery_address: '12 Main Street',
  payment_method: 'COD', product_id: 'p1', quantity: '1',
};
const products = [
  {id: 'p1', sku: 'TEE-01', name: 'Shirt', price_cents: 150000, stock_qty: 10, active: true},
  {id: 'p2', sku: 'CUP-01', name: 'Cup', price_cents: 80000, stock_qty: 10, active: true},
  {id: 'p3', sku: 'BAG-01', name: 'Bag', price_cents: 200000, stock_qty: 10, active: true},
];

function response(body, status = 200){
  return {ok: status >= 200 && status < 300, json: async () => body};
}

function deferred(){
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function browser({storage = new Map(), onDraft, onPatch, catalog = products} = {}){
  const nodes = new Map();
  const listeners = {};
  const requests = [];
  const prompts = [];
  const answers = [];
  let nextId = 0;

  function node(){
    return {
      value: '', textContent: '', innerHTML: '', disabled: false,
      classList: {toggle(){}}, addEventListener(){}, querySelector(){return null;},
    };
  }

  // Replacing real select options resets its value; exercise the restoration too.
  const productSelect = {
    options: [''], selected: '', markup: '',
    get value(){return this.selected;},
    set value(value){this.selected = this.options.includes(String(value)) ? String(value) : '';},
    get innerHTML(){return this.markup;},
    set innerHTML(value){
      this.markup = value;
      this.options = [...value.matchAll(/value="([^"]*)"/g)].map(match => match[1]);
      this.selected = this.options[0] || '';
    },
  };
  nodes.set('product-select', productSelect);

  function form(defaults = {}){
    const fields = Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, {...node(), value}]));
    const button = node();
    const events = {};
    return {
      fields, button, events,
      elements: {namedItem(name){return fields[name];}},
      querySelector(selector){return selector === 'button[type="submit"]' ? button : null;},
      addEventListener(name, handler){events[name] = handler;},
      reset(){for(const [name, field] of Object.entries(fields)) field.value = defaults[name];},
    };
  }

  const draft = form({phone: '', name: '', delivery_address: '', payment_method: 'COD', quantity: '1'});
  draft.fields.product_id = productSelect;
  const resetFields = draft.reset.bind(draft);
  draft.reset = () => {resetFields(); productSelect.value = '';};
  nodes.set('draft-form', draft);
  nodes.set('product-form', form());
  nodes.set('chat-form', form());

  const context = {
    document: {
      getElementById(id){if(!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
      querySelectorAll(){return [];},
      addEventListener(name, handler){listeners[name] = handler;},
    },
    sessionStorage: {
      getItem(key){return storage.get(key) ?? null;},
      setItem(key, value){storage.set(key, String(value));},
      removeItem(key){storage.delete(key);},
    },
    FormData: class {constructor(target){this.form = target;} get(name){return this.form.fields[name]?.value ?? null;}},
    crypto: {randomUUID(){return `request-${++nextId}`;}},
    prompt(message){prompts.push(message); return answers.shift() ?? null;},
    confirm(){return true;}, setTimeout(){}, clearTimeout(){},
    fetch: async (url, options = {}) => {
      const request = {url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null};
      requests.push(request);
      if(url === '/api/orders/drafts') return onDraft ? onDraft(request) : response({id: 'created-order'});
      if(request.method === 'PATCH') return onPatch ? onPatch(request) : response({id: 'edited-order'});
      if(url === '/api/products') return response(catalog);
      if(url === '/api/finance') return response({booked_net_cents: 0});
      return response([]);
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  // A connected catalog is available without storing credentials in this fixture.
  vm.runInContext(`state.products = ${JSON.stringify(catalog)}; render();`, context);

  return {
    context, storage, requests, draft, prompts, answers, nodes,
    setFields(data, fire = false){
      for(const [key, value] of Object.entries(data)) draft.fields[key].value = String(value);
      if(fire) draft.events.input();
    },
    submit(){return draft.onsubmit({target: draft, preventDefault(){}});},
    async edit(order){
      vm.runInContext(`state.orders = [${JSON.stringify(order)}];`, context);
      const button = {dataset: {action: 'edit', id: order.id}, disabled: false};
      await listeners.click({target: {closest(){return button;}}});
      return button;
    },
  };
}

function multiItemOrder(){
  return {
    id: 'o1', delivery_address: 'Original address', payment_method: 'COD',
    items: [
      {product_id: 'p1', sku: 'TEE-01', quantity: 1},
      {product_id: 'p2', sku: 'CUP-01', quantity: 2},
    ],
  };
}

test('editing the first item preserves every other product and quantity', async () => {
  const page = browser();
  page.answers.push('BAG-01', '3', 'Changed address', 'BANK_TRANSFER');
  const button = await page.edit(multiItemOrder());
  const request = page.requests.find(item => item.method === 'PATCH');
  assert.deepEqual(request.body, {
    items: [{product_id: 'p3', quantity: 3}, {product_id: 'p2', quantity: 2}],
    delivery_address: 'Changed address', payment_method: 'BANK_TRANSFER',
  });
  assert.match(page.prompts[0], /first item.*other items are kept/i);
  assert.equal(button.disabled, false);
});

test('selecting a product already on another line rejects the edit without sending a request', async () => {
  const page = browser();
  const order = multiItemOrder();
  page.answers.push('CUP-01');
  const button = await page.edit(order);
  assert.equal(page.requests.filter(item => item.method === 'PATCH').length, 0);
  assert.match(page.nodes.get('notice').textContent, /already another item.*all items were kept/i);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(state.orders[0].items)', page.context)), order.items);
  assert.equal(button.disabled, false);
});

test('a saved draft with a lost response retries the same complete request and creates only one order', async () => {
  const saved = new Map();
  let loseResponse = true;
  const page = browser({onDraft(request){
    if(!saved.has(request.body.source_key)) saved.set(request.body.source_key, {id: 'server-saved-order'});
    if(loseResponse){loseResponse = false; throw new TypeError('Connection closed after save');}
    return response(saved.get(request.body.source_key));
  }});
  page.setFields(originalData);
  await page.submit();
  assert.equal(page.draft.button.disabled, false);
  assert.equal(page.draft.button.textContent, 'Retry draft');
  assert.ok(page.storage.has(attemptKey));
  await page.submit();
  const attempts = page.requests.filter(item => item.url === '/api/orders/drafts');
  assert.deepEqual(attempts[1].body, attempts[0].body);
  assert.equal(saved.size, 1);
  assert.equal(page.storage.has(attemptKey), false);
  assert.equal(page.draft.button.disabled, false);
});

test('a second submit while the first request is pending sends no additional request', async () => {
  const waiting = deferred();
  const page = browser({onDraft(){return waiting.promise;}});
  page.setFields(originalData);
  const first = page.submit();
  assert.equal(page.draft.button.disabled, true);
  await page.submit();
  assert.equal(page.requests.filter(item => item.url === '/api/orders/drafts').length, 1);
  waiting.resolve(response({id: 'o1'}));
  await first;
  assert.equal(page.draft.button.disabled, false);
});

test('an unreadable success response retains the pending request for an exact retry', async () => {
  let first = true;
  const page = browser({onDraft(){
    if(first){
      first = false;
      return {ok: true, async json(){throw new SyntaxError('Truncated JSON response');}};
    }
    return response({id: 'already-saved-order'});
  }});
  page.setFields(originalData);
  await page.submit();
  assert.ok(page.storage.has(attemptKey));
  assert.equal(page.draft.button.disabled, false);
  await page.submit();
  const attempts = page.requests.filter(item => item.url === '/api/orders/drafts');
  assert.deepEqual(attempts[1].body, attempts[0].body);
  assert.equal(page.storage.has(attemptKey), false);
});

test('a success response missing its order ID cannot clear the pending request', async () => {
  const page = browser({onDraft(){return response({});}});
  page.setFields(originalData);
  await page.submit();
  assert.ok(page.storage.has(attemptKey));
  assert.equal(page.draft.fields.phone.value, originalData.phone);
  assert.equal(page.draft.button.disabled, false);
  assert.match(page.nodes.get('notice').textContent, /response was incomplete/i);
});

test('explicitly changed order data clears the pending attempt and receives a new key', async () => {
  const page = browser({onDraft(){throw new TypeError('Network unavailable');}});
  page.setFields(originalData);
  await page.submit();
  const first = page.requests[0].body;
  page.setFields({delivery_address: 'A deliberately changed address'}, true);
  assert.equal(page.storage.has(attemptKey), false);
  await page.submit();
  const second = page.requests.filter(item => item.url === '/api/orders/drafts')[1].body;
  assert.notEqual(second.source_key, first.source_key);
  assert.equal(second.delivery_address, 'A deliberately changed address');
});

test('a successful submission clears the attempt and a subsequent identical order gets a new key', async () => {
  const page = browser();
  page.setFields(originalData);
  await page.submit();
  assert.equal(page.storage.has(attemptKey), false);
  assert.equal(page.draft.fields.phone.value, '');
  assert.equal(page.draft.button.textContent, 'Create draft');
  page.setFields(originalData);
  await page.submit();
  const attempts = page.requests.filter(item => item.url === '/api/orders/drafts');
  assert.notEqual(attempts[1].body.source_key, attempts[0].body.source_key);
});

test('reloading restores the exact pending order and retry key', async () => {
  const storage = new Map();
  const first = browser({storage, onDraft(){throw new TypeError('Response lost');}});
  first.setFields(originalData);
  await first.submit();
  const original = first.requests.find(item => item.url === '/api/orders/drafts').body;
  const reloaded = browser({storage});
  assert.equal(reloaded.draft.fields.phone.value, originalData.phone);
  assert.equal(reloaded.draft.fields.product_id.value, originalData.product_id);
  assert.equal(reloaded.draft.fields.delivery_address.value, originalData.delivery_address);
  assert.equal(reloaded.draft.button.textContent, 'Retry draft');
  await reloaded.submit();
  const retry = reloaded.requests.find(item => item.url === '/api/orders/drafts').body;
  assert.deepEqual(retry, original);
  assert.equal(storage.has(attemptKey), false);
});

test('a catalog refresh retains a pending product even if it is no longer active', async () => {
  const storage = new Map();
  const first = browser({storage, onDraft(){throw new TypeError('Response lost');}});
  first.setFields(originalData);
  await first.submit();
  const original = first.requests.find(item => item.url === '/api/orders/drafts').body;
  const reloaded = browser({storage, catalog: products.filter(item => item.id !== originalData.product_id)});
  assert.equal(reloaded.draft.fields.product_id.value, originalData.product_id);
  await reloaded.submit();
  assert.deepEqual(reloaded.requests.find(item => item.url === '/api/orders/drafts').body, original);
});
