"""Simulador local de planta industrial.

Genera lecturas pseudoaleatorias para varios nodos y las inserta en la misma
base de datos que consume la aplicacion web.
"""

# =============================================================================
# DESCRIPCION GENERAL DEL SIMULADOR
# -----------------------------------------------------------------------------
# Este script actua como fuente auxiliar de telemetria para S.M.I. CORE.
# Genera datos para diez nodos y los escribe directamente en la misma base
# SQLite que consumen el dashboard, el historico y los reportes.
#
# Flujo general:
# 1. Inicializa tabla e indice.
# 2. Genera temperatura, humedad, sonido, presion y luz.
# 3. Introduce ocasionalmente un valor fuera de rango.
# 4. Inserta una lectura por nodo.
# 5. Confirma la transaccion.
# 6. Espera cinco segundos y repite.
# =============================================================================

# =============================================================================
# IMPORTACIONES
# -----------------------------------------------------------------------------
# random genera datos pseudoaleatorios; sqlite3 persiste la telemetria;
# time controla la frecuencia del ciclo y datetime genera las trazas horarias.
# =============================================================================
import random
import sqlite3
import time
from datetime import datetime


# =============================================================================
# CONFIGURACION GLOBAL
# -----------------------------------------------------------------------------
# DB_PATH apunta a la base SQLite compartida con la aplicacion principal.
# =============================================================================
DB_PATH = "smi_core.db"

# =============================================================================
# CATALOGO DE NODOS
# -----------------------------------------------------------------------------
# Cada elemento representa un punto de medida de una zona de la planta.
# =============================================================================
# Catalogo basico de nodos para poblar distintas zonas de la maqueta industrial.
nodos = [
    {"id": "FISICO_ARDUINO", "tipo": "Maquinaria Pesada"},
    {"id": "SIM_ALMACEN_01", "tipo": "Ambiente"},
    {"id": "SIM_ALMACEN_02", "tipo": "Ambiente"},
    {"id": "SIM_SALA_MAQUINAS", "tipo": "Critico"},
    {"id": "SIM_EXTERIOR", "tipo": "Meteorologico"},
    {"id": "SIM_LINEA_PROD_A", "tipo": "Produccion"},
    {"id": "SIM_LINEA_PROD_B", "tipo": "Produccion"},
    {"id": "SIM_CUARTO_FRIO", "tipo": "Refrigeracion"},
    {"id": "SIM_ZONA_CARGA", "tipo": "Logistica"},
    {"id": "SIM_CALDERAS", "tipo": "Critico"},
]


# =============================================================================
# FUNCION: get_connection()
# -----------------------------------------------------------------------------
# Abre una conexion SQLite contra DB_PATH.
# Parametros: ninguno.
# Retorno: sqlite3.Connection.
# =============================================================================
def get_connection():
    # Abre la conexion SQLite usada por el simulador para inyectar telemetria.
    return sqlite3.connect(DB_PATH)


# =============================================================================
# FUNCION: init_db()
# -----------------------------------------------------------------------------
# Crea la tabla historica si no existe, mantiene compatibilidad con bases
# antiguas y crea el indice de consulta por nodo y fecha.
# Parametros: ninguno.
# Retorno: no devuelve valor; prepara la estructura persistente.
# =============================================================================
def init_db():
    # Prepara la tabla historica e indice necesarios para el simulador.
    # El gestor de contexto garantiza el cierre de la conexion al terminar.
    with get_connection() as connection:
        # CREATE TABLE IF NOT EXISTS permite repetir la inicializacion sin
        # destruir mediciones previamente almacenadas.
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
        # Se comprueba la estructura de la tabla para soportar versiones antiguas.
        # Compatibilidad con bases de datos antiguas que aun no tengan "luz".
        # PRAGMA table_info devuelve informacion de columnas; la comprension
        # extrae de cada fila el nombre de la columna.
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(historial_telemetria)").fetchall()
        ]
        # Si falta la magnitud luz, se agrega con valor por defecto cero.
        if "luz" not in columns:
            connection.execute(
                "ALTER TABLE historial_telemetria ADD COLUMN luz REAL NOT NULL DEFAULT 0"
            )
        # El indice compuesto acelera consultas historicas por nodo y fecha.
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_historial_nodo_fecha
            ON historial_telemetria (nodo_id, fecha_hora)
        """)


# =============================================================================
# FUNCION: generar_datos(nodo_id)
# -----------------------------------------------------------------------------
# Produce una lectura pseudoaleatoria para el nodo recibido. Con una probabilidad
# aproximada del 3 % introduce una incidencia fuera de rango para demostrar
# visualmente el sistema de alertas.
# Parametros:
# - nodo_id: identificador del nodo.
# Retorno: tupla (nodo_id, temperatura, humedad, sonido, presion, luz).
# =============================================================================
def generar_datos(nodo_id):
    # Produce una lectura base y, de forma ocasional, un fallo controlado de demo.
    # uniform() genera valores decimales dentro del intervalo indicado y
    # round(..., 2) limita la precision almacenada.
    temperatura = round(random.uniform(20.0, 26.0), 2)
    humedad = round(random.uniform(40.0, 55.0), 2)
    sonido = round(random.uniform(50.0, 65.0), 2)
    presion = round(random.uniform(1010.0, 1015.0), 2)
    # randint() genera un entero para representar el nivel de luz.
    luz = random.randint(250, 850)

    # Inyectamos fallos controlados con baja probabilidad para disparar alertas.
    # random.random() devuelve un valor entre 0 y 1; compararlo con 0.03
    # introduce aproximadamente un 3 % de probabilidad de fallo.
    if random.random() < 0.03:
        # choice() selecciona aleatoriamente una de las incidencias disponibles.
        fallo = random.choice(["temperatura", "humedad_alta", "humedad_baja", "sonido"])

        # Cada rama sustituye solamente la magnitud asociada al fallo elegido.
        if fallo == "temperatura":
            temperatura = round(random.uniform(38.0, 48.0), 2)
        elif fallo == "humedad_alta":
            humedad = round(random.uniform(85.0, 99.0), 2)
        elif fallo == "humedad_baja":
            humedad = round(random.uniform(5.0, 15.0), 2)
        elif fallo == "sonido":
            sonido = round(random.uniform(90.0, 120.0), 2)

        # La traza de consola permite comprobar cuándo se ha generado una alarma.
        # Este mensaje ayuda a comprobar por consola que el simulador tambien genera incidencias.
        print(f"ALERTA SIMULADA en {nodo_id}. Fallo en: {fallo.upper()}")

    # El orden de la tupla coincide con las columnas del INSERT posterior.
    return (nodo_id, temperatura, humedad, sonido, presion, luz)


# =============================================================================
# FUNCION: inyectar()
# -----------------------------------------------------------------------------
# Inicializa la base de datos y ejecuta un bucle continuo de captura simulada.
# En cada ciclo se inserta una muestra para cada uno de los diez nodos, se
# confirma la transaccion y se espera cinco segundos.
# Parametros: ninguno.
# Retorno: no finaliza durante la ejecucion normal.
# =============================================================================
def inyectar():
    # Inserta un ciclo continuo de lecturas para todos los nodos simulados.
    # Antes de insertar telemetria se garantiza que la tabla exista.
    init_db()

    # Se reutiliza la misma conexion durante toda la ejecucion del simulador.
    with get_connection() as connection:
        # El cursor ejecuta las sentencias SQL de insercion.
        cursor = connection.cursor()

        # Cada iteracion representa un ciclo de captura de toda la planta.
        # while True mantiene el simulador activo hasta detener manualmente el proceso.
        while True:
            # Se recorre el catalogo completo para producir una lectura por nodo.
            for nodo in nodos:
                # Se inserta una muestra por nodo para que el dashboard vea toda la instalacion.
                # La sentencia utiliza placeholders ?, de modo que los valores se
                # envian como parametros separados en lugar de concatenarse al SQL.
                cursor.execute(
                    """
                    INSERT INTO historial_telemetria
                    (nodo_id, temperatura, humedad, sonido, presion, luz)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    generar_datos(nodo["id"]),
                )

            # commit() confirma conjuntamente las diez inserciones del ciclo.
            connection.commit()
            # Dejamos traza horaria para saber que el bucle sigue activo.
            # datetime.now() obtiene la hora actual y strftime() la formatea.
            ahora = datetime.now().strftime("%H:%M:%S")
            # La traza permite verificar que el proceso sigue ejecutandose.
            print(f"[{ahora}] Ciclo de datos inyectado (10 nodos).")
            # sleep(5) establece un intervalo de cinco segundos entre ciclos.
            time.sleep(5)


# =============================================================================
# PUNTO DE ENTRADA DEL SCRIPT
# -----------------------------------------------------------------------------
# __name__ solo vale '__main__' cuando el archivo se ejecuta directamente.
# Si se importa desde otro modulo, el simulador no arranca automaticamente.
# =============================================================================
if __name__ == "__main__":
    # Mensaje inicial de diagnostico mostrado en consola.
    print("Simulador Industrial v2 activo (10 Nodos | Temp, Hum, Sonido, Presion)")
    # Esta llamada inicia el bucle continuo de inyeccion.
    inyectar()
