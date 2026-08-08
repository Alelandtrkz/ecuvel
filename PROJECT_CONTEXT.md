# Contexto permanente de ECUVEL

Última actualización: 2026-08-05.

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

El método funcional es transferencia bancaria. El comprador sube comprobantes privados; el sistema valida archivo, lo almacena fuera de `static`, marca el pago como en proceso y deja la decisión financiera a comandos CLI. Existe prevalidación asistida de comprobantes con evidencia estructurada, pero nunca aprueba ni rechaza pagos por sí sola.

### Inventario y fulfillment

Inventario maneja recepción, putaway, reserva, liberación, consumo, expiración y picking. Fulfillment crea paquetes uno-a-uno por artículo, permite empaque, preparación para retiro y entrega atómica por escaneo completo. Las reservas y movimientos se registran con claves idempotentes.

### Pedidos del cliente

`/pedidos` y el detalle de pedido muestran solo pedidos del usuario autenticado. Los estados visuales se derivan de pagos, comprobantes, pedidos, subpedidos y paquetes. Los GET de pedidos son de solo lectura; cancelación y comprobantes usan POST protegidos.

### Favoritos

Los favoritos son persistentes por usuario y producto. Se muestran en página propia, header y tarjetas públicas. El producto es la entidad pública estable; la oferta canónica solo define precio, disponibilidad y carrito.

### Reseñas verificadas

Las reseñas se crean por `OrderItem` entregado, quedan pendientes de moderación y solo se publican vía CLI. Las imágenes de reseña se guardan privadas hasta publicación. El detalle de producto muestra agregados, distribución, lista pública segura y fotos publicadas. La calificación de tienda se deriva de reseñas publicadas de productos vendidos por esa tienda.

### Tiendas públicas

`/tiendas/<slug>` muestra solo tiendas activas. La cabecera usa nombre comercial, avatar por inicial, código público, calificación derivada y conteo de productos visibles. Los modales de información/rating/productos no exponen datos privados ni métricas de pedidos.

### ECUVEL Partners

`/partners` es el área privada para vendedores autenticados. Incluye onboarding de tienda, revisión por CLI, contrato con código de confirmación, activación de tienda y flujo de productos. Los productos se crean primero como borradores persistentes; no se crean `Product`, `ProductVariant` ni `SellerOffer` hasta una fase posterior de aprobación/publicación.

## Flujos críticos

### Compra por transferencia

1. Cliente agrega productos al carrito.
2. Checkout revalida usuario, stock, catálogo y precios.
3. Se crean pedido, subpedidos, artículos, reservas y pago pendiente en una transacción.
4. Cliente ve instrucciones de transferencia y puede subir comprobante.
5. Comprobante pasa a revisión manual.
6. Aprobación CLI consume reservas y confirma pedido/subpedidos.
7. Rechazo o vencimiento libera reservas y cancela/expira el pedido según corresponda.

### Fulfillment y retiro

1. Reservas pagadas se consumen.
2. Picking descuenta existencia física y reservado.
3. Se crea un paquete por artículo.
4. El paquete pasa de creado a empacado.
5. Luego se prepara en ubicación de retiro.
6. La entrega exige escanear el conjunto completo de paquetes del pedido.

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
- Los GET públicos o de cliente no deben mutar pagos, pedidos, reservas, inventario ni fulfillment.
- Revisión administrativa sensible sigue siendo por CLI, no por panel web, hasta existir autenticación/roles administrativos adecuados.
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
- No hay panel administrativo web seguro; revisión de pagos, comprobantes, reseñas y onboarding se hace por CLI.
- No hay publicación final de productos desde borradores hacia catálogo público.
- No hay gestión completa de inventario para vendedores desde Partners.
- No hay favoritos anónimos persistentes, listas múltiples, notificaciones, chat, social login, 2FA, passkeys ni panel de vendedor completo.
- No hay SMTP/SMS productivo integrado; los backends de desarrollo/pruebas son controlados por configuración.
- Las imágenes reales de catálogo y logos persistentes aún son limitados; se usan placeholders cuando corresponde.

## Cómo mantener este archivo

Actualizar este archivo cuando cambie cualquiera de estos puntos:

- nuevo módulo o flujo de negocio relevante;
- cambio en rutas públicas o privadas importantes;
- migración que altere entidades principales;
- cambio en reglas de seguridad, identificación pública, almacenamiento privado o autorización;
- cambio en checkout, pagos, inventario, fulfillment, reseñas, Partners o publicación de productos;
- decisión de producto que cambie límites, supuestos o pendientes.

Mantenerlo corto y accionable. Si un detalle pertenece mejor a una prueba, migración o doc de diseño, enlazarlo o resumirlo en vez de duplicarlo.
