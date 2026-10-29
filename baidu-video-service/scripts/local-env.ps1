# Dot-source in a project-local PowerShell session before installing tools.
$serviceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$serviceCache = Join-Path $serviceRoot '.cache'
$serviceTemp = Join-Path $serviceCache 'tmp'
New-Item -ItemType Directory -Force -Path $serviceTemp | Out-Null
$env:TMP = $serviceTemp
$env:TEMP = $serviceTemp
$env:TMPDIR = $serviceTemp
$env:APPDATA = Join-Path $serviceCache 'appdata'
$env:LOCALAPPDATA = Join-Path $serviceCache 'localappdata'
$env:XDG_CACHE_HOME = Join-Path $serviceCache 'xdg'
$env:XDG_CONFIG_HOME = Join-Path $serviceCache 'config'
$env:XDG_DATA_HOME = Join-Path $serviceCache 'data'
$env:XDG_STATE_HOME = Join-Path $serviceCache 'state'
$env:COREPACK_HOME = Join-Path $serviceCache 'corepack'
$env:PNPM_HOME = Join-Path $serviceCache 'pnpm-home'
$env:npm_config_cache = Join-Path $serviceCache 'npm-cache'
$env:DENO_DIR = Join-Path $serviceCache 'deno-dir'
$env:DENO_NO_UPDATE_CHECK = '1'
$env:PATH = (Join-Path $serviceCache 'deno-cli') + [System.IO.Path]::PathSeparator + $env:PATH
