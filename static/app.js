// Mealie Mixer — Alpine frontend. Thin client over the REST API; the session
// cookie (same-origin) authorises calls.

function mixer() {
  return {
    view: 'gate',   // gate | login | setup | settings | input | review | done | history | cookbook-upload | cookbook | cookbook-review | fix
    languages: ['English', 'Polish', 'German', 'French', 'Spanish', 'Italian', 'Ukrainian'],
    foods: [],
    categories: [],
    tags: [],
    recipeNames: [],
    history: [],
    users: [], newUser: { username: '', password: '', display_name: '' }, userMsg: '',   // admin user management (v0.15.0)
    myPassword: '', myPasswordMsg: '', myDisplayName: '', myDisplayNameMsg: '',   // self-service account management
    expandedId: null, payloads: {},   // history-row preview (lazy-loaded by id)
    // config / auth
    cfgInfo: {},
    cfg: emptyCfg(),
    loginUser: '', loginPass: '',
    mealieTest: { ok: false, msg: '' },
    aiTest: { ok: false, msg: '' },
    genKeyMsg: '', cfgMsg: '',
    // recipe input
    activeTab: 'link',   // link | photo | voice | text
    fileList: null, url: '', pastedText: '', language: 'English', unitsSystem: 'metric', prompt: '',
    recording: false, audioBlob: null, audioUrl: '', recElapsed: 0, audioProgress: 0, jobActive: false,   // voice note (B3) + tab-close resume
    // review
    recipe: emptyRecipe(), instructionsText: '', queue: [],
    photoFile: null, photoPreview: '', categoryInput: '', tagInput: '',
    commonUnits: ['g', 'ml', 'tbsp', 'tsp', 'cup', 'clove', 'slice', 'piece', 'can', 'pinch', 'pack', 'head', 'stick', 'rasher', 'kg', 'l', 'oz', 'lb', 'fl oz'],
    foodPickerModal: false, foodPickerSearch: '', foodPickerTargetIng: null,
    dupModal: false, _dupOk: false,
    cropModal: false, cropSrc: '', cropAngle: 0, cropMargins: { top: 0, bottom: 0, left: 0, right: 0 },
    // cookbook (B7, dev)
    cbRecipes: [], cbStructured: [], cbExpanded: null, cbEditIndex: null, cookbookJob: null, cbReviewJobId: '',
    // done
    lastName: '', lastUrl: '',
    // fix existing recipe (B9)
    mealieRecipes: [], fixSearch: '', updateMode: null,  // null = create, 'slug' = update that slug
    // ui
    loading: false, loadingMsg: '', error: '', toast: '', menuOpen: false,

    // ── gate / auth ─────────────────────────────────────────────────────
    async init() {
      try { this.language = localStorage.getItem('mm-lang') || this.language; } catch (_) {}
      try { this.unitsSystem = localStorage.getItem('mm-units') || this.unitsSystem; } catch (_) {}
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
      try { this.tags = (await getJSON('/api/tags')).tags || []; } catch (_) { this.tags = []; }
      try { this.recipeNames = (await getJSON('/api/recipe-names')).names || []; } catch (_) { this.recipeNames = []; }
      try { this.history = (await getJSON('/api/history')).items || []; } catch (_) { this.history = []; }
      this.error = ''; this.view = 'input';
      this.restoreSession();
      // a saved cookbook review wins; otherwise pick up a running/done job (close & come back)
      if (!(await this.cbRestoreReview())) this.cbResume();
      // re-attach to an in-flight voice-note / combine extract job, unless something already
      // claimed the screen (a restored in-progress review or a cookbook job).
      if (this.view === 'input') await this.audioResume();
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
        ai_rpm: c.ai_rpm || '',
        ai_rules: c.ai_rules || '',
        auth_user: c.auth_user || '', auth_pass: '',
      };
    },
    async openSettings() {
      this.menuOpen = false;
      try { this.cfgInfo = await getJSON('/api/config'); } catch (_) {}
      this.prefillCfg();
      this.mealieTest = { ok: false, msg: '' }; this.aiTest = { ok: false, msg: '' };
      this.genKeyMsg = ''; this.cfgMsg = ''; this.error = '';
      this.view = 'settings';
    },
    async openHistory() {
      this.menuOpen = false; this.expandedId = null;
      try { this.history = (await getJSON('/api/history')).items || []; } catch (_) {}
      this.error = ''; this.view = 'history';
    },
    async openAccount() {
      this.menuOpen = false; this.error = '';
      this.myPassword = ''; this.myPasswordMsg = '';
      this.myDisplayName = this.cfgInfo.display_name || ''; this.myDisplayNameMsg = '';
      this.view = 'account';
    },
    async updateMyDisplayName() {
      this.error = ''; this.myDisplayNameMsg = '';
      try {
        const r = await api('/api/users/me/display-name', { method: 'POST', body: JSON.stringify({ display_name: this.myDisplayName }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        this.cfgInfo.display_name = j.display_name;
        this.myDisplayNameMsg = 'Display name saved.';
        this.showToast('Display name updated');
      } catch (e) { this.error = String(e.message || e); }
    },
    async changeMyPassword() {
      this.error = ''; this.myPasswordMsg = '';
      if (!this.myPassword) { this.error = "Password can't be empty."; return; }
      try {
        const r = await api('/api/users/me/password', { method: 'POST', body: JSON.stringify({ password: this.myPassword }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        this.myPassword = '';
        this.myPasswordMsg = 'Password changed successfully.';
        this.showToast('Password updated');
      } catch (e) { this.error = String(e.message || e); }
    },
    async openUsers() {
      this.menuOpen = false; this.error = ''; this.userMsg = '';
      this.newUser = { username: '', password: '', display_name: '' };
      this.loadingMsg = 'Loading users…'; this.loading = true;
      try {
        this.users = (await getJSON('/api/users')).users || [];
        this.view = 'users';
      } catch (e) { this.error = 'Could not load users: ' + (e.message || e); }
      finally { this.loading = false; }
    },
    async addUser() {
      this.error = ''; this.userMsg = '';
      const username = (this.newUser.username || '').trim();
      if (!username || !this.newUser.password) { this.error = 'Enter a username and a password.'; return; }
      try {
        const r = await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password: this.newUser.password, display_name: this.newUser.display_name || '', is_admin: false }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        this.users = j.users || []; this.newUser = { username: '', password: '', display_name: '' };
        this.userMsg = 'Added "' + username + '".';
      } catch (e) { this.error = String(e.message || e); }
    },
    async resetUserPassword(u) {
      const pw = window.prompt('New password for "' + u.username + '":', '');
      if (pw == null) return;
      if (!pw) { this.error = "Password can't be empty."; return; }
      this.error = '';
      try {
        const r = await api('/api/users/' + encodeURIComponent(u.username) + '/password', { method: 'POST', body: JSON.stringify({ password: pw }) });
        if (!r.ok) throw new Error(await detail(r));
        this.showToast('Password reset for "' + u.username + '"');
      } catch (e) { this.error = String(e.message || e); }
    },
    async toggleAdmin(u) {
      this.error = '';
      try {
        const r = await api('/api/users/' + encodeURIComponent(u.username) + '/admin', { method: 'POST', body: JSON.stringify({ is_admin: !u.is_admin }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        this.users = j.users || [];
      } catch (e) { this.error = String(e.message || e); }
    },
    async deleteUser(u) {
      if (!confirm('Delete user "' + u.username + '"? This can\'t be undone.')) return;
      this.error = '';
      try {
        const r = await api('/api/users/' + encodeURIComponent(u.username), { method: 'DELETE' });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
        this.users = j.users || [];
        this.showToast('Deleted "' + u.username + '"');
      } catch (e) { this.error = String(e.message || e); }
    },
    goHome() {
      if (['gate', 'login', 'setup'].includes(this.view)) return;   // not navigable yet
      this.menuOpen = false; this.error = ''; this.view = 'input';
    },
    openCookbook() {
      if (this.cookbookJob) { this.openCookbookJob(); return; }   // a job is waiting — go to it
      this.menuOpen = false; this.error = '';
      this.cbRecipes = []; this.cbStructured = []; this.cbExpanded = null; this.cbEditIndex = null;
      this.view = 'cookbook-upload';
    },
    async openFix() {
      this.menuOpen = false; this.error = ''; this.fixSearch = '';
      this.loadingMsg = 'Loading recipes…'; this.loading = true;
      try {
        this.mealieRecipes = (await getJSON('/api/mealie-recipes')).recipes || [];
        this.view = 'fix';
      } catch (e) { this.error = 'Could not load recipes from Mealie: ' + (e.message || e); }
      finally { this.loading = false; }
    },
    fixFiltered() {
      const q = (this.fixSearch || '').trim().toLowerCase();
      if (!q) return this.mealieRecipes;
      return this.mealieRecipes.filter(r => r.name.toLowerCase().includes(q));
    },
    async toggleHist(h) {
      if (this.expandedId === h.id) { this.expandedId = null; return; }
      if (!this.payloads[h.id]) {
        try { const row = await getJSON('/api/history/' + h.id); this.payloads = { ...this.payloads, [h.id]: row.payload || {} }; }
        catch (_) { this.payloads = { ...this.payloads, [h.id]: {} }; }
      }
      this.expandedId = h.id;
    },
    ingLine(ing) {
      const p = [];
      if (ing.quantity !== '' && ing.quantity != null) p.push(ing.quantity);
      if (ing.unit) p.push(ing.unit);
      if (ing.food) p.push(ing.food);
      let s = p.join(' ');
      if (ing.note) s += (s ? ', ' : '') + ing.note;
      return s || '(item)';
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
    hasTabSource(t) {
      if (t === 'link') return !!(this.url || '').trim();
      if (t === 'photo') return !!(this.fileList && this.fileList.length);
      if (t === 'voice') return !!this.audioBlob || this.recording;
      if (t === 'text') return !!(this.pastedText || '').trim();
      return false;
    },
    async extract() {
      this.error = '';
      // Any audio/video → background job (whisper is slow). Everything else combines
      // synchronously (the backend merges all provided sources into one recipe).
      if (this.audioBlob) { return this.extractJob(); }
      this.loadingMsg = 'Reading your recipe…'; this.loading = true;
      try { localStorage.setItem('mm-lang', this.language); localStorage.setItem('mm-units', this.unitsSystem); } catch (_) {}   // remember for next time / share flow
      this.clearSourceImages();
      if (this.fileList && this.fileList.length) this.sourceImages = [...this.fileList].filter(f => (f.type || '').startsWith('image/')).map(f => URL.createObjectURL(f));
      try {
        const fd = new FormData();
        if (this.fileList && this.fileList.length) { for (const f of this.fileList) fd.append('files', f); }
        if (this.url.trim()) { fd.append('url', this.url.trim()); }
        if (this.pastedText.trim()) { fd.append('text', this.pastedText.trim()); }
        fd.append('language', this.language); fd.append('prompt', this.prompt || ''); fd.append('units_system', this.unitsSystem);
        const r = await fetch('/api/extract', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const recipes = (await r.json()).recipes || [];
        if (!recipes.length) throw new Error('No recipe found — try a clearer shot or a different link.');
        this.queue = recipes.slice(1); this.updateMode = null; this.loadRecipe(recipes[0]); this.view = 'review';
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    // Combine flow: when a voice note / screen-recording is in the mix, transcription (and
    // any link fetch) runs server-side as a job we poll, so the slow first run doesn't hang
    // the request. Sends every source the user added — they get merged into one recipe.
    async extractJob() {
      this.error = ''; this.audioProgress = 0;
      this.loadingMsg = 'Working…'; this.loading = true;
      try { localStorage.setItem('mm-lang', this.language); localStorage.setItem('mm-units', this.unitsSystem); } catch (_) {}
      this.clearSourceImages();
      if (this.fileList && this.fileList.length) this.sourceImages = [...this.fileList].filter(f => (f.type || '').startsWith('image/')).map(f => URL.createObjectURL(f));
      try {
        const fd = new FormData();
        if (this.fileList && this.fileList.length) { for (const f of this.fileList) fd.append('files', f); }
        if (this.url.trim()) { fd.append('url', this.url.trim()); }
        if (this.pastedText.trim()) { fd.append('text', this.pastedText.trim()); }
        if (this.audioBlob) { fd.append('audio', this.audioBlob, 'note.webm'); }
        fd.append('language', this.language); fd.append('prompt', this.prompt || ''); fd.append('units_system', this.unitsSystem);
        const r = await fetch('/api/extract/job', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        this._extractJob = (await r.json()).job_id;
        this.jobActive = true;
        try { localStorage.setItem('mm-extract-job', this._extractJob); } catch (_) {}   // tab-close resume
        this.jobPoll();
      } catch (e) { this.error = String(e.message || e); this.loading = false; }
    },
    async jobPoll() {
      const jid = this._extractJob; if (!jid) return;
      let job;
      try { job = await getJSON('/api/extract/job/' + jid); }
      catch (e) { this.error = String(e.message || e); this.loading = false; this._clearExtractJob(); return; }
      this.audioProgress = job.progress || 0;
      this.loadingMsg = ({
        'fetching link': 'Fetching the link…',
        'transcribing': 'Transcribing… ' + Math.round((job.progress || 0) * 100) + '%',
        'structuring': 'Structuring the recipe…',
      })[job.phase] || 'Working…';
      if (job.status === 'done') {
        this.loading = false; this.audioProgress = 0; this._clearExtractJob();
        const recipes = (job.recipes || []).map(x => x.recipe);
        if (!recipes.length) { this.error = 'No recipe found — try adding a clearer source.'; return; }
        this.queue = recipes.slice(1); this.updateMode = null; this.loadRecipe(recipes[0]); this.view = 'review';
        return;
      }
      if (job.status === 'error') {
        this.loading = false; this.audioProgress = 0; this._clearExtractJob();
        this.error = job.error || 'Extraction failed.'; return;
      }
      this._extractTimer = setTimeout(() => this.jobPoll(), 1500);
    },
    _clearExtractJob() { this._extractJob = null; this.jobActive = false; try { localStorage.removeItem('mm-extract-job'); } catch (_) {} },
    // Tab-close resume: a voice-note / combine extract runs as a server job persisted to /data,
    // so if the tab is closed and reopened we re-attach to it and pick the progress back up
    // (mirrors the cookbook job resume). jobPoll handles every outcome (progress / review / error).
    async audioResume() {
      let jid; try { jid = localStorage.getItem('mm-extract-job'); } catch (_) {}
      if (!jid) return;
      try { await getJSON('/api/extract/job/' + jid); }   // still there? (404 once the job is gone)
      catch (_) { this._clearExtractJob(); return; }       // stale id — drop it silently, no error banner
      this._extractJob = jid; this.jobActive = true;
      this.loading = true; this.loadingMsg = 'Working…'; this.audioProgress = 0;
      this.jobPoll();
    },
    // Escape hatch on the progress overlay: stop waiting. Detaches the browser only — there is no
    // server-side cancel for a single in-flight LLM call (closing the tab has the same effect), but
    // it frees the user from a job that can't finish (e.g. the server was restarted mid-transcription).
    cancelExtractJob() {
      try { clearTimeout(this._extractTimer); } catch (_) {}
      this._clearExtractJob();
      this.loading = false; this.audioProgress = 0; this.loadingMsg = '';
      this.view = 'input'; this.showToast('Stopped waiting for the import');
    },
    // ── voice note (B3) ──────────────────────────────────────────────────
    async toggleRecord() {
      if (this.recording) { try { this._rec && this._rec.stop(); } catch (_) {} return; }
      this.error = '';
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const rec = new MediaRecorder(stream);
        const chunks = [];
        rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = () => {
          stream.getTracks().forEach(t => t.stop());
          clearInterval(this._recTimer); this.recording = false;
          this.clearAudio();
          this.audioBlob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
          this.audioUrl = URL.createObjectURL(this.audioBlob);
        };
        this._rec = rec; rec.start();
        this.recording = true; this.recElapsed = 0;
        const t0 = Date.now();
        this._recTimer = setInterval(() => this.recElapsed = Math.floor((Date.now() - t0) / 1000), 250);

        // Start waveform visualizer
        setTimeout(() => {
          try { this.visualize(stream); } catch (_) {}
        }, 100);
      } catch (_) { this.error = 'Microphone unavailable or permission denied.'; }
    },
    visualize(stream) {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 32;
      source.connect(analyser);
      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      
      const canvas = document.getElementById('waveCanvas');
      if (!canvas) return;
      const canvasCtx = canvas.getContext('2d');
      
      const draw = () => {
        if (!this.recording) {
          canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
          try { audioCtx.close(); } catch (_) {}
          return;
        }
        requestAnimationFrame(draw);
        analyser.getByteFrequencyData(dataArray);
        
        canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
        
        const barWidth = (canvas.width / bufferLength) * 1.6;
        let barHeight;
        let x = 0;
        
        for (let i = 0; i < bufferLength; i++) {
          barHeight = (dataArray[i] / 255) * canvas.height * 0.9;
          if (barHeight < 3) barHeight = 3;
          
          const grad = canvasCtx.createLinearGradient(0, canvas.height, 0, 0);
          grad.addColorStop(0, '#f97316');
          grad.addColorStop(1, '#fdba74');
          canvasCtx.fillStyle = grad;
          
          const y = (canvas.height - barHeight) / 2;
          canvasCtx.beginPath();
          if (canvasCtx.roundRect) {
            canvasCtx.roundRect(x, y, barWidth - 3, barHeight, 3);
          } else {
            canvasCtx.rect(x, y, barWidth - 3, barHeight);
          }
          canvasCtx.fill();
          
          x += barWidth;
        }
      };
      draw();
    },
    pickAudio(e) {
      const f = e.target.files && e.target.files[0]; e.target.value = '';
      if (f) { this.clearAudio(); this.audioBlob = f; this.audioUrl = URL.createObjectURL(f); }
    },
    clearAudio() {
      if (this.audioUrl) { try { URL.revokeObjectURL(this.audioUrl); } catch (_) {} }
      this.audioBlob = null; this.audioUrl = ''; this.recElapsed = 0;
    },
    recClock() { const s = this.recElapsed; return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); },
    pickPhoto(e) {
      const f = e.target.files && e.target.files[0];
      this.clearPhoto();
      if (f) { this.photoFile = f; this.photoPreview = URL.createObjectURL(f); }
    },
    clearPhoto() {
      if (this.photoPreview) URL.revokeObjectURL(this.photoPreview);
      this.photoFile = null; this.photoPreview = '';
    },
    removePhoto() { this.clearPhoto(); this.recipe.image_url = ''; },   // clears a picked file AND an auto thumbnail → no photo
    autoPhotoWarn() {  // an auto-grabbed thumbnail from a social post (often not the dish)
      return !this.photoFile && !!this.recipe.image_url &&
        /(instagram|tiktok|youtube|youtu\.be|facebook|fb\.watch)/i.test(this.recipe.source_url || '');
    },

    // ── Image Cropper & Photo Refinement (v0.21.0) ────────────────────────
    openCrop(src) {
      if (!src) return;
      this.cropSrc = src;
      this.cropAngle = 0;
      this.cropMargins = { top: 0, bottom: 0, left: 0, right: 0 };
      this.cropModal = true;
      this.$nextTick(() => this.renderCropCanvas());
    },
    rotateCrop() {
      this.cropAngle = (this.cropAngle + 90) % 360;
      this.renderCropCanvas();
    },
    resetCrop() {
      this.cropAngle = 0;
      this.cropMargins = { top: 0, bottom: 0, left: 0, right: 0 };
      this.renderCropCanvas();
    },
    renderCropCanvas() {
      const canvas = this.$refs.cropCanvas;
      if (!canvas || !this.cropSrc) return;
      const ctx = canvas.getContext('2d');
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const rad = (this.cropAngle * Math.PI) / 180;
        const swap = (this.cropAngle / 90) % 2 !== 0;
        const width = swap ? img.height : img.width;
        const height = swap ? img.width : img.height;
        canvas.width = width;
        canvas.height = height;

        ctx.save();
        ctx.translate(width / 2, height / 2);
        ctx.rotate(rad);
        ctx.drawImage(img, -img.width / 2, -img.height / 2);
        ctx.restore();
      };
      img.src = this.cropSrc;
    },
    applyCrop() {
      const sourceCanvas = this.$refs.cropCanvas;
      if (!sourceCanvas) return;
      const m = this.cropMargins;
      const x = (m.left / 100) * sourceCanvas.width;
      const y = (m.top / 100) * sourceCanvas.height;
      const w = sourceCanvas.width * (1 - (m.left + m.right) / 100);
      const h = sourceCanvas.height * (1 - (m.top + m.bottom) / 100);

      if (w <= 10 || h <= 10) {
        this.showToast('Crop region too small');
        return;
      }

      const outCanvas = document.createElement('canvas');
      outCanvas.width = Math.round(w);
      outCanvas.height = Math.round(h);
      const outCtx = outCanvas.getContext('2d');
      outCtx.drawImage(sourceCanvas, Math.round(x), Math.round(y), Math.round(w), Math.round(h), 0, 0, Math.round(w), Math.round(h));

      outCanvas.toBlob(blob => {
        if (!blob) return;
        this.clearPhoto();
        const file = new File([blob], 'cropped-dish.jpg', { type: 'image/jpeg' });
        this.photoFile = file;
        this.photoPreview = URL.createObjectURL(blob);
        this.recipe.image_url = '';
        this.cropModal = false;
        this.showToast('Photo updated!');
      }, 'image/jpeg', 0.92);
    },

    loadRecipe(r) {
      this.clearPhoto();   // each recipe starts without a picked photo
      this.cbEditIndex = null;   // normal review flow (not a cookbook edit) unless set after
      // updateMode is set separately by the caller (restandardize sets it; normal extract clears it)
      this.recipe = {
        name: r.name || '', description: r.description || '', servings: r.servings,
        yield: r.yield || '', image_url: r.image_url || '', tags: r.tags || [],
        categories: r.categories || [], source_url: r.source_url || '',
        notes: (r.notes || []).map(n => ({ title: n.title || '', text: n.text || '' })),
        ingredients: (r.ingredients || []).map(i => ({ quantity: i.quantity ?? '', unit: i.unit ?? '', food: i.food ?? '', note: i.note ?? '', title: i.title ?? '' })),
      };
      this.instructionsText = (r.instructions || []).join('\n');
      this.categoryInput = '';
      this.tagInput = '';
      this.saveSession();
    },
    addIngredient() { this.recipe.ingredients.push({ quantity: '', unit: '', food: '', note: '', title: '' }); },
    moveIngredient(from, to) {
      if (from === to || from == null || to == null) return;
      const items = [...this.recipe.ingredients];
      const [moved] = items.splice(from, 1);
      items.splice(to, 0, moved);
      this.recipe.ingredients = items;
      this.saveSession();
    },
    addNote() { (this.recipe.notes ||= []).push({ title: '', text: '' }); },
    removeNote(i) { this.recipe.notes.splice(i, 1); },
    addCategory(name) {
      const v = (name == null ? this.categoryInput : name).trim();
      this.categoryInput = '';
      if (v && !this.recipe.categories.some(c => c.toLowerCase() === v.toLowerCase())) this.recipe.categories.push(v);
    },
    removeCategory(i) { this.recipe.categories.splice(i, 1); },
    addTag(name) {
      const v = (name == null ? this.tagInput : name).trim();
      this.tagInput = '';
      if (v && !(this.recipe.tags ||= []).some(t => t.toLowerCase() === v.toLowerCase())) this.recipe.tags.push(v);
    },
    removeTag(i) { (this.recipe.tags ||= []).splice(i, 1); },
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
    filteredFoods(q) {
      const query = (q || '').trim().toLowerCase();
      if (!query) {
        return this.foods.slice(0, 10).map(f => ({ name: f, status: 'exists' }));
      }
      return this.foods
        .filter(f => f.toLowerCase().includes(query))
        .slice(0, 10)
        .map(f => ({ name: f, status: 'exists' }));
    },
    filteredUnits(q) {
      const query = (q || '').trim().toLowerCase();
      if (!query) return this.commonUnits.slice(0, 10);
      return this.commonUnits.filter(u => u.toLowerCase().includes(query)).slice(0, 8);
    },
    filteredCategories(q) {
      const query = (q || '').trim().toLowerCase();
      if (!query) return [];
      return this.categories.filter(c => c.toLowerCase().includes(query)).slice(0, 8);
    },
    filteredTags(q) {
      const query = (q || '').trim().toLowerCase();
      if (!query) return [];
      return this.tags.filter(t => t.toLowerCase().includes(query)).slice(0, 8);
    },
    openFoodPicker(ing = null) {
      this.foodPickerTargetIng = ing;
      this.foodPickerSearch = ing ? (ing.food || '') : '';
      this.foodPickerModal = true;
    },
    selectPickerFood(foodName) {
      if (this.foodPickerTargetIng) {
        this.foodPickerTargetIng.food = foodName;
      } else {
        this.recipe.ingredients.push({ quantity: '', unit: '', food: foodName, note: '', title: '' });
      }
      this.foodPickerModal = false;
      this.foodPickerTargetIng = null;
      this.saveSession();
    },
    filteredPickerFoods() {
      const q = (this.foodPickerSearch || '').trim().toLowerCase();
      if (!q) return this.foods;
      return this.foods.filter(f => f.toLowerCase().includes(q));
    },
    alreadyImported() {
      // non-blocking dedupe: does the entered URL match something already imported?
      const norm = s => (s || '').trim().replace(/\/+$/, '').toLowerCase();
      const u = norm(this.url);
      if (!u) return null;
      // only a successful push counts as "already imported" — discards don't
      return this.history.find(h => h.status !== 'discarded' && norm(h.source_url) === u) || null;
    },
    importedNote() {
      const h = this.alreadyImported();
      if (!h) return '';
      const d = (h.created_at || '').slice(0, 10);
      return '⚠ Already imported' + (d ? ' on ' + d : '') + (h.name ? ' → ' + h.name : '')
        + '. Importing again makes a separate copy.';
    },
    histHost(u) { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return u; } },
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
        if (!Array.isArray(this.recipe.notes)) this.recipe.notes = [];   // older sessions predate notes
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
      if (this.updateMode) { return this.pushUpdate(); }   // B9: update existing recipe
      if (this.nameExists() && !this._dupOk) { this.dupModal = true; return; }  // confirm duplicates
      this._dupOk = false;
      this.error = ''; this.loadingMsg = 'Saving to Mealie…'; this.loading = true;
      try {
        const body = {
          name: (this.recipe.name || '').trim(), description: this.recipe.description || '',
          servings: numOrNull(this.recipe.servings), yield: this.recipe.yield || '',
          image_url: this.photoFile ? null : (this.recipe.image_url || null),  // picked file wins
          ingredients: this.recipe.ingredients.filter(i => blank(i.food) || blank(i.note))
            .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note), title: blank(i.title) })),
          instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean), tags: [],
          categories: this.recipe.categories || [], source_url: this.recipe.source_url || '',
          notes: (this.recipe.notes || []).filter(n => (n.text || '').trim() || (n.title || '').trim())
            .map(n => ({ title: (n.title || '').trim(), text: (n.text || '').trim() })),
        };
        if (!body.name) throw new Error('Give the recipe a name first.');
        const r = await fetch('/api/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const out = await r.json(); this.lastName = body.name; this.lastUrl = out.url;
        this.history.unshift({ name: body.name, slug: out.slug, source_url: body.source_url || '',
          mealie_url: out.url, status: 'success', created_at: new Date().toISOString() });
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
    async discard() {
      await this.stashDiscard();   // keep a restorable copy in case it was a misclick
      if (this.queue.length) { this.showToast('Discarded — next recipe (' + this.queue.length + ' left)'); this.error = ''; this.loadRecipe(this.queue.shift()); }
      else { this.reset(); }
    },
    hasSources() {
      // Are the original inputs still in scope to re-run extraction? (After a page
      // reload they're gone — File objects can't be persisted — so the button hides.)
      return !!(this.fileList?.length || this.url.trim() || this.pastedText.trim() || this.audioBlob);
    },
    reExtract() {
      // Back to the input screen WITHOUT clearing the sources (fileList/url/text/audio/
      // prompt), so the user can tweak the prompt and re-run — no re-uploading. Drops
      // only the review-only image previews. Only shown when hasSources() && normal flow.
      this.error = '';
      this.clearSourceImages();
      this.dupModal = false;
      this.showToast('Sources kept — tweak the prompt, then Make recipe');
      this.view = 'input';
    },
    async stashDiscard() {
      const r = this.recipe;
      if (!r || (!(r.name || '').trim() && !(r.ingredients || []).length)) return;  // nothing worth keeping
      const payload = {
        name: r.name || '', description: r.description || '', servings: r.servings,
        yield: r.yield || '', image_url: r.image_url || '', tags: [], categories: r.categories || [],
        source_url: r.source_url || '', notes: r.notes || [], ingredients: r.ingredients || [],
        instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean),
      };
      try { await api('/api/history/discard', { method: 'POST', body: JSON.stringify(payload) }); } catch (_) {}
    },
    async restoreImport(h) {
      this.error = ''; this.loadingMsg = 'Restoring…'; this.loading = true;
      try {
        const row = await getJSON('/api/history/' + h.id);
        if (!row.payload) throw new Error('Nothing to restore for this entry.');
        this.queue = []; this.loadRecipe(row.payload); this.view = 'review';
        this.showToast('Restored — review and push when ready');
      } catch (e) { this.error = String(e.message || e); this.showToast('Could not restore'); }
      finally { this.loading = false; }
    },
    reset() {
      this.fileList = null; this.url = ''; this.pastedText = ''; this.prompt = ''; this.error = '';
      this.clearPhoto(); this.clearAudio(); this.clearSourceImages(); this.zoomSrc = ''; this.dupModal = false; this.clearSession();
      this.recipe = emptyRecipe(); this.instructionsText = ''; this.queue = []; this.updateMode = null; this.view = 'input';
    },
    showToast(m) { this.toast = m; clearTimeout(this._t); this._t = setTimeout(() => this.toast = '', 2600); },

    // ── B9: fix existing recipe (re-standardize) ────────────────────────
    async restandardize(slug) {
      this.error = ''; this.loadingMsg = 'Re-standardizing…'; this.loading = true;
      try {
        const r = await api('/api/restandardize', { method: 'POST', body: JSON.stringify({ slug, language: this.language, units_system: this.unitsSystem }) });
        if (!r.ok) throw new Error(await detail(r));
        const data = await r.json();
        this.updateMode = data.slug;   // signals review to use the update endpoint
        this.loadRecipe(data.recipe);
        this.view = 'review';
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    async pushUpdate() {
      const slug = this.updateMode;
      if (!slug) return;
      this.error = ''; this.loadingMsg = 'Saving changes…'; this.loading = true;
      try {
        const body = {
          name: (this.recipe.name || '').trim(), description: this.recipe.description || '',
          servings: numOrNull(this.recipe.servings), yield: this.recipe.yield || '',
          image_url: null,   // don't touch the existing image via the update PATCH
          ingredients: this.recipe.ingredients.filter(i => blank(i.food) || blank(i.note))
            .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note), title: blank(i.title) })),
          instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean), tags: [],
          categories: this.recipe.categories || [], source_url: this.recipe.source_url || '',
          notes: (this.recipe.notes || []).filter(n => (n.text || '').trim() || (n.title || '').trim())
            .map(n => ({ title: (n.title || '').trim(), text: (n.text || '').trim() })),
        };
        if (!body.name) throw new Error('Give the recipe a name first.');
        const r = await fetch('/api/recipes/' + slug + '/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const out = await r.json(); this.lastName = body.name; this.lastUrl = out.url;
        this.history.unshift({ name: body.name, slug: out.slug, source_url: body.source_url || '',
          mealie_url: out.url, status: 'updated', created_at: new Date().toISOString() });
        if (this.photoFile) {
          try {
            const fd = new FormData(); fd.append('file', this.photoFile);
            const ir = await fetch('/api/recipe-image/' + out.slug, { method: 'PUT', body: fd, credentials: 'same-origin' });
            if (!ir.ok) this.showToast('Recipe updated — but the photo upload failed');
          } catch (_) { this.showToast('Recipe updated — but the photo upload failed'); }
        }
        this.updateMode = null; this.clearSession(); this.view = 'done';
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },

    // ── Cookbook bulk import (B7, dev) ──────────────────────────────────
    async pickCookbook(e) {
      const f = e.target.files && e.target.files[0];
      e.target.value = '';                 // allow re-picking the same file
      if (!f) return;
      this.error = ''; this.loadingMsg = 'Reading cookbook…'; this.loading = true;
      try {
        const fd = new FormData(); fd.append('file', f);
        const r = await fetch('/api/cookbook/split', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (!r.ok) throw new Error(await detail(r));
        const recipes = (await r.json()).recipes || [];
        if (!recipes.length) throw new Error('No recipes found in that PDF.');
        this.cbRecipes = recipes.map(x => ({ ...x, sel: !this.cbImported(x.title) }));  // skip dupes by default
        this.view = 'cookbook';
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    cbImported(title) {
      const n = (title || '').trim().toLowerCase();
      return !!n && this.history.some(h => h.status !== 'discarded' && (h.name || '').trim().toLowerCase() === n);
    },
    cbSelCount() { return this.cbRecipes.filter(r => r.sel).length; },
    cbToggleAll(v) { this.cbRecipes.forEach(r => r.sel = v); },
    async cbStructure() {
      // Phase B: hand the selected chunks to a server-side background job, then poll —
      // so a long run survives closing the tab.
      const chosen = this.cbRecipes.filter(r => r.sel);
      if (!chosen.length) { this.error = 'Select at least one recipe.'; return; }
      this.error = ''; this.loading = true; this.loadingMsg = 'Starting…';
      try {
        const body = { recipes: chosen.map(r => ({ text: r.text, image: r.image, title: r.title })), language: this.language, units_system: this.unitsSystem };
        const r = await api('/api/cookbook/job', { method: 'POST', body: JSON.stringify(body) });
        if (!r.ok) throw new Error(await detail(r));
        const { job_id } = await r.json();
        this.cookbookJob = { job_id, status: 'running', done: 0, total: chosen.length, failed: 0 };
        try { localStorage.setItem('mm-cookbook-job', job_id); } catch (_) {}
        this.view = 'cookbook-progress';
        this.cbPoll();
      } catch (e) { this.error = String(e.message || e); }
      finally { this.loading = false; }
    },
    async cbPoll() {
      clearTimeout(this._cbTimer);
      const jid = this.cookbookJob && this.cookbookJob.job_id;
      if (!jid) return;
      try {
        const job = await getJSON('/api/cookbook/job/' + jid);
        this.cookbookJob = { job_id: jid, status: job.status, done: job.done, total: job.total, failed: job.failed };
        if (job.status === 'done') {
          if (this.view === 'cookbook-progress') this.cbLoadJob(job);
          else this.showToast('Cookbook ready — ' + (job.total - job.failed) + ' to review');
          return;   // stop polling
        }
      } catch (_) {}
      this._cbTimer = setTimeout(() => this.cbPoll(), 2000);
    },
    cbLoadJob(job) {
      this.cbReviewJobId = job.id || '';
      this.cbStructured = (job.recipes || []).map((x, i) => ({ recipe: x.recipe, image: x.image, sel: true, idx: i }));
      this.cbExpanded = null; this.cbClearJob(); this.view = 'cookbook-review';
      this.cbSaveReview();
    },
    cbClearJob() { this.cookbookJob = null; try { localStorage.removeItem('mm-cookbook-job'); } catch (_) {} },
    async cbCancel() {
      const jid = this.cookbookJob && this.cookbookJob.job_id;
      if (jid) { try { await api('/api/cookbook/job/' + jid + '/cancel', { method: 'POST', body: '{}' }); } catch (_) {} }
      clearTimeout(this._cbTimer); this.cbClearJob();
      this.showToast('Stopped'); this.view = 'cookbook-upload';
    },
    // ── bulk-review extras: dupe + near-match badges, refresh-persistence ──
    cbNameExists(name) {
      const n = (name || '').trim().toLowerCase();
      return !!n && this.recipeNames.some(x => x.toLowerCase() === n);
    },
    cbNearFoods(recipe) {
      return (recipe.ingredients || []).filter(i => this.foodStatus(i.food) === 'near').length;
    },
    cbSaveReview() {
      try {
        if (!this.cbStructured.length) { localStorage.removeItem('mm-cookbook-review'); return; }
        localStorage.setItem('mm-cookbook-review', JSON.stringify({
          job_id: this.cbReviewJobId || '',
          items: this.cbStructured.map(s => ({ recipe: s.recipe, sel: s.sel, idx: s.idx })),
        }));
      } catch (_) {}
    },
    async cbRestoreReview() {
      let saved; try { saved = JSON.parse(localStorage.getItem('mm-cookbook-review') || 'null'); } catch (_) {}
      if (!saved || !(saved.items || []).length) return false;
      this.cbReviewJobId = saved.job_id || '';
      this.cbStructured = saved.items.map(it => ({ recipe: it.recipe, image: null, sel: it.sel !== false, idx: it.idx }));
      if (saved.job_id) {   // re-hydrate photos from the server job (stored without images)
        try {
          const recs = (await getJSON('/api/cookbook/job/' + saved.job_id)).recipes || [];
          this.cbStructured.forEach(s => { if (s.idx != null && recs[s.idx]) s.image = recs[s.idx].image; });
        } catch (_) {}
      }
      this.cbExpanded = null; this.view = 'cookbook-review';
      return true;
    },
    cbClearReview() { this.cbStructured = []; this.cbReviewJobId = ''; try { localStorage.removeItem('mm-cookbook-review'); } catch (_) {} },
    cbDiscardReview() { this.cbClearReview(); this.cbExpanded = null; this.error = ''; this.view = 'input'; },
    async cbResume() {
      let jid; try { jid = localStorage.getItem('mm-cookbook-job'); } catch (_) {}
      if (!jid) return;
      try {
        const job = await getJSON('/api/cookbook/job/' + jid);
        this.cookbookJob = { job_id: jid, status: job.status, done: job.done, total: job.total, failed: job.failed };
        if (job.status !== 'done') this.cbPoll();   // keep polling in the background; banner shows status
      } catch (_) { this.cbClearJob(); }
    },
    async openCookbookJob() {
      this.menuOpen = false;
      const jid = this.cookbookJob && this.cookbookJob.job_id;
      if (!jid) return;
      try {
        const job = await getJSON('/api/cookbook/job/' + jid);
        if (job.status === 'done') this.cbLoadJob(job);
        else { this.view = 'cookbook-progress'; this.cbPoll(); }
      } catch (_) { this.cbClearJob(); }
    },
    cbRemove(i) { if (this.cbExpanded === i) this.cbExpanded = null; this.cbStructured.splice(i, 1); this.cbSaveReview(); },
    cbToggleRow(i) { this.cbExpanded = this.cbExpanded === i ? null : i; },
    cbEditOne(i) {
      this.loadRecipe(this.cbStructured[i].recipe);   // full edit in the normal review screen
      this.cbEditIndex = i;
      this.photoPreview = this.cbStructured[i].image || '';   // show the cookbook photo (display only)
      this.error = ''; this.view = 'review';
    },
    cbSaveEdit() {
      if (this.cbEditIndex === null) return;
      const r = this.recipe;
      this.cbStructured[this.cbEditIndex].recipe = {
        name: (r.name || '').trim(), description: r.description || '', servings: numOrNull(r.servings),
        yield: r.yield || '', image_url: null, tags: [], categories: r.categories || [], source_url: r.source_url || '',
        notes: (r.notes || []).filter(n => (n.text || '').trim() || (n.title || '').trim())
          .map(n => ({ title: (n.title || '').trim(), text: (n.text || '').trim() })),
        ingredients: r.ingredients.filter(i => blank(i.food) || blank(i.note))
          .map(i => ({ quantity: parseQty(i.quantity), unit: blank(i.unit), food: blank(i.food), note: blank(i.note), title: blank(i.title) })),
        instructions: this.instructionsText.split('\n').map(s => s.trim()).filter(Boolean),
      };
      this.cbEditIndex = null; this.clearPhoto(); this.error = ''; this.view = 'cookbook-review';
      this.cbSaveReview();
    },
    cbCancelEdit() { this.cbEditIndex = null; this.clearPhoto(); this.error = ''; this.view = 'cookbook-review'; },
    cbPushCount() { return this.cbStructured.filter(s => s.sel).length; },
    async cbPushAll() {
      const chosen = this.cbStructured.filter(s => s.sel);
      if (!chosen.length) { this.error = 'Select at least one to push.'; return; }
      this.error = ''; this.loading = true;
      let ok = 0, fail = 0;
      try {
        for (let i = 0; i < chosen.length; i++) {
          this.loadingMsg = 'Pushing ' + (i + 1) + ' / ' + chosen.length + '…';
          try {
            const r = await fetch('/api/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(chosen[i].recipe), credentials: 'same-origin' });
            if (!r.ok) { fail++; continue; }
            const out = await r.json();
            if (chosen[i].image) { try { await this.uploadDataUrl(out.slug, chosen[i].image); } catch (_) {} }
            ok++;
          } catch (_) { fail++; }
          await new Promise(res => setTimeout(res, 400));
        }
        try { this.history = (await getJSON('/api/history')).items || []; } catch (_) {}
        this.cbRecipes = []; this.cbClearReview(); this.view = 'input';
        this.showToast('Pushed ' + ok + ' recipe(s)' + (fail ? ', ' + fail + ' failed' : ''));
      } finally { this.loading = false; }
    },
    async uploadDataUrl(slug, dataUrl) {
      const blob = await (await fetch(dataUrl)).blob();
      const fd = new FormData(); fd.append('file', blob, 'photo.jpg');
      const r = await fetch('/api/recipe-image/' + slug, { method: 'PUT', body: fd, credentials: 'same-origin' });
      if (!r.ok) throw new Error('image upload failed');
    },
  };
}

// ── helpers ────────────────────────────────────────────────────────────
function emptyRecipe() { return { name: '', description: '', servings: null, yield: '', image_url: '', tags: [], categories: [], notes: [], source_url: '', ingredients: [] }; }
function emptyCfg() { return { mealie_url: '', mealie_token: '', ai_key: '', ai_base: '', ai_model: '', ai_rpm: '', ai_rules: '', auth_user: '', auth_pass: '', api_key: '' }; }
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
