# Slide Screenshotter

Cattura automatica delle slide durante le lezioni online (Zoom, Teams, Meet…).
Monitora un'area dello schermo e salva uno screenshot ogni volta che la slide cambia.
Alla fine puoi scegliere quali scatti tenere ed esportarli in un PDF.

Piattaforma principale: **Windows 10/11**. Funziona anche su macOS e Linux X11.
Su Linux Wayland `mss` non riesce a catturare lo schermo.

## Installazione (Windows)

Serve Python 3.11+ da [python.org](https://www.python.org/downloads/).

**Automatica:** doppio clic su `install.bat`. Lo script:
1. crea `.venv` con `python -m venv`;
2. installa le dipendenze con `pip`;
3. crea il collegamento **Slide Screenshotter** sul Desktop e nel Menu Start.

Il collegamento avvia `.venv\Scripts\pythonw.exe -m slide_screenshotter`, senza finestra console.
Le slide finiscono in `temp_slides\` dentro la cartella del progetto.

Altre opzioni (da PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShortcutsOnly  # ricrea solo i collegamenti
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall      # rimuove i collegamenti
```

Se sposti la cartella del progetto, rilancia `install.bat`: venv e collegamenti contengono percorsi assoluti.

**Manuale:**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m slide_screenshotter            # cartella temporanea: .\temp_slides
python -m slide_screenshotter D:\lezioni # cartella temporanea personalizzata
```

### Eseguibile standalone (opzionale)

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name SlideScreenshotter --icon slide_screenshotter\assets\icon.ico --add-data "slide_screenshotter\assets;slide_screenshotter\assets" run.py
```

L'eseguibile si trova in `dist\SlideScreenshotter.exe`. Crea `temp_slides\` accanto a sé.

### OCR (opzionale)

Per avere PDF con testo ricercabile e copiabile serve [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki).
Durante l'installazione spunta le lingue **Italian** (e **English** se servono slide in inglese).
L'app lo trova nel PATH o in `C:\Program Files\Tesseract-OCR`. Senza Tesseract l'opzione OCR resta disattivata.

## Uso

1. **Seleziona ROI**: trascina un rettangolo sull'area delle slide. Esc o clic destro annullano.
   L'ultima area usata viene ripristinata al prossimo avvio.
   Con **Salva…** la memorizzi come profilo (es. "Teams", "Zoom") da richiamare dal menu **Profilo**.
2. **Start**: viene salvata subito la slide visibile, poi ogni cambio di slide.
3. **Soglia**: è la percentuale di area che deve cambiare per salvare.
   Durante il monitoraggio il pannello mostra la "Differenza attuale", utile per tararla.
   Il default è 5%. Abbassala se perde slide con poche modifiche, alzala se il video del relatore causa falsi scatti.
4. **Scatto manuale** (Ctrl+Shift+S) e **Annulla scatto** (Ctrl+Z).
   Dopo un annullamento la schermata attuale diventa il riferimento, così il falso positivo non viene ricatturato.
5. **Scorciatoie globali** (solo Windows), attive anche mentre guardi la lezione:
   **Ctrl+Shift+F9** scatto manuale, **Ctrl+Shift+F10** avvia/pausa.
   Se un'altra app le usa già, il pannello lo segnala.
6. **Esporta PDF**: mette in pausa e apre la galleria.
   - Clic seleziona, Shift/Ctrl+clic per selezioni multiple, Ctrl+A tutte.
   - Spazio o doppio clic includono/escludono dal PDF le slide selezionate (anche dalla checkbox o dal menu del tasto destro).
   - Trascina per riordinare: l'ordine viene salvato anche su disco.
   - Canc elimina definitivamente i file selezionati.
   - **Trova duplicati** esclude le slide ripetute e quelle incomplete (elenchi che compaiono una voce alla volta), tenendo l'ultima di ogni serie. Sono segnate in arancione: controllale prima di esportare.
   - Zoom con lo slider, Ctrl+rotellina o Ctrl +/−.
   - **Testo ricercabile (OCR)** aggiunge al PDF un livello di testo invisibile (richiede Tesseract).

Soglia, intervallo, cooldown, area, profili, zoom della galleria, OCR e cartella di esportazione vengono ricordati tra una sessione e l'altra
(su Windows nel registro, in `HKCU\Software\SlideScreenshotter`).

Overlay in alto a destra (trascinabile): numero di slide e puntino di stato.
Verde = pronto, rosso = cooldown, grigio = in pausa.
Su Windows 10 2004+ l'overlay è escluso dagli screenshot.

## Struttura

| Modulo | Ruolo |
|---|---|
| `image_diff.py` | Preprocessing e calcolo della differenza percentuale tra frame |
| `monitor.py` | `CaptureWorker` (QThread): loop di cattura, soglia, cooldown, scatto manuale |
| `storage.py` | `SlideStorage`: cartella temporanea thread-safe |
| `pdf_export.py` | Unione dei PNG in PDF con img2pdf (senza ricompressione), livello OCR con Tesseract + pypdf |
| `duplicates.py` | Riconoscimento di slide duplicate o incomplete tra scatti consecutivi |
| `settings.py` | Impostazioni persistenti e profili dell'area (QSettings) |
| `ui/` | Pannello di controllo, overlay, selettore ROI, galleria, scorciatoie globali |

Test: `pip install -r requirements-dev.txt`, poi `pytest`.
