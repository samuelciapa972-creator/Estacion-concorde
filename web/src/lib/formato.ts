/**
 * Dinero y galones como TEXTO decimal, nunca como número de punto flotante (la API los envía así: "29757",
 * "1.861"). Se formatean partiendo el texto: la parte entera con BigInt + Intl (es-CO) y los decimales tal cual.
 */

const ENTERO = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const DECIMAL_API = /^(-?)(\d+)(?:\.(\d+))?$/;

function partes(valor: string): { signo: string; entero: string; decimales: string } {
  const m = DECIMAL_API.exec(valor.trim());
  if (!m) throw new Error(`valor decimal inválido: ${JSON.stringify(valor)}`);
  return { signo: m[1] ?? "", entero: m[2] ?? "0", decimales: m[3] ?? "" };
}

function agrupar(entero: string): string {
  return ENTERO.format(BigInt(entero));
}

/** Pesos sin centavos: "29757" -> "$ 29.757". Los centavos (si llegan) se muestran, no se redondean. */
export function pesos(valor: string): string {
  const { signo, entero, decimales } = partes(valor);
  const frac = decimales.replace(/0+$/, "");
  return `${signo}$ ${agrupar(entero)}${frac ? "," + frac : ""}`;
}

/** Galones con exactamente 3 decimales: "1.861" -> "1,861"; "12.5" -> "12,500". */
export function galones(valor: string): string {
  const { signo, entero, decimales } = partes(valor);
  if (decimales.length > 3 && /[1-9]/.test(decimales.slice(3))) {
    throw new Error(`galones con más de 3 decimales: ${valor}`);
  }
  return `${signo}${agrupar(entero)},${decimales.slice(0, 3).padEnd(3, "0")}`;
}

/**
 * Galones digitados por el bombero -> texto para la API ("1,861" o "1.861" -> "1.861").
 * Acepta coma o punto como separador decimal (uno solo) y hasta 3 decimales. Devuelve null si no es válido.
 * Un despacho de 1.000 galones o más no existe en la pista, así que "1.861" no es ambiguo.
 */
export function leerGalones(texto: string): string | null {
  const m = /^(\d{1,6})(?:[.,](\d{1,3}))?$/.exec(texto.trim());
  if (!m) return null;
  const entero = (m[1] ?? "").replace(/^0+(?=\d)/, "");
  const valor = m[2] ? `${entero}.${m[2]}` : entero;
  return /[1-9]/.test(valor) ? valor : null;
}

/**
 * Pesos digitados -> texto entero para la API ("29.757", "29757", "$ 29.757" -> "29757").
 * No acepta centavos ni coma decimal. Si trae puntos, deben ser separadores de miles bien puestos.
 */
export function leerPesos(texto: string): string | null {
  const limpio = texto.trim().replace(/^\$\s*/, "");
  if (!/^(\d{1,3}(\.\d{3})+|\d+)$/.test(limpio)) return null;
  const valor = limpio.replace(/\./g, "").replace(/^0+(?=\d)/, "");
  return valor === "0" ? null : valor;
}

const HORA_COLOMBIA = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * Fecha y hora de la estación ("2026-09-28T02:18:00" -> "28/09/2026 02:18"). Las fechas locales sin zona se
 * muestran tal cual; las que traen zona (p. ej. `creada_en` de anomalías, en UTC con "Z") se pasan a hora de
 * Colombia, sin depender de la zona configurada en la tablet.
 */
export function fechaHora(iso: string): string {
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(iso)) {
    const d = new Date(iso);
    if (!Number.isNaN(d.getTime())) {
      const p = Object.fromEntries(HORA_COLOMBIA.formatToParts(d).map((x) => [x.type, x.value]));
      return `${p.day ?? ""}/${p.month ?? ""}/${p.year ?? ""} ${p.hour ?? ""}:${p.minute ?? ""}`;
    }
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : iso;
}

export function fecha(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

/** Hoy en la estación como "AAAA-MM-DD" (la tablet está en hora de Colombia). */
export function hoyIso(ahora: Date = new Date()): string {
  const d = (n: number) => String(n).padStart(2, "0");
  return `${ahora.getFullYear()}-${d(ahora.getMonth() + 1)}-${d(ahora.getDate())}`;
}

/** Ahora en la estación como "AAAA-MM-DDTHH:MM:SS", sin zona. */
export function ahoraIso(ahora: Date = new Date()): string {
  const d = (n: number) => String(n).padStart(2, "0");
  return `${hoyIso(ahora)}T${d(ahora.getHours())}:${d(ahora.getMinutes())}:${d(ahora.getSeconds())}`;
}

/** Suma exacta de decimales en texto (para totales de reportes): escala a enteros con BigInt. */
export function sumar(valores: string[], decimales: number): string {
  const escala = 10n ** BigInt(decimales);
  let total = 0n;
  for (const v of valores) {
    const { signo, entero, decimales: frac } = partes(v);
    if (frac.length > decimales && /[1-9]/.test(frac.slice(decimales))) {
      throw new Error(`más de ${decimales} decimales: ${v}`);
    }
    const n = BigInt(entero) * escala + BigInt(frac.slice(0, decimales).padEnd(decimales, "0") || "0");
    total += signo ? -n : n;
  }
  const negativo = total < 0n;
  const abs = negativo ? -total : total;
  const entero = (abs / escala).toString();
  const frac = decimales ? "." + (abs % escala).toString().padStart(decimales, "0") : "";
  return `${negativo ? "-" : ""}${entero}${frac}`;
}
