const el = id => document.getElementById(id);
let portTouched = false;

function show(s) {
  if (!s) return;
  el('state').textContent = s.state;
  el('state').className = s.state === 'connected' || s.state === 'running' ? 'ok' : 'bad';
  el('err').textContent = s.lastError || '';
  el('job').textContent = s.lastJob ? 'Last job: ' + s.lastJob : '';
  el('activate').checked = s.activate;
  if (!portTouched) el('port').value = s.port;
  el('meta').textContent = `v${s.version} · extension id ${s.id}`;
}

const send = msg => chrome.runtime.sendMessage(msg).then(show);

el('port').oninput = () => { portTouched = true; };
el('save').onclick = () => { portTouched = false; send({ type: 'setPort', port: el('port').value }); };
el('reconnect').onclick = () => send({ type: 'reconnect' });
el('activate').onchange = e => send({ type: 'setActivate', value: e.target.checked });

send({ type: 'getState' });
setInterval(() => send({ type: 'getState' }), 1500);
