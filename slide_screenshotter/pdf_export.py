"""Esportazione delle slide selezionate in un unico PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import img2pdf


class ExportError(Exception):
    """Esportazione PDF non riuscita."""


def export_pdf(images: Sequence[Path], output: Path) -> Path:
    """Unisce le immagini (una per pagina, nell'ordine dato) in `output`.

    img2pdf incorpora i PNG senza ricompressione, quindi la qualità resta identica
    agli screenshot originali.
    """
    if not images:
        raise ExportError("Nessuna immagine selezionata per l'esportazione.")
    missing = [p for p in images if not Path(p).is_file()]
    if missing:
        names = ", ".join(p.name for p in missing[:5])
        raise ExportError(f"File non trovati: {names}")

    output = Path(output)
    try:
        pdf_bytes = img2pdf.convert([str(p) for p in images])
        output.write_bytes(pdf_bytes)
    except (OSError, img2pdf.ImageOpenError, ValueError) as exc:
        raise ExportError(f"Errore durante la creazione del PDF: {exc}") from exc
    return output
