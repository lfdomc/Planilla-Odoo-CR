/** @odoo-module **/

import { mount } from "@odoo/owl";
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
 * mount() de OWL no requiere los servicios del webclient (useService, etc.):
 * FacialAttendanceKiosk solo usa rpc() (fetch directo al endpoint JSON-RPC)
 * y APIs nativas del navegador (getUserMedia, canvas).
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

    mount(FacialAttendanceKiosk, target, {
        dev: false,
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
