/**
 * Tipos de la API. NO se escriben a mano: salen de `esquema.ts`, generado del OpenAPI de FastAPI (`make tipos`).
 * Si la API cambia y nadie regenera, fallan `make lint` (web) y la prueba `test_openapi_al_dia` (backend).
 * Dinero y galones son TEXTO decimal.
 */
import type { components } from "./esquema";

type S = components["schemas"];

export type Decimal = string;
export type Usuario = S["UsuarioSalida"];
export type Rol = Usuario["rol"];
export type Token = S["TokenSalida"];
export type Surtidor = S["SurtidorSalida"];
export type Producto = S["ProductoSalida"];
export type Catalogos = S["CatalogosSalida"];
export type Pendiente = S["PendienteSalida"];
export type OrigenTipo = Pendiente["origen_tipo"];
export type VentaManualEntrada = S["VentaManualEntrada"];
export type ClienteEntrada = S["ClienteEntrada"];
export type RegistroPublicoEntrada = S["RegistroPublicoEntrada"];
export type ConfiguracionPublica = S["ConfiguracionPublicaSalida"];
export type CaptchaConfig = S["CaptchaSalida"];
export type Cliente = S["ClienteSalida"];
export type FacturaEntrada = S["FacturaEntrada"];
export type MedioPago = FacturaEntrada["medio_pago"];
export type Factura = S["FacturaSalida"];
export type EstadoFactura = Factura["estado"];
export type FilaVentas = S["FilaVentasSalida"];
export type FilaTurno = S["FilaTurnoSalida"];
export type Anomalia = S["AnomaliaSalida"];
export type Severidad = Anomalia["severidad"];
export type Venta = S["VentaSalida"];
export type BusquedaVentas = S["BusquedaVentasSalida"];
export type UsuarioListado = S["UsuarioListadoSalida"];
export type ClientesPagina = S["ClientesPaginaSalida"];
export type Vehiculo = S["VehiculoSalida"];
export type VehiculoEntrada = S["VehiculoEntrada"];
export type VehiculosPagina = S["VehiculosPaginaSalida"];
export type Importacion = S["ImportacionSalida"];
export type PosibleDuplicado = S["PosibleDuplicadoSalida"];
