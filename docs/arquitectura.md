# Arquitectura de Rewind

Rewind recibe un video de alguien usando un programa antiguo y devuelve una app web moderna que hace lo mismo. No necesita el código fuente del programa original.

```
video.mp4
   │
   ▼
1. Agente de pantallas ──► pantallas, campos, botones, tablas, diálogos y navegación
   │
   ▼
2. Agente de flujos ─────► flujos paso a paso (escribir, clic, pantalla siguiente)
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
| Flujos (`rewind/agents/flows.py`) | Cuadros y pantallas | Lista ordenada de acciones por flujo | Muse Spark Open | Planificado |
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
