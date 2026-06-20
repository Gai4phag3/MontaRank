// Shared helpers for MontaRanker pages.

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

// Modal -------------------------------------------------------------------
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

// Local server stores a relative path ("attendance/x.png" -> /uploads/...);
// Vercel stores a full https Blob URL. Normalise either to a usable URL.
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

// read a File as data URL
function fileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
