# Borrador DIAN (referencia, no es código del sistema)

Plantillas de nota crédito y nota débito del primer borrador del cliente DIAN (`backend.zip`, `src/Dian/`,
4–5 de octubre de 2026). Se conservan solo como referencia para cuando el motor genere notas
(`CustomizationID` 20/30, `BillingReference`, `DiscrepancyResponse`, CUDE).

Lo demás de ese borrador quedó descartado: la firma era XMLDSig simple (la DIAN exige XAdES-EPES, ver
`backend/src/estacion/facturacion/firma.py`), el cliente SOAP no firmaba con WS-Security y los valores
usaban `float`. Los datos del emisor ya están en la tabla `estacion`.
