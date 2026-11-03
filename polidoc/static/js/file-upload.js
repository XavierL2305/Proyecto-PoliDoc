/* Comportamiento compartido del componente de subida de archivo:
   - muestra el nombre del archivo seleccionado y el estado `has-file`;
   - permite arrastrar y soltar sobre `.file-upload-wrapper`.
   El markup asociado vive en documentos/_file_upload_field.html. */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('input[type="file"]').forEach(function (input) {
      var label = document.getElementById('label-' + input.id);
      var filename = document.getElementById('filename-' + input.id);

      if (!label || !filename) return;

      input.addEventListener('change', function () {
        if (input.files && input.files.length > 0) {
          filename.textContent = input.files[0].name;
          label.classList.add('has-file');
        } else {
          filename.textContent = 'Seleccione un archivo';
          label.classList.remove('has-file');
        }
      });

      // Estado inicial (edición con archivo ya cargado).
      if (input.files && input.files.length > 0) {
        filename.textContent = input.files[0].name;
        label.classList.add('has-file');
      }
    });

    // Prevenir el comportamiento por defecto del navegador una sola vez
    // por página (aunque existan varios wrappers).
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function (evt) {
      window.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
    });

    document.querySelectorAll('.file-upload-wrapper').forEach(function (wrapper) {
      var dropLabel = wrapper.querySelector('.file-upload-label');
      var fileInput = wrapper.querySelector('input[type="file"]');
      if (!dropLabel || !fileInput) return;

      // Contador para evitar flicker al entrar/salir de elementos hijos.
      var dragCounter = 0;

      dropLabel.addEventListener('dragenter', function (e) {
        e.preventDefault();
        dragCounter++;
        dropLabel.classList.add('drag-over');
      });

      dropLabel.addEventListener('dragleave', function (e) {
        e.preventDefault();
        dragCounter--;
        if (dragCounter === 0) {
          dropLabel.classList.remove('drag-over');
        }
      });

      dropLabel.addEventListener('drop', function (e) {
        e.preventDefault();
        dragCounter = 0;
        dropLabel.classList.remove('drag-over');

        var files = e.dataTransfer.files;
        if (files.length > 0) {
          fileInput.files = files;
          fileInput.dispatchEvent(new Event('change'));
        }
      });
    });
  });
})();
