from pathlib import Path

import numpy as np
import pytest

from slide_screenshotter.image_diff import difference_percent, preprocess
from slide_screenshotter.pdf_export import ExportError, export_pdf
from slide_screenshotter.storage import SlideStorage


def _slide(text_rows: int = 0, noise: int = 0, seed: int = 0) -> np.ndarray:
    """Slide finta 1280x720: sfondo bianco con `text_rows` barre nere tipo testo."""
    frame = np.full((720, 1280, 3), 255, np.uint8)
    for i in range(text_rows):
        frame[80 + i * 60 : 110 + i * 60, 100:1100] = 0
    if noise:
        rng = np.random.default_rng(seed)
        jitter = rng.integers(-noise, noise + 1, frame.shape)
        frame = np.clip(frame.astype(int) + jitter, 0, 255).astype(np.uint8)
    return frame


def test_identical_frames_have_zero_difference():
    a = preprocess(_slide(3))
    assert difference_percent(a, a.copy()) == 0.0


def test_compression_noise_stays_below_threshold():
    a = preprocess(_slide(3, noise=8, seed=1))
    b = preprocess(_slide(3, noise=8, seed=2))
    assert difference_percent(a, b) < 0.5


def test_new_content_exceeds_threshold():
    a = preprocess(_slide(3))
    b = preprocess(_slide(6))
    assert difference_percent(a, b) > 5.0


def test_different_shapes_count_as_full_change():
    a = preprocess(_slide())
    b = preprocess(np.zeros((100, 100, 3), np.uint8))
    assert difference_percent(a, b) == 100.0


def test_storage_save_order_and_undo(tmp_path: Path):
    storage = SlideStorage(tmp_path / "slides")
    first = storage.save(_slide(1))
    second = storage.save(_slide(2))
    assert storage.list_slides() == [first, second]
    assert storage.delete_last() == second
    third = storage.save(_slide(3))
    assert third.name.startswith("slide_0002_")
    assert storage.count() == 2
    assert storage.clear() == 2
    assert storage.delete_last() is None


def test_storage_ignores_foreign_files(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("x")
    storage = SlideStorage(tmp_path)
    assert storage.list_slides() == []


def test_export_pdf(tmp_path: Path):
    storage = SlideStorage(tmp_path)
    paths = [storage.save(_slide(i)) for i in range(3)]
    out = export_pdf(paths, tmp_path / "out.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert b"/Count 3" in data


def test_export_empty_raises(tmp_path: Path):
    with pytest.raises(ExportError):
        export_pdf([], tmp_path / "out.pdf")


def test_export_missing_file_raises(tmp_path: Path):
    with pytest.raises(ExportError):
        export_pdf([tmp_path / "missing.png"], tmp_path / "out.pdf")
