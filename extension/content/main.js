// Grok Imagine automation (runs in the page world on grok.com). Ops arrive from content/relay.js as window messages:
//   status -> {path, editor, loggedIn, visible}
//   image  {prompt, aspect, n (1..2 to keep), mode ('speed'|'quality'|''), timeout_s}
//          -> {files:[{mime,b64}], submitted, generated, kept, aspectOk, mode, secs, warning?, modeWarning?}
//   video  {prompt, aspect?, image_b64?, image_mime?, image_name?, timeout_s} -> {files:[{mime:'video/mp4',b64}], submitted, secs}  (EXPERIMENTAL)
// Every submission uses the user's Grok quota: one submission per job, the page is never scrolled (scrolling the
// feed can make Grok generate more), and errors after the submit carry submitted:true so the server counts them.
(() => {
  if (window.__grokBridgeMain) return;
  window.__grokBridgeMain = true;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const $ = s => document.querySelector(s);
  const editor = () => $('.tiptap.ProseMirror');
  const buttons = () => [...document.querySelectorAll('button')];

  // Radix menus need the full pointer sequence; a plain .click() is ignored in background tabs.
  function pc(el) {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, button: 0, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1, pointerType: 'mouse' };
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
      el.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, o));
  }

  // Result images are data: URLs. Hidden tabs don't decode images (naturalWidth 0), so also accept big src strings.
  const dataImgs = () => [...new Set([...document.querySelectorAll('img')]
    .map(i => [i, i.currentSrc || i.src || ''])
    .filter(([i, s]) => s.startsWith('data:image') && (i.naturalWidth > 300 || s.length > 60000))
    .map(([, s]) => s))];

  const loggedOut = () => [...document.querySelectorAll('a,button')]
    .some(e => /^(sign in|log in|sign up)$/i.test((e.innerText || '').trim()));

  function skipSurvey() { // "Which image do you prefer to keep?" hides the prompt box
    const b = buttons().find(b => b.innerText.trim() === 'Skip');
    if (b) { b.click(); return true; }
    return false;
  }

  async function waitEditor() {
    for (let i = 0; i < 20; i++) {
      skipSurvey();
      if (editor()) return editor();
      await sleep(1000);
    }
    if (loggedOut()) throw new Error('not_logged_in: grok.com shows Sign in - log in to grok.com in this Chrome profile');
    throw new Error('no prompt box on grok.com/imagine (page not loaded or Grok UI changed)');
  }

  async function setMode(label) { // 'Image' | 'Video' toggle on /imagine (shared by all grok tabs)
    const b = $(`button[aria-label="${label}"]`);
    if (b && b.getAttribute('aria-checked') === 'false') { pc(b); await sleep(900); }
    return !!b;
  }

  async function setAspect(aspect) {
    const btn = $('button[aria-label="Aspect Ratio"]');
    if (!btn || btn.innerText.trim() === aspect) return !!btn;
    pc(btn); await sleep(700);
    // menu items read "16:9 Widescreen", "1:1 Square", "9:16 Story" ...
    const opt = [...document.querySelectorAll('[role="menuitemradio"],[role="menuitem"],[role="option"]')]
      .find(e => e.innerText && e.innerText.trim().split(/\s+/)[0] === aspect);
    if (!opt) { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return false; }
    pc(opt); await sleep(500);
    return ($('button[aria-label="Aspect Ratio"]')?.innerText || '').trim() === aspect;
  }

  // "Image Count" menu: Auto | ×2 | ×4 | ×8 | ×12. "Auto" keeps generating, so every job forces the minimum ×2.
  async function setImageCount(want = '×2') {
    const btn = $('button[aria-label="Image Count"]');
    if (!btn) return true;                       // control not on this page version: one submission = one batch
    if (btn.innerText.trim() === want) return true;
    pc(btn); await sleep(700);
    const opt = [...document.querySelectorAll('[role="menuitemradio"]')].find(e => e.innerText.trim() === want);
    if (!opt) { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return false; }
    pc(opt); await sleep(500);
    return ($('button[aria-label="Image Count"]')?.innerText || '').trim() === want;
  }

  // 'Speed' / 'Quality 2.0' control next to the prompt box: segmented buttons or one dropdown button.
  async function setQuality(mode) {
    if (!mode) return { ok: true, mode: 'unchanged' };
    const want = mode === 'quality' ? /^quality/i : /^speed/i;
    const label = e => ((e.innerText || '').trim() || e.getAttribute('aria-label') || '').trim();
    const ctls = buttons().filter(b => /^(speed|quality)/i.test(label(b)));
    const miss = { ok: false, mode: 'unchanged', warning: `speed/quality control not found; '${mode}' not applied (page default used)` };
    if (!ctls.length) return miss;
    const direct = ctls.find(b => want.test(label(b)));
    if (direct && ctls.length > 1) { pc(direct); await sleep(500); return { ok: true, mode }; } // segmented
    if (direct) return { ok: true, mode };                                                     // dropdown already on it
    pc(ctls[0]); await sleep(700);
    const opt = [...document.querySelectorAll('[role="menuitemradio"],[role="menuitem"],[role="option"]')]
      .find(e => want.test((e.innerText || '').trim()));
    if (!opt) { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return miss; }
    pc(opt); await sleep(500);
    return { ok: true, mode };
  }

  const afterSubmit = e => { const err = e instanceof Error ? e : new Error(String(e)); err.submitted = true; return err; };

  async function setPrompt(text) {
    const ed = editor();
    if (ed.editor && ed.editor.commands) { // tiptap API: works in hidden tabs too
      ed.editor.commands.setContent(text); ed.editor.commands.focus('end', { scrollIntoView: false });
    } else {
      ed.focus({ preventScroll: true }); document.execCommand('selectAll', false, null); document.execCommand('insertText', false, text);
    }
    await sleep(500);
  }

  const pressEnter = () => editor()?.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));

  const toB64 = blob => new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',')[1]); fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(blob);
  });
  const dataUrlFile = s => ({ mime: s.slice(5, s.indexOf(';')), b64: s.slice(s.indexOf(',') + 1) });

  async function genImage({ prompt, aspect = '16:9', n = 1, mode = '', timeout_s = 180 }) {
    await waitEditor();
    await setMode('Image');
    const before = new Set(dataImgs());
    const aspectOk = await setAspect(aspect);
    if (!(await setImageCount('×2'))) throw new Error('could not set Image Count to ×2 (Auto would over-generate); nothing submitted');
    const q = await setQuality(mode);
    await setPrompt(prompt);
    pressEnter(); // the one and only submission of this job
    try {
      // Grok sharpens images progressively (~100 KB previews first): wait until the first `need` new ones stop
      // changing, then stop at once. Images that appear later (more results, feed loading) are ignored.
      const t0 = Date.now(), need = Math.max(1, Math.min(n, 2));
      let fresh = [], last = '', stable = 0, done = false;
      while (Date.now() - t0 < timeout_s * 1000) {
        await sleep(3000);
        skipSurvey();
        fresh = dataImgs().filter(s => !before.has(s));
        const head = fresh.slice(0, need), sig = head.map(s => s.length).join(',');
        if (head.length >= need && sig === last && head.every(s => s.length > 250000)) { if (++stable >= 2) { done = true; break; } }
        else stable = 0;
        last = sig;
      }
      if (!fresh.length) throw new Error(`timeout: no images appeared after ${timeout_s}s (moderated prompt, rate limit or UI change?)`);
      const files = fresh.slice(0, need).map(dataUrlFile);
      const out = { files, submitted: true, generated: Math.max(fresh.length, files.length), kept: files.length, aspectOk,
                    mode: q.mode, secs: Math.round((Date.now() - t0) / 1000) };
      if (!done) out.warning = 'timed out before the images were final; they may be low-res previews';
      if (q.warning) out.modeWarning = q.warning;
      return out;
    } catch (e) { throw afterSubmit(e); }
  }

  // ---- EXPERIMENTAL video: Video mode, optional image attach, prompt, submit, then poll for the mp4.
  const videoSrcs = () => [...new Set([...document.querySelectorAll('video, video source')]
    .map(v => v.currentSrc || v.src || '').filter(s => /^https?:/.test(s)))];
  const assetPrefix = () => { // https://assets.grok.com/users/<uid>/generated/  (taken from any asset on the page)
    for (const e of document.querySelectorAll('img,video,source')) {
      const m = (e.currentSrc || e.src || '').match(/^https:\/\/assets\.grok\.com\/users\/[^/]+\/generated\//);
      if (m) return m[0];
    }
    return null;
  };
  const postId = () => (location.pathname.match(/\/imagine\/post\/([^/?#]+)/) || [])[1];

  async function genVideo({ prompt = '', image_b64, image_mime = 'image/jpeg', image_name = 'image.jpg', aspect = '16:9', timeout_s = 420 }) {
    await waitEditor();
    await setMode('Video');
    await setAspect(aspect);
    const before = new Set(videoSrcs()), startPost = postId();
    if (image_b64) {
      for (const rm of document.querySelectorAll('button[aria-label="Remove image"]')) { rm.click(); await sleep(300); }
      const bytes = Uint8Array.from(atob(image_b64), c => c.charCodeAt(0));
      // the page has several file inputs (Text Edit / Precise Edit panels); the composer's one sits next to "Upload"
      const up = $('button[aria-label="Upload"]');
      const inp = (up && [...(up.parentElement?.querySelectorAll('input[type=file]') || [])][0])
        || [...document.querySelectorAll('input[type=file]')].find(i => i.multiple)
        || null;
      if (!inp) throw new Error('no file input on the page (Grok UI changed?)');
      const dt = new DataTransfer(); dt.items.add(new File([bytes], image_name, { type: image_mime }));
      inp.files = dt.files; inp.dispatchEvent(new Event('change', { bubbles: true }));
      let ok = false;
      for (let t = 0; t < 30 && !ok; t++) { await sleep(1000); ok = !!$('button[aria-label="Remove image"]'); }
      if (!ok) throw new Error('image upload was not accepted');
      await sleep(1000);
      await waitEditor();
    }
    if (prompt) await setPrompt(prompt);
    // the composer's visible, enabled Submit (hidden "Make video" buttons elsewhere on the page are ignored)
    let btn = null;
    for (let t = 0; t < 10 && !btn; t++) {
      btn = [...document.querySelectorAll('button[aria-label="Submit"],button[aria-label="Make video"]')]
        .find(b => b.offsetParent && !b.disabled) || null;
      if (!btn) await sleep(500);
    }
    if (!btn) throw new Error('video submit button not found (nothing submitted)');
    pc(btn); // submitted: from here on errors carry submitted:true
    try { return await waitVideo(before, startPost, timeout_s); } catch (e) { throw afterSubmit(e); }
  }

  async function waitVideo(before, startPost, timeout_s) {
    const t0 = Date.now(), seenPosts = [];
    while (Date.now() - t0 < timeout_s * 1000) {
      await sleep(4000);
      skipSurvey();
      const fresh = videoSrcs().filter(s => !before.has(s));
      const id = postId();
      if (id && id !== startPost && !seenPosts.includes(id)) seenPosts.push(id); // temp id first, then the real one
      const pfx = assetPrefix();
      const candidates = [...(pfx ? seenPosts.map(p => `${pfx}${p}/generated_video.mp4`) : []), ...fresh];
      for (const url of candidates) {
        try {
          const r = await fetch(url + (url.includes('?') ? '&' : '?') + 'x=' + Date.now(), { credentials: 'include' });
          if (!r.ok) continue;
          const b = await r.blob();
          if (b.size > 300000) return { files: [{ mime: 'video/mp4', b64: await toB64(b) }], submitted: true, secs: Math.round((Date.now() - t0) / 1000) };
        } catch (_) { /* not ready */ }
      }
    }
    throw new Error(`timeout: no video after ${timeout_s}s (experimental path; Grok UI may have changed)`);
  }

  const ops = {
    status: async () => ({ path: location.pathname, editor: !!editor(), loggedIn: !loggedOut(), visible: document.visibilityState }),
    image: genImage,
    video: genVideo,
  };

  let busy = false;
  window.addEventListener('message', async e => {
    if (e.source !== window || !e.data || e.data.__grokBridge !== 'req') return;
    const { id, op, args } = e.data;
    let payload, mine = false;
    try {
      if (!ops[op]) throw new Error('unknown op ' + op);
      if (op !== 'status') {
        if (busy) throw new Error('this tab is already running a job');
        busy = mine = true;
      }
      payload = { ok: true, data: await ops[op](args) };
    } catch (err) {
      payload = { ok: false, error: String(err && err.message || err), submitted: !!(err && err.submitted) };
    } finally {
      if (mine) busy = false;
    }
    window.postMessage({ __grokBridge: 'res', id, payload }, location.origin);
  });
})();
