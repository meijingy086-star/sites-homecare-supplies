$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $basePython = $null
    $runtimeDirs = Get-ChildItem -LiteralPath 'C:\Program Files\WindowsApps' -Directory -Filter 'OpenAI.CodexPrimaryRuntime*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    foreach ($runtimeDir in $runtimeDirs) {
        $candidatePython = Join-Path $runtimeDir.FullName 'dependencies\python\python.exe'
        if (Test-Path -LiteralPath $candidatePython) { $basePython = $candidatePython; break }
    }
    if (-not $basePython) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) { $basePython = $pythonCommand.Source }
    }
    if (-not $basePython) { throw 'Python 3.10+ is required. Install Python and try again.' }
    & $basePython -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
    & $basePython -m venv '.venv'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $venvPython -c 'import streamlit' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing Streamlit. First launch needs internet access...'
    & $venvPython -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Check your internet connection.' }
}
Write-Host 'Computer: http://localhost:8501'
Write-Host 'Phone: connect to the same Wi-Fi, then open one of these addresses:'
try {
    [Net.Dns]::GetHostAddresses([Net.Dns]::GetHostName()) | Where-Object { $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and -not [Net.IPAddress]::IsLoopback($_) } | ForEach-Object { Write-Host ('http://' + $_.IPAddressToString + ':8501') }
} catch {}
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $venvPython -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
