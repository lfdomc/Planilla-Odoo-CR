/** @odoo-module **/

/**
 * face_api_loader.js
 *
 * Este módulo NO usa face-api.js (que depende de TensorFlow).
 * En su lugar, gestiona la captura de imagen desde la cámara web
 * en el navegador y envía los frames al backend de Odoo (Python/face_recognition)
 * para el procesamiento del reconocimiento.
 *
 * Flujo:
 *   1. El navegador abre la cámara (getUserMedia)
 *   2. Captura frames a intervalos regulares
 *   3. Envía el frame (base64) al endpoint /facial_attendance/recognize
 *   4. El backend Python hace el reconocimiento con face_recognition
 *   5. El resultado se muestra al usuario
 */

export const FacialCameraUtils = {

    /**
     * Solicita acceso a la cámara del dispositivo.
     * @param {HTMLVideoElement} videoEl - Elemento video del DOM
     * @param {Object} constraints - Restricciones de getUserMedia
     * @returns {Promise<MediaStream>}
     */
    async startCamera(videoEl, constraints = {}) {
        const defaultConstraints = {
            video: {
                width: { ideal: 640, max: 1280 },
                height: { ideal: 480, max: 720 },
                facingMode: 'user',
            },
            audio: false,
        };

        const merged = Object.assign({}, defaultConstraints, constraints);

        try {
            const stream = await navigator.mediaDevices.getUserMedia(merged);
            videoEl.srcObject = stream;
            await videoEl.play();
            return stream;
        } catch (err) {
            console.error('[FacialAttendance] Error al acceder a la cámara:', err);
            if (err.name === 'NotAllowedError') {
                throw new Error(
                    'Permiso de cámara denegado. Por favor permita el acceso en la configuración del navegador.'
                );
            } else if (err.name === 'NotFoundError') {
                throw new Error(
                    'No se encontró ninguna cámara conectada al dispositivo.'
                );
            }
            throw new Error(`Error de cámara: ${err.message}`);
        }
    },

    /**
     * Detiene el stream de la cámara.
     * @param {MediaStream} stream
     */
    stopCamera(stream) {
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
        }
    },

    /**
     * Captura un frame del video y lo retorna como base64 JPEG.
     * @param {HTMLVideoElement} videoEl
     * @param {HTMLCanvasElement} canvasEl
     * @param {number} quality - 0.0 a 1.0
     * @returns {string} base64 JPEG
     */
    captureFrame(videoEl, canvasEl, quality = 0.85) {
        if (!videoEl || !canvasEl) return null;
        if (videoEl.readyState < 2) return null;

        const w = videoEl.videoWidth || 640;
        const h = videoEl.videoHeight || 480;
        canvasEl.width = w;
        canvasEl.height = h;

        const ctx = canvasEl.getContext('2d');
        // Capturamos sin mirror (el mirror es solo CSS)
        ctx.save();
        ctx.scale(-1, 1);
        ctx.drawImage(videoEl, -w, 0, w, h);
        ctx.restore();

        return canvasEl.toDataURL('image/jpeg', quality);
    },

    /**
     * Verifica si el navegador soporta getUserMedia.
     */
    isCameraSupported() {
        return !!(
            navigator.mediaDevices &&
            typeof navigator.mediaDevices.getUserMedia === 'function'
        );
    },

    /**
     * Formatea milisegundos en string legible mm:ss
     */
    formatMs(ms) {
        const s = Math.floor(ms / 1000);
        const m = Math.floor(s / 60);
        return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    },
};

export default FacialCameraUtils;
