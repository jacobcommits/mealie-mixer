// Mealie Mixer — Alpine frontend. Thin client over the REST API; the session
// cookie (same-origin) authorises calls.

function mixer() {
  return {
    view: 'gate',   // gate | login | setup | settings | input | review | done
    languages: ['English', 'Polish', 'German', 'French', 'Spanish', 'Italian', 'Ukrainian'],
    foods: [],
    categories: [],
    recipeNames: [],
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
    sourceImages: [], zoomSrc: '',
    dupModal: false, _dupOk: false,
    // done
    lastName: '', lastUrl: '',
    // ui
    loading: false, loadingMsg: '', error: '', toast: '',

    // ── gate / auth ─────────────────────────────────────────────────────
    async init() {
      try { this.language = localStorage.getItem('mm-lang') || this.language; } catch (_) {}
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
      try { this.recipeNames = (await getJSON('/api/recipe-names')).names || []; } catch (_) { this.recipeNames = []; }
      this.error = ''; this.view = 'input';
      this.restoreSession();
      await this.maybeReadShare();
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
      try { localStorage.setItem('mm-lang', this.language); } catch (_) {}   // remember for next time / share flow
      this.clearSourceImages();
      if (this.fileList && this.fileList.length) this.sourceImages = [...this.fileList].map(f => URL.createObjectURL(f));
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
        categories: r.categories || [], source_url: r.source_url || '',
        ingredients: (r.ingredients || []).map(i => ({ quantity: i.quantity ?? '', unit: i.unit ?? '', food: i.food ?? '', note: i.note ?? '' })),
      };
      this.instructionsText = (r.instructions || []).join('\n');
      this.categoryInput = '';
      this.saveSession();
    },
    addIngredient() { this.recipe.ingredients.push({ quantity: '', unit: '', food: '', note: '' }); },
    addCategory(name) {
      const v = (name == null ? this.categoryInput : name).trim();
      this.categoryInput = '';
      if (v && !this.recipe.categories.some(c => c.toLowerCase() === v.toLowerCase())) this.recipe.categories.push(v);
    },
    removeCategory(i) { this.recipe.categories.splice(i, 1); },
    nameExists() {
      const n = (this.recipe.name || '').trim().toLowerCase();
      return !!n && this.recipeNames.some(x => x.toLowerCase() === n);
    },
    confirmDup() { this.dupModal = false; this._dupOk = true; this.push(); },
    cancelDup() { this.dupModal = false; },
    nearestFood(name) {
      const raw = (name || '').trim(); if (!raw) return '';
      const lc = raw.toLowerCase();
      if (this.foods.some(f => f.toLowerCase() === lc)) return '';   // already a real food
      const norm = s => s.toLowerCase().trim().replace(/\s+/g, ' ').replace(/s$/, '');
      const target = norm(raw);
      for (const f of this.foods) { const fl = f.toLowerCase(); if (norm(f) === target || lev(lc, fl) <= 1) return f; }
      return '';
    },
    foodStatus(name) {
      const raw = (name || '').trim(); if (!raw) return '';
      if (this.foods.some(f => f.toLowerCase() === raw.toLowerCase())) return 'exists';
      return this.nearestFood(raw) ? 'near' : 'new';
    },
    socialUrl() {
      // social posts aren't scrapeable (auth-walled, recipe is in the caption) —
      // the user should screenshot the post and share the image instead
      return /(?:instagram\.com|tiktok\.com|fb\.watch|facebook\.com)/i.test(this.url || '');
    },
    clearSourceImages() { this.sourceImages.forEach(u => { try { URL.revokeObjectURL(u); } catch (_) {} }); this.sourceImages = []; },
    saveSession() {
      try { localStorage.setItem('mm-session', JSON.stringify({ recipe: this.recipe, instructionsText: this.instructionsText, queue: this.queue })); } catch (_) {}
    },
    clearSession() { try { localStorage.removeItem('mm-session'); } catch (_) {} },
    restoreSession() {
      let s; try { s = JSON.parse(localStorage.getItem('mm-session') || 'null'); } catch (_) { s = null; }
      if (s && s.recipe && ((s.recipe.name || '').trim() || (s.recipe.ingredients || []).length || (s.queue || []).length)) {
        this.recipe = s.recipe; this.instructionsText = s.instructionsText || ''; this.queue = s.queue || [];
        this.view = 'review'; this.showToast('Restored your in-progress review');
      }
    },
    // ── Web Share Target: the SW stashed shared image(s)/link in a cache and
    //    redirected here with ?shared=1; pull it into the input screen.
    async maybeReadShare() {
      if (!new URLSearchParams(location.search).has('shared')) return;
      let got = false;
      try { got = await this.readShare(); } catch (_) {}
      try { history.replaceState({}, '', location.pathname); } catch (_) {}
      if (got) { this.view = 'input'; this.showToast('Shared in — pick a language, then Extract'); }
    },
    async readShare() {
      const cache = await caches.open('mm-share');
      const metaRes = await cache.match('/__share_meta');
      if (!metaRes) return false;
      let meta = {}; try { meta = await metaRes.json(); } catch (_) {}
      const files = [];
      for (let i = 0; i < (meta.count || 0); i++) {
        const fr = await cache.match('/__share_file_' + i);
        if (!fr) continue;
        const blob = await fr.blob();
        files.push(new File([blob], fr.headers.get('x-filename') || ('shared-' + i + '.jpg'), { type: blob.type || 'image/jpeg' }));
        await cache.delete('/__share_file_' + i);
      }
      await cache.delete('/__share_meta');
      if (files.length) { this.fileList = files; this.url = ''; return true; }
      const shared = (meta.url || meta.text || '').trim();
      if (shared) { const m = shared.match(/https?:\/\/\S+/); this.url = m ? m[0] : shared; return true; }
      return false;
    },
    async push() {
      if (this.nameExists() && !this._dupOk) { this.dupModal = true; return; }  // confirm duplicates
      this._dupOk = false;
      this.error = ''; this.loadingMsg = 'Saving to Mealie…'; this.loading = true;
      try {
        const body = {
          name: (this.recipe.name || '').trim(), description: this.recipe.description || '',
          servings: numOrNull(this.recipe.servings), yield: this.recipe.yield || '',
          image_url: this.photoFile ? null : (this.recipe.image_url || null),  // picked file wins
          ingredients: this.recipe.ingredients.filter(i => blank(i.food) || blank(i.note))
            .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note) })),
          instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean), tags: [],
          categories: this.recipe.categories || [], source_url: this.recipe.source_url || '',
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
        else { this.clearSession(); this.view = 'done'; }
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    discard() {
      if (this.queue.length) { this.showToast('Discarded — next recipe (' + this.queue.length + ' left)'); this.error = ''; this.loadRecipe(this.queue.shift()); }
      else { this.reset(); }
    },
    reset() {
      this.fileList = null; this.url = ''; this.prompt = ''; this.error = '';
      this.clearPhoto(); this.clearSourceImages(); this.zoomSrc = ''; this.dupModal = false; this.clearSession();
      this.recipe = emptyRecipe(); this.instructionsText = ''; this.queue = []; this.view = 'input';
    },
    showToast(m) { this.toast = m; clearTimeout(this._t); this._t = setTimeout(() => this.toast = '', 2600); },
  };
}

// ── helpers ────────────────────────────────────────────────────────────
function emptyRecipe() { return { name: '', description: '', servings: null, yield: '', image_url: '', tags: [], categories: [], source_url: '', ingredients: [] }; }
function emptyCfg() { return { mealie_url: '', mealie_token: '', ai_key: '', ai_base: '', ai_model: '', auth_user: '', auth_pass: '', api_key: '' }; }
function api(path, opts = {}) {
  return fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
}
async function getJSON(path) { return (await api(path)).json(); }
async function detail(r) { try { return (await r.json()).detail || ('HTTP ' + r.status); } catch (_) { return 'HTTP ' + r.status; } }
function parseQty(v) { const s = (v == null ? '' : String(v)).trim().replace(',', '.'); if (!s) return null; const f = parseFloat(s); return (isNaN(f) || f === 0) ? null : f; }
function numOrNull(v) { if (v === '' || v == null) return null; const n = Number(v); return isNaN(n) || n === 0 ? null : n; }
function blank(v) { const s = (v == null ? '' : String(v)).trim(); return s || null; }
// Levenshtein distance, bounded — we only care whether it's ≤1 (a single typo).
function lev(a, b) {
  if (Math.abs(a.length - b.length) > 1) return 2;
  const m = a.length, n = b.length;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[n];
}
