# legacy-clinica

Second "old program" for Rewind: SISTEMA DE CITAS v1.4 of a fictional clinic, POLICLINICO SANTA ROSA (Flask + SQLite + 98.css). Rewind never reads this code; it only sees the video.

It is built to differ from `legacy-demo/`:
- a full-screen window with an F-key toolbar (F2 PACIENTES, F3 AGENDA, F10 SALIR) instead of a menu bar;
- error codes like `E-101 DATO REQUERIDO: APELLIDOS` instead of `ERROR 0x0042: ...`;
- the program opens on the agenda, not on the first menu entry;
- patients listed oldest first, and a column that joins two fields (APELLIDOS Y NOMBRES);
- a rule the billing demo does not have: a slot that is already taken cannot be booked (`E-305`).

```
uv run python legacy-clinica/app.py --reset      # http://127.0.0.1:5002, operator recepcion / clinica
uv run python scripts/record_demo.py --app clinica --fast
```

Slots are generated for the day the app starts. 98.css, its fonts and the message box icons are served from `legacy-demo/static/` (see that folder's README for their licenses).
