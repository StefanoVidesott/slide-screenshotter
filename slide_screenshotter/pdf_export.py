"""Esportazione delle slide selezionate in un unico PDF, con testo ricercabile opzionale."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence

import img2pdf
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError

# Lingue preferite per l'OCR, se installate in Tesseract.
PREFERRED_LANGUAGES = ("ita", "eng")

# Timeout per singola pagina: evita che un processo bloccato congeli l'esportazione.
_OCR_TIMEOUT = 120

# Su Windows (pythonw.exe) ogni sottoprocesso aprirebbe una finestra console.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ExportError(Exception):
    """Esportazione PDF non riuscita."""


class ExportCancelled(ExportError):
    """Esportazione interrotta dall'utente."""


@dataclass(frozen=True)
class OcrConfig:
    """Eseguibile di Tesseract e lingue da usare (es. "ita+eng")."""

    tesseract: Path
    languages: str


@lru_cache(maxsize=1)
def find_tesseract() -> Path | None:
    """Cerca l'eseguibile di Tesseract nel PATH e nei percorsi di installazione di Windows."""
    found = shutil.which("tesseract")
    if found:
        return Path(found)
    if sys.platform == "win32":
        for base in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = os.environ.get(base)
            if not root:
                continue
            for sub in ("Tesseract-OCR", r"Programs\Tesseract-OCR"):
                candidate = Path(root) / sub / "tesseract.exe"
                if candidate.is_file():
                    return candidate
    return None


@lru_cache(maxsize=4)
def tesseract_languages(tesseract: Path) -> tuple[str, ...]:
    """Lingue installate (esclusa "osd", che rileva solo l'orientamento)."""
    try:
        completed = subprocess.run(
            [str(tesseract), "--list-langs"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    # La prima riga è un'intestazione ("List of available languages in ...").
    lines = completed.stdout.splitlines()[1:]
    return tuple(line.strip() for line in lines if line.strip() and line.strip() != "osd")


def default_ocr_config() -> OcrConfig | None:
    """Configurazione OCR automatica, o None se Tesseract non è disponibile."""
    tesseract = find_tesseract()
    if tesseract is None:
        return None
    available = tesseract_languages(tesseract)
    if not available:
        return None
    chosen = [lang for lang in PREFERRED_LANGUAGES if lang in available] or list(available)
    return OcrConfig(tesseract, "+".join(chosen))


def export_pdf(
    images: Sequence[Path],
    output: Path,
    ocr: OcrConfig | None = None,
    progress: Callable[[int, int], bool] | None = None,
) -> Path:
    """Unisce le immagini (una per pagina, nell'ordine dato) in `output`.

    img2pdf incorpora i PNG senza ricompressione, quindi la qualità resta identica
    agli screenshot originali. Con `ocr`, a ogni pagina viene sovrapposto un livello
    di testo invisibile prodotto da Tesseract: il PDF diventa ricercabile e il testo
    si può selezionare e copiare.

    `progress(fatte, totali)` viene chiamata per ogni pagina; se restituisce False
    l'esportazione si interrompe con ExportCancelled e `output` non viene scritto.
    """
    if not images:
        raise ExportError("Nessuna immagine selezionata per l'esportazione.")
    images = [Path(p) for p in images]
    missing = [p for p in images if not p.is_file()]
    if missing:
        names = ", ".join(p.name for p in missing[:5])
        raise ExportError(f"File non trovati: {names}")

    output = Path(output)
    try:
        pdf_bytes = img2pdf.convert([str(p) for p in images])
    except (OSError, img2pdf.ImageOpenError, ValueError) as exc:
        raise ExportError(f"Errore durante la creazione del PDF: {exc}") from exc

    if ocr is not None:
        pdf_bytes = _add_text_layer(pdf_bytes, images, ocr, progress)
    elif progress is not None:
        progress(len(images), len(images))

    try:
        output.write_bytes(pdf_bytes)
    except OSError as exc:
        raise ExportError(f"Impossibile scrivere {output}: {exc}") from exc
    return output


def _add_text_layer(
    pdf_bytes: bytes,
    images: Sequence[Path],
    ocr: OcrConfig,
    progress: Callable[[int, int], bool] | None,
) -> bytes:
    try:
        # Le pagine vanno modificate dopo essere state copiate nel writer.
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
        for i, (page, image) in enumerate(zip(writer.pages, images)):
            if progress is not None and not progress(i, len(images)):
                raise ExportCancelled("Esportazione annullata.")
            text_page = PdfReader(io.BytesIO(_ocr_page(image, ocr))).pages[0]
            # Tesseract e img2pdf possono assumere risoluzioni diverse:
            # si adatta il livello di testo alle dimensioni della pagina immagine.
            sx = float(page.mediabox.width) / float(text_page.mediabox.width)
            sy = float(page.mediabox.height) / float(text_page.mediabox.height)
            page.merge_transformed_page(text_page, Transformation().scale(sx, sy))
        if progress is not None:
            progress(len(images), len(images))
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
    except PyPdfError as exc:
        raise ExportError(f"Errore durante l'unione del testo OCR: {exc}") from exc


def _ocr_page(image: Path, ocr: OcrConfig) -> bytes:
    """PDF di una pagina con il solo testo invisibile riconosciuto nell'immagine."""
    # L'immagine passa da stdin: Tesseract su Windows non apre percorsi non-ASCII.
    command = [
        str(ocr.tesseract),
        "stdin",
        "stdout",
        "-l",
        ocr.languages,
        "--dpi",
        "96",
        # Opzioni esplicite invece del file di configurazione "pdf", che non
        # sempre è installato insieme alle lingue.
        "-c",
        "tessedit_create_pdf=1",
        "-c",
        "tessedit_create_txt=0",
        "-c",
        "textonly_pdf=1",
    ]
    try:
        completed = subprocess.run(
            command,
            input=image.read_bytes(),
            capture_output=True,
            timeout=_OCR_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExportError(f"OCR troppo lento su {image.name}") from exc
    except OSError as exc:
        raise ExportError(f"Impossibile avviare Tesseract: {exc}") from exc
    if completed.returncode != 0 or not completed.stdout.startswith(b"%PDF"):
        detail = completed.stderr.decode(errors="replace").strip().splitlines()
        raise ExportError(f"OCR fallito su {image.name}: {detail[-1] if detail else 'errore sconosciuto'}")
    return completed.stdout
