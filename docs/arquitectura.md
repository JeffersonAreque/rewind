# Arquitectura de Rewind

Rewind recibe un video de alguien usando un programa antiguo y devuelve una app web moderna que hace lo mismo. No necesita el código fuente del programa original.

```
video.mp4
   │
   ▼
1. Agente de pantallas ──► pantallas, campos, botones, tablas, diálogos y navegación
   │
   ▼
2. Agente de flujos ─────► pasos del usuario, flujos de negocio y reglas de negocio
   │
   ▼
3. Agente constructor ───► app web moderna
   │
   ▼
4. Agente verificador ───► informe: cada paso del flujo, app nueva vs. video original
```

| Agente | Entrada | Salida | Modelo | Estado |
|---|---|---|---|---|
| Pantallas (`rewind/agents/screens.py`) | Video MP4 | `screens.json` + cuadros clave en PNG | Muse Spark Open (SAM 3.1 opcional) | Listo |
| Flujos (`rewind/agents/flows.py`) | Corrida del agente de pantallas | `flows.json`: pasos, flujos y reglas | Muse Spark Open | Listo |
| Constructor (`rewind/agents/builder.py`) | Pantallas y flujos | App web | Muse Spark Open | Planificado |
| Verificador (`rewind/agents/verifier.py`) | App nueva, flujos y video | Comparación visual paso por paso | Muse Spark Open | Planificado |

## Modelos

**Muse Spark Open** (modelo de pesos abiertos de Meta, usado a través de Baseten) es el núcleo de Rewind y hace todo el trabajo: lee cada pantalla, entiende los flujos, escribe la app y la verifica.

**SAM 3.1** es un complemento opcional. Se activará solo si en el futuro hay acceso a la API de Meta, para afinar la posición exacta de cada elemento. Nada depende de él: hoy no se usa, `screens.json` lo registra como `"segmentation": "off"` y la variable `SAM_API_KEY` de `.env.example` queda vacía.

## Agente 1: pantallas

```
uv run python -m rewind screens samples/facturacion-demo.mp4
```

1. **Cuadros clave** (`rewind/keyframes.py`). ffmpeg decodifica el video a 10 cuadros por segundo, en gris y a 320×180. Un cuadro es clave cuando cumple dos condiciones:
   - La pantalla se queda quieta al menos 0,4 s, es decir, la persona hizo una pausa.
   - Es distinta del último cuadro clave en más píxeles de los que cambia un movimiento del mouse.

   Escribir y mover el mouse cambian pocos píxeles; cambiar de pantalla o abrir un diálogo cambia miles. En el video de ejemplo salen 17 cuadros clave de unos 1.500 cuadros.
2. **Lectura con Muse Spark Open.** Cada cuadro clave va al modelo, que responde en JSON con:
   - el nombre de la pantalla y el título de la ventana;
   - las secciones, con sus campos: etiqueta, tipo, valor y si es editable (fondo blanco) o de solo lectura (fondo gris);
   - los botones, con su estado habilitado o deshabilitado;
   - los menús, las tablas con sus columnas, el diálogo visible y la barra de estado.

   El JSON se pide con `response_format` (`json_schema`) y se valida con pydantic. Si llega mal, se reintenta hasta 2 veces y se le devuelve al modelo el error exacto. El razonamiento del modelo se guarda aparte.
3. **Caché.** Cada respuesta se guarda en `runs/cache/screens/`. La clave sale de la imagen, el modelo y el texto del prompt, así que repetir una corrida cuesta 0 tokens.
4. **Agrupación.** Los cuadros con el mismo nombre de pantalla forman una sola pantalla.
   - Los campos se unen por etiqueta; el tipo y si es editable se deciden por mayoría.
   - Un diálogo no es una pantalla nueva: se guarda como evento de la pantalla que tiene detrás.
   - La navegación es la lista de pantallas en el orden en que aparecen.

Salida, en `runs/<fecha>-<video>/`:
- `frames/*.png`: los cuadros clave.
- `frames.json`: la respuesta de cada cuadro, con su razonamiento y sus tokens.
- `screens.json`: las pantallas únicas, la navegación y los tokens gastados.

Opciones útiles: `--dry-run` solo extrae los cuadros clave, sin llamar al modelo; `--workers` define cuántas llamadas van en paralelo (4 por defecto).

### Prueba de precisión

`samples/facturacion-demo.expected.json` dice qué debe encontrar el agente en el video de ejemplo. Se escribió a mano a partir de las plantillas de `legacy-demo/`, que Rewind nunca ve.

```
uv run python scripts/eval_screens.py            # evalúa la última corrida
```

- **Acierto:** de lo esperado, qué parte encontró el agente. Cada pantalla, campo, editable, tipo, botón, menú, columna, diálogo y la navegación vale un punto.
- **Precisión:** de lo que reportó, qué parte existe de verdad. Inventar campos también cuenta como error.

Resultado actual con Muse Spark Open: **100 % de acierto (110/110) y 100 % de precisión (32/32)**, con 17 llamadas y unos 85 mil tokens.

## Agente 2: flujos

```
uv run python -m rewind flows runs/<id>
```

Lee la corrida del agente de pantallas (`screens.json`, `frames.json` y los cuadros clave) y escribe `runs/<id>/flows.json`.

1. **Qué pasó entre dos cuadros (Muse Spark Open).** Por cada par de cuadros clave seguidos (16 en el video de ejemplo), el modelo recibe:
   - las dos imágenes;
   - lo que leyó el agente 1 en cada una, para usar las mismas etiquetas.

   Responde con dos cosas:
   - las **acciones** del usuario: escribir, hacer clic o elegir, con el campo o botón y el valor;
   - las **observaciones**: datos que cambiaron como resultado (estado, saldo, número de registros, filas nuevas).

   Se valida, se reintenta y se guarda en caché igual que en el agente 1.
2. **Hechos que ya se saben.** "Abrir pantalla" y "ver mensaje" (con su código de error, como `0x0042`) salen de los datos del agente 1, sin preguntar al modelo, así que no se pueden inventar.

   Si un campo aparece en dos pares con un valor parecido (estaba a medio escribir o se leyó mal una vez), se junta en un solo paso que guarda las dos lecturas.
3. **Revisión de valores.** Cada valor escrito o elegido se compara con cómo se ve ese mismo campo después:
   - en los cuadros siguientes de la misma pantalla, hasta que el campo se borra;
   - en otras pantallas, como el CLIENTE de la búsqueda que vuelve a salir en REGISTRAR PAGO;
   - en la barra de estado (`USUARIO: ADMIN` revela un valor que nunca se vio escribir);
   - en las observaciones, como la fila nueva del cliente.

   Así se decide la confianza de cada valor:
   - **alta:** todas las lecturas coinciden;
   - **media:** hay una sola lectura, o una mayoría clara;
   - **baja:** no hay mayoría clara;
   - **oculta:** es una contraseña.

   Las lecturas que no coinciden quedan como `variants`; nunca se inventa un valor. En el video de ejemplo esto atrapa las dos lecturas equivocadas del agente 1: `R.A.C.I` en vez de `S.A.C.` (confianza baja) y `TAMBOPATAS` en vez de `TAMBOPATA` (gana la mayoría, 5 a 1).
4. **Flujos y reglas (Muse Spark Open, una llamada de texto).**
   - Con los pasos numerados, las observaciones y un resumen de las pantallas, el modelo agrupa los pasos en flujos de negocio con nombre.
   - También deduce las reglas: campos obligatorios y errores con su código, cambios de estado, cálculos, botones que solo se habilitan con una condición, y adónde lleva cada acción. Cada regla cita los pasos que la prueban.
   - El código rechaza respuestas que citan pasos que no existen o ponen un paso en dos flujos, y se las devuelve al modelo.

`flows.json` tiene:
- `steps`, cada uno con su pantalla, acción, campo o botón, valor, confianza, variantes y lecturas;
- `workflows`, `rules` y `observations`;
- `low_confidence` (los pasos a revisar a mano) y los tokens gastados.

En el video de ejemplo salen 29 pasos, 4 flujos (Log in, Register customer, Search invoices, Register payment) y 10 reglas, entre ellas:
- RUC / DNI obligatorio (`0x0042`);
- 6 → 7 clientes registrados;
- REGISTRAR PAGO solo se habilita al elegir una factura;
- PENDIENTE → PARCIAL;
- 640.00 − 300.00 = 340.00.

### Prueba de precisión

`samples/facturacion-demo.expected-flows.json` lista a mano lo que el video muestra: 29 pasos, 4 flujos y 7 reglas.

```
uv run python scripts/eval_flows.py              # evalúa la última corrida
```

- **Pasos:** se alinean en orden y se mide qué parte encontró el agente y su precisión.
- **Valores:** cuántos están bien. Un valor equivocado que no se marcó con confianza baja se señala aparte, porque es el peor error.
- **Flujos:** cuentan cuando cubren los mismos pasos con al menos 60 % de coincidencia; el nombre no importa.
- **Reglas:** se buscan por patrones.

Resultado actual: **pasos 29/29, valores 11/11, flujos 4/4, reglas 7/7**. Costó unos 104 mil tokens (17 llamadas); repetir la corrida cuesta 0 tokens.

## Proveedor de modelos

Muse Spark se usa a través de Baseten o Fireworks. Los dos tienen una API compatible con la de OpenAI, así que un proveedor se define con tres datos: URL base, clave y nombre del modelo.

`rewind/config.py` lee esos datos del archivo `.env`. Para cambiar de proveedor solo se cambia una línea:

```
REWIND_PROVIDER=baseten   # o fireworks
```

- `uv run python -m rewind config` muestra qué proveedor está activo y qué falta configurar. No hace ninguna llamada y nunca imprime la clave.
- `uv run python scripts/check_provider.py` hace una llamada de texto y otra de visión para probar la conexión.

## App de ejemplo

`legacy-demo/` es el "programa antiguo" que se graba para la demo: un sistema de facturación con estética de Windows 98 (Flask + SQLite + 98.css). Rewind no lee su código; solo ve el video.
