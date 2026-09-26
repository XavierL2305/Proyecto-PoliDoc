// Utilidad global anti-XSS compartida.
// Escapa texto antes de interpolarlo en innerHTML/atributos. Los datos de
// documentos, usuarios y respuestas de formularios son contenido introducido
// por el usuario y NUNCA deben insertarse crudos en el DOM.
(function () {
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Escapa una cadena para usarla como literal JS entre comillas simples DENTRO
  // de un atributo HTML (p.ej. onclick="fn('...')"). Primero se escapa para JS
  // y luego para HTML, de modo que ni una comilla ni un `"` puedan romper el
  // atributo y ejecutar código (doble contexto JS+HTML).
  function escapeAttrJs(value) {
    if (value === null || value === undefined) return '';
    const js = String(value)
      .replace(/\\/g, '\\\\')
      .replace(/'/g, "\\'")
      .replace(/"/g, '\\"')
      .replace(/\n/g, '\\n')
      .replace(/\r/g, '\\r');
    return escapeHtml(js);
  }

  // No sobrescribir si ya existe (p.ej. documentos.js define la suya).
  if (typeof window.escapeHtml !== 'function') {
    window.escapeHtml = escapeHtml;
  }
  if (typeof window.escapeAttrJs !== 'function') {
    window.escapeAttrJs = escapeAttrJs;
  }
})();
