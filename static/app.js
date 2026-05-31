// Mealie Mixer — Alpine frontend. Thin client over the REST API; the session
// cookie (same-origin) authorises calls.

function mixer() {
  return {
    view: 'gate',   // gate | login | setup | settings | input | review | done
    languages: ['English', 'Polish', 'German', 'French', 'Spanish', 'Italian', 'Ukrainian'],
    foods: [],
    categories: [],
    // config / auth
    cfgInfo: {},
    cfg: emptyCfg(),
    loginUser: '', loginPass: '',
    mealieTest: { ok: false, msg: '' },
    aiTest: { ok: false, msg: '' },
    genKeyMsg: '', cfgMsg: '',
    // recipe input
    fileList: null, url: '', language: 'English', prompt: '',
    // review
    recipe: emptyRecipe(), instructionsText: '', queue: [],
    photoFile: null, photoPreview: '', categoryInput: '',
    // done
    lastName: '', lastUrl: '',
    // ui
    loading: false, loadingMsg: '', error: '', toast: '',

    // ── gate / auth ─────────────────────────────────────────────────────
    async init() {
      try { this.cfgInfo = await getJSON('/api/config'); }
      catch (_) { this.error = 'Could not reach the server.'; }
      if (!this.cfgInfo.configured) { this.prefillCfg(); this.view = 'setup'; return; }
      if (this.cfgInfo.login_required && !this.cfgInfo.authed) { this.view = 'login'; return; }
      if (!this.cfgInfo.login_required) { try { await api('/api/login', { method: 'POST', body: '{}' }); } catch (_) {} }
      await this.afterAuth();
    },
    async afterAuth() {
      try { this.foods = (await getJSON('/api/foods')).foods || []; } catch (_) { this.foods = []; }
      try { this.categories = (await getJSON('/api/categories')).categories || []; } catch (_) { this.categories = []; }
      this.error = ''; this.view = 'input';
    },
    async doLogin() {
      this.error = ''; this.loading = true;
      try {
        const r = await api('/api/login', { method: 'POST', body: JSON.stringify({ username: this.loginUser, password: this.loginPass }) });
        if (!r.ok) throw new Error('Invalid username or password.');
        this.loginPass = '';
        this.cfgInfo = await getJSON('/api/config');
        await this.afterAuth();
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    async logout() { try { await api('/api/logout', { method: 'POST', body: '{}' }); } catch (_) {} location.reload(); },

    // ── setup / settings ────────────────────────────────────────────────
    prefillCfg() {
      const c = this.cfgInfo || {};
      this.cfg = {
        mealie_url: c.mealie_url || '', mealie_token: '', ai_key: '', api_key: '',
        ai_base: c.ai_base_url || 'https://generativelanguage.googleapis.com/v1beta/openai/',
        ai_model: c.ai_model || 'gemini-3.1-flash-lite',
        auth_user: c.auth_user || '', auth_pass: '',
      };
    },
    async openSettings() {
      try { this.cfgInfo = await getJSON('/api/config'); } catch (_) {}
      this.prefillCfg();
      this.mealieTest = { ok: false, msg: '' }; this.aiTest = { ok: false, msg: '' };
      this.genKeyMsg = ''; this.cfgMsg = ''; this.error = '';
      this.view = 'settings';
    },
    pinned(key) { return (this.cfgInfo.env_pinned || []).includes(key); },
    secretPh(which) {
      const has = { MEALIE: this.cfgInfo.has_mealie_token, AI: this.cfgInfo.has_ai_key, MIXER_API: this.cfgInfo.has_api_key }[which];
      if (has) return 'leave blank to keep current';
      return which === 'MIXER_API' ? 'leave blank to disable the API' : 'required';
    },
    async testMealie() {
      this.mealieTest = { ok: false, msg: 'testing…' };
      try { const j = await (await api('/api/config/test-mealie', { method: 'POST', body: JSON.stringify(this.cfg) })).json();
        this.mealieTest = { ok: j.ok, msg: (j.ok ? '✅ ' : '❌ ') + j.message };
      } catch (e) { this.mealieTest = { ok: false, msg: '❌ ' + e }; }
    },
    async testAi() {
      this.aiTest = { ok: false, msg: 'testing…' };
      try { const j = await (await api('/api/config/test-ai', { method: 'POST', body: JSON.stringify(this.cfg) })).json();
        this.aiTest = { ok: j.ok, msg: (j.ok ? '✅ ' : '❌ ') + j.message };
      } catch (e) { this.aiTest = { ok: false, msg: '❌ ' + e }; }
    },
    async genKey() {
      try { const j = await (await api('/api/config/generate-key', { method: 'POST', body: '{}' })).json();
        this.cfg.api_key = j.key; this.genKeyMsg = 'Copy this for your agent: ' + j.key;
      } catch (e) { this.genKeyMsg = 'Could not generate: ' + e; }
    },
    async saveConfig() {
      this.error = ''; this.cfgMsg = ''; this.loading = true;
      try {
        const r = await api('/api/config', { method: 'POST', body: JSON.stringify(this.cfg) });
        if (!r.ok) throw new Error(await detail(r));
        this.cfgInfo = await getJSON('/api/config');
        if (this.view === 'setup') {
          if (this.cfgInfo.login_required && !this.cfgInfo.authed) { this.view = 'login'; }
          else { if (!this.cfgInfo.login_required) { try { await api('/api/login', { method: 'POST', body: '{}' }); } catch (_) {} } await this.afterAuth(); }
        } else {
          this.showToast('Settings saved');
          this.cfgMsg = 'Saved. Mealie/AI apply now; a login change takes effect next login.';
        }
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },

    // ── recipe flow ─────────────────────────────────────────────────────
    async extract() {
      this.error = ''; this.loadingMsg = 'Reading your recipe…'; this.loading = true;
      try {
        const fd = new FormData();
        if (this.fileList && this.fileList.length) { for (const f of this.fileList) fd.append('files', f); }
        else if (this.url.trim()) { fd.append('url', this.url.trim()); }
        fd.append('language', this.language); fd.append('prompt', this.prompt || '');
        const r = await fetch('/api/extract', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const recipes = (await r.json()).recipes || [];
        if (!recipes.length) throw new Error('No recipe found — try a clearer shot or a different link.');
        this.queue = recipes.slice(1); this.loadRecipe(recipes[0]); this.view = 'review';
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    pickPhoto(e) {
      const f = e.target.files && e.target.files[0];
      this.clearPhoto();
      if (f) { this.photoFile = f; this.photoPreview = URL.createObjectURL(f); }
    },
    clearPhoto() {
      if (this.photoPreview) URL.revokeObjectURL(this.photoPreview);
      this.photoFile = null; this.photoPreview = '';
    },

    loadRecipe(r) {
      this.clearPhoto();   // each recipe starts without a picked photo
      this.recipe = {
        name: r.name || '', description: r.description || '', servings: r.servings,
        yield: r.yield || '', image_url: r.image_url || '', tags: r.tags || [],
        categories: r.categories || [],
        ingredients: (r.ingredients || []).map(i => ({ quantity: i.quantity ?? '', unit: i.unit ?? '', food: i.food ?? '', note: i.note ?? '' })),
      };
      this.instructionsText = (r.instructions || []).join('\n');
      this.categoryInput = '';
    },
    addIngredient() { this.recipe.ingredients.push({ quantity: '', unit: '', food: '', note: '' }); },
    addCategory(name) {
      const v = (name == null ? this.categoryInput : name).trim();
      this.categoryInput = '';
      if (v && !this.recipe.categories.some(c => c.toLowerCase() === v.toLowerCase())) this.recipe.categories.push(v);
    },
    removeCategory(i) { this.recipe.categories.splice(i, 1); },
    async push() {
      this.error = ''; this.loadingMsg = 'Saving to Mealie…'; this.loading = true;
      try {
        const body = {
          name: (this.recipe.name || '').trim(), description: this.recipe.description || '',
          servings: numOrNull(this.recipe.servings), yield: this.recipe.yield || '',
          image_url: this.photoFile ? null : (this.recipe.image_url || null),  // picked file wins
          ingredients: this.recipe.ingredients.filter(i => blank(i.food) || blank(i.note))
            .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note) })),
          instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean), tags: [],
          categories: this.recipe.categories || [],
        };
        if (!body.name) throw new Error('Give the recipe a name first.');
        const r = await fetch('/api/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const out = await r.json(); this.lastName = body.name; this.lastUrl = out.url;
        if (this.photoFile) {
          try {
            const fd = new FormData(); fd.append('file', this.photoFile);
            const ir = await fetch('/api/recipe-image/' + out.slug, { method: 'PUT', body: fd, credentials: 'same-origin' });
            if (!ir.ok) this.showToast('Recipe saved — but the photo upload failed');
          } catch (_) { this.showToast('Recipe saved — but the photo upload failed'); }
        }
        if (this.queue.length) { this.showToast('Pushed — next recipe loaded (' + this.queue.length + ' left)'); this.loadRecipe(this.queue.shift()); }
        else { this.view = 'done'; }
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    discard() {
      if (this.queue.length) { this.showToast('Discarded — next recipe (' + this.queue.length + ' left)'); this.error = ''; this.loadRecipe(this.queue.shift()); }
      else { this.reset(); }
    },
    reset() {
      this.fileList = null; this.url = ''; this.prompt = ''; this.error = '';
      this.clearPhoto();
      this.recipe = emptyRecipe(); this.instructionsText = ''; this.queue = []; this.view = 'input';
    },
    showToast(m) { this.toast = m; clearTimeout(this._t); this._t = setTimeout(() => this.toast = '', 2600); },
  };
}

// ── helpers ────────────────────────────────────────────────────────────
function emptyRecipe() { return { name: '', description: '', servings: null, yield: '', image_url: '', tags: [], categories: [], ingredients: [] }; }
function emptyCfg() { return { mealie_url: '', mealie_token: '', ai_key: '', ai_base: '', ai_model: '', auth_user: '', auth_pass: '', api_key: '' }; }
function api(path, opts = {}) {
  return fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
}
async function getJSON(path) { return (await api(path)).json(); }
async function detail(r) { try { return (await r.json()).detail || ('HTTP ' + r.status); } catch (_) { return 'HTTP ' + r.status; } }
function parseQty(v) { const s = (v == null ? '' : String(v)).trim().replace(',', '.'); if (!s) return null; const f = parseFloat(s); return (isNaN(f) || f === 0) ? null : f; }
function numOrNull(v) { if (v === '' || v == null) return null; const n = Number(v); return isNaN(n) || n === 0 ? null : n; }
function blank(v) { const s = (v == null ? '' : String(v)).trim(); return s || null; }
