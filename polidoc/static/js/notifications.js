document.addEventListener('DOMContentLoaded', function () {
    const notificationsBtn = document.getElementById('notifications-btn');
    const notifCount = document.getElementById('notif-count');

    if (!notificationsBtn) return;

    // Crear dropdown
    const dropdown = document.createElement('div');
    dropdown.className = 'notifications-dropdown';
    dropdown.id = 'notifications-dropdown';

    const header = document.createElement('div');
    header.className = 'notifications-header';

    const headerTitle = document.createElement('h4');
    headerTitle.textContent = 'Notificaciones';
    header.appendChild(headerTitle);

    const markAllBtn = document.createElement('button');
    markAllBtn.className = 'btn btn--ghost btn--sm';
    markAllBtn.id = 'mark-all-read';
    markAllBtn.textContent = 'Marcar todas como leídas';
    header.appendChild(markAllBtn);

    dropdown.appendChild(header);

    const list = document.createElement('div');
    list.className = 'notifications-list';
    list.id = 'notifications-list';
    const loading = document.createElement('div');
    loading.className = 'notifications-loading';
    loading.textContent = 'Cargando...';
    list.appendChild(loading);
    dropdown.appendChild(list);

    notificationsBtn.parentElement.appendChild(dropdown);

    // Toggle
    notificationsBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        dropdown.classList.toggle('active');
        if (dropdown.classList.contains('active')) {
            loadNotifications();
        }
    });

    // Cerrar al hacer click fuera
    document.addEventListener('click', function (e) {
        if (!dropdown.contains(e.target) && !notificationsBtn.contains(e.target)) {
            dropdown.classList.remove('active');
        }
    });

    // Marcar todas como leídas
    markAllBtn.addEventListener('click', function () {
        fetch('/api/notificaciones/leer-todas/', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCookie('csrftoken') },
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') loadNotifications();
            })
            .catch(function () { });
    });

    // Cargar inicialmente y cada 30s (pausar si pestaña oculta)
    loadNotifications();
    var notifInterval = setInterval(function () {
        if (!document.hidden) loadNotifications();
    }, 30000);

    // Limpiar interval si la pestaña se oculta largo rato
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            clearInterval(notifInterval);
            notifInterval = setInterval(function () {
                if (!document.hidden) loadNotifications();
            }, 60000);
        }
    });

    // ── Cargar notificaciones ──
    function loadNotifications() {
        fetch('/api/notificaciones/')
            .then(function (r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function (data) {
                notifCount.textContent = data.no_leidas;
                notifCount.style.display = data.no_leidas > 0 ? 'flex' : 'none';

                var listEl = document.getElementById('notifications-list');
                if (!listEl) return;
                listEl.innerHTML = '';

                if (!data.notificaciones || data.notificaciones.length === 0) {
                    var empty = document.createElement('div');
                    empty.className = 'notifications-empty';
                    empty.textContent = 'No hay notificaciones';
                    listEl.appendChild(empty);
                    return;
                }

                data.notificaciones.forEach(function (n) {
                    listEl.appendChild(createNotificationItem(n));
                });
            })
            .catch(function () {
                var listEl = document.getElementById('notifications-list');
                if (listEl) {
                    listEl.innerHTML = '';
                    var err = document.createElement('div');
                    err.className = 'notifications-empty';
                    err.textContent = 'Error al cargar notificaciones';
                    listEl.appendChild(err);
                }
            });
    }

    // ── Construir item de notificación (XSS-safe) ──
    function createNotificationItem(n) {
        var item = document.createElement('div');
        item.className = 'notification-item' + (n.leida ? '' : ' unread');
        item.dataset.id = n.id;
        item.dataset.url = n.url_accion || '';

        // Ícono
        var iconWrap = document.createElement('div');
        iconWrap.className = 'notification-icon notification-icon--' + escapeAttr(n.tipo);
        var iconInner = document.createElement('i');
        iconInner.className = 'fa-solid ' + getNotificationIcon(n.tipo);
        iconWrap.appendChild(iconInner);
        item.appendChild(iconWrap);

        // Contenido
        var content = document.createElement('div');
        content.className = 'notification-content';

        var title = document.createElement('div');
        title.className = 'notification-title';
        title.textContent = n.titulo;
        if (n.es_de_otro_departamento) {
            var badge = document.createElement('span');
            badge.className = 'badge-dept';
            badge.textContent = 'Otro depto';
            title.appendChild(badge);
        }
        content.appendChild(title);

        var msg = document.createElement('div');
        msg.className = 'notification-message';
        msg.textContent = n.mensaje;
        content.appendChild(msg);

        var time = document.createElement('div');
        time.className = 'notification-time';
        time.textContent = n.fecha_creacion;
        content.appendChild(time);

        // Botón "Evaluar" para tipos que requieren acción
        if (n.url_accion && requiresAction(n.tipo)) {
            var actions = document.createElement('div');
            actions.className = 'notification-actions';

            var btn = document.createElement('button');
            btn.className = 'notif-btn notif-btn--view';
            btn.textContent = ' Evaluar';
            var btnIcon = document.createElement('i');
            btnIcon.className = 'fa-solid fa-eye';
            btn.insertBefore(btnIcon, btn.firstChild);
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                window.location.href = n.url_accion;
            });
            actions.appendChild(btn);
            content.appendChild(actions);
        }

        item.appendChild(content);

        // Click en el item → marcar leído + navegar
        item.addEventListener('click', function () {
            markAsRead(n.id);
            if (n.url_accion) window.location.href = n.url_accion;
        });

        return item;
    }

    function requiresAction(tipo) {
        return tipo === 'solicitud_descarga' ||
               tipo === 'solicitud_aprobacion' ||
               tipo === 'planilla_pendiente';
    }

    function markAsRead(id) {
        fetch('/api/notificaciones/' + id + '/leer/', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCookie('csrftoken') },
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') loadNotifications();
            })
            .catch(function () { });
    }

    // ── Mapa de íconos (font-awesome classes) ──
    function getNotificationIcon(tipo) {
        var icons = {
            solicitud_descarga: 'fa-download',
            aprobacion_descarga: 'fa-circle-check',
            rechazo_descarga: 'fa-circle-xmark',
            documento_asignado: 'fa-file-circle-check',
            solicitud_aprobacion: 'fa-file-circle-plus',
            aprobacion_documento: 'fa-circle-check',
            rechazo_documento: 'fa-circle-xmark',
            planilla_pendiente: 'fa-file-signature',
            nueva_version: 'fa-code-branch',
            propiedad_transferida: 'fa-right-left',
            documento_distribuido: 'fa-share-nodes',
            distribucion_revocada: 'fa-ban',
            cambio_vigencia: 'fa-calendar-check',
            aprobacion_escalada: 'fa-hourglass-half',
            edicion_formato: 'fa-layer-group',
            sistema: 'fa-bell',
        };
        return icons[tipo] || icons.sistema;
    }

    // ── Helpers ──
    function escapeAttr(str) {
        return String(str).replace(/[^a-z0-9_-]/gi, '');
    }

    function getCookie(name) {
        var cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            var cookies = document.cookie.split(';');
            for (var i = 0; i < cookies.length; i++) {
                var cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === name + '=') {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
});
