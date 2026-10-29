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
3. Agente constructor ───► app web moderna + una prueba por flujo
   │
   ▼
4. Agente verificador ───► informe: cada paso del flujo, app nueva vs. video original
```

| Agente | Entrada | Salida | Modelo | Estado |
|---|---|---|---|---|
| Pantallas (`rewind/agents/screens.py`) | Video MP4 | `screens.json` + cuadros clave en PNG | Muse Spark Open (SAM 3.1 opcional) | Listo |
| Flujos (`rewind/agents/flows.py`) | Corrida del agente de pantallas | `flows.json`: pasos, flujos y reglas | Muse Spark Open | Listo |
| Constructor (`rewind/agents/builder.py`) | `screens.json` y `flows.json` | `app/`: app web moderna + pruebas por flujo | Muse Spark Open | Listo |
| Verificador (`rewind/agents/verifier.py`) | App nueva, flujos y video | `VERIFICATION.md`: comparación visual paso por paso | Muse Spark Open | Listo |

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

## Agente 3: constructor

```
uv run python -m rewind build runs/<id>
uv run python runs/<id>/app/app.py        # http://127.0.0.1:5001
```

**El modelo nunca escribe código.** La app se arma con dos piezas:
- un **runtime** fijo que escribimos y probamos una vez (`rewind/builder/runtime/`: Flask + SQLite, interfaz moderna);
- un **spec** en JSON que llena Muse Spark Open y que validan pydantic y los chequeos de `rewind/builder/spec.py`.

Si un chequeo falla, el error vuelve al modelo y la llamada se reintenta. Una respuesta guardada en caché se vuelve a revisar con los chequeos actuales.

1. **Datos iniciales.** Muse Spark copia las filas de cada tabla del primer cuadro donde aparece con datos: los 6 clientes y las 3 facturas de TAMBOPATA. Ese es el estado antes de que el usuario cambie algo. El número de filas debe coincidir con el que vio el agente 1, y no se inventan registros que no salen en el video.
2. **Modelo de datos.** Define las entidades (cliente, factura, pago) con campos tipados. Un código `serial` (como C0006) solo vale si los códigos vistos son 1, 2, 3… seguidos; los números de factura (F001-000110) quedan como texto.
3. **Modernización.** El modelo propone cambios de una lista fija, cada uno con su motivo y los pasos del video que lo justifican:
   - unir la búsqueda y el pago en una sola página;
   - errores junto al campo en vez de un diálogo;
   - un aviso que se cierra solo en vez de un ACEPTAR;
   - etiquetas más claras, valores por defecto, otro orden de campos;
   - quitar menús que ningún paso usa.

   Los chequeos exigen que cada cambio se aplique de verdad y que ningún elemento usado por un paso desaparezca.
4. **Una pantalla por llamada.** Cada pantalla antigua se describe en uno de 4 tipos que el runtime entiende (`login`, `create`, `search`, `transaction`):
   - con la etiqueta moderna y la antigua de cada elemento (`data-legacy`);
   - con las validaciones con su código original (`0x0042`);
   - con efectos escritos como expresiones seguras (`invoice.balance - payment.amount`), que se leen con `ast` contra una lista blanca, nunca con `eval`.
5. **Pruebas.** El código, no el modelo, escribe una prueba Playwright por flujo a partir de `flows.json`:
   - repite los 29 pasos buscando cada elemento por su etiqueta antigua;
   - convierte las observaciones en comprobaciones: 6 → 7 clientes, 3 resultados, REGISTRAR PAGO habilitado solo al elegir una fila, PARCIAL y S/ 340.00.

   Los clics que un cambio vuelve innecesarios (el ACEPTAR de un mensaje que ya no es diálogo) se marcan como "absorbidos" con el cambio que los explica; nunca se ocultan.

`runs/<id>/app/` contiene:
- `app.py` (el runtime), `spec.json`, `seed.json` y `tests/`;
- `CHANGES.md`: tabla antiguo / modernizado / motivo / pasos, escrita a partir de lo que se construyó y no del texto del modelo;
- `build.json`: el resultado.

Resultado en el video de ejemplo:
- **4/4 flujos pasan**, con 29 pasos, 3 de ellos absorbidos por los cambios.
- **36 de 36 elementos antiguos** se conservan.
- **7 cambios de modernización.** Un cambio que deja todo igual que el programa antiguo no cuenta: "FECHA PAGO empieza con la fecha de hoy" se descartó porque la pantalla antigua ya lo hacía.
- **Valores iniciales:** los campos de un formulario nuevo empiezan como en la grabación (FECHA PAGO = hoy, METODO = EFECTIVO). El checker lo exige y la prueba lo comprueba al abrir la pantalla; si se quita EFECTIVO, la prueba del pago falla.
- **Prueba de que las pruebas miden algo:** si se quita la fórmula del saldo, la prueba del pago falla en el paso 28 ("no muestra S/ 340.00"); si se quita el campo obligatorio, falla la del cliente.
- **Costo:** unos 105 mil tokens (10 llamadas); repetir cuesta 0.

## Agente 4: verificador

```
uv run python -m rewind verify runs/<id>
```

Las pruebas del constructor dicen si cada flujo llega al final. El verificador además mira cada paso y lo compara con el video.

1. **Capturas.** Las pruebas de la app se repiten una vez por flujo, así que un fallo no oculta los demás flujos. Con `REWIND_EVIDENCE`, cada paso deja una captura de la app nueva (`evidence/new/step_NN.png`) y si se repitió bien. La imagen antigua de cada paso es su cuadro clave `frame_to`. Los pasos que solo preparan un flujo (iniciar sesión antes de pagar) no esperan mensajes: un mensaje roto falla en su propio flujo, no en los siguientes.
2. **Comparación (Muse Spark Open, visión).** Una llamada por cada paso no absorbido (26 en el video de ejemplo), con las dos imágenes. Responde:
   - `same_data`: los valores escritos o elegidos, las filas, montos, estado y saldo;
   - `same_message`: el mismo mensaje, con su código, o ninguno;
   - `same_outcome`: el mismo efecto (se guardó o no, la misma pantalla, el mismo botón habilitado);

   cada uno con `pass`, `fail` o `unclear`, más la lista de diferencias. El estilo, la disposición, las etiquetas, la fecha de hoy y los 7 cambios de `CHANGES.md` (como la página unida) no cuentan. Si el cuadro clave se tomó después de pasos posteriores (varios pasos comparten un cuadro), el modelo los recibe y lo que cambiaron no cuenta como diferencia. Las respuestas se validan y se guardan en caché por el hash de las imágenes.
3. **Veredictos (código, no el modelo).** Un paso pasa si se repitió bien y la comparación dice `pass`. Una regla pasa si todos sus pasos de evidencia pasan. `unclear` se muestra como advertencia, nunca como aprobado.
4. **Salida.** `runs/<id>/verification.json` y `VERIFICATION.md`: tabla por paso y por regla, la imagen antigua y la nueva lado a lado, y las diferencias encontradas.

Resultado en el video de ejemplo:
- **24 pasos pasan, 0 fallan y 2 quedan dudosos**, más 3 absorbidos por cambios. Los dudosos son USUARIO y CLAVE: el video no tiene un cuadro del inicio de sesión lleno, porque el siguiente cuadro ya muestra la pantalla de clientes.
- **10 de 10 reglas pasan.**
- **Encontró dos diferencias reales** en la primera corrida, que se corrigieron en el runtime: la búsqueda de facturas mostraba todas las facturas antes de BUSCAR (la pantalla antigua empieza vacía) y el error 0x0042 seguía debajo de RUC / DNI después de llenarlo.
- **Costo:** unos 175 mil tokens la primera vez (26 llamadas); repetir cuesta 0.

### Prueba con copias rotas

```
uv run python scripts/eval_verifier.py
```

Verifica la app sana y 3 copias rotas en `runs/<id>/eval/`. La app sana no debe fallar ningún paso ni regla; cada copia debe fallar el paso donde se nota el daño y una regla sobre él. Si no, el script termina con error.

| Copia | Paso que falla | Reglas que fallan |
|---|---|---|
| Sin la fórmula del saldo | 28 en la prueba (no muestra S/ 340.00); Muse Spark ya lo ve en los pasos 26 y 27 (S/ 640.00 en vez de S/ 340.00) | saldo, estado, cálculo y regreso a la búsqueda |
| Sin el campo obligatorio | 7 en la prueba (no sale 0x0042); Muse Spark ya lo ve en el paso 6 (sale el aviso de éxito en vez del error) | RUC / DNI obligatorio y cliente nuevo |
| Sin EFECTIVO | 22 en la prueba (METODO empieza vacío); Muse Spark ya lo ve en el paso 21 | abrir REGISTRAR PAGO y las del pago |

Las copias solo pagan las capturas que cambian: unos 34 mil tokens en total. Repetir cuesta 0.

## Todo de una vez: `rewind run` e informe HTML

```
uv run python -m rewind run video.mp4      # los 4 agentes y runs/<id>/report.html
uv run python -m rewind report runs/<id>   # solo el informe, sin llamar al modelo
```

`rewind/report.py` solo lee lo que ya escribieron los agentes, así que cuesta 0 tokens. Arma un solo archivo HTML (plantilla en `rewind/templates/report.html`) con:
- cada pantalla antigua junto a la misma pantalla en la app nueva;
- los flujos, con el veredicto de cada paso;
- las reglas de negocio y si pasaron la verificación;
- los cambios de modernización, descritos desde lo construido;
- la verificación paso a paso, con las dos imágenes y las diferencias.

Las imágenes van adentro en base64 y cada una se guarda una sola vez aunque aparezca varias veces: unos 4 MB en el video de ejemplo. Si a la corrida le falta una parte (aún no se construyó o no se verificó), el informe la omite.

Si el proveedor rechaza la clave, el comando termina con un mensaje claro. Lo ya hecho queda en caché, así que al repetir sigue donde se quedó.

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
