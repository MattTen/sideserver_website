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

  // Formulaires async (OK vert si succes, boite rouge avec X sinon/timeout 2s)
  document.querySelectorAll('form[data-async-form]').forEach(form => {
    const okIcon = form.querySelector('[data-async-ok]');
    const errBox = form.querySelector('[data-async-err]');
    const errMsg = form.querySelector('[data-async-err-msg]');
    const errClose = form.querySelector('[data-async-err-close]');
    const resetOnSuccess = form.hasAttribute('data-reset-on-success');
    const defaultErr = errMsg ? errMsg.textContent : 'Une erreur est survenue.';
    let okTimer = null;
    function showOk() {
      if (errBox) errBox.style.display = 'none';
      if (!okIcon) return;
      okIcon.classList.add('show');
      clearTimeout(okTimer);
      okTimer = setTimeout(() => okIcon.classList.remove('show'), 1500);
    }
    function showErr(msg) {
      if (okIcon) okIcon.classList.remove('show');
      if (!errBox) return;
      if (errMsg) errMsg.textContent = msg || defaultErr;
      errBox.style.display = 'flex';
    }
    if (errClose) errClose.addEventListener('click', () => { errBox.style.display = 'none'; });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 2000);
      try {
        const r = await fetch(form.action, {
          method: 'POST', body: fd, credentials: 'same-origin', signal: ctrl.signal,
        });
        clearTimeout(timeout);
        if (r.ok) {
          if (resetOnSuccess) form.reset();
          showOk();
        } else {
          let msg = null;
          try { const j = await r.json(); msg = j.error || j.message; } catch (_) {}
          showErr(msg);
        }
      } catch (_) {
        clearTimeout(timeout);
        showErr(null);
      }
    });
  });

  // Indexing toggle (settings page)
  const indexingToggle = document.getElementById('toggle-indexing');
  if (indexingToggle) {
    const okIcon = document.getElementById('toggle-indexing-ok');
    const errBox = document.getElementById('toggle-indexing-err');
    const errClose = document.getElementById('toggle-indexing-err-close');
    let okTimer = null;
    function showOk() {
      if (errBox) errBox.style.display = 'none';
      if (!okIcon) return;
      okIcon.classList.add('show');
      clearTimeout(okTimer);
      okTimer = setTimeout(() => okIcon.classList.remove('show'), 1500);
    }
    function showErr() {
      if (okIcon) okIcon.classList.remove('show');
      indexingToggle.checked = !indexingToggle.checked;
      if (errBox) errBox.style.display = 'flex';
    }
    if (errClose) errClose.addEventListener('click', () => { errBox.style.display = 'none'; });
    indexingToggle.addEventListener('change', async () => {
      const fd = new FormData();
      fd.append('disable_indexing', indexingToggle.checked ? '1' : '0');
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 2000);
      try {
        const r = await fetch('/settings/indexing', {
          method: 'POST', body: fd, credentials: 'same-origin', signal: ctrl.signal,
        });
        clearTimeout(timeout);
        if (r.ok) showOk();
        else showErr();
      } catch (_) {
        clearTimeout(timeout);
        showErr();
      }
    });
  }

  // Source token (settings page) : toggle + copy + regenerate
  const srcTokenToggle = document.getElementById('toggle-srctoken');
  if (srcTokenToggle) {
    const okIcon = document.getElementById('toggle-srctoken-ok');
    const errBox = document.getElementById('toggle-srctoken-err');
    const errClose = document.getElementById('toggle-srctoken-err-close');
    const block = document.getElementById('srctoken-block');
    const valBox = document.getElementById('srctoken-value');
    const copyBtn = document.getElementById('srctoken-copy-btn');
    const regenBtn = document.getElementById('srctoken-regen-btn');
    let okTimer = null;
    function showOk() {
      if (errBox) errBox.style.display = 'none';
      if (!okIcon) return;
      okIcon.classList.add('show');
      clearTimeout(okTimer);
      okTimer = setTimeout(() => okIcon.classList.remove('show'), 1500);
    }
    function showErr(revert) {
      if (okIcon) okIcon.classList.remove('show');
      if (revert) srcTokenToggle.checked = !srcTokenToggle.checked;
      if (errBox) errBox.style.display = 'flex';
    }
    if (errClose) errClose.addEventListener('click', () => { errBox.style.display = 'none'; });

    srcTokenToggle.addEventListener('change', async () => {
      const fd = new FormData();
      fd.append('enabled', srcTokenToggle.checked ? '1' : '0');
      try {
        const r = await fetch('/settings/source-token', {
          method: 'POST', body: fd, credentials: 'same-origin',
        });
        if (!r.ok) { showErr(true); return; }
        const data = await r.json();
        if (valBox) valBox.textContent = data.token || '';
        if (block) block.style.display = data.enabled ? 'block' : 'none';
        showOk();
      } catch (_) { showErr(true); }
    });

    if (copyBtn) copyBtn.addEventListener('click', async () => {
      const text = valBox ? valBox.textContent.trim() : '';
      try {
        await navigator.clipboard.writeText(text);
        const old = copyBtn.textContent;
        copyBtn.textContent = 'Copié ✓';
        setTimeout(() => { copyBtn.textContent = old; }, 1200);
      } catch (_) {
        if (valBox) {
          const range = document.createRange();
          range.selectNodeContents(valBox);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          document.execCommand('copy');
        }
      }
    });

    if (regenBtn) regenBtn.addEventListener('click', async () => {
      // Confirmation explicite : la regen casse tous les liens partages.
      if (!confirm("Régénérer le jeton ?\n\nTous les liens contenant l'ancien jeton cesseront de fonctionner. Vous devrez re-partager le nouveau lien aux utilisateurs autorisés.")) {
        return;
      }
      try {
        const r = await fetch('/settings/source-token/regenerate', {
          method: 'POST', credentials: 'same-origin',
        });
        if (!r.ok) { showErr(false); return; }
        const data = await r.json();
        if (valBox) valBox.textContent = data.token || '';
        showOk();
      } catch (_) { showErr(false); }
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

  // Logs viewer (settings page)
  const logsToggleBtn = document.getElementById('logs-toggle-btn');
  const logsView = document.getElementById('logs-view');
  const logsRefreshBtn = document.getElementById('logs-refresh-btn');
  if (logsToggleBtn && logsView) {
    let logsTimer = null;
    async function fetchLogs() {
      try {
        const r = await fetch('/settings/logs?lines=500', { credentials: 'same-origin' });
        if (!r.ok) { logsView.textContent = 'Erreur ' + r.status; return; }
        const j = await r.json();
        const lines = j.lines || [];
        logsView.textContent = lines.length ? lines.join('\n') : (j.note || '(vide)');
        logsView.scrollTop = logsView.scrollHeight;
      } catch (e) {
        logsView.textContent = 'Erreur réseau : ' + e.message;
      }
    }
    logsToggleBtn.addEventListener('click', () => {
      const open = logsView.style.display !== 'none';
      if (open) {
        logsView.style.display = 'none';
        logsRefreshBtn.style.display = 'none';
        logsToggleBtn.textContent = 'Voir les logs';
        if (logsTimer) { clearInterval(logsTimer); logsTimer = null; }
      } else {
        logsView.style.display = 'block';
        logsRefreshBtn.style.display = 'inline-flex';
        logsToggleBtn.textContent = 'Masquer les logs';
        fetchLogs();
        logsTimer = setInterval(fetchLogs, 3000);
      }
    });
    if (logsRefreshBtn) logsRefreshBtn.addEventListener('click', fetchLogs);
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

  // QR modal (dashboard) : overlay centre au-dessus de la page, pas un
  // nouvel onglet. Click sur la miniature ou bouton "QR plein ecran" pour
  // ouvrir, click hors de la card / bouton X / Escape pour fermer.
  const qrModal = document.getElementById('qr-modal');
  if (qrModal) {
    const openBtn = document.getElementById('qr-open-btn');
    const thumb = document.getElementById('qr-thumb');
    const closeBtn = document.getElementById('qr-modal-close');
    function open() {
      qrModal.classList.add('is-visible');
      document.body.style.overflow = 'hidden';
    }
    function close() {
      qrModal.classList.remove('is-visible');
      document.body.style.overflow = '';
    }
    if (openBtn) openBtn.addEventListener('click', open);
    if (thumb) thumb.addEventListener('click', open);
    if (closeBtn) closeBtn.addEventListener('click', close);
    // Click sur le fond (mais pas sur la card) ferme
    qrModal.addEventListener('click', (e) => {
      if (e.target === qrModal) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && qrModal.classList.contains('is-visible')) close();
    });
  }
})();
