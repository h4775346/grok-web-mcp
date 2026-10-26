const el = id => document.getElementById(id);
let portTouched = false;

// service-worker state -> [card class, title]
const VIEW = {
  connected: ['ok', 'Connected'],
  running: ['busy', 'Generating…'],
  connecting: ['busy', 'Connecting…'],
  disconnected: ['bad', 'Not connected'],
  'not allowed': ['bad', 'Not allowed'],
};

function show(s) {
  if (!s) return;
  // a reconnect attempt after a failure is still "not connected" for the user
  const retrying = s.state === 'connecting' && s.lastError;
  const [cls, title] = retrying ? VIEW.disconnected : VIEW[s.state] || ['bad', s.state];
  el('status').className = 'status ' + cls;
  el('title').textContent = title;
  const addr = document.createElement('code');
  addr.textContent = '127.0.0.1:' + s.port;
  el('sub').replaceChildren(cls === 'bad' ? 'Retrying ' : 'Bridge on ', addr, cls === 'bad' ? ' every few seconds' : '');
  el('job').textContent = s.lastJob ? (s.state === 'running' ? 'Now · ' : 'Last · ') + s.lastJob : '';
  el('err').textContent = cls === 'bad' ? s.lastError || '' : '';
  el('activate').checked = s.activate;
  if (!portTouched) el('port').value = s.port;
  el('meta').textContent = 'id ' + s.id;
  el('meta').title = s.id;
  el('ver').textContent = 'v' + s.version;
}

const send = msg => chrome.runtime.sendMessage(msg).then(show);

el('port').oninput = () => { portTouched = true; };
el('port').onkeydown = e => { if (e.key === 'Enter') el('save').click(); };
el('save').onclick = () => { portTouched = false; send({ type: 'setPort', port: el('port').value }); };
el('reconnect').onclick = () => send({ type: 'reconnect' });
el('activate').onchange = e => send({ type: 'setActivate', value: e.target.checked });

send({ type: 'getState' });
setInterval(() => send({ type: 'getState' }), 1500);
