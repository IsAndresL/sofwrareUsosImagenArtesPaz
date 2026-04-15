import base64

import cv2
import numpy as np


def decode_data_url(raw_data):
    if not raw_data or "," not in raw_data:
        raise ValueError("Formato de imagen invalido")

    encoded = raw_data.split(",", 1)[1]
    img_bytes = np.frombuffer(base64.b64decode(encoded), np.uint8)
    imagen = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
    if imagen is None:
        raise ValueError("No se pudo decodificar la imagen")
    return imagen


def detectar_documento(imagen):
    gray = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 75, 200)

    contornos, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contornos = sorted(contornos, key=cv2.contourArea, reverse=True)

    for contorno in contornos:
        perimetro = cv2.arcLength(contorno, True)
        approx = cv2.approxPolyDP(contorno, 0.02 * perimetro, True)
        if len(approx) == 4:
            return approx

    return None


def ordenar_puntos(puntos):
    pts = np.array(puntos, dtype="float32").reshape(4, 2)
    rect = np.zeros((4, 2), dtype="float32")

    suma = pts.sum(axis=1)
    rect[0] = pts[np.argmin(suma)]
    rect[2] = pts[np.argmax(suma)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    return rect


def transformar(imagen, puntos):
    rect = ordenar_puntos(puntos)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = int(max(width_a, width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = int(max(height_a, height_b))

    dst = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(imagen, matrix, (max_width, max_height))


def aplicar_filtro(imagen, filtro):
    if filtro == "bn":
        gray = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)

    if filtro == "contraste":
        gray = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    return imagen


def ajustar_a_carta(imagen):
    target_ratio = 8.5 / 11.0
    h, w = imagen.shape[:2]
    if h == 0 or w == 0:
        return imagen

    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x0 = max((w - new_w) // 2, 0)
        recorte = imagen[:, x0 : x0 + new_w]
    else:
        new_h = int(w / target_ratio)
        y0 = max((h - new_h) // 2, 0)
        recorte = imagen[y0 : y0 + new_h, :]

    return cv2.resize(recorte, (1275, 1650), interpolation=cv2.INTER_CUBIC)
