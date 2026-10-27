# samples

Videos de entrada para Rewind.

- `facturacion-demo.mp4`: recorrido por `legacy-demo/` (unos 50 s, 1280×720). Se grabó con `scripts/record_demo.py`; ver [docs/grabar-video.md](../docs/grabar-video.md).
- `facturacion-demo.expected.json`: lo que el agente de pantallas debe encontrar en ese video, escrito a mano desde las plantillas de `legacy-demo/`. Lo usa `scripts/eval_screens.py`.
- `facturacion-demo.expected-flows.json`: los pasos, flujos y reglas que el agente de flujos debe encontrar, escritos a mano desde `docs/grabar-video.md` y `legacy-demo/`. Lo usa `scripts/eval_flows.py`.
