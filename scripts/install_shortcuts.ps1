param([switch]$Autostart)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$appPath = Join-Path $repoRoot '_app\meeting_asr.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install requirements first.' }
$shortcutShell = New-Object -ComObject WScript.Shell
$desktopLink = $shortcutShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '会议录音转写.lnk'))
$desktopLink.TargetPath = $pythonPath
$desktopLink.Arguments = '"' + $appPath + '"'
$desktopLink.WorkingDirectory = Join-Path $repoRoot '_app'
$desktopLink.Save()
if ($Autostart) {
    $startupLink = $shortcutShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) '会议录音（后台）.lnk'))
    $startupLink.TargetPath = $pythonPath
    $startupLink.Arguments = '"' + $appPath + '" --background'
    $startupLink.WorkingDirectory = Join-Path $repoRoot '_app'
    $startupLink.Save()
}
Write-Output 'Shortcuts installed.'
