// Service worker: keeps a WebSocket to the local grok-web-mcp server, receives jobs, runs them in a
// grok.com/imagine tab through the content scripts, and sends the results back (base64).
// The server recognises this extension by its chrome-extension:// Origin, so there is nothing to paste.
const DEFAULT_PORT = 8799;
const IMAGINE = 'https://grok.com/imagine';
const VERSION = chrome.runtime.getManifest().version;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let ws = null, state = 'disconnected', lastError = '', lastJob = '', backoff = 1000, retryTimer = null, pingTimer = null;

function setState(s, err) {
  state = s;
  if (err !== undefined) lastError = err;
  chrome.action.setBadgeText({ text: { connected: 'on', running: 'job' }[s] || 'off' });
  chrome.action.setBadgeBackgroundColor({ color: s === 'connected' || s === 'running' ? '#1a7f37' : '#b42318' });
}

function retry(delay) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, delay || backoff);
  backoff = Math.min(backoff * 2, 30000);
}

async function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return; // CONNECTING or OPEN
  clearTimeout(retryTimer);
  const { port = DEFAULT_PORT } = await chrome.storage.local.get('port');
  setState('connecting');
  const sock = new WebSocket(`ws://127.0.0.1:${port}`);
  ws = sock;
  sock.onopen = () => sock.send(JSON.stringify({ type: 'hello', role: 'extension', version: VERSION }));
  sock.onmessage = ev => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'welcome') {
      backoff = 1000; setState('connected', '');
      clearInterval(pingTimer); // app-level traffic keeps the MV3 worker alive (Chrome 116+)
      pingTimer = setInterval(() => sock.readyState === WebSocket.OPEN && sock.send('{"type":"ping"}'), 20000);
    } else if (msg.type === 'job') handleJob(sock, msg);
    else if (msg.type === 'error') lastError = msg.error;
  };
  sock.onclose = ev => {
    if (ws !== sock) return; // an older socket we already replaced
    clearInterval(pingTimer);
    ws = null;
    if (ev.code === 4004) { setState('not allowed', lastError || 'the server pinned another extension id; see extensions.json in ~/.grok-web-mcp'); retry(60000); }
    else { setState('disconnected', ev.reason || 'server not running: start your MCP client (Claude, Cursor...) with grok-web-mcp configured'); retry(); }
  };
}

// ------------------------------------------------------------------ jobs
async function handleJob(sock, job) {
  let res;
  try { res = { ok: true, data: await runJob(job) }; }
  catch (e) { res = { ok: false, error: String((e && e.message) || e), submitted: !!(e && e.submitted) }; }
  if (sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify({ type: 'result', id: job.id, ...res }));
}

async function runJob(job) {
  if (job.kind === 'status') {
    const tab = await findTab();
    if (!tab) return { tab: null, note: 'no grok.com/imagine tab open (one is opened on the first job)' };
    let page;
    try { page = await ask(tab.id, 'status', {}, 5000); } catch (e) { page = { error: e.message }; }
    return { tab: { id: tab.id, url: tab.url }, page };
  }
  if (job.kind !== 'image' && job.kind !== 'video') throw new Error('unknown job kind ' + job.kind);
  lastJob = `${job.kind}: ${String(job.params.prompt || '').slice(0, 60)}`;
  setState('running');
  try {
    const tab = await prepareTab();
    return await ask(tab.id, job.kind, { ...job.params, timeout_s: job.timeout_s }, (job.timeout_s + 60) * 1000);
  } finally {
    setState(ws ? 'connected' : 'disconnected');
  }
}

// one message to the tab's content script; resolves with its data or throws its error
async function ask(tabId, op, args, ms) {
  let t;
  const timeout = new Promise((_, rej) => { t = setTimeout(() => rej(new Error(`timeout: tab did not answer '${op}' in ${ms / 1000}s`)), ms); });
  try {
    const res = await Promise.race([chrome.tabs.sendMessage(tabId, { type: 'grokBridge', op, args }), timeout]);
    if (!res) throw new Error('the grok tab closed or reloaded during the job');
    if (!res.ok) throw Object.assign(new Error(res.error), { submitted: !!res.submitted }); // server counts quota
    return res.data;
  } finally { clearTimeout(t); }
}

async function findTab() {
  const tabs = await chrome.tabs.query({ url: 'https://grok.com/imagine*' });
  return tabs.find(t => new URL(t.url).pathname === '/imagine') || tabs[0] || null;
}

async function prepareTab() {
  let tab = await findTab();
  if (!tab) tab = await chrome.tabs.create({ url: IMAGINE, active: false });
  else if (new URL(tab.url || IMAGINE).pathname !== '/imagine') tab = await chrome.tabs.update(tab.id, { url: IMAGINE });
  // hidden tabs get throttled timers and undecoded images: bring the tab to front unless the user turned it off
  const { activate = true } = await chrome.storage.local.get('activate');
  if (activate) await chrome.tabs.update(tab.id, { active: true });
  for (let i = 0; i < 40; i++) { // page loaded + content scripts answering
    try {
      const t = await chrome.tabs.get(tab.id);
      if (t.status === 'complete') {
        const s = await ask(tab.id, 'status', {}, 3000);
        if (s.editor || !s.loggedIn) return tab;
      }
    } catch (e) {
      if (/No tab with id/.test(e.message)) throw new Error('the grok tab was closed');
      // a tab opened before the extension was (re)loaded has no content script: reload it once
      if (/Receiving end does not exist/.test(e.message) && i === 3) await chrome.tabs.reload(tab.id);
    }
    await sleep(1000);
  }
  return tab; // the content script reports the precise problem (not logged in / no prompt box)
}

// ------------------------------------------------------------------ popup + lifetime
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.type === 'setPort') {
      const port = parseInt(msg.port, 10);
      if (port >= 1024 && port <= 65535) await chrome.storage.local.set({ port });
      if (ws) ws.close(); ws = null; backoff = 1000; await connect();
    } else if (msg.type === 'setActivate') {
      await chrome.storage.local.set({ activate: !!msg.value });
    } else if (msg.type === 'reconnect') {
      backoff = 1000; await connect();
    }
    const { port = DEFAULT_PORT, activate = true } = await chrome.storage.local.get(['port', 'activate']);
    sendResponse({ state, lastError, lastJob, port, activate, version: VERSION, id: chrome.runtime.id });
  })();
  return true;
});

chrome.alarms.create('grokWebMcpKeepAlive', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'grokWebMcpKeepAlive') connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
