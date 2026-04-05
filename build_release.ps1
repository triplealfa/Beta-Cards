# Copyright (C) 2026 Triple Alfa
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Beta Cards.
#
# Beta Cards is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# Beta Cards is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Beta Cards. If not, see <https://www.gnu.org/licenses/>.

$ErrorActionPreference = "Stop"

$python = Join-Path $env:LOCALAPPDATA "Python\bin\python.exe"
if (-not (Test-Path $python)) {
    throw "Python was not found at $python"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "BetaCards" `
    --icon "_internal\icons\main_icon.ico" `
    --add-data "sounds;sounds" `
    beta_cards.py

$releaseDir = Join-Path $PSScriptRoot "dist\BetaCards"
$sourceNoticePath = Join-Path $releaseDir "SOURCE_CODE.txt"

# Copy icons to _internal/icons in release
$internalIconsDir = Join-Path $releaseDir "_internal\icons"
if (Test-Path $internalIconsDir) {
    Remove-Item -Recurse -Force $internalIconsDir
}
Copy-Item -Recurse -Force (Join-Path $PSScriptRoot "_internal\icons") $internalIconsDir

# Copy rules to _internal/rules in release
$internalRulesDir = Join-Path $releaseDir "_internal\rules"
if (Test-Path $internalRulesDir) {
    Remove-Item -Recurse -Force $internalRulesDir
}
Copy-Item -Recurse -Force (Join-Path $PSScriptRoot "_internal\rules") $internalRulesDir

# Create empty cards folder with user guidance file
$publicCardsDir = Join-Path $releaseDir "cards"
if (Test-Path $publicCardsDir) {
    Remove-Item -Recurse -Force $publicCardsDir
}
New-Item -ItemType Directory -Force -Path $publicCardsDir | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "cards\How to add cards.txt") $publicCardsDir

# Ship a source pointer next to the executable for GPL compliance.
$sourceNotice = @'
Beta Cards source code
======================

This release executable is distributed under the GNU General Public License,
version 3 or later.

The corresponding source code for this release is available at:
https://github.com/triplealfa/Beta-Cards

If the repository is not yet public at the moment you received this build,
the release should not be distributed publicly until the source code is made
available at the address above.
'@
Set-Content -Path $sourceNoticePath -Value $sourceNotice

Write-Host ""
Write-Host "Release build complete:"
Write-Host "  dist\\BetaCards\\BetaCards.exe"
Write-Host "  dist\\BetaCards\\SOURCE_CODE.txt (source code location)"
Write-Host "  dist\\BetaCards\\_internal\\icons (app icons)"
Write-Host "  dist\\BetaCards\\_internal\\rules (rules content)"
Write-Host "  dist\\BetaCards\\cards (user folder with guidance)"
