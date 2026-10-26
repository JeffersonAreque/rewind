# Arquitectura de Rewind

Rewind recibe un video de alguien usando un programa antiguo y devuelve una app web moderna que hace lo mismo. No necesita el código fuente del programa original.

```
video.mp4
   │
   ▼
1. Agente de pantallas ──► pantallas, campos y botones (con su posición)
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

| Agente | Entrada | Salida | Modelos | Estado |
|---|---|---|---|---|
| Pantallas (`rewind/agents/screens.py`) | Video MP4 | Cuadros clave; por pantalla: campos, botones y textos con su caja | SAM 3.1 + Muse Spark | Planificado |
| Flujos (`rewind/agents/flows.py`) | Cuadros y pantallas | Lista ordenada de acciones por flujo | Muse Spark | Planificado |
| Constructor (`rewind/agents/builder.py`) | Pantallas y flujos | App web | Muse Spark | Planificado |
| Verificador (`rewind/agents/verifier.py`) | App nueva, flujos y video | Comparación visual paso por paso | Muse Spark | Planificado |

## Proveedor de modelos

Muse Spark se usa a través de Fireworks o Baseten. Los dos tienen una API compatible con la de OpenAI, así que un proveedor se define con tres datos: URL base, clave y nombre del modelo.

`rewind/config.py` lee esos datos del archivo `.env`. Para cambiar de proveedor solo se cambia una línea:

```
REWIND_PROVIDER=baseten   # o fireworks
```

`uv run python -m rewind config` muestra qué proveedor está activo y qué falta configurar. No hace ninguna llamada y nunca imprime la clave.

## App de ejemplo

`legacy-demo/` es el "programa antiguo" que se graba para la demo: un sistema de facturación con estética de Windows 98 (Flask + SQLite + 98.css). Rewind no lee su código; solo ve el video.
