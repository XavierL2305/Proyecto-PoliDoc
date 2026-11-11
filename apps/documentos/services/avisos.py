"""Avisos de clasificación de la información (correo, UI, constancia, PDF).

Un único punto de verdad para el texto que acompaña cada distribución
(ISO 9001:7.5.3 "distribución"). El texto legal de confidencial/restringido
está pendiente de validación por Asesoría Legal (CONTEXT §35).

Colores del banner de correo (aprobados por la usuaria):

* público    → verde institucional
* interno    → azul
* confidencial → ámbar
* restringido  → rojo
"""

#: ``(fondo, borde_color)`` por clasificación para el banner HTML.
COLORES = {
    'publico': ('#e8f5e9', '#2e7d32'),
    'interno': ('#e3f2fd', '#1565c0'),
    'confidencial': ('#fff3e0', '#e65100'),
    'restringido': ('#ffebee', '#b71c1c'),
}

#: ``(título, cuerpo)`` por clasificación.
AVISOS = {
    'publico': (
        'Documento de uso institucional.',
        'La información de este documento está destinada al personal de la '
        'Policlínica Táchira y forma parte del Sistema de Gestión de la '
        'Calidad. Su clasificación como pública permite su consulta por las '
        'áreas de la institución; no implica autorización para difundirla, '
        'reproducirla o distribuirla fuera de la Policlínica Táchira sin la '
        'autorización correspondiente.',
    ),
    'interno': (
        'Documento de uso interno.',
        'La información de este documento es de uso interno de la Policlínica '
        'Táchira. Por favor, evite compartirlo fuera de la institución; su '
        'distribución no autorizada puede afectar la confidencialidad y el '
        'control de la información del Sistema de Gestión de la Calidad.',
    ),
    'confidencial': (
        'Documento confidencial.',
        'Este documento contiene información confidencial de la Policlínica '
        'Táchira destinada exclusivamente a las personas autorizadas. Quien '
        'lo recibe se obliga a mantenerlo en reserva, a no reproducirlo ni '
        'compartirlo sin autorización escrita, y a custodiarlo o eliminarlo '
        'cuando concluya la finalidad para la que se le entregó, conforme a '
        'la Ley de Protección de Datos Personales (Gaceta Oficial N° 38.296) '
        'y a los procedimientos del Sistema de Gestión de la Calidad. El '
        'acceso o la divulgación indebida generan responsabilidad '
        'administrativa y, cuando corresponda, penal (Ley contra los Delitos '
        'Informáticos).',
    ),
    'restringido': (
        'Documento de uso restringido.',
        'Este documento contiene información de acceso restringido: su '
        'distribución se realiza exclusivamente por enlace seguro de '
        'POLIDOC, sin adjunto. Queda terminantemente prohibido copiar, '
        'imprimir, reenviar, divulgar o usar su contenido fuera de la '
        'finalidad autorizada, incluso entre usuarios internos, salvo '
        'autorización expresa de la Dirección. El acceso queda registrado y '
        'auditado; su incumplimiento genera responsabilidad conforme a la '
        'Ley de Protección de Datos Personales (Gaceta Oficial N° 38.296), '
        'la Ley contra los Delitos Informáticos y los procedimientos del '
        'Sistema de Gestión de la Calidad.',
    ),
}

_CLASIFICACION_POR_DEFECTO = 'interno'


def _par(clasificacion):
    return AVISOS.get(clasificacion or _CLASIFICACION_POR_DEFECTO,
                      AVISOS[_CLASIFICACION_POR_DEFECTO])


def aviso_texto(clasificacion):
    """Aviso en texto plano (``título\\n\\ncuerpo``) para correos/cuerpo."""
    titulo, cuerpo = _par(clasificacion)
    return f'{titulo}\n\n{cuerpo}'


def aviso_html(clasificacion):
    """Aviso en HTML seguro (título en negrita + cuerpo)."""
    from django.utils.safestring import mark_safe

    titulo, cuerpo = _par(clasificacion)
    return mark_safe(f'<strong>{titulo}</strong> {cuerpo}')


def banner_html(clasificacion):
    """Franja de color con el aviso, en HTML apto para clientes de correo.

    Tabla inline (email-safe: sin divs ni clases externas) con franja
    izquierda de 6px y colores por clasificación.
    """
    from django.utils.safestring import mark_safe

    fondo, borde = COLORES.get(clasificacion or _CLASIFICACION_POR_DEFECTO,
                               COLORES[_CLASIFICACION_POR_DEFECTO])
    titulo, cuerpo = _par(clasificacion)
    return mark_safe(
        '<table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" style="border-collapse:collapse;margin:16px 0;">'
        '<tr><td style="background:{fondo};border-left:6px solid {borde};'
        'padding:12px 16px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:1.45;color:#1a1a1a;border-radius:4px;">'
        '<strong style="color:{borde};">{titulo}</strong><br>{cuerpo}'
        '</td></tr></table>'.format(
            fondo=fondo, borde=borde, titulo=titulo, cuerpo=cuerpo,
        )
    )


def clasificacion_de_documento(documento):
    """Clasificación efectiva de un documento (o ``interno`` si no hay)."""
    if documento is None:
        return _CLASIFICACION_POR_DEFECTO
    return (documento.clasificacion or _CLASIFICACION_POR_DEFECTO).lower()


def cuerpo_email_html(lineas, clasificacion):
    """Cuerpo HTML de un correo: párrafos + banner de clasificación + firma.

    Args:
        lineas: líneas de texto plano (se convierten en párrafos).
        clasificacion: clave de :data:`COLORES`/data:`AVISOS`.

    Returns:
        str HTML seguro apto para clientes de correo.
    """
    from django.utils.safestring import mark_safe

    parrafos = []
    for linea in lineas:
        if linea == '':
            continue
        parrafos.append(f'<p style="margin:0 0 10px;">{linea}</p>')
    return mark_safe(
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1a1a1a;line-height:1.5;">'
        + ''.join(parrafos)
        + banner_html(clasificacion)
        + '<p style="margin:16px 0 0;font-size:12px;color:#666;">— POLIDOC</p>'
        '</div>'
    )
