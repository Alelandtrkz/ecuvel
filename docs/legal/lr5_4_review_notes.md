# LR5.4 Review Notes

## 1. Baseline

- SHA local y remoto: `8aac7b64c68a6bde9c05f5ebb34567ab837e1b2e`.
- Commit: `feat(legal): add versioned legal evidence foundation`.
- Rama `master`, worktree limpio al inicio y cabeza Alembic previa única `9e5f7a8b0c1d`.
- La suite completa LR5.3 reportada fue `1323 passed`; no se repitió como prueba de baseline.

## 2. Register integration

Con `LEGAL_ENFORCEMENT_ENABLED=true`, Registro resuelve Términos y Privacidad contra el registro estático, la versión DB actual y el contenido de plantilla. Exige acciones separadas y registra, dentro de la misma transacción que crea la cuenta, Términos como `ACCEPTED` y Privacidad como `ACKNOWLEDGED`, ambas con fuente `REGISTER`. No crea consentimiento de marketing.

## 3. Checkout integration

Checkout conserva identidad verificada y mayoría de edad. En modo enforced calcula únicamente requisitos actuales faltantes para el usuario y registra esa evidencia en la misma transacción que crea el pedido. Usuarios pre-beta pueden navegar normalmente; solo el acto de compra exige evidencia faltante.

Una prueba de regresión fuerza un fallo de creación del pedido después del `flush` de evidencia y confirma el rollback conjunto: no queda pedido ni evidencia legal huérfana.

## 4. Version race protection

GET presenta identificadores públicos exactos. POST vuelve a resolver la versión actual del servidor y compara el identificador presentado. Un cambio v1→v2 invalida la acción anterior: no crea cuenta/pedido ni aplica la acción a v2. Los UUID, hashes y timestamps no provienen del cliente.

## 5. Prepublication mode

`LEGAL_ENFORCEMENT_ENABLED` queda en `false`. Registro y Checkout siguen operativos sin inventar versiones ni evidencia. LR5.4 no publica documentos. La telemetría opcional no utiliza este bypass y permanece apagada sin elección válida.

Phone OTP permanece deshabilitado en pre-beta. La configuración bloquea explícitamente iniciar con `PHONE_OTP_ENABLED=true` y `LEGAL_ENFORCEMENT_ENABLED=true` hasta integrar evidencia legal en el registro telefónico de clientes; el OTP contractual Seller no cambia.

## 6. Footer

Se incorporan enlaces públicos a `/docs`, `/docs/ecuvel/como-funciona`, `/docs/ecuvel/contacto`, `/docs/compradores`, `/docs/privacidad`, `/docs/vendedores`, `/docs/legal/operador` y `/privacidad/preferencias`. No se enlazan artículos DRAFT desde el footer.

## 7. Privacy preference architecture

Invitados guardan una decisión mínima en la sesión firmada de ECUVEL, sin crear un UUID conductual. Usuarios autenticados generan eventos append-oriented en `user_privacy_preference_events`. Una preferencia de cuenta existente prevalece sobre la sesión; si no existe, la preferencia válida del navegador puede seguir aplicando sin convertirse automáticamente en una decisión de cuenta.

Un `REJECTED` autenticado neutraliza cualquier `GRANTED` anterior del navegador en ese mismo browser para impedir que reaparezca tras cerrar sesión. Un `GRANTED` de cuenta nunca se copia silenciosamente al navegador.

La FK de preferencia usa `ON DELETE CASCADE`: esta evidencia opcional no bloquea la eliminación futura de una cuenta. No se añadió prohibición permanente de DELETE. La política final de retención/purga sigue pendiente para LR5.5.

## 8. Telemetry gating

El servicio central `privacy_preferences.py` decide si la telemetría está permitida. La plantilla base solo carga `catalog-telemetry.js` con grant válido. El endpoint directo y las acciones server-side `ADD_TO_CART`/`FAVORITE` consultan el mismo servicio antes de crear UUID o evento. `IMPRESSION` y `CLICK` quedan igualmente protegidos. `PURCHASE` y `DELIVERED` existen en el enum histórico, pero no tienen productores activos en el código auditado.

El ranking context firmado puede seguir generándose porque describe la presentación y no persiste una interacción. Catálogo, búsqueda, carrito, favoritos y compra siguen funcionando cuando la telemetría está apagada.

## 9. Anonymous identifier

`anonymous_session_id()` solo se invoca después de un grant válido y cuando un evento calificante se intenta registrar. Visitar inicio, buscar, abrir producto, agregar al carrito, no elegir o rechazar no crea `catalog_anonymous_session_id`.

## 10. Cookies notice version

Todo grant se liga al identificador y SHA-256 de la versión actual sincronizada de `privacidad/cookies-y-tecnologias-similares`. Sin publicación real y coherente no puede existir grant. Un grant contra v1 queda inactivo cuando v2 se vuelve actual hasta que haya una nueva elección explícita.

## 11. Withdrawal/rejection

`REJECTED` siempre apaga telemetría y no vuelve a activarse por un cambio de aviso. Cambiar de `GRANTED` a `REJECTED` agrega un evento y detiene futuros eventos cliente y servidor. No se borran retroactivamente interacciones históricas.

## 12. Marketing separation

`UserMarketingConsent` no fue modificado ni reutilizado. Aceptar Términos, reconocer Privacidad o permitir telemetría no concede marketing. Un grant de marketing existente tampoco permite telemetría.

## 13. Seller separation

`StoreContractAcceptance`, `StoreContractOtpChallenge`, versiones contractuales, onboarding, PDF y OTP Seller permanecen intactos. No se creó una versión `seller-contract`.

## 14. Migration

La migración LR5.4 `a9b0c1d2e3f4` desciende de `9e5f7a8b0c1d` y crea únicamente enums, tabla, checks, FK e índices para historial de preferencias. No incluye seeds ni backfill; usuarios existentes quedan sin decisión.

## 15. LR5.P activation steps

LR5.P debe realizar en una misma liberación controlada:

1. Crear las versiones inmutables reales de Términos, Privacidad y Cookies desde las plantillas finales.
2. Publicar y sincronizar en el registro estático identificadores y timestamps exactos.
3. Confirmar que rutas, plantillas y hashes coinciden con las versiones DB actuales.
4. Cambiar `LEGAL_ENFORCEMENT_ENABLED=true`.
5. Confirmar que la preferencia queda ligada al aviso Cookies publicado.
6. Verificar en Registro y Checkout la presentación y evidencia separada de Términos/Privacidad.
7. Verificar que telemetría continúa apagada hasta un grant opcional explícito.

## 16. LR5.5 blockers

- Divergencia entre auto-confirmación Seller y producto actual.
- Reconciliación operativa de custodia 14→7 días.
- Notificación Seller.
- Flujo operativo de reembolsos y garantías.
- Seller Contract Canonicalization.
- Controles ARCSA.
- Procedimientos y plazos de retención, anonimización, eliminación y purga para preferencias y telemetría.
- Definir si `PURCHASE`/`DELIVERED` tendrán productores futuros y clasificar/gobernar esos productores antes de activarlos.
