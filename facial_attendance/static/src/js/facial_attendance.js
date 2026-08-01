/** @odoo-module **/

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { FacialCameraUtils } from "./face_api_loader";

// ─── Constantes ───────────────────────────────────────────────────────────────

/** Intervalo entre intentos de reconocimiento en el quiosco (ms). */
const RECOGNITION_INTERVAL = 2500;
/** Tiempo que se muestra la tarjeta de resultado antes de volver a escanear (ms). */
const RESULT_DISPLAY_TIME = 4000;
/**
 * Duracion de la ventana de deteccion activa despues de presionar el
 * boton "Marcar Asistencia" (ms). Durante esta ventana el kiosco
 * reintenta el reconocimiento cada RECOGNITION_INTERVAL; si nadie se
 * reconoce dentro de este tiempo, el kiosco vuelve al estado de espera
 * (boton) automaticamente, sin seguir escaneando de forma indefinida.
 */
const ACTIVE_DETECTION_WINDOW = 8000;
/** Intervalo entre chequeos de posicion GPS en vivo (ms). Mas espaciado
 *  que el reconocimiento facial porque el GPS cambia poco y consultarlo
 *  muy seguido gasta bateria innecesariamente en dispositivos moviles. */
const GPS_STATUS_INTERVAL = 15000;

// ─── Componente Quiosco ───────────────────────────────────────────────────────

export class FacialAttendanceKiosk extends Component {
    static template = "facial_attendance.Kiosk";
    /**
     * Acepta props de forma flexible porque puede montarse de dos maneras:
     *  1. Via ir.actions.client (backend): Odoo inyecta props estandar del framework.
     *  2. Via kiosk_standalone.js (pagina publica): se pasa { recognizeUrl }.
     */
    static props = ["*"];

    setup() {
        // recognizeUrl: ruta del endpoint de reconocimiento. Llega como prop
        // en modo standalone (tablet publica). En modo backend (ir.actions.client)
        // usa la ruta autenticada por defecto.
        this.recognizeUrl = this.props?.recognizeUrl || "/facial_attendance/recognize";

        this.videoRef = useRef("video");
        this.canvasRef = useRef("canvas");

        this.state = useState({
            status: "loading",     // loading | waiting_for_button | scanning | success | error | no_camera | pending_activation
            errorMsg: "",
            result: null,
            cameraError: null,
            clockTime: "",
            clockDate: "",
            // Posicion GPS en vivo del kiosco (independiente del resultado
            // de una marcacion): 'not_required' | 'ok' | 'out_of_range' | 'no_gps'
            gpsStatus: "not_required",
            gpsDistance: null,
        });

        this._deviceToken = FacialCameraUtils.getOrCreateDeviceToken();

        this._stream = null;
        this._activeDetectionTimer = null;
        this._detectionWindowEnd = 0;
        this._resultTimer = null;
        this._clockTimer = null;
        this._gpsStatusTimer = null;
        this._isRecognizing = false;

        onMounted(async () => {
            this._startClock();
            await this._initCamera();
            this._startGpsStatusLoop();
        });

        onWillUnmount(() => {
            this._cleanup();
        });
    }

    // ─── Reloj ────────────────────────────────────────────────────────────────

    _startClock() {
        const update = () => {
            const now = new Date();
            this.state.clockTime = now.toLocaleTimeString('es-MX', {
                hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
            });
            this.state.clockDate = now.toLocaleDateString('es-MX', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
            });
        };
        update();
        this._clockTimer = setInterval(update, 1000);
    }

    // ─── Cámara ───────────────────────────────────────────────────────────────

    async _initCamera() {
        if (!FacialCameraUtils.isCameraSupported()) {
            this.state.status = "no_camera";
            this.state.cameraError = "Este navegador no soporta acceso a cámara.";
            return;
        }
        try {
            this.state.status = "loading";
            this._stream = await FacialCameraUtils.startCamera(this.videoRef.el);
            // FIX: ya NO se arranca el bucle de reconocimiento
            // automatico aqui. La camara queda encendida (para que la
            // persona pueda verse y encuadrarse), pero el
            // reconocimiento activo solo empieza cuando el usuario
            // presiona el boton "Marcar Asistencia" -- ver
            // startActiveDetection(). Esto evita que el kiosco este
            // intentando reconocer cada 2.5s de forma indefinida sin
            // que haya nadie enfrente.
            this.state.status = "waiting_for_button";
        } catch (err) {
            this.state.status = "no_camera";
            this.state.cameraError = err.message;
        }
    }

    // ─── Deteccion activa (por boton, con ventana de tiempo) ──────────────────

    /**
     * Inicia una ventana de deteccion activa de ACTIVE_DETECTION_WINDOW
     * milisegundos: reintenta el reconocimiento cada RECOGNITION_INTERVAL
     * mientras dura la ventana. Se detiene automaticamente al vencerse
     * el tiempo (volviendo al estado "waiting_for_button", listo para
     * un nuevo intento) o antes si hay un reconocimiento exitoso.
     *
     * FIX: reemplaza el bucle indefinido anterior (_startRecognitionLoop,
     * que corria cada 2.5s sin parar mientras el kiosco estuviera
     * montado). Ese bucle generaba un intento de reconocimiento -- y,
     * antes del fix del backend, un registro en la base de datos --
     * cada 2.5 segundos incluso sin nadie enfrente de la camara,
     * acumulando decenas de registros de "no se detecto rostro" por
     * cada sesion de espera. Ahora el reconocimiento solo corre
     * cuando el usuario presiona explicitamente "Marcar Asistencia",
     * y se apaga solo despues de unos segundos si no encuentra a nadie.
     */
    startActiveDetection() {
        if (this.state.status !== "waiting_for_button") return;

        this.state.status = "scanning";
        this._detectionWindowEnd = Date.now() + ACTIVE_DETECTION_WINDOW;

        const attempt = async () => {
            // La ventana pudo cerrarse mientras un intento anterior
            // seguia en curso (ej. esperando la respuesta del backend);
            // no seguir intentando ni programar el siguiente intento.
            if (this.state.status !== "scanning") return;

            if (Date.now() >= this._detectionWindowEnd) {
                this._stopActiveDetection();
                return;
            }
            if (!this._isRecognizing) {
                await this._doRecognition();
            }
            // Si _doRecognition() encontro una coincidencia, ya cambio
            // this.state.status a "success" -- no programar otro intento.
            if (this.state.status === "scanning") {
                this._activeDetectionTimer = setTimeout(attempt, RECOGNITION_INTERVAL);
            }
        };
        this._activeDetectionTimer = setTimeout(attempt, RECOGNITION_INTERVAL);
    }

    _stopActiveDetection() {
        clearTimeout(this._activeDetectionTimer);
        this._activeDetectionTimer = null;
        if (this.state.status === "scanning") {
            this.state.status = "waiting_for_button";
        }
    }

    // ─── Posición GPS en vivo del kiosco ──────────────────────────────────────

    /**
     * Deriva la URL del endpoint de estado GPS a partir de recognizeUrl,
     * para que automaticamente use la variante correspondiente
     * (autenticada, publica compartida, o enlace por token de kiosco)
     * sin necesidad de otro prop.
     */
    get kioskStatusUrl() {
        // Enlace por token de kiosco: /facial_attendance/k/<token>/recognize
        // -> /facial_attendance/k/<token>/kiosk_status
        const tokenMatch = this.recognizeUrl.match(/^\/facial_attendance\/k\/([^/]+)\/recognize$/);
        if (tokenMatch) {
            return `/facial_attendance/k/${tokenMatch[1]}/kiosk_status`;
        }
        if (this.recognizeUrl.includes("/kiosk/public/")) {
            return "/facial_attendance/kiosk/public/kiosk_status";
        }
        return "/facial_attendance/kiosk_status";
    }

    _startGpsStatusLoop() {
        if (!FacialCameraUtils.isGeolocationSupported()) return;
        this._checkGpsStatus();
        this._gpsStatusTimer = setInterval(() => {
            this._checkGpsStatus();
        }, GPS_STATUS_INTERVAL);
    }

    async _checkGpsStatus() {
        try {
            const pos = await FacialCameraUtils.getCurrentPosition(6000);
            const result = await rpc(this.kioskStatusUrl, {
                device_token: this._deviceToken,
                gps_lat: pos.lat,
                gps_lng: pos.lng,
            });
            this.state.gpsStatus = result.status || "not_required";
            this.state.gpsDistance = result.distance_meters;
        } catch (err) {
            // Fallo silencioso: el chequeo de posicion es informativo,
            // nunca debe interrumpir el flujo principal del kiosco.
            console.warn("[FacialAttendance] Error consultando estado GPS:", err);
        }
    }

    async _doRecognition() {
        const videoEl = this.videoRef.el;
        const canvasEl = this.canvasRef.el;
        if (!videoEl || !canvasEl) return;

        const frame = FacialCameraUtils.captureFrame(videoEl, canvasEl);
        if (!frame) return;

        this._isRecognizing = true;
        this.state.status = "scanning";

        // GPS es best-effort: si el usuario no ha dado permiso o el
        // dispositivo no tiene GPS, se envia igual la marcacion sin
        // coordenadas -- el backend decide si eso importa segun si el
        // kiosco tiene require_gps activo.
        let gpsLat = null;
        let gpsLng = null;
        if (FacialCameraUtils.isGeolocationSupported()) {
            const pos = await FacialCameraUtils.getCurrentPosition(5000);
            gpsLat = pos.lat;
            gpsLng = pos.lng;
        }

        try {
            const result = await rpc(this.recognizeUrl, {
                image_data: frame,
                device_ip: null,
                device_token: this._deviceToken,
                gps_lat: gpsLat,
                gps_lng: gpsLng,
            });

            if (result.success) {
                clearTimeout(this._activeDetectionTimer);
                this._activeDetectionTimer = null;
                this.state.status = "success";
                this.state.result = result;

                clearTimeout(this._resultTimer);
                this._resultTimer = setTimeout(() => {
                    this.state.status = "waiting_for_button";
                    this.state.result = null;
                }, RESULT_DISPLAY_TIME);
            } else if (result.error === "kiosk_pending_activation") {
                clearTimeout(this._activeDetectionTimer);
                this._activeDetectionTimer = null;
                this.state.status = "pending_activation";
                this.state.errorMsg = result.error_detail;
            } else if (result.error === "kiosk_revoked") {
                clearTimeout(this._activeDetectionTimer);
                this._activeDetectionTimer = null;
                this.state.status = "error";
                this.state.errorMsg = result.error_detail;
            } else {
                // no_face_detected y no_match son condiciones normales
                // durante la ventana de deteccion activa -- se deja el
                // estado en "scanning" para que startActiveDetection()
                // siga reintentando hasta que se acabe la ventana de
                // tiempo o alguien sea reconocido. No es un error real,
                // asi que NO se detiene el ciclo activo por esto.
                const silentErrors = ["no_face_detected", "no_match"];
                if (silentErrors.includes(result.error)) {
                    this.state.status = "scanning";
                } else {
                    clearTimeout(this._activeDetectionTimer);
                    this._activeDetectionTimer = null;
                    this.state.status = "error";
                    this.state.errorMsg = result.error_detail || "Error desconocido";
                    setTimeout(() => { this.state.status = "waiting_for_button"; }, 3000);
                }
            }
        } catch (err) {
            console.error("[FacialAttendance] Error RPC:", err);
            clearTimeout(this._activeDetectionTimer);
            this._activeDetectionTimer = null;
            this.state.status = "waiting_for_button";
        } finally {
            this._isRecognizing = false;
        }
    }

    // ─── Cleanup ──────────────────────────────────────────────────────────────

    _cleanup() {
        clearTimeout(this._activeDetectionTimer);
        clearInterval(this._clockTimer);
        clearInterval(this._gpsStatusTimer);
        clearTimeout(this._resultTimer);
        FacialCameraUtils.stopCamera(this._stream);
    }

    // ─── Computed getters ─────────────────────────────────────────────────────

    get cameraState() {
        const { status } = this.state;
        if (status === "success") return "success";
        if (status === "scanning") return "detecting";
        if (status === "error") return "error";
        return "";
    }

    get showScanning() { return this.state.status === "scanning"; }
    get showResult()   { return this.state.status === "success" && this.state.result; }
    get showError()    { return this.state.status === "error"; }
    get showNoCamera() { return this.state.status === "no_camera"; }
    get showLoading()  { return this.state.status === "loading"; }
    get showPendingActivation() { return this.state.status === "pending_activation"; }
    get showWaitingButton() { return this.state.status === "waiting_for_button"; }
    get resultOutOfRange() { return !!(this.state.result && this.state.result.out_of_range); }
    get actionClass()  { return this.state.result?.action_type || ""; }

    /** Clase CSS para el borde del área de cámara según la posición GPS
     *  en vivo del kiosco: verde = en el lugar correcto, naranja = fuera
     *  de zona. Sin clase si el kiosco no requiere GPS o aún no hay
     *  lectura (comportamiento normal, no es un error). */
    get gpsIndicatorClass() {
        if (this.state.gpsStatus === "ok") return "o_facial_gps_ok";
        if (this.state.gpsStatus === "out_of_range") return "o_facial_gps_out";
        return "";
    }

    get showGpsBadge() {
        return this.state.gpsStatus === "ok" || this.state.gpsStatus === "out_of_range";
    }

    get gpsBadgeText() {
        if (this.state.gpsStatus === "ok") return "Kiosco GPS en el lugar correcto de Asistencias";
        if (this.state.gpsStatus === "out_of_range") return "GPS fuera de la zona de Asistencias";
        return "";
    }

    get gpsOutOfRangeAlert() {
        return this.state.gpsStatus === "out_of_range";
    }
}

// ─── Componente Widget de Cámara para el Wizard ───────────────────────────────

export class FacialCameraWidget extends Component {
    static template = "facial_attendance.CameraWidget";
    static props = ["*"];

    setup() {
        this.videoRef   = useRef("wizardVideo");
        this.canvasRef  = useRef("wizardCanvas");
        this.previewRef = useRef("wizardPreview");

        this.state = useState({
            streaming: false,
            captured: false,
            error: null,
        });

        this._stream = null;

        onMounted(async () => { await this._startCamera(); });
        onWillUnmount(() => { this._stopCamera(); });
    }

    async _startCamera() {
        try {
            this._stream = await FacialCameraUtils.startCamera(this.videoRef.el);
            this.state.streaming = true;
        } catch (err) {
            this.state.error = err.message;
        }
    }

    _stopCamera() {
        FacialCameraUtils.stopCamera(this._stream);
        this._stream = null;
    }

    captureImage() {
        const imageData = FacialCameraUtils.captureFrame(
            this.videoRef.el, this.canvasRef.el, 0.92
        );
        if (!imageData) return;

        if (this.previewRef.el) {
            this.previewRef.el.src = imageData;
        }
        this.state.captured = true;
        this._updateWizardField(imageData);
    }

    retakeImage() {
        if (this.previewRef.el) this.previewRef.el.src = "";
        this.state.captured = false;
        this._updateWizardField(null);
    }

    _updateWizardField(imageData) {
        // Actualizar directamente el registro del wizard a traves del prop
        // "record" que Odoo inyecta automaticamente en los widgets genericos
        // de vista (view_widgets registry).
        if (this.props.record) {
            this.props.record.update({ captured_image: imageData });
        }
    }
}

// ─── Registrar componentes ────────────────────────────────────────────────────

// Accion cliente para el menu backend "Abrir Quiosco"
registry.category("actions").add("facial_attendance.Kiosk", FacialAttendanceKiosk);

// Widget embebido en el formulario del wizard de registro facial
registry.category("view_widgets").add("facial_camera", {
    component: FacialCameraWidget,
});
