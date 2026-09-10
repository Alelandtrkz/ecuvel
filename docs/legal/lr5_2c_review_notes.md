# LR5.2C — notas internas de investigación y revisión

**Estado:** interno; borradores no publicados.

**Fecha de investigación:** 2026-09-10 (Ecuador).

**Baseline:** `c7ca1984a27284002e30152ba07c5c217f613842`.

## 1. Fuentes jurídicas verificadas

| Materia | Fuente oficial | Aplicación cautelosa |
|---|---|---|
| Derechos constitucionales y consumo | [Constitución de la República](https://www.asambleanacional.gob.ec/es/contenido/constitucion-de-la-republica-del-ecuador), arts. 52–55 y 66.19 | Información, reparación, tutela del consumidor y protección de datos. |
| Protección de datos | [LOPDP](https://spdp.gob.ec/wp-content/uploads/2024/12/03.pdf.pdf), especialmente arts. 7–21, 37–43 y 46–48 | Base por finalidad, consentimiento, derechos, privacidad por defecto, seguridad y responsabilidad demostrada. |
| Desarrollo reglamentario | [Reglamento General a la LOPDP, Decreto 904](https://www.registroficial.gob.ec/tercer-suplemento-al-registro-oficial-no-435/) | Procedimientos, responsable/encargado, riesgos y obligaciones complementarias. |
| Normativa SPDP vigente | [Índice de resoluciones](https://spdp.gob.ec/resoluciones2/) | Se revisaron, en especial, 2025-0030-R (seudonimización, anonimización, bloqueo y eliminación), 2025-0041-R (interés legítimo), 2026-0004-R (transferencias), 2026-0005-R (gran escala) y 2026-0009-R (IA). |
| Interés legítimo | [SPDP-SPD-2025-0041-R](https://spdp.gob.ec/wp-content/uploads/2025/11/41.01.01-SPSP-SPD-2025-0041-R-Normativa-general-para-la-aplicacion-del-interes-legitimo-y-su-anexo.pdf) | Exige evaluación; por decisión del propietario no se usa para telemetría conductual opcional. |
| Consumo | [Ley Orgánica de Defensa del Consumidor](https://www.produccion.gob.ec/wp-content/uploads/2025/03/LEY-ORGANICA-DE-DEFENSA-DEL-CONSUMIDOR_2022_02_11.pdf) | Reclamos, información, seguridad, garantías y remedios permanecen separados. |
| Comercio electrónico | [Ley de Comercio Electrónico, Firmas Electrónicas y Mensajes de Datos](https://www.telecomunicaciones.gob.ec/wp-content/uploads/downloads/2012/11/Ley-de-Comercio-Electronico-Firmas-y-Mensajes-de-Datos.pdf) y su [Reglamento](https://www.telecomunicaciones.gob.ec/wp-content/uploads/2020/07/REGLAMENTO-A-LA-LEY-DE-COMERCIO-ELECTRONICO.pdf) | Información del consumidor, mensajes de datos y comunicaciones electrónicas. |
| Contratación mercantil y civil | [Código de Comercio](https://www.asambleanacional.gob.ec/es/system/files/ro_codigo_de_comercio.pdf) y [Código Civil](https://biblioteca.defensoria.gob.ec/handle/37000/3410) | Marco general de obligaciones y relaciones contractuales; no se inventaron artículos específicos. |
| Propiedad intelectual | [Código Orgánico de la Economía Social de los Conocimientos, Creatividad e Innovación](https://www.asambleanacional.gob.ec/sites/default/files/private/asambleanacional/filesasambleanacionalnameuid-29/Leyes%202013-2017/133-conocimiento/ro-cod-econ-conoc-899-sup-09-12-2016.pdf) | Marco ecuatoriano para derecho de autor, derechos conexos y propiedad industrial; no se importó un proceso DMCA. |
| Control sanitario | [Servicios oficiales ARCSA](https://aplicaciones.controlsanitario.gob.ec/) y [permisos/categorización](https://permisosfuncionamiento.controlsanitario.gob.ec/) | Los regímenes cambian según producto/riesgo; LR5.2C no inventa documentos ni plazos. |
| Armas y explosivos | [Ley de Armas, Municiones, Explosivos y Materiales Relacionados](https://www.registroficial.gob.ec/cuarto-suplemento-al-registro-oficial-no-680/) | Marco vigente desde 2024; ECUVEL adopta además prohibición comercial de lanzamiento. |
| Ambiente y vida silvestre | [Código Orgánico del Ambiente](https://www.ambiente.gob.ec/wp-content/uploads/downloads/2017/12/CODIGO-ORGANICO-DEL-AMBIENTE.pdf) y [Reglamento](https://www.ambiente.gob.ec/wp-content/uploads/downloads/2021/06/REGLAMENTO-AL-CODIGO-ORGANICO-DEL-AMBIENTE.pdf) | Sustenta el tratamiento cauteloso de contrabando ambiental y vida silvestre. |
| Conductas ilícitas | [Código Orgánico Integral Penal](https://www.asambleanacional.gob.ec/es/contenido/codigo-organico-integral-penal-0) | Referencia general para fraude, bienes robados, falsificación y conductas prohibidas; sin atribuir delitos sin revisión. |

## 2. Hechos de producto y código verificados

- Flask usa sesión firmada del lado del cliente; la configuración establece `HttpOnly`, `SameSite=Lax` y `Secure` según entorno.
- La opción de recordar acceso se transmite a Flask-Login y comparte el plazo configurado de sesión permanente.
- El carrito invitado, tokens de unión, identificadores de checkout y otros estados de continuidad se guardan dentro de la sesión.
- La telemetría del catálogo observa impresiones y registra eventos mediante peticiones con credenciales del mismo origen. Sin cuenta usa `catalog_anonymous_session_id`; con cuenta puede usar el usuario.
- Los contextos de ranking y cursores son valores firmados con expiración, no cookies independientes.
- Google Fonts y unpkg/Lucide son solicitudes externas en el marketplace general; ECUVEL Docs usa recursos locales y no carga `catalog-telemetry.js`.
- Existe `UserMarketingConsent` con EMAIL y SMS/WhatsApp, y estados concedido, revocado o desconocido; no se encontró captura activa ni campañas.
- Phone OTP general está desactivado y la interfaz lo anuncia como próximo.
- Estados de usuario: `PENDING_VERIFICATION`, `ACTIVE`, `BLOCKED`, `SUSPENDED`, más `is_active`. La suspensión administrativa revoca sesiones mediante `auth_version`; la reactivación existe. No se localizó flujo general que asigne `BLOCKED`.
- Pago comprador: transferencia bancaria; OCR/preanálisis auxilia, y personal autorizado decide.
- Reseña: paquete entregado, una por artículo, 1–5 estrellas, texto e imágenes; reglas determinísticas; publicación automática si no hay señales ni imágenes; revisión humana en otros casos; corrección/reenvío y respuesta de Tienda.
- El catálogo tiene categorías generales y moderación de publicaciones, pero no verificación integral de licencias, controles sanitarios o edad por categoría regulada.

## 3. Inventario de cookies y tecnologías

| Mecanismo | Parte/controlador | Finalidad actual | Persistencia/configuración | Evaluación |
|---|---|---|---|---|
| Cookie de sesión Flask (`session`, salvo cambio de configuración) | Primera parte / ECUVEL | Autenticación, CSRF, carrito invitado y continuidad | De sesión cuando no es permanente | Esencial según función solicitada. |
| Cookie remember de Flask-Login | Primera parte / ECUVEL | Recordar acceso a elección del usuario | 14 días por defecto; configurable 300 s–90 días | Funcional solicitada. |
| Datos dentro de sesión | ECUVEL | Carrito, merge token, checkout/pedidos, cargas y UUID de catálogo | Vinculada a sesión/flujo | Clasificar por finalidad; no todo es cookie independiente. |
| UUID de catálogo | ECUVEL | Vincular eventos sin cuenta | Dentro de sesión | Seudónimo/potencial dato personal, no anonimato irreversible. |
| Cursores/contextos firmados | ECUVEL | Paginación y contexto de ranking | 1 hora por defecto; rango 5 min–24 h | Tokens de solicitud, no cookies. |
| Eventos de telemetría | ECUVEL, servidor | Impresión, clic, favorito, carrito, compra, entrega y ranking | Sin purga automática localizada | No esencial cuando mide conducta/intereses; requiere elección previa. |
| Google Fonts/unpkg | Terceros por solicitud del navegador | Fuente e iconos | Depende del tercero/navegador | Solicitud externa, no afirmar que instala cookie ECUVEL. |

## 4. Evaluación de consentimiento y control de preferencias

Decisión aprobada: la telemetría conductual no esencial estará apagada por defecto y requerirá elección previa, informada, opcional y no preseleccionada. La persona podrá aceptar, rechazar, cambiar la preferencia y retirar el consentimiento; el rechazo no impedirá navegar, usar cuenta/carrito ni comprar. No es obligatorio que la interfaz adopte forma de «banner».

El código actual registra automáticamente eventos, por lo que existe un **PUBLICATION BLOCKER** hasta implementar el control, impedir eventos antes de aceptar, detener eventos futuros al retirar y registrar evidencia proporcional. No se selecciona interés legítimo para esta finalidad.

### Evaluaciones de interés legítimo para otras finalidades

La decisión sobre telemetría no convierte el interés legítimo en una base automática para seguridad, fraude, moderación, trazabilidad, defensa de reclamaciones u otros tratamientos. Antes de LR5.P, cada tratamiento que efectivamente pretenda apoyarse en interés legítimo debe identificarse individualmente y contar con la evaluación documentada exigida por la normativa ecuatoriana aplicable, incluida la finalidad legítima concreta, la necesidad del tratamiento y la ponderación frente a los intereses, derechos y libertades del titular.

LR5.2C no realiza ni presume superada ninguna de esas evaluaciones. Su aprobación y correspondencia con la operación real son una **dependencia legal de prepublicación**.

## 5. Derechos y plazos

LOPDP: información (art. 12); acceso (art. 13); rectificación/actualización (art. 14); eliminación (art. 15); oposición (art. 16); portabilidad (art. 17); suspensión (art. 19); garantías frente a decisiones automatizadas (art. 20); y consulta de datos crediticios (art. 21, no presentada como función de ECUVEL).

El plazo legal verificado de quince días se aplica a acceso, rectificación/actualización, eliminación y oposición. La rectificación incluye informar al destinatario, de ser el caso, en el mismo plazo. No se atribuyó número no verificado a portabilidad, suspensión, retiro o revisión automatizada. El consentimiento es revocable mediante mecanismo sencillo, célere, eficaz y gratuito.

## 6. Comunicaciones y marketing

Las comunicaciones transaccionales de cuenta, seguridad, pago, Pedido, retiro, reembolso, garantía, reclamo y servicio se separan del marketing. Disponer de correo, teléfono o género no concede permiso comercial. El modelo de consentimiento no demuestra captura activa. El futuro consentimiento debe ser separado por canal, opcional, registrado y revocable; el retiro comercial no detiene mensajes necesarios.

## 7. Clasificación de productos restringidos

1. **Prohibidos:** ilegalidad o prohibición expresa; y, separadamente, prohibición comercial ECUVEL de lanzamiento.
2. **No soportados actualmente:** categorías potencialmente lícitas que requieren controles especializados inexistentes.
3. **Regulados/soporte condicional futuro:** sólo tras verificación normativa, evidencia de Tienda y producto habilitado.
4. **Mercancía general ordinaria:** admisible en principio, pero cada producto permanece sujeto a seguridad, licitud, autenticidad y reglas particulares.

Prohibición legal y política comercial no se mezclan. Armas, municiones, explosivos, alcohol, tabaco/nicotina y medicamentos restringidos se excluyen del lanzamiento como decisión de ECUVEL, sin afirmar que toda la categoría sea ilegal en Ecuador.

## 8. Categorías pendientes y decisión aprobada

- OTC, dispositivos médicos, productos medicinales naturales, suplementos especializados, alimentos/bebidas con verificación particular, plaguicidas y productos peligrosos: no soportados hasta implementar controles.
- Cosméticos: categoría prevista, no prohibida permanentemente; venta irrestricta bloqueada hasta verificar el régimen ARCSA aplicable.
- Categorías adultas o con verificación documental/edad: inactivas.
- LR5.2D debe precisar por tipo de producto la evidencia exigida a Tiendas sin extrapolar requisitos de un régimen ARCSA a otro.

## 9. Reseñas y moderación

La documentación conserva exactamente la elegibilidad por entrega, una reseña por artículo, estrellas, texto, imágenes, respuesta de Tienda y trazabilidad. El filtrado es determinístico, no IA. Se documenta corrección/reenvío real, no una apelación inexistente. La futura evaluación de resolución de garantía permanece separada de la reseña pública.

## 10. Propiedad intelectual

El marco aplicable es ecuatoriano, principalmente el Código Ingenios: derechos de autor y conexos, marcas y demás propiedad industrial. No se utiliza DMCA. ECUVEL no adquiere la propiedad de contenido de Tiendas o usuarios; recibe una licencia no exclusiva y operativamente limitada. Se usa ayuda como canal factual temporal y se recomienda un buzón legal/IP dedicado antes de escalar el marketplace.

## 11. Límites de fraude y seguridad

Las políticas públicas describen conductas y posibles medidas a alto nivel. No publican rate limits, umbrales, firmas, lexicones, pesos, heurísticas, secretos o procedimientos de personal. OCR y señales automáticas no prueban culpabilidad; la aclaración y revisión deben ser proporcionales.

## 12. Mapeo de suspensión y estados

| Estado/marca | Realidad actual | Redacción |
|---|---|---|
| `PENDING_VERIFICATION` | Puede autenticarse si `is_active`; controles posteriores pueden limitar funciones | Acceso limitado mientras se verifica. |
| `ACTIVE` | Autenticación permitida si `is_active` | Cuenta operativa. |
| `BLOCKED` | Existe en enum/filtros; no se localizó flujo general que lo asigne | No afirmar proceso o efecto adicional. |
| `SUSPENDED` | Admin exige motivo, desactiva y aumenta `auth_version` | Revoca sesiones; revisión/reactivación administrativa existe. |
| `is_active=False` | Bloquea autenticación independientemente del enum | Marca independiente explicada. |

La restricción nunca se describe como extinción de Pedidos pagados, reembolsos, garantías, reclamos, privacidad u obligaciones causadas.

## 13. Matriz de dependencias de producto y publicación

| POLÍTICA | CURRENT CODE | DRAFT POLICY | GAP | PUBLICATION BLOCKER? | FUTURE DOMAIN |
|---|---|---|---|---|---|
| Preferencias de telemetría | No existe control; eventos automáticos | Apagada por defecto, aceptar/rechazar | Implementar compuerta previa | **Sí** | LR5.4/LR5.5 privacidad/frontend |
| UUID seudónimo de catálogo (`anonymous` interno) | UUID en sesión | Seudónimo/potencial dato personal | No emitir/usar para finalidad opcional antes de elegir | **Sí** | Catálogo/privacidad |
| Retiro/cambio de preferencia | No existe | Revocable; detiene eventos futuros | UI, persistencia y aplicación | **Sí** | Privacidad/frontend |
| Evidencia del consentimiento | No existe | Registro proporcional | Diseño de prueba y versión de aviso | **Sí** | Privacidad/auditoría |
| Retención de telemetría | Sin purga localizada | Período breve por finalidad | Aprobar plazo y job de purga | **Sí** | Datos/operaciones |
| Cookies esenciales | Sesión/remember/CSRF/carrito | Separadas de opcionales | Confirmar configuración productiva | Sí, antes de LR5.P | Infra/privacidad |
| Recursos externos | Google Fonts/unpkg en marketplace | Transparencia sin llamarlos cookies | Confirmar tratamiento/transferencia o autoalojar | Sí, antes de LR5.P | Infra/privacidad |
| Captura marketing | Modelo existe; captura no localizada | Consentimiento separado por canal | Crear flujo antes de campañas | No si marketing sigue inactivo | Marketing/privacidad |
| Retiro marketing | Estado REVOKED existe; no UI localizada | Sencillo y por canal | Implementar antes de activar | No si marketing sigue inactivo | Marketing/privacidad |
| Segmentación por género | Campo opcional/display; no campañas | Futura, inactiva | Revisión legal y consentimiento | No si sigue inactiva | Marketing/perfil |
| Derechos de datos | Correo, sin workflow | Canal principal no exclusivo | Registro, verificación, respuesta y seguridad | **Sí** | Privacidad/soporte |
| Rectificación DOB | Sin edición in-app | Solicitud de privacidad | Flujo seguro proporcional | **Sí** | Perfil/privacidad |
| Cierre/eliminación cuenta | Sin flujo localizado | Cierre no borra todo | Definir proceso y excepciones | **Sí** | Auth/privacidad |
| Evidencia hasta 7 años | Sin matriz/bloqueo general | Sólo subconjunto justificado | Matriz, bloqueo y purga | **Sí** | Datos/legal |
| Seguimiento de reclamos | Correo; no expediente integral | Coordinación y trazabilidad | Sistema/procedimiento operativo | **Sí** | Soporte/consumo |
| Restricciones de producto | Moderación general | Cuatro clases de lanzamiento | Reglas y evidencia por categoría | **Sí** | LR5.2D/catálogo |
| Cosméticos | Categoría diseñada; verificación no localizada | Admitibles condicionalmente | Flujo ARCSA por régimen | **Sí para venta irrestricta** | LR5.2D/Seller |
| Categorías por edad | No activas; sin verificación dedicada | Deshabilitadas | Diseño legal/técnico antes de habilitar | **Sí para activarlas** | Catálogo/Auth |
| Moderación de reseñas | Reglas, humano, historial, reenvío | Refleja código | Definir revisión adicional sólo si se desea | No | Reseñas |
| Quejas IP | Sin workflow dedicado | Ayuda como ruta temporal | Canal/procedimiento y roles | **Sí** | Legal/soporte |
| Revisión de fraude | Humano en pago; controles dispersos | Señal no equivale a culpa | Procedimiento transversal | **Sí** | Riesgo/operaciones |
| Revisión de suspensión | Suspender/reactivar admin | Contacto y proporcionalidad | Proceso documentado y responsables | **Sí** | Auth/soporte |
| Aviso de restricción | No se confirmó notificación general | Aviso cuando proceda | Plantilla, excepciones y evidencia | **Sí** | Auth/comunicaciones |
| Interés legítimo en otras finalidades | Varias bases propuestas, sin evaluaciones documentadas localizadas | Asignación individual, no automática | Documentar finalidad, necesidad y ponderación antes de invocarlo | **Sí** | Privacidad/legal/RAT |

## 14. Correcciones de coherencia

Se aplicó una corrección mínima a la Política de Privacidad DRAFT: la base propuesta para telemetría conductual no esencial dejó de ser «interés legítimo si supera evaluación; de lo contrario consentimiento» y pasó a consentimiento previo, opcional y revocable, conforme a la decisión final del propietario. También se hizo explícita la ausencia actual del control y su carácter de bloqueo. No se modificaron Términos ni políticas LR5.2B.

## 15. Preguntas para propietario/asesoría y criterio de salida

1. Aprobar plazo exacto y purga de telemetría, y diseño de evidencia de consentimiento.
2. Validar que la clasificación de lanzamiento no omita una categoría prioritaria.
3. En LR5.2D, definir la evidencia ARCSA exacta por tipo de cosmético y producto regulado.
4. Definir procedimiento operativo y canal futuro para propiedad intelectual.
5. Completar y aprobar antes de LR5.P la evaluación individual de cada tratamiento que vaya a utilizar interés legítimo, sin asignarlo automáticamente a seguridad o moderación.
6. Aprobar workflows de derechos, reclamos, fraude y suspensión antes de LR5.P.
7. Confirmar contratos, países, subencargados y transferencias de Hostinger, Microsoft/Hotmail, Resend, Google Fonts y unpkg.

Los borradores pueden pasar a revisión LR5.2D, pero no a publicación. LR5.P depende de cerrar los bloqueos marcados y verificar alineación entre código, operación y texto.
