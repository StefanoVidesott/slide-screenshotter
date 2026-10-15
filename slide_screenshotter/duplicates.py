"""Riconoscimento di slide duplicate o "incomplete" tra scatti consecutivi.

Le animazioni che fanno comparire un elenco puntato una voce alla volta producono
una serie di catture in cui ognuna contiene tutto ciò che c'era nella precedente
più qualcosa di nuovo. Di ogni serie conviene tenere solo l'ultima, completa.

Criterio: per ogni slide si calcola la maschera dell'"inchiostro", cioè dei pixel
che si distinguono dallo sfondo (testo, linee, immagini). A è contenuta in B se
quasi nessun pixel di inchiostro di A manca in B:
- se manca qualcosa in entrambe le direzioni → slide diverse;
- se a B non manca nulla di A ma B ha qualcosa in più → A è incompleta;
- se non manca nulla in nessuna direzione → duplicato.
Basta un titolo o un numero diverso perché due slide risultino distinte, mentre
gli artefatti di compressione del video vengono assorbiti da una tolleranza di
un pixel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

import cv2
import numpy as np

# Larghezza di analisi: abbastanza alta da distinguere una singola cifra in un titolo.
ANALYSIS_WIDTH = 960

# Differenza minima di luminosità dallo sfondo perché un pixel sia "inchiostro".
_INK_DELTA = 60

# Inchiostro mancante tollerato, come frazione dell'area (0,01%: ~50 pixel a 960×540).
# Una cifra cambiata nel titolo vale circa 0,02%; il rumore del video resta a 0.
MAX_MISSING = 0.0001

_TOLERANCE_KERNEL = np.ones((3, 3), np.uint8)  # ±1 pixel per piccoli spostamenti
_NOISE_KERNEL = np.ones((2, 2), np.uint8)  # elimina pixel isolati


@dataclass(frozen=True)
class Superseded:
    """La slide è superata da quella in posizione `by` (indice nella sequenza)."""

    kind: Literal["duplicate", "incomplete"]
    by: int


def ink_mask(image: np.ndarray) -> np.ndarray:
    """Maschera booleana dei pixel diversi dallo sfondo, alla larghezza di analisi."""
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = image.shape
    if width != ANALYSIS_WIDTH:
        new_height = max(1, round(height * ANALYSIS_WIDTH / width))
        image = cv2.resize(image, (ANALYSIS_WIDTH, new_height), interpolation=cv2.INTER_AREA)
    background = np.median(image)  # nelle slide lo sfondo occupa la maggior parte dell'area
    return np.abs(image.astype(np.int16) - background) > _INK_DELTA


def missing_fraction(a: np.ndarray, b: np.ndarray) -> float:
    """Frazione dell'area occupata da inchiostro di `a` che non compare in `b`."""
    if a.shape != b.shape:
        return 1.0
    b_wide = cv2.dilate(b.astype(np.uint8), _TOLERANCE_KERNEL)
    missing = (a & (b_wide == 0)).astype(np.uint8)
    missing = cv2.morphologyEx(missing, cv2.MORPH_OPEN, _NOISE_KERNEL)
    return np.count_nonzero(missing) / missing.size


def is_contained(a: np.ndarray, b: np.ndarray) -> bool:
    return missing_fraction(a, b) <= MAX_MISSING


def _load_mask(path: Path) -> np.ndarray | None:
    # np.fromfile + imdecode: cv2.imread non gestisce percorsi non-ASCII su Windows.
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    return None if image is None else ink_mask(image)


def find_superseded(
    paths: Sequence[Path],
    progress: Callable[[int, int], bool] | None = None,
) -> list[Superseded | None]:
    """Per ogni slide indica se è superata da una successiva della stessa serie.

    Una serie è una sequenza di slide consecutive in cui ognuna è contenuta nella
    seguente: tutte vengono segnalate come superate dall'ultima della serie.
    `progress(fatte, totali)` può restituire False per interrompere l'analisi:
    le slide non ancora analizzate restano senza segnalazione.
    """
    count = len(paths)
    result: list[Superseded | None] = [None] * count
    # Serie in corso: solo queste maschere restano in memoria.
    pending: list[tuple[int, np.ndarray]] = []
    previous: np.ndarray | None = None

    def close_series(last: int, last_mask: np.ndarray | None) -> None:
        for index, mask in pending:
            same = last_mask is not None and is_contained(last_mask, mask)
            result[index] = Superseded("duplicate" if same else "incomplete", last)
        pending.clear()

    for i, path in enumerate(paths):
        if progress is not None and not progress(i, count):
            if previous is not None:
                close_series(i - 1, previous)
            return result
        current = _load_mask(path)
        if previous is not None:
            if current is not None and is_contained(previous, current):
                pending.append((i - 1, previous))
            else:
                close_series(i - 1, previous)
        previous = current
    if previous is not None:
        close_series(count - 1, previous)
    if progress is not None:
        progress(count, count)
    return result
