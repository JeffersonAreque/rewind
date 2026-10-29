# samples

Videos de entrada para Rewind.

- `facturacion-demo.mp4`: recorrido por `legacy-demo/` (unos 50 s, 1280×720). Se grabó con `scripts/record_demo.py`; ver [docs/grabar-video.md](../docs/grabar-video.md).
- `facturacion-demo.expected.json`: lo que el agente de pantallas debe encontrar en ese video, escrito a mano desde las plantillas de `legacy-demo/`. Lo usa `scripts/eval_screens.py`.
- `facturacion-demo.expected-flows.json`: los pasos, flujos y reglas que el agente de flujos debe encontrar, escritos a mano desde `docs/grabar-video.md` y `legacy-demo/`. Lo usa `scripts/eval_flows.py`.
- `clinica-demo.mp4`: una recepcionista trabajando rápido en `legacy-clinica/` (31 s, 1280×720), sin pausas, con un error de tipeo corregido, un campo olvidado y un turno ya tomado. Se grabó con `scripts/record_demo.py --app clinica --fast`.
- `clinica-demo.expected.json` y `clinica-demo.expected-flows.json`: lo que deben encontrar los agentes de pantallas y de flujos en ese video, escrito a mano desde `legacy-clinica/` y el guion de grabación.
- `facturacion-demo.spec.json`: un spec de app y sus datos iniciales escritos a mano, con la misma forma que produce el agente constructor. Las pruebas lo usan para probar el runtime y el generador de pruebas sin llamar al modelo.
