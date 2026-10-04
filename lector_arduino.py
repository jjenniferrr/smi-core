"""Utilidad de consola para leer el Arduino por Serial y guardar sus lecturas.

Sirve como herramienta independiente para pruebas locales del nodo fisico sin
necesidad de arrancar toda la aplicacion web.
"""

# =============================================================================
# ARQUITECTURA Y PAPEL DENTRO DE S.M.I. CORE
# -----------------------------------------------------------------------------
# Esta utilidad actua como pasarela independiente entre el Arduino fisico y el
# resto del sistema. Puede ejecutarse en el equipo conectado al microcontrolador
# sin necesidad de arrancar el backend Flask completo.
#
# Recorridos posibles de una muestra:
#
#   Arduino -> Puerto serie -> SQLite local
#   Arduino -> Puerto serie -> API remota
#   Arduino -> Puerto serie -> SQLite local + API remota
#
# El parametro --target selecciona uno de estos tres comportamientos.
#
# ROBUSTEZ DEL ENVIO REMOTO
# -------------------------
# Esta version diferencia dos tipos de error de red:
#
# - HTTPError: el servidor ha respondido con un codigo HTTP de error. Se intenta
#   leer tambien el cuerpo de la respuesta para mostrar el motivo real.
#
# - URLError: la comunicacion ha fallado antes de obtener una respuesta valida,
#   por ejemplo por DNS, timeout o servidor no disponible.
#
# Ademas, read_serial() captura RuntimeError al llamar a post_measurement().
# Esto evita que una caida puntual del servidor remoto cierre el puerto serie.
# =============================================================================

# =============================================================================
# DESCRIPCION GENERAL DE LA UTILIDAD
# -----------------------------------------------------------------------------
# Este script funciona como herramienta independiente de adquisicion para el
# nodo fisico FISICO_ARDUINO. Su responsabilidad es leer el monitor serie del
# microcontrolador, reconocer cada magnitud mediante expresiones regulares y
# reconstruir una muestra completa antes de enviarla a su destino.
#
# Puede funcionar en tres modos:
# - local: guarda las lecturas en smi_core.db.
# - api: envia las lecturas a /api/telemetria de una aplicacion remota.
# - both: realiza simultaneamente ambas operaciones.
#
# Flujo general:
# 1. Se procesan los argumentos de linea de comandos.
# 2. Se usa el puerto indicado o se intenta detectar automaticamente.
# 3. Se abre la conexion serie con pyserial.
# 4. Se leen lineas UTF-8 procedentes del sketch.
# 5. PATTERNS identifica temperatura, humedad, presion, luz y sonido.
# 6. Las magnitudes se acumulan en el diccionario current.
# 7. Cuando todas estan disponibles, la muestra se considera completa.
# 8. Se guarda localmente, se envia a la API o se realizan ambas acciones.
# 9. current se reinicia y comienza la captura de una nueva muestra.
# =============================================================================

# =============================================================================
# IMPORTACIONES
# -----------------------------------------------------------------------------
# argparse define la interfaz CLI; json serializa las lecturas; re interpreta
# las lineas serie; sqlite3 persiste datos; time permite esperar al Arduino;
# urllib.request y urllib.error realizan el envio HTTP sin dependencias externas.
# =============================================================================

import argparse
import json
import os
import re
import sqlite3
import time
from urllib import error, request


# =============================================================================
# CONSTANTES GLOBALES
# -----------------------------------------------------------------------------
# DB_PATH identifica la base local y NODE_ID fija el identificador utilizado por
# todas las lecturas procedentes del Arduino fisico.
# =============================================================================

# DB_PATH es una ruta relativa. Se usa exclusivamente cuando el destino incluye
# almacenamiento local.
DB_PATH = "smi_core.db"
# NODE_ID identifica todas las lecturas de esta utilidad como procedentes del
# Arduino fisico y permite diferenciarlas de los nodos SIM_*.
NODE_ID = "FISICO_ARDUINO"
# Token compartido exclusivamente entre el lector y la API remota. No debe
# escribirse en el repositorio ni en los argumentos de la linea de comandos.
INGESTION_TOKEN = os.environ.get("INGESTION_TOKEN", "").strip()


# =============================================================================
# PATRONES DE PARSEO DEL PUERTO SERIE
# -----------------------------------------------------------------------------
# Cada expresion regular busca una linea concreta emitida por el sketch y captura
# solamente el valor numerico. El grupo entre parentesis queda accesible mediante
# match.group(1).
# =============================================================================

# Patrones de texto esperados desde el monitor serie del sketch de Arduino.
# El diccionario asocia cada magnitud con una expresion regular precompilada.
# re.compile() evita recompilar el patron en cada linea recibida.
PATTERNS = {
    # -? permite temperaturas negativas y (?:\.\d+)? hace opcional la parte decimal.
    "temperatura": re.compile(r"Temperatura:\s*(-?\d+(?:\.\d+)?)"),
    "humedad": re.compile(r"Humedad:\s*(-?\d+(?:\.\d+)?)"),
    "presion": re.compile(r"Presion:\s*(-?\d+(?:\.\d+)?)"),
    # Para luz y sonido se esperan valores enteros positivos procedentes del ADC.
    "luz": re.compile(r"Nivel de luz:\s*(\d+)"),
    "sonido": re.compile(r"Nivel de sonido:\s*(\d+)"),
}


# ============================================================================
# FUNCION: init_db()
# ----------------------------------------------------------------------------
# Garantiza que la tabla historial_telemetria exista y aplica una migracion compatible con
# bases de datos antiguas que aun no dispongan de la columna luz.
#
# Parametros: ninguno.
#
# Retorno: No devuelve valor; prepara o migra la estructura SQLite.
# ============================================================================

def init_db():
    # Garantiza que exista la tabla de telemetria y la columna de luz.
    # El gestor de contexto garantiza el cierre de la conexion SQLite.
    # El contexto with confirma la transaccion si no hay errores y libera la
    # conexion al terminar.
    with sqlite3.connect(DB_PATH) as connection:
        # CREATE TABLE IF NOT EXISTS permite ejecutar esta inicializacion varias
        # veces sin eliminar telemetria ya almacenada.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS historial_telemetria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nodo_id TEXT NOT NULL,
                temperatura REAL NOT NULL,
                humedad REAL NOT NULL,
                sonido REAL NOT NULL,
                presion REAL NOT NULL,
                luz REAL NOT NULL DEFAULT 0,
                fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Se inspecciona la estructura real para mantener compatibilidad con
        # bases creadas por versiones anteriores del proyecto.
        # Si la BD ya existia de antes, verificamos que tenga todas las columnas actuales.
        # PRAGMA table_info devuelve metadatos de columnas; la comprension de lista
        # extrae de cada fila el nombre situado en la posicion 1.
        # PRAGMA table_info devuelve informacion estructural de la tabla.
        # row[1] corresponde al nombre de cada columna en el resultado de SQLite.
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(historial_telemetria)").fetchall()
        ]
        # Si falta luz, ALTER TABLE la incorpora conservando las filas existentes.
        if "luz" not in columns:
            connection.execute(
                "ALTER TABLE historial_telemetria ADD COLUMN luz REAL NOT NULL DEFAULT 0"
            )


# ============================================================================
# FUNCION: normalize_sound()
# ----------------------------------------------------------------------------
# Convierte el valor analogico bruto del microfono a porcentaje cuando se usa el modo
# percent, o conserva el valor original cuando se solicita raw.
#
# Parametros:
# - raw_value: Valor analogico bruto del sensor de sonido.
# - mode: Modo de conversion: raw o percent.
#
# Retorno: Valor float normalizado en porcentaje o valor bruto convertido a float.
# ============================================================================

def normalize_sound(raw_value, mode):
    # Convierte el valor crudo del microfono a porcentaje salvo que se pida raw.
    # En modo raw no se modifica la escala; solo se asegura que el valor sea float.
    # En modo raw se mantiene la escala analogica original del Arduino.
    if mode == "raw":
        return float(raw_value)
    # 1023 es el valor maximo habitual de una lectura ADC de 10 bits.
    # La regla de tres transforma 0-1023 en una escala porcentual 0-100.
    # 1023 equivale a 2^10 - 1, valor maximo habitual de un ADC de 10 bits.
    # La regla de tres convierte ese rango a una escala porcentual 0-100.
    return round((float(raw_value) / 1023.0) * 100.0, 2)


# ============================================================================
# FUNCION: save_measurement()
# ----------------------------------------------------------------------------
# Normaliza el sonido y persiste una lectura completa del Arduino en la base de datos SQLite
# local utilizada por S.M.I. CORE.
#
# Parametros:
# - values: Diccionario con temperatura, humedad, presion, luz y sonido.
# - sound_mode: Modo de tratamiento del sonido.
#
# Retorno: No devuelve valor; inserta una fila en SQLite y escribe una traza en consola.
# ============================================================================

def save_measurement(values, sound_mode):
    # Persiste una lectura completa del Arduino en la base de datos local.
    # La normalizacion se realiza antes de persistir para que la interfaz web
    # reciba un valor directamente interpretable.
    # Se normaliza el sonido antes de persistir para que el frontend no tenga
    # que conocer la escala analogica original del sensor.
    sonido = normalize_sound(values["sonido"], sound_mode)

    # Guardamos la lectura ya normalizada para que quede lista para la interfaz web.
    # Se abre una conexion independiente para esta escritura y se cierra al salir.
    # Cada escritura local usa una conexion independiente y de corta duracion.
    with sqlite3.connect(DB_PATH) as connection:
        # Los signos ? son placeholders de SQLite; los valores se envian aparte,
        # evitando concatenarlos directamente dentro de la sentencia SQL.
        # Los placeholders ? separan el SQL de los datos y evitan concatenaciones
        # directas de valores dentro de la sentencia.
        connection.execute(
            """
            INSERT INTO historial_telemetria
            (nodo_id, temperatura, humedad, sonido, presion, luz)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                NODE_ID,
                values["temperatura"],
                values["humedad"],
                sonido,
                values["presion"],
                values["luz"],
            ),
        )

    # La traza permite verificar visualmente que la lectura se ha almacenado.
    print(
        f"Guardado {NODE_ID}: "
        f"T={values['temperatura']} C, "
        f"H={values['humedad']} %, "
        f"P={values['presion']} hPa, "
        f"Luz={values['luz']}, "
        f"Sonido={sonido}"
    )


# ============================================================================
# FUNCION: post_measurement()
# ----------------------------------------------------------------------------
# Construye un payload JSON y envia una lectura completa al endpoint /api/telemetria de una
# instancia remota de S.M.I. CORE.
#
# Parametros:
# - api_url: URL base de la aplicacion remota.
# - values: Diccionario con temperatura, humedad, presion, luz y sonido.
# - sound_mode: Modo de tratamiento del sonido.
#
# Retorno: No devuelve valor; envia la lectura por HTTP o lanza RuntimeError ante fallo de red.
# ============================================================================

def post_measurement(api_url, values, sound_mode):
    # Envia una lectura del Arduino a la API publica del despliegue remoto.
    # El payload usa el mismo formato que espera el endpoint /api/telemetria.
    # El payload utiliza exactamente los nombres esperados por /api/telemetria.
    # El payload reproduce exactamente el contrato esperado por POST /api/telemetria.
    payload = {
        "nodo_id": NODE_ID,
        "temperatura": values["temperatura"],
        "humedad": values["humedad"],
        "sonido": normalize_sound(values["sonido"], sound_mode),
        "presion": values["presion"],
        "luz": values["luz"],
    }
    # json.dumps() convierte el diccionario a JSON y encode('utf-8') lo transforma
    # en bytes, formato requerido para el cuerpo de urllib.request.Request.
    # json.dumps() serializa el diccionario y encode('utf-8') produce los bytes
    # que urllib necesita para construir el cuerpo HTTP.
    body = json.dumps(payload).encode("utf-8")
    # rstrip('/') evita producir una doble barra al concatenar el endpoint.
    # rstrip('/') evita generar una URL con doble barra antes de /api/telemetria.
    endpoint = api_url.rstrip("/") + "/api/telemetria"
    # Request encapsula URL, cuerpo, Content-Type y metodo HTTP POST.
    # Request agrupa URL, cuerpo, cabeceras y metodo HTTP en un unico objeto.
    if not INGESTION_TOKEN:
        raise RuntimeError("INGESTION_TOKEN debe definirse para enviar telemetria remota")

    req = request.Request(
        endpoint,
        data=body,
        # Content-Type informa al backend de que el cuerpo recibido es JSON.
        headers={
            "Content-Type": "application/json",
            "X-Ingestion-Token": INGESTION_TOKEN,
        },
        # El endpoint de ingesta utiliza POST porque crea una nueva medicion.
        method="POST",
    )

    # Si la app esta publicada, este bloque empuja las lecturas al servidor remoto.
    # urlopen() realiza la llamada HTTP con un timeout maximo de diez segundos.
    try:
        # timeout=10 evita que una perdida de conectividad bloquee indefinidamente
        # el proceso que esta leyendo el puerto serie.
        with request.urlopen(req, timeout=10) as response:
            # Se aceptan 200 y 201 porque ambos representan respuestas correctas
            # habituales en una operacion de recepcion/creacion.
            # 200 y 201 se aceptan como respuestas correctas. Cualquier otro estado
            # se convierte en RuntimeError para un tratamiento uniforme.
            if response.status not in (200, 201):
                raise RuntimeError(f"Respuesta inesperada de la API: {response.status}")
    # URLError agrupa errores de conexion, DNS, timeout y otros fallos de red.
    # HTTPError es una subclase de URLError; por eso debe capturarse primero para
    # poder extraer el codigo HTTP y el cuerpo de error devuelto por el servidor.
    except error.HTTPError as exc:
        # El servidor puede devolver un JSON con el motivo concreto del rechazo.
        # Leerlo aquí evita mostrar únicamente un mensaje genérico HTTP 500.
        # exc.read() recupera el cuerpo HTTP. decode() lo transforma en texto y
        # errors='ignore' evita que una codificacion inesperada rompa el lector.
        response_body = exc.read().decode("utf-8", errors="ignore").strip()
        # Si el servidor no devuelve cuerpo, se utiliza como respaldo el mensaje
        # de la excepcion.
        detail = response_body or str(exc)
        raise RuntimeError(
            f"No se pudo enviar la lectura a {endpoint}: HTTP {exc.code}: {detail}"
        ) from exc
    # URLError cubre fallos de transporte como DNS, timeout, conexion rechazada
    # o servidor temporalmente inaccesible.
    except error.URLError as exc:
        raise RuntimeError(f"No se pudo enviar la lectura a {endpoint}: {exc}") from exc

    print(
        f"Enviado a API {NODE_ID}: "
        f"T={payload['temperatura']} C, "
        f"H={payload['humedad']} %, "
        f"P={payload['presion']} hPa, "
        f"Luz={payload['luz']}, "
        f"Sonido={payload['sonido']}"
    )


# ============================================================================
# FUNCION: detect_port()
# ----------------------------------------------------------------------------
# Intenta localizar automaticamente el puerto serie del Arduino, priorizando dispositivos
# cuya descripcion contiene terminos habituales como Arduino, USB, CH340, WCH o Serial.
#
# Parametros: ninguno.
#
# Retorno: Nombre del puerto detectado, por ejemplo COM3, o None cuando no se encuentra ninguno.
# ============================================================================

def detect_port():
    # Intenta localizar automaticamente el puerto serie del Arduino conectado.
    # La importacion de pyserial se realiza dentro de la funcion para que el
    # script pueda al menos cargarse aunque la dependencia no este instalada.
    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    # comports() devuelve los puertos disponibles; list() materializa el iterable.
    # comports() consulta al sistema operativo y list() materializa el iterable
    # para poder comprobarlo y recorrerlo varias veces.
    ports = list(list_ports.comports())
    if not ports:
        return None

    # Muchos Arduino Uno compatibles se identifican con chip CH340 o WCH.
    # La tupla contiene palabras frecuentes en Arduino oficiales y clones CH340/WCH.
    preferred_terms = ("arduino", "usb", "ch340", "wch", "serial")
    # Se recorre la lista hasta encontrar una descripcion compatible.
    for port in ports:
        # Se combinan descripcion y fabricante y se normalizan a minusculas.
        # Se combinan descripcion y fabricante porque muchos clones no incluyen
        # literalmente la palabra Arduino.
        description = f"{port.description} {port.manufacturer or ''}".lower()
        # any() devuelve True cuando al menos uno de los terminos aparece.
        # any() devuelve True en cuanto uno de los terminos preferidos aparece.
        if any(term in description for term in preferred_terms):
            return port.device

    # Como ultimo recurso se utiliza el primer puerto disponible.
    return ports[0].device


# ============================================================================
# FUNCION: read_serial()
# ----------------------------------------------------------------------------
# Abre el puerto serie, agrupa las magnitudes que llegan linea a linea y, cuando la muestra
# esta completa, la guarda localmente, la envia a la API o realiza ambas operaciones segun el
# parametro target.
#
# Parametros:
# - port: Nombre del puerto serie que se desea abrir.
# - baudrate: Velocidad de comunicacion serie en baudios.
# - sound_mode: Modo de tratamiento del sonido.
# - target: Destino de las lecturas: local, api o both.
# - api_url: URL base de la aplicacion remota.
#
# Retorno: No finaliza durante la ejecucion normal; funciona como bucle continuo de lectura.
# ============================================================================

def read_serial(port, baudrate, sound_mode, target="local", api_url=None):
    # Lee el stream serie y redirige cada medicion a BD local, API o ambos.
    # pyserial es imprescindible para abrir el puerto fisico.
    try:
        import serial
    except ImportError as error:
        raise SystemExit(
            "Falta pyserial. Instala dependencias con: "
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from error

    # La base de datos solo necesita inicializarse si se guardara en local.
    if target in ("local", "both"):
        init_db()
    # current acumula lecturas parciales hasta completar el paquete de sensores.
    # current actua como buffer temporal de una muestra incompleta.
    current = {}

    # serial.Serial abre el puerto con timeout=2 para evitar bloqueos indefinidos.
    # serial.Serial abre el puerto con el baudrate elegido y timeout=2 para que
    # readline() no quede bloqueado indefinidamente.
    with serial.Serial(port, baudrate, timeout=2) as arduino:
        # Se esperan dos segundos para dar tiempo al Arduino a reiniciarse tras
        # abrir la conexion serie, comportamiento habitual en placas Uno.
        # Muchas placas Arduino se reinician al abrir el puerto; estos dos segundos
        # permiten que vuelvan a comenzar a transmitir.
        time.sleep(2)
        print(f"Leyendo Arduino en {port} a {baudrate} baudios...")

        # El bucle permanece activo mientras la herramienta siga ejecutandose.
        while True:
            # readline() obtiene una linea en bytes; decode() la convierte a texto.
            # errors='ignore' evita que un byte invalido interrumpa el lector.
            # La cadena realiza: lectura de bytes -> decodificacion UTF-8 ->
            # descarte de bytes invalidos -> eliminacion de espacios/saltos.
            raw_line = arduino.readline().decode("utf-8", errors="ignore").strip()
            # Las lineas vacias se descartan inmediatamente mediante continue.
            if not raw_line:
                continue

            # Mostrar la linea cruda facilita depuracion del sketch y del puerto.
            print(raw_line)

            # El sketch suele emitir una magnitud por linea, asi que vamos agrupandolas.
            # .items() permite recorrer simultaneamente nombre de magnitud y patron.
            # .items() permite recorrer conjuntamente el nombre de magnitud y su
            # expresion regular asociada.
            for key, pattern in PATTERNS.items():
                # pattern.search() intenta localizar la magnitud dentro de la linea.
                # search() localiza el patron aunque no aparezca al principio de la linea.
                match = pattern.search(raw_line)
                if match:
                    # group(1) contiene exclusivamente el numero capturado por la regex.
                    # group(1) contiene exclusivamente el numero capturado por la regex.
                    current[key] = float(match.group(1))

            # Solo procesamos la muestra cuando ya tenemos todas las variables requeridas.
            # all() exige que las cinco claves definidas en PATTERNS existan en current.
            # all() exige que current contenga las cinco magnitudes antes de
            # considerar que se dispone de una muestra completa.
            if all(key in current for key in PATTERNS):
                # Se puede usar como lector local, pasarela hacia la nube o ambos a la vez.
                # target controla si se persiste localmente.
                # Los destinos local y both comparten la persistencia en SQLite.
                if target in ("local", "both"):
                    save_measurement(current, sound_mode)
                # En api o both se realiza tambien el envio remoto.
                # Los destinos api y both comparten el envio HTTP remoto.
                if target in ("api", "both"):
                    # La URL base es obligatoria para cualquier destino remoto.
                    if not api_url:
                        raise SystemExit("Debes indicar --api-url para usar target api o both.")
                    # El envio remoto se aisla en su propio try/except para que
                    # un error de Internet no cierre la conexion serie.
                    try:
                        post_measurement(api_url, current, sound_mode)
                    # RuntimeError representa aqui un fallo ya normalizado por
                    # post_measurement(). Se trata como aviso recuperable.
                    except RuntimeError as error:
                        # Render puede tardar en despertar o sufrir un corte puntual.
                        # El lector no debe cerrarse: informa y reintenta con la
                        # siguiente muestra mientras mantiene abierto el puerto.
                        # El error se muestra por consola y se continua con la
                        # siguiente muestra sin reiniciar el puerto.
                        print(f"AVISO DE ENVIO: {error}")
                # Una vez procesada la muestra, se vacia el buffer para la siguiente.
                # El buffer se reinicia para no mezclar magnitudes de ciclos distintos.
                current = {}


# ============================================================================
# FUNCION: main()
# ----------------------------------------------------------------------------
# Configura la interfaz de linea de comandos, resuelve el puerto serie y lanza el bucle
# principal de lectura.
#
# Parametros: ninguno.
#
# Retorno: No devuelve valor durante la ejecucion normal; configura y arranca el lector.
# ============================================================================

def main():
    # Punto de entrada CLI para configurar puerto, baudios y modo de sonido.
    # ArgumentParser define la ayuda general de la herramienta CLI.
    # ArgumentParser genera automaticamente la ayuda de uso y centraliza la
    # validacion de los parametros recibidos por consola.
    parser = argparse.ArgumentParser(
        description="Lee los datos del Arduino por Serial y los guarda en local o los envia a una API."
    )
    # --port es opcional porque detect_port() puede resolverlo automaticamente.
    parser.add_argument("--port", help="Puerto serie, por ejemplo COM3.")
    # type=int convierte el argumento y default=9600 reproduce la velocidad
    # habitual configurada en el sketch.
    parser.add_argument("--baudrate", type=int, default=9600)
    # --target limita los valores permitidos mediante choices.
    parser.add_argument(
        "--target",
        # choices restringe el valor a los tres destinos implementados.
        choices=["local", "api", "both"],
        default="local",
        help="local guarda en SQLite, api envia al despliegue remoto y both hace ambas cosas.",
    )
    # --api-url solamente es necesaria cuando target incluye el destino remoto.
    parser.add_argument(
        "--api-url",
        help="URL base publica de la app desplegada, por ejemplo https://smi-core-tfg.onrender.com",
    )
    # --sound-mode permite elegir entre porcentaje normalizado y lectura ADC cruda.
    parser.add_argument(
        "--sound-mode",
        # choices evita modos de sonido no soportados por normalize_sound().
        choices=["percent", "raw"],
        default="percent",
        help="percent convierte el sonido analogico 0-1023 a escala 0-100.",
    )
    # parse_args() procesa sys.argv y devuelve un objeto con los argumentos.
    # parse_args() procesa sys.argv y aplica tipos, valores por defecto y choices.
    args = parser.parse_args()

    # Si el usuario no indica puerto manualmente, intentamos adivinarlo.
    # El operador or prioriza el puerto indicado manualmente y utiliza deteccion
    # automatica solo cuando no se ha proporcionado --port.
    # El operador or prioriza --port y solo ejecuta detect_port() cuando no se
    # proporciono un puerto manualmente.
    port = args.port or detect_port()
    # Si no existe ningun puerto valido se aborta con un mensaje comprensible.
    if not port:
        raise SystemExit(
            "No he encontrado ningun puerto Serial. Conecta el Arduino o indica --port COMx."
        )

    # Finalmente se transfieren todos los parametros al bucle de lectura serie.
    # Los argumentos ya validados se transfieren al bucle continuo de adquisicion.
    read_serial(port, args.baudrate, args.sound_mode, args.target, args.api_url)


# =============================================================================
# PUNTO DE ENTRADA
# -----------------------------------------------------------------------------
# __name__ vale '__main__' solamente cuando el archivo se ejecuta directamente.
# Si se importa como modulo, main() no se ejecuta automaticamente.
# =============================================================================

# Este bloque solo se ejecuta cuando el fichero se lanza directamente con Python.
if __name__ == "__main__":
    main()
