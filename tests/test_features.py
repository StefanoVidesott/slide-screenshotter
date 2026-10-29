"""Riordino/eliminazione, duplicati, impostazioni, scorciatoie, OCR e galleria."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from slide_screenshotter.duplicates import Superseded, find_superseded, ink_mask, is_contained
from slide_screenshotter.monitor import Roi
from slide_screenshotter.pdf_export import default_ocr_config, export_pdf
from slide_screenshotter.settings import AppSettings, roi_from_text, roi_to_text
from slide_screenshotter.storage import SlideStorage, StorageError
from slide_screenshotter.ui import gallery as gallery_module
from slide_screenshotter.ui.hotkeys import to_win32


def slide(rows: int, title_offset: int = 0) -> np.ndarray:
    """Slide bianca con un titolo e `rows` righe di elenco (come una build progressiva)."""
    frame = np.full((360, 640, 3), 255, np.uint8)
    cv2.rectangle(frame, (40 + title_offset, 20), (400 + title_offset, 50), (0, 0, 0), -1)
    for i in range(rows):
        cv2.rectangle(frame, (60, 80 + i * 40), (560, 100 + i * 40), (0, 0, 0), -1)
    return frame


@pytest.fixture
def storage(tmp_path: Path) -> SlideStorage:
    return SlideStorage(tmp_path / "slides")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ----- Storage -----


def test_reorder_renames_in_new_order_keeping_timestamps(storage: SlideStorage) -> None:
    a, b, c = (storage.save(slide(i)) for i in range(3))
    contents = {p: p.read_bytes() for p in (a, b, c)}

    new_paths = storage.reorder([c, a, b])

    assert [p.name[:10] for p in new_paths] == ["slide_0001", "slide_0002", "slide_0003"]
    assert storage.list_slides() == new_paths
    assert [p.read_bytes() for p in new_paths] == [contents[c], contents[a], contents[b]]
    assert [p.name[11:] for p in new_paths] == [c.name[11:], a.name[11:], b.name[11:]]


def test_reorder_rejects_unknown_files(storage: SlideStorage, tmp_path: Path) -> None:
    storage.save(slide(1))
    with pytest.raises(StorageError):
        storage.reorder([tmp_path / "slides" / "slide_0099_20260101-000000.png"])


def test_new_capture_after_reorder_goes_last(storage: SlideStorage) -> None:
    first, second = storage.save(slide(1)), storage.save(slide(2))
    storage.reorder([second, first])
    third = storage.save(slide(3))
    assert storage.list_slides()[-1] == third


def test_delete_removes_only_given_files(storage: SlideStorage) -> None:
    a, b, c = (storage.save(slide(i)) for i in range(3))
    assert storage.delete([a, c]) == 2
    assert storage.list_slides() == [b]
    assert storage.delete([a]) == 0  # già eliminata: nessun errore


# ----- Duplicati -----


def text_slide(title: str, rows: int) -> np.ndarray:
    frame = np.full((540, 960, 3), 250, np.uint8)
    cv2.putText(frame, title, (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (40, 40, 40), 3)
    for i in range(rows):
        cv2.putText(frame, f"- punto {i + 1}", (70, 150 + i * 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
    return frame


def jpeg(frame: np.ndarray, quality: int) -> np.ndarray:
    return cv2.imdecode(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])[1], 1)


def test_progressive_build_is_contained_in_next_slide() -> None:
    partial, full = ink_mask(slide(2)), ink_mask(slide(4))
    assert is_contained(partial, full)
    assert not is_contained(full, partial)


def test_video_compression_noise_is_tolerated() -> None:
    build = jpeg(text_slide("Intro", 2), 40)
    full = cv2.resize(jpeg(text_slide("Intro", 3), 60), (1920, 1080))
    assert is_contained(ink_mask(build), ink_mask(full))


@pytest.mark.parametrize(
    ("first", "second"),
    [("Metodi", "Risultati"), ("Capitolo 2: metodi", "Capitolo 3: metodi")],
)
def test_same_body_but_different_title_is_a_new_slide(first: str, second: str) -> None:
    # Stesso elenco ma titolo diverso: la seconda non "completa" la prima.
    assert not is_contained(ink_mask(text_slide(first, 2)), ink_mask(text_slide(second, 3)))


def test_find_superseded_groups_builds_and_duplicates(tmp_path: Path) -> None:
    frames = [
        slide(1),  # 0: build → superata da 2
        slide(2),  # 1: build → superata da 2
        slide(3),  # 2: completa
        slide(3),  # 3: duplicato di 4
        slide(3),  # 4: ultima della serie
        slide(1, title_offset=180),  # 5: slide nuova (titolo diverso)
    ]
    paths = []
    for i, frame in enumerate(frames):
        path = tmp_path / f"{i}.png"
        cv2.imwrite(str(path), frame)
        paths.append(path)

    result = find_superseded(paths)

    # 2 contiene 0 e 1; 2, 3 e 4 sono identiche → l'ultima della serie è 4.
    assert result[0] == Superseded("incomplete", 4)
    assert result[1] == Superseded("incomplete", 4)
    assert result[2] == Superseded("duplicate", 4)
    assert result[3] == Superseded("duplicate", 4)
    assert result[4] is None
    assert result[5] is None


def test_find_superseded_can_be_cancelled(tmp_path: Path) -> None:
    paths = []
    for i in range(4):
        path = tmp_path / f"{i}.png"
        cv2.imwrite(str(path), slide(i))
        paths.append(path)
    result = find_superseded(paths, progress=lambda done, _total: done < 2)
    assert len(result) == 4
    assert result[2] is None and result[3] is None


# ----- Impostazioni -----


def test_settings_roundtrip(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings = AppSettings(store)
    settings.threshold = 7.5
    settings.ocr_enabled = False
    settings.roi = Roi(10, 20, 300, 200)
    settings.save_roi_profile("Teams / lezione", Roi(0, 0, 100, 50))
    settings.save_roi_profile("Zoom", Roi(5, 5, 10, 10))
    settings.delete_roi_profile("Zoom")
    store.sync()

    reloaded = AppSettings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert reloaded.threshold == 7.5
    assert reloaded.ocr_enabled is False
    assert reloaded.roi == Roi(10, 20, 300, 200)
    assert reloaded.roi_profiles() == {"Teams / lezione": Roi(0, 0, 100, 50)}


def test_roi_text_parsing() -> None:
    assert roi_from_text(roi_to_text(Roi(-1920, 0, 800, 600))) == Roi(-1920, 0, 800, 600)
    assert roi_from_text("1,2,0,5") is None
    assert roi_from_text("rotto") is None


# ----- Scorciatoie -----


def test_hotkey_conversion_to_win32() -> None:
    mods, vk = to_win32("Ctrl+Alt+S")
    assert vk == ord("S")
    assert mods & 0x0002 and mods & 0x0001  # MOD_CONTROL, MOD_ALT
    assert to_win32("Ctrl+Shift+F9")[1] == 0x78
    assert to_win32("") is None


# ----- OCR -----


def test_ocr_adds_searchable_text(tmp_path: Path) -> None:
    config = default_ocr_config()
    if config is None or "eng" not in config.languages.split("+"):
        pytest.skip("Tesseract con lingua inglese non installato")
    from pypdf import PdfReader

    image = np.full((720, 1280, 3), 255, np.uint8)
    cv2.putText(image, "Searchable lecture", (80, 360), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 5)
    path = tmp_path / "slide.png"
    cv2.imwrite(str(path), image)

    output = export_pdf([path], tmp_path / "out.pdf", config)

    plain = export_pdf([path], tmp_path / "plain.pdf")
    page = PdfReader(output).pages[0]
    assert "lecture" in page.extract_text().lower()
    assert page.mediabox == PdfReader(plain).pages[0].mediabox


# ----- Galleria -----


@pytest.fixture
def gallery(app, storage: SlideStorage, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gallery_module.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        gallery_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    for i in range(1, 5):
        storage.save(slide(i, title_offset=60 * i))
    settings = AppSettings(QSettings(str(tmp_path / "g.ini"), QSettings.Format.IniFormat))
    dialog = gallery_module.GalleryDialog(storage, settings)
    dialog.resize(1000, 700)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.close()


def _paths(dialog) -> list[Path]:
    return [dialog._list.item(r).data(gallery_module._PathRole) for r in range(dialog._list.count())]


def test_gallery_drag_reorder_is_saved_on_disk(gallery, storage: SlideStorage) -> None:
    before = [p.read_bytes() for p in storage.list_slides()]
    gallery._list.item(3).setSelected(True)

    assert gallery._list.move_selected(0)

    after = [p.read_bytes() for p in storage.list_slides()]
    assert after == [before[3], before[0], before[1], before[2]]
    assert _paths(gallery) == storage.list_slides()


def test_gallery_multi_selection_toggle_and_delete(gallery, storage: SlideStorage) -> None:
    gallery._list.selectAll()
    gallery._toggle_selected()
    assert gallery._included_paths() == []
    gallery._toggle_selected()
    assert len(gallery._included_paths()) == 4

    gallery._list.clearSelection()
    gallery._list.item(1).setSelected(True)
    gallery._list.item(2).setSelected(True)
    removed = _paths(gallery)[1:3]
    gallery._delete_selected()

    assert gallery._list.count() == 2
    assert not any(p.exists() for p in removed)
    assert storage.count() == 2


def test_gallery_duplicates_are_excluded(app, storage: SlideStorage, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(gallery_module.QMessageBox, "information", lambda *a, **k: None)
    for rows in (1, 2, 3):
        storage.save(slide(rows))
    settings = AppSettings(QSettings(str(tmp_path / "d.ini"), QSettings.Format.IniFormat))
    dialog = gallery_module.GalleryDialog(storage, settings)

    dialog._find_duplicates()

    checked = [dialog._list.item(r).checkState() == Qt.CheckState.Checked for r in range(3)]
    assert checked == [False, False, True]
    assert dialog._included_paths() == [storage.list_slides()[-1]]


def test_gallery_zoom_is_remembered(gallery) -> None:
    gallery._zoom_by(80)
    assert gallery._settings.gallery_zoom == 320
    gallery._zoom_by(-10_000)
    assert gallery._settings.gallery_zoom == gallery_module._THUMB_MIN


def test_gallery_drop_position_follows_reading_order(gallery) -> None:
    lst = gallery._list
    first = lst.visualRect(lst.indexFromItem(lst.item(0)))
    last = lst.visualRect(lst.indexFromItem(lst.item(lst.count() - 1)))
    assert lst.drop_row_at(first.center() - QPoint(10, 0)) == 0
    assert lst.drop_row_at(first.center() + QPoint(10, 0)) == 1
    assert lst.drop_row_at(last.bottomRight() + QPoint(0, 50)) == lst.count()


def test_gallery_import_images_appends_slides(gallery, storage: SlideStorage, tmp_path: Path) -> None:
    good = tmp_path / "scrèenshot.png"
    cv2.imwrite(str(tmp_path / "a.png"), np.full((40, 60, 3), 200, np.uint8))
    (tmp_path / "a.png").rename(good)
    bad = tmp_path / "not_an_image.png"
    bad.write_bytes(b"nope")
    before = gallery._list.count()

    gallery._import_paths([good])
    assert gallery._list.count() == before + 1
    assert _paths(gallery) == storage.list_slides()

    added, failed = storage.import_images([good, bad])
    assert len(added) == 1 and failed == ["not_an_image.png"]
    assert storage.list_slides()[-1] == added[0]


def test_gallery_undo_delete_reorder_and_import(gallery, storage: SlideStorage, tmp_path: Path) -> None:
    original = [p.read_bytes() for p in storage.list_slides()]

    gallery._list.item(1).setSelected(True)
    gallery._delete_selected()
    assert len(storage.list_slides()) == len(original) - 1
    gallery._undo_last()
    assert [p.read_bytes() for p in storage.list_slides()] == original
    assert _paths(gallery) == storage.list_slides()

    gallery._list.clearSelection()
    gallery._list.item(3).setSelected(True)
    gallery._list.move_selected(0)
    assert [p.read_bytes() for p in storage.list_slides()] != original
    gallery._undo_last()
    assert [p.read_bytes() for p in storage.list_slides()] == original
    assert _paths(gallery) == storage.list_slides()

    image = tmp_path / "extra.png"
    cv2.imwrite(str(image), np.full((40, 60, 3), 90, np.uint8))
    gallery._import_paths([image])
    assert len(storage.list_slides()) == len(original) + 1
    gallery._undo_last()
    assert [p.read_bytes() for p in storage.list_slides()] == original
    assert not gallery._undo_button.isEnabled()


def test_gallery_undo_keeps_exclusion_of_restored_slide(gallery, storage: SlideStorage) -> None:
    gallery._set_included([gallery._list.item(0)], False)
    gallery._list.item(0).setSelected(True)
    gallery._delete_selected()
    gallery._undo_last()
    assert gallery._list.item(0).checkState() == Qt.CheckState.Unchecked
