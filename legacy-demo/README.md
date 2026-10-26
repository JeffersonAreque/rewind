# legacy-demo: Sistema de Facturación v2.3

Programa "antiguo" de ejemplo para grabarlo en video y modernizarlo con Rewind. Imita una aplicación de escritorio de Windows 98: todo en mayúsculas, errores con código hexadecimal y recarga completa de la página en cada acción.

## Ejecutar

Desde la raíz del repo:

```
uv sync
uv run python legacy-demo/app.py          # http://127.0.0.1:5000
uv run python legacy-demo/app.py --reset  # borra los datos y vuelve a cargar los de ejemplo
```

Usuario de demostración: `admin` / `admin`. No es una credencial real: solo existe en la base local.

## Pantallas

| Pantalla | Ruta | Qué hace |
|---|---|---|
| Inicio de sesión | `/login` | Valida usuario y clave |
| Registro de clientes | `/clientes` | Registra clientes con RUC (11 dígitos) o DNI (8) y los lista |
| Búsqueda de facturas | `/facturas` | Filtra por número, cliente, estado y fechas |
| Registrar pago | `/pagos` | Registra un pago y actualiza el saldo y el estado de la factura |

## Códigos de error

| Código | Mensaje |
|---|---|
| 0x0013 | USUARIO O CLAVE INCORRECTOS |
| 0x0042 | CAMPO OBLIGATORIO |
| 0x0057 | FECHA INVALIDA |
| 0x0071 | RUC/DNI INVALIDO |
| 0x0075 | CORREO INVALIDO |
| 0x0088 | CLIENTE YA REGISTRADO |
| 0x00A1 | MONTO INVALIDO |
| 0x00A4 | MONTO EXCEDE SALDO PENDIENTE |
| 0x00A9 | FACTURA YA CANCELADA |
| 0x00B2 | NO SE ENCONTRARON REGISTROS |
| 0x00B7 | FACTURA NO EXISTE |

## Datos

SQLite en `legacy-demo/data/facturacion.db`, creada automáticamente (no se sube al repo). Los clientes y las facturas son ficticios. Los montos se guardan en céntimos; el saldo y el estado de cada factura se calculan a partir de sus pagos.

## Créditos

- [98.css](https://github.com/jdan/98.css) de Jordan Scales, licencia MIT (`static/vendor/98css/LICENSE`).
- Fuente "MS Sans Serif" de "lou" en FontStruct, licencia CC BY-SA 3.0 (`static/vendor/98css/FONT-LICENSE.txt`).
