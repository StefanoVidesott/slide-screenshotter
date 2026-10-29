"""Gestione della cartella temporanea delle slide."""

from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

_SLIDE_PATTERN = re.compile(r"^slide_(\d{4,})_(\d{8}-\d{6})\.png$")
_REORDER_SUFFIX = ".reorder"


class StorageError(Exception):
    """Errore di lettura/scrittura nella cartella temporanea."""


class SlideStorage:
    """Salva, elenca ed elimina gli screenshot in una cartella su disco.

    I file sono nominati `slide_<indice>_<timestamp>.png`: l'indice progressivo
    garantisce l'ordine cronologico anche se l'orologio di sistema cambia.
    I metodi sono thread-safe perché il salvataggio avviene nel thread di cattura
    mentre annullamento e pulizia avvengono nel thread della GUI.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Impossibile creare la cartella {self.directory}: {exc}") from exc

    def list_slides(self) -> list[Path]:
        """Slide presenti, in ordine cronologico."""
        with self._lock:
            return self._list_unlocked()

    def count(self) -> int:
        return len(self.list_slides())

    def save(self, frame_bgr: np.ndarray) -> Path:
        """Salva un frame BGR come PNG e restituisce il percorso."""
        with self._lock:
            slides = self._list_unlocked()
            next_index = self._index_of(slides[-1]) + 1 if slides else 1
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.directory / f"slide_{next_index:04d}_{timestamp}.png"
            # cv2.imwrite non gestisce percorsi non-ASCII su Windows (es. C:\Users\Niccolò):
            # codifichiamo in memoria e scriviamo con pathlib.
            ok, encoded = cv2.imencode(".png", frame_bgr)
            if not ok:
                raise StorageError(f"Codifica PNG fallita: {path.name}")
            try:
                path.write_bytes(encoded.tobytes())
            except OSError as exc:
                raise StorageError(f"Scrittura fallita: {path}: {exc}") from exc
            return path

    def import_images(self, paths: Sequence[Path]) -> tuple[list[Path], list[str]]:
        """Aggiunge in coda immagini esistenti (screenshot, foto...) come nuove slide.

        Restituisce (nuovi percorsi, nomi dei file non leggibili).
        """
        added: list[Path] = []
        failed: list[str] = []
        for source in paths:
            source = Path(source)
            try:
                # imdecode + pathlib: cv2.imread non gestisce percorsi non-ASCII su Windows.
                data = np.frombuffer(source.read_bytes(), dtype=np.uint8)
                frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
            except OSError:
                frame = None
            if frame is None:
                failed.append(source.name)
                continue
            added.append(self.save(frame))
        return added, failed

    def delete_last(self) -> Path | None:
        """Elimina l'ultima slide salvata. Restituisce il file eliminato o None."""
        with self._lock:
            slides = self._list_unlocked()
            if not slides:
                return None
            last = slides[-1]
            try:
                last.unlink()
            except OSError as exc:
                raise StorageError(f"Impossibile eliminare {last.name}: {exc}") from exc
            return last

    def delete(self, paths: Sequence[Path]) -> int:
        """Elimina le slide indicate. Restituisce il numero di file eliminati."""
        with self._lock:
            removed = 0
            for path in paths:
                try:
                    Path(path).unlink()
                    removed += 1
                except FileNotFoundError:
                    pass  # già eliminata: il risultato è comunque quello voluto
                except OSError as exc:
                    raise StorageError(f"Impossibile eliminare {Path(path).name}: {exc}") from exc
            return removed

    def reorder(self, paths: Sequence[Path]) -> list[Path]:
        """Rinomina le slide perché l'ordine su disco sia quello di `paths`.

        Gli indici diventano 1..n; il timestamp nel nome (e la data di modifica)
        restano quelli originali. Restituisce i nuovi percorsi, nello stesso ordine.
        """
        with self._lock:
            paths = [Path(p) for p in paths]
            current = set(self._list_unlocked())
            unknown = [p.name for p in paths if p not in current]
            if unknown:
                raise StorageError(f"Slide non trovate: {', '.join(unknown[:5])}")
            targets = [
                self.directory / f"slide_{i:04d}_{self._timestamp_of(p)}.png"
                for i, p in enumerate(paths, start=1)
            ]
            ours = set(paths)
            clashes = [t.name for t in targets if t.exists() and t not in ours]
            if clashes:
                raise StorageError(f"Nomi già in uso: {', '.join(clashes[:5])}")

            # Due passaggi (nome temporaneo → nome finale): uno scambio di posizione
            # tra due slide non deve mai sovrascrivere un file.
            moved: list[tuple[Path, Path]] = []  # (originale, temporaneo)
            try:
                for path in paths:
                    temp = path.with_name(path.name + _REORDER_SUFFIX)
                    path.rename(temp)
                    moved.append((path, temp))
                for (_, temp), target in zip(moved, targets):
                    temp.rename(target)
            except OSError as exc:
                for original, temp in moved:
                    try:
                        if temp.exists():
                            temp.rename(original)
                    except OSError:
                        pass
                raise StorageError(f"Riordino non riuscito: {exc}") from exc
            return targets

    def clear(self) -> int:
        """Elimina tutte le slide. Restituisce il numero di file eliminati."""
        with self._lock:
            removed = 0
            for path in self._list_unlocked():
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    raise StorageError(f"Impossibile eliminare {path.name}: {exc}") from exc
            return removed

    def _list_unlocked(self) -> list[Path]:
        slides = [p for p in self.directory.iterdir() if _SLIDE_PATTERN.match(p.name)]
        return sorted(slides, key=self._index_of)

    @staticmethod
    def _index_of(path: Path) -> int:
        match = _SLIDE_PATTERN.match(path.name)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _timestamp_of(path: Path) -> str:
        match = _SLIDE_PATTERN.match(path.name)
        return match.group(2) if match else datetime.now().strftime("%Y%m%d-%H%M%S")
