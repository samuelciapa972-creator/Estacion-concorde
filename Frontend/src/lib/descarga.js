/** Descarga un archivo que llegó como Blob (la API exige el token: no sirve un enlace directo). */
export function guardarArchivo(blob, nombre) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}
