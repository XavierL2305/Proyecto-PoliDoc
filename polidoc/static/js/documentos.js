// Estado actual de la tabla
const estado = {
    ordenar: 'nombre',
    direccion: 'asc',
    tipo: '',
    estado_doc: '',
    buscar: '',
    pagina: 1
};

// CSRF Token
function getCSRFToken() {
    const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
}

// Cargar documentos
async function cargarDocumentos() {
    const params = new URLSearchParams();
    params.set('ordenar', estado.ordenar);
    params.set('direccion', estado.direccion);
    params.set('pagina', estado.pagina);
    if (estado.tipo) params.set('tipo', estado.tipo);
    if (estado.estado_doc) params.set('estado', estado.estado_doc);
    if (estado.buscar) params.set('buscar', estado.buscar);

    try {
        const response = await fetch(`/documentos/api/lista/?${params}`);
        const data = await response.json();
        renderTabla(data.documentos);
        renderPaginacion(data);
        actualizarTextoPaginacion(data);
        actualizarIconosOrden();
    } catch (error) {
        console.error('Error cargando documentos:', error);
        mostrarAlerta('danger', 'Error al cargar los documentos');
    }
}

// Renderizar tabla
function renderTabla(documentos) {
    const tbody = document.getElementById('documentos-tbody');
    if (!documentos.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; padding: 2rem; color: var(--color-muted);">
                    <i class="fa-solid fa-folder-open" style="font-size: 2rem; margin-bottom: 0.5rem; display: block;"></i>
                    No se encontraron documentos
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = documentos.map(doc => `
        <tr>
            <td><input type="checkbox" class="table-checkbox" value="${doc.id}"></td>
            <td class="table-cell--bold">${escapeHtml(doc.nombre)}</td>
            <td><span class="badge badge--${doc.tipo}">${capitalize(doc.tipo)}</span></td>
            <td><span class="badge badge--primary">${escapeHtml(doc.tipo_archivo)}</span></td>
            <td><span class="badge badge--${getEstadoBadge(doc.estado)}">${capitalize(doc.estado)}</span></td>
            <td>${escapeHtml(doc.propietario)}</td>
            <td class="table-cell--muted">${doc.fecha_modificacion}</td>
            <td class="table-actions-cell">
                <button class="action-btn action-btn--icon" data-tooltip="Ver" onclick="verDocumento('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-eye"></i></button>
                <div class="actions-dropdown" id="actions-${doc.id}">
                    <button class="action-btn action-btn--icon" data-tooltip="Más opciones" onclick="toggleActionsDropdown(event, '${doc.id}')"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                    <div class="actions-dropdown-menu">
                        <a class="actions-dropdown-item" onclick="editarDocumento('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-pen"></i> Editar</a>
                        <a class="actions-dropdown-item" onclick="descargarDocumento('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-download"></i> Descargar</a>
                        <a class="actions-dropdown-item" onclick="verHistorial('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-clock-rotate-left"></i> Historial</a>
                        <a class="actions-dropdown-item" onclick="abrirModalEstado('${doc.tipo}', '${doc.id}', '${doc.estado}')"><i class="fa-solid fa-toggle-on"></i> Cambiar Estado</a>
                        ${userIsAdmin || userIsJefe ? `<a class="actions-dropdown-item" onclick="abrirModalTransferir('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-user-arrow-right"></i> Transferir</a>` : ''}
                        <div class="actions-dropdown-divider"></div>
                        <a class="actions-dropdown-item actions-dropdown-item--danger" onclick="eliminarDocumento('${doc.tipo}', '${doc.id}')"><i class="fa-solid fa-trash"></i> Eliminar</a>
                    </div>
                </div>
            </td>
        </tr>
    `).join('');
}

// Renderizar paginación
function renderPaginacion(data) {
    const container = document.getElementById('paginacion-container');
    if (data.total_paginas <= 1) {
        container.innerHTML = '';
        return;
    }

    let html = '';
    html += `<button class="pagination-item pagination-item--arrow" ${!data.tiene_anterior ? 'disabled' : ''} onclick="cambiarPagina(${data.pagina_actual - 1})"><i class="fa-solid fa-chevron-left"></i></button>`;

    for (let i = 1; i <= data.total_paginas; i++) {
        if (i === data.pagina_actual) {
            html += `<button class="pagination-item pagination-item--active">${i}</button>`;
        } else if (i === 1 || i === data.total_paginas || Math.abs(i - data.pagina_actual) <= 2) {
            html += `<button class="pagination-item" onclick="cambiarPagina(${i})">${i}</button>`;
        } else if (Math.abs(i - data.pagina_actual) === 3) {
            html += `<span class="pagination-ellipsis">...</span>`;
        }
    }

    html += `<button class="pagination-item pagination-item--arrow" ${!data.tiene_siguiente ? 'disabled' : ''} onclick="cambiarPagina(${data.pagina_actual + 1})"><i class="fa-solid fa-chevron-right"></i></button>`;

    container.innerHTML = html;
}

// Actualizar texto de paginación
function actualizarTextoPaginacion(data) {
    const texto = document.getElementById('texto-paginacion');
    const inicio = (data.pagina_actual - 1) * 25 + 1;
    const fin = Math.min(data.pagina_actual * 25, data.total_documentos);
    texto.textContent = `Mostrando ${inicio}-${fin} de ${data.total_documentos} documentos`;
}

// Actualizar iconos de orden
function actualizarIconosOrden() {
    document.querySelectorAll('.sortable').forEach(th => {
        th.classList.remove('active');
        const icon = th.querySelector('.sortable-icon');
        if (icon) {
            icon.className = 'fa-solid fa-sort sortable-icon';
        }
    });

    const thActivo = document.querySelector(`.sortable[onclick*="${estado.ordenar}"]`);
    if (thActivo) {
        thActivo.classList.add('active');
        const icon = thActivo.querySelector('.sortable-icon');
        if (icon) {
            icon.className = estado.direccion === 'asc'
                ? 'fa-solid fa-sort-up sortable-icon'
                : 'fa-solid fa-sort-down sortable-icon';
        }
    }
}

// Ordenar por columna
function ordenarPor(campo) {
    if (estado.ordenar === campo) {
        estado.direccion = estado.direccion === 'asc' ? 'desc' : 'asc';
    } else {
        estado.ordenar = campo;
        estado.direccion = 'asc';
    }
    estado.pagina = 1;
    cargarDocumentos();
}

// Cambiar página
function cambiarPagina(pagina) {
    estado.pagina = pagina;
    cargarDocumentos();
}

// Toggle dropdown (uno a la vez)
function toggleDropdown(id) {
    const dropdown = document.getElementById(id);
    const isActive = dropdown.classList.contains('active');

    // Cerrar todos los dropdowns
    document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('active'));

    // Abrir el que se clickeó (si no estaba abierto)
    if (!isActive) {
        dropdown.classList.add('active');
    }
}

// Filtrar por tipo
function filtrarTipo(tipo) {
    estado.tipo = tipo;
    estado.pagina = 1;
    document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('active'));
    cargarDocumentos();
}

// Filtrar por estado
function filtrarEstado(estadoDoc) {
    estado.estado_doc = estadoDoc;
    estado.pagina = 1;
    document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('active'));
    cargarDocumentos();
}

// Búsqueda
let busquedaTimeout;
document.getElementById('buscar-input')?.addEventListener('input', function(e) {
    clearTimeout(busquedaTimeout);
    busquedaTimeout = setTimeout(() => {
        estado.buscar = e.target.value;
        estado.pagina = 1;
        cargarDocumentos();
    }, 300);
});

// Toggle all checkboxes
function toggleAllCheckboxes(checkbox) {
    document.querySelectorAll('.table-checkbox').forEach(cb => {
        cb.checked = checkbox.checked;
    });
}

// Ver documento
function verDocumento(tipo, id) {
    window.location.href = `/documentos/${tipo}/${id}/`;
}

// Editar documento
function editarDocumento(tipo, id) {
    window.location.href = `/documentos/${tipo}/${id}/editar/`;
}

// Descargar documento
function descargarDocumento(tipo, id) {
    window.location.href = `/documentos/${tipo}/${id}/solicitar-descarga/`;
}

// Ver historial de versiones
function verHistorial(tipo, id) {
    window.location.href = `/documentos/${tipo}/${id}/versiones/`;
}

// Eliminar documento (marcar como obsoleto)
function eliminarDocumento(tipo, id) {
    window.location.href = `/documentos/${tipo}/${id}/eliminar/`;
}

// Abrir modal de cambiar estado
function abrirModalEstado(tipo, docId, estadoActual) {
    document.getElementById('modal-estado-doc-id').value = docId;
    document.getElementById('modal-estado-tipo').value = tipo;
    document.getElementById('modal-estado-nuevo').value = estadoActual === 'activo' ? 'obsoleto' : 'activo';
    document.getElementById('modal-estado-titulo').textContent =
        estadoActual === 'activo' ? 'Inhabilitar Documento' : 'Habilitar Documento';
    document.getElementById('modal-estado-motivo').value = '';
    document.getElementById('modal-estado-texto').value = '';
    document.getElementById('modal-estado').classList.add('active');
}

// Confirmar cambio de estado
async function confirmarCambioEstado() {
    const docId = document.getElementById('modal-estado-doc-id').value;
    const tipo = document.getElementById('modal-estado-tipo').value;
    const nuevoEstado = document.getElementById('modal-estado-nuevo').value;
    const motivoSeleccion = document.getElementById('modal-estado-motivo').value;
    const motivoTexto = document.getElementById('modal-estado-texto').value;

    if (!motivoSeleccion) {
        mostrarAlerta('warning', 'Debe seleccionar un motivo');
        return;
    }

    try {
        const response = await fetch(`/documentos/api/${tipo}/${docId}/cambiar-estado/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify({
                estado: nuevoEstado,
                motivo_seleccion: motivoSeleccion,
                motivo_texto: motivoTexto
            })
        });

        const data = await response.json();
        if (data.success) {
            mostrarAlerta('success', data.mensaje);
            cerrarModal('modal-estado');
            cargarDocumentos();
        } else {
            mostrarAlerta('danger', data.error || 'Error al cambiar estado');
        }
    } catch (error) {
        mostrarAlerta('danger', 'Error de conexión');
    }
}

// Abrir modal de transferir
async function abrirModalTransferir(tipo, docId) {
    document.getElementById('modal-transferir-doc-id').value = docId;
    document.getElementById('modal-transferir-tipo').value = tipo;

    // Cargar usuarios del departamento
    try {
        const response = await fetch('/documentos/api/usuarios-departamento/');
        const data = await response.json();
        const select = document.getElementById('modal-transferir-usuario');
        select.innerHTML = '<option value="">Seleccione un usuario</option>';
        data.usuarios.forEach(u => {
            select.innerHTML += `<option value="${u.id}">${escapeHtml(u.nombre)}</option>`;
        });
    } catch (error) {
        console.error('Error cargando usuarios:', error);
    }

    document.getElementById('modal-transferir').classList.add('active');
}

// Confirmar transferencia
async function confirmarTransferencia() {
    const docId = document.getElementById('modal-transferir-doc-id').value;
    const tipo = document.getElementById('modal-transferir-tipo').value;
    const nuevoId = document.getElementById('modal-transferir-usuario').value;

    if (!nuevoId) {
        mostrarAlerta('warning', 'Debe seleccionar un usuario');
        return;
    }

    try {
        const response = await fetch(`/documentos/api/${tipo}/${docId}/transferir/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify({ nuevo_propietario_id: parseInt(nuevoId) })
        });

        const data = await response.json();
        if (data.success) {
            mostrarAlerta('success', data.mensaje);
            cerrarModal('modal-transferir');
            cargarDocumentos();
        } else {
            mostrarAlerta('danger', data.error || 'Error al transferir');
        }
    } catch (error) {
        mostrarAlerta('danger', 'Error de conexión');
    }
}

// Cerrar modal
function cerrarModal(id) {
    document.getElementById(id).classList.remove('active');
}

// Mostrar alerta
function mostrarAlerta(tipo, mensaje) {
    const container = document.getElementById('alert-container');
    const iconos = {
        success: 'fa-solid fa-circle-check',
        warning: 'fa-solid fa-triangle-exclamation',
        danger: 'fa-solid fa-circle-xmark',
        info: 'fa-solid fa-circle-info'
    };

    const alerta = document.createElement('div');
    alerta.className = `alert alert--${tipo}`;
    alerta.innerHTML = `
        <span class="alert-icon"><i class="${iconos[tipo]}"></i></span>
        <div class="alert-content">${escapeHtml(mensaje)}</div>
        <button class="alert-close" onclick="this.parentElement.remove()">&times;</button>
    `;

    container.appendChild(alerta);

    // Auto-eliminar después de 5 segundos
    setTimeout(() => {
        if (alerta.parentElement) {
            alerta.remove();
        }
    }, 5000);
}

// Utilidades
function escapeHtml(value) {
    // Escapa datos de usuario/ servidor antes de interpolarlos en innerHTML,
    // previniendo XSS almacenado (p.ej. un nombre de documento malicioso).
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function getEstadoBadge(estado) {
    const badges = {
        'borrador': 'warning',
        'activo': 'success',
        'obsoleto': 'danger'
    };
    return badges[estado] || 'primary';
}

// Cerrar dropdowns al hacer clic fuera
document.addEventListener('click', function(e) {
    if (!e.target.closest('.dropdown') && !e.target.closest('.actions-dropdown')) {
        document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('active'));
        document.querySelectorAll('.actions-dropdown-menu').forEach(d => {
            d.classList.remove('active');
            d.style.top = '';
            d.style.right = '';
        });
    }
});

// Cerrar dropdowns al hacer scroll
document.addEventListener('scroll', function() {
    document.querySelectorAll('.actions-dropdown-menu').forEach(d => {
        d.classList.remove('active');
        d.style.top = '';
        d.style.right = '';
    });
}, true);

// Toggle dropdown de acciones de la tabla
function toggleActionsDropdown(event, docId) {
    event.stopPropagation();
    const dropdown = document.getElementById(`actions-${docId}`);
    const menu = dropdown.querySelector('.actions-dropdown-menu');
    const isOpen = menu.classList.contains('active');

    // Cerrar todos los otros dropdowns de acciones
    document.querySelectorAll('.actions-dropdown-menu').forEach(d => {
        d.classList.remove('active');
        d.style.top = '';
        d.style.right = '';
    });

    if (!isOpen) {
        const rect = dropdown.getBoundingClientRect();
        menu.style.top = (rect.bottom + 4) + 'px';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.classList.add('active');
    }
}

// Inicializar
document.addEventListener('DOMContentLoaded', function() {
    cargarContadores();
    cargarDocumentos();
});

// Mapeo de tipos legibles
const TIPOS_LABELS = {
    'proceso': 'Procesos',
    'procedimiento': 'Procedimientos',
    'formulario': 'Formularios',
    'manual_calidad': 'Manuales de Calidad',
    'politica_calidad': 'Políticas de Calidad',
    'objetivos': 'Objetivos',
    'plan_calidad': 'Planes de Calidad',
    'mapa_procesos': 'Mapas de Procesos',
    'descripcion_cargos': 'Descripciones de Cargos',
    'programa_seguridad': 'Programas de Seguridad',
    'organigrama': 'Organigramas',
    'descripcion_procesos': 'Descripciones de Procesos',
    'normas': 'Normas',
    'procedimientos_generales': 'Procedimientos Generales',
    'instructivo_llenado': 'Instructivos de Llenado',
    'procedimientos_especificos': 'Procedimientos Específicos',
    'instrucciones_trabajo': 'Instrucciones de Trabajo',
};

const ESTADOS_LABELS = {
    'borrador': 'Borrador',
    'en_revision': 'En Revisión',
    'activo': 'Activo',
    'obsoleto': 'Obsoleto',
};

// Cargar contadores y poblar dropdowns
async function cargarContadores() {
    try {
        const response = await fetch('/documentos/api/contadores/');
        const data = await response.json();

        // Poblar dropdown de tipos
        const menuTipo = document.getElementById('menu-tipo');
        if (menuTipo) {
            let html = '<a class="dropdown-item" href="#" onclick="filtrarTipo(\'\')">Todos</a>';
            const tipos = Object.entries(data.por_tipo);
            const tieneRegistros = tipos.some(([_, count]) => count > 0);

            if (!tieneRegistros) {
                html += '<a class="dropdown-item disabled" href="#">No hay documentos</a>';
            } else {
                for (const [tipo, count] of tipos) {
                    const label = TIPOS_LABELS[tipo] || tipo;
                    if (count > 0) {
                        html += `<a class="dropdown-item" href="#" onclick="filtrarTipo('${tipo}')">${label} <span class="dropdown-count">${count}</span></a>`;
                    } else {
                        html += `<a class="dropdown-item disabled" href="#" title="No hay registros">${label}</a>`;
                    }
                }
            }
            menuTipo.innerHTML = html;
        }

        // Poblar dropdown de estados
        const menuEstado = document.getElementById('menu-estado');
        if (menuEstado) {
            let html = '<a class="dropdown-item" href="#" onclick="filtrarEstado(\'\')">Todos</a>';
            const estados = Object.entries(data.por_estado);
            const tieneRegistros = estados.some(([_, count]) => count > 0);

            if (!tieneRegistros) {
                html += '<a class="dropdown-item disabled" href="#">No hay documentos</a>';
            } else {
                for (const [estadoKey, count] of estados) {
                    const label = ESTADOS_LABELS[estadoKey] || estadoKey;
                    if (count > 0) {
                        html += `<a class="dropdown-item" href="#" onclick="filtrarEstado('${estadoKey}')">${label} <span class="dropdown-count">${count}</span></a>`;
                    } else {
                        html += `<a class="dropdown-item disabled" href="#" title="No hay registros">${label}</a>`;
                    }
                }
            }
            menuEstado.innerHTML = html;
        }
    } catch (error) {
        console.error('Error cargando contadores:', error);
    }
}
