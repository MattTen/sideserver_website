// Drag & drop enhancer for dropzones + copy-to-clipboard.
(() => {
  const dz = document.getElementById('dropzone');
  const input = document.getElementById('ipa-input');
  const submit = document.getElementById('upload-submit');
  const status = document.getElementById('upload-status');
  const form = document.getElementById('upload-form');
  const progressBar = dz?.querySelector('.progress-bar');
  const progressWrap = dz?.querySelector('.progress');

  if (dz && input) {
    ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => {
      e.preventDefault(); dz.classList.add('drag-over');
    }));
    ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
      e.preventDefault(); dz.classList.remove('drag-over');
    }));
    dz.addEventListener('drop', e => {
      const f = e.dataTransfer.files?.[0];
      if (f) { input.files = e.dataTransfer.files; fileSelected(f); }
    });
    input.addEventListener('change', () => {
      if (input.files?.[0]) fileSelected(input.files[0]);
    });
  }

  function fileSelected(f) {
    if (status) status.textContent = `${f.name} — ${formatSize(f.size)}`;
    if (submit) submit.disabled = false;
  }

  function formatSize(n) {
    const units = ['o', 'Kio', 'Mio', 'Gio'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + units[i];
  }

  if (form && submit && progressBar && progressWrap) {
    form.addEventListener('submit', e => {
      if (!input.files?.[0]) return;
      e.preventDefault();
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', ev => {
        if (!ev.lengthComputable) return;
        const pct = (ev.loaded / ev.total) * 100;
        progressWrap.style.display = 'block';
        progressBar.style.width = pct.toFixed(1) + '%';
        if (status) status.textContent = `Upload ${pct.toFixed(1)}%`;
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 400) {
          const target = xhr.responseURL || '/apps';
          window.location.href = target;
        } else {
          let msg = `Erreur ${xhr.status}`;
          try { const j = JSON.parse(xhr.responseText); if (j.detail) msg += ` — ${j.detail}`; } catch {}
          if (status) status.textContent = msg;
          progressBar.style.width = '0%';
          progressWrap.style.display = 'none';
          submit.disabled = false;
        }
      });
      xhr.addEventListener('error', () => {
        if (status) status.textContent = 'Erreur réseau';
        submit.disabled = false;
      });
      submit.disabled = true;
      const fd = new FormData(form);
      xhr.open('POST', form.action);
      xhr.send(fd);
    });
  }

  // Copy source URL
  const copyBtn = document.getElementById('copy-btn');
  const srcUrl = document.getElementById('src-url');
  if (copyBtn && srcUrl) {
    copyBtn.addEventListener('click', async () => {
      const text = srcUrl.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        const label = copyBtn.querySelector('span');
        const prev = label.textContent;
        label.textContent = 'Copié';
        setTimeout(() => { label.textContent = prev; }, 1500);
      } catch (_) {
        // fallback
        const sel = document.createRange();
        sel.selectNode(srcUrl);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(sel);
      }
    });
  }
})();
