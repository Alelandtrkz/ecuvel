# LR5.2B — notas internas de revisión

Estado: borradores completos para revisión; no publicados, sin versión ni fechas. Este archivo es interno y no forma parte del contenido público de ECUVEL Docs.

## Corrección puntual a LR5.2A

Texto anterior corregido en los Términos:

> “Cuando sea legalmente aplicable la devolución o cambio de un bien adquirido por medios distintos a una venta directa presencial...”

Interpretación aplicada: el artículo 45 vigente de la Ley Orgánica de Defensa del Consumidor reconoce el derecho de devolución o cambio respecto de bienes o servicios adquiridos **por cualquier medio**, dentro de los quince días posteriores a la recepción; para bienes, siempre que su naturaleza lo permita y conserven el estado en que fueron recibidos. Para servicios, la norma contempla la cesación inmediata del contrato de provisión.

La corrección mantiene este derecho separado de:

1. cancelaciones operativas;
2. reembolso íntegro por falta de stock físico Seller;
3. reembolso por no retiro dentro de siete días calendario;
4. garantías por defecto, vicio oculto o falta de conformidad; y
5. otros remedios previstos por la ley.

Los Términos continúan en estado `DRAFT`, con `requires_acceptance=True`, sin `version_identifier`, `published_at`, `effective_at` ni versiones históricas.

## Verificación jurídica oficial

| Materia | Fuente oficial verificada | Reglas utilizadas | Aplicación en LR5.2B |
| --- | --- | --- | --- |
| Información, entrega, precio, defectos y factura | [Ley Orgánica de Defensa del Consumidor](https://www.produccion.gob.ec/wp-content/uploads/2025/03/LEY-ORGANICA-DE-DEFENSA-DEL-CONSUMIDOR_2022_02_11.pdf) | arts. 17–21 | Información suficiente; cumplimiento oportuno; precio final visible; remedios por vicios ocultos; factura del proveedor vendedor. |
| Responsabilidad y cláusulas prohibidas | misma fuente | arts. 28 y 43 | No presentar a ECUVEL como mero intermediario sin responsabilidad; no renunciar a derechos, limitar responsabilidad imperativa ni invertir indebidamente la carga probatoria. |
| Devolución o cambio | misma fuente | art. 45, sustituido en 2022 | Cualquier medio; quince días posteriores a recepción; naturaleza y mismo estado del bien; cesación inmediata para servicios; no compensar el valor con notas de crédito, bienes o servicios cuando corresponda devolverlo. |
| Garantías y productos deficientes | misma fuente | arts. 11, 20 y 71 | Respetar garantía anunciada y legal; distinguir vicios ocultos; reparación gratuita y remedios posteriores sólo en los supuestos y plazos legales aplicables. |
| Mensajes y contratación electrónica | [Ley de Comercio Electrónico, Firmas Electrónicas y Mensajes de Datos](https://www.funcionjudicial.gob.ec/resources/pdf/LEY%20DE%20COMERCIO%20ELECTRONICO%20FIRMAS%20Y%20MENSAJES%20DE%20DATOS.pdf) | arts. 2, 44, 48–50 y 52 | Validez y prueba del registro electrónico; recepción no equivale por sí sola a aceptación; informar requisitos, restricciones y derechos. |
| Ofertas y sistemas automatizados | [Código de Comercio](https://www.asambleanacional.gob.ec/es/system/files/ro_codigo_de_comercio.pdf) | arts. 233, 238 y 239 | Precio y cargos transparentes; validez de comunicaciones y contratación electrónica automatizada; sin fijar aquí un momento jurídico definitivo no aprobado. |

No se utilizaron proyectos de ley, anuncios ni texto de Ozon como fuente normativa.

## Hallazgos del producto actual

- Checkout revalida oferta, precio en USD, cantidad, Tienda, disponibilidad y almacén; crea un Pedido y Subpedidos agrupados por Tienda.
- El pago de comprador disponible es transferencia bancaria. La configuración revisada permite JPEG, PNG y PDF, con límite mostrado en la interfaz, almacenamiento privado y huella SHA-256.
- El intento de pago y las reservas tienen vencimiento. Si vence antes de una carga válida, el producto libera la reserva; transferir después no reactiva automáticamente el Pedido.
- El preanálisis local puede usar Tesseract/OpenCV, OCR y QR, y conserva resultados estructurados. La decisión final la toma personal ECUVEL autorizado.
- Al aprobar el pago, el código confirma Pedido y Subpedidos, pero todavía mantiene `SellerOrderDecisionStatus.PENDING` y acciones Seller de aceptación/rechazo posteriores.
- El flujo actual genera notificaciones de decisión de pago para el comprador, no el correo objetivo de venta a la Tienda.
- Existen recepción, códigos de paquete, custodia, movimientos internos, `READY_FOR_PICKUP` y entrega por escaneo. No existe entrega domiciliaria ni transportista externo identificado.
- La configuración todavía contiene `ECUVEL_ORDER_HOLD_DAYS=14` como valor predeterminado; no implementa la regla aprobada de siete días ni el cierre, devolución a Tienda y reembolso automáticos.
- No existe un flujo integral de devolución, reembolso, captura segura de cuenta, garantía o retroalimentación de resolución Seller.

## Decisiones de política incorporadas

- Tienda vendedora ordinaria; ECUVEL operador/propietario, intermediario y participante activo según sus funciones.
- Confirmación automática objetivo después de aprobar el pago, sin segunda aceptación discrecional Seller.
- Notificación operativa a la Tienda después de la confirmación, pendiente de implementación.
- Falta de stock físico: ECUVEL contacta al Comprador, cierra la parte afectada y coordina el reembolso del 100 %, sin penalidad ni deducción, que debe completarse en un plazo máximo de quince días hábiles.
- Custodia: siete días calendario desde el aviso de listo para retiro; después, cierre/cancelación, retorno a Tienda y reembolso del 100 %, sin penalidad ni deducción, que debe completarse en un plazo máximo de quince días hábiles.
- La falta de stock físico y la falta de retiro son causas operativas separadas. El máximo de quince días hábiles para completar sus reembolsos tampoco modifica el derecho legal de devolución o cambio del artículo 45, que se ejerce dentro de quince días posteriores a la recepción bajo sus propias condiciones.
- Garantía: ECUVEL recibe, registra y coordina; Tienda atiende; ECUVEL conserva trazabilidad. Remedios sólo cuando correspondan por hechos, garantía y ley.
- Retroalimentación Seller separada de reseña/estrellas públicas y destinada a una futura métrica interna.
- Tienda emite factura de su venta; ECUVEL entrega recibo o registro de transacción que no la reemplaza y coordina ayuda.
- En Pedidos multitienda, el problema se limita en principio al Subpedido o porción afectada.

## Matriz de dependencia de implementación

| Política | Código actual | Código objetivo | ¿Bloquea publicación? | Dominio futuro |
| --- | --- | --- | --- | --- |
| Confirmación automática Seller | Pago aprobado confirma estados, pero deja decisión Seller pendiente y permite aceptar/rechazar | Eliminar decisión discrecional posterior; Subpedido listo para preparación al aprobar pago | Sí | Seller / Order / pagos |
| Correo de venta a Tienda | No existe el correo objetivo completo | Enviar referencia, productos, cantidades, hora, punto y datos de preparación tras confirmar | Sí | Seller / notificaciones |
| Falta de stock físico | Rechazo Seller puede cancelar y marcar `requires_refund_resolution` | Caso ECUVEL, contacto al comprador, cierre de parte y reembolso total | Sí | Seller / Order / soporte |
| Reembolso íntegro | No existe ejecución bancaria integral | Registrar y completar 100 % de la porción afectada sin deducciones | Sí | Pagos / reembolsos |
| Máximo de 15 días hábiles por falta de stock | Sin temporizador ni seguimiento | SLA de falta de stock con hitos, alertas y cierre dentro del máximo aprobado | Sí | Reembolsos / operaciones |
| Vencimiento de retiro a siete días | Valor predeterminado actual de 14 días; sin automatización integral | Contar siete días desde aviso `READY_FOR_PICKUP` | Sí | Fulfillment / jobs |
| Retorno a Tienda | Sin flujo integral para no retiro | Registrar salida de custodia y devolución del producto a la Tienda | Sí | Fulfillment / inventario |
| Cancelación/cierre automático | Estados existen, pero no la transición aprobada por vencimiento | Cerrar sólo Pedido/Subpedido/paquete afectado con trazabilidad | Sí | Order / fulfillment |
| Reembolso por no retiro | Sin flujo implementado | Reembolso completo del 100 %, sin penalidad ni deducción, dentro de un máximo de 15 días hábiles | Sí | Reembolsos / operaciones |
| Creación de garantía | No existe caso integral | Crear caso vinculado a comprador, Tienda, Subpedido y producto | Sí | Soporte / garantías |
| Seguimiento de garantía | Sin estados ni historial especializado | Trazar recepción, contacto, evaluación, remedio y cierre | Sí | Soporte / garantías |
| Retroalimentación de resolución | Reseña pública de producto existente; no encuesta Seller separada | Capturar `seller_case_resolution` o `seller_service_quality` separadamente | No para texto general; sí antes de afirmar disponibilidad | Seller / soporte / métricas |
| Puntaje interno Seller | No existe métrica aprobada | Métrica interna separada, gobernada y no pública | No para la política; sí antes de usarla | Seller / analítica |
| Ayuda con factura | Canal de ayuda existe; flujo específico no | Registrar y coordinar solicitud de factura a la Tienda | Sí | Soporte / Seller / tributario |
| Captura de cuenta de reembolso | Sólo canal de correo; no formulario seguro | Flujo estructurado, cifrado y minimizado con acceso restringido | Sí | Reembolsos / privacidad / seguridad |

## Preguntas pendientes para propietario, asesoría y contabilidad

1. Confirmar el momento jurídico exacto de formación de la compra dentro del flujo automático aprobado.
2. Definir procedimiento y logística de devolución/cambio del artículo 45, incluida recepción, verificación razonable, costos y cierre, sin obstaculizar el derecho.
3. Confirmar redacción tributaria y operación de factura de Tienda, recibo ECUVEL y futuras ventas directas.
4. Definir reglas por categoría para garantías, diagnóstico, transporte del producto y aplicación de remedios, sin reducir plazos legales.
5. Definir tratamiento de pérdida o daño según la etapa de custodia y responsabilidad de cada interviniente.
6. Aprobar estados, comunicaciones y evidencia para cada solución en Pedidos multitienda.
7. Aprobar el tratamiento, cifrado, acceso y conservación de los datos de la cuenta de reembolso.

## Bloqueo de publicación

Los cinco documentos LR5.2B y los Términos deben permanecer `DRAFT`. No publicar hasta completar LR5.2A–D, implementar aceptación versionada en LR5.3, integrar en LR5.4, validar consistencia en LR5.5 y resolver todas las divergencias materiales de esta matriz.
