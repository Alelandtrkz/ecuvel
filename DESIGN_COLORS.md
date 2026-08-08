# Sistema visual de ECUVEL

Esta guía define los colores, la tipografía y los patrones de interfaz que deben mantenerse en ECUVEL Storefront y ECUVEL Partners. Su objetivo es que las nuevas pantallas se sientan parte del mismo producto, incluso cuando parten de bocetos o referencias externas.

La fuente técnica de verdad está en [`backend/app/static/css/tokens.css`](backend/app/static/css/tokens.css). Si un token cambia, este documento también debe actualizarse.

## Identidad visual

ECUVEL utiliza una interfaz clara, amplia y comercial. El azul identifica acciones, selección y navegación. El blanco domina las superficies; los grises azulados organizan la jerarquía; los colores verde, amarillo, naranja y rojo se reservan para estados semánticos.

Principios:

- Usar azul ECUVEL como color de acción principal.
- Mantener superficies blancas sobre un fondo gris muy claro.
- Preferir bordes sutiles y sombras suaves.
- Usar Onest en toda la interfaz, incluyendo formularios y menús.
- Mantener el texto de opciones y controles ligero; no usar negrita en listas desplegables.
- No introducir otro color corporativo principal sin actualizar esta guía y los tokens.
- No usar verde o turquesa como color decorativo principal. El verde comunica éxito o disponibilidad.

## Paleta corporativa

| Uso | Token | Color | Aplicación |
|---|---|---:|---|
| Azul principal | `--color-primary` | `#085DF8` | Botones principales, enlaces, selección, pestañas activas y foco |
| Azul hover | `--color-primary-hover` | `#1164FF` | Estado hover de acciones principales |
| Azul activo | `--color-primary-active` | `#0050E0` | Estado presionado |
| Azul suave | `--color-primary-soft` | `#EAF1FF` | Opciones seleccionadas, iconos y fondos informativos |
| Blanco | `--color-white` | `#FFFFFF` | Texto sobre azul y superficies |
| Fondo de página | `--color-page-background` | `#FBFCFD` | Fondo general |
| Superficie | `--color-surface` | `#FFFFFF` | Tarjetas, tablas, paneles y modales |
| Superficie secundaria | `--color-surface-subtle` | `#F5F7FA` | Cabeceras de tabla y agrupaciones suaves |
| Texto principal | `--color-text` | `#0C0C0C` | Títulos, contenido principal y valores |
| Texto secundario | `--color-text-secondary` | `#6F8193` | Descripciones, etiquetas y metadatos |
| Texto tenue | `--color-text-muted` | `#7C8DAD` | Placeholders y estados secundarios |
| Borde | `--color-border` | `#D4D9E2` | Inputs y divisiones visibles |
| Borde suave | `--color-border-light` | `#E5E7EB` | Filas, tarjetas y separadores |
| Azul marino | `--color-footer` | `#001F3F` | Footer y fondos oscuros institucionales |

### Colores semánticos

| Estado | Token/color | Uso correcto |
|---|---:|---|
| Éxito | `--color-success` / `#10C44C` | Confirmaciones, disponibilidad y procesos completos |
| Peligro | `--color-danger` / `#E60101` | Errores y acciones destructivas |
| Calificación | `--color-rating` / `#E69301` | Estrellas y valoración |
| Acento cálido | `--color-accent-soft` / `#F0BC61` | Apoyo visual para calificaciones |
| Transferencia | `--color-bank-transfer` / `#FFDD00` | Identificación exclusiva del método bancario |

Los badges usan combinaciones de texto y fondo con contraste suficiente:

| Estado | Texto | Fondo |
|---|---:|---:|
| Activo / Aprobado | `#187547` | `#E7F6ED` |
| En revisión / Listo | `#956200` | `#FFF4D5` |
| Incompleto / Cambios | `#A64B00` | `#FFF0E2` |
| Rechazado | `#B42318` | `#FEECEB` |
| Borrador | `#365B91` | `#EAF2FF` |
| Desactivado | `#637083` | `#E9EDF1` |

No comunicar un estado únicamente mediante color. Incluir siempre texto y, cuando ayude, un icono.

## Tipografía

La familia oficial es **Onest**:

```css
font-family: "Onest", Arial, Helvetica, sans-serif;
```

Se carga con pesos `400`, `500`, `600` y `700`. No usar pesos superiores ni tipografías diferentes para la interfaz. Los SKU y códigos técnicos pueden utilizar una fuente monoespaciada del sistema.

| Estilo | Tamaño / línea | Peso recomendado |
|---|---:|---:|
| Display | `32px / 38px` | 700 |
| Título grande | `27px / 32px` | 700 |
| Título medio | `24px / 30px` | 600–700 |
| Título pequeño | `20px / 24px` | 600–700 |
| Texto grande | `18px / 28px` | 400–600 |
| Cuerpo | `16px / 24px` | 400 |
| Texto pequeño | `14px / 20px` | 400–600 |
| Caption | `12px / 16px` | 400–600 |

Reglas de jerarquía:

- Títulos principales: peso `700` y texto casi negro.
- Etiquetas de formulario: peso `600` o `700`.
- Texto descriptivo: peso `400`, color secundario.
- Opciones de select y menús: peso `400`.
- Botones: peso `600` o `700`.
- Encabezados de tabla: peso `700`, mayúsculas y espaciado de letras moderado.
- Evitar párrafos completos en negrita.

## Espaciado, radios y sombras

La escala base utiliza múltiplos de cuatro:

```text
4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64 px
```

| Elemento | Radio recomendado |
|---|---:|
| Controles pequeños e iconos | `8px` |
| Inputs y botones compactos | `12px` |
| Botones y tarjetas estándar | `16px` |
| Tarjetas principales | `18–24px` |
| Modales | `28px` |
| Badges y chips | `999px` |

Sombras oficiales:

```css
--shadow-small: 0 2px 8px rgba(0, 31, 63, 0.06);
--shadow-card: 0 4px 16px rgba(0, 31, 63, 0.08);
--shadow-modal: 0 12px 32px rgba(0, 31, 63, 0.16);
```

Las sombras deben aportar separación, no convertirse en un borde oscuro. En tablas y formularios extensos se priorizan bordes suaves.

## Componentes

### Botones

Los botones principales usan fondo azul, texto blanco y una altura mínima de `48px`.

```css
.button--primary {
  background: var(--color-primary);
  color: var(--color-white);
}

.button--primary:hover {
  background: var(--color-primary-hover);
}
```

- Acción principal: azul sólido.
- Acción secundaria: fondo blanco o azul suave, borde tenue y texto azul.
- Acción neutral: blanco con borde gris.
- Acción destructiva: rojo; debe estar separada visualmente de las acciones comunes.
- Estado deshabilitado: gris suave, sin perder legibilidad.
- No mostrar dos acciones principales compitiendo en el mismo bloque.

### Campos de formulario

- Altura mínima: `46–48px`.
- Fondo blanco y texto principal.
- Borde normal: `#CBD7E5` o el token de borde.
- Radio: `10–12px`.
- Placeholder: gris tenue y peso `400`.
- Foco: borde azul y halo `0 0 0 3px rgba(8, 93, 248, 0.13)`.
- Mensajes de ayuda debajo del campo; errores en rojo y asociados al control.

### Listas desplegables

Las listas visibles deben usar el selector personalizado de Partners cuando el diseño necesite consistencia entre navegadores.

- Botón cerrado de `46px` como mínimo.
- Texto del valor y de las opciones en peso `400`.
- Menú blanco, radio de `16px`, sombra suave y máximo aproximado de `278px` con scroll.
- Opciones con altura mínima de `44px` y padding `11px 15px`.
- Opción seleccionada con fondo azul suave, texto azul e icono de confirmación.
- Hover y foco con fondo azul suave.
- Flecha alineada a la derecha y rotación al abrir.
- Navegación mediante teclado, `Escape` para cerrar y atributos ARIA correspondientes.
- En móvil el menú debe permanecer dentro del viewport.

No usar opciones en negrita ni el azul nativo del navegador como estilo final.

### Tarjetas y paneles

- Fondo blanco.
- Borde `1px` gris azulado suave.
- Radio de `16–20px`.
- Padding habitual de `20–32px`.
- Sombra pequeña o de tarjeta solo cuando sea necesaria para elevar el bloque.
- Encabezado claro, descripción secundaria y acciones alineadas de forma consistente.

### Tablas y listas administrativas

- Encabezado sobre fondo `#F7F9FC`.
- Separadores `#E5EBF2`.
- Filas de `80–88px` cuando incluyen miniatura.
- Hover apenas visible: `#FAFCFF`.
- Miniaturas con fondo neutro, borde suave y radio de `10–12px`.
- Acciones secundarias dentro de un menú horizontal de tres puntos.
- En móvil cada fila se convierte en tarjeta etiquetada; no se comprime hasta hacer ilegible el contenido.

### Dashboards financieros de Partners

- El azul ECUVEL `#085DF8` identifica la tarjeta principal, las series del gráfico, tabs activos y acciones de consulta.
- Los importes usan Onest, peso `700` y dos decimales; el color no sustituye el signo ni la etiqueta del concepto.
- Las cuatro tarjetas KPI se muestran en una fila en escritorio, en cuadrícula `2×2` en tablet y apiladas en móvil.
- La tarjeta de neto puede usar fondo azul suave `#EDF4FF`; las demás permanecen blancas.
- Pagado usa verde semántico, programado usa amarillo, en revisión usa violeta suave y cancelado usa rojo.
- Los gráficos deben ser SVG accesibles alimentados por datos del backend; no derivan importes ni reglas financieras en JavaScript.
- El detalle de una liquidación abre un drawer a la derecha y ocupa todo el ancho en móvil. Debe cerrar con botón, `Escape` y clic en el backdrop, restaurando el foco.
- Nunca se muestra una cuenta bancaria completa: solo banco y últimos cuatro caracteres.

### Badges, chips y selección

- Usar radio pill.
- Mantener texto breve y peso `600–700`.
- El estado seleccionado usa azul suave y texto azul.
- Los chips removibles incluyen una `×` o un botón con etiqueta accesible.
- Los colores de estado deben seguir la tabla semántica de esta guía.

### Galerías

- Miniaturas oficiales: `72px` como base; las galerías del editor pueden ampliarlas según el contexto.
- Portada claramente identificada.
- Slots vacíos con borde discontinuo y fondo blanco o muy claro.
- Controles sobre la imagen en botones circulares blancos.
- Máximo de seis imágenes cuando la regla de producto así lo indique.
- Una galería por color debe usar acordeones simples, resumen visible y estados “Completa” o “Sin imágenes”.

### Modales y menús flotantes

- Fondo blanco, borde sutil y sombra modal.
- Capa posterior oscura semitransparente; se permite desenfoque ligero.
- Cerrar con botón visible, clic fuera cuando sea seguro y tecla `Escape`.
- Restaurar el foco al control que abrió el elemento.
- El menú contextual usa opciones ligeras; las destructivas van después de un divisor y en rojo.

## Estructura y responsive

- Ancho máximo general: `1440px`.
- Padding horizontal: `32px` en escritorio, `24px` en tablet y `16px` en móvil.
- Partners puede usar un contenedor más estrecho cuando el flujo necesita concentración.
- Breakpoints de referencia: móvil hasta `599px`, tablet hasta `1023px`.
- Evitar scroll horizontal de página. Las tablas pueden tener scroll propio en anchos intermedios.
- En móvil, apilar métricas y acciones; convertir filtros laterales en panel desplegable.
- Mantener áreas táctiles de al menos `44px`.

## Iconografía e imágenes

- Usar iconos lineales con trazos consistentes y tamaño normal de `18–24px`.
- El icono acompaña al texto; no reemplaza una etiqueta importante sin `aria-label`.
- Los iconos activos usan azul ECUVEL.
- Las fotografías de producto usan `object-fit: contain` salvo miniaturas editoriales que requieran recorte explícito.
- Proporcionar placeholder cuando no exista una imagen válida.

## Accesibilidad y movimiento

- Contraste mínimo WCAG AA para texto y controles.
- Foco global visible: halo azul de `3px`.
- Asociar cada label con su input.
- Usar `aria-expanded`, `aria-controls`, roles y estados en desplegables, acordeones y menús.
- Mantener navegación completa por teclado.
- Nunca depender solo del color para comunicar errores, éxito o selección.
- Respetar `prefers-reduced-motion`; las transiciones habituales duran aproximadamente `160ms`.
- Escapar contenido generado por usuarios y no renderizar HTML sin sanitización.

## Uso de referencias externas

Los diseños de Stitch, Ozon u otras plataformas pueden orientar estructura, jerarquía o interacción, pero deben adaptarse antes de implementarse:

1. Sustituir su color principal por azul ECUVEL.
2. Sustituir su tipografía por Onest.
3. Aplicar los radios, bordes y sombras de esta guía.
4. Adaptar selects, botones, tablas y badges a los componentes existentes.
5. Verificar escritorio, tablet, móvil, teclado y contraste.

## Ejemplo de tokens

```css
.ecuvel-card {
  padding: var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-default);
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
}

.ecuvel-card__action {
  min-height: 48px;
  padding: var(--space-3) var(--space-5);
  border: 0;
  border-radius: var(--radius-default);
  color: var(--color-white);
  background: var(--color-primary);
  font-family: var(--font-family);
  font-weight: var(--font-weight-semibold);
}
```

## Lista de comprobación para nuevos diseños

- [ ] Usa Onest y la escala tipográfica existente.
- [ ] Usa `#085DF8` como acción principal.
- [ ] Reutiliza tokens en lugar de duplicar colores sin motivo.
- [ ] Los selects tienen opciones ligeras, estados accesibles y aspecto corporativo.
- [ ] Los estados usan color, texto e icono cuando corresponde.
- [ ] Los botones tienen jerarquía clara y estados hover, activo, foco y deshabilitado.
- [ ] Las tarjetas, tablas y formularios siguen los radios y bordes definidos.
- [ ] La pantalla funciona en escritorio y móvil.
- [ ] El teclado y el foco visible permiten completar toda la interacción.
- [ ] Se respeta `prefers-reduced-motion`.
- [ ] El resultado se compara con los componentes existentes de ECUVEL antes de aprobarse.
