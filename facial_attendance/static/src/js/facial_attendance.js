/** @odoo-module **/

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { FacialCameraUtils } from "./face_api_loader";

// ─── Intervalo de reconocimiento (ms) ────────────────────────────────────────
const RECOGNITION_INTERVAL = 2500;
// Tiempo que se muestra el resultado antes de volver a escanear (ms)
const RESULT_DISPLAY_TIME = 4000;

// ─── Componente Quiosco ───────────────────────────────────────────────────────

export class FacialAttendanceKiosk extends Component {
    static template = "facial_attendance.Kiosk";

    setup() {
        this.notification = useService("notification");

        this.videoRef = useRef("video");
        this.canvasRef = useRef("canvas");

        this.state = useState({
            status: "loading",     // loading | ready | scanning | success | error | no_camera
            errorMsg: "",
            result: null,
            cameraError: null,
            clockTime: "",
            clockDate: "",
        });

        this._stream = null;
        this._recognitionTimer = null;
        this._resultTimer = null;
        this._clockTimer = null;
        this._isRecognizing = false;

        onMounted(async () => {
            this._startClock();
            await this._initCamera();
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
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            });
            this.state.clockDate = now.toLocaleDateString('es-MX', {
                weekday: 'long',
                day: 'numeric',
                month: 'long',
                year: 'numeric',
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
            const videoEl = this.videoRef.el;
            this._stream = await FacialCameraUtils.startCamera(videoEl);
            this.state.status = "ready";
            // Esperar un momento para que la cámara se estabilice
            setTimeout(() => this._startRecognitionLoop(), 1000);
        } catch (err) {
            this.state.status = "no_camera";
            this.state.cameraError = err.message;
        }
    }

    // ─── Loop de reconocimiento ───────────────────────────────────────────────

    _startRecognitionLoop() {
        this._recognitionTimer = setInterval(async () => {
            if (this._isRecognizing) return;
            if (this.state.status === "success") return;
            await this._doRecognition();
        }, RECOGNITION_INTERVAL);
    }

    async _doRecognition() {
        const videoEl = this.videoRef.el;
        const canvasEl = this.canvasRef.el;
        if (!videoEl || !canvasEl) return;

        const frame = FacialCameraUtils.captureFrame(videoEl, canvasEl);
        if (!frame) return;

        this._isRecognizing = true;
        this.state.status = "scanning";

        try {
            const result = await rpc("/facial_attendance/recognize", {
                image_data: frame,
                device_ip: null,
            });

            if (result.success) {
                this.state.status = "success";
                this.state.result = result;

                // Volver a escanear después de mostrar resultado
                clearTimeout(this._resultTimer);
                this._resultTimer = setTimeout(() => {
                    this.state.status = "ready";
                    this.state.result = null;
                }, RESULT_DISPLAY_TIME);

            } else {
                const ignoredErrors = ["no_face_detected", "no_match"];
                if (ignoredErrors.includes(result.error)) {
                    this.state.status = "ready";
                } else {
                    this.state.status = "error";
                    this.state.errorMsg = result.error_detail || "Error desconocido";
                    setTimeout(() => {
                        this.state.status = "ready";
                    }, 3000);
                }
            }
        } catch (err) {
            console.error("[FacialAttendance] Error RPC:", err);
            this.state.status = "ready";
        } finally {
            this._isRecognizing = false;
        }
    }

    // ─── Cleanup ──────────────────────────────────────────────────────────────

    _cleanup() {
        clearInterval(this._recognitionTimer);
        clearInterval(this._clockTimer);
        clearTimeout(this._resultTimer);
        FacialCameraUtils.stopCamera(this._stream);
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    get cameraState() {
        const { status, result } = this.state;
        if (status === "success") return "success";
        if (status === "scanning") return "detecting";
        if (status === "error") return "error";
        return "";
    }

    get showScanning() {
        return this.state.status === "scanning";
    }

    get showResult() {
        return this.state.status === "success" && this.state.result;
    }

    get showError() {
        return this.state.status === "error";
    }

    get showNoCamera() {
        return this.state.status === "no_camera";
    }

    get showLoading() {
        return this.state.status === "loading";
    }

    get actionClass() {
        return this.state.result?.action_type || "";
    }
}

// ─── Componente Widget de Cámara para el Wizard ───────────────────────────────

export class FacialCameraWidget extends Component {
    static template = "facial_attendance.CameraWidget";
    static props = ["*"];

    setup() {
        this.videoRef = useRef("wizardVideo");
        this.canvasRef = useRef("wizardCanvas");
        this.previewRef = useRef("wizardPreview");

        this.state = useState({
            streaming: false,
            captured: false,
            error: null,
            captureCount: 0,
        });

        this._stream = null;

        onMounted(async () => {
            await this._startCamera();
        });

        onWillUnmount(() => {
            this._stopCamera();
        });
    }

    async _startCamera() {
        try {
            const videoEl = this.videoRef.el;
            this._stream = await FacialCameraUtils.startCamera(videoEl);
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
        const videoEl = this.videoRef.el;
        const canvasEl = this.canvasRef.el;
        const previewEl = this.previewRef.el;

        const imageData = FacialCameraUtils.captureFrame(videoEl, canvasEl, 0.92);
        if (!imageData) return;

        // Mostrar preview
        if (previewEl) {
            previewEl.src = imageData;
        }

        this.state.captured = true;
        this.state.captureCount++;

        // Actualizar el campo en el wizard
        // Buscamos el campo oculto del wizard y seteamos el valor
        this._updateWizardField(imageData);
    }

    _updateWizardField(imageData) {
        // Actualizar directamente el registro del wizard a traves del prop "record"
        // que Odoo inyecta automaticamente en los widgets genericos de vista.
        // (Antes se emitia un CustomEvent con this.el.dispatchEvent(), pero
        // this.el puede ser undefined para widgets de vista genericos y nada
        // escuchaba ese evento, por lo que el campo nunca se actualizaba.)
        if (this.props.record) {
            this.props.record.update({ captured_image: imageData });
        }
    }

    retakeImage() {
        const previewEl = this.previewRef.el;
        if (previewEl) previewEl.src = "";
        this.state.captured = false;
        this._updateWizardField(null);
    }
}

// ─── Registrar componentes ────────────────────────────────────────────────────

registry.category("actions").add("facial_attendance.Kiosk", FacialAttendanceKiosk);

// Registrar como widget de campo para el wizard
registry.category("view_widgets").add("facial_camera", {
    component: FacialCameraWidget,
});
