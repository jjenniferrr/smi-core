"""Backend principal de S.M.I. Core.

Este archivo centraliza la logica del sistema: base de datos, simulacion,
integracion con Arduino, API REST, autenticacion web y exportacion de informes.
"""

# =============================================================================
# ARQUITECTURA GENERAL DEL BACKEND
# -----------------------------------------------------------------------------
# Este fichero actua como nucleo de S.M.I. CORE y concentra varias capas que en
# una aplicacion de mayor escala podrian estar separadas en modulos distintos.
#
# CAPAS PRINCIPALES
# -----------------
# 1. Configuracion y constantes.
#    Define limites operativos, roles, rutas de base de datos y valores iniciales.
#
# 2. Autenticacion y autorizacion.
#    Gestiona login, logout, sesiones, contrasenas provisionales y permisos.
#
# 3. Persistencia SQLite.
#    Crea tablas, indices, migraciones y operaciones CRUD sobre los datos.
#
# 4. Telemetria.
#    Inserta mediciones, consulta historicos y calcula estados de alerta.
#
# 5. Simulacion.
#    Genera lecturas pseudoaleatorias para mantener el sistema operativo incluso
#    cuando no existe hardware conectado.
#
# 6. Integracion Arduino.
#    Detecta el puerto serie, interpreta las lineas del microcontrolador y guarda
#    las lecturas fisicas.
#
# 7. API REST.
#    Expone endpoints JSON consumidos por las distintas vistas HTML/JavaScript.
#
# 8. Exportacion.
#    Genera CSV, PDF y XLSX directamente en memoria para evitar archivos
#    temporales innecesarios en el servidor.
#
# 9. Arranque.
#    Inicializa la base de datos y pone en marcha las fuentes de datos en hilos
#    daemon antes de servir peticiones.
#
# CONCURRENCIA Y SQLITE
# ---------------------
# Esta version utiliza WAL, busy_timeout y reintentos de escritura porque el
# dashboard, el simulador y el lector Arduino pueden acceder a la misma base de
# datos al mismo tiempo. Estas medidas reducen los errores 'database is locked'
# sin cambiar el modelo de persistencia utilizado por el proyecto.
# =============================================================================

# ============================================================================
# IMPORTACIONES
# ----------------------------------------------------------------------------
# Flask proporciona servidor web, peticiones, sesiones, respuestas JSON,
# redirecciones y envio de archivos. El resto de modulos cubre persistencia,
# concurrencia, simulacion, seguridad, serializacion y exportacion.
# ============================================================================

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS
import csv
from datetime import datetime, timedelta
from html import escape as xml_escape
import hmac
from io import BytesIO, StringIO
import secrets
import os
import random
import re
import sqlite3
import threading
import time
import zipfile
from werkzeug.security import check_password_hash, generate_password_hash


# ============================================================================
# CONSTANTES Y CONFIGURACION GLOBAL
# ----------------------------------------------------------------------------
# Las variables de entorno permiten adaptar el mismo codigo a desarrollo y
# despliegue sin modificar el fichero fuente.
# ============================================================================

# Se consulta primero la variable de entorno DB_PATH. Si no esta definida se
# utiliza smi_core.db, lo que simplifica el desarrollo local.
DB_PATH = os.environ.get("DB_PATH", "smi_core.db")
# Limites globales utilizados por historicos y exportaciones para clasificar de
# forma rapida una lectura como normal o anomala.
LIMITES = {"temp": 35, "hum": 80, "min_hum": 20, "sonido": 85}
# Las credenciales administrativas solo existen cuando se proporcionan mediante
# variables de entorno. Nunca se incluyen valores de fallback en el codigo.
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "").strip()
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
INGESTION_TOKEN = os.environ.get("INGESTION_TOKEN", "").strip()
ROLE_NORMAL = "normal"
ROLE_MEDIO = "medio"
ROLE_ADMIN = "administrador"
# El set permite validar pertenencia de roles de forma directa mediante "in".
VALID_ROLES = {ROLE_NORMAL, ROLE_MEDIO, ROLE_ADMIN}

# Ajustes globales del sistema que se cargan al arrancar si la BD aun esta vacia.
DEFAULT_SETTINGS = {
    "umbral_temp_max": "35.0",
    "umbral_humedad_max": "80",
    "intervalo_muestreo": "5",
    "sensibilidad_luz": "300",
}
DEFAULT_NODE_CONFIG = {
    "umbral_temp_max": "35.0",
    "umbral_humedad_max": "80",
    "umbral_humedad_min": "20",
    "umbral_sonido_max": "85",
}

# Nodos base del simulador industrial. Cada uno representa una zona de la planta.
SIM_NODES = [
    "SIM_MAQUINA_PESADA",
    "SIM_ALMACEN_01",
    "SIM_ALMACEN_02",
    "SIM_SALA_MAQUINAS",
    "SIM_EXTERIOR",
    "SIM_LINEA_PROD_A",
    "SIM_LINEA_PROD_B",
    "SIM_CUARTO_FRIO",
    "SIM_ZONA_CARGA",
    "SIM_CALDERAS",
]

# Expresiones regulares para extraer valores desde las lineas del monitor serie.
ARDUINO_PATTERNS = {
    "temperatura": re.compile(r"Temperatura:\s*(-?\d+(?:\.\d+)?)"),
    "humedad": re.compile(r"Humedad:\s*(-?\d+(?:\.\d+)?)"),
    "presion": re.compile(r"Presion:\s*(-?\d+(?:\.\d+)?)"),
    "luz": re.compile(r"Nivel de luz:\s*(\d+)"),
    "sonido": re.compile(r"Nivel de sonido:\s*(\d+)"),
}

# Estado en memoria del lector Arduino para diagnostico rapido desde la web.
ARDUINO_STATUS = {
    "connected": False,
    "port": None,
    "last_error": None,
    "last_line": None,
    "last_saved": None,
    "recent_lines": [],
}
# Lock protege ARDUINO_STATUS frente a accesos concurrentes desde el hilo serie y
# desde peticiones HTTP que consultan el diagnostico.
ARDUINO_STATUS_LOCK = threading.Lock()

# ============================================================================
# CREACION Y CONFIGURACION DE LA APLICACION FLASK
# ----------------------------------------------------------------------------
# Se habilitan cookies HttpOnly, SameSite=Lax y configuracion Secure opcional.
# CORS se limita logicamente a las rutas /api/*, aunque el origen configurado
# actualmente permite cualquier origen.
# ============================================================================

# __name__ permite a Flask localizar correctamente recursos y contexto del modulo.
app = Flask(__name__)
# La clave firma criptograficamente la cookie de sesion. No se permite ningun
# fallback porque una clave publicada permitiria falsificar sesiones.
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY debe definirse mediante una variable de entorno")
app.secret_key = SECRET_KEY
# HttpOnly impide que JavaScript del navegador lea directamente la cookie de sesion.
app.config["SESSION_COOKIE_HTTPONLY"] = True
# SameSite=Lax reduce determinados escenarios de CSRF manteniendo la navegacion
# normal entre paginas del mismo sitio.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes", "si")
# CORS se aplica exclusivamente a /api/*. El comodin de origen facilita pruebas y
# despliegues, aunque en produccion podria restringirse a dominios conocidos.
CORS(app, resources={r"/api/*": {"origins": "*"}})


# ============================================================================
# AUTENTICACION WEB, SESIONES Y CONTROL DE ROLES
# ----------------------------------------------------------------------------
# Esta capa gestiona cuentas internas, login/logout, contrasenas provisionales,
# permisos por rol y proteccion global de rutas mediante @app.before_request.
#
# La ocultacion de enlaces en frontend es solo una mejora de interfaz. La
# autorizacion efectiva se aplica aqui, en el backend, antes de ejecutar rutas
# protegidas.
# ============================================================================

# ============================================================================
# FUNCION: default_user_seeds()
# ----------------------------------------------------------------------------
# Construye la lista de cuentas iniciales que se utilizaran para sembrar una instalacion
# nueva, eliminando posibles usuarios duplicados.
#
# Parametros: ninguno.
#
# Retorno: Lista de diccionarios con las cuentas iniciales sin duplicados.
# ============================================================================

def default_user_seeds():
    # Define las cuentas iniciales que se crean en una instalacion nueva.
    seeds = [
        {"username": "usuario", "password": "usuario123", "role": ROLE_NORMAL},
    ]
    # El administrador se crea solo cuando ambas credenciales llegan desde el
    # entorno de ejecucion. Asi nunca se publica una cuenta privilegiada fija.
    if AUTH_USERNAME and AUTH_PASSWORD:
        seeds.append({"username": AUTH_USERNAME, "password": AUTH_PASSWORD, "role": ROLE_ADMIN})
    unique = []
    seen = set()
    for seed in seeds:
        key = seed["username"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(seed)
    return unique


# ============================================================================
# FUNCION: get_user_by_username()
# ----------------------------------------------------------------------------
# Consulta en SQLite una cuenta concreta a partir de su nombre de usuario y devuelve los
# datos necesarios para autenticacion, permisos y gestion de sesion.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
# - username: Nombre de usuario que se desea consultar.
#
# Retorno: Fila sqlite3.Row correspondiente al usuario o None.
# ============================================================================

def get_user_by_username(connection, username):
    # Recupera una cuenta concreta para login, menu contextual y permisos.
    return connection.execute(
        """
        SELECT id, username, password_hash, role, active, must_change_password, created_at
        FROM user_accounts
        WHERE username = ?
        """,
        (username,),
    ).fetchone()


# ============================================================================
# FUNCION: current_role()
# ----------------------------------------------------------------------------
# Obtiene el rol asociado a la sesion autenticada y utiliza el rol normal como valor de
# respaldo.
#
# Parametros: ninguno.
#
# Retorno: Cadena con el rol actual.
# ============================================================================

def current_role():
    # Devuelve el rol actualmente guardado en la sesion.
    # session.get() evita KeyError y aplica ROLE_NORMAL como valor de respaldo.
    return session.get("role", ROLE_NORMAL)


# ============================================================================
# FUNCION: current_actor_name()
# ----------------------------------------------------------------------------
# Obtiene el nombre del usuario que realiza una accion para incorporarlo a los registros de
# auditoria.
#
# Parametros:
# - fallback: Nombre alternativo cuando la sesion no contiene un usuario.
#
# Retorno: Nombre utilizado como actor de auditoria.
# ============================================================================

def current_actor_name(fallback="OPERADOR_LOCAL"):
    # Nombre util para dejar trazabilidad real en el log de auditoria.
    # El operador "or" utiliza fallback cuando no existe nombre autenticado.
    return session.get("login_user") or fallback


# ============================================================================
# FUNCION: role_allows()
# ----------------------------------------------------------------------------
# Comprueba si el rol actualmente autenticado pertenece al conjunto de roles autorizados para
# una accion.
#
# Parametros:
# - required_roles: Coleccion de roles que autorizan la operacion.
#
# Retorno: True si el rol actual esta autorizado; False en caso contrario.
# ============================================================================

def role_allows(required_roles):
    # Comprueba si el rol de la sesion pertenece al conjunto autorizado.
    # set(required_roles) normaliza cualquier coleccion recibida y permite comprobar
    # pertenencia de manera directa.
    return current_role() in set(required_roles)


# ============================================================================
# FUNCION: sync_session_user()
# ----------------------------------------------------------------------------
# Sincroniza la informacion almacenada en la sesion con el estado real de la cuenta
# persistida en la base de datos.
#
# Parametros: ninguno.
#
# Retorno: Diccionario del usuario sincronizado o None si la sesion deja de ser valida.
# ============================================================================

def sync_session_user():
    # Refresca la cuenta desde la BD por si ha cambiado de rol o se ha desactivado.
    if not session.get("authenticated"):
        return None

    username = session.get("login_user")
    if not username:
        session.clear()
        return None

    connection = None
    try:
        connection = get_connection()
        user = get_user_by_username(connection, username)
        if not user or int(user["active"]) != 1:
            session.clear()
            return None

        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["must_change_password"] = int(user["must_change_password"] or 0)
        # g es almacenamiento contextual de Flask valido solamente durante la peticion
        # actual. Se evita asi volver a consultar el mismo usuario dentro de esa peticion.
        g.current_user = dict(user)
        return g.current_user
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: is_login_allowed_path()
# ----------------------------------------------------------------------------
# Determina si una ruta puede consultarse sin haber iniciado sesion.
#
# Parametros:
# - path: Ruta HTTP que se desea evaluar.
#
# Retorno: Booleano que indica si la ruta es publica.
# ============================================================================

def is_login_allowed_path(path):
    # Define las rutas publicas que deben seguir siendo accesibles sin sesion.
    public_paths = {
        "/login",
        "/logout",
        "/theme.css",
        "/theme.js",
        "/login.html",
        "/change-password.html",
        "/mi-cuenta.html",
    }
    if path in public_paths:
        return True
    # Se mantienen publicos algunos recursos comunes de navegador si aparecen.
    return path.endswith(".png") or path.endswith(".ico")


# ============================================================================
# FUNCION: is_authenticated()
# ----------------------------------------------------------------------------
# Indica si la sesion actual se encuentra marcada como autenticada.
#
# Parametros: ninguno.
#
# Retorno: Booleano que representa el estado de autenticacion.
# ============================================================================

def is_authenticated():
    # La sesion guarda un simple flag para las vistas web del proyecto.
    # bool() normaliza el valor de sesion a True o False.
    return bool(session.get("authenticated"))


# ============================================================================
# FUNCION: password_change_allowed_path()
# ----------------------------------------------------------------------------
# Define las rutas que siguen accesibles mientras una cuenta esta obligada a sustituir una
# contrasena provisional.
#
# Parametros:
# - path: Ruta HTTP que se desea evaluar.
#
# Retorno: Booleano que indica si la ruta sigue permitida durante el cambio obligatorio.
# ============================================================================

def password_change_allowed_path(path):
    # Cuando una cuenta entra con clave provisional, solo se le deja cambiarla.
    allowed_paths = {
        "/logout",
        "/theme.css",
        "/theme.js",
        "/change-password.html",
        "/api/auth/me",
        "/api/auth/change-password",
    }
    return path in allowed_paths or path.endswith(".png") or path.endswith(".ico")


# ============================================================================
# FUNCION: required_roles_for_request()
# ----------------------------------------------------------------------------
# Calcula los roles necesarios para acceder a la ruta y metodo HTTP de la peticion actual.
#
# Parametros: ninguno.
#
# Retorno: Conjunto de roles requeridos o None si la ruta no impone una restriccion adicional.
# ============================================================================

def required_roles_for_request():
    # Define las vistas y APIs reservadas a roles superiores.
    path = request.path

    if path == "/configurar-sistema.html":
        return {ROLE_MEDIO, ROLE_ADMIN}
    if path == "/gestion-permisos.html":
        return {ROLE_ADMIN}
    if path == "/api/configuracion":
        return {ROLE_MEDIO, ROLE_ADMIN}
    if path == "/api/nodos" and request.method == "POST":
        return {ROLE_MEDIO, ROLE_ADMIN}
    if path.startswith("/api/nodos/") and path.endswith("/config") and request.method == "POST":
        return {ROLE_MEDIO, ROLE_ADMIN}
    if path.startswith("/api/admin/"):
        return {ROLE_ADMIN}

    return None


# ============================================================================
# FUNCION: unauthorized_response()
# ----------------------------------------------------------------------------
# Genera la respuesta apropiada cuando no existe una sesion valida: JSON para API o
# redireccion para paginas web.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP 401 o redireccion al login.
# ============================================================================

def unauthorized_response():
    # Devuelve JSON en API y redireccion en paginas para no romper la UX.
    if request.path.startswith("/api/"):
        return jsonify({"error": "auth_required", "message": "Debes iniciar sesion"}), 401
    # Si existen parametros de consulta se conserva la URL completa para que, tras el
    # login, el usuario pueda regresar al destino originalmente solicitado.
    next_url = request.full_path if request.query_string else request.path
    return redirect(url_for("login_page", next=next_url))


# ============================================================================
# FUNCION: forbidden_response()
# ----------------------------------------------------------------------------
# Genera la respuesta utilizada cuando existe sesion, pero el rol no dispone de permisos
# suficientes.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP 403 o redireccion al indice.
# ============================================================================

def forbidden_response():
    # Se usa cuando existe sesion pero el rol no permite entrar en la accion.
    if request.path.startswith("/api/"):
        return jsonify({"error": "forbidden", "message": "No tienes permisos para esta accion"}), 403
    return redirect(url_for("index"))


# ============================================================================
# FUNCION: password_change_required_response()
# ----------------------------------------------------------------------------
# Informa o redirige al usuario cuando debe cambiar primero una contrasena provisional.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP 403 o redireccion a la pantalla de cambio de clave.
# ============================================================================

def password_change_required_response():
    # Redirige o informa de que la cuenta debe cambiar la contrasena primero.
    if request.path.startswith("/api/"):
        return jsonify({
            "error": "password_change_required",
            "message": "Debes cambiar la contrasena provisional antes de continuar",
        }), 403
    return redirect(url_for("change_password_page"))


# ============================================================================
# FUNCION: require_login()
# ----------------------------------------------------------------------------
# Filtro global before_request que protege paginas y endpoints, sincroniza la sesion y aplica
# las restricciones de rol.
#
# Parametros: ninguno.
#
# Retorno: None cuando la peticion puede continuar o una respuesta Flask cuando debe bloquearse.
#
# Decoradores:
# - app.before_request
# ============================================================================

@app.before_request
def require_login():
    # Protege la interfaz publica y la API sin bloquear la ingesta del Arduino.
    if is_login_allowed_path(request.path):
        return None
    if request.path == "/api/telemetria":
        return None
    if is_authenticated():
        user = sync_session_user()
        if not user:
            return unauthorized_response()

        # Si el admin ha creado la cuenta con clave provisional, forzamos el cambio.
        if int(user["must_change_password"] or 0) == 1 and not password_change_allowed_path(request.path):
            return password_change_required_response()

        required_roles = required_roles_for_request()
        if required_roles and not role_allows(required_roles):
            return forbidden_response()
        return None
    return unauthorized_response()


# ============================================================================
# FUNCION: login_page()
# ----------------------------------------------------------------------------
# Gestiona tanto la visualizacion del formulario de acceso como la validacion de credenciales
# y creacion de la sesion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/login", methods=["GET", "POST"])
# ============================================================================

@app.route("/login", methods=["GET", "POST"])
def login_page():
    # Muestra el formulario de acceso y valida las credenciales del operador.
    if request.method == "POST":
        # request.form contiene los campos enviados por el formulario HTML.
        # strip() elimina espacios accidentales al inicio o final del usuario.
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        connection = None
        try:
            connection = get_connection()
            user = get_user_by_username(connection, username)
            if (
                user
                and int(user["active"]) == 1
                # compare_digest realiza una comparacion diseñada para reducir diferencias
                # temporales observables entre cadenas.
                and secrets.compare_digest(username, user["username"])
                # La contrasena nunca se compara en texto plano: Werkzeug verifica el valor
                # introducido contra el hash persistido.
                and check_password_hash(user["password_hash"], password)
            ):
                session["authenticated"] = True
                session["user_id"] = user["id"]
                session["login_user"] = user["username"]
                session["role"] = user["role"]
                # Se registra la fecha de inicio de sesion en formato ISO, util para diagnostico.
                session["login_at"] = datetime.now().isoformat(timespec="seconds")
                session["must_change_password"] = int(user["must_change_password"] or 0)
                if int(user["must_change_password"] or 0) == 1:
                    return redirect(url_for("change_password_page"))
                next_url = request.args.get("next") or request.form.get("next") or url_for("index")
                if not next_url.startswith("/"):
                    next_url = url_for("index")
                return redirect(next_url)
        finally:
            if connection:
                connection.close()

        next_url = request.form.get("next") or request.args.get("next") or "/"
        return redirect(url_for("login_page", error=1, next=next_url))

    if is_authenticated():
        if session.get("must_change_password"):
            return redirect(url_for("change_password_page"))
        return redirect(url_for("index"))
    return send_from_directory(".", "login.html")


# ============================================================================
# FUNCION: logout()
# ----------------------------------------------------------------------------
# Elimina todos los datos de la sesion actual y redirige de nuevo al formulario de acceso.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/logout")
# ============================================================================

@app.route("/logout")
def logout():
    # Cierra la sesion actual y devuelve al formulario de acceso.
    session.clear()
    return redirect(url_for("login_page"))


# ============================================================================
# FUNCION: change_password_page()
# ----------------------------------------------------------------------------
# Sirve la pagina de cambio de contrasena exclusivamente a usuarios autenticados.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/change-password.html")
# ============================================================================

@app.route("/change-password.html")
def change_password_page():
    # Pantalla dedicada a actualizar la clave personal o provisional del usuario.
    if not is_authenticated():
        return redirect(url_for("login_page"))
    return send_from_directory(".", "change-password.html")


# ============================================================================
# FUNCION: my_account_page()
# ----------------------------------------------------------------------------
# Sirve la pagina personal de la cuenta cuando existe una sesion autenticada.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/mi-cuenta.html")
# ============================================================================

@app.route("/mi-cuenta.html")
def my_account_page():
    # Vista personal con la informacion basica de la sesion y accesos propios.
    if not is_authenticated():
        return redirect(url_for("login_page"))
    return send_from_directory(".", "mi-cuenta.html")


# ============================================================================
# FUNCION: get_auth_me()
# ----------------------------------------------------------------------------
# Expone al frontend la identidad, rol, estado de cambio de clave y permisos derivados de la
# sesion actual.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/auth/me", methods=["GET"])
# ============================================================================

@app.route("/api/auth/me", methods=["GET"])
def get_auth_me():
    # Expone la identidad y el rol actual para adaptar menus y pantallas.
    return jsonify({
        "authenticated": True,
        "username": session.get("login_user"),
        "role": current_role(),
        "must_change_password": bool(session.get("must_change_password")),
        "permissions": {
            "can_configure_system": role_allows({ROLE_MEDIO, ROLE_ADMIN}),
            "can_manage_permissions": role_allows({ROLE_ADMIN}),
            "can_change_own_password": True,
        },
    })


# ============================================================================
# FUNCION: change_own_password()
# ----------------------------------------------------------------------------
# Valida y actualiza la contrasena del usuario autenticado, elimina la marca provisional y
# registra la operacion en auditoria.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/auth/change-password", methods=["POST"])
# ============================================================================

@app.route("/api/auth/change-password", methods=["POST"])
def change_own_password():
    # Permite que cada usuario actualice unicamente su propia contrasena.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    if not current_password:
        return jsonify({"error": "Debes indicar tu contrasena actual"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "La nueva contrasena debe tener al menos 6 caracteres"}), 400
    if new_password != confirm_password:
        return jsonify({"error": "La confirmacion no coincide con la nueva contrasena"}), 400
    if current_password == new_password:
        return jsonify({"error": "La nueva contrasena debe ser distinta a la actual"}), 400

    connection = None
    try:
        connection = get_connection()
        user = get_user_by_username(connection, session.get("login_user") or "")
        if not user or int(user["active"]) != 1:
            session.clear()
            return jsonify({"error": "La cuenta ya no esta disponible"}), 401
        if not check_password_hash(user["password_hash"], current_password):
            return jsonify({"error": "La contrasena actual no es correcta"}), 400

        connection.execute(
            """
            UPDATE user_accounts
            SET password_hash = ?, must_change_password = 0
            WHERE id = ?
            """,
            (generate_password_hash(new_password), user["id"]),
        )
        connection.execute(
            """
            INSERT INTO audit_log (operario, parametro, valor_anterior, valor_nuevo, origen)
            VALUES (?, ?, ?, ?, 'Cambio de Clave')
            """,
            (current_actor_name("USUARIO"), f"Cuenta.{user['username']}.Password", "OCULTA", "ACTUALIZADA"),
        )
        connection.commit()

        session["must_change_password"] = 0
        return jsonify({"saved": True})
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: list_user_accounts()
# ----------------------------------------------------------------------------
# Devuelve al administrador la lista de cuentas internas sin exponer sus hashes de
# contrasena.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/admin/users", methods=["GET"])
# ============================================================================

@app.route("/api/admin/users", methods=["GET"])
def list_user_accounts():
    # Lista todas las cuentas disponibles para la vista de gestion de permisos.
    connection = None
    try:
        connection = get_connection()
        rows = connection.execute(
            """
            SELECT id, username, role, active, must_change_password, created_at
            FROM user_accounts
            ORDER BY username
            """
        ).fetchall()
        # La comprension transforma cada sqlite3.Row en un diccionario serializable a JSON.
        return jsonify([dict(row) for row in rows])
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: create_user_account()
# ----------------------------------------------------------------------------
# Crea una nueva cuenta interna con rol inicial y contrasena provisional, registrando la
# accion en auditoria.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/admin/users", methods=["POST"])
# ============================================================================

@app.route("/api/admin/users", methods=["POST"])
def create_user_account():
    # Permite al administrador crear cuentas internas con un rol inicial.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or ROLE_NORMAL).strip().lower()

    if not username:
        return jsonify({"error": "El nombre de usuario es obligatorio"}), 400
    if len(password) < 6:
        return jsonify({"error": "La contrasena debe tener al menos 6 caracteres"}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": "Rol no valido"}), 400

    connection = None
    try:
        connection = get_connection()
        connection.execute(
            """
            INSERT INTO user_accounts (username, password_hash, role, active, must_change_password)
            VALUES (?, ?, ?, 1, 1)
            """,
            (username, generate_password_hash(password), role),
        )
        connection.execute(
            """
            INSERT INTO audit_log (operario, parametro, valor_anterior, valor_nuevo, origen)
            VALUES (?, ?, '-', ?, 'Gestion de Permisos')
            """,
            (current_actor_name("ADMIN"), f"Cuenta.{username}", f"CREADA ({role}, CLAVE PROVISIONAL)"),
        )
        connection.commit()
        return jsonify({"created": True}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ya existe una cuenta con ese usuario"}), 409
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: update_user_account()
# ----------------------------------------------------------------------------
# Modifica rol, estado activo o contrasena de una cuenta y evita dejar el sistema sin
# administradores activos.
#
# Parametros:
# - user_id: Identificador numerico de la cuenta que se modifica.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
# ============================================================================

@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
def update_user_account(user_id):
    # Actualiza rol, estado activo o contrasena de una cuenta existente.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    connection = None
    try:
        connection = get_connection()
        user = connection.execute(
            "SELECT id, username, role, active, must_change_password FROM user_accounts WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return jsonify({"error": "La cuenta no existe"}), 404

        changes = []

        if "role" in payload:
            new_role = str(payload.get("role") or "").strip().lower()
            if new_role not in VALID_ROLES:
                return jsonify({"error": "Rol no valido"}), 400
            if user["role"] == ROLE_ADMIN and new_role != ROLE_ADMIN:
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM user_accounts WHERE role = ? AND active = 1",
                    (ROLE_ADMIN,),
                ).fetchone()[0]
                if int(active_admins) <= 1:
                    return jsonify({"error": "Debe quedar al menos un administrador activo"}), 400
            if new_role != user["role"]:
                connection.execute(
                    "UPDATE user_accounts SET role = ? WHERE id = ?",
                    (new_role, user_id),
                )
                changes.append(("Rol", user["role"], new_role))

        if "active" in payload:
            # Se aceptan varias representaciones textuales de verdadero para facilitar el
            # consumo del endpoint desde formularios o clientes distintos.
            new_active = 1 if str(payload.get("active")).lower() in ("1", "true", "si", "yes", "on") else 0
            if user["role"] == ROLE_ADMIN and int(user["active"]) == 1 and new_active == 0:
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM user_accounts WHERE role = ? AND active = 1",
                    (ROLE_ADMIN,),
                ).fetchone()[0]
                if int(active_admins) <= 1:
                    return jsonify({"error": "Debe quedar al menos un administrador activo"}), 400
            if int(user["active"]) != new_active:
                connection.execute(
                    "UPDATE user_accounts SET active = ? WHERE id = ?",
                    (new_active, user_id),
                )
                changes.append(("Activo", str(user["active"]), str(new_active)))

        if payload.get("password"):
            new_password = str(payload.get("password"))
            if len(new_password) < 6:
                return jsonify({"error": "La contrasena debe tener al menos 6 caracteres"}), 400
            connection.execute(
                "UPDATE user_accounts SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                (generate_password_hash(new_password), user_id),
            )
            changes.append(("Password", "OCULTA", "ACTUALIZADA (PROVISIONAL)"))

        for parametro, anterior, nuevo in changes:
            connection.execute(
                """
                INSERT INTO audit_log (operario, parametro, valor_anterior, valor_nuevo, origen)
                VALUES (?, ?, ?, ?, 'Gestion de Permisos')
                """,
                (current_actor_name("ADMIN"), f"Cuenta.{user['username']}.{parametro}", anterior, nuevo),
            )

        connection.commit()
        return jsonify({"saved": True, "changes": len(changes)})
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# CAPA DE DATOS Y ESTRUCTURA SQLITE
# ----------------------------------------------------------------------------
# Se centralizan conexion, creacion/migracion de tablas, indices y datos iniciales.
# Las consultas utilizan parametros ? siempre que los valores proceden de datos
# externos, reduciendo el riesgo de inyeccion SQL.
# ============================================================================

# ============================================================================
# FUNCION: get_connection()
# ----------------------------------------------------------------------------
# Abre una conexion SQLite configurada para poder acceder a las columnas mediante su nombre.
#
# Parametros: ninguno.
#
# Retorno: Conexion sqlite3.Connection configurada.
# ============================================================================

def get_connection():
    # timeout evita que una petición falle inmediatamente si el simulador está
    # terminando otra escritura. SQLite esperará hasta treinta segundos.
    # timeout=30 indica a sqlite3 que espere hasta treinta segundos cuando otro hilo
    # mantiene temporalmente bloqueado el archivo para escritura.
    connection = sqlite3.connect(DB_PATH, timeout=30)
    # sqlite3.Row permite acceder a una columna como row["nombre"] en lugar de usar
    # exclusivamente posiciones numericas.
    connection.row_factory = sqlite3.Row
    # busy_timeout aplica la misma espera también a las operaciones ejecutadas
    # después de haber abierto la conexión.
    # busy_timeout expresa la espera en milisegundos para operaciones posteriores a
    # la apertura de la conexion.
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


# ============================================================================
# FUNCION: init_db()
# ----------------------------------------------------------------------------
# Crea tablas, indices y migraciones compatibles con versiones anteriores y siembra la
# configuracion, usuarios y nodos iniciales.
#
# Parametros: ninguno.
#
# Retorno: No devuelve valor; crea o migra la estructura persistente.
# ============================================================================

def init_db():
    # Crea la estructura persistente del sistema y siembra datos base.
    with get_connection() as connection:
        # WAL permite que las lecturas del dashboard continúen mientras el
        # simulador o la pasarela del Arduino escriben una nueva medición.
        # WAL (Write-Ahead Logging) permite que lectores y escritor convivan mejor:
        # las nuevas escrituras se registran primero en un fichero de log separado.
        connection.execute("PRAGMA journal_mode = WAL")
        # synchronous=NORMAL ofrece un equilibrio entre durabilidad y rendimiento y es
        # una configuracion habitual cuando SQLite trabaja en modo WAL.
        connection.execute("PRAGMA synchronous = NORMAL")
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
        # Si la BD viene de una version anterior, comprobamos si le falta alguna columna.
        columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(historial_telemetria)").fetchall()
        ]
        if "luz" not in columns:
            connection.execute(
                "ALTER TABLE historial_telemetria ADD COLUMN luz REAL NOT NULL DEFAULT 0"
            )
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_historial_nodo_fecha
            ON historial_telemetria (nodo_id, fecha_hora)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                operario TEXT NOT NULL,
                parametro TEXT NOT NULL,
                valor_anterior TEXT NOT NULL,
                valor_nuevo TEXT NOT NULL,
                origen TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                parametro TEXT PRIMARY KEY,
                valor TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                nodo_id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL,
                origen TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS node_settings (
                nodo_id TEXT NOT NULL,
                parametro TEXT NOT NULL,
                valor TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (nodo_id, parametro),
                FOREIGN KEY (nodo_id) REFERENCES nodes(nodo_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                measurement_id INTEGER,
                nodo_id TEXT NOT NULL,
                tipo TEXT NOT NULL,
                valor REAL NOT NULL,
                umbral REAL NOT NULL,
                estado TEXT NOT NULL DEFAULT 'ABIERTA',
                fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_alerts_nodo_fecha
            ON alerts (nodo_id, fecha_hora)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Esta columna permite marcar claves provisionales creadas por admin.
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(user_accounts)").fetchall()
        }
        if "must_change_password" not in existing_columns:
            connection.execute(
                "ALTER TABLE user_accounts ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
            )
        # Inserta la configuracion por defecto solo la primera vez.
        for parametro, valor in DEFAULT_SETTINGS.items():
            connection.execute(
                """
                INSERT OR IGNORE INTO system_settings (parametro, valor)
                VALUES (?, ?)
                """,
                (parametro, valor),
            )
        seed_users(connection)
        seed_nodes(connection)
        # Si no hay auditoria previa, sembramos unas entradas de ejemplo para la demo.
        total_logs = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        if total_logs == 0:
            connection.executemany(
                """
                INSERT INTO audit_log
                (fecha_hora, operario, parametro, valor_anterior, valor_nuevo, origen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("2026-06-11 09:10:02", "ADMIN_01", "Umbral_Temp_Max", "35.0", "38.5", "Interfaz Web"),
                    ("2026-06-11 09:45:12", "OPERADOR_B", "Humedad_Alerta", "80", "75", "Interfaz Web"),
                    ("2026-06-11 10:05:44", "SYSTEM", "Intervalo_Muestreo", "10", "5", "Auto-Sync"),
                ],
            )


# ============================================================================
# FUNCION: seed_users()
# ----------------------------------------------------------------------------
# Crea las cuentas base del sistema o aplica migraciones suaves sobre instalaciones ya
# existentes.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
#
# Retorno: No devuelve valor; modifica la base de datos recibida.
# ============================================================================

def seed_users(connection):
    # Si la instalacion es nueva, se crean las cuentas base con sus tres roles.
    total_users = connection.execute("SELECT COUNT(*) FROM user_accounts").fetchone()[0]
    if total_users > 0:
        # Migracion suave: si existe la cuenta legacy "administrador", se renombra al nombre final "admin".
        legacy_admin = connection.execute(
            "SELECT id FROM user_accounts WHERE username = 'administrador'"
        ).fetchone()
        target_admin = connection.execute(
            "SELECT id FROM user_accounts WHERE username = 'admin'"
        ).fetchone()

        if legacy_admin and not target_admin:
            connection.execute(
                "UPDATE user_accounts SET username = 'admin' WHERE id = ?",
                (legacy_admin["id"],),
            )

        # Otra migracion suave: el rol intermedio paso de "operador" a "operario"
        # para que coincida con el nombre final mostrado al usuario.
        legacy_operator = connection.execute(
            "SELECT id FROM user_accounts WHERE username = 'operador'"
        ).fetchone()
        target_operator = connection.execute(
            "SELECT id FROM user_accounts WHERE username = 'operario'"
        ).fetchone()

        if legacy_operator and not target_operator:
            connection.execute(
                "UPDATE user_accounts SET username = 'operario' WHERE id = ?",
                (legacy_operator["id"],),
            )

        # Reforzamos las credenciales base de demo para que siempre queden
        # exactamente como las vas a presentar en clase.
        for user in default_user_seeds():
            connection.execute(
                """
                UPDATE user_accounts
                SET password_hash = ?, role = ?, active = 1, must_change_password = 0
                WHERE username = ?
                """,
                (
                    generate_password_hash(user["password"]),
                    user["role"],
                    user["username"],
                ),
            )
        return

    for user in default_user_seeds():
        connection.execute(
            """
            INSERT INTO user_accounts (username, password_hash, role, active, must_change_password)
            VALUES (?, ?, ?, 1, 0)
            """,
            (
                user["username"],
                generate_password_hash(user["password"]),
                user["role"],
            ),
        )


# ============================================================================
# FUNCION: seed_nodes()
# ----------------------------------------------------------------------------
# Registra los nodos simulados y el Arduino fisico y garantiza que todos dispongan de
# configuracion minima.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
#
# Retorno: No devuelve valor; modifica la base de datos recibida.
# ============================================================================

def seed_nodes(connection):
    # Registra los nodos simulados y el nodo fisico por defecto si aun no existen.
    for node_id in SIM_NODES:
        connection.execute(
            """
            INSERT OR IGNORE INTO nodes (nodo_id, nombre, tipo, origen, activo)
            VALUES (?, ?, ?, 'SIMULADO', 1)
            """,
            (node_id, node_id.replace("SIM_", "").replace("_", " ").title(), "Simulado"),
        )

    connection.execute(
        """
        INSERT OR IGNORE INTO nodes (nodo_id, nombre, tipo, origen, activo)
        VALUES ('FISICO_ARDUINO', 'Arduino Uno', 'Fisico', 'FISICO', 1)
        """
    )

    # Cada nodo debe arrancar con una configuracion minima de umbrales.
    rows = connection.execute("SELECT nodo_id FROM nodes").fetchall()
    for row in rows:
        for parametro, valor in DEFAULT_NODE_CONFIG.items():
            connection.execute(
                """
                INSERT OR IGNORE INTO node_settings (nodo_id, parametro, valor)
                VALUES (?, ?, ?)
                """,
                (row["nodo_id"], parametro, valor),
            )


# ============================================================================
# FUNCION: current_settings()
# ----------------------------------------------------------------------------
# Obtiene la configuracion global efectiva combinando los valores persistidos con los valores
# por defecto.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
#
# Retorno: Diccionario con la configuracion global efectiva.
# ============================================================================

def current_settings(connection):
    # Devuelve la configuracion global aplicando valores por defecto cuando falten.
    rows = connection.execute("SELECT parametro, valor FROM system_settings").fetchall()
    # copy() evita modificar accidentalmente el diccionario global de valores por defecto.
    settings = DEFAULT_SETTINGS.copy()
    # La comprension de diccionario transforma las filas persistidas en pares
    # parametro -> valor y update() sobrescribe solo los valores existentes en BD.
    settings.update({row["parametro"]: row["valor"] for row in rows})
    return settings


# ============================================================================
# FUNCION: node_config()
# ----------------------------------------------------------------------------
# Obtiene la configuracion efectiva de un nodo combinando sus ajustes persistidos con los
# umbrales por defecto.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
# - node_id: Identificador unico del nodo.
#
# Retorno: Diccionario con la configuracion efectiva del nodo.
# ============================================================================

def node_config(connection, node_id):
    # Recupera la configuracion efectiva de un nodo concreto.
    rows = connection.execute(
        "SELECT parametro, valor FROM node_settings WHERE nodo_id = ?",
        (node_id,),
    ).fetchall()
    # Cada nodo parte de una copia independiente de los umbrales por defecto.
    config = DEFAULT_NODE_CONFIG.copy()
    config.update({row["parametro"]: row["valor"] for row in rows})
    return config


# ============================================================================
# CONSULTAS, FILTROS TEMPORALES Y DETECCION DE ALERTAS
# ----------------------------------------------------------------------------
# Este bloque abstrae el acceso al historico, construye filtros reutilizables y
# diferencia entre limites globales de consulta y umbrales propios de cada nodo.
# ============================================================================

# ============================================================================
# FUNCION: simulated_nodes()
# ----------------------------------------------------------------------------
# Devuelve los nodos simulados activos que participan en la generacion automatica de
# telemetria.
#
# Parametros: ninguno.
#
# Retorno: Lista de identificadores de nodos que deben simularse.
# ============================================================================

def simulated_nodes():
    # Lista solo los nodos simulados activos que deben generar lecturas automaticas.
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT nodo_id
            FROM nodes
            WHERE origen = 'SIMULADO' AND activo = 1
            ORDER BY nodo_id
            """
        ).fetchall()
        node_ids = [row["nodo_id"] for row in rows]

        # En despliegue podemos forzar un "Arduino virtual" para que el nodo fisico
        # tambien tenga lecturas aunque no exista una placa conectada de verdad.
        if os.environ.get("SIMULATE_ARDUINO", "0").lower() in ("1", "true", "yes", "si"):
            physical = connection.execute(
                """
                SELECT nodo_id
                FROM nodes
                WHERE nodo_id = 'FISICO_ARDUINO' AND activo = 1
                """
            ).fetchone()
            if physical:
                node_ids.append("FISICO_ARDUINO")

        return node_ids


# ============================================================================
# FUNCION: parse_datetime_value()
# ----------------------------------------------------------------------------
# Interpreta una fecha procedente de la interfaz aceptando varios formatos con o sin
# componente horaria.
#
# Parametros:
# - value: Valor recibido que debe interpretarse o formatearse.
#
# Retorno: Tupla (fecha, solo_fecha).
# ============================================================================

def parse_datetime_value(value):
    # Interpreta fechas recibidas desde la interfaz en varios formatos compatibles.
    if not value:
        return None, False

    value = value.strip()
    formats = [
        ("%Y-%m-%d", True),
        ("%Y-%m-%dT%H:%M", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y-%m-%dT%H:%M:%S", False),
        ("%Y-%m-%d %H:%M:%S", False),
    ]

    # Probamos varios formatos porque desde la UI pueden llegar fechas con o sin hora.
    for fmt, date_only in formats:
        try:
            return datetime.strptime(value, fmt), date_only
        except ValueError:
            continue

    raise ValueError(f"Formato de fecha no valido: {value}")


# ============================================================================
# FUNCION: build_date_filter()
# ----------------------------------------------------------------------------
# Construye las clausulas SQL y parametros necesarios para filtrar telemetria por intervalo
# temporal.
#
# Parametros:
# - start: Inicio opcional del intervalo temporal.
# - end: Fin opcional del intervalo temporal.
# - period: Periodo rapido opcional, por ejemplo 24h, week o month.
#
# Retorno: Tupla formada por fragmento WHERE SQL y lista de parametros.
# ============================================================================

def build_date_filter(start=None, end=None, period=None):
    # Construye el filtro SQL comun para consultas historicas por rango temporal.
    clauses = []
    params = []

    # Los rangos rapidos de la interfaz se convierten aqui a fechas concretas.
    if period:
        now = datetime.now()
        if period == "24h":
            start = now - timedelta(hours=24)
        elif period == "week":
            start = now - timedelta(days=7)
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    start_is_date_only = False
    end_is_date_only = False

    if isinstance(start, str):
        start, start_is_date_only = parse_datetime_value(start)
    if isinstance(end, str):
        end, end_is_date_only = parse_datetime_value(end)

    # La consulta usa [start, end) para evitar duplicados al cambiar de pagina/rango.
    if start:
        clauses.append("fecha_hora >= ?")
        params.append(start.strftime("%Y-%m-%d %H:%M:%S"))
    if end:
        if end_is_date_only:
            end = end + timedelta(days=1)
        clauses.append("fecha_hora < ?")
        params.append(end.strftime("%Y-%m-%d %H:%M:%S"))

    # join() concatena las condiciones mediante AND. El ternario evita generar WHERE
    # cuando no existe ningun filtro.
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


# ============================================================================
# FUNCION: fetch_measurements()
# ----------------------------------------------------------------------------
# Recupera mediciones historicas aplicando filtros de fecha, nodo, alertas y limite de
# resultados.
#
# Parametros:
# - start: Inicio opcional del intervalo temporal.
# - end: Fin opcional del intervalo temporal.
# - period: Periodo rapido opcional, por ejemplo 24h, week o month.
# - only_alerts: Indica si deben conservarse solamente mediciones anomalas.
# - limit: Numero maximo opcional de registros.
# - node_id: Identificador unico del nodo.
#
# Retorno: Lista de mediciones representadas como diccionarios.
# ============================================================================

def fetch_measurements(start=None, end=None, period=None, only_alerts=False, limit=None, node_id=None):
    # Lee mediciones historicas aplicando filtros de fecha, nodo, alertas y limite.
    where_sql, params = build_date_filter(start=start, end=end, period=period)
    # Permite filtrar por nodo cuando una vista solo quiere una maquina concreta.
    if node_id:
        where_sql += " AND nodo_id = ?" if where_sql else "WHERE nodo_id = ?"
        params.append(node_id)
    query = f"""
        SELECT *
        FROM historial_telemetria
        {where_sql}
        ORDER BY fecha_hora DESC
    """

    # En vistas como dashboard o reportes reducimos el numero de filas para no cargar de mas.
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection() as connection:
        rows = [dict(row) for row in connection.execute(query, params).fetchall()]

    # Algunas vistas piden solo registros anormales y se filtran aqui.
    if only_alerts:
        # La comprension conserva unicamente las filas para las que is_alert() devuelve True.
        rows = [row for row in rows if is_alert(row)]

    return rows


# ============================================================================
# FUNCION: is_alert()
# ----------------------------------------------------------------------------
# Indica si una medicion incumple al menos uno de los limites operativos globales.
#
# Parametros:
# - row: Fila de telemetria representada como diccionario.
#
# Retorno: Booleano.
# ============================================================================

def is_alert(row):
    # Determina si una medicion cae fuera de los limites operativos globales.
    # Una lista vacia se evalua como False y una lista con motivos como True.
    return bool(alert_reasons(row))


# ============================================================================
# FUNCION: alert_reasons()
# ----------------------------------------------------------------------------
# Calcula las causas concretas por las que una medicion debe considerarse anomala.
#
# Parametros:
# - row: Fila de telemetria representada como diccionario.
#
# Retorno: Lista de textos con los motivos de alerta.
# ============================================================================

def alert_reasons(row):
    # Resume los motivos por los que una fila concreta debe tratarse como alerta.
    reasons = []
    temperatura = float(row["temperatura"])
    humedad = float(row["humedad"])
    sonido = float(row["sonido"])

    # Estos limites globales sirven como criterio rapido para historicos y exportes.
    if temperatura > LIMITES["temp"]:
        reasons.append("Temperatura alta")
    if humedad > LIMITES["hum"]:
        reasons.append("Humedad alta")
    if humedad < LIMITES["min_hum"]:
        reasons.append("Humedad baja")
    if sonido > LIMITES["sonido"]:
        reasons.append("Ruido alto")

    return reasons


# ============================================================================
# FUNCION: row_status()
# ----------------------------------------------------------------------------
# Convierte el resultado de la deteccion de alertas en una etiqueta simple NORMAL o ALERTA.
#
# Parametros:
# - row: Fila de telemetria representada como diccionario.
#
# Retorno: Cadena NORMAL o ALERTA.
# ============================================================================

def row_status(row):
    # Devuelve el estado visual principal de una medicion para tablas y tarjetas.
    # El operador ternario convierte el resultado booleano en una etiqueta legible.
    return "ALERTA" if is_alert(row) else "NORMAL"


# ============================================================================
# FUNCION: node_alerts_for_measurement()
# ----------------------------------------------------------------------------
# Evalua una lectura contra los umbrales especificos del nodo y devuelve todas las
# incidencias detectadas.
#
# Parametros:
# - data: Datos de una medicion o payload normalizado.
# - config: Configuracion efectiva del nodo.
#
# Retorno: Lista de tuplas (tipo, valor, umbral).
# ============================================================================

def node_alerts_for_measurement(data, config):
    # Evalua una medicion contra los umbrales configurados del nodo correspondiente.
    alerts = []
    temperatura = float(data["temperatura"])
    humedad = float(data["humedad"])
    sonido = float(data["sonido"])

    temp_max = float(config["umbral_temp_max"])
    hum_max = float(config["umbral_humedad_max"])
    hum_min = float(config["umbral_humedad_min"])
    sonido_max = float(config["umbral_sonido_max"])

    # Aqui se evalua la lectura contra la configuracion propia del nodo, no la global.
    if temperatura > temp_max:
        alerts.append(("TEMPERATURA", temperatura, temp_max))
    if humedad > hum_max:
        alerts.append(("HUMEDAD_ALTA", humedad, hum_max))
    if humedad < hum_min:
        alerts.append(("HUMEDAD_BAJA", humedad, hum_min))
    if sonido > sonido_max:
        alerts.append(("RUIDO", sonido, sonido_max))

    return alerts


# ============================================================================
# FUNCION: record_measurement_alerts()
# ----------------------------------------------------------------------------
# Persiste en la tabla alerts todas las incidencias derivadas de una medicion concreta.
#
# Parametros:
# - connection: Conexion SQLite activa utilizada por la operacion.
# - measurement_id: Identificador de la medicion persistida.
# - data: Datos de una medicion o payload normalizado.
#
# Retorno: No devuelve valor; persiste alertas mediante la conexion recibida.
# ============================================================================

def record_measurement_alerts(connection, measurement_id, data):
    # Guarda en la tabla de alertas todas las incidencias detectadas en una medicion.
    config = node_config(connection, data["nodo_id"])
    # Una misma medicion puede abrir varias alertas si incumple varios umbrales.
    # La funcion devuelve tuplas (tipo, valor, umbral), que se desempaquetan en cada
    # iteracion para persistir una alerta independiente.
    for tipo, valor, umbral in node_alerts_for_measurement(data, config):
        connection.execute(
            """
            INSERT INTO alerts (measurement_id, nodo_id, tipo, valor, umbral)
            VALUES (?, ?, ?, ?, ?)
            """,
            (measurement_id, data["nodo_id"], tipo, valor, umbral),
        )


# ============================================================================
# FUNCION: insert_measurement()
# ----------------------------------------------------------------------------
# Guarda una lectura de telemetria y genera inmediatamente las alertas relacionadas con esa
# misma medicion.
#
# Parametros:
# - data: Datos de una medicion o payload normalizado.
#
# Retorno: Identificador numerico de la medicion insertada.
# ============================================================================

def insert_measurement(data):
    # Inserta una lectura en telemetria y genera sus alertas derivadas. Los
    # reintentos absorben colisiones breves con el hilo del simulador.
    # Se limita el numero de reintentos para evitar un bucle infinito si el bloqueo
    # no es temporal.
    max_attempts = 5

    # range(max_attempts) produce los indices 0..4 y permite calcular una espera
    # progresiva en cada intento.
    for attempt in range(max_attempts):
        connection = get_connection()
        try:
            # Primero guardamos la lectura bruta.
            cursor = connection.execute(
                """
                INSERT INTO historial_telemetria
                (nodo_id, temperatura, humedad, sonido, presion, luz)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data["nodo_id"],
                    data["temperatura"],
                    data["humedad"],
                    data["sonido"],
                    data["presion"],
                    data.get("luz", 0),
                ),
            )

            # Después se enlazan las alertas de la misma medición y se confirma
            # toda la operación como una única transacción.
            # lastrowid recupera el identificador AUTOINCREMENT asignado por SQLite a la
            # medicion recien insertada.
            measurement_id = cursor.lastrowid
            record_measurement_alerts(connection, measurement_id, data)
            connection.commit()
            return measurement_id
        except sqlite3.OperationalError as error:
            # rollback() descarta la transaccion parcial antes de reintentar o propagar el error.
            connection.rollback()

            # SQLite comunica los bloqueos mediante OperationalError. Se inspecciona el texto
            # para distinguir un bloqueo transitorio de otros errores operativos.
            is_locked = "locked" in str(error).lower()
            if not is_locked or attempt == max_attempts - 1:
                raise

            # La espera crece ligeramente en cada intento para no competir de
            # nuevo con la misma transacción que mantiene ocupado el archivo.
            # Backoff lineal: 0.2 s, 0.4 s, 0.6 s... entre intentos sucesivos.
            time.sleep(0.2 * (attempt + 1))
        except Exception:
            # rollback() descarta la transaccion parcial antes de reintentar o propagar el error.
            connection.rollback()
            raise
        finally:
            # El gestor de contexto de sqlite3 confirma transacciones, pero no
            # cierra la conexión. Aquí se libera siempre de forma explícita.
            connection.close()

    raise RuntimeError("No se pudo guardar la medición después de varios intentos.")


# ============================================================================
# GENERACION AUTOMATICA DE TELEMETRIA SIMULADA
# ----------------------------------------------------------------------------
# Los nodos simulados producen valores pseudoaleatorios y, de forma ocasional,
# incidencias fuera de rango para demostrar el funcionamiento de las alertas.
# ============================================================================

# ============================================================================
# FUNCION: generate_simulated_measurement()
# ----------------------------------------------------------------------------
# Genera una lectura pseudoaleatoria para un nodo simulado, incluyendo fallos ocasionales
# destinados a demostrar el sistema de alertas.
#
# Parametros:
# - node_id: Identificador unico del nodo.
#
# Retorno: Diccionario con una lectura simulada completa.
# ============================================================================

def generate_simulated_measurement(node_id):
    # Fabrica una lectura pseudoaleatoria con fallos puntuales para la demo.
    temperatura = round(random.uniform(20.0, 26.0), 2)
    humedad = round(random.uniform(40.0, 55.0), 2)
    sonido = round(random.uniform(50.0, 65.0), 2)
    presion = round(random.uniform(1010.0, 1015.0), 2)
    luz = random.randint(250, 850)

    # De forma ocasional generamos un valor fuera de rango para demostrar alarmas.
    # random.random() devuelve un valor entre 0 y 1. Compararlo con 0.03 introduce
    # una probabilidad aproximada del 3 % de generar una incidencia.
    if random.random() < 0.03:
        fallo = random.choice(["temperatura", "humedad_alta", "humedad_baja", "sonido"])
        if fallo == "temperatura":
            temperatura = round(random.uniform(38.0, 48.0), 2)
        elif fallo == "humedad_alta":
            humedad = round(random.uniform(85.0, 99.0), 2)
        elif fallo == "humedad_baja":
            humedad = round(random.uniform(5.0, 15.0), 2)
        elif fallo == "sonido":
            sonido = round(random.uniform(90.0, 120.0), 2)

        print(f"ALERTA SIMULADA en {node_id}. Fallo en: {fallo.upper()}")

    return {
        "nodo_id": node_id,
        "temperatura": temperatura,
        "humedad": humedad,
        "sonido": sonido,
        "presion": presion,
        "luz": luz,
    }


# ============================================================================
# FUNCION: simulated_data_loop()
# ----------------------------------------------------------------------------
# Bucle de fondo que genera y persiste telemetria periodica para todos los nodos simulados
# activos.
#
# Parametros: ninguno.
#
# Retorno: No finaliza durante la ejecucion normal; funciona como bucle de fondo.
# ============================================================================

def simulated_data_loop():
    # Bucle de fondo que mantiene vivos los nodos simulados mientras corre la API.
    print("Simulador automatico activo: generando nodos SIM_* cada 5 segundos")
    while True:
        try:
            # Cada ciclo genera una lectura fresca para todos los nodos activos.
            for node_id in simulated_nodes():
                insert_measurement(generate_simulated_measurement(node_id))
            time.sleep(5)
        except Exception as error:
            print(f"Error en simulador automatico: {error}")
            time.sleep(5)


# ============================================================================
# FUNCION: normalize_arduino_sound()
# ----------------------------------------------------------------------------
# Convierte el valor analogico de sonido recibido desde Arduino a un porcentaje normalizado.
#
# Parametros:
# - raw_value: Valor analogico bruto recibido desde Arduino.
#
# Retorno: Porcentaje normalizado redondeado a dos decimales.
# ============================================================================

def normalize_arduino_sound(raw_value):
    # Convierte el valor analogico de sonido del Arduino a un porcentaje legible.
    # El ADC de Arduino trabaja habitualmente en una escala de 10 bits (0-1023).
    # La regla de tres convierte ese rango a un porcentaje 0-100.
    return round((float(raw_value) / 1023.0) * 100.0, 2)


# ============================================================================
# INTEGRACION CON ARDUINO Y PUERTO SERIE
# ----------------------------------------------------------------------------
# Se detecta el puerto, se leen lineas en un hilo daemon, se extraen magnitudes
# mediante expresiones regulares y se persisten lecturas completas.
#
# ARDUINO_STATUS_LOCK evita condiciones de carrera al compartir estado entre el
# hilo serie y las peticiones HTTP.
# ============================================================================

# ============================================================================
# FUNCION: update_arduino_status()
# ----------------------------------------------------------------------------
# Actualiza de forma segura el diccionario compartido con el estado actual del lector
# Arduino.
#
# Parametros:
# - **values: Pares nombre=valor recibidos de forma variable.
#
# Retorno: No devuelve valor; actualiza estado compartido.
# ============================================================================

def update_arduino_status(**values):
    # Actualiza el estado compartido del lector Arduino para diagnostico y paneles.
    with ARDUINO_STATUS_LOCK:
        # update() modifica solamente las claves recibidas y conserva el resto del estado.
        ARDUINO_STATUS.update(values)


# ============================================================================
# FUNCION: remember_arduino_line()
# ----------------------------------------------------------------------------
# Conserva la ultima linea serie y mantiene un buffer limitado con las lineas recientes del
# Arduino.
#
# Parametros:
# - line: Linea de texto recibida desde el puerto serie.
#
# Retorno: No devuelve valor; actualiza el buffer en memoria.
# ============================================================================

def remember_arduino_line(line):
    # Conserva las ultimas lineas serie para depuracion rapida desde la API.
    with ARDUINO_STATUS_LOCK:
        ARDUINO_STATUS["last_line"] = line
        ARDUINO_STATUS["recent_lines"].append(line)
        # Conservamos un buffer corto para no crecer indefinidamente en memoria.
        # El slicing [-20:] conserva solo las veinte entradas mas recientes y limita el
        # crecimiento de memoria del buffer de diagnostico.
        ARDUINO_STATUS["recent_lines"] = ARDUINO_STATUS["recent_lines"][-20:]


# ============================================================================
# FUNCION: detect_arduino_port()
# ----------------------------------------------------------------------------
# Detecta el puerto serie mas probable del Arduino, permitiendo tambien forzarlo mediante
# variable de entorno.
#
# Parametros: ninguno.
#
# Retorno: Nombre del puerto detectado o None.
# ============================================================================

def detect_arduino_port():
    # Detecta automaticamente el puerto serie mas probable del Arduino.
    forced_port = os.environ.get("ARDUINO_PORT")
    if forced_port:
        return forced_port

    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    # comports() devuelve un iterable de puertos disponibles; list() lo materializa
    # para poder comprobar longitud y recorrerlo varias veces.
    ports = list(list_ports.comports())
    if not ports:
        return None

    # Muchos clónicos se anuncian como CH340/WCH y no como "Arduino" puro.
    preferred_terms = ("arduino", "usb", "ch340", "wch", "serial")
    for port in ports:
        description = f"{port.description} {port.manufacturer or ''}".lower()
        # any() devuelve True en cuanto uno de los terminos conocidos aparece en la
        # descripcion del dispositivo.
        if any(term in description for term in preferred_terms):
            return port.device

    return ports[0].device


# ============================================================================
# FUNCION: arduino_reader_loop()
# ----------------------------------------------------------------------------
# Bucle de lectura serie que reconecta automaticamente, recompone mediciones completas y las
# persiste en SQLite.
#
# Parametros: ninguno.
#
# Retorno: No finaliza durante la ejecucion normal salvo ausencia de pyserial.
# ============================================================================

def arduino_reader_loop():
    # Escucha el puerto serie, recompone lecturas completas y las persiste.
    try:
        import serial
    except ImportError:
        print("pyserial no esta instalado. El lector Arduino queda desactivado.")
        return

    # El lector intenta reconectar indefinidamente para que el sistema se recupere solo.
    while True:
        port = detect_arduino_port()
        if not port:
            update_arduino_status(
                connected=False,
                port=None,
                last_error="No se ha detectado ningun Arduino por USB.",
            )
            time.sleep(10)
            continue

        # current va acumulando cada magnitud hasta completar una lectura entera.
        current = {}
        try:
            print(f"Arduino detectado en {port}. Leyendo datos fisicos...")
            update_arduino_status(
                connected=False,
                port=port,
                last_error=None,
            )
            # El gestor de contexto abre el puerto a 9600 baudios y garantiza su cierre cuando
            # se pierde la conexion o se produce una excepcion.
            with serial.Serial(port, 9600, timeout=2) as arduino:
                update_arduino_status(connected=True, port=port, last_error=None)
                time.sleep(2)
                while True:
                    # readline() devuelve bytes. decode() los transforma a texto y errors="ignore"
                    # evita detener el proceso por un byte no valido.
                    line = arduino.readline().decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue

                    remember_arduino_line(line)

                    # Cada linea serie puede traer un unico valor; por eso vamos juntandolos.
                    # items() permite recorrer conjuntamente el nombre de la magnitud y su expresion
                    # regular asociada.
                    for key, pattern in ARDUINO_PATTERNS.items():
                        match = pattern.search(line)
                        if match:
                            # group(1) contiene el valor numerico capturado por el primer grupo de la regex.
                            current[key] = float(match.group(1))

                    # Solo guardamos cuando ya han llegado temperatura, humedad, presion, luz y sonido.
                    # all() solo devuelve True cuando el buffer current contiene todas las magnitudes
                    # definidas en ARDUINO_PATTERNS.
                    if all(key in current for key in ARDUINO_PATTERNS):
                        measurement = {
                            "nodo_id": "FISICO_ARDUINO",
                            "temperatura": current["temperatura"],
                            "humedad": current["humedad"],
                            "presion": current["presion"],
                            "luz": current["luz"],
                            "sonido": normalize_arduino_sound(current["sonido"]),
                        }
                        insert_measurement(measurement)
                        update_arduino_status(last_saved=measurement, last_error=None)
                        print("Lectura fisica guardada: FISICO_ARDUINO")
                        current = {}
        except Exception as error:
            update_arduino_status(connected=False, port=port, last_error=str(error))
            print(f"No se pudo leer Arduino en {port}: {error}")
            time.sleep(10)


# ============================================================================
# FUNCION: start_background_sources()
# ----------------------------------------------------------------------------
# Arranca los hilos daemon responsables del simulador y, cuando esta habilitado, del lector
# fisico Arduino.
#
# Parametros: ninguno.
#
# Retorno: No devuelve valor; inicia hilos daemon.
# ============================================================================

def start_background_sources():
    # Arranca los hilos de simulacion y Arduino al levantar el servidor.
    # El simulador siempre se levanta para que la interfaz no quede vacia.
    # daemon=True hace que el hilo no impida finalizar el proceso principal.
    threading.Thread(target=simulated_data_loop, daemon=True).start()
    if os.environ.get("ENABLE_ARDUINO", "1").lower() not in ("0", "false", "no"):
        # El lector fisico solo se activa si no se ha deshabilitado por variable de entorno.
        # El lector Arduino se ejecuta de forma paralela al servidor HTTP.
        threading.Thread(target=arduino_reader_loop, daemon=True).start()
    else:
        update_arduino_status(
            connected=False,
            port=None,
            last_error="Arduino desactivado por configuracion",
        )


# ============================================================================
# EXPORTACION DE DATOS
# ----------------------------------------------------------------------------
# Se generan CSV, PDF y XLSX directamente en memoria. El PDF y el XLSX se crean
# manualmente para reducir dependencias externas y demostrar la estructura de los
# formatos utilizados.
# ============================================================================

# ============================================================================
# FUNCION: csv_response()
# ----------------------------------------------------------------------------
# Construye una respuesta HTTP descargable en CSV a partir de filas de telemetria.
#
# Parametros:
# - filename: Nombre con el que se entregara el archivo descargado.
# - rows: Coleccion de filas que se desea exportar.
#
# Retorno: Objeto Response de Flask preparado como descarga CSV.
# ============================================================================

def csv_response(filename, rows):
    # Genera una descarga CSV comun para telemetria y reportes de alertas.
    # StringIO mantiene el CSV en memoria como texto y evita crear un archivo temporal.
    output = StringIO()
    # lineterminator fija saltos de linea uniformes independientemente del sistema.
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "ID_REGISTRO",
        "FECHA_HORA",
        "NODO_ORIGEN",
        "TEMPERATURA_C",
        "HUMEDAD_PORC",
        "RUIDO_DB",
        "PRESION_HPA",
        "LUZ_RAW",
        "ESTADO",
        "MOTIVOS_ALERTA",
    ])

    # Cada fila exportada incluye tambien el estado y las causas de alerta.
    for row in rows:
        writer.writerow([
            row["id"],
            row["fecha_hora"],
            row["nodo_id"],
            row["temperatura"],
            row["humedad"],
            row["sonido"],
            row["presion"],
            row["luz"],
            row_status(row),
            "; ".join(alert_reasons(row)),
        ])

    return Response(
        # El BOM UTF-8 (\ufeff) mejora la deteccion de codificacion al abrir el CSV con
        # determinadas versiones de Excel.
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================================
# FUNCION: pdf_escape()
# ----------------------------------------------------------------------------
# Escapa caracteres que tienen significado especial dentro de las cadenas de texto del PDF
# generado manualmente.
#
# Parametros:
# - text: Texto que debe escaparse.
#
# Retorno: Texto escapado para su uso dentro del PDF.
# ============================================================================

def pdf_escape(text):
    # Escapa caracteres conflictivos para incrustarlos en el PDF artesanal.
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# ============================================================================
# FUNCION: build_pdf()
# ----------------------------------------------------------------------------
# Construye en memoria un documento PDF basico sin depender de una libreria externa de
# generacion de informes.
#
# Parametros:
# - title: Titulo principal del informe PDF.
# - subtitle: Subtitulo del informe PDF.
# - lines: Lineas de texto que forman el contenido del informe.
#
# Retorno: Objeto BytesIO situado al principio del PDF generado.
# ============================================================================

def build_pdf(title, subtitle, lines):
    # Construye un PDF sencillo sin librerias externas a partir de texto plano.
    pages = []
    current = [title, subtitle, ""]

    # Dividimos manualmente el contenido en paginas para no depender de librerias externas.
    for line in lines:
        if len(current) >= 48:
            pages.append(current)
            current = [title, "Continuacion", ""]
        current.append(line)
    pages.append(current)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    page_refs = []
    for page_lines in pages:
        content_lines = ["BT", "/F1 16 Tf", "50 800 Td", f"({pdf_escape(page_lines[0])}) Tj"]
        content_lines += ["/F1 10 Tf", "0 -22 Td", f"({pdf_escape(page_lines[1])}) Tj"]
        content_lines += ["/F1 9 Tf", "0 -20 Td"]

        # El cuerpo del PDF se construye con texto monoespaciado para facilitar lectura.
        for line in page_lines[2:]:
            content_lines.append(f"({pdf_escape(line[:120])}) Tj")
            content_lines.append("0 -14 Td")

        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        content_obj = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )

        page_obj_id = len(objects) + 1
        content_obj_id = len(objects) + 2
        page_refs.append(f"{page_obj_id} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_obj_id} 0 R >>".encode("latin-1")
        )
        objects.append(content_obj)

    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>"
    ).encode("latin-1")

    # BytesIO actua como un fichero binario en memoria sobre el que se escribe el PDF.
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        # tell() devuelve la posicion actual en bytes. El PDF necesita estos offsets para
        # construir posteriormente la tabla de referencias cruzadas (xref).
        offsets.append(pdf.tell())
        pdf.write(f"{index} 0 obj\n".encode("latin-1"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")

    # Se conserva la posicion donde comienza xref porque el trailer debe apuntar a ella.
    xref_position = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF".encode("latin-1")
    )
    pdf.seek(0)
    return pdf


# ============================================================================
# FUNCION: pdf_response()
# ----------------------------------------------------------------------------
# Envuelve el PDF generado en memoria como una descarga HTTP de Flask.
#
# Parametros:
# - filename: Nombre con el que se entregara el archivo descargado.
# - title: Titulo principal del informe PDF.
# - subtitle: Subtitulo del informe PDF.
# - lines: Lineas de texto que forman el contenido del informe.
#
# Retorno: Respuesta de Flask preparada como descarga PDF.
# ============================================================================

def pdf_response(filename, title, subtitle, lines):
    # Envuelve el PDF generado como respuesta descargable de Flask.
    return send_file(
        build_pdf(title, subtitle, lines),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ============================================================================
# FUNCION: xlsx_response()
# ----------------------------------------------------------------------------
# Genera manualmente un libro XLSX minimo mediante XML OpenXML empaquetado en ZIP.
#
# Parametros:
# - filename: Nombre con el que se entregara el archivo descargado.
# - rows: Coleccion de filas que se desea exportar.
#
# Retorno: Respuesta de Flask preparada como descarga XLSX.
# ============================================================================

def xlsx_response(filename, rows):
    # Genera un archivo XLSX minimo empaquetando el XML directamente.
    headers = [
        "ID_REGISTRO",
        "FECHA_HORA",
        "NODO_ORIGEN",
        "TEMPERATURA_C",
        "HUMEDAD_PORC",
        "RUIDO_DB",
        "PRESION_HPA",
        "LUZ_RAW",
        "ESTADO",
        "MOTIVOS_ALERTA",
    ]
    # Se arma primero una tabla intermedia y luego se serializa a XML OpenXML.
    table = [headers] + [[
        row["id"],
        row["fecha_hora"],
        row["nodo_id"],
        row["temperatura"],
        row["humedad"],
        row["sonido"],
        row["presion"],
        row["luz"],
        row_status(row),
        "; ".join(alert_reasons(row)),
    ] for row in rows]

    # ============================================================================
    # FUNCION: cell_ref()
    # ----------------------------------------------------------------------------
    # Convierte indices numericos de fila y columna en una referencia de celda de Excel, por
    # ejemplo A1 o AB12.
    #
    # Parametros:
    # - row_index: Indice de fila utilizado para crear la referencia Excel.
    # - column_index: Indice numerico de columna, comenzando en cero.
    #
    # Retorno: Referencia de celda Excel en notacion A1.
    # ============================================================================

    def cell_ref(row_index, column_index):
        name = ""
        column_index += 1
        while column_index:
            # divmod() obtiene cociente y resto en base 26 para convertir indices numericos
            # en letras de columna: A..Z, AA..AZ, etc.
            column_index, remainder = divmod(column_index - 1, 26)
            # chr(65) corresponde a "A" en ASCII. El resto determina la letra de la columna.
            name = chr(65 + remainder) + name
        return f"{name}{row_index}"

    sheet_rows = []
    # Cada celda se etiqueta con su referencia Excel (A1, B1, C1...).
    for row_index, row in enumerate(table, start=1):
        cells = []
        for column_index, value in enumerate(row):
            ref = cell_ref(row_index, column_index)
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(str(value))}</t></is></c>'
                )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Telemetria" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    output = BytesIO()
    # XLSX es realmente un conjunto de documentos XML empaquetados en un ZIP.
    # ZIP_DEFLATED aplica compresion al contenido generado.
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", rels)
        xlsx.writestr("xl/workbook.xml", workbook)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        xlsx.writestr("xl/worksheets/sheet1.xml", worksheet)

    # seek(0) reposiciona el cursor al inicio antes de entregar el objeto a Flask.
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
# ============================================================================
# FUNCION: index()
# ----------------------------------------------------------------------------
# Sirve la pagina principal correspondiente al dashboard vivo.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/")
# ============================================================================

@app.route("/")
def index():
    # Sirve la pagina de entrada del dashboard vivo.
    return send_from_directory(".", "index.html")


# ============================================================================
# API REST DE VISUALIZACION, CONFIGURACION Y REPORTES
# ----------------------------------------------------------------------------
# Los endpoints siguientes proporcionan los datos consumidos por las distintas
# vistas HTML: dashboard, historico, configuracion, detalle de nodos, alertas,
# diagnostico y centro de reportes.
# ============================================================================

# ============================================================================
# FUNCION: get_datos_recientes()
# ----------------------------------------------------------------------------
# Devuelve la ultima lectura reciente de cada nodo activo y la enriquece con metadatos y
# configuracion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/datos_recientes", methods=["GET"])
# ============================================================================

@app.route("/api/datos_recientes", methods=["GET"])
def get_datos_recientes():
    # Devuelve la ultima lectura reciente de cada nodo activo para el dashboard.
    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        # Partimos de la tabla de nodos para que todos los nodos activos aparezcan
        # aunque todavía no tengan una lectura reciente. Esto es especialmente
        # importante para FISICO_ARDUINO: desconectado debe verse con guiones, no
        # desaparecer por completo del dashboard.
        query = """
            SELECT
                h.id,
                n.nodo_id,
                h.temperatura,
                h.humedad,
                h.sonido,
                h.presion,
                h.luz,
                h.fecha_hora
            FROM nodes n
            LEFT JOIN (
                SELECT lectura.*
                FROM historial_telemetria lectura
                INNER JOIN (
                    SELECT nodo_id, MAX(id) AS max_id
                    FROM historial_telemetria
                    WHERE fecha_hora >= datetime('now', '-2 minutes')
                    GROUP BY nodo_id
                ) ultimos ON lectura.id = ultimos.max_id
            ) h ON h.nodo_id = n.nodo_id
            WHERE n.activo = 1
            ORDER BY n.nodo_id
        """
        cursor.execute(query)
        rows = []
        # Enriquecemos cada lectura con origen, tipo y configuracion del nodo.
        for row in cursor.fetchall():
            # sqlite3.Row se transforma a dict para poder serializarlo mediante jsonify().
            item = dict(row)
            config = node_config(connection, item["nodo_id"])
            # update() incorpora al mismo objeto los umbrales configurados del nodo.
            item.update(config)
            node = connection.execute(
                "SELECT origen, tipo, nombre FROM nodes WHERE nodo_id = ?",
                (item["nodo_id"],),
            ).fetchone()
            if node:
                item["origen_nodo"] = node["origen"]
                item["tipo_nodo"] = node["tipo"]
                item["nombre_nodo"] = node["nombre"]
            else:
                item["origen_nodo"] = "DESCONOCIDO"
            rows.append(item)
        return jsonify(rows)
    except Exception as error:
        print(f"Error en API Datos Recientes: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_historial()
# ----------------------------------------------------------------------------
# Expone el historico de telemetria con filtros opcionales, modo solo alertas y paginacion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/historial", methods=["GET"])
# ============================================================================

@app.route("/api/historial", methods=["GET"])
def get_historial():
    # Expone el historico filtrable y paginado que usa la vista de telemetria.
    try:
        node_id = (request.args.get("nodo_id") or "").strip()
        # min(max(...)) acota el parametro recibido entre 1 y 10000 para evitar consultas
        # sin limite razonable.
        limit = min(max(int(request.args.get("limit", 5000)), 1), 10000)
        only_alerts = (request.args.get("only_alerts") or "").lower() in ("1", "true", "si", "yes")
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        page = request.args.get("page")
        page_size = request.args.get("page_size")

        # Si la vista pide paginacion, respondemos con metadatos de navegacion.
        if page or page_size:
            current_page = max(int(page or 1), 1)
            current_page_size = min(max(int(page_size or 200), 25), 500)
            # OFFSET indica cuantas filas deben omitirse antes de devolver la pagina actual.
            offset = (current_page - 1) * current_page_size

            where_sql, params = build_date_filter(start=start, end=end)
            if node_id:
                where_sql += " AND nodo_id = ?" if where_sql else "WHERE nodo_id = ?"
                params.append(node_id)
            if only_alerts:
                alert_sql = (
                    "(temperatura > ? OR humedad > ? OR humedad < ? OR sonido > ?)"
                )
                where_sql += f" AND {alert_sql}" if where_sql else f"WHERE {alert_sql}"
                params.extend([
                    LIMITES["temp"],
                    LIMITES["hum"],
                    LIMITES["min_hum"],
                    LIMITES["sonido"],
                ])

            # Se calculan total y pagina actual por separado para que la UI pueda paginar.
            count_query = f"SELECT COUNT(*) AS total FROM historial_telemetria {where_sql}"
            data_query = f"""
                SELECT *
                FROM historial_telemetria
                {where_sql}
                ORDER BY fecha_hora DESC
                LIMIT ? OFFSET ?
            """

            with get_connection() as connection:
                total = connection.execute(count_query, params).fetchone()["total"]
                rows = [
                    dict(row)
                    for row in connection.execute(
                        data_query,
                        params + [current_page_size, offset],
                    ).fetchall()
                ]

            # Esta formula realiza una division entera redondeada hacia arriba para calcular
            # el numero total de paginas.
            total_pages = max((total + current_page_size - 1) // current_page_size, 1)
            return jsonify({
                "items": rows,
                "page": current_page,
                "page_size": current_page_size,
                "total": total,
                "total_pages": total_pages,
                "has_prev": current_page > 1,
                "has_next": current_page < total_pages,
            })

        # Si no hay paginacion, devolvemos una lista simple para compatibilidad.
        rows = fetch_measurements(
            start=start,
            end=end,
            only_alerts=only_alerts,
            limit=limit,
            node_id=node_id or None,
        )
        return jsonify(rows)
    except Exception as error:
        print(f"Error en API Historial: {error}")
        return jsonify({"error": str(error)}), 500


# ============================================================================
# FUNCION: create_telemetry()
# ----------------------------------------------------------------------------
# Recibe telemetria externa por JSON, valida los campos numericos y persiste la lectura junto
# con sus alertas.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/telemetria", methods=["POST"])
# ============================================================================

@app.route("/api/telemetria", methods=["POST"])
def create_telemetry():
    # Permite insertar telemetria desde clientes externos o pruebas manuales.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    if not INGESTION_TOKEN:
        return jsonify({"error": "La ingesta remota no esta configurada"}), 503
    supplied_token = request.headers.get("X-Ingestion-Token", "")
    if not hmac.compare_digest(supplied_token, INGESTION_TOKEN):
        return jsonify({"error": "Token de ingesta no valido"}), 401

    payload = request.get_json(silent=True) or {}
    required = ["temperatura", "humedad", "sonido", "presion"]

    try:
        for field in required:
            if field not in payload:
                return jsonify({"error": f"Falta el campo {field}"}), 400

        # Esta ruta es clave para recibir lecturas externas, por ejemplo desde el aula.
        data = {
            "nodo_id": str(payload.get("nodo_id") or "FISICO_ARDUINO"),
            "temperatura": float(payload["temperatura"]),
            "humedad": float(payload["humedad"]),
            "sonido": float(payload["sonido"]),
            "presion": float(payload["presion"]),
            "luz": float(payload.get("luz", 0)),
        }
        measurement_id = insert_measurement(data)
        return jsonify({"saved": True, "id": measurement_id, "data": data}), 201
    except ValueError:
        return jsonify({"error": "Los valores de telemetria deben ser numericos"}), 400
    except Exception as error:
        print(f"Error guardando Telemetria: {error}")
        return jsonify({"error": str(error)}), 500


# ============================================================================
# FUNCION: get_arduino_status()
# ----------------------------------------------------------------------------
# Publica una copia del estado actual del lector Arduino protegida mediante el bloqueo
# compartido.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/arduino/status", methods=["GET"])
# ============================================================================

@app.route("/api/arduino/status", methods=["GET"])
def get_arduino_status():
    # Publica el estado del lector serie local cuando existe. En Render no hay
    # acceso al USB, por lo que la conexión se deduce de la última lectura física
    # recibida por /api/telemetria desde la pasarela del ordenador.
    with ARDUINO_STATUS_LOCK:
        status = dict(ARDUINO_STATUS)

    # El lector serie integrado tiene prioridad cuando la aplicación se ejecuta
    # localmente y mantiene el puerto abierto directamente.
    if status.get("connected"):
        return jsonify(status)

    try:
        with get_connection() as connection:
            latest = connection.execute(
                """
                SELECT
                    temperatura,
                    humedad,
                    sonido,
                    presion,
                    luz,
                    fecha_hora,
                    CAST(
                        (julianday('now') - julianday(fecha_hora)) * 86400
                        AS INTEGER
                    ) AS age_seconds
                FROM historial_telemetria
                WHERE nodo_id = 'FISICO_ARDUINO'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        # Veinte segundos permiten varios ciclos de muestreo sin mostrar falsos
        # cortes. Si la pasarela se cierra, el estado vuelve solo a OFF.
        if latest and latest["age_seconds"] is not None and latest["age_seconds"] <= 20:
            status.update({
                "connected": True,
                "port": "API",
                "last_error": None,
                "last_saved": {
                    "temperatura": latest["temperatura"],
                    "humedad": latest["humedad"],
                    "sonido": latest["sonido"],
                    "presion": latest["presion"],
                    "luz": latest["luz"],
                    "fecha_hora": latest["fecha_hora"],
                },
            })
        else:
            status.update({
                "connected": False,
                "port": None,
                "last_error": "No se reciben lecturas recientes del Arduino.",
            })
    except Exception as error:
        status.update({
            "connected": False,
            "port": None,
            "last_error": str(error),
        })

    return jsonify(status)


# ============================================================================
# FUNCION: get_system_status()
# ----------------------------------------------------------------------------
# Construye un resumen de diagnostico con estado de base de datos, simulador, Arduino, nodos
# y almacenamiento.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/sistema/estado", methods=["GET"])
# ============================================================================

@app.route("/api/sistema/estado", methods=["GET"])
def get_system_status():
    # Resume la salud global del sistema para diagnostico y paneles de control.
    connection = None
    try:
        connection = get_connection()
        # Este resumen alimenta el panel de diagnostico del sistema.
        active_nodes = connection.execute(
            "SELECT origen, COUNT(*) AS total FROM nodes WHERE activo = 1 GROUP BY origen"
        ).fetchall()
        recent_nodes = connection.execute(
            """
            SELECT COUNT(DISTINCT nodo_id) AS total
            FROM historial_telemetria
            WHERE fecha_hora >= datetime('now', '-2 minutes')
            """
        ).fetchone()
        measurements = connection.execute(
            "SELECT COUNT(*) AS total, MAX(fecha_hora) AS last_date FROM historial_telemetria"
        ).fetchone()
        alerts = connection.execute(
            "SELECT COUNT(*) AS total FROM alerts"
        ).fetchone()

        counters = {row["origen"]: row["total"] for row in active_nodes}
        with ARDUINO_STATUS_LOCK:
            arduino = dict(ARDUINO_STATUS)

        return jsonify({
            "database": {
                "active": True,
                "path": DB_PATH,
                "size_bytes": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
            },
            "simulator": {
                "active": True,
                "nodes_configured": len(SIM_NODES),
                "interval_seconds": 5,
            },
            "arduino": arduino,
            "nodes": {
                "active_total": sum(counters.values()),
                "active_recent": recent_nodes["total"] if recent_nodes else 0,
                "simulated": counters.get("SIMULADO", 0),
                "physical": counters.get("FISICO", 0),
            },
            "storage": {
                "measurements": measurements["total"] if measurements else 0,
                "last_measurement": measurements["last_date"] if measurements else None,
                "alerts": alerts["total"] if alerts else 0,
            },
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as error:
        return jsonify({"error": str(error), "database": {"active": False}}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_change_log()
# ----------------------------------------------------------------------------
# Devuelve los registros de auditoria mas recientes ordenados de forma descendente.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/log-cambios", methods=["GET"])
# ============================================================================

@app.route("/api/log-cambios", methods=["GET"])
def get_change_log():
    # Devuelve el log reciente de cambios de configuracion para auditoria.
    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM audit_log ORDER BY fecha_hora DESC, id DESC LIMIT 200")
        return jsonify([dict(row) for row in cursor.fetchall()])
    except Exception as error:
        print(f"Error en API Log Cambios: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_configuration()
# ----------------------------------------------------------------------------
# Devuelve al frontend la configuracion global efectiva del sistema.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/configuracion", methods=["GET"])
# ============================================================================

@app.route("/api/configuracion", methods=["GET"])
def get_configuration():
    # Entrega la configuracion general persistida del sistema.
    connection = None
    try:
        connection = get_connection()
        return jsonify(current_settings(connection))
    except Exception as error:
        print(f"Error en API Configuracion: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: save_configuration()
# ----------------------------------------------------------------------------
# Persiste cambios autorizados en la configuracion global y crea una entrada de auditoria por
# cada modificacion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/configuracion", methods=["POST"])
# ============================================================================

@app.route("/api/configuracion", methods=["POST"])
def save_configuration():
    # Guarda parametros globales y registra cada cambio en la auditoria.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    operario = current_actor_name("OPERADOR_LOCAL")[:40]
    origen = "Interfaz Web"
    allowed = {
        "umbral_temp_max": "Umbral_Temp_Max",
        "umbral_humedad_max": "Umbral_Humedad_Max",
        "intervalo_muestreo": "Intervalo_Muestreo",
        "sensibilidad_luz": "Sensibilidad_Luz",
    }

    connection = None
    try:
        connection = get_connection()
        before = current_settings(connection)
        changes = []

        # Solo se aceptan parametros previamente autorizados para evitar escritura arbitraria.
        for parametro, audit_name in allowed.items():
            if parametro not in payload:
                continue

            new_value = str(payload[parametro]).strip()
            old_value = str(before.get(parametro, DEFAULT_SETTINGS[parametro]))

            if new_value == "" or new_value == old_value:
                continue

            connection.execute(
                """
                INSERT INTO system_settings (parametro, valor, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(parametro) DO UPDATE SET
                    valor = excluded.valor,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (parametro, new_value),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                (operario, parametro, valor_anterior, valor_nuevo, origen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (operario, audit_name, old_value, new_value, origen),
            )
            changes.append({
                "parametro": audit_name,
                "valor_anterior": old_value,
                "valor_nuevo": new_value,
            })

        connection.commit()
        return jsonify({"saved": True, "changes": changes, "settings": current_settings(connection)})
    except Exception as error:
        print(f"Error guardando Configuracion: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_nodes()
# ----------------------------------------------------------------------------
# Lista todos los nodos junto con sus metadatos y configuracion efectiva.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/nodos", methods=["GET"])
# ============================================================================

@app.route("/api/nodos", methods=["GET"])
def get_nodes():
    # Lista todos los nodos disponibles junto con su configuracion resumida.
    connection = None
    try:
        connection = get_connection()
        rows = connection.execute(
            """
            SELECT nodo_id, nombre, tipo, origen, activo, created_at
            FROM nodes
            ORDER BY origen, nodo_id
            """
        ).fetchall()

        result = []
        # Cada nodo se devuelve junto a su configuracion resumida para simplificar la UI.
        for row in rows:
            item = dict(row)
            item["config"] = node_config(connection, item["nodo_id"])
            result.append(item)

        return jsonify(result)
    except Exception as error:
        print(f"Error listando Nodos: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: create_node()
# ----------------------------------------------------------------------------
# Crea un nuevo nodo simulado, normaliza su identificador, inicializa sus umbrales y registra
# la operacion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/nodos", methods=["POST"])
# ============================================================================

@app.route("/api/nodos", methods=["POST"])
def create_node():
    # Crea un nuevo nodo simulado desde la interfaz de administracion.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    raw_id = str(payload.get("nodo_id") or "").strip().upper()
    # re.sub() reemplaza caracteres no permitidos por "_" para obtener un identificador
    # compatible con la convencion interna de nodos.
    node_id = re.sub(r"[^A-Z0-9_]", "_", raw_id)

    if not node_id:
        return jsonify({"error": "El nodo_id es obligatorio"}), 400
    # Los nodos creados desde interfaz siempre se normalizan al namespace SIM_*.
    if not node_id.startswith("SIM_"):
        node_id = f"SIM_{node_id}"

    nombre = str(payload.get("nombre") or node_id.replace("SIM_", "").replace("_", " ").title()).strip()
    tipo = str(payload.get("tipo") or "Simulado").strip()
    operario = current_actor_name("OPERADOR_LOCAL")[:40]

    connection = None
    try:
        connection = get_connection()
        connection.execute(
            """
            INSERT INTO nodes (nodo_id, nombre, tipo, origen, activo)
            VALUES (?, ?, ?, 'SIMULADO', 1)
            """,
            (node_id, nombre, tipo),
        )
        for parametro, valor in DEFAULT_NODE_CONFIG.items():
            connection.execute(
                """
                INSERT INTO node_settings (nodo_id, parametro, valor)
                VALUES (?, ?, ?)
                """,
                (node_id, parametro, valor),
            )
        connection.execute(
            """
            INSERT INTO audit_log (operario, parametro, valor_anterior, valor_nuevo, origen)
            VALUES (?, ?, '-', 'CREADO', 'Interfaz Web')
            """,
            (operario, f"{node_id}.Nodo"),
        )
        connection.commit()
        return jsonify({"created": True, "nodo_id": node_id}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ya existe un nodo con ese identificador"}), 409
    except Exception as error:
        print(f"Error creando Nodo: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: save_node_config()
# ----------------------------------------------------------------------------
# Actualiza metadatos y umbrales de un nodo concreto, registrando cada cambio en el log de
# auditoria.
#
# Parametros:
# - node_id: Identificador unico del nodo.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/nodos/<path:node_id>/config", methods=["POST"])
# ============================================================================

@app.route("/api/nodos/<path:node_id>/config", methods=["POST"])
def save_node_config(node_id):
    # Persiste cambios de metadatos y umbrales para un nodo concreto.
    # silent=True evita que un JSON inexistente o mal formado genere automaticamente
    # una excepcion HTTP; en ese caso se trabaja con un diccionario vacio.
    payload = request.get_json(silent=True) or {}
    operario = current_actor_name("OPERADOR_LOCAL")[:40]
    allowed = {
        "umbral_temp_max": "Temp_Max",
        "umbral_humedad_max": "Hum_Max",
        "umbral_humedad_min": "Hum_Min",
        "umbral_sonido_max": "Sonido_Max",
    }

    connection = None
    try:
        connection = get_connection()
        exists = connection.execute(
            "SELECT nodo_id, nombre, tipo, origen, activo FROM nodes WHERE nodo_id = ?",
            (node_id,),
        ).fetchone()
        if not exists:
            return jsonify({"error": "El nodo no existe"}), 404

        before = node_config(connection, node_id)
        changes = []

        # Distinguimos entre metadatos del nodo y umbrales tecnicos configurables.
        metadata_fields = {
            "nombre": "Nombre",
            "tipo": "Tipo",
            "origen": "Origen",
            "activo": "Activo",
        }

        # Primero procesamos cambios sobre nombre, tipo, origen y estado.
        for field, audit_name in metadata_fields.items():
            if field not in payload:
                continue

            if field == "activo":
                new_value = "1" if str(payload[field]).lower() in ("1", "true", "si", "sí", "on") else "0"
                old_value = str(exists[field])
                db_value = int(new_value)
            elif field == "origen":
                new_value = str(payload[field]).strip().upper()
                if new_value not in ("SIMULADO", "FISICO"):
                    return jsonify({"error": "El origen debe ser SIMULADO o FISICO"}), 400
                old_value = str(exists[field])
                db_value = new_value
            else:
                new_value = str(payload[field]).strip()
                if not new_value:
                    continue
                old_value = str(exists[field])
                db_value = new_value

            if new_value == old_value:
                continue

            connection.execute(
                f"UPDATE nodes SET {field} = ? WHERE nodo_id = ?",
                (db_value, node_id),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                (operario, parametro, valor_anterior, valor_nuevo, origen)
                VALUES (?, ?, ?, ?, 'Interfaz Web')
                """,
                (operario, f"{node_id}.{audit_name}", old_value, new_value),
            )
            changes.append({
                "parametro": field,
                "valor_anterior": old_value,
                "valor_nuevo": new_value,
            })

        # Despues persistimos los umbrales propios del nodo.
        for parametro, audit_name in allowed.items():
            if parametro not in payload:
                continue
            new_value = str(payload[parametro]).strip()
            old_value = str(before.get(parametro, DEFAULT_NODE_CONFIG[parametro]))
            if new_value == "" or new_value == old_value:
                continue

            connection.execute(
                """
                INSERT INTO node_settings (nodo_id, parametro, valor, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(nodo_id, parametro) DO UPDATE SET
                    valor = excluded.valor,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (node_id, parametro, new_value),
            )
            connection.execute(
                """
                INSERT INTO audit_log
                (operario, parametro, valor_anterior, valor_nuevo, origen)
                VALUES (?, ?, ?, ?, 'Interfaz Web')
                """,
                (operario, f"{node_id}.{audit_name}", old_value, new_value),
            )
            changes.append({
                "parametro": parametro,
                "valor_anterior": old_value,
                "valor_nuevo": new_value,
            })

        connection.commit()
        return jsonify({"saved": True, "changes": changes, "config": node_config(connection, node_id)})
    except Exception as error:
        print(f"Error guardando Configuracion de Nodo: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_node_detail()
# ----------------------------------------------------------------------------
# Devuelve la ficha completa de un nodo con metadatos, configuracion, mediciones recientes y
# alertas.
#
# Parametros:
# - node_id: Identificador unico del nodo.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/nodos/<path:node_id>/detalle", methods=["GET"])
# ============================================================================

@app.route("/api/nodos/<path:node_id>/detalle", methods=["GET"])
def get_node_detail(node_id):
    # Devuelve la ficha completa de un nodo con medidas, alertas y configuracion.
    connection = None
    try:
        connection = get_connection()
        node = connection.execute(
            "SELECT nodo_id, nombre, tipo, origen, activo, created_at FROM nodes WHERE nodo_id = ?",
            (node_id,),
        ).fetchone()
        if not node:
            return jsonify({"error": "El nodo no existe"}), 404

        # La ficha de detalle carga muestras recientes para graficas y resumenes.
        measurements = connection.execute(
            """
            SELECT *
            FROM historial_telemetria
            WHERE nodo_id = ?
            ORDER BY fecha_hora DESC
            LIMIT 120
            """,
            (node_id,),
        ).fetchall()
        alerts = connection.execute(
            """
            SELECT *
            FROM alerts
            WHERE nodo_id = ?
            ORDER BY fecha_hora DESC
            LIMIT 50
            """,
            (node_id,),
        ).fetchall()

        return jsonify({
            "node": dict(node),
            "config": node_config(connection, node_id),
            "measurements": [dict(row) for row in measurements],
            "alerts": [dict(row) for row in alerts],
        })
    except Exception as error:
        print(f"Error obteniendo Detalle de Nodo: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: get_persistent_alerts()
# ----------------------------------------------------------------------------
# Lista las alertas persistidas mas recientes con un limite controlado por la peticion.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/alertas", methods=["GET"])
# ============================================================================

@app.route("/api/alertas", methods=["GET"])
def get_persistent_alerts():
    # Lista alertas recientes para notificaciones y vistas de seguimiento.
    limit = min(int(request.args.get("limit", 100)), 500)
    connection = None
    try:
        connection = get_connection()
        # Se listan las alertas mas recientes en orden descendente para notificaciones.
        rows = connection.execute(
            """
            SELECT *
            FROM alerts
            ORDER BY fecha_hora DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    except Exception as error:
        print(f"Error listando Alertas: {error}")
        return jsonify({"error": str(error)}), 500
    finally:
        if connection:
            connection.close()


# ============================================================================
# FUNCION: export_telemetry_csv()
# ----------------------------------------------------------------------------
# Genera una descarga CSV con la telemetria filtrada solicitada desde el centro de reportes.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/reportes/telemetria.csv")
# ============================================================================

@app.route("/api/reportes/telemetria.csv")
def export_telemetry_csv():
    # Exporta telemetria historica filtrada en formato CSV.
    rows = fetch_measurements(
        start=request.args.get("start"),
        end=request.args.get("end"),
        node_id=request.args.get("node_id") or None,
    )
    filename = f"SMI_Telemetria_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return csv_response(filename, rows)


# ============================================================================
# FUNCION: export_telemetry_pdf()
# ----------------------------------------------------------------------------
# Genera un informe PDF resumido con telemetria historica y contexto del filtro aplicado.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/reportes/telemetria.pdf")
# ============================================================================

@app.route("/api/reportes/telemetria.pdf")
def export_telemetry_pdf():
    # Exporta un informe PDF resumido de telemetria historica.
    rows = fetch_measurements(
        start=request.args.get("start"),
        end=request.args.get("end"),
        node_id=request.args.get("node_id") or None,
    )
    node_label = request.args.get("node_id") or "todos"
    lines = [
        f"Registros exportados: {len(rows)}",
        f"Nodo: {node_label}",
        f"Rango desde: {request.args.get('start') or 'inicio'}",
        f"Rango hasta: {request.args.get('end') or 'actualidad'}",
        "",
        "ID | FECHA | NODO | TEMP C | HUM % | RUIDO | PRES hPa | LUZ | ESTADO",
    ]

    # Limitamos el PDF para mantenerlo ligero; el CSV sirve para volcado completo.
    for row in rows[:220]:
        lines.append(
            f"{row['id']} | {row['fecha_hora']} | {row['nodo_id']} | "
            f"{row['temperatura']} | {row['humedad']} | {row['sonido']} | "
            f"{row['presion']} | {row['luz']} | {row_status(row)}"
        )

    if len(rows) > 220:
        lines.append("")
        lines.append("Nota: el PDF muestra los primeros 220 registros. Usa CSV/XLSX para el volcado completo.")

    filename = f"SMI_Telemetria_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return pdf_response(
        filename,
        "S.M.I. CORE - Informe de Telemetria",
        "Lecturas historicas de sensores industriales",
        lines,
    )


# ============================================================================
# FUNCION: export_alerts_csv()
# ----------------------------------------------------------------------------
# Exporta a CSV las mediciones que incumplen los limites operativos dentro del periodo
# solicitado.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/reportes/alertas.csv")
# ============================================================================

@app.route("/api/reportes/alertas.csv")
def export_alerts_csv():
    # Exporta solo las mediciones que han activado alertas en formato CSV.
    rows = fetch_measurements(
        period=request.args.get("period", "24h"),
        only_alerts=True,
        node_id=request.args.get("node_id") or None,
    )
    filename = f"SMI_Alertas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return csv_response(filename, rows)


# ============================================================================
# FUNCION: export_alerts_pdf()
# ----------------------------------------------------------------------------
# Genera un PDF con resumen por tipo de incidencia y detalle de las alertas detectadas.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/reportes/alertas.pdf")
# ============================================================================

@app.route("/api/reportes/alertas.pdf")
def export_alerts_pdf():
    # Exporta un PDF con resumen y detalle de incidencias por umbral.
    period = request.args.get("period", "24h")
    node_label = request.args.get("node_id") or "todos"
    rows = fetch_measurements(
        period=period,
        only_alerts=True,
        node_id=request.args.get("node_id") or None,
    )
    counters = {
        "Temperatura alta": 0,
        "Humedad alta": 0,
        "Humedad baja": 0,
        "Ruido alto": 0,
    }

    # Primero agregamos por tipo de alerta para el resumen ejecutivo.
    for row in rows:
        for reason in alert_reasons(row):
            counters[reason] += 1

    lines = [
        f"Periodo analizado: {period}",
        f"Nodo: {node_label}",
        f"Incidencias detectadas: {len(rows)}",
        "",
        "Resumen por tipo:",
    ]
    lines.extend([f"- {name}: {count}" for name, count in counters.items()])
    lines.extend(["", "Detalle:", "ID | FECHA | NODO | MOTIVOS | VALORES"])

    for row in rows[:220]:
        lines.append(
            f"{row['id']} | {row['fecha_hora']} | {row['nodo_id']} | "
            f"{'; '.join(alert_reasons(row))} | "
            f"T={row['temperatura']} H={row['humedad']} S={row['sonido']} P={row['presion']} L={row['luz']}"
        )

    if len(rows) > 220:
        lines.append("")
        lines.append("Nota: el PDF muestra las primeras 220 alertas. Usa CSV para el detalle completo.")

    filename = f"SMI_Alertas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return pdf_response(
        filename,
        "S.M.I. CORE - Resumen Critico de Alertas",
        "Incidencias calculadas a partir de los umbrales operativos",
        lines,
    )


# ============================================================================
# FUNCION: export_history_xlsx()
# ----------------------------------------------------------------------------
# Genera un libro XLSX descargable con el historico solicitado.
#
# Parametros: ninguno.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/api/reportes/historial.xlsx")
# ============================================================================

@app.route("/api/reportes/historial.xlsx")
def export_history_xlsx():
    # Exporta el historico solicitado en un libro Excel descargable.
    rows = fetch_measurements(node_id=request.args.get("node_id") or None)
    filename = f"SMI_Historial_Completo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return xlsx_response(filename, rows)


# ============================================================================
# FUNCION: static_pages()
# ----------------------------------------------------------------------------
# Sirve archivos estaticos restantes del proyecto mediante una ruta generica.
#
# Parametros:
# - filename: Nombre con el que se entregara el archivo descargado.
#
# Retorno: Respuesta HTTP de Flask: JSON, HTML, redireccion o archivo segun el endpoint.
#
# Decoradores:
# - app.route("/<path:filename>")
# ============================================================================

@app.route("/<path:filename>")
def static_pages(filename):
    # Sirve el resto de paginas estaticas HTML/CSS/JS del proyecto.
    return send_from_directory(".", filename)


# ============================================================================
# FUNCION: boot_application()
# ----------------------------------------------------------------------------
# Inicializa una sola vez la base de datos y las fuentes de telemetria en segundo plano.
#
# Parametros: ninguno.
#
# Retorno: No devuelve valor; garantiza una inicializacion unica.
# ============================================================================

def boot_application():
    # Inicializa la base de datos y las fuentes en segundo plano al importar la app.
    # getattr() consulta una marca almacenada sobre la propia funcion y devuelve False
    # si todavia no existe.
    if getattr(boot_application, "_booted", False):
        return
    # Esto permite que tambien al ejecutar con gunicorn la app arranque "viva".
    init_db()
    start_background_sources()
    # La funcion se utiliza como objeto para guardar una bandera simple de inicializacion.
    boot_application._booted = True


boot_application()


# Este bloque se ejecuta solo al lanzar el fichero directamente con Python.
# Cuando Flask/Gunicorn importa el modulo, boot_application() ya ha preparado los
# recursos pero app.run() no se inicia manualmente.
if __name__ == "__main__":
    # Plataformas de despliegue suelen proporcionar el puerto mediante PORT.
    port = int(os.environ.get("PORT", "5000"))
    print(f"Servidor S.M.I. CORE activo en http://localhost:{port}")
    # host="0.0.0.0" permite aceptar conexiones externas al equipo.
    # use_reloader=False evita que Flask duplique los hilos de simulacion/Arduino.
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False)
