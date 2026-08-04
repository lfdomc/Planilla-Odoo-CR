# -*- coding: utf-8 -*-
"""
Utilidades de deteccion de vida (liveness) por parpadeo.

Separadas del controlador principal porque son funciones puras (sin
estado, sin dependencia de request/self) que calculan el Eye Aspect
Ratio (EAR) a partir de los landmarks faciales -- la tecnica estandar
de la industria para deteccion de parpadeo (Soukupova & Cech, 2016,
"Real-Time Eye Blink Detection Using Facial Landmarks").

Usa face_recognition.face_landmarks(), una funcion NATIVA de la
libreria ya instalada -- no requiere ningun modelo ni dependencia
adicional, el detector de 68 puntos ya viene incluido en dlib.
"""
try:
    import numpy as np
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False


def eye_aspect_ratio(eye_points):
    """
    Calcula el Eye Aspect Ratio (EAR) de un ojo.

    eye_points: lista de 6 puntos (x, y) del contorno del ojo, tal como
    los retorna face_recognition.face_landmarks()['left_eye'] o
    ['right_eye'].

    Retorna un valor tipicamente entre 0.15 (ojo cerrado) y 0.35 (ojo
    abierto) -- cuando el ojo se cierra, la distancia vertical entre
    parpados colapsa mientras la distancia horizontal (ancho del ojo)
    se mantiene, haciendo que el ratio caiga notablemente.
    """
    p = [np.array(pt) for pt in eye_points]
    vertical_1 = np.linalg.norm(p[1] - p[5])
    vertical_2 = np.linalg.norm(p[2] - p[4])
    horizontal = np.linalg.norm(p[0] - p[3])
    if horizontal == 0:
        return 0.3  # valor neutro, evita division por cero en casos degenerados
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def get_ear_from_image(image_np):
    """
    Detecta el rostro principal en la imagen y retorna su Eye Aspect
    Ratio promedio (ambos ojos), o None si no se detecto ningun rostro
    o sus landmarks. Se usa para deteccion de vida (liveness) por
    parpadeo -- ver EAR_BLINK_THRESHOLD en
    FacialRecognitionController._do_recognize().
    """
    landmarks_list = face_recognition.face_landmarks(image_np)
    if not landmarks_list:
        return None
    landmarks = landmarks_list[0]
    if 'left_eye' not in landmarks or 'right_eye' not in landmarks:
        return None
    left_ear = eye_aspect_ratio(landmarks['left_eye'])
    right_ear = eye_aspect_ratio(landmarks['right_eye'])
    return (left_ear + right_ear) / 2.0
