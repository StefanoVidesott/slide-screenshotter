"""Logica di rilevamento del CaptureWorker, senza thread né schermo reale."""

from pathlib import Path

import numpy as np
import pytest

from slide_screenshotter import monitor
from slide_screenshotter.monitor import CaptureWorker, Roi
from slide_screenshotter.storage import SlideStorage


class FakeScreen:
    """Sostituisce mss: restituisce il frame impostato dal test (in BGRA come mss)."""

    def __init__(self) -> None:
        self.frame = self.slide(0)

    @staticmethod
    def slide(rows: int) -> np.ndarray:
        frame = np.full((360, 640, 4), 255, np.uint8)
        for i in range(rows):
            frame[40 + i * 40 : 60 + i * 40, 50:590, :3] = 0
        return frame

    def grab(self, _roi):
        return self.frame


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(monitor.time, "monotonic", clock)
    storage = SlideStorage(tmp_path)
    worker = CaptureWorker(storage)
    worker.set_roi(Roi(0, 0, 640, 360))
    worker.set_threshold(5.0)
    worker.set_cooldown(3.0)
    return worker, storage, FakeScreen(), clock


def test_paused_worker_does_not_capture(setup):
    worker, storage, screen, _ = setup
    worker._tick(screen)
    assert storage.count() == 0


def test_first_frame_saved_then_changes_respect_cooldown(setup):
    worker, storage, screen, clock = setup
    worker.start_monitoring()

    worker._tick(screen)  # prima slide visibile
    assert storage.count() == 1

    screen.frame = screen.slide(5)  # cambio slide durante il cooldown
    clock.now += 1.0
    worker._tick(screen)
    assert storage.count() == 1

    clock.now += 2.5  # cooldown scaduto: lo stato finale viene catturato
    worker._tick(screen)
    assert storage.count() == 2

    clock.now += 5.0  # nessun cambiamento: niente duplicati
    worker._tick(screen)
    assert storage.count() == 2


def test_small_change_below_threshold_ignored(setup):
    worker, storage, screen, clock = setup
    worker.start_monitoring()
    worker._tick(screen)
    frame = screen.slide(0)
    frame[10:20, 10:20, :3] = 0  # "cursore": area minuscola
    screen.frame = frame
    clock.now += 10
    worker._tick(screen)
    assert storage.count() == 1


def test_manual_capture_works_while_paused(setup):
    worker, storage, screen, _ = setup
    worker.request_manual_capture()
    worker._tick(screen)
    assert storage.count() == 1
    assert worker._in_cooldown


def test_reference_reset_prevents_recapture_after_undo(setup):
    worker, storage, screen, clock = setup
    worker.start_monitoring()
    worker._tick(screen)
    screen.frame = screen.slide(5)
    clock.now += 5
    worker._tick(screen)
    assert storage.count() == 2

    storage.delete_last()  # falso positivo annullato dall'utente
    worker.request_reference_reset()
    clock.now += 5
    worker._tick(screen)
    clock.now += 5
    worker._tick(screen)
    assert storage.count() == 1


def test_saved_frame_drops_alpha(setup):
    worker, storage, screen, _ = setup
    worker.request_manual_capture()
    worker._tick(screen)
    import cv2

    img = cv2.imread(str(storage.list_slides()[0]), cv2.IMREAD_UNCHANGED)
    assert img.shape == (360, 640, 3)
