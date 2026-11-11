/**
 * POLIDOC - Sidebar Manager
 * Hamburger toggle: drawer (móvil) / collapse (PC) con persistencia.
 */
(function () {
  'use strict';

  var sidebar = document.getElementById('app-sidebar');
  var overlay = document.getElementById('sidebar-overlay');
  var toggle  = document.getElementById('sidebar-toggle');
  if (!sidebar || !toggle) return;

  var mq = window.matchMedia('(max-width: 768px)');
  var STORAGE_KEY = 'polidoc:sidebar';
  var GROUP_KEY_PREFIX = 'polidoc:sidebar:group:';

  /* ---------- helpers ---------- */

  function isMobile() { return mq.matches; }

  function setAria(open) {
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
  }

  function setDesktopAria(collapsed) {
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    toggle.setAttribute('aria-label', collapsed ? 'Expandir menú' : 'Colapsar menú');
  }

  /* ---------- mobile drawer ---------- */

  function setDrawer(open) {
    sidebar.classList.toggle('active', open);
    if (overlay) overlay.classList.toggle('active', open);
    toggle.classList.toggle('is-open', open);
    setAria(open);
  }

  function closeDrawer() {
    setDrawer(false);
  }

  /* ---------- desktop collapse ---------- */

  function setCollapsed(collapsed) {
    sidebar.classList.toggle('collapsed', collapsed);
    setDesktopAria(collapsed);
    try { localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch (e) {}
  }

  function loadDesktopState() {
    if (isMobile()) return;
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  /* ---------- grupos colapsables ---------- */

  var groups = sidebar.querySelectorAll('.sidebar-group');

  function groupId(group) {
    return group.getAttribute('data-group') || '';
  }

  function setGroupOpen(group, open, persist) {
    group.classList.toggle('is-open', open);
    var btn = group.querySelector('.sidebar-group-toggle');
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (persist) {
      try {
        localStorage.setItem(GROUP_KEY_PREFIX + groupId(group), open ? '1' : '0');
      } catch (e) {}
    }
  }

  function storedGroup(group) {
    try {
      return localStorage.getItem(GROUP_KEY_PREFIX + groupId(group));
    } catch (e) {
      return null;
    }
  }

  function initGroups() {
    groups.forEach(function (group) {
      // El grupo que contiene la página activa manda: se abre siempre.
      if (group.classList.contains('has-active')) {
        setGroupOpen(group, true, false);
      } else {
        var saved = storedGroup(group);
        if (saved !== null) {
          setGroupOpen(group, saved === '1', false);
        } else {
          setGroupOpen(group, group.classList.contains('is-open'), false);
        }
      }

      var btn = group.querySelector('.sidebar-group-toggle');
      if (!btn) return;
      btn.addEventListener('click', function () {
        if (!isMobile() && sidebar.classList.contains('collapsed')) {
          // Modo iconos: un click = expandir la sidebar + abrir el grupo.
          setCollapsed(false);
          setGroupOpen(group, true, true);
          return;
        }
        setGroupOpen(group, !group.classList.contains('is-open'), true);
      });
    });
  }

  /* ---------- init ---------- */

  function initDesktop() {
    var saved = loadDesktopState();
    setCollapsed(saved === '1');
  }

  function initMobile() {
    closeDrawer();
  }

  /* ---------- events ---------- */

  toggle.addEventListener('click', function () {
    if (isMobile()) {
      setDrawer(!sidebar.classList.contains('active'));
    } else {
      setCollapsed(!sidebar.classList.contains('collapsed'));
    }
  });

  if (overlay) {
    overlay.addEventListener('click', closeDrawer);
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isMobile() && sidebar.classList.contains('active')) {
      closeDrawer();
    }
  });

  sidebar.querySelectorAll('.sidebar-item').forEach(function (item) {
    item.addEventListener('click', function () {
      if (isMobile()) closeDrawer();
    });
  });

  mq.addEventListener('change', function () {
    if (isMobile()) {
      closeDrawer();
    } else {
      initDesktop();
    }
  });

  /* ---------- run ---------- */
  if (isMobile()) {
    initMobile();
  } else {
    initDesktop();
  }
  initGroups();
})();
