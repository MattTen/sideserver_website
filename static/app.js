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

  // Indexing toggle (settings page)
  const indexingToggle = document.getElementById('toggle-indexing');
  if (indexingToggle) {
    const okIcon = document.getElementById('toggle-indexing-ok');
    const errBox = document.getElementById('toggle-indexing-err');
    let okTimer = null;
    let errTimer = null;
    function showOk() {
      if (errBox) { errBox.style.display = 'none'; clearTimeout(errTimer); }
      if (!okIcon) return;
      okIcon.classList.add('show');
      clearTimeout(okTimer);
      okTimer = setTimeout(() => okIcon.classList.remove('show'), 1500);
    }
    function showErr() {
      if (okIcon) okIcon.classList.remove('show');
      indexingToggle.checked = !indexingToggle.checked;
      if (!errBox) return;
      errBox.style.display = 'block';
      clearTimeout(errTimer);
      errTimer = setTimeout(() => { errBox.style.display = 'none'; }, 2000);
    }
    indexingToggle.addEventListener('change', async () => {
      const fd = new FormData();
      fd.append('disable_indexing', indexingToggle.checked ? '1' : '0');
      try {
        const r = await fetch('/settings/indexing', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (r.ok) showOk();
        else showErr();
      } catch (_) {
        showErr();
      }
    });
  }

  // Update management (settings page)
  const updCard = document.getElementById('updates-card');
  if (updCard) {
    const curEl = document.getElementById('upd-current');
    const latEl = document.getElementById('upd-latest');
    const chkEl = document.getElementById('upd-checked');
    const banner = document.getElementById('upd-banner');
    const checkBtn = document.getElementById('upd-check-btn');
    const applyBtn = document.getElementById('upd-apply-btn');
    const restartBtn = document.getElementById('upd-restart-btn');

    function showBanner(kind, msg) {
      banner.style.display = 'block';
      banner.className = 'alert alert-' + kind;
      banner.textContent = msg;
    }
    function hideBanner() {
      banner.style.display = 'none';
      banner.textContent = '';
    }
    function fmtTs(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      return d.toLocaleString('fr-FR');
    }

    function render(s) {
      curEl.textContent = s.current || '<aucune>';
      latEl.textContent = s.latest || '<non disponible>';
      chkEl.textContent = fmtTs(s.checked_at);
      if (s.error) {
        showBanner('error', 'Erreur : ' + s.error);
        applyBtn.disabled = true;
        return;
      }
      if (s.update_available) {
        showBanner('success', 'Mise à jour disponible : ' + (s.current || '<aucune>') + ' → ' + s.latest);
        applyBtn.disabled = false;
      } else {
        showBanner('info', 'À jour (' + (s.current || s.latest || '—') + ')');
        applyBtn.disabled = true;
      }
    }

    async function checkNow() {
      checkBtn.disabled = true;
      const prev = checkBtn.textContent;
      checkBtn.textContent = 'Vérification...';
      try {
        const r = await fetch('/settings/updates/check', { credentials: 'same-origin' });
        const j = await r.json();
        render(j);
      } catch (e) {
        showBanner('error', 'Erreur réseau : ' + e.message);
      } finally {
        checkBtn.disabled = false;
        checkBtn.textContent = prev;
      }
    }

    async function applyNow() {
      if (!confirm('Lancer la mise à jour maintenant ? Le conteneur va redémarrer.')) return;
      applyBtn.disabled = true;
      const prev = applyBtn.textContent;
      applyBtn.textContent = 'Demande envoyée...';
      try {
        const r = await fetch('/settings/updates/apply', {
          method: 'POST',
          credentials: 'same-origin',
        });
        const j = await r.json();
        if (j.ok) {
          showBanner('success', j.message || 'Mise à jour lancée.');
          applyBtn.textContent = 'Redémarrage en cours...';
          setTimeout(() => { window.location.reload(); }, 30000);
        } else {
          showBanner('error', (j.message || j.reason || 'Erreur inconnue'));
          applyBtn.disabled = false;
          applyBtn.textContent = prev;
        }
      } catch (e) {
        showBanner('error', 'Erreur réseau : ' + e.message);
        applyBtn.disabled = false;
        applyBtn.textContent = prev;
      }
    }

    async function restartNow() {
      if (!confirm('Redémarrer le conteneur maintenant ? L\'interface sera indisponible quelques secondes.')) return;
      restartBtn.disabled = true;
      const prev = restartBtn.textContent;
      restartBtn.textContent = 'Redémarrage...';
      try {
        const r = await fetch('/settings/updates/restart', {
          method: 'POST',
          credentials: 'same-origin',
        });
        const j = await r.json();
        if (j.ok) {
          showBanner('success', j.message || 'Redémarrage lancé.');
          setTimeout(() => { window.location.reload(); }, 8000);
        } else {
          showBanner('error', j.message || 'Erreur inconnue');
          restartBtn.disabled = false;
          restartBtn.textContent = prev;
        }
      } catch (e) {
        // Une erreur reseau est attendue : le serveur quitte avant/pendant la reponse.
        showBanner('success', 'Redémarrage en cours...');
        setTimeout(() => { window.location.reload(); }, 8000);
      }
    }

    checkBtn.addEventListener('click', checkNow);
    applyBtn.addEventListener('click', applyNow);
    if (restartBtn) restartBtn.addEventListener('click', restartNow);
    checkNow();
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
