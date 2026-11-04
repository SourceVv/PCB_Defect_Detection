$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv and install the project using README first.'
}
Push-Location $repo
try {
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    & $python -m pcb_inspection evaluate --config configs/roi_config.json --input data/raw/s01 --truth data/labels/component_truth.csv --out outputs/s01
    if ($LASTEXITCODE -ne 0) { throw 's01 regression failed' }
    & $python -m pcb_inspection make-golden --config configs/roi_config.json --out golden/generated
    if ($LASTEXITCODE -ne 0) { throw 'Golden generation failed' }
    & $python -m pcb_inspection verify-golden --config configs/roi_config.json --golden golden/generated
    if ($LASTEXITCODE -ne 0) { throw 'Golden verification failed' }
} finally {
    Pop-Location
}
