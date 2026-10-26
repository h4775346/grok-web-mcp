// Isolated-world relay: chrome.runtime messages from the service worker <-> window messages to content/main.js.
// main.js runs in the page's own world because it needs the page's JS objects (tiptap editor, Next router).
(() => {
  const pending = new Map();
  window.addEventListener('message', e => {
    if (e.source !== window || !e.data || e.data.__grokBridge !== 'res') return;
    const done = pending.get(e.data.id);
    if (done) { pending.delete(e.data.id); done(e.data.payload); }
  });
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type !== 'grokBridge') return;
    const id = crypto.randomUUID();
    pending.set(id, sendResponse);
    window.postMessage({ __grokBridge: 'req', id, op: msg.op, args: msg.args || {} }, location.origin);
    return true; // answer asynchronously
  });
})();
