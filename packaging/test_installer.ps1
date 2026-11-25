param([Parameter(Mandatory = $true)][string]$Installer)

$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$installDir = Join-Path ([IO.Path]::GetTempPath()) ("DIRIGERA install test " + [Guid]::NewGuid())
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'DIRIGERA for Windows.lnk'
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'DIRIGERA for Windows.lnk'
$commonArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')

function Install-Dirigera([string[]]$TaskArgs = @()) {
    $setup = Start-Process -FilePath $installerPath -Wait -PassThru -ArgumentList (
        $commonArgs + @("/DIR=`"$installDir`"") + $TaskArgs
    )
    if ($setup.ExitCode -ne 0) {
        throw "Setup exited with code $($setup.ExitCode)"
    }
}

Install-Dirigera
if (!(Test-Path -LiteralPath $startMenu) -or (Test-Path -LiteralPath $startup)) {
    throw 'A fresh install should create the Start menu shortcut and leave autostart off.'
}

Install-Dirigera -TaskArgs '/MERGETASKS=autostart'
if (!(Test-Path -LiteralPath $startup)) {
    throw 'Opting in should create the Startup shortcut.'
}
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($startup)
if ($link.TargetPath -ne (Join-Path $installDir 'Dirigera.exe') -or $link.Arguments -ne '--tray') {
    throw 'The Startup shortcut should launch the installed app in the tray.'
}

Install-Dirigera
if (!(Test-Path -LiteralPath $startup)) {
    throw 'An upgrade should remember the startup preference.'
}

Install-Dirigera -TaskArgs '/MERGETASKS=!autostart'
if (Test-Path -LiteralPath $startup) {
    throw 'Opting out should remove the Startup shortcut.'
}

Install-Dirigera -TaskArgs '/MERGETASKS=autostart'
$uninstaller = Join-Path $installDir 'unins000.exe'
$uninstall = Start-Process -FilePath $uninstaller -Wait -PassThru -ArgumentList $commonArgs
if ($uninstall.ExitCode -ne 0 -or (Test-Path -LiteralPath $startMenu) -or (Test-Path -LiteralPath $startup)) {
    throw 'Uninstall should remove both shortcuts.'
}

Write-Host 'Verified clean install, startup opt-in, upgrade, opt-out, and uninstall.'
