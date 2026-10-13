<#
.SYNOPSIS
    Installa Slide Screenshotter in un venv locale e crea i collegamenti.

.DESCRIPTION
    1. Crea .venv nella cartella del progetto (python -m venv) se non esiste.
    2. Installa le dipendenze con pip da requirements.txt.
    3. Crea i collegamenti "Slide Screenshotter" sul Desktop e nel Menu Start,
       che avviano .venv\Scripts\pythonw.exe -m slide_screenshotter (senza console).

.PARAMETER ShortcutsOnly
    Salta venv e pip: ricrea solo i collegamenti.

.PARAMETER Uninstall
    Rimuove i collegamenti (non tocca .venv ne' le slide salvate).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
param(
    [switch]$ShortcutsOnly,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$AppName = "Slide Screenshotter"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvPythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Icon = Join-Path $Root "slide_screenshotter\assets\icon.ico"
$ShortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk")
)

function Invoke-Checked {
    # Esegue un comando esterno e si ferma se fallisce (ErrorActionPreference non copre gli exe).
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando fallito ($LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Find-Python {
    # Preferisce il launcher "py" (installer ufficiale di python.org), poi "python" nel PATH.
    # Il segnaposto WindowsApps\python.exe (alias del Microsoft Store) viene ignorato.
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @("py", "-3") }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\WindowsApps\*") { $candidates += , @($python.Source) }

    foreach ($cmd in $candidates) {
        $exe = $cmd[0]
        $prefix = @($cmd | Select-Object -Skip 1)
        try {
            # In PowerShell 5.1 l'output su stderr di un exe con ErrorActionPreference=Stop
            # diventa un'eccezione: un candidato non funzionante va semplicemente saltato.
            $version = & $exe @prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]"3.11") {
            return , $cmd
        }
    }
    throw "Python 3.11+ non trovato. Installalo da https://www.python.org/downloads/ (spunta 'Add python.exe to PATH')."
}

function New-Shortcuts {
    if (-not (Test-Path $VenvPythonW)) {
        throw "$VenvPythonW non trovato: esegui prima lo script senza -ShortcutsOnly."
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($path in $ShortcutPaths) {
        $lnk = $shell.CreateShortcut($path)
        $lnk.TargetPath = $VenvPythonW
        $lnk.Arguments = "-m slide_screenshotter"
        # La cartella di lavoro rende importabile il package e decide dove finisce temp_slides\.
        $lnk.WorkingDirectory = $Root
        $lnk.IconLocation = "$Icon,0"
        $lnk.Description = "Cattura automatica delle slide durante le lezioni online"
        $lnk.Save()
        Write-Host "Collegamento creato: $path"
    }
}

if ($Uninstall) {
    foreach ($path in $ShortcutPaths) {
        if (Test-Path $path) {
            Remove-Item $path
            Write-Host "Rimosso: $path"
        }
    }
    exit 0
}

if (-not $ShortcutsOnly) {
    if (-not (Test-Path $VenvPython)) {
        $python = Find-Python
        $exe = $python[0]
        $prefix = @($python | Select-Object -Skip 1)
        Write-Host "Creo il virtualenv in $Root\.venv ..."
        Invoke-Checked $exe ($prefix + @("-m", "venv", (Join-Path $Root ".venv")))
    }
    Write-Host "Installo le dipendenze ..."
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", (Join-Path $Root "requirements.txt"))
}

New-Shortcuts
Write-Host ""
Write-Host "Fatto. Avvia '$AppName' dal Desktop o dal Menu Start."
