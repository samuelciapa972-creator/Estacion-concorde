/**
 * Dinero y galones como TEXTO decimal, nunca como número de punto flotante (la API los envía así: "29757",
 * "1.861"). Se formatean partiendo el texto: la parte entera con BigInt + Intl (es-CO) y los decimales tal cual.
 */

const ENTERO = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
const DECIMAL_API = /^(-?)(\d+)(?:\.(\d+))?$/;

function partes(valor) {
  const m = DECIMAL_API.exec(String(valor).trim());
  if (!m) throw new Error(`valor decimal inválido: ${JSON.stringify(valor)}`);
  return { signo: m[1] ?? '', entero: m[2] ?? '0', decimales: m[3] ?? '' };
}

function agrupar(entero) {
  return ENTERO.format(BigInt(entero));
}

/** Pesos sin centavos: "29757" -> "$ 29.757". Los centavos (si llegan) se muestran, no se redondean. */
export function pesos(valor) {
  const { signo, entero, decimales } = partes(valor);
  const frac = decimales.replace(/0+$/, '');
  return `${signo}$ ${agrupar(entero)}${frac ? ',' + frac : ''}`;
}

/** Galones con exactamente 3 decimales: "1.861" -> "1,861"; "12.5" -> "12,500". */
export function galones(valor) {
  const { signo, entero, decimales } = partes(valor);
  if (decimales.length > 3 && /[1-9]/.test(decimales.slice(3))) {
    throw new Error(`galones con más de 3 decimales: ${valor}`);
  }
  return `${signo}${agrupar(entero)},${decimales.slice(0, 3).padEnd(3, '0')}`;
}

/** Galones digitados ("1,861" o "1.861") -> texto para la API ("1.861"). null si no es válido (hasta 3 decimales). */
export function leerGalones(texto) {
  const m = /^(\d{1,6})(?:[.,](\d{1,3}))?$/.exec(String(texto).trim());
  if (!m) return null;
  const entero = (m[1] ?? '').replace(/^0+(?=\d)/, '');
  const valor = m[2] ? `${entero}.${m[2]}` : entero;
  return /[1-9]/.test(valor) ? valor : null;
}

/** Pesos digitados ("29.757", "29757", "$ 29.757") -> texto entero para la API ("29757"). Sin centavos. */
export function leerPesos(texto) {
  const limpio = String(texto).trim().replace(/^\$\s*/, '');
  if (!/^(\d{1,3}(\.\d{3})+|\d+)$/.test(limpio)) return null;
  const valor = limpio.replace(/\./g, '').replace(/^0+(?=\d)/, '');
  return valor === '0' ? null : valor;
}

const HORA_COLOMBIA = new Intl.DateTimeFormat('es-CO', {
  timeZone: 'America/Bogota',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/**
 * Fecha y hora de la estación ("2026-09-28T02:18:00" -> "28/09/2026 02:18"). Las fechas locales sin zona se
 * muestran tal cual; las que traen zona (UTC con "Z") se pasan a hora de Colombia.
 */
export function fechaHora(iso) {
  if (!iso) return '—';
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(iso)) {
    const d = new Date(iso);
    if (!Number.isNaN(d.getTime())) {
      const p = Object.fromEntries(HORA_COLOMBIA.formatToParts(d).map((x) => [x.type, x.value]));
      return `${p.day ?? ''}/${p.month ?? ''}/${p.year ?? ''} ${p.hour ?? ''}:${p.minute ?? ''}`;
    }
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : iso;
}

export function fecha(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

/** Hoy en la estación como "AAAA-MM-DD". */
export function hoyIso(ahora = new Date()) {
  const d = (n) => String(n).padStart(2, '0');
  return `${ahora.getFullYear()}-${d(ahora.getMonth() + 1)}-${d(ahora.getDate())}`;
}

/** Un día como "AAAA-MM-DDT00:00:00" (hora local sin zona, como lo pide la API). */
export function inicioDelDia(d) {
  return `${hoyIso(d)}T00:00:00`;
}

/**
 * Rango [desde, hasta) que termina mañana a las 00:00, en hora local sin zona (como lo pide la API).
 * `periodo`: 'hoy', 'semana' (últimos 7 días), 'mes' (este mes) o 'anio' (últimos 12 meses).
 */
export function rangoHastaHoy(periodo, ahora = new Date()) {
  const [a, m, d] = [ahora.getFullYear(), ahora.getMonth(), ahora.getDate()];
  const desde = { hoy: new Date(a, m, d), semana: new Date(a, m, d - 6), mes: new Date(a, m, 1), anio: new Date(a, m - 11, 1) }[periodo];
  if (!desde) throw new Error(`periodo desconocido: ${periodo}`);
  return { desde: inicioDelDia(desde), hasta: inicioDelDia(new Date(a, m, d + 1)) };
}

/** Suma exacta de decimales en texto: escala a enteros con BigInt. */
export function sumar(valores, decimales) {
  const escala = 10n ** BigInt(decimales);
  let total = 0n;
  for (const v of valores) {
    const { signo, entero, decimales: frac } = partes(v);
    if (frac.length > decimales && /[1-9]/.test(frac.slice(decimales))) {
      throw new Error(`más de ${decimales} decimales: ${v}`);
    }
    const n = BigInt(entero) * escala + BigInt(frac.slice(0, decimales).padEnd(decimales, '0') || '0');
    total += signo ? -n : n;
  }
  const negativo = total < 0n;
  const abs = negativo ? -total : total;
  const entero = (abs / escala).toString();
  const frac = decimales ? '.' + (abs % escala).toString().padStart(decimales, '0') : '';
  return `${negativo ? '-' : ''}${entero}${frac}`;
}

/** "EN_PROCESO" → "En proceso": los valores de la API se muestran como texto normal, no gritados. */
export function oracion(texto) {
  if (typeof texto !== 'string') return texto;
  const t = texto.replaceAll('_', ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
