# SPDX-License-Identifier: GPL-3.0-only
param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$ds100Project = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ds100Project 'build/tutorial-audio' }
$ds100AudioPath = [System.IO.Path]::GetFullPath($OutputDirectory)
$ds100Workspace = [System.IO.Path]::GetFullPath($ds100Project) + [System.IO.Path]::DirectorySeparatorChar
if (-not $ds100AudioPath.StartsWith($ds100Workspace, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Use a project output directory.' }
[void](New-Item -ItemType Directory -Path $ds100AudioPath -Force)
$ds100Plan = Get-Content -LiteralPath (Join-Path $ds100Project 'media/tutorial-plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$ds100Voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $ds100Voice.SelectVoice('Microsoft Huihui Desktop')
    $ds100Voice.Rate = 1
    $ds100Chunks = @()
    foreach ($ds100Scene in $ds100Plan.scenes) {
        $ds100Index = 0
        foreach ($ds100Sentence in $ds100Scene.narration) {
            $ds100Name = $ds100Scene.id + '-' + $ds100Index.ToString('00') + '.wav'
            $ds100Wave = Join-Path $ds100AudioPath $ds100Name
            $ds100Voice.SetOutputToWaveFile($ds100Wave)
            $ds100Voice.Speak([string]$ds100Sentence)
            $ds100Voice.SetOutputToNull()
            $ds100Chunks += @{ scene = $ds100Scene.id; file = $ds100Name; text = [string]$ds100Sentence }
            $ds100Index++
        }
    }
    @{ voice = 'Microsoft Huihui Desktop'; synthetic_voice = $true; network_used = $false; chunks = $ds100Chunks } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ds100AudioPath 'chunks.json') -Encoding UTF8
    Write-Output ('Created ' + $ds100Chunks.Count + ' narration chunks.')
} finally { $ds100Voice.Dispose() }
