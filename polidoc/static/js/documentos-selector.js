/* Selector reutilizable de documentos (búsqueda libre por nombre/código).
 *
 * Auto-inicializa cada `.documentos-selector` del documento. La config se lee
 * de atributos data-* del contenedor (ver documentos/_documentos_selector.html).
 *
 * payload_mode:
 *   - 'inputs': emite <input name="hidden_name" value="<id>"> por documento y
 *     el JSON `json_name` con la lista de códigos pendientes.
 *   - 'json':   emite el JSON `json_name` con items
 *     {uuid, codigo, nombre, estado, tipo, pendiente}.
 */
(function () {
  'use strict';

  function esc(value) {
    if (window.escapeHtml) return window.escapeHtml(value);
    var el = document.createElement('span');
    el.textContent = value == null ? '' : String(value);
    return el.innerHTML;
  }

  function escAttr(value) {
    if (window.escapeAttrJs) return window.escapeAttrJs(value);
    return String(value == null ? '' : value).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  function initSelector(box) {
    var cfg = {
      searchUrl: box.dataset.searchUrl || '/documentos/api/referenciables/',
      excludeId: box.dataset.excludeId || '',
      payloadMode: box.dataset.payloadMode || 'inputs',
      hiddenName: box.dataset.hiddenName || '',
      jsonName: box.dataset.jsonName || '',
      permitePendiente: box.dataset.permitePendiente === '1',
      extraDeptSelector: box.dataset.extraDeptSelector || '',
      afectaTodosId: box.dataset.afectaTodosId || '',
    };

    var searchInput = box.querySelector('.js-ds-search');
    var select = box.querySelector('.js-ds-select');
    var codeInput = box.querySelector('.js-ds-code');
    var addCodeBtn = box.querySelector('.js-ds-add-code');
    var tags = box.querySelector('.js-ds-tags');
    var hidden = box.querySelector('.js-ds-hidden');
    var jsonInput = box.querySelector('.js-ds-json');
    if (!searchInput || !select || !tags || !hidden) return;

    var selected = {};   // key -> item existente
    var pending = [];    // códigos pendientes (únicos, en orden)

    var initialEl = box.querySelector('.js-ds-initial');
    if (initialEl) {
      try {
        (JSON.parse(initialEl.textContent) || []).forEach(function (it) {
          if (it.pendiente) {
            var c = String(it.codigo || '').toUpperCase();
            if (c && pending.indexOf(c) === -1) pending.push(c);
          } else if (it.uuid || it.id) {
            selected[it.uuid || it.id] = {
              id: it.id, uuid: it.uuid, codigo: it.codigo, nombre: it.nombre,
              estado: it.estado || 'activo', tipo: it.tipo || '',
            };
          }
        });
      } catch (e) { /* JSON inválido: ignorar */ }
    }

    function render() {
      tags.innerHTML = '';
      hidden.innerHTML = '';
      var items = [];
      Object.keys(selected).forEach(function (key) {
        var it = selected[key];
        var label = (it.codigo ? it.codigo + ' - ' : '') + (it.nombre || '');
        tags.insertAdjacentHTML(
          'beforeend',
          '<span class="depto-tag">' + esc(label) +
          '<button type="button" class="depto-tag-remove" data-remove="' + escAttr(key) + '">' +
          '<i class="fa-solid fa-xmark"></i></button></span>'
        );
        if (cfg.payloadMode === 'inputs' && cfg.hiddenName) {
          hidden.insertAdjacentHTML(
            'beforeend',
            '<input type="hidden" name="' + cfg.hiddenName + '" value="' + escAttr(it.id) + '">'
          );
        }
        items.push({
          id: it.id, uuid: it.uuid, codigo: it.codigo, nombre: it.nombre,
          estado: it.estado, tipo: it.tipo, pendiente: false,
        });
      });
      pending.forEach(function (codigo) {
        tags.insertAdjacentHTML(
          'beforeend',
          '<span class="depto-tag depto-tag--pendiente" title="Documento aún no creado. Se enlazará al crearse.">' +
          esc(codigo) + ' <span class="badge badge--warning" style="font-size:.65rem;margin-left:.25rem;">Pendiente</span>' +
          '<button type="button" class="depto-tag-remove" data-remove-pending="' + escAttr(codigo) + '">' +
          '<i class="fa-solid fa-xmark"></i></button></span>'
        );
        items.push({ codigo: codigo, pendiente: true });
      });
      if (jsonInput && cfg.jsonName) {
        jsonInput.value = cfg.payloadMode === 'inputs'
          ? JSON.stringify(pending)
          : JSON.stringify(items);
      }
    }

    tags.addEventListener('click', function (e) {
      var rm = e.target.closest('[data-remove]');
      if (rm) { delete selected[rm.getAttribute('data-remove')]; render(); return; }
      var rmp = e.target.closest('[data-remove-pending]');
      if (rmp) {
        var c = rmp.getAttribute('data-remove-pending');
        pending = pending.filter(function (x) { return x !== c; });
        render();
      }
    });

    select.addEventListener('change', function () {
      var opt = select.options[select.selectedIndex];
      if (!opt || !opt.value) return;
      var key = opt.dataset.uuid || opt.dataset.id;
      if (key && !selected[key]) {
        selected[key] = {
          id: opt.dataset.id, uuid: opt.dataset.uuid, codigo: opt.dataset.codigo,
          nombre: opt.dataset.nombre, estado: opt.dataset.estado || 'activo',
          tipo: opt.dataset.tipo || '',
        };
        render();
      }
      select.value = '';
    });

    if (cfg.permitePendiente && addCodeBtn && codeInput) {
      addCodeBtn.addEventListener('click', function () {
        var c = (codeInput.value || '').trim().toUpperCase();
        codeInput.value = '';
        if (!c) return;
        var repetido = Object.keys(selected).some(function (k) {
          return String(selected[k].codigo || '').toUpperCase() === c;
        });
        if (!repetido && pending.indexOf(c) === -1) { pending.push(c); render(); }
      });
    }

    function extraParams() {
      var params = new URLSearchParams();
      if (cfg.excludeId) params.set('exclude_id', cfg.excludeId);
      if (cfg.afectaTodosId) {
        var cb = document.getElementById(cfg.afectaTodosId);
        if (cb && cb.checked) { params.set('todos', '1'); return params; }
      }
      if (cfg.extraDeptSelector) {
        document.querySelectorAll(cfg.extraDeptSelector).forEach(function (inp) {
          if (inp.value) params.append('departamentos', inp.value);
        });
        if (params.has('departamentos')) params.set('incluir_borradores', '1');
      }
      return params;
    }

    function fetchDocs(query) {
      var params = extraParams();
      if (query) params.set('buscar', query);
      fetch(cfg.searchUrl + '?' + params.toString())
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var docs = (data && data.documentos) || [];
          if (docs.length === 0) {
            select.innerHTML = '<option value="">Sin resultados</option>';
            return;
          }
          select.innerHTML = '<option value="">Seleccione un documento...</option>';
          docs.forEach(function (d) {
            var opt = document.createElement('option');
            opt.value = d.uuid || d.id;
            opt.textContent = (d.codigo ? d.codigo + ' - ' : '') + d.nombre;
            opt.dataset.id = d.id;
            opt.dataset.uuid = d.uuid || '';
            opt.dataset.codigo = d.codigo || '';
            opt.dataset.nombre = d.nombre || '';
            opt.dataset.estado = d.estado || 'activo';
            opt.dataset.tipo = d.tipo || '';
            select.appendChild(opt);
          });
        })
        .catch(function () { select.innerHTML = '<option value="">Error al buscar</option>'; });
    }

    var timer = null;
    searchInput.addEventListener('input', function () {
      clearTimeout(timer);
      var q = searchInput.value.trim();
      timer = setTimeout(function () { fetchDocs(q); }, 300);
    });

    render();
    fetchDocs('');
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.documentos-selector').forEach(initSelector);
  });
})();
