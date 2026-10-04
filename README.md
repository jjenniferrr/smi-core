# S.M.I. CORE

**Real-time industrial monitoring platform with IoT sensors, simulated data and a Flask web dashboard.**

S.M.I. CORE es una plataforma web de monitorización industrial en tiempo real desarrollada como Trabajo Fin de Grado del Grado en Ingeniería Informática en Sistemas de la Información de la Universidad de Salamanca.

El proyecto combina adquisición de datos mediante Arduino, generación de datos simulados, validación y procesamiento de mediciones, almacenamiento histórico en SQLite, alertas por umbral y visualización mediante un dashboard web.

## Flujo de datos

```text
Sensores físicos / simulador
            ↓
       Adquisición
            ↓
   Procesamiento y validación
            ↓
        SQLite
            ↓
 Históricos · alertas · informes
            ↓
 Dashboard web y API REST
```

## Funcionalidades principales

- Monitorización de nodos industriales en tiempo real.
- Simulación de datos de diez nodos industriales.
- Integración con sensores conectados a Arduino.
- Históricos de telemetría y filtros por nodo y fecha.
- Alertas por temperatura, humedad y sonido.
- Mapa y detalle de nodos.
- Configuración de umbrales y parámetros del sistema.
- Autenticación basada en sesiones.
- Roles `normal`, `medio` y `administrador`.
- Gestión de usuarios y permisos administrativos.
- Exportación de informes en CSV, PDF y XLSX.
- API REST para consulta e ingesta de telemetría.

## Tecnologías

- Python y Flask
- SQLite
- JavaScript, HTML y CSS
- Bootstrap y Chart.js
- Arduino
- Gunicorn
- Render

## Arquitectura

El backend Flask centraliza la autenticación, autorización, persistencia, simulación, integración con Arduino, API REST y generación de informes. La interfaz está organizada en páginas HTML independientes que consumen los endpoints JSON del backend.

La aplicación utiliza SQLite para el desarrollo y la demo. La base de datos se crea localmente al iniciar la aplicación y los archivos de base de datos están excluidos del repositorio mediante `.gitignore`.

## Acceso a la demo

La aplicación desplegada en Render puede utilizarse directamente desde el navegador:

**[Abrir S.M.I. CORE](https://smi-core-tfg.onrender.com/login?next=/)**

### Cuenta demo

```text
Usuario: usuario
Contraseña: usuario123
Rol: normal
```

Esta cuenta existe exclusivamente para demostración y tiene permisos limitados. No permite gestionar usuarios, modificar roles, administrar permisos ni cambiar la configuración crítica del sistema.

La demo online utiliza datos simulados, por lo que funciona aunque no haya ningún Arduino conectado.

## Uso con datos simulados

1. Abrir la URL de la aplicación.
2. Iniciar sesión con la cuenta demo.
3. Acceder al dashboard o a cualquiera de las vistas disponibles.
4. Esperar unos segundos para que aparezcan nuevas mediciones simuladas.

En una instalación local, el simulador puede ejecutarse con:

```powershell
python simulador_planta.py
```

## Uso con Arduino físico

El Arduino físico no se conecta directamente a Render. Hay que conectarlo por USB a un ordenador y ejecutar el lector localmente.

Primero se deben definir las variables locales necesarias. Consulta `.env.example` como referencia y no subas nunca un archivo `.env` real al repositorio.

Después, desde la carpeta del proyecto:

```powershell
python lector_arduino.py `
  --target api `
  --api-url https://smi-core-tfg.onrender.com
```

El lector envía el token de ingesta mediante `INGESTION_TOKEN`. Ese token es privado y debe coincidir con el configurado en Render.

También se puede guardar la lectura en una base de datos local usando `--target local`, o utilizar `--target both` para guardar localmente y enviar a la API.

## Instalación local

Se recomienda utilizar un entorno virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Define al menos una clave de sesión antes de iniciar Flask:

```powershell
$env:SECRET_KEY = "genera-una-clave-aleatoria-larga"
$env:ENABLE_ARDUINO = "0"
```

Si necesitas una cuenta administradora local, define también `AUTH_USERNAME` y `AUTH_PASSWORD`. Esas credenciales no deben escribirse en el código ni publicarse.

Para iniciar la aplicación:

```powershell
python app.py
```

La aplicación estará disponible en `http://localhost:5000`.

## Variables de entorno

| Variable | Uso |
|---|---|
| `SECRET_KEY` | Firma segura de las sesiones Flask. Obligatoria. |
| `AUTH_USERNAME` | Usuario administrador inicial opcional. |
| `AUTH_PASSWORD` | Contraseña del administrador inicial opcional. |
| `INGESTION_TOKEN` | Protege la ingesta remota de telemetría. |
| `DB_PATH` | Ruta de la base de datos SQLite. |
| `ENABLE_ARDUINO` | Activa o desactiva el lector físico. |
| `SIMULATE_ARDUINO` | Controla la simulación del nodo físico. |
| `SESSION_COOKIE_SECURE` | Activa cookies seguras en despliegues HTTPS. |

## Estructura del proyecto

```text
app.py                    Backend Flask y API REST
lector_arduino.py         Lectura del Arduino y envío de telemetría
simulador_planta.py       Generación local de datos simulados
*.html                    Vistas de la aplicación
theme.css                 Estilos de la interfaz
theme.js                  Utilidades JavaScript compartidas
sketch_apr24a/            Código del Arduino
DiagramasTFG.drawio       Diagramas de arquitectura y diseño
EsquemaArduino.fzz        Esquema del circuito
render.yaml               Configuración del despliegue en Render
requirements.txt          Dependencias Python
DEPLOY_RENDER.md          Notas de despliegue
.env.example              Referencia de variables locales
```

## Despliegue

El despliegue de demostración utiliza Render y Gunicorn. Las variables sensibles se configuran desde el panel de Render y no se almacenan en Git.

```text
Build:  pip install -r requirements.txt
Start:  gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app
```

## Contexto académico

Este proyecto es el Trabajo Fin de Grado de Jennifer Fuente Manzanares para el Grado en Ingeniería Informática en Sistemas de la Información de la Universidad de Salamanca.

El objetivo del trabajo es diseñar y desarrollar una plataforma de monitorización industrial que permita estudiar el ciclo completo de los datos: adquisición, procesamiento, persistencia, análisis, alertas y visualización.

## Licencia

Proyecto académico y portfolio personal. Consulta al autor antes de reutilizar sus contenidos, diseños o documentación.
