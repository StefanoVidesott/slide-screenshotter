"""Confronto tra frame per rilevare il cambio di slide.

L'idea di base: due screenshot della stessa slide non sono mai identici bit per bit
(cursore, compressione video di Zoom, piccoli artefatti), quindi un confronto esatto
non funziona. Misuriamo invece *quale percentuale dell'area* è cambiata in modo
significativo, ignorando il rumore.
"""

from __future__ import annotations

import cv2
import numpy as np

# Larghezza a cui vengono ridimensionati i frame prima del confronto. Lavorare su
# un'immagine piccola rende il calcolo molto veloce e attenua il rumore ad alta
# frequenza (artefatti di compressione, anti-aliasing del testo).
COMPARE_WIDTH = 320

# Variazione minima di luminosità (0-255) perché un pixel sia considerato "cambiato".
# Sotto questa soglia le differenze sono trattate come rumore del flusso video.
PIXEL_DELTA = 25


def preprocess(frame_bgr: np.ndarray) -> np.ndarray:
    """Prepara un frame BGR per il confronto.

    1. Scala di grigi: il cambio di slide si vede nella luminosità; ignorare il
       colore riduce i dati di 3 volte senza perdere informazione utile.
    2. Ridimensionamento a COMPARE_WIDTH: velocità e riduzione del rumore.
    3. Sfocatura gaussiana: piccoli spostamenti di 1 pixel (es. ri-scalatura del
       video) non producono differenze spurie lungo i bordi del testo.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if width > COMPARE_WIDTH:
        new_height = max(1, round(height * COMPARE_WIDTH / width))
        gray = cv2.resize(gray, (COMPARE_WIDTH, new_height), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def difference_percent(reference: np.ndarray, current: np.ndarray) -> float:
    """Restituisce la percentuale (0-100) di pixel cambiati tra due frame preprocessati.

    - cv2.absdiff calcola |reference - current| pixel per pixel.
    - I pixel con differenza > PIXEL_DELTA sono "cambiati"; gli altri sono rumore.
    - Il risultato è (pixel cambiati / pixel totali) * 100.

    Esempio: con una ROI di 320x180 = 57.600 pixel, se 2.880 pixel cambiano
    oltre la soglia la differenza è 5%. Un nuovo titolo o un nuovo elenco puntato
    supera facilmente questo valore, mentre il movimento del cursore resta sotto l'1%.

    Se le dimensioni non coincidono (ROI cambiata) i frame sono considerati
    completamente diversi.
    """
    if reference.shape != current.shape:
        return 100.0
    diff = cv2.absdiff(reference, current)
    changed = np.count_nonzero(diff > PIXEL_DELTA)
    return changed * 100.0 / diff.size
