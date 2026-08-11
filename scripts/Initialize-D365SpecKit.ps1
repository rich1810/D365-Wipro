[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$ProjectPath,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$specKitVersion = '0.12.4'

if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Project path does not exist or is not a directory: $ProjectPath"
}

$projectRoot = (Resolve-Path -LiteralPath $ProjectPath).Path
$compatibilityPath = Join-Path $projectRoot '.d365\spec-kit\compatibility.yml'
if (-not (Test-Path -LiteralPath $compatibilityPath -PathType Leaf)) {
    throw "Craft compatibility contract is missing: $compatibilityPath"
}
$compatibilityText = Get-Content -LiteralPath $compatibilityPath -Raw
if ($compatibilityText -notmatch '(?m)^\s*version:\s*"?0\.12\.4"?\s*$') {
    throw "Compatibility contract does not pin specify-cli $specKitVersion."
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("d365-speckit-" + [guid]::NewGuid())
$generatedRoot = Join-Path $tempRoot 'generated'

function Get-RelativePath {
    param(
        [Parameter(Mandatory)][string]$BasePath,
        [Parameter(Mandatory)][string]$TargetPath
    )

    $resolvedBasePath = [System.IO.Path]::GetFullPath($BasePath)
    $resolvedTargetPath = [System.IO.Path]::GetFullPath($TargetPath)

    if ($resolvedBasePath -notmatch '[\\/]$') {
        $resolvedBasePath = $resolvedBasePath + [System.IO.Path]::DirectorySeparatorChar
    }

    $baseUri = [System.Uri]$resolvedBasePath
    $targetUri = [System.Uri]$resolvedTargetPath
    $relativeUri = $baseUri.MakeRelativeUri($targetUri)
    $relativePath = [System.Uri]::UnescapeDataString($relativeUri.ToString())
    return $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
}

try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null

    $uvxCommand = Get-Command uvx -ErrorAction SilentlyContinue
    $specifyCommand = Get-Command specify -ErrorAction SilentlyContinue
    $usedFallback = $false

    if ($uvxCommand) {
        & uvx --from "specify-cli==$specKitVersion" specify init $generatedRoot `
            --integration copilot --script ps --ignore-agent-tools
        if ($LASTEXITCODE -eq 0) {
            $usedFallback = $false
        }
        elseif ($specifyCommand) {
            Write-Warning "uvx bootstrap failed with exit code $LASTEXITCODE; retrying with the installed specify CLI."
            & specify init $generatedRoot --integration copilot --script ps --ignore-agent-tools
            $usedFallback = $true
        }
        else {
            throw "Official specify-cli $specKitVersion initialization failed with exit code $LASTEXITCODE."
        }
    }
    elseif ($specifyCommand) {
        & specify init $generatedRoot --integration copilot --script ps --ignore-agent-tools
        $usedFallback = $true
    }
    else {
        throw "Neither uvx nor a locally installed specify CLI was found. Install uvx or run 'python -m pip install --user specify-cli==0.12.4'."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Official specify-cli $specKitVersion initialization failed with exit code $LASTEXITCODE."
    }

    $runtimeRoots = @(
        '.github\agents',
        '.github\prompts',
        '.specify\integrations',
        '.specify\scripts',
        '.specify\workflows'
    )
    $runtimeFiles = foreach ($relativeRoot in $runtimeRoots) {
        $sourceRoot = Join-Path $generatedRoot $relativeRoot
        if (Test-Path -LiteralPath $sourceRoot -PathType Container) {
            Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
                [pscustomobject]@{
                    Source = $_.FullName
                    RelativePath = Get-RelativePath -BasePath $generatedRoot -TargetPath $_.FullName
                }
            }
        }
    }
    foreach ($relativeFile in @('.specify\init-options.json', '.specify\integration.json')) {
        $sourceFile = Join-Path $generatedRoot $relativeFile
        if (Test-Path -LiteralPath $sourceFile -PathType Leaf) {
            $runtimeFiles += [pscustomobject]@{ Source = $sourceFile; RelativePath = $relativeFile }
        }
    }

    $overrideMap = @{
        '.specify\templates\overrides\spec-template.md' = '.specify\templates\spec-template.md'
        '.specify\templates\overrides\plan-template.md' = '.specify\templates\plan-template.md'
        '.specify\templates\overrides\tasks-template.md' = '.specify\templates\tasks-template.md'
    }
    foreach ($entry in $overrideMap.GetEnumerator()) {
        $source = Join-Path $projectRoot $entry.Key
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Required Craft override is missing: $source"
        }
        $runtimeFiles += [pscustomobject]@{ Source = $source; RelativePath = $entry.Value }
    }

    $conflicts = $runtimeFiles | Where-Object {
        Test-Path -LiteralPath (Join-Path $projectRoot $_.RelativePath) -PathType Leaf
    }
    if ($conflicts -and -not $Force) {
        $paths = ($conflicts.RelativePath | Sort-Object -Unique) -join ', '
        throw "Spec Kit initialization stopped because target files already exist: $paths. Review them, then rerun with -Force to refresh the pinned runtime."
    }

    $copied = 0
    foreach ($item in $runtimeFiles) {
        $destination = Join-Path $projectRoot $item.RelativePath
        if (-not $PSCmdlet.ShouldProcess($destination, "Install pinned Spec Kit runtime file")) {
            continue
        }
        $directory = Split-Path -Parent $destination
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        Copy-Item -LiteralPath $item.Source -Destination $destination -Force
        $copied++
    }

    if ($WhatIfPreference) {
        Write-Output "Previewed pinned specify-cli $specKitVersion installation; no files copied."
    }
    else {
        Write-Output "Installed pinned specify-cli $specKitVersion runtime and Craft overrides ($copied files)."
    }
}
finally {
    if (Test-Path -LiteralPath $tempRoot -PathType Container) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
