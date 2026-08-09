# Superficies web de ECUVEL

## Estado actual

Durante desarrollo, una sola aplicación Flask sirve tres superficies bajo el mismo host:

- `ecuvel.local/` — storefront y cuenta del comprador;
- `ecuvel.local/partners` — panel privado de tiendas;
- `ecuvel.local/admin` — panel read-only para staff interno de ECUVEL.

Las rutas internas se generan con `url_for()`. El código no depende de `localhost`, de un hostname concreto ni de `SERVER_NAME`.

## Producción futura

La arquitectura puede evolucionar a:

- `ecuvel.com` — storefront;
- `partners.ecuvel.com` — Partners;
- `admin.ecuvel.com` — Admin.

El reverse proxy decidirá qué superficie recibe cada hostname y deberá impedir que las rutas administrativas se sirvan accidentalmente desde el dominio público. En esta fase las rutas internas siguen usando `url_for()` y no se añade configuración de hosts que todavía no tenga un consumidor real.

No se activa todavía `subdomain_matching` ni se configura `SERVER_NAME`, para conservar Docker, desarrollo local y pruebas actuales.

## Aislamiento de sesión

La futura cookie de Admin debe ser host-only para `admin.ecuvel.com`. No debe configurarse anticipadamente `Domain=.ecuvel.com` para compartir una sesión administrativa con storefront o Partners. Si las superficies se despliegan como procesos separados, cada una deberá mantener claves, cookies y controles de acceso acordes a su nivel de riesgo, sin duplicar el dominio de datos.
