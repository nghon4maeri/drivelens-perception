# Configure project-local cache/temp storage for commands in this PowerShell.
# Input: repo checkout; output: process environment and directories under data/output.
# Run: . .\scripts\use_project_storage.ps1 before Python, npm or tests.
$ravStorageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# local-only: run from a checkout on the desired disk; LOCAL_SINGLE_USER_LIMITS storage policy.
$ravStoragePaths = @{
    HF_HOME = 'data/system-cache/huggingface'
    TORCH_HOME = 'data/system-cache/torch'
    PIP_CACHE_DIR = 'data/system-cache/pip'
    npm_config_cache = 'data/system-cache/npm'
    RAV_CLIP_CACHE_DIR = 'data/model-cache/huggingface/hub'
    YOLO_CONFIG_DIR = 'output/ultralytics'
    TEMP = 'output/tmp'
    TMP = 'output/tmp'
}
foreach ($ravStorageEntry in $ravStoragePaths.GetEnumerator()) {
    $ravStoragePath = Join-Path $ravStorageRoot $ravStorageEntry.Value
    New-Item -ItemType Directory -Path $ravStoragePath -Force | Out-Null
    [Environment]::SetEnvironmentVariable($ravStorageEntry.Key, $ravStoragePath, 'Process')
}
# Optional project-installed dependencies (pip --target data/python-packages), kept off C.
$ravPythonPackages = Join-Path $ravStorageRoot 'data/python-packages'
$ravPythonSearchPaths = @($ravPythonPackages) + @($env:PYTHONPATH -split ';' | Where-Object { $_ -and $_ -ne $ravPythonPackages })
$env:PYTHONPATH = $ravPythonSearchPaths -join ';'
Write-Host "Cache/temp cua terminal nay: $ravStorageRoot (data/output)."
