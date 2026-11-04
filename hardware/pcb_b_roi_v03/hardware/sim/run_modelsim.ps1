[CmdletBinding()]
param(
    [string]$ToolBin = 'D:\intelFPGA_pro\21.1\modelsim_ase\win32aloem',
    [string]$PythonPath = 'python',
    [ValidateSet('Unit','Fullframe','All')][string]$Test = 'All',
    [string]$WorkDirectory = ''
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $root ('sim_runs/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss_fffZ') + '_' + $PID)
}
if (Test-Path -LiteralPath $WorkDirectory) { throw 'Use a new WorkDirectory to preserve previous logs.' }
$null = New-Item -ItemType Directory -Path $WorkDirectory
$WorkDirectory = (Resolve-Path -LiteralPath $WorkDirectory).Path
$library = Join-Path $WorkDirectory 'modelsim_lib'
$rtl = Join-Path $root 'hardware/rtl/axis_roi_threshold_stats.v'
$sources = @($rtl)
$specs = @()
if ($Test -in @('Unit','All')) {
    $sources += Join-Path $PSScriptRoot 'tb_axis_roi_threshold_stats.sv'
    $specs += @{Name='unit'; Top='tb_axis_roi_threshold_stats'; Marker='PASS: ROI_STATS_UNIT'; Args=''}
}
function TclArg([string]$value) {
    if ($value.Contains('{') -or $value.Contains('}')) { throw 'Braces are unsupported in simulation paths.' }
    return '{' + $value.Replace('\','/') + '}'
}
if ($Test -in @('Fullframe','All')) {
    $vectors = Join-Path $WorkDirectory 'vectors'
    & $PythonPath (Join-Path $PSScriptRoot 'prepare_fullframe_vectors.py') --output $vectors
    if ($LASTEXITCODE -ne 0) { throw 'Independent vector generation failed.' }
    $sources += Join-Path $PSScriptRoot 'tb_roi_fullframe.sv'
    $specs += @{Name='fullframe'; Top='tb_roi_fullframe'; Marker='PASS: ROI_STATS_FULLFRAME frames=3 input_beats=3609525 output_beats=3609525 bytes=14438100'; Args=(TclArg ('+DATA_DIR='+$vectors))}
}
& (Join-Path $ToolBin 'vlib.exe') $library
if ($LASTEXITCODE -ne 0) { throw 'vlib failed.' }
& (Join-Path $ToolBin 'vlog.exe') -sv -work $library @sources 2>&1 | Tee-Object -FilePath (Join-Path $WorkDirectory 'compile.log')
if ($LASTEXITCODE -ne 0) { throw 'Compilation failed.' }
$compileText = Get-Content -LiteralPath (Join-Path $WorkDirectory 'compile.log') -Raw
if ($compileText -match '(?im)^\s*#?\s*\*\*\s*(Error|Fatal):' -or $compileText -match 'Errors:\s*[1-9]') { throw 'Compiler errors found.' }
foreach ($spec in $specs) {
    $name = $spec.Name
    $top = $spec.Top
    $log = Join-Path $WorkDirectory ($name+'.log')
    $macro = Join-Path $WorkDirectory ($name+'.do')
    $wave = Join-Path $WorkDirectory ($name+'.wlf')
    $macroText = "onerror {quit -force -code 1}`nonbreak {quit -force -code 1}`nvsim -wlf " +
        (TclArg $wave) + ' -lib ' + (TclArg $library) + ' ' + $top + ' ' + $spec.Args +
        "`nrun -all`nquit -force -code 0`n"
    [IO.File]::WriteAllText($macro,$macroText,(New-Object Text.UTF8Encoding($false)))
    & (Join-Path $ToolBin 'vsim.exe') -c -l $log -do ('do '+(TclArg $macro))
    if ($LASTEXITCODE -ne 0) { throw ('Simulation failed: '+$log) }
    $text = Get-Content -LiteralPath $log -Raw
    if ($text -match '(?im)^\s*#?\s*\*\*\s*(Error|Fatal):' -or $text -match 'Errors:\s*[1-9]') { throw ('Simulator error: '+$log) }
    if ($text -notmatch ('(?m)^\s*#?\s*'+[regex]::Escape($spec.Marker))) { throw ('Missing pass marker: '+$log) }
    Write-Host ('Verified '+$name+' PASS: '+$log)
}
Write-Host ('ROI_V03_SIMULATION_PASS '+$Test+' -> '+$WorkDirectory)
Write-Host 'Scope: pure RTL only; physical-board and DMA results must be recorded separately.'
