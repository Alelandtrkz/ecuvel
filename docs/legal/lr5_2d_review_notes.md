# LR5.2D Review Notes

Estado: material interno de auditoría; no publicable. Investigación y contraste técnico cerrados el 10 de septiembre de 2026. Los ocho documentos Seller continúan `DRAFT`, sin versión, fechas de publicación/vigencia ni historial.

## 1. Scope

Objetivo: completar el corpus informativo Seller de ECUVEL Docs sin cambiar el contrato de Partners, PMT-, PAY-, pedidos, pagos, liquidaciones, modelos, migraciones ni rutas públicas.

Archivos documentales: `backend/app/services/legal_documents.py`, ocho templates bajo `backend/app/templates/docs/content/vendedores/`, `backend/tests/test_docs.py` y estas notas. La redacción diferencia producto implementado, decisión aprobada pendiente, asunto jurídico pendiente y recomendación futura.

## 2. Product audit

| Dominio | Hallazgo verificable |
| --- | --- |
| Onboarding | Cinco pasos: detalles; dirección legal; contacto; documentos; banca. Estados `DRAFT`, `SUBMITTED`, `CORRECTIONS_REQUESTED`, `APPROVED`, `REJECTED`, `CONTRACT_PENDING`, `COMPLETED`; etapas `VERIFY_DATA`, `WAITING_VERIFICATION`, `CONTRACT_ACCEPTANCE`, `PRODUCTS`. |
| Contract | Se habilita tras aprobación; cuatro declaraciones + OTP; registra aceptación y PDF. El visor y PDF son resúmenes breves, no un contrato canónico completo. |
| Commissions | Menos de USD 3,00 usa USD 0,25 fijo; precio debe ser mayor a USD 0,25; desde USD 3,00 usa porcentaje por categoría/linaje y fallback global. |
| Seller orders | Al aprobar PMT- el código confirma estados, pero conserva `SellerOrderDecisionStatus.PENDING` y acciones Seller de aprobar/rechazar. |
| Logistics | Paquete de ingreso Seller, recepción, ubicación/custodia ECUVEL, `READY_FOR_PICKUP` y entrega `HANDED_OVER`. No se encontró flujo integral de no retiro a siete días. |
| Payouts | Entrega completa, espera de cuatro días, PMT- aprobado, conciliación, ausencia de refund pendiente y banca aprobada; ciclos 15 y fin de mes. |
| Restricted products | Existe moderación general de borradores y archivos, pero no verificación regulatoria integral por subfamilia ARCSA. |
| Privacy | Onboarding, documentos, banca cifrada/versionada, contrato/OTP, miembros, catálogo, operación, PAY- y auditoría contienen datos personales con finalidades y plazos diferentes. |

## 3. Contract audit

Flujo real: administración aprueba la incorporación y mueve a `CONTRACT_ACCEPTANCE`; el Seller solicita un OTP, enviado a teléfono verificado o, en su defecto, correo verificado; acepta veracidad, términos, tarifas/anexos y obligaciones; el servicio valida vigencia e intentos del OTP; y registra `StoreContractAcceptance` como `ACCEPTED`. Luego onboarding pasa a `COMPLETED`, la Tienda a `ACTIVE` y se crea su ubicación de inventario.

Evidencia registrada: versión del contrato y anexo obtenidas de configuración, fecha/hora, flags de términos y OTP, IP, user-agent y clave privada del PDF. La descarga posterior usa el PDF guardado.

Hallazgo crítico: `_contract_panel.html` presenta sólo dos párrafos generales. `_draw_contract_pdf()` genera identificación, versión y dos líneas de aceptación general. No se encontró una fuente contractual completa y canónica compartida por visor y PDF, ni un archivo visible completo de versiones históricas. La relación uno-a-uno de aceptación preserva el registro actual, pero no demuestra por sí sola un repositorio histórico contractual completo.

Resultado: **FUTURE BLOCKER — Seller Contract Canonicalization**. Crear en otra fase una fuente canónica única, usada por Partners y PDF, identificada por versión, preservada históricamente y referenciada desde Docs. LR5.2D no modifica visor, PDF, OTP, versiones configuradas, modelos, endpoints ni aceptación.

## 4. Commission matrix

| Precio/regla | Resolución real | Snapshot y neto | Administración |
| --- | --- | --- | --- |
| Precio ≤ USD 0,25 | Rechazado: el precio Seller debe ser estrictamente mayor. | No debe producir presentación vendible. | Constante funcional. |
| USD 0,26–2,99 | Modo `FIXED`, USD 0,25. | Precio, categoría/ruta, modo, fixed, comisión, neto, fuente `LOW_PRICE_FIXED`. | Constante funcional. |
| Desde USD 3,00 | Modo `PERCENTAGE`; categoría concreta/ancestros y luego global. | Añade porcentaje, `rule_id`, fuente `CATEGORY` o `GLOBAL`; neto = precio − comisión. | Reglas activas en base de datos/CLI. No se fija aquí una tabla de porcentajes. |

`resolve_marketplace_commission()` acepta `store_id` sólo por compatibilidad y lo ignora; consulta reglas con `store_id IS NULL`. El modelo aún posee una columna `store_id`, pero el resolver y los comandos auditados no aplican reglas negociadas por Store. Los snapshots se capturan al envío a revisión, se validan al publicar y pasan a ofertas/ítems; un cambio futuro no debe reescribir retroactivamente una condición ya capturada.

## 5. PAY- matrix

| Condición | Comportamiento real |
| --- | --- |
| Identidad | `PMT-` = intento de pago Comprador→ECUVEL; `PAY-` = liquidación ECUVEL→Tienda. |
| Entrega | Todos los artículos deben tener paquete `HANDED_OVER` y fecha; SellerOrder pasa a `COMPLETED`. |
| Liberación | `payout_eligible_at = delivered_at + 4 días`. |
| Pago y finanzas | Requiere PMT- `APPROVED`, USD y reconciliación consistente de bruto, descuento, comisión y neto. |
| Refund | `requires_refund_resolution` debe ser falso. |
| Banca | Al programar exige versión bancaria aprobada y utilizable; el PAY- conserva la versión, banco y últimos cuatro dígitos. |
| Ciclos | Día 15 y último día hábil del mes, zona `America/Guayaquil`. Corte del 15: día 14, 23:59:59 local. Fin de mes: día anterior al último día hábil. |
| Día hábil | Sólo lunes–viernes; feriados ecuatorianos/bancarios no están modelados. |
| Estados | `SCHEDULED`, `ON_HOLD`, `PAID`, `CANCELLED`; reanudación/pago revalidan condiciones. |

Programación ECUVEL no equivale a acreditación bancaria. No hay promesa de acreditación inmediata ni de una hora bancaria específica.

## 6. Seller obligations

Soportadas por el producto/documentadas: información veraz, precio Seller, documentos de incorporación, banca versionada, publicaciones y archivos, stock e inventario, preparación de paquetes, respuesta operativa, autenticidad/licencia de contenido, garantías y colaboración con casos, y comprobante tributario propio sujeto a la obligación aplicable.

Decisiones aprobadas pendientes de implementación: confirmación automática sin veto Seller posterior al pago; notificación de venta completa; siete días calendario de custodia; retorno a Tienda y reembolso integral por no retiro; flujo integral de garantías/reembolsos y métrica interna Seller. No se presentan como funciones vigentes.

Pendiente de abogado/contador: calificación jurídica exacta de Tienda y ECUVEL; momento de perfeccionamiento; factura/comprobante y momento de emisión; reparto de riesgo durante custodia; requisitos regulatorios por categoría; alcance de retención documental; y máximo contractual de quince días hábiles para refund por falta de stock. Este último se marca `PENDING COUNSEL VALIDATION` y se mantiene separado del derecho del artículo 45.

Distinción obligatoria: el máximo de quince días hábiles tratado en el corpus del Comprador es un plazo de reembolso al Comprador. No constituye por sí mismo un plazo de pago, reintegro o responsabilidad financiera contractual del Seller frente a ECUVEL. La eventual imputación económica al Seller, su base contractual y cualquier plazo Seller separado permanecen `PENDING COUNSEL VALIDATION`.

## 7. Regulatory products

Fuentes ARCSA verificadas: [servicios y simuladores oficiales](https://aplicaciones.controlsanitario.gob.ec/), [normativa de cosméticos e higiene](https://www.controlsanitario.gob.ec/wp-content/uploads/downloads/2018/12/Resoluci%C3%B3n-ARCSA-DE-006-2017-CFMR-Reformado-COSM%C3%89TICOS.pdf), [normativa de control posterior de cosméticos](https://www.controlsanitario.gob.ec/wp-content/uploads/downloads/2022/08/Resolucion-ARCSA-DE-2021-016-AKRG.pdf) y [alerta sanitaria 2025 sobre cosméticos sin NSO](https://www.controlsanitario.gob.ec/wp-content/uploads/downloads/2025/01/Alerta-sanitaria-por-cosmeticos-sin-Notificacion-Sanitaria-Obligatoria-en-el-pais-24-01-2025.pdf).

| Clase LR5.2C | Tratamiento Seller | Diseño pendiente |
| --- | --- | --- |
| Prohibido | No publicar bienes ilícitos, robados, falsificados, retirados/inseguros no comercializables; separar prohibición legal de prohibición comercial de lanzamiento. | Evidencia y escalamiento por caso. |
| No soportado | OTC, dispositivos médicos, naturales medicinales, suplementos especializados, alimentos/bebidas particulares, plaguicidas y peligrosos permanecen fuera mientras falten controles. | **PENDING PRODUCT/LEGAL DESIGN** por subfamilia. |
| Regulado/condicional | Sólo después de definir producto, origen, fabricante/importador, titular/responsable, establecimiento, autorización, rotulado, claims, lote y trazabilidad. | **PENDING PRODUCT/LEGAL DESIGN**. |
| Mercancía general | Admisible en principio, pero sujeta a licitud, autenticidad, seguridad y reglas concretas. | Controles de riesgo ordinarios. |

Cosméticos: ARCSA contempla NSO, permisos y control posterior. No puede inferirse de ello una lista universal para todo Seller ni trasladarse ese régimen a alimentos, medicamentos o dispositivos. ECUVEL aún debe decidir qué documento consulta, quién debe ser titular/responsable, cómo valida vigencia, lotes, etiquetas, importación, alertas y retiros. Una obligación sustancial de producto existe antes de venta irrestricta; no se implementa en LR5.2D.

## 8. Privacy

Datos: identidad/RUC, nombre legal/comercial, dirección y contacto; archivos y metadatos; banca cifrada, últimos cuatro dígitos y versiones; miembros/roles; contrato, OTP, IP y user-agent; productos, stock, ventas, fulfillment, incidencias, garantías, devoluciones, comisiones, PAY- y auditoría.

Finalidades/bases deben asignarse por tratamiento: incorporación y contrato, seguridad, operación, consumidor, cumplimiento, finanzas, regulación y defensa. Consentimiento no es una base general; el interés legítimo exige evaluación documentada.

Destinatarios/proveedores confirmados con cautela: personal ECUVEL por permisos; Comprador sólo en lo necesario; autoridades con fundamento; hosting identificado en Estados Unidos; correo transaccional configurado como Resend; buzones Microsoft/Hotmail. Países, roles, contratos y garantías deben inventariarse antes de publicar.

Conservación: hasta siete años sólo para el subconjunto contractual, comercial, tributario, contable, de reclamos/garantías/reembolsos y financiero cuando se justifique. No se extiende automáticamente a OTP, sesiones, tokens, carritos, telemetría, temporales ni cachés. Falta matriz implementada de plazos, bloqueo y purga. `ecuvel.privacidad@hotmail.com` es canal principal habilitado, no exclusivo; ayuda y reclamos conservan buzones distintos.

Fuentes: [LOPDP](https://spdp.gob.ec/wp-content/uploads/2024/12/03.pdf.pdf), [Reglamento General, Decreto 904](https://spdp.gob.ec/wp-content/uploads/2024/12/04.pdf.pdf) e [índice oficial de resoluciones SPDP](https://spdp.gob.ec/resoluciones2/), incluidas las reglas vigentes sobre cláusulas contractuales, eliminación/bloqueo, interés legítimo, transferencias, gran escala y evaluación de riesgos ya auditadas en LR5.2A/C.

## 9. Product/legal mismatches

| Tema | Código actual | Decisión objetivo | Acción futura | ¿Bloquea publicación? |
| --- | --- | --- | --- | --- |
| Seller manual decision | `SellerOrderDecisionStatus.PENDING/APPROVED/REJECTED`; Partners permite aprobar/rechazar y fulfillment/PAY- exige `APPROVED`. | Auto-confirmación tras PMT- aprobado; Seller notificado, sin veto tardío discrecional. | Fase funcional coordinada en Orders, Seller, fulfillment y PAY-, con migración/compatibilidad si procede. | Sí. |
| Contrato canónico | Visor y `_draw_contract_pdf()` contienen resumen funcional; no fuente completa compartida. | Una fuente contractual, versionada, histórica, usada por visor/PDF y referenciada desde Docs. | Seller Contract Canonicalization. | Sí. |
| Custodia | Configuración predeterminada conserva 14 días; no hay vencimiento integral. | Siete días calendario desde `READY_FOR_PICKUP`. | Job/estados/notificación con pruebas. | Sí. |
| Retorno/reembolso por no retiro | No existe flujo integral de retorno y reembolso. | Retorno a Tienda y 100 % sin penalidad/deducción. | Fulfillment, inventario y refunds. | Sí. |
| Falta de stock | Rechazo Seller histórico marca resolución de reembolso. | Caso coordinado por ECUVEL, sin veto discrecional. | Caso de incidencia/refund y métricas. | Sí. |
| Máximo stock-refund | Sin SLA implementado. | Propuesta 15 días hábiles. | Validación jurídica y, sólo si se aprueba, temporizador/alertas. | Sí para prometerlo. |
| Garantías | Sin caso integral ni encuesta Seller. | ECUVEL coordina; Tienda participa; métrica interna separada. | Workflow trazable. | Sí para afirmar disponibilidad. |
| Facturación | No se encontró emisión de factura Seller dentro del flujo. | Tienda emite su comprobante; ECUVEL sólo recibo de plataforma. | Validación tributaria y soporte operativo. | Sí. |
| ARCSA | Moderación genérica sin validación de NSO/registro/permiso por subfamilia. | Controles regulatorios antes de habilitar. | Diseño producto/legal y verificación. | Sí para venta regulada irrestricta. |
| Retención de datos | Evidencia existe, pero no matriz global de plazos/purga. | Conservación diferenciada y bloqueo/purga verificables. | Gobierno de datos. | Sí para declaración definitiva. |

## 10. Counsel questions

A. ¿Cómo debe calificarse en Ecuador la relación en la que la Tienda vende y factura el producto mientras ECUVEL opera marketplace, cobro, fulfillment, custodia, reclamos, garantías, refunds y liquidaciones?

B. ¿Cuándo se perfecciona jurídicamente la compraventa si el modelo final elimina el veto manual Seller después del pago?

C. ¿Es correcto y conveniente asumir como compromiso contractual un máximo de quince días hábiles para el reembolso por falta de stock Seller, y desde qué hito se cuenta?

D. ¿Qué comprobante tributario debe emitir cada tipo de Seller, cuándo debe emitirlo/entregarlo y qué obligaciones de transmisión electrónica inmediata vigentes desde 2026 le aplican?

E. ¿Qué requisitos, documentos y controles regulatorios deben exigirse por categoría, en especial para cosméticos según fabricación/importación, titularidad de NSO, establecimiento, rotulado y comercialización?

F. ¿Qué cambios contractuales requieren nueva aceptación expresa Seller y cuáles admiten aviso informativo?

G. ¿Qué evidencia comercial, contractual, tributaria y financiera debe conservarse siete años y qué datos —incluidos OTP, sesiones, tokens y telemetría— deben tener plazos menores?

## 11. Publication blockers

- Canonicalizar el contrato completo, PDF, versión e historial Seller.
- Eliminar/reconciliar la decisión manual Seller posterior al pago y sus dependencias en fulfillment/PAY-.
- Implementar notificación operativa de venta al Seller.
- Implementar siete días calendario, cierre, retorno a Tienda y reembolso por no retiro.
- Implementar el flujo integral de falta de stock y validar con asesoría el máximo de quince días hábiles antes de prometerlo en Seller.
- Implementar casos trazables de garantías, refunds y métrica interna separada.
- Confirmar con abogado/contador la calificación de las partes y facturación Seller, incluido el régimen SRI 2026.
- Diseñar y operar controles ARCSA por subfamilia antes de habilitar productos regulados.
- Completar matriz de conservación, bloqueo y purga; contratos y mapa de transferencias/proveedores.
- Completar LR5.2A–D, aceptación versionada aplicable en LR5.3, integración LR5.4 y reconciliación LR5.5 antes de cualquier transición `DRAFT` → `PUBLISHED`.

Fuentes legales principales adicionales verificadas: [Constitución](https://www.asambleanacional.gob.ec/es/contenido/constitucion-de-la-republica-del-ecuador); [Ley Orgánica de Defensa del Consumidor](https://www.produccion.gob.ec/wp-content/uploads/2025/03/LEY-ORGANICA-DE-DEFENSA-DEL-CONSUMIDOR_2022_02_11.pdf); [Ley de Comercio Electrónico](https://www.registroficial.gob.ec/suplemento-al-registro-oficial-no-557-5/); [Código de Comercio, arts. 238–239](https://www.asambleanacional.gob.ec/es/system/files/ro_codigo_de_comercio.pdf); [SRI — facturación electrónica](https://www.sri.gob.ec/facturacion-electronica), [obligación de comprobantes, art. 41](https://www.sri.gob.ec/o/sri-portlet-biblioteca-alfresco-internet/descargar/fe699a5c-a49a-42e1-a71f-61d66e752ed8/Reglamento%20para%20la%20Aplicaci%C3%B3n%20de%20la%20Ley%20de%20R%C3%A9gimen%20Tributario%20Interno.pdf) y [transmisión inmediata desde 2026](https://www.sri.gob.ec/detalle-noticias?idnoticia=1239&marquesina=1); [SENADI — derechos intelectuales](https://www.derechosintelectuales.gob.ec/derechos-intelectuales/). No se trataron proyectos de ley como derecho vigente.
