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

  /* ---------- helpers ---------- */

  function isMobile() { return mq.matches; }

  function setAria(open) {
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
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
    setAria(!collapsed);
    try { localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch (e) {}
  }

  function loadDesktopState() {
    if (isMobile()) return;
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
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
})();
