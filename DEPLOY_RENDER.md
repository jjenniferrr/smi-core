# Despliegue en Render

## Que funciona en la nube

- Dashboard
- Nodos simulados
- Historial
- Mapa
- Reportes
- Configuracion por nodo
- Registro de configuracion

## Recomendacion para este TFG

La mejor opcion para publicar una demo rapida es **Render** usando el
simulador en la nube:

- ya tienes `render.yaml` preparado
- no necesitas montar servidor propio
- la URL publica sale en pocos minutos
- para la defensa puedes enseñar una demo online estable aunque el Arduino no este conectado

La demo del **Arduino fisico** te recomiendo hacerla en local, en tu portatil.
Si mas adelante quieres, se puede montar un puente para que tu portatil envie
las lecturas fisicas del Arduino al despliegue online mediante la API publica.

## Que no funciona directamente en la nube

El Arduino fisico no estara conectado al servidor de Render.

Por eso, en Render el despliegue queda preparado con:

- `ENABLE_ARDUINO=0`
- simulador activo

## Pasos

1. Sube este proyecto a GitHub.
2. En Render, crea un `Web Service`.
3. Conecta el repositorio.
4. Render detectara `render.yaml`.
5. Lanza el despliegue.
6. Cuando termine, abre la URL publica y espera unos segundos a que empiecen a entrar datos simulados.

## Comandos usados por Render

- Build: `pip install -r requirements.txt`
- Start: `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`

## URL publica

Cuando termine, Render te dara una URL tipo:

`https://smi-core-tfg.onrender.com`

## Persistencia de SQLite

En plan gratuito, el sistema de ficheros puede ser efimero.
Si quieres conservar `smi_core.db` entre reinicios, necesitas:

- un disco persistente en Render, o
- cambiar a Postgres

Para una **demo del TFG**, esto no bloquea la presentacion:

- el simulador arrancara otra vez
- la aplicacion seguira funcionando
- pero el historico antiguo puede perderse tras reinicios o redeploys

Si quieres una demo publica mas solida durante varios dias, lo recomendable es:

1. usar Render Web Service para la app
2. cambiar SQLite por Postgres
3. mantener `ENABLE_ARDUINO=0` en la nube

## Demo recomendada

- Demo online: usar simulador
- Demo con Arduino fisico: usar local en tu portatil
- Demo mixta: web online + Arduino local enviando lecturas por API

## Uso del Arduino real con la web desplegada

Si en la defensa tienes un ordenador disponible, esta es la opcion mas recomendable:

1. Conecta el Arduino por USB a ese ordenador.
2. Abre una terminal en la carpeta del proyecto.
3. Ejecuta el lector para enviar datos a la web publica:

```powershell
.\\.venv\\Scripts\\python.exe lector_arduino.py --target api --api-url https://TU-APP.onrender.com
```

Si quieres guardar a la vez en local y enviar al despliegue:

```powershell
.\\.venv\\Scripts\\python.exe lector_arduino.py --target both --api-url https://TU-APP.onrender.com
```

Si el puerto no se detecta automaticamente, puedes fijarlo manualmente:

```powershell
.\\.venv\\Scripts\\python.exe lector_arduino.py --port COM3 --target api --api-url https://TU-APP.onrender.com
```

Con esta arquitectura:

- la app web vive online
- el Arduino real se conecta al ordenador del aula
- ese ordenador actua como pasarela Serial -> API
