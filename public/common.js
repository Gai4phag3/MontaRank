// stuff shared by all the pages

async function api(path, body, method) {
  const r = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString([], { month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit' });
}

// how many hours late a task is, null if it's fine (same idea as the backend)
function taskHoursLate(t) {
  if (!t || t.status === 'verified') return null;
  const dl = new Date(t.deadline);
  if (isNaN(dl)) return null;
  if (t.submitted_at) {
    const sub = new Date(t.submitted_at);
    return sub > dl ? Math.ceil((sub - dl) / 3600000) : null;
  }
  const now = new Date();
  return now > dl ? Math.ceil((now - dl) / 3600000) : null;
}

function fmtLate(hours) {
  if (hours == null) return '';
  if (hours < 24) return hours + 'h late';
  const d = Math.floor(hours / 24), h = hours % 24;
  return d + 'd ' + (h ? h + 'h ' : '') + 'late';
}

function strikeClass(n) {
  if (n >= 3) return 'strike3';
  if (n === 2) return 'strike2';
  if (n === 1) return 'strike1';
  return '';
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// modal popups
function showModal(html) {
  closeModal();
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.id = 'modal-bg';
  bg.innerHTML = `<div class="modal"><span class="close-x" onclick="closeModal()">×</span>${html}</div>`;
  bg.addEventListener('click', e => { if (e.target === bg) closeModal(); });
  document.body.appendChild(bg);
}
function closeModal() {
  const m = document.getElementById('modal-bg');
  if (m) m.remove();
}

// local server gives a relative path, the hosted one gives a full blob url
function mediaURL(v) {
  if (!v) return '';
  return /^https?:\/\//.test(v) ? v : '/uploads/' + v;
}

function mediaPreview(url, mime) {
  if (!url) return '<p class="muted">No file.</p>';
  const full = mediaURL(url);
  if ((mime || '').startsWith('image/') || /\.(png|jpg|jpeg|gif|webp)$/i.test(url))
    return `<img src="${full}">`;
  if ((mime || '').startsWith('video/') || /\.(mp4|webm|mov)$/i.test(url))
    return `<video src="${full}" controls></video>`;
  if ((mime || '').includes('pdf') || /\.pdf$/i.test(url))
    return `<iframe src="${full}" style="width:100%;height:70vh;border:none;border-radius:8px"></iframe>`;
  return `<p><a href="${full}" target="_blank">Open / download file</a></p>`;
}

async function logout() {
  await api('/api/logout', {});
  location.href = '/';
}

function fileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
