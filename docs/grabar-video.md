# Cómo grabar el video de entrada

El video es la única entrada de Rewind. Mientras más limpio y pausado, mejor trabajan los agentes.

## 1. Preparar

1. Reiniciar los datos de la app:
   ```
   uv run python legacy-demo/app.py --reset
   ```
2. Abrir `http://127.0.0.1:5000` en Chrome, con zoom al 100 % (`Ctrl+0`).
3. Pulsar **F11** (pantalla completa) para que no se vea el navegador moderno.
4. Activar "No molestar" en Windows para que no aparezcan notificaciones.

## 2. Grabar

**Opción A, OBS Studio (recomendada).** Lienzo y salida en 1280×720, 30 fps, formato MP4. Fuente: "Captura de pantalla".

**Opción B, Xbox Game Bar** (ya viene en Windows). `Win+Alt+R` empieza y detiene la grabación. El video queda en `Videos\Capturas`.

## 3. Guion (60 a 90 segundos)

Mueve el mouse despacio y espera alrededor de 1 segundo en cada pantalla y en cada mensaje.

1. **Login**: usuario `admin`, clave `admin`, botón ACEPTAR.
2. **Clientes**: registrar `FERRETERIA EL CASTAÑAL S.A.C.`, RUC `20612345678`, dirección `JR. LORETO 455 - PUERTO MALDONADO`. Botón GRABAR y luego ACEPTAR en el mensaje.
   - Opcional: antes, intentar grabar con el RUC vacío para que se vea `ERROR 0x0042: CAMPO OBLIGATORIO`.
3. **Facturas**: cliente `INVERSIONES TAMBOPATA S.A.C.`, estado `PENDIENTE`, botón BUSCAR. Hacer clic en la factura `F001-000110` y luego en REGISTRAR PAGO.
4. **Pago**: monto `300.00`, método TRANSFERENCIA, número de operación `OP-784512`, botón GRABAR.
5. La app vuelve a la búsqueda con la factura en estado **PARCIAL** y saldo S/ 340.00. ACEPTAR y fin.

## 4. Comprimir

Con ffmpeg, para dejar el video liviano (menos de 10 MB) y sin audio:

```
ffmpeg -i crudo.mp4 -vf "scale=1280:720,fps=30" -c:v libx264 -crf 28 -an samples/facturacion-demo.mp4
```

El archivo final va en `samples/facturacion-demo.mp4`. Los videos crudos (`.mkv`, `.mov`) no se suben al repo.
