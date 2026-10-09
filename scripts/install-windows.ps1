# vid2guide installer for Windows 10/11.
# Launched by install-windows.bat (double-click). Safe to re-run: every step
# skips what is already installed. Compatible with Windows PowerShell 5.1.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Log = Join-Path $Root "install.log"
"" | Out-File -FilePath $Log -Encoding utf8

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor White }
function Ok($msg)   { Write-Host "    OK $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    ATTENZIONE $msg" -ForegroundColor Yellow }

# winget installs do not update PATH in the current session: reload it.
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# Native commands do not throw on failure: check the exit code explicitly.
# In PowerShell 5.1 stderr output of a native command becomes a terminating
# error under "Stop" (pip prints warnings there): relax it locally.
function Run($exe, [string[]]$argList, $what) {
    $ErrorActionPreference = "Continue"
    & $exe @argList 2>&1 | Out-File -FilePath $Log -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "$what non riuscito (codice $LASTEXITCODE)" }
}

function Winget-Install($id, $what) {
    Run "winget" @("install", "--id", $id, "-e", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements") $what
    Refresh-Path
}

# Python 3.10-3.13: Whisper and PyTorch publish wheels for these versions.
# Returns the command as an array (e.g. "py", "-3.12"), or nothing.
function Find-Python {
    $ErrorActionPreference = "Continue"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return "py", "-$v" }
        }
    }
    # Skip the Microsoft Store alias in WindowsApps: it is a stub, not Python.
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
        & $cmd.Source -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $cmd.Source }
    }
}

try {
    Write-Host "vid2guide - installazione per Windows"
    Write-Host "Cartella: $Root"

    # --- 1. winget ------------------------------------------------------------
    Step "1/5 winget (gestore dei pacchetti di Windows)"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget non trovato. Installa 'Programma di installazione app' (App Installer) dal Microsoft Store, poi rilancia questo file."
    }
    Ok "presente"

    # --- 2. Python ------------------------------------------------------------
    Step "2/5 Python"
    $py = @(Find-Python)
    if ($py.Count -eq 0) {
        Write-Host "    Nessun Python compatibile (serve 3.10-3.13). Installo Python 3.12..."
        Winget-Install "Python.Python.3.12" "Installazione di Python"
        $py = @(Find-Python)
        if ($py.Count -eq 0) { throw "Python installato ma non raggiungibile: chiudi questa finestra e rilancia install-windows.bat." }
    }
    $pyExe = $py[0]
    $pyArgs = @($py | Select-Object -Skip 1)
    $ver = & $pyExe @pyArgs --version
    Ok "$ver"

    # --- 3. FFmpeg ------------------------------------------------------------
    Step "3/5 FFmpeg (estrazione audio)"
    if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
        Ok "già installato"
    } else {
        Write-Host "    Installo FFmpeg (qualche minuto)..."
        Winget-Install "Gyan.FFmpeg" "Installazione di FFmpeg"
        if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            throw "FFmpeg installato ma non ancora nel PATH: chiudi questa finestra e rilancia install-windows.bat."
        }
        Ok "installato"
    }

    # --- 4. Python environment and dependencies -------------------------------
    Step "4/5 Ambiente Python e dipendenze (~1 GB, può richiedere diversi minuti)"
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Run $pyExe ($pyArgs + @("-m", "venv", ".venv")) "Creazione dell'ambiente .venv"
    }
    Run $venvPy @("-m", "pip", "install", "--upgrade", "pip") "Aggiornamento di pip"
    Run $venvPy @("-m", "pip", "install", "-r", "requirements.txt") "Installazione delle dipendenze"
    Ok "dipendenze installate"

    # --- 5. Final check -------------------------------------------------------
    Step "5/5 Verifica"
    $ErrorActionPreference = "Continue"
    & $venvPy -c "import whisper, torch, markdown; print('    OK whisper + torch', torch.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "Verifica delle dipendenze non riuscita" }
    $ff = (& ffmpeg -version | Select-Object -First 1)
    $ErrorActionPreference = "Stop"
    Ok "$ff"
    $chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
                "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                "$env:LocalAppData\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ }
    if ($chrome) { Ok "Chrome trovato (serve per esportare le guide in PDF)" }
    else { Warn "Chrome non trovato: la trascrizione funziona, l'export PDF delle guide no." }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "input_videos") | Out-Null

    Write-Host ""
    Write-Host "Installazione completata." -ForegroundColor Green
    Write-Host ""
    Write-Host "Come si usa:"
    Write-Host "  1. Copia i tuoi video nella cartella input_videos\"
    Write-Host "  2. Apri PowerShell in questa cartella e lancia:"
    Write-Host "       .venv\Scripts\python main_pipeline.py --model small"
    Write-Host "  3. Le trascrizioni arrivano in transcriptions\"
    Write-Host ""
    Write-Host "Al primo avvio viene scaricato il modello Whisper (~460 MB per 'small'):"
    Write-Host "serve internet una volta sola. Istruzioni complete nel README.md."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Installazione interrotta: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Il dettaglio dell'errore è in: $Log"
    Write-Host "Puoi rilanciare install-windows.bat: riparte saltando i passi già fatti."
    exit 1
}
