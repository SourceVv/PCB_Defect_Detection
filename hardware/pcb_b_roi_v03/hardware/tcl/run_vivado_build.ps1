[CmdletBinding()]
param(
    [ValidateSet('Create','Build','All','BuildInProcess','AllInProcess')][string]$Stage = 'AllInProcess',
    [ValidateRange(1,16)][int]$Jobs = 4,
    [string]$VivadoPath = 'D:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat',
    [string]$BoardRepo = 'D:\codex\board_files'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$packageRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$resultRoot = Join-Path $packageRoot ('evidence\vivado\batch_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
$null = New-Item -ItemType Directory -Path $resultRoot -Force
if (-not (Test-Path -LiteralPath $VivadoPath -PathType Leaf)) { throw "Missing Vivado: $VivadoPath" }
if (-not (Test-Path -LiteralPath $BoardRepo -PathType Container)) { throw "Missing board repository: $BoardRepo" }
$env:XILINX_LOCAL_USER_DATA = Join-Path $packageRoot 'build\vivado_user_data'
$env:PYNQ_BOARD_REPO = $BoardRepo
$null = New-Item -ItemType Directory -Path $env:XILINX_LOCAL_USER_DATA -Force
function Get-InputHashes {
    $files = @(
        Get-ChildItem -LiteralPath (Join-Path $packageRoot 'hardware\rtl') -File -Filter '*.v'
        Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter '*.tcl'
    ) | Sort-Object FullName
    $result = [ordered]@{}
    foreach ($file in $files) {
        $rootPrefix = $packageRoot.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
        if (-not $file.FullName.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Input path outside package' }
        $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\','/')
        $result[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $result
}
$beforeHashes = Get-InputHashes
$beforeHashes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $resultRoot 'input_hashes_before.json') -Encoding utf8
$phases = @()
if ($Stage -in @('Create','All','AllInProcess')) { $phases += 'create_project' }
if ($Stage -in @('Build','All')) { $phases += 'build_overlay' }
if ($Stage -in @('BuildInProcess','AllInProcess')) { $phases += 'build_overlay_inprocess' }
foreach ($phase in $phases) {
    $script = Join-Path $PSScriptRoot ($phase + '.tcl')
    $log = Join-Path $resultRoot ($phase + '.log')
    $journal = Join-Path $resultRoot ($phase + '.jou')
    $arguments = @('-mode','batch','-notrace','-source',$script,'-log',$log,'-journal',$journal)
    if ($phase -in @('build_overlay','build_overlay_inprocess')) { $arguments += @('-tclargs',[string]$Jobs) }
    & $VivadoPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "Vivado phase failed: $phase (exit $LASTEXITCODE). See $log" }
    $logText = Get-Content -LiteralPath $log -Raw
    if ($logText -match '(?m)^ERROR:') { throw "Vivado reported an error. See $log" }
}
$afterHashes = Get-InputHashes
$afterHashes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $resultRoot 'input_hashes_after.json') -Encoding utf8
$changed = @($beforeHashes.Keys | Where-Object { -not $afterHashes.Contains($_) -or $afterHashes[$_] -ne $beforeHashes[$_] })
if ($changed.Count -or $beforeHashes.Count -ne $afterHashes.Count) {
    $message = "SOURCE_CHANGED_DURING_BUILD: " + ($changed -join ', ')
    $message | Set-Content -LiteralPath (Join-Path $resultRoot 'SOURCE_CHANGED.txt') -Encoding utf8
    $release = Join-Path $packageRoot 'release\roi_v0_3'
    if (Test-Path -LiteralPath $release -PathType Container) {
        $message | Set-Content -LiteralPath (Join-Path $release 'DO_NOT_USE_SOURCE_CHANGED.txt') -Encoding utf8
    }
    throw $message
}
Write-Host "Selected Vivado phases completed. Logs: $resultRoot"
Write-Host 'This command does not connect to JTAG, load a board, or establish board correctness.'
