/** @odoo-module **/

import { mountComponent } from "@web/env";
import { FacialAttendanceKiosk } from "./facial_attendance";

/**
 * kiosk_standalone.js
 *
 * Monta el componente OWL del quiosco directamente en la pagina publica
 * (/facial_attendance/kiosk y /facial_attendance/kiosk/public), que se
 * sirve fuera del backend de Odoo (sin ir.actions.client).
 *
 * Sin este archivo, FacialAttendanceKiosk solo quedaba registrado en
 * registry.category("actions"), lo que funciona cuando el backend de
 * Odoo lo abre como accion (menu "Abrir Quiosco"), pero no hace nada en
 * una pagina standalone: nadie lo montaba. Esto dejaba el quiosco publico
 * (pensado para una tablet sin login) como una pantalla en blanco.
 *
 * FIX: se usa mountComponent() de @web/env, NO mount() generico de
 * @odoo/owl. Segun la documentacion oficial de Odoo 19 ("Create a
 * standalone Owl application"), mountComponent() es la utilidad
 * especifica de Odoo que crea el entorno, inicia los servicios,
 * activa las traducciones, Y da acceso al componente a los templates
 * QWeb del bundle de assets -- mount() puro de OWL no hace esa ultima
 * parte, lo que causaba "OwlError: Missing template:
 * facial_attendance.Kiosk" aunque el archivo XML del template ya
 * estuviera correctamente declarado en el bundle.
 */
function mountStandaloneKiosk() {
    const target = document.getElementById("facial_kiosk_app");
    if (!target) {
        // No estamos en la pagina del quiosco standalone: o estamos en el
        // backend (donde el montaje lo hace el webclient via ir.actions.client),
        // o el quiosco publico esta deshabilitado y el controlador renderizo
        // un mensaje alternativo sin el div #facial_kiosk_app.
        return;
    }

    mountComponent(FacialAttendanceKiosk, target, {
        props: {
            // recognizeUrl se inyecta como data-attribute por el controlador
            // de Odoo segun si la pagina es autenticada o publica.
            recognizeUrl: target.dataset.recognizeUrl || "/facial_attendance/recognize",
        },
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountStandaloneKiosk);
} else {
    mountStandaloneKiosk();
}
