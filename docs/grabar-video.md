# Cómo grabar el video de entrada

El video es la única entrada de Rewind. Mientras más limpio y pausado, mejor trabajan los agentes. Hay dos formas de grabarlo y las dos siguen el mismo guion.

| | Automática (script) | Manual (OBS o Game Bar) |
|---|---|---|
| Esfuerzo | Un comando | Grabar a mano |
| Resultado | Siempre igual, se puede repetir | Cada toma sale distinta |
| Cursor | Cursor de Windows 98 dibujado por el script | Cursor real |
| Listas desplegables | No se ve la lista abierta, solo el valor que cambia | Se ve la lista abierta |

El video del repo (`samples/facturacion-demo.mp4`) se grabó con la opción automática.

## Guion

1. **Login**: usuario `admin`, clave `admin`, botón ACEPTAR.
2. **Clientes**: pulsar GRABAR con el formulario vacío, para que aparezca `ERROR 0x0042: CAMPO OBLIGATORIO`, y luego ACEPTAR. Después registrar `FERRETERIA EL CASTAÑAL S.A.C.`, RUC `20612345678`, dirección `JR. LORETO 455 - PUERTO MALDONADO`. Botón GRABAR y luego ACEPTAR en el mensaje.
3. **Facturas**: menú FACTURAS. Cliente `INVERSIONES TAMBOPATA S.A.C.`, estado `PENDIENTE`, botón BUSCAR. Hacer clic en la factura `F001-000110` y luego en REGISTRAR PAGO.
4. **Pago**: monto `300.00`, método TRANSFERENCIA, número de operación `OP-784512`, botón GRABAR.
5. La app vuelve a la búsqueda con la factura en estado **PARCIAL** y saldo S/ 340.00. ACEPTAR y fin.

Mueve el mouse despacio y espera alrededor de 1 segundo en cada pantalla y en cada mensaje.

## Opción A: automática

```
uv run playwright install chromium   # solo la primera vez
uv run python scripts/record_demo.py
```

El script `scripts/record_demo.py`:

1. Arranca `legacy-demo` con `--reset` (datos limpios) en un puerto libre.
2. Abre Chromium sin ventana a 1280×720 y sigue el guion: mueve el mouse despacio, escribe tecla por tecla y hace una pausa de 1 segundo en cada pantalla.
3. Graba con Playwright y comprime con ffmpeg a `samples/facturacion-demo.mp4` (alrededor de 50 s y menos de 1 MB).
4. Cierra la app al terminar.

Opciones:
- `--headed`: muestra el navegador mientras graba.
- `--output otra/ruta.mp4`: guarda el video en otro lugar.

Requiere ffmpeg instalado y en el PATH.

## Opción B: manual

### Preparar

1. Reiniciar los datos de la app:
   ```
   uv run python legacy-demo/app.py --reset
   ```
2. Abrir `http://127.0.0.1:5000` en Chrome, con zoom al 100 % (`Ctrl+0`).
3. Pulsar **F11** (pantalla completa) para que no se vea el navegador moderno.
4. Activar "No molestar" en Windows para que no aparezcan notificaciones.

### Grabar

- **OBS Studio (recomendado):** lienzo y salida en 1280×720, 30 fps, formato MP4. Fuente: "Captura de pantalla".
- **Xbox Game Bar** (ya viene en Windows): `Win+Alt+R` empieza y detiene la grabación. El video queda en `Videos\Capturas`.

### Comprimir

Con ffmpeg, para dejar el video liviano (menos de 10 MB) y sin audio:

```
ffmpeg -i crudo.mp4 -vf "scale=1280:720,fps=30" -c:v libx264 -crf 28 -an samples/facturacion-demo.mp4
```

Los videos crudos (`.mkv`, `.mov`, `.webm`) no se suben al repo.
