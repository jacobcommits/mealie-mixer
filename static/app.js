// Mealie Mixer — Alpine frontend (Stage 2: the recipe flow).
// Thin client over the REST API; the session cookie (same-origin) authorises calls.

function mixer() {
  return {
    view: 'gate',                       // gate | needs-setup | input | review | done
    languages: ['English', 'Polish', 'German', 'French', 'Spanish', 'Italian', 'Ukrainian'],
    foods: [],
    // input
    fileList: null,
    url: '',
    language: 'English',
    prompt: '',
    // review
    recipe: emptyRecipe(),
    instructionsText: '',
    queue: [],
    // done
    lastName: '',
    lastUrl: '',
    // ui
    loading: false,
    loadingMsg: '',
    error: '',
    toast: '',

    async init() {
      try {
        // open instances auto-grant a session; configured-with-login is Stage 3
        await api('/api/login', { method: 'POST', body: '{}' });
        const cfg = await (await api('/api/config')).json();
        if (!cfg.configured) { this.view = 'needs-setup'; return; }
        try {
          const f = await (await api('/api/foods')).json();
          this.foods = f.foods || [];
        } catch (_) { this.foods = []; }
        this.view = 'input';
      } catch (e) {
        this.error = 'Could not reach the server.';
        this.view = 'input';
      }
    },

    async extract() {
      this.error = ''; this.loadingMsg = 'Reading your recipe…'; this.loading = true;
      try {
        const fd = new FormData();
        if (this.fileList && this.fileList.length) {
          for (const f of this.fileList) fd.append('files', f);
        } else if (this.url.trim()) {
          fd.append('url', this.url.trim());
        }
        fd.append('language', this.language);
        fd.append('prompt', this.prompt || '');
        const r = await fetch('/api/extract', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const recipes = (await r.json()).recipes || [];
        if (!recipes.length) throw new Error('No recipe found — try a clearer shot or a different link.');
        this.queue = recipes.slice(1);
        this.loadRecipe(recipes[0]);
        this.view = 'review';
      } catch (e) {
        this.error = String(e.message || e);
      } finally { this.loading = false; }
    },

    loadRecipe(r) {
      this.recipe = {
        name: r.name || '', description: r.description || '',
        servings: r.servings, yield: r.yield || '',
        image_url: r.image_url || '', tags: r.tags || [],
        ingredients: (r.ingredients || []).map(i => ({
          quantity: i.quantity ?? '', unit: i.unit ?? '', food: i.food ?? '', note: i.note ?? '',
        })),
      };
      this.instructionsText = (r.instructions || []).join('\n');
    },

    addIngredient() { this.recipe.ingredients.push({ quantity: '', unit: '', food: '', note: '' }); },

    async push() {
      this.error = ''; this.loadingMsg = 'Saving to Mealie…'; this.loading = true;
      try {
        const body = {
          name: (this.recipe.name || '').trim(),
          description: this.recipe.description || '',
          servings: numOrNull(this.recipe.servings),
          yield: this.recipe.yield || '',
          image_url: this.recipe.image_url || null,
          ingredients: this.recipe.ingredients
            .filter(i => blank(i.food) || blank(i.note))
            .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note) })),
          instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean),
          tags: [],
        };
        if (!body.name) throw new Error('Give the recipe a name first.');
        const r = await fetch('/api/push', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body), credentials: 'same-origin',
        });
        if (!r.ok) throw new Error(await detail(r));
        const out = await r.json();
        this.lastName = body.name; this.lastUrl = out.url;
        if (this.queue.length) {
          this.showToast('Pushed — next recipe loaded (' + this.queue.length + ' left)');
          this.loadRecipe(this.queue.shift());
        } else {
          this.view = 'done';
        }
      } catch (e) {
        this.error = String(e.message || e);
      } finally { this.loading = false; }
    },

    discard() {
      // drop just THIS recipe; advance to the next queued one if there is one
      if (this.queue.length) {
        this.showToast('Discarded — next recipe (' + this.queue.length + ' left)');
        this.error = '';
        this.loadRecipe(this.queue.shift());
      } else {
        this.reset();
      }
    },

    reset() {
      this.fileList = null; this.url = ''; this.prompt = ''; this.error = '';
      this.recipe = emptyRecipe(); this.instructionsText = ''; this.queue = [];
      this.view = 'input';
    },

    showToast(m) { this.toast = m; clearTimeout(this._t); this._t = setTimeout(() => this.toast = '', 2600); },
  };
}

// ── helpers ──────────────────────────────────────────────────────────────
function emptyRecipe() {
  return { name: '', description: '', servings: null, yield: '', image_url: '', tags: [], ingredients: [] };
}
function api(path, opts = {}) {
  return fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
}
async function detail(r) {
  try { return (await r.json()).detail || ('HTTP ' + r.status); } catch (_) { return 'HTTP ' + r.status; }
}
function parseQty(v) {
  const s = (v == null ? '' : String(v)).trim().replace(',', '.');
  if (!s) return null;
  const f = parseFloat(s);
  return (isNaN(f) || f === 0) ? null : f;
}
function numOrNull(v) {
  if (v === '' || v == null) return null;
  const n = Number(v);
  return isNaN(n) || n === 0 ? null : n;
}
function blank(v) { const s = (v == null ? '' : String(v)).trim(); return s || null; }
