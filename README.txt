## Acceso a la aplicación y uso del Arduino

La aplicación desplegada en Render puede utilizarse directamente desde el
navegador. Basta con abrir la URL pública:

https://smi-core-tfg.onrender.com/login?next=/

La aplicación incluye un simulador de datos industriales, por lo que la
versión online funciona aunque no haya ningún Arduino conectado.

### Uso con datos simulados

Para utilizar la demostración online:

1. Abrir la URL de la aplicación.
2. Iniciar sesión.
3. Acceder al dashboard o a cualquiera de las vistas disponibles.
4. Esperar unos segundos para que aparezcan nuevas mediciones simuladas.

### Uso con el Arduino físico

El Arduino físico no se conecta directamente a Render. Para leer sus datos
es necesario conectar el Arduino por USB a un ordenador y ejecutar el lector
localmente.

Desde la carpeta del proyecto, ejecutar:

```powershell
.\.venv\Scripts\python.exe lector_arduino.py `
  --target api `
  --api-url https://TU-APP.onrender.com