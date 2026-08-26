# Contexto permanente de ECUVEL

Última actualización: 2026-08-21.

Este archivo resume el estado funcional y técnico de ECUVEL para que una persona o agente pueda orientarse rápido antes de trabajar. No reemplaza las migraciones, pruebas ni el código fuente. Debe mantenerse como una memoria viva del proyecto.

## Propósito del producto

ECUVEL es un marketplace ecuatoriano en construcción. Cubre una experiencia de compra pública, pagos por transferencia bancaria con revisión manual, operación de inventario y fulfillment, cuentas de clientes, reseñas verificadas, tiendas públicas y un panel privado para vendedores.

El producto prioriza flujos reales y auditables sobre simulaciones: los precios, stock, pedidos, reservas, pagos, comprobantes, paquetes y reseñas se derivan de PostgreSQL y servicios transaccionales. Las pantallas aún incompletas se muestran como “Próximamente” o estados honestos, sin prometer funciones no implementadas.

## Arquitectura general

- Backend Flask con fábrica `create_app()`, blueprints y servicios separados por dominio.
- SQLAlchemy ORM con PostgreSQL y migraciones Alembic/Flask-Migrate.
- Flask-WTF protege formularios POST con CSRF.
- Flask-Login gestiona sesiones de clientes.
- Flask-Limiter aplica límites conservadores en flujos sensibles.
- Docker Compose levanta aplicación, PostgreSQL y worker recurrente de expiración de pagos.
- La suite de integración corre contra `ecuvel_test` mediante `compose.test.yaml`; nunca debe usar la base normal.
- Los archivos privados viven fuera de `static`; las rutas de lectura validan propiedad y estado antes de servir contenido.

## Módulos principales

### Storefront público

Incluye portada, detalle de producto, carrito, checkout, transferencia, pedidos, favoritos, reseñas públicas y tienda pública. Usa Jinja, CSS propio, JavaScript progresivo y datos reales del catálogo. El detalle admite variantes estructuradas de una misma tienda: el selector actualiza oferta, URL, galería, precio, SKU y stock sin recargar. Las tarjetas de producto consumen oferta canónica, disponibilidad vendible, favoritos y reseñas agregadas.

### Autenticación y perfil

Soporta registro/login por correo y contraseña, verificación de correo, recuperación de contraseña y autenticación por teléfono con código de un solo uso. El perfil permite editar datos, cambiar correo, cambiar contraseña, añadir teléfono y conservar/reclamar pedidos temporales cuando corresponde.

### Carrito y checkout

El carrito vive en sesión firmada, pero siempre rehidrata precios, disponibilidad y catálogo desde PostgreSQL. El checkout requiere usuario autenticado y verificado, revalida stock/precios y crea de forma atómica pedido, subpedidos, artículos, reservas y un intento de pago pendiente por transferencia.

### Pagos y comprobantes

El método funcional es transferencia bancaria. El comprador sube comprobantes privados; el sistema valida el archivo, lo almacena fuera de `static` y marca el pago como en proceso. ECUVEL staff puede revisar la evidencia y aprobar o rechazar el comprobante desde el Admin web; la interfaz reutiliza el mismo servicio transaccional e idempotente de dominio disponible para CLI. La prevalidación asistida nunca decide por sí sola.

### Inventario y fulfillment

Inventario maneja recepción, putaway, reserva, liberación, consumo, expiración y picking. El dominio conserva dos paquetes distintos: `SellerInboundPackage` representa la entrega vendedor → ECUVEL y, después de su recepción física, puede continuar por la red inter-puntos; `OrderPackage` representa el paquete ECUVEL → comprador para empaque, retiro y handover. Fulfillment Admin mantiene un estado actual transaccional, traslados y eventos append-only para responder dónde está el paquete de entrada, quién lo custodia, hacia dónde va y si se desvió. Un `Warehouse` con `seller_store_id IS NULL` representa un punto logístico ECUVEL; un Warehouse asociado a una tienda representa inventario comercial del vendedor y queda fuera de Scanner, Fulfillment y KPI físicos Admin. `WarehouseLocation` conserva las ubicaciones internas de ambos tipos. Las reservas y movimientos comerciales no se alteran por registrar trazabilidad logística.

### Pedidos del cliente

`/pedidos` y el detalle de pedido muestran solo pedidos del usuario autenticado. Los estados visuales se derivan de pagos, comprobantes, pedidos, subpedidos y paquetes. Los GET de pedidos son de solo lectura; cancelación y comprobantes usan POST protegidos.

### Favoritos

Los favoritos son persistentes por usuario y producto. Se muestran en página propia, header y tarjetas públicas. El producto es la entidad pública estable; la oferta canónica solo define precio, disponibilidad y carrito.

### Reseñas verificadas

Las reseñas se crean por `OrderItem` entregado y conservan revisiones append-only. Un motor local, determinístico y versionado normaliza el texto y aplica reglas explícitas de términos, datos personales y spam: una revisión limpia sin imágenes se publica automáticamente, mientras cualquier señal, imagen o fallo del motor exige revisión manual. La calificación y el tono negativo nunca son señales de moderación. Admin dispone de `/admin/reviews` para revisar datos reales, aprobar o rechazar con motivo; el cliente puede corregir una reseña rechazada creando una nueva revisión sin borrar el historial. Las imágenes permanecen privadas hasta que la revisión vigente sea publicada y una imagen de una revisión antigua nunca vuelve a ser pública. Los rechazos generan una notificación durable mediante outbox, independiente de la transacción de moderación.

### Tiendas públicas

`/tiendas/<slug>` muestra solo tiendas activas. La cabecera usa nombre comercial, avatar por inicial, código público, calificación derivada y conteo de productos visibles. Los modales de información/rating/productos no exponen datos privados ni métricas de pedidos.

### ECUVEL Partners

`/partners` es el área privada para vendedores autenticados. Incluye onboarding de tienda, correcciones estructuradas, contrato con código de confirmación, activación de tienda, productos, reseñas, pedidos y ventas. En el onboarding, una observación administrativa abre directamente el paso afectado; los documentos reemplazados permanecen privados y auditables, mientras la nueva versión queda pendiente de revisión. Los productos nacen como `ProductDraft`; al ser aprobados por ECUVEL conservan el borrador y su historial, pero pasan a mostrarse como la oferta pública autoritativa sin duplicar filas en “Mis productos”. El vendedor ve motivos y observaciones de corrección/rechazo y puede reenviar los estados `CHANGES_REQUESTED`. El flujo de pedidos separa la preparación de la tienda mediante `SellerInboundPackage` del fulfillment hacia el comprador mediante `OrderPackage`. Ventas deriva importes de `SellerOrder` y utiliza `SellerPayout`/`SellerPayoutItem` para liquidaciones ECUVEL → tienda.

### ECUVEL Admin

`/admin` es una superficie administrativa separada de storefront y Partners. Solo admite usuarios autenticados, activos, con estado `ACTIVE` e `is_ecuvel_staff=True`. El Centro de Operaciones calcula KPIs, flujo, alertas, colas de atención, actividad reciente y búsqueda limitada a partir de tablas reales. `/admin/orders` ofrece listado paginado y filtrable por `Order`, detalle histórico por número público y revisión privada de comprobantes. `/admin/products` implementa la cola real de moderación: tabs por estado, búsqueda, paginación, panel de prevalidación determinística, checklist manual, preview privada que reutiliza el detalle público, correcciones, rechazo, aprobación y publicación transaccional. `/admin/reviews` implementa la cola real de reseñas: KPIs, tabs, filtros, búsqueda, paginación, drawer accesible, medios privados y decisiones manuales protegidas por `reviews.view`/`reviews.moderate`. `/admin/stores` implementa la moderación real del onboarding: listado y KPI, búsqueda/filtros, documentos y contrato privados, historial append-only, correcciones por campo/documento y aprobación de verificación con checklist. Aprobar solo habilita el contrato; no activa ni verifica la tienda. `/admin/fulfillment` es el centro de control de paquetes de entrada que ya fueron recibidos por ECUVEL: lista ubicación, custodia, destino, último movimiento, tiempo en estado y desviaciones; el detalle permite asignar/reasignar traslado y crear una corrección de ruta mediante POST. `/admin/scanner` es la superficie de ejecución física: recepción vendedor → ECUVEL, pickup a transportista, llegada inter-puntos, entrega completa al comprador y consulta read-only. Reutiliza los servicios canónicos de dominio con CSRF, rate limit, locking, idempotencia y Post/Redirect/Get. Los GET son read-only. Los módulos no implementados se identifican como “Próximamente”.

### Usuarios y personal ECUVEL

- `User` continúa siendo la única identidad de login. Un empleado es un `User` con un `StaffProfile` opcional 1:1; `StoreMember` continúa siendo otra relación independiente sobre el mismo usuario.
- `/admin/users` lista clientes reales con búsqueda, filtros, paginación, verificaciones, pedidos, tiendas asociadas y consentimientos de marketing. El consentimiento de email y SMS/WhatsApp es explícito, separado, auditable y de solo lectura para Admin.
- `/admin/users/staff` gestiona personal ECUVEL, alta segura, detalle, rol, historial de asignación a puntos, estado laboral, acceso e invitaciones. El identificador público laboral `EMP-000001` se genera con una secuencia PostgreSQL y no puede ser suministrado por el navegador.
- Estado laboral (`ACTIVE`, `SUSPENDED`, etc.), estado operativo derivado (`AVAILABLE`, `ASSIGNED`, `IN_ROUTE`, `OFF_DUTY`) y acceso/autenticación (`ENABLED`, `DISABLED`, invitación pendiente) son tres dimensiones diferentes.
- Las asignaciones operativas solo admiten bodegas ECUVEL (`Warehouse.seller_store_id IS NULL`). La restricción parcial garantiza una sola asignación primaria activa por empleado, pero permite muchos empleados en un punto y conserva el historial.
- Las invitaciones de personal almacenan únicamente el hash SHA-256 del token, expiran, son de un solo uso y revocan invitaciones anteriores al reenviar. El empleado define su contraseña mediante el flujo público seguro; Admin nunca conoce ni establece esa contraseña.
- La matriz canónica `StaffRole → permisos` protege las nuevas vistas de usuarios/personal. Cuentas administrativas legacy sin `StaffProfile` conservan temporalmente el acceso anterior; no se inventan identidad, rol ni asignación para ellas.
- Suspensión, reactivación, reset administrativo, alta, edición, acceso e invitaciones son POST con CSRF/rate limit y generan `AdminAuditEvent`. No existe todavía una infraestructura de revocación inmediata de sesiones ya emitidas.

## Flujos críticos

### Compra por transferencia

1. Cliente agrega productos al carrito.
2. Checkout revalida usuario, stock, catálogo y precios.
3. Se crean pedido, subpedidos, artículos, reservas y pago pendiente en una transacción.
4. Cliente ve instrucciones de transferencia y puede subir comprobante.
5. Comprobante pasa a revisión manual.
6. La aprobación manual desde Admin web o CLI consume reservas y confirma pedido/subpedidos mediante el mismo servicio de dominio.
7. Rechazo o vencimiento libera reservas y cancela/expira el pedido según corresponda.

### Fulfillment y retiro

1. El vendedor crea un `SellerInboundPackage`, imprime su etiqueta y lo marca listo para drop-off.
2. ECUVEL lo recibe en una `WarehouseLocation`; esa recepción inicia su estado logístico en el `Warehouse` correspondiente y la custodia queda en el punto.
3. Un traslado inter-puntos asignado conserva la custodia en origen hasta confirmar pickup.
4. Pickup transfiere la custodia a un usuario ECUVEL activo y deja el paquete en tránsito.
5. La llegada correcta transfiere custodia al Warehouse destino; una llegada a otro punto conserva el destino esperado y marca desviación.
6. Una desviación se corrige creando un nuevo traslado desde el punto real al destino original, sin borrar el recorrido anterior.
7. Separadamente, reservas pagadas, picking y empaque producen `OrderPackage`; la entrega al comprador continúa exigiendo el handover completo y no se activa por una llegada entre puntos.

### Operación con escáner

1. El operador elige un `Warehouse` activo como punto operativo; el ID interno queda en la sesión administrativa y se revalida dentro de cada mutación.
2. Un lector USB funciona como teclado: escribe en el input enfocado y Enter inicia la identificación. No se usa WebUSB ni cámara.
3. Recepción en punto exige una `WarehouseLocation` activa de tipo `RECEIVING` del punto actual y el multiconjunto exacto de `OrderItem.seller_sku_snapshot` declarado en el paquete.
4. Salida y llegada validan paquete, traslado, punto y custodia otra vez bajo bloqueo antes de llamar al servicio logístico canónico.
5. Una llegada a un punto distinto registra la ubicación real y una desviación; no la corrige ni la resuelve automáticamente.
6. Entrega al cliente localiza al comprador mediante `U-...`, pero ese código no autentica. El operador debe confirmar identidad física y escanear todos los `OrderPackage` del pedido; no existe entrega parcial.
7. Consulta rápida reconoce por separado paquetes de entrada y salida, detecta códigos ambiguos y no modifica datos.

### Reseñas verificadas

1. Solo un artículo con paquete entregado puede reseñarse.
2. El envío crea `ProductReviewRevision` y ejecuta el motor determinístico con su versión y hash de lexicón congelados en un assessment append-only.
3. Texto limpio sin imágenes se publica automáticamente, incluso con una estrella o comentario negativo; señales, imágenes o fallo técnico conservan la reseña pendiente.
4. ECUVEL staff con `reviews.moderate` decide manualmente desde `/admin/reviews`. Aprobar o rechazar añade una decisión; nunca edita assessments ni señales existentes.
5. Un rechazo exige código de motivo, se notifica mediante outbox y permite al cliente enviar una nueva revisión. La revisión rechazada y su media permanecen en el historial privado.
6. Solo la revisión vigente en estado `PUBLISHED` afecta producto y tienda y puede exponer sus imágenes.

### Onboarding vendedor

1. Usuario inicia `/partners`.
2. Completa datos y documentos.
3. Envía solicitud.
4. ECUVEL staff revisa en `/admin/stores` y puede solicitar correcciones específicas; cada reenvío crea un nuevo evento `PENDING` sin borrar el historial.
5. Aprobar la verificación cambia el onboarding a `APPROVED` y habilita el contrato, pero conserva `Store.PENDING_REVIEW` e `is_verified=False`.
6. El seller solicita OTP, acepta las declaraciones y firma el contrato mediante el servicio canónico.
7. Solo esa aceptación cambia el onboarding a `COMPLETED`, la tienda a `ACTIVE + verified` y provisiona su ubicación comercial predeterminada.
8. Productos queda disponible para crear borradores.

### Borradores de producto

1. Vendedor selecciona categoría y subcategoría.
2. Se crea o recupera un `ProductDraft`.
3. El código público del producto se genera automáticamente.
4. Las variantes V4 usan modo `family`: la publicación madre solo conserva información compartida y cada presentación controla opciones, precio, precio anterior, stock, SKU e imágenes por color. Admiten hasta 3 campos, 12 valores distintos por campo y 50 filas, sin producto cartesiano.
5. En Electronics Phones, Color es el único eje visual y admite una galería independiente de 1 a 6 imágenes por valor, con mínimo 3 imágenes totales.
6. Las imágenes que pierden su color pasan a una bandeja sin asignar y pueden moverse a otro color sin borrar archivos.
7. El formulario guarda datos incompletos como borrador e integra variantes y galerías con autoguardado y checklist.
8. Enviar a revisión marca el borrador como `SUBMITTED`, sin publicar nada. Solo una aprobación POST de ECUVEL materializa el catálogo.

### Moderación y publicación de productos

1. ECUVEL revisa exclusivamente borradores en estados de moderación mediante `/admin/products`; sus GET de listado, preview y media privada son read-only.
2. La prevalidación deriva pendientes de las mismas plantillas y validaciones del borrador. El checklist manual registra la decisión humana, pero no sustituye la validación canónica.
3. `CHANGES_REQUESTED` y `REJECTED` guardan eventos append-only con moderador, motivo, observación y snapshot del checklist. Solo `CHANGES_REQUESTED` vuelve al flujo editable del vendedor.
4. Aprobar bloquea y revalida el borrador, la tienda, categorías, variantes, medios, comisión y ubicación comercial de stock. Después materializa `Product`, solo las `ProductVariant` activas, una `SellerOffer` por presentación, `ProductMedia`, `InventoryBalance`, movimiento inicial, `ProductDraftPublication` y evento `APPROVED`.
5. `ProductDraftPublication` es único por borrador y producto; evita duplicados y conserva la trazabilidad del origen. El borrador aprobado no se elimina.
6. Las variantes V4, sus SKU estables, configuración, eje visual y asociaciones de media por color se conservan en el catálogo público. El storefront usa sus selectores, galerías, precio y stock normales.
7. La comisión comercial no admite excepciones por tienda. Para cada presentación con precio menor a USD 3.00 se aplica USD 0.25 fijo; desde USD 3.00 se utiliza el porcentaje de la categoría más específica, luego sus ancestros y finalmente una regla global explícita. El precio debe ser mayor a USD 0.25.
8. La comisión se calcula con `Decimal`, centavos y `ROUND_HALF_UP`; se muestra dinámicamente mientras se edita y queda congelada por SKU al enviar. Moderación y publicación usan exclusivamente ese snapshot. Un reenvío después de correcciones crea un snapshot nuevo.
9. Cada tienda `ACTIVE + VERIFIED` recibe de forma idempotente una `StoreInventoryLocation` predeterminada y activa dentro de un `Warehouse` comercial asociado a la propia tienda. La publicación vuelve a garantizarla como backstop. El stock inicial nunca usa `admin_operating_warehouse_id` ni un punto logístico ECUVEL.
10. Cada `SellerOffer` conserva `commission_type`, porcentaje o tarifa fija y moneda. La comisión del seller ya cubre marketplace y logística seller → red ECUVEL en esta fase; no se añaden cargos paralelos de transporte o handling al seller.
11. Los archivos del draft siguen privados. Al aprobar se verifican y copian a almacenamiento de catálogo con identificadores públicos nuevos; si falla la operación, se revierten las filas y se eliminan las copias creadas.

## Reglas importantes del proyecto

- Los UUID son identificadores internos; no deben mostrarse como identidad pública salvo que una ruta técnica lo requiera.
- Hay códigos públicos secuenciales nuevos:
  - usuarios: `U-00000001`;
  - tiendas: `PPP-00000001`;
  - productos/borradores: `PPP-00000001-000001`.
- `public_code` existente queda como compatibilidad legacy y no debe sobrescribirse sin una decisión explícita.
- En Partners, el código de producto vive actualmente en `ProductDraft.seller_sku`; `ProductDraft.barcode` debe reflejar el mismo valor.
- La condición de borradores de producto queda fija en `NEW`.
- Los borradores no publican productos ni ofertas por sí solos; solo la aprobación administrativa POST ejecuta la publicación real.
- Un borrador `SUBMITTED` sin snapshot de comisión nunca se recalcula silenciosamente al aprobar: debe volver a `CHANGES_REQUESTED` y ser reenviado por el seller.
- Los cambios futuros de precio de una oferta requerirán un flujo comercial explícito porque cruzar USD 2.99 → USD 3.00 cambia de tarifa fija a porcentaje; esta fase no modifica ofertas publicadas silenciosamente.
- Existe un contrato puro de conversión de borrador V4 (con migración diferida no destructiva de V2/V3) reutilizado por el servicio transaccional de publicación. La configuración pública `ProductMedia`/variantes conserva bindings por color y SKU.
- Los archivos privados no deben ir bajo `static`.
- El acceso de administrador ECUVEL no se deriva de ser `OWNER` o `ADMINISTRATOR` de una tienda; exige `User.is_ecuvel_staff`.
- No se debe crear un modelo de login paralelo para empleados: toda autenticación pertenece a `User` y los datos laborales pertenecen a `StaffProfile`.
- Estado laboral, disponibilidad operativa y acceso/autenticación no deben colapsarse en un único booleano. Suspender empleo puede deshabilitar acceso como regla de seguridad, pero reactivar empleo no debe habilitarlo implícitamente.
- Un Punto ECUVEL asignable a personal es un `Warehouse` activo sin `seller_store_id`; una bodega comercial seller nunca es asignación laboral.
- Los GET de `/admin` son de solo lectura y no deben aprobar pagos, mover inventario ni cambiar estados de dominio.
- El punto operativo del Scanner se conserva como `admin_operating_warehouse_id` en la sesión firmada; nunca se confía en él sin revalidar Warehouse y WarehouseLocation en servidor.
- `User.public_account_code` permite buscar al comprador en la entrega física, pero no sustituye documento, QR firmado, OTP ni otra autenticación; la confirmación humana es obligatoria.
- Las transiciones de Fulfillment Admin son exclusivamente POST y actualizan atómicamente estado actual, traslado, custodia y evento; los eventos logísticos no se editan ni eliminan.
- Los GET públicos o de cliente no deben mutar pagos, pedidos, reservas, inventario ni fulfillment.
- La revisión web de comprobantes, productos, reseñas y onboarding de tiendas está limitada a ECUVEL staff activo y reutiliza reglas transaccionales del dominio. Reseñas separa `reviews.view` de `reviews.moderate`; la decisión manual siempre es POST con CSRF, rate limit, bloqueo y control de revisión vigente.
- La moderación automática de reseñas es determinística y local: no usa LLM, sentimiento, puntuación, visión ni proveedores externos. El lexicón se versiona en `app/data/review_moderation_es_v1.json` y se provisiona de forma idempotente con `flask review-moderation bootstrap`; lexicón vacío o error del motor debe fallar cerrado y dejar la reseña pendiente.
- El correo transaccional usa el servicio canónico `MailService`. `memory` es exclusivo de pruebas, `console` es para desarrollo y `resend` es el backend productivo; producción rechaza `console` al iniciar. Remitente, reply-to, URL pública, timeout y credencial se configuran por entorno y ningún secreto pertenece al repositorio.
- Verificación de cuenta, restablecimiento de contraseña e invitaciones de personal usan plantillas HTML/texto y enlaces generados desde `PUBLIC_BASE_URL`. Los rechazos de reseñas se envían mediante outbox durable y el dispatcher periódico `review-notifications`; nunca se envía correo externo dentro de la transacción de moderación.
- El dominio remitente productivo previsto es `ecuvel.com`, cuya verificación se realiza externamente en Resend. El repositorio solo conserva nombres de variables y configuración reproducible, no credenciales ni datos privados del proveedor.
- El lexicón determinístico de reseñas incluye cobertura español/ES-EC versionada. Las coincidencias locales, variantes censuradas, mayúsculas, acentos y plurales generan señales `FLAG`; ninguna de estas reglas auto-rechaza una reseña.
- `ProductReviewRevision`, assessments, señales y decisiones son append-only. Una nueva corrección crea otra revisión y las imágenes quedan vinculadas a esa revisión; nunca se reutiliza media rechazada como pública.
- No se deben ejecutar comandos destructivos como reset de Git, limpieza masiva, downgrade de base o borrado de volúmenes sin autorización explícita.

## Comandos y validaciones habituales

Desde `E:\ecuvel`:

```powershell
docker compose ps
docker compose exec web flask --app wsgi:app db current
docker compose exec web flask --app wsgi:app db heads
docker compose exec web flask --app wsgi:app review-moderation bootstrap
docker compose exec web flask --app wsgi:app review-notifications dispatch --limit 50
docker compose exec web python -m compileall app
docker compose exec web pytest -q tests/<archivo_o_filtro>
git diff --check
git status --short
git diff --stat
```

Para pruebas aisladas:

```powershell
docker compose -p ecuvel-test -f compose.test.yaml run --rm test pytest -q tests/<archivo_o_filtro>
docker compose -p ecuvel-test -f compose.test.yaml down -v --remove-orphans
```

### Prueba manual de correo productivo

La prueba real se realiza fuera de pytest y sin guardar credenciales en archivos
rastreados. Configurar localmente `MAIL_BACKEND=resend`, `MAIL_FROM`,
`PUBLIC_BASE_URL` y `RESEND_API_KEY`; luego levantar `web` y
`review_notification_dispatcher`. Verificar con una cuenta de prueba: registro y
confirmación de correo, recuperación de contraseña, invitación de personal y
rechazo de reseña despachado desde el outbox. Confirmar texto/HTML, enlaces,
acentos, llegada a Inbox y estado `SENT` del evento. Nunca usar una cuenta
personal en fixtures ni ejecutar esta comprobación desde la suite automática.

Si la suite completa tarda demasiado, reportar timeout con precisión y conservar los resultados de pruebas específicas; no presentar una suite incompleta como aprobada.

## Límites actuales y pendientes conocidos

- No hay pasarela de tarjeta real; tarjeta permanece deshabilitada.
- Existe un Centro de Operaciones administrativo conectado con el listado y detalle real de pedidos. La revisión de comprobantes, la moderación/publicación de productos, la moderación de reseñas, la moderación del onboarding de tiendas y la gestión de usuarios/personal están disponibles para ECUVEL staff.
- Pedidos ya tiene listado, detalle, revisión de pago y enlaces bidireccionales con Fulfillment, Scanner e Inventario. Fulfillment Admin ya tiene lista y detalle operativos. Escáner cubre las cinco operaciones físicas y la consulta rápida; Inventario cubre paquetes, esperados, existencias, movimientos y conteo físico por punto. Marketplace, finanzas, gestión completa de incidencias y auditoría completa todavía no tienen pantallas operativas propias.
- No existe todavía un QR firmado, token de retiro de un solo uso ni OTP para entrega. El código público `U-...` solo identifica la cuenta y la entrega depende de verificación física explícita del operador.
- No hay gestión completa de inventario para vendedores desde Partners.
- Checkout ya copia a `OrderItem` el importe monetario de comisión y la tasa (cero para tarifa fija). Antes de construir liquidaciones históricas completas conviene añadir snapshots explícitos de `commission_type` y tarifa fija unitaria; el importe congelado actual sí permite calcular el neto de la venta sin consultar reglas vigentes.
- No hay favoritos anónimos persistentes, listas múltiples, notificaciones, chat, social login, 2FA, passkeys ni panel de vendedor completo.
- No hay SMTP/SMS productivo integrado; los backends de desarrollo/pruebas son controlados por configuración.
- No existe un modelo formal de flota o vehículos asignables; `LogisticsTransfer.vehicle_code` sigue siendo un snapshot textual. No mostrar ni persistir una asignación laboral de vehículo hasta que exista ese dominio.
- La autorización granular se aplica al nuevo módulo de Usuarios/Personal. Scanner, Fulfillment y módulos administrativos anteriores todavía usan el guard legacy `is_ecuvel_staff`; extender el mapa de permisos a esas rutas requiere una fase compatible y pruebas de regresión dedicadas.
- Las imágenes reales de catálogo y logos persistentes aún son limitados; se usan placeholders cuando corresponde.

## Inventario operativo por punto ECUVEL

- Scanner e Inventario comparten el punto operativo mediante la clave de sesión firmada `admin_operating_warehouse_id`. Toda lectura y mutación vuelve a validar que el almacén y la ubicación existan y estén activos.
- El inventario físico de paquetes usa dos fuentes canónicas: `SellerInboundPackage + LogisticsPackageState` para paquetes de tiendas y `OrderPackage` para paquetes de salida al comprador.
- El stock comercial continúa separado en `InventoryBalance`. La disponibilidad se calcula como existencia menos reservado y bloqueado; nunca debe inferirse de la cantidad de paquetes físicos.
- Publicar inicializa `InventoryBalance` en la bodega del seller y no crea paquetes, trazabilidad o inventario físico ECUVEL. La reserva aumenta `reserved` sin reducir `on_hand`; picking consume ambos una sola vez. El drop-off no vuelve a descontar stock.
- Los almacenes seller (`Warehouse.seller_store_id IS NOT NULL`) no son puntos operativos y quedan excluidos de Scanner, Fulfillment, selectores y KPI de Admin Inventory. La red beta usa actualmente Punto A como único Punto ECUVEL, aunque el modelo admite múltiples puntos.
- El circuito físico ECUVEL empieza únicamente cuando un paquete real de entrada se recibe mediante Scanner; hasta entonces el stock publicado sigue siendo inventario comercial del seller.
- `/admin/inventory` ofrece Paquetes, Existencias y Movimientos; `/admin/inventory/expected` separa paquetes esperados que todavía no forman parte del inventario físico del punto.
- Los conteos físicos usan `PhysicalInventoryCount`, `PhysicalInventoryCountExpectedPackage` y `PhysicalInventoryCountScan`. El baseline queda congelado al iniciar, solo puede existir un conteo abierto por almacén, los escaneos duplicados son idempotentes y el conteo finalizado es inmutable desde los servicios web.
- Rechazar una recepción fuera de ruta solo registra `INCIDENT_REPORTED`; no cambia ubicación, custodia, estado ni traslado. Aceptarla usa la recepción normal y marca la desviación de forma explícita.
- Los movimientos de inventario son una proyección de eventos existentes de logística, movimientos comerciales, entregas y hallazgos de conteo. No existe una bitácora genérica paralela ni un ajuste libre desde la interfaz.
- El acceso operativo futuro debe asignarse a cuentas personales con roles y puntos autorizados. No se creará una contraseña compartida por punto operativo.

## Cómo mantener este archivo

Actualizar este archivo cuando cambie cualquiera de estos puntos:

- nuevo módulo o flujo de negocio relevante;
- cambio en rutas públicas o privadas importantes;
- migración que altere entidades principales;
- cambio en reglas de seguridad, identificación pública, almacenamiento privado o autorización;
- cambio en checkout, pagos, inventario, fulfillment, reseñas, Partners o publicación de productos;
- decisión de producto que cambie límites, supuestos o pendientes.

Mantenerlo corto y accionable. Si un detalle pertenece mejor a una prueba, migración o doc de diseño, enlazarlo o resumirlo en vez de duplicarlo.
