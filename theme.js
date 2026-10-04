/*
 * ============================================================================
 * ARCHIVO: theme.js
 * LOGICA GLOBAL COMPARTIDA DE S.M.I. CORE
 * ============================================================================
 *
 * Este archivo centraliza comportamientos reutilizables que deben estar
 * disponibles en distintas vistas de la aplicacion.
 *
 * Sus responsabilidades principales son:
 *
 * 1. Gestionar el tema claro y oscuro.
 * 2. Persistir la preferencia visual del usuario mediante localStorage.
 * 3. Crear dinamicamente el boton de cambio de tema.
 * 4. Insertar enlaces comunes del menu lateral:
 *    - Mi cuenta.
 *    - Gestion de Permisos para administradores.
 *    - Cerrar sesion.
 * 5. Crear y gestionar el modal global de confirmacion de cierre de sesion.
 * 6. Consultar el perfil autenticado mediante /api/auth/me.
 * 7. Adaptar la visibilidad del menu en funcion del rol.
 * 8. Preparar el sidebar movil plegable.
 * 9. Mantener atributos ARIA coherentes para mejorar accesibilidad.
 * 10. Coordinar toda la inicializacion cuando el DOM se encuentra disponible.
 *
 * Este archivo funciona conjuntamente con theme.css. JavaScript controla el
 * estado y las clases dinamicas, mientras que CSS define su representacion visual.
 *
 * FLUJO GENERAL DE INICIALIZACION
 * --------------------------------
 * 1. Se espera al evento DOMContentLoaded.
 * 2. Se inserta el selector de tema.
 * 3. Se inserta el acceso a "Mi cuenta".
 * 4. Se inserta el enlace de cierre de sesion.
 * 5. Se crea el modal de confirmacion de logout.
 * 6. Se interceptan los enlaces de cierre de sesion.
 * 7. Se prepara el menu movil.
 * 8. Se aplican los listeners del ciclo de vida responsive.
 * 9. Se aplica el tema almacenado.
 * 10. Se consulta el perfil autenticado.
 * 11. Se adapta la navegacion segun el rol.
 * 12. Se marca el sidebar como completamente inicializado.
 * ============================================================================
 */


/*
 * Clave utilizada para almacenar la preferencia de tema en localStorage.
 *
 * Centralizar la clave evita repetir el literal "smi-theme" y facilita futuras
 * modificaciones.
 */
const THEME_STORAGE_KEY = "smi-theme";


/**
 * Marca el menu lateral como completamente inicializado.
 *
 * theme.css utiliza la clase sidebar-ready para evitar que determinadas zonas
 * dinamicas del menu aparezcan antes de que JavaScript termine de prepararlas.
 *
 * @returns {void}
 */
function markSidebarReady() {
    document.documentElement.classList.add(
        "sidebar-ready"
    );
}


/**
 * Obtiene el tema actualmente almacenado.
 *
 * localStorage permite mantener la preferencia entre recargas del navegador.
 * Cuando todavia no existe ningun valor guardado se utiliza "dark".
 *
 * @returns {string} "dark" o "light".
 */
function currentTheme() {
    return (
        localStorage.getItem(
            THEME_STORAGE_KEY
        )
        || "dark"
    );
}


/**
 * Alterna entre el tema oscuro y el claro.
 *
 * La nueva seleccion se guarda primero en localStorage y posteriormente se aplica
 * sobre el documento.
 *
 * @returns {void}
 */
function toggleTheme() {
    /*
     * El operador ternario selecciona el tema contrario al actual.
     */
    const next =
        currentTheme() === "dark"
            ? "light"
            : "dark";

    /*
     * Se persiste la nueva preferencia.
     */
    localStorage.setItem(
        THEME_STORAGE_KEY,
        next
    );

    /*
     * Finalmente se actualiza la interfaz.
     */
    applyTheme(next);
}


/**
 * Aplica visualmente un tema al documento.
 *
 * El atributo data-theme es utilizado por theme.css para activar las reglas
 * correspondientes al modo claro u oscuro.
 *
 * Tambien se actualizan el texto y aria-label de todos los botones de cambio de
 * tema existentes.
 *
 * @param {string} theme Tema que debe aplicarse.
 * @returns {void}
 */
function applyTheme(theme) {
    /*
     * El atributo se establece sobre <html>, representado mediante
     * document.documentElement.
     */
    document.documentElement.setAttribute(
        "data-theme",
        theme
    );

    /*
     * Pueden existir varios botones de tema en distintas posiciones de la interfaz.
     */
    document
        .querySelectorAll(".theme-toggle")
        .forEach((button) => {
            /*
             * El texto indica la accion disponible, no el tema actualmente activo.
             */
            button.textContent =
                theme === "dark"
                    ? "Modo claro"
                    : "Modo oscuro";

            /*
             * aria-label proporciona una descripcion accesible equivalente.
             */
            button.setAttribute(
                "aria-label",
                theme === "dark"
                    ? "Cambiar a modo claro"
                    : "Cambiar a modo oscuro"
            );
        });
}


/**
 * Crea un boton reutilizable para alternar el tema.
 *
 * El parametro floating permite generar una variante flotante cuando la vista no
 * dispone de un lugar adecuado dentro del sidebar.
 *
 * @param {boolean} floating Indica si debe utilizarse la variante flotante.
 * @returns {HTMLButtonElement} Boton creado.
 */
function createThemeToggle(
    floating = false
) {
    const button =
        document.createElement(
            "button"
        );

    button.type =
        "button";

    /*
     * La clase adicional theme-toggle-floating solo se incorpora cuando procede.
     */
    button.className =
        floating
            ? "theme-toggle theme-toggle-floating"
            : "theme-toggle";

    /*
     * El boton reutiliza la funcion global toggleTheme().
     */
    button.addEventListener(
        "click",
        toggleTheme
    );

    return button;
}


/**
 * Inserta el selector de tema en la vista actual.
 *
 * Si ya existe un control no se crea otro. Cuando existe .logo-box se inserta
 * dentro del sidebar; en caso contrario se utiliza la variante flotante.
 *
 * @returns {void}
 */
function injectThemeToggle() {
    /*
     * Evita duplicar el componente si ya se ha creado.
     */
    if (
        document.querySelector(
            ".theme-toggle"
        )
    ) {
        return;
    }

    const logoBox =
        document.querySelector(
            ".logo-box"
        );

    if (logoBox) {
        /*
         * Se crea una envoltura para poder aplicar estilos de separacion.
         */
        const wrap =
            document.createElement(
                "div"
            );

        wrap.className =
            "theme-toggle-wrap";

        wrap.appendChild(
            createThemeToggle(false)
        );

        logoBox.appendChild(
            wrap
        );

    } else {
        /*
         * Las vistas sin logo/sidebar reciben un boton flotante.
         */
        document.body.appendChild(
            createThemeToggle(true)
        );
    }
}


/**
 * Inserta el enlace global de cierre de sesion.
 *
 * No se incorpora:
 * - Si ya existe un enlace equivalente.
 * - En la pagina de login.
 * - Si la vista no contiene sidebar.
 *
 * @returns {void}
 */
function injectLogoutButton() {
    if (
        document.querySelector(
            ".logout-link"
        )
    ) {
        return;
    }

    if (
        document.body.classList.contains(
            "login-page"
        )
    ) {
        return;
    }

    const footer =
        document.querySelector(
            ".sidebar-footer"
        );

    const sidebar =
        document.querySelector(
            ".sidebar"
        );

    /*
     * Sin sidebar no existe una ubicacion adecuada para este enlace.
     */
    if (!sidebar) {
        return;
    }

    const link =
        document.createElement(
            "a"
        );

    link.href =
        "/logout";

    link.className =
        "nav-item logout-link";

    link.textContent =
        "Cerrar sesion";

    /*
     * Se prefiere el footer cuando existe.
     */
    if (footer) {
        footer.appendChild(
            link
        );
    } else {
        sidebar.appendChild(
            link
        );
    }
}


/**
 * Crea el modal global de confirmacion de cierre de sesion.
 *
 * El modal se genera una sola vez y se incorpora directamente al body.
 *
 * Se utilizan atributos ARIA para describirlo como dialogo modal accesible.
 *
 * @returns {void}
 */
function injectLogoutConfirmModal() {
    /*
     * Evita crear multiples instancias.
     */
    if (
        document.getElementById(
            'logoutConfirmModal'
        )
    ) {
        return;
    }

    const modal =
        document.createElement(
            'div'
        );

    modal.id =
        'logoutConfirmModal';

    modal.className =
        'logout-confirm-modal';

    /*
     * aria-hidden="true" indica inicialmente que el modal no esta visible.
     */
    modal.setAttribute(
        'aria-hidden',
        'true'
    );

    /*
     * El backdrop y el boton Cancelar incluyen el atributo data-close-logout-modal.
     * Este atributo se utilizara posteriormente mediante dataset para detectar si
     * un click debe cerrar el dialogo.
     */
    modal.innerHTML = `
        <div
            class="logout-confirm-backdrop"
            data-close-logout-modal="true">
        </div>

        <div
            class="logout-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="logoutConfirmTitle">

            <div class="logout-confirm-eyebrow">
                Confirmacion
            </div>

            <h3
                id="logoutConfirmTitle"
                class="logout-confirm-title">
                Cerrar sesion
            </h3>

            <p class="logout-confirm-text">
                Vas a salir de tu sesion actual. Tendras que volver a introducir tus credenciales para entrar de nuevo.
            </p>

            <div class="logout-confirm-actions">

                <button
                    type="button"
                    class="logout-confirm-button logout-confirm-cancel"
                    data-close-logout-modal="true">
                    Cancelar
                </button>

                <a
                    href="/logout"
                    class="logout-confirm-button logout-confirm-danger">
                    Cerrar sesion
                </a>

            </div>
        </div>
    `;

    /*
     * Se utiliza delegacion de eventos sobre todo el modal.
     */
    modal.addEventListener(
        'click',
        (event) => {
            /*
             * dataset.closeLogoutModal representa el atributo
             * data-close-logout-modal.
             */
            if (
                event.target.dataset.closeLogoutModal
                === 'true'
            ) {
                closeLogoutConfirmModal();
            }
        }
    );

    document.body.appendChild(
        modal
    );
}


/**
 * Abre el modal de confirmacion de cierre de sesion.
 *
 * Se modifica tanto el estado visual como los atributos de accesibilidad y se
 * bloquea el scroll del contenido de fondo.
 *
 * @returns {void}
 */
function openLogoutConfirmModal() {
    const modal =
        document.getElementById(
            'logoutConfirmModal'
        );

    if (!modal) {
        return;
    }

    modal.classList.add(
        'visible'
    );

    modal.setAttribute(
        'aria-hidden',
        'false'
    );

    document.body.classList.add(
        'logout-modal-open'
    );
}


/**
 * Cierra el modal de confirmacion de cierre de sesion.
 *
 * @returns {void}
 */
function closeLogoutConfirmModal() {
    const modal =
        document.getElementById(
            'logoutConfirmModal'
        );

    if (!modal) {
        return;
    }

    modal.classList.remove(
        'visible'
    );

    modal.setAttribute(
        'aria-hidden',
        'true'
    );

    document.body.classList.remove(
        'logout-modal-open'
    );
}


/**
 * Intercepta los enlaces de cierre de sesion.
 *
 * En lugar de navegar inmediatamente a /logout, se cancela el comportamiento
 * predeterminado y se muestra primero el modal de confirmacion.
 *
 * @returns {void}
 */
function bindLogoutConfirmation() {
    document
        .querySelectorAll(
            '.logout-link'
        )
        .forEach((link) => {
            /*
             * confirmBound actua como marca para no registrar varias veces el mismo
             * listener sobre un enlace.
             */
            if (
                link.dataset.confirmBound
                === 'true'
            ) {
                return;
            }

            link.dataset.confirmBound =
                'true';

            link.addEventListener(
                'click',
                (event) => {
                    /*
                     * preventDefault() evita la navegacion inmediata hacia /logout.
                     */
                    event.preventDefault();

                    openLogoutConfirmModal();
                }
            );
        });
}


/**
 * Inserta el enlace "Mi cuenta" en la navegacion global.
 *
 * Si existe un footer se intenta colocarlo antes del enlace de logout para
 * mantener una jerarquia coherente.
 *
 * @returns {void}
 */
function injectMyAccountLink() {
    if (
        document.querySelector(
            '.my-account-link, a[href="mi-cuenta.html"]'
        )
    ) {
        return;
    }

    if (
        document.body.classList.contains(
            'login-page'
        )
    ) {
        return;
    }

    const footer =
        document.querySelector(
            '.sidebar-footer'
        );

    const sidebar =
        document.querySelector(
            '.sidebar'
        );

    if (!sidebar) {
        return;
    }

    const link =
        document.createElement(
            'a'
        );

    link.href =
        'mi-cuenta.html';

    link.className =
        'nav-item my-account-link';

    if (
        window.location.pathname.endsWith(
            '/mi-cuenta.html'
        )
        || window.location.pathname.endsWith(
            'mi-cuenta.html'
        )
    ) {
        /*
         * Cuando la vista actual es Mi cuenta, el enlace inyectado debe verse
         * activo igual que cualquier otra opcion del sidebar.
         */
        link.classList.add(
            'active'
        );
    }

    link.textContent =
        'Mi cuenta';

    if (footer) {
        const logout =
            footer.querySelector(
                '.logout-link'
            );

        if (logout) {
            /*
             * insertBefore() coloca Mi cuenta inmediatamente antes de logout.
             */
            footer.insertBefore(
                link,
                logout
            );
        } else {
            footer.appendChild(
                link
            );
        }

    } else {
        sidebar.appendChild(
            link
        );
    }
}


/**
 * Inserta el acceso a Gestion de Permisos.
 *
 * Esta funcion se utiliza exclusivamente cuando el perfil autenticado posee rol
 * administrador.
 *
 * @returns {void}
 */
function injectAdminPermissionsLink() {
    /*
     * Se comprueban tanto la clase dinamica como un enlace que pudiera estar
     * definido directamente en el HTML.
     */
    if (
        document.querySelector(
            '.permissions-link, a[href="gestion-permisos.html"]'
        )
    ) {
        return;
    }

    const footer =
        document.querySelector(
            '.sidebar-footer'
        );

    if (!footer) {
        return;
    }

    const link =
        document.createElement(
            'a'
        );

    link.href =
        'gestion-permisos.html';

    link.className =
        'nav-item permissions-link';

    link.textContent =
        'Gestion de Permisos';

    const myAccountLink =
        footer.querySelector(
            '.my-account-link'
        );

    const logout =
        footer.querySelector(
            '.logout-link'
        );

    /*
     * Se intenta mantener el siguiente orden:
     * opciones administrativas -> Mi cuenta -> Cerrar sesion.
     */
    if (logout) {
        footer.insertBefore(
            link,
            logout
        );

    } else if (myAccountLink) {
        footer.insertBefore(
            link,
            myAccountLink.nextSibling
        );

    } else {
        footer.appendChild(
            link
        );
    }
}


/**
 * Oculta divisores y etiquetas de secciones vacias del sidebar.
 *
 * Se consideran visibles los enlaces cuyo style.display no sea "none".
 *
 * @returns {void}
 */
function cleanupSidebarVisibility() {
    document
        .querySelectorAll(
            '.sidebar-footer'
        )
        .forEach((footer) => {
            /*
             * Array.from() convierte la NodeList en array para poder aplicar filter().
             */
            const visibleItems =
                Array.from(
                    footer.querySelectorAll(
                        '.nav-item'
                    )
                )
                    .filter(
                        (item) =>
                            item.style.display
                            !== 'none'
                    );

            const divider =
                footer.querySelector(
                    '.sidebar-divider'
                );

            const label =
                footer.querySelector(
                    '.sidebar-label'
                );

            const hasItems =
                visibleItems.length > 0;

            if (divider) {
                divider.style.display =
                    hasItems
                        ? ''
                        : 'none';
            }

            if (label) {
                label.style.display =
                    hasItems
                        ? ''
                        : 'none';
            }
        });
}


/**
 * Adapta la visibilidad de enlaces segun el rol autenticado.
 *
 * IMPORTANTE:
 * Esta logica afecta solamente a la interfaz. La seguridad real debe seguir
 * siendo aplicada por el backend, ya que ocultar un enlace no impide acceder
 * manualmente a una URL.
 *
 * @param {Object} profile Perfil recibido desde /api/auth/me.
 * @returns {void}
 */
function applyRoleVisibility(profile) {
    /*
     * El encadenamiento opcional ?. evita errores cuando profile es null o
     * undefined.
     */
    const role =
        profile?.role
        || 'normal';

    /*
     * Se almacena el rol tambien como data-user-role sobre body.
     */
    document.body.dataset.userRole =
        role;


    /*
     * CONFIGURACION DEL SISTEMA
     * ---------------------------------------------------------------------
     *
     * Los usuarios de rol normal no visualizan el enlace.
     */
    document
        .querySelectorAll(
            'a[href="configurar-sistema.html"]'
        )
        .forEach((link) => {
            link.style.display =
                role === 'normal'
                    ? 'none'
                    : '';
        });


    /*
     * GESTION DE PERMISOS
     * ---------------------------------------------------------------------
     *
     * Se muestra exclusivamente a administradores.
     */
    document
        .querySelectorAll(
            'a[href="gestion-permisos.html"]'
        )
        .forEach((link) => {
            link.style.display =
                role === 'administrador'
                    ? ''
                    : 'none';
        });


    if (
        role === 'administrador'
    ) {
        /*
         * Si el enlace no estaba definido en el HTML se crea dinamicamente.
         */
        injectAdminPermissionsLink();

    } else {
        /*
         * Para otros roles se eliminan enlaces dinamicos que pudieran existir.
         */
        document
            .querySelectorAll(
                '.permissions-link'
            )
            .forEach(
                (link) =>
                    link.remove()
            );
    }

    /*
     * Finalmente se limpian posibles secciones vacias.
     */
    cleanupSidebarVisibility();
}


/**
 * Consulta el perfil autenticado y aplica la navegacion correspondiente al rol.
 *
 * La pagina de login se excluye porque todavia no existe una sesion autenticada.
 *
 * @returns {Promise<void>}
 */
async function initRoleAwareness() {
    if (
        document.body.classList.contains(
            'login-page'
        )
    ) {
        /*
         * Aunque no se consulte el perfil, se debe informar a CSS de que el sidebar
         * ya puede considerarse inicializado.
         */
        markSidebarReady();
        return;
    }

    try {
        /*
         * credentials: 'same-origin' permite enviar las credenciales/cookies del
         * mismo origen junto a la peticion.
         */
        const response =
            await fetch(
                '/api/auth/me',
                {
                    credentials:
                        'same-origin'
                }
            );

        /*
         * fetch() no lanza automaticamente una excepcion por estados HTTP 4xx/5xx,
         * por lo que response.ok se comprueba expresamente.
         */
        if (!response.ok) {
            markSidebarReady();
            return;
        }

        const profile =
            await response.json();

        /*
         * Se expone el perfil globalmente para que otras partes del frontend puedan
         * reutilizarlo si lo necesitan.
         */
        window.smiAuthProfile =
            profile;

        applyRoleVisibility(
            profile
        );

    } catch (error) {
        /*
         * Un fallo de esta peticion no modifica la seguridad real.
         * El backend continua siendo responsable de validar permisos.
         */
        console.warn(
            'No se pudo cargar el perfil de acceso',
            error
        );

    } finally {
        /*
         * finally se ejecuta tanto si la operacion termina correctamente como si se
         * produce una excepcion.
         */
        markSidebarReady();
    }
}


/**
 * Abre o cierra el menu movil compartido.
 *
 * La clase mobile-sidebar-open es interpretada por theme.css para modificar la
 * altura y visibilidad del sidebar.
 *
 * @param {boolean} isOpen Estado deseado del menu.
 * @returns {void}
 */
function setMobileSidebarState(
    isOpen
) {
    /*
     * Boolean() normaliza el valor recibido a true o false.
     */
    document.body.classList.toggle(
        "mobile-sidebar-open",
        Boolean(isOpen)
    );

    const toggle =
        document.querySelector(
            ".mobile-nav-toggle"
        );

    if (toggle) {
        /*
         * aria-expanded comunica el estado del menu a tecnologias de asistencia.
         */
        toggle.setAttribute(
            "aria-expanded",
            isOpen
                ? "true"
                : "false"
        );

        /*
         * aria-label describe la accion disponible en cada momento.
         */
        toggle.setAttribute(
            "aria-label",
            isOpen
                ? "Cerrar menu de navegacion"
                : "Abrir menu de navegacion"
        );
    }
}


/**
 * Inserta el boton hamburguesa utilizado por el sidebar en dispositivos moviles.
 *
 * @returns {void}
 */
function injectMobileSidebarToggle() {
    const sidebar =
        document.querySelector(
            ".sidebar"
        );

    const logoBox =
        document.querySelector(
            ".logo-box"
        );

    /*
     * El componente requiere sidebar y logoBox y no debe duplicarse.
     */
    if (
        !sidebar
        || !logoBox
        || document.querySelector(
            ".mobile-nav-toggle"
        )
    ) {
        return;
    }

    /*
     * Esta clase activa en theme.css la estructura responsive compartida.
     */
    document.body.classList.add(
        "has-mobile-sidebar"
    );

    const button =
        document.createElement(
            "button"
        );

    button.type =
        "button";

    button.className =
        "mobile-nav-toggle";

    button.setAttribute(
        "aria-expanded",
        "false"
    );

    button.setAttribute(
        "aria-label",
        "Abrir menu de navegacion"
    );

    /*
     * Las tres lineas se transforman mediante CSS en una X cuando el menu esta
     * abierto.
     */
    button.innerHTML = `
        <span class="mobile-nav-toggle-line"></span>
        <span class="mobile-nav-toggle-line"></span>
        <span class="mobile-nav-toggle-line"></span>
    `;

    button.addEventListener(
        "click",
        () => {
            /*
             * Se invierte el estado actual consultando la clase del body.
             */
            setMobileSidebarState(
                !document.body.classList.contains(
                    "mobile-sidebar-open"
                )
            );
        }
    );

    logoBox.appendChild(
        button
    );


    /*
     * En pantallas pequeñas, seleccionar una opcion vuelve a cerrar el menu para
     * dejar espacio libre al contenido.
     */
    sidebar
        .querySelectorAll(
            ".nav-item"
        )
        .forEach((item) => {
            item.addEventListener(
                "click",
                () => {
                    if (
                        window.innerWidth <= 560
                    ) {
                        setMobileSidebarState(
                            false
                        );
                    }
                }
            );
        });
}


/**
 * Registra eventos asociados al ciclo de vida del menu movil.
 *
 * - Al ampliar la ventana por encima de 560 px se cierra el menu movil.
 * - La tecla Escape cierra tanto el sidebar como el modal de logout.
 *
 * @returns {void}
 */
function bindMobileSidebarLifecycle() {
    /**
     * Sincroniza el estado del sidebar con el ancho actual de la ventana.
     *
     * @returns {void}
     */
    const syncState = () => {
        if (
            window.innerWidth > 560
        ) {
            setMobileSidebarState(
                false
            );
        }
    };

    /*
     * resize se dispara cuando cambia el tamano del viewport.
     */
    window.addEventListener(
        "resize",
        syncState
    );

    /*
     * keydown permite responder a la tecla Escape desde cualquier elemento.
     */
    document.addEventListener(
        "keydown",
        (event) => {
            if (
                event.key === "Escape"
            ) {
                setMobileSidebarState(
                    false
                );

                closeLogoutConfirmModal();
            }
        }
    );

    /*
     * Primera sincronizacion inmediata.
     */
    syncState();
}


/*
 * ============================================================================
 * INICIALIZACION GLOBAL
 * ============================================================================
 *
 * DOMContentLoaded se dispara cuando el HTML ya ha sido interpretado y todos los
 * elementos necesarios pueden localizarse mediante selectores DOM.
 *
 * El callback se declara async porque la ultima fase espera a initRoleAwareness().
 */
document.addEventListener(
    "DOMContentLoaded",
    async () => {
        /*
         * Se insertan primero los componentes globales.
         */
        injectThemeToggle();
        injectMyAccountLink();
        injectLogoutButton();
        injectLogoutConfirmModal();

        /*
         * Se registran comportamientos asociados a los componentes anteriores.
         */
        bindLogoutConfirmation();
        injectMobileSidebarToggle();
        bindMobileSidebarLifecycle();

        /*
         * Se aplica la preferencia visual persistida.
         */
        applyTheme(
            currentTheme()
        );

        /*
         * Finalmente se consulta el perfil y se adapta el menu al rol.
         *
         * await evita marcar la inicializacion como terminada antes de completar esta
         * fase asincrona.
         */
        await initRoleAwareness();
    }
);
