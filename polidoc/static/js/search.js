(() => {
  'use strict';

  const DEBOUNCE_MS = 300;
  const MIN_CHARS = 2;
  const MAX_RESULTS = 8;

  let sugerencias = [];
  let selectedIndex = -1;
  let syncingGuard = false;
  let activeSearchController = null;

  const navbarInput = document.getElementById('navbar-buscar');
  const navbarResults = document.getElementById('navbar-buscar-resultados');
  const mainForm = document.querySelector('form.filter-bar');
  const mainInput = mainForm ? mainForm.querySelector('input[name="buscar"]') : null;

  if (!navbarInput || !navbarResults) return;

  // ── Debounce ──
  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  // ── Escape HTML (XSS-safe) ──
  function escapeHtml(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
  }

  // ── Highlight del texto coincidente (XSS-safe) ──
  function highlightMatch(text, query) {
    if (!query) return escapeHtml(text);
    const escaped = escapeHtml(text);
    const q = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return escaped.replace(
      new RegExp(`(${q})`, 'gi'),
      '<mark>$1</mark>'
    );
  }

  // ── Badge de estado ──
  function estadoBadge(estado) {
    const colors = {
      borrador: 'warning',
      en_revision: 'info',
      activo: 'success',
      obsoleto: 'danger',
    };
    const labels = {
      borrador: 'Borrador',
      en_revision: 'En Revisión',
      activo: 'Activo',
      obsoleto: 'Obsoleto',
    };
    return `<span class="badge badge--${colors[estado] || 'primary'}">${labels[estado] || estado}</span>`;
  }

  // ── Fetch sugerencias ──
  const fetchSugerencias = debounce(async (q) => {
    if (q.length < MIN_CHARS) {
      hideResults();
      return;
    }

    if (activeSearchController) activeSearchController.abort();
    activeSearchController = new AbortController();

    showLoading();

    try {
      const resp = await fetch(
        `/documentos/api/buscar-sugerencias/?q=${encodeURIComponent(q)}&limit=${MAX_RESULTS}`,
        { signal: activeSearchController.signal }
      );
      const data = await resp.json();
      sugerencias = data.sugerencias || [];
      selectedIndex = -1;
      renderResults(q);
    } catch (e) {
      if (e.name === 'AbortError') return;
      sugerencias = [];
      renderResults(q);
    }
  }, DEBOUNCE_MS);

  // ── Render resultados (createElement + textContent = XSS-safe) ──
  function renderResults(query) {
    navbarResults.innerHTML = '';

    if (sugerencias.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'search-result-empty';
      empty.textContent = 'No se encontraron resultados';
      navbarResults.appendChild(empty);
      navbarResults.style.display = 'block';
      updateAria(false);
      return;
    }

    sugerencias.forEach((s, i) => {
      const item = document.createElement('div');
      item.className = 'search-result-item';
      item.id = `navbar-result-${i}`;
      item.setAttribute('role', 'option');
      item.setAttribute('data-index', i);
      item.setAttribute('data-tipo', s.tipo);
      item.setAttribute('data-id', s.id);
      if (i === selectedIndex) {
        item.classList.add('selected');
        item.setAttribute('aria-selected', 'true');
      }

      // Code
      const code = document.createElement('span');
      code.className = 'result-code';
      code.innerHTML = highlightMatch(s.codigo, query);

      // Name + Owner
      const nameWrap = document.createElement('span');
      nameWrap.className = 'result-name-wrap';

      const name = document.createElement('span');
      name.className = 'result-name';
      name.innerHTML = highlightMatch(s.nombre, query);
      nameWrap.appendChild(name);

      if (s.propietario) {
        const owner = document.createElement('span');
        owner.className = 'result-owner';
        let ownerText = s.propietario;
        if (s.fecha_vencimiento) {
          const hoy = new Date();
          hoy.setHours(0,0,0,0);
          const fv = new Date(s.fecha_vencimiento + 'T00:00:00');
          const diff = Math.ceil((fv - hoy) / (1000 * 60 * 60 * 24));
          if (diff <= 0) ownerText += ' · Vencido';
          else if (diff <= 7) ownerText += ' · Vence pronto';
        }
        owner.textContent = ownerText;
        nameWrap.appendChild(owner);
      }

      // Badge
      const badge = document.createElement('span');
      badge.className = 'result-badge';
      badge.innerHTML = estadoBadge(s.estado);

      // Tipo label
      const tipo = document.createElement('span');
      tipo.className = 'result-tipo';
      tipo.textContent = s.tipo_display;

      item.appendChild(code);
      item.appendChild(nameWrap);
      item.appendChild(badge);
      item.appendChild(tipo);
      navbarResults.appendChild(item);
    });

    navbarResults.style.display = 'block';
    updateAria(false);
  }

  function showLoading() {
    navbarResults.innerHTML = '';
    const loading = document.createElement('div');
    loading.className = 'search-result-loading';
    const spinner = document.createElement('i');
    spinner.className = 'fa-solid fa-spinner fa-spin';
    loading.appendChild(spinner);
    loading.appendChild(document.createTextNode(' Buscando...'));
    navbarResults.appendChild(loading);
    navbarResults.style.display = 'block';
  }

  function hideResults() {
    navbarResults.style.display = 'none';
    navbarResults.innerHTML = '';
    selectedIndex = -1;
    navbarInput.removeAttribute('aria-activedescendant');
    updateAria(true);
  }

  function updateAria(resultsHidden) {
    navbarInput.setAttribute('aria-expanded', resultsHidden ? 'false' : 'true');
  }

  function selectItem(index) {
    const items = navbarResults.querySelectorAll('.search-result-item');
    items.forEach((el) => {
      el.classList.remove('selected');
      el.removeAttribute('aria-selected');
    });

    if (index < 0 || index >= items.length) {
      selectedIndex = -1;
      navbarInput.removeAttribute('aria-activedescendant');
      return;
    }

    selectedIndex = index;
    const el = items[index];
    el.classList.add('selected');
    el.setAttribute('aria-selected', 'true');
    navbarInput.setAttribute('aria-activedescendant', el.id);
    el.scrollIntoView({ block: 'nearest' });
  }

  function navigateToItem(s) {
    window.location.href = `/documentos/${s.tipo}/${s.id}/`;
  }

  // ── Eventos del navbar input ──
  navbarInput.addEventListener('input', (e) => {
    const q = e.target.value.trim();
    syncMainInput(q);
    fetchSugerencias(q);
  });

  navbarInput.addEventListener('keydown', (e) => {
    if (navbarResults.style.display === 'none' || !sugerencias.length) return;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        selectItem(Math.min(selectedIndex + 1, sugerencias.length - 1));
        break;

      case 'ArrowUp':
        e.preventDefault();
        selectItem(Math.max(selectedIndex - 1, -1));
        break;

      case 'Enter':
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < sugerencias.length) {
          navigateToItem(sugerencias[selectedIndex]);
        } else {
          hideResults();
          redirectToMisDocumentos();
        }
        break;

      case 'Escape':
        e.preventDefault();
        hideResults();
        navbarInput.blur();
        break;
    }
  });

  navbarInput.addEventListener('blur', () => {
    setTimeout(hideResults, 200);
  });

  navbarInput.addEventListener('focus', () => {
    const q = navbarInput.value.trim();
    if (q.length >= MIN_CHARS) {
      if (sugerencias.length) {
        navbarResults.style.display = 'block';
        updateAria(false);
      } else {
        fetchSugerencias(q);
      }
    }
  });

  // Click en resultado
  navbarResults.addEventListener('click', (e) => {
    const item = e.target.closest('.search-result-item');
    if (!item) return;
    const idx = parseInt(item.getAttribute('data-index'), 10);
    if (idx >= 0 && idx < sugerencias.length) {
      navigateToItem(sugerencias[idx]);
    }
  });

  // Hover en resultado
  navbarResults.addEventListener('mousemove', (e) => {
    const item = e.target.closest('.search-result-item');
    if (!item) return;
    const idx = parseInt(item.getAttribute('data-index'), 10);
    if (idx !== selectedIndex) selectItem(idx);
  });

  // Cerrar al hacer clic fuera
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.navbar-search')) {
      hideResults();
    }
  });

  // ── Sync bidireccional navbar ↔ mis_documentos ──
  function syncMainInput(value) {
    if (!mainInput || syncingGuard) return;
    syncingGuard = true;
    mainInput.value = value;
    syncingGuard = false;
  }

  function syncNavbarInput(value) {
    if (syncingGuard) return;
    syncingGuard = true;
    navbarInput.value = value;
    syncingGuard = false;
  }

  if (mainInput) {
    // Sincronizar valor inicial del form al navbar
    if (mainInput.value) {
      syncNavbarInput(mainInput.value);
    }

    mainInput.addEventListener('input', (e) => {
      syncNavbarInput(e.target.value);
    });
  }

  // ── Enter en navbar sin selección → buscar en mis_documentos ──
  function redirectToMisDocumentos() {
    const q = navbarInput.value.trim();
    if (q.length < MIN_CHARS) return;
    window.location.href = `/documentos/mis-documentos/?buscar=${encodeURIComponent(q)}`;
  }

  // ── Cerrar dropdown al hacer scroll ──
  document.addEventListener('scroll', (e) => {
    if (e.target !== navbarResults && !navbarResults.contains(e.target)) {
      hideResults();
    }
  }, true);
})();
