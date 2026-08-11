# Contexto permanente de ECUVEL

Última actualización: 2026-08-08.

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

Inventario maneja recepción, putaway, reserva, liberación, consumo, expiración y picking. El dominio conserva dos paquetes distintos: `SellerInboundPackage` representa la entrega vendedor → ECUVEL y, después de su recepción física, puede continuar por la red inter-puntos; `OrderPackage` representa el paquete ECUVEL → comprador para empaque, retiro y handover. Fulfillment Admin mantiene un estado actual transaccional, traslados y eventos append-only para responder dónde está el paquete de entrada, quién lo custodia, hacia dónde va y si se desvió. `Warehouse` es el punto ECUVEL y `WarehouseLocation` su ubicación interna. Las reservas y movimientos de inventario no se alteran por registrar trazabilidad logística.

### Pedidos del cliente

`/pedidos` y el detalle de pedido muestran solo pedidos del usuario autenticado. Los estados visuales se derivan de pagos, comprobantes, pedidos, subpedidos y paquetes. Los GET de pedidos son de solo lectura; cancelación y comprobantes usan POST protegidos.

### Favoritos

Los favoritos son persistentes por usuario y producto. Se muestran en página propia, header y tarjetas públicas. El producto es la entidad pública estable; la oferta canónica solo define precio, disponibilidad y carrito.

### Reseñas verificadas

Las reseñas se crean por `OrderItem` entregado, quedan pendientes de moderación y solo se publican vía CLI. Las imágenes de reseña se guardan privadas hasta publicación. El detalle de producto muestra agregados, distribución, lista pública segura y fotos publicadas. La calificación de tienda se deriva de reseñas publicadas de productos vendidos por esa tienda.

### Tiendas públicas

`/tiendas/<slug>` muestra solo tiendas activas. La cabecera usa nombre comercial, avatar por inicial, código público, calificación derivada y conteo de productos visibles. Los modales de información/rating/productos no exponen datos privados ni métricas de pedidos.

### ECUVEL Partners

`/partners` es el área privada para vendedores autenticados. Incluye onboarding de tienda, revisión por CLI, contrato con código de confirmación, activación de tienda, productos, reseñas, pedidos y ventas. Los productos se crean primero como borradores persistentes; no se crean `Product`, `ProductVariant` ni `SellerOffer` hasta una fase posterior de aprobación/publicación. El flujo de pedidos separa la preparación de la tienda mediante `SellerInboundPackage` del fulfillment hacia el comprador mediante `OrderPackage`. Ventas deriva importes de `SellerOrder` y utiliza `SellerPayout`/`SellerPayoutItem` para liquidaciones ECUVEL → tienda.

### ECUVEL Admin

`/admin` es una superficie administrativa separada de storefront y Partners. Solo admite usuarios autenticados, activos, con estado `ACTIVE` e `is_ecuvel_staff=True`. El Centro de Operaciones calcula KPIs, flujo, alertas, colas de atención, actividad reciente y búsqueda limitada a partir de tablas reales. `/admin/orders` ofrece listado paginado y filtrable por `Order`, detalle histórico por número público y revisión privada de comprobantes. `/admin/fulfillment` es el centro de control de paquetes de entrada que ya fueron recibidos por ECUVEL: lista ubicación, custodia, destino, último movimiento, tiempo en estado y desviaciones; el detalle permite asignar/reasignar traslado y crear una corrección de ruta mediante POST. `/admin/scanner` es la superficie de ejecución física: recepción vendedor → ECUVEL, pickup a transportista, llegada inter-puntos, entrega completa al comprador y consulta read-only. Reutiliza los servicios canónicos de dominio con CSRF, rate limit, locking, idempotencia y Post/Redirect/Get. Los GET son read-only. Los módulos no implementados se identifican como “Próximamente”.

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
2. La reseña queda pendiente.
3. Moderación CLI aprueba o rechaza.
4. Solo reseñas publicadas afectan producto y tienda.

### Onboarding vendedor

1. Usuario inicia `/partners`.
2. Completa datos y documentos.
3. Envía solicitud.
4. Revisión CLI aprueba, rechaza o pide correcciones.
5. Si se aprueba, firma contrato mediante código de confirmación.
6. La tienda queda activa y verificada.
7. Productos queda disponible para crear borradores.

### Borradores de producto

1. Vendedor selecciona categoría y subcategoría.
2. Se crea o recupera un `ProductDraft`.
3. El código público del producto se genera automáticamente.
4. Las variantes V4 usan modo `family`: la publicación madre solo conserva información compartida y cada presentación controla opciones, precio, precio anterior, stock, SKU e imágenes por color. Admiten hasta 3 campos, 12 valores distintos por campo y 50 filas, sin producto cartesiano.
5. En Electronics Phones, Color es el único eje visual y admite una galería independiente de 1 a 6 imágenes por valor, con mínimo 3 imágenes totales.
6. Las imágenes que pierden su color pasan a una bandeja sin asignar y pueden moverse a otro color sin borrar archivos.
7. El formulario guarda datos incompletos como borrador e integra variantes y galerías con autoguardado y checklist.
8. Enviar a revisión marca el borrador como `SUBMITTED`, sin publicar nada.

## Reglas importantes del proyecto

- Los UUID son identificadores internos; no deben mostrarse como identidad pública salvo que una ruta técnica lo requiera.
- Hay códigos públicos secuenciales nuevos:
  - usuarios: `U-00000001`;
  - tiendas: `PPP-00000001`;
  - productos/borradores: `PPP-00000001-000001`.
- `public_code` existente queda como compatibilidad legacy y no debe sobrescribirse sin una decisión explícita.
- En Partners, el código de producto vive actualmente en `ProductDraft.seller_sku`; `ProductDraft.barcode` debe reflejar el mismo valor.
- La condición de borradores de producto queda fija en `NEW`.
- Los borradores no publican productos ni ofertas.
- Existe un contrato puro de conversión de borrador V4 (con migración diferida no destructiva de V2/V3) y modelos públicos `ProductMedia`/configuración de variantes, pero la aprobación administrativa final continúa fuera de alcance.
- Los archivos privados no deben ir bajo `static`.
- El acceso de administrador ECUVEL no se deriva de ser `OWNER` o `ADMINISTRATOR` de una tienda; exige `User.is_ecuvel_staff`.
- Los GET de `/admin` son de solo lectura y no deben aprobar pagos, mover inventario ni cambiar estados de dominio.
- El punto operativo del Scanner se conserva como `admin_operating_warehouse_id` en la sesión firmada; nunca se confía en él sin revalidar Warehouse y WarehouseLocation en servidor.
- `User.public_account_code` permite buscar al comprador en la entrega física, pero no sustituye documento, QR firmado, OTP ni otra autenticación; la confirmación humana es obligatoria.
- Las transiciones de Fulfillment Admin son exclusivamente POST y actualizan atómicamente estado actual, traslado, custodia y evento; los eventos logísticos no se editan ni eliminan.
- Los GET públicos o de cliente no deben mutar pagos, pedidos, reservas, inventario ni fulfillment.
- La revisión web de comprobantes está limitada a ECUVEL staff activo y reutiliza las mismas reglas transaccionales del dominio; otras moderaciones sensibles continúan por CLI cuando no existe una interfaz web explícita.
- No se deben ejecutar comandos destructivos como reset de Git, limpieza masiva, downgrade de base o borrado de volúmenes sin autorización explícita.

## Comandos y validaciones habituales

Desde `E:\ecuvel`:

```powershell
docker compose ps
docker compose exec web flask --app wsgi:app db current
docker compose exec web flask --app wsgi:app db heads
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

Si la suite completa tarda demasiado, reportar timeout con precisión y conservar los resultados de pruebas específicas; no presentar una suite incompleta como aprobada.

## Límites actuales y pendientes conocidos

- No hay pasarela de tarjeta real; tarjeta permanece deshabilitada.
- Existe un Centro de Operaciones administrativo conectado con el listado y detalle real de pedidos. La revisión de comprobantes de pago está disponible para ECUVEL staff; reseñas, onboarding y productos continúan por CLI donde no haya una interfaz administrativa explícita.
- No hay publicación final de productos desde borradores hacia catálogo público.
- Pedidos ya tiene listado, detalle, revisión de pago y enlaces bidireccionales con Fulfillment, Scanner e Inventario. Fulfillment Admin ya tiene lista y detalle operativos. Escáner cubre las cinco operaciones físicas y la consulta rápida; Inventario cubre paquetes, esperados, existencias, movimientos y conteo físico por punto. Marketplace, finanzas, gestión completa de incidencias y auditoría completa todavía no tienen pantallas operativas propias.
- No existe todavía un QR firmado, token de retiro de un solo uso ni OTP para entrega. El código público `U-...` solo identifica la cuenta y la entrega depende de verificación física explícita del operador.
- No hay gestión completa de inventario para vendedores desde Partners.
- No hay favoritos anónimos persistentes, listas múltiples, notificaciones, chat, social login, 2FA, passkeys ni panel de vendedor completo.
- No hay SMTP/SMS productivo integrado; los backends de desarrollo/pruebas son controlados por configuración.
- Las imágenes reales de catálogo y logos persistentes aún son limitados; se usan placeholders cuando corresponde.

## Inventario operativo por punto ECUVEL

- Scanner e Inventario comparten el punto operativo mediante la clave de sesión firmada `admin_operating_warehouse_id`. Toda lectura y mutación vuelve a validar que el almacén y la ubicación existan y estén activos.
- El inventario físico de paquetes usa dos fuentes canónicas: `SellerInboundPackage + LogisticsPackageState` para paquetes de tiendas y `OrderPackage` para paquetes de salida al comprador.
- El stock comercial continúa separado en `InventoryBalance`. La disponibilidad se calcula como existencia menos reservado y bloqueado; nunca debe inferirse de la cantidad de paquetes físicos.
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
