/**
 * POLIDOC - Theme Manager
 * Maneja el modo oscuro y claro con persistencia en localStorage
 */

(function() {
  'use strict';

  // Detectar tema guardado o preferencia del sistema
  const getPreferredTheme = () => {
    const saved = localStorage.getItem('theme');
    if (saved) {
      return saved;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  };

  // Aplicar tema
  const applyTheme = (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  };

  // Initialize theme (already set by inline script in base.html head)
  // This ensures the matchMedia listener is still active for system changes
  if (!document.documentElement.getAttribute('data-theme')) {
    applyTheme(getPreferredTheme());
  }

  // Escuchar cambios en la preferencia del sistema
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    if (!localStorage.getItem('theme')) {
      applyTheme(e.matches ? 'dark' : 'light');
    }
  });
})();

// Función global para toggle de tema
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}

// Inicializar Lucide icons cuando el DOM esté listo
document.addEventListener('DOMContentLoaded', () => {
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }

  // Cerrar dropdown del usuario al hacer clic fuera
  document.addEventListener('click', (e) => {
    const dropdowns = document.querySelectorAll('.navbar-user-wrapper');
    dropdowns.forEach(dropdown => {
      if (!dropdown.contains(e.target)) {
        dropdown.classList.remove('active');
      }
    });
  });

  // Cerrar alertas al hacer clic en el botón de cerrar
  document.querySelectorAll('.alert-close').forEach(btn => {
    btn.addEventListener('click', function() {
      this.closest('.alert').style.display = 'none';
    });
  });
});
