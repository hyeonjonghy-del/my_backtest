// ZIP bytes live in this app origin's IndexedDB, never in shared server storage.
const fileInput = document.getElementById('file');
const status = document.getElementById('status');
const clearButton = document.getElementById('clear');
let parentOrigin = '*', initialized = false, pending = null, current = null;
let limit = 100 * 1024 * 1024, operation = 0, saving = false, transfer = null;
let chunkBytes = 6 * 1024 * 1024;
function send(type, data = {}) {
  window.parent.postMessage({isStreamlitMessage: true, type, ...data}, parentOrigin);
}
function notify(value) { send('streamlit:setComponentValue', {value, dataType: 'json'}); }
function message(text) { status.textContent = text; send('streamlit:setFrameHeight', {height: 160}); }
function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('comp-browser-bundle-v1', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('files');
    request.onerror = () => reject(new Error('storage'));
    request.onsuccess = () => resolve(request.result);
  });
}
async function stored(mode, value) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('files', mode === 'get' ? 'readonly' : 'readwrite');
      const store = tx.objectStore('files');
      const request = mode === 'get' ? store.get('latest') : mode === 'put' ? store.put(value, 'latest') : store.delete('latest');
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => reject(new Error('storage'));
    });
  } finally { db.close(); }
}
async function dispatch(record, ticket, source) {
  const bytes = new Uint8Array(record.bytes);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('');
  if (ticket !== operation) return;
  record.digest = digest;
  pending = record;
  transfer = {id: `${Date.now()}-${ticket}`, bytes, source, part: 0, total: Math.ceil(bytes.length / chunkBytes)};
  sendPiece();
}
function sendPiece() {
  let binary = '';
  const piece = transfer.bytes.subarray(transfer.part * chunkBytes, (transfer.part + 1) * chunkBytes);
  for (let i = 0; i < piece.length; i += 32768) binary += String.fromCharCode(...piece.subarray(i, i + 32768));
  message(`불러오는 중 · ${pending.name} · ${transfer.part + 1}/${transfer.total}`);
  notify({action: 'chunk', name: pending.name, digest: pending.digest, base64: btoa(binary),
    transfer_id: transfer.id, part: transfer.part, total: transfer.total, source: transfer.source, event_id: transfer.id});
}
async function restore() {
  const ticket = ++operation;
  try {
    const record = await stored('get');
    if (ticket !== operation) return;
    if (!record) { message('ZIP을 선택하면 이 PC의 같은 브라우저에 보관합니다.'); return; }
    if (!(record.bytes instanceof ArrayBuffer) || record.bytes.byteLength > limit) throw new Error('invalid');
    current = record;
    message(`보관 파일 불러오는 중 · ${record.name}`);
    await dispatch(record, ticket, 'cache');
  } catch (_) { message('브라우저 보관을 사용할 수 없습니다. 아래 일회용 업로드를 이용하세요.'); }
}
fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0]; if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip') || file.size === 0 || file.size > limit) {
    message('100MB 이하의 ZIP을 선택하세요. 기존 보관 파일은 유지됩니다.'); return;
  }
  const ticket = ++operation;
  fileInput.disabled = clearButton.disabled = true;
  message(`파일 확인 중 · ${file.name}`);
  try {
    const bytes = await file.arrayBuffer();
    if (ticket === operation) await dispatch({name: file.name, bytes, saved_at: new Date().toISOString()}, ticket, 'picker');
  } catch (_) { message('파일을 읽을 수 없습니다. 아래 일회용 업로드를 이용하세요.'); }
  finally { fileInput.disabled = clearButton.disabled = false; }
});
clearButton.addEventListener('click', async () => {
  ++operation;
  try {
    await stored('delete'); pending = current = transfer = null; fileInput.value = '';
    message('이 브라우저의 보관 파일을 지웠습니다. 원본 ZIP은 삭제하지 않습니다.');
    notify({action: 'clear', id: Date.now()});
  } catch (_) { message('보관 파일을 지우지 못했습니다.'); }
});
window.addEventListener('message', async event => {
  if (event.source !== window.parent || event.data.type !== 'streamlit:render') return;
  parentOrigin = event.origin;
  const args = event.data.args || {};
  limit = args.max_bytes || limit;
  chunkBytes = args.chunk_bytes || chunkBytes;
  if (event.data.theme) {
    document.body.style.setProperty('--text', event.data.theme.textColor);
    document.body.style.backgroundColor = event.data.theme.backgroundColor;
  }
  if (!initialized) { initialized = true; await restore(); }
  if (pending && args.rejected_digest === pending.digest) {
    pending = transfer = null;
    message('COMP 자료 검증에 실패했습니다. 기존 보관 파일을 유지합니다. 다른 ZIP을 선택하세요.');
    return;
  }
  if (transfer && args.received_id === transfer.id && args.received_part === transfer.part) {
    transfer.part++;
    if (transfer.part < transfer.total) sendPiece();
  }
  if (pending && !saving && args.accepted_digest === pending.digest) {
    saving = true;
    fileInput.disabled = clearButton.disabled = true;
    const accepted = pending;
    try {
      if (!current || current.digest !== accepted.digest) await stored('put', accepted);
      current = accepted;
      if (pending === accepted) pending = null;
      message(`보관됨 · ${accepted.name} · ${(accepted.bytes.byteLength / 1024 / 1024).toFixed(1)}MB`);
    } catch (_) { message(`현재 접속에서만 사용 중 · ${accepted.name} · 브라우저 보관에 실패했습니다.`); }
    finally { saving = false; fileInput.disabled = clearButton.disabled = false; }
  }
});
send('streamlit:componentReady', {apiVersion: 1});
send('streamlit:setFrameHeight', {height: 160});
