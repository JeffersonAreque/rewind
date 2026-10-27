# Rewind

**Español** · [English](README.en.md)

> Built from scratch during the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).
> Hecho desde cero durante el Meta Global AI Developer Hackathon (26 oct - 4 nov 2026).

Moderniza software antiguo a partir de un video, sin el código fuente. Grabas a alguien usando el programa viejo y un equipo de agentes reconstruye sus pantallas y flujos en una app web moderna, y luego verifica paso a paso que hace lo mismo.

![Sistema de facturación de ejemplo con estética de Windows 98](docs/img/legacy-demo.png)

## Cómo funciona

1. **Agente de pantallas**: extrae del video los cuadros clave, pantallas, campos, botones, tablas y diálogos.
2. **Agente de flujos**: reconstruye los flujos de trabajo paso a paso.
3. **Agente constructor**: genera una app web con las mismas pantallas y flujos.
4. **Agente verificador**: repite cada flujo en la app nueva y la compara con el video original.

**Muse Spark Open** (modelo de pesos abiertos de Meta, vía Baseten) es el núcleo y hace todo el trabajo. **SAM 3.1** es un complemento opcional que se activará solo si en el futuro hay acceso a la API de Meta; nada depende de él.

Más detalle en [docs/arquitectura.md](docs/arquitectura.md).

## Estado

| Parte | Estado |
|---|---|
| App antigua de ejemplo (`legacy-demo/`) | Lista |
| Configuración de proveedor (Baseten / Fireworks) | Lista |
| Agente 1: pantallas | Listo: 100 % de acierto en el video de ejemplo |
| Agentes 2 a 4 | En desarrollo |

## Tecnologías

Python 3.12 · uv · Muse Spark Open (vía Baseten o Fireworks, API compatible con OpenAI) · ffmpeg · numpy · pydantic · Flask · SQLite · 98.css

## Instalación

Requisitos: [uv](https://docs.astral.sh/uv/). uv descarga Python 3.12 por su cuenta si no lo tienes.

```
git clone https://github.com/JeffersonAreque/rewind.git
cd rewind
uv sync
cp .env.example .env    # luego pon tu clave y el modelo
```

## Uso

App antigua de ejemplo (usuario `admin`, clave `admin`):

```
uv run python legacy-demo/app.py --reset
```

Abre http://127.0.0.1:5000. Para grabarla y usarla como entrada, sigue [docs/grabar-video.md](docs/grabar-video.md).

Ver qué proveedor de modelos está activo:

```
uv run python -m rewind config
```

Para cambiar de proveedor, edita `REWIND_PROVIDER` en `.env` (`fireworks` o `baseten`).

Probar el proveedor con una llamada de texto y otra de visión (un cuadro del video de ejemplo). Muestra la respuesta y los tokens, nunca la clave; `--reasoning` muestra también el razonamiento del modelo:

```
uv run python scripts/check_provider.py
```

Agente 1, pantallas (requiere ffmpeg). Analiza el video y guarda `runs/<id>/screens.json` más los cuadros clave; con `--dry-run` solo extrae los cuadros, sin gastar créditos:

```
uv run python -m rewind screens samples/facturacion-demo.mp4
uv run python scripts/eval_screens.py     # % de acierto contra samples/facturacion-demo.expected.json
```

Pruebas sin llamadas al modelo:

```
uv run pytest
```

## Estructura

```
legacy-demo/   app antigua de ejemplo (Flask + SQLite + 98.css)
rewind/        pipeline de agentes en Python
docs/          arquitectura y guía de grabación
samples/       video de entrada y respuestas esperadas
scripts/       grabación del video, prueba del proveedor y prueba de precisión
tests/         pruebas sin API
```

## Autor

Jefferson Areque, participación individual (pista Indie).

## Licencia

[MIT](LICENSE). 98.css (MIT) y la fuente MS Sans Serif (CC BY-SA 3.0) mantienen sus licencias; ver [legacy-demo/README.md](legacy-demo/README.md#créditos).
