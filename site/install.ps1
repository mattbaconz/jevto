# JevTO installer: https://github.com/mattbaconz/jevto
# Installs a pinned release for the current user. No elevation or agent config changes.
# Optional: $env:JEVTO_INSTALL_DIR and $env:JEVTO_NO_PATH = '1'.
& {
    $ErrorActionPreference = 'Stop'
    $release = 'v0.1.1'
    $releaseRoot = 'https://github.com/mattbaconz/jevto/releases/download'
    $expectedHash = 'd5a67511cde9d42cbb8ebadf258a210f5b82ec10bd8c706534c779f8994b1bb9'

    function Add-JevtoPathEntry([string] $CurrentPath, [string] $Directory) {
        foreach ($entry in ($CurrentPath -split ';')) {
            $expanded = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))
            if ($expanded.TrimEnd('\', '/') -ieq $Directory.TrimEnd('\', '/')) {
                return $CurrentPath
            }
        }
        if ([string]::IsNullOrEmpty($CurrentPath)) { return $Directory }
        return $CurrentPath.TrimEnd(';') + ';' + $Directory
    }

    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Use https://jevto.xyz/install.sh on macOS or Linux.'
    }
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64') {
        throw "No prebuilt Windows release for $architecture. See https://jevto.xyz/docs/#manual-install for source installation."
    }
    if ($env:JEVTO_NO_PATH -and $env:JEVTO_NO_PATH -notin @('0', '1')) {
        throw 'JEVTO_NO_PATH must be 0 or 1.'
    }
    $installDirectory = $env:JEVTO_INSTALL_DIR
    if (-not $installDirectory) {
        $installDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'JevTO\bin'
    }
    if ($installDirectory.IndexOfAny([char[]]";`r`n") -ge 0) {
        throw 'The install directory cannot contain semicolons or newlines.'
    }
    $installDirectory = [IO.Path]::GetFullPath($installDirectory)
    $destination = Join-Path $installDirectory 'jevto.exe'
    if (Test-Path -LiteralPath $destination -PathType Container) {
        throw "A directory already exists at $destination. Choose a different JEVTO_INSTALL_DIR."
    }
    $archiveName = "jevto-$release-x86_64-pc-windows-msvc"
    $temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ('jevto-install-' + [guid]::NewGuid().ToString('N'))
    $archivePath = Join-Path $temporaryDirectory 'release.zip'
    $candidatePath = Join-Path $temporaryDirectory 'jevto.exe'
    $stagedPath = $null
    $environmentKey = $null

    try {
        [void][IO.Directory]::CreateDirectory($temporaryDirectory)
        Write-Host "Downloading JevTO $release for Windows x64..."
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseRoot/$release/$archiveName.zip" -OutFile $archivePath -TimeoutSec 180
        $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        if ($actualHash -ine $expectedHash) {
            throw 'Release checksum mismatch. Nothing was installed; download again or report this at https://github.com/mattbaconz/jevto/issues.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            $entries = @($archive.Entries | Where-Object { $_.FullName -ceq "$archiveName/jevto.exe" })
            if ($entries.Count -ne 1) { throw 'The release archive does not contain exactly one expected executable.' }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $candidatePath)
        } finally {
            $archive.Dispose()
        }
        $versionOutput = & $candidatePath --version 2>&1
        if ($LASTEXITCODE -ne 0 -or "$versionOutput".Trim() -cne "jevto $($release.Substring(1))") {
            throw "The downloaded executable did not pass its version check: $versionOutput"
        }

        [void][IO.Directory]::CreateDirectory($installDirectory)
        $sameBinary = (Test-Path -LiteralPath $destination -PathType Leaf) -and
            ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq
             (Get-FileHash -LiteralPath $candidatePath -Algorithm SHA256).Hash)
        if (-not $sameBinary) {
            $stagedPath = Join-Path $installDirectory ('.jevto-' + [guid]::NewGuid().ToString('N') + '.exe')
            [IO.File]::Copy($candidatePath, $stagedPath)
            try {
                if ([IO.File]::Exists($destination)) {
                    # PowerShell 5.1 otherwise coerces $null to an invalid empty path.
                    [IO.File]::Replace($stagedPath, $destination, [NullString]::Value)
                } else {
                    [IO.File]::Move($stagedPath, $destination)
                }
            } catch {
                throw "Could not replace $destination. Close processes using that executable and rerun the installer. $($_.Exception.Message)"
            }
        }

        if ($env:JEVTO_NO_PATH -ne '1') {
            # Preserve the user's raw PATH entries (including %VARIABLES%) and registry type.
            $environmentKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
            if (-not $environmentKey) { $environmentKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment') }
            $userPath = [string]$environmentKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $pathKind = [Microsoft.Win32.RegistryValueKind]::ExpandString
            if ($environmentKey.GetValueNames() -contains 'Path') { $pathKind = $environmentKey.GetValueKind('Path') }
            $nextPath = Add-JevtoPathEntry $userPath $installDirectory
            if ($nextPath -cne $userPath) {
                # This API also notifies Windows that the user environment changed.
                [Environment]::SetEnvironmentVariable('Path', $nextPath, 'User')
                $environmentKey.SetValue('Path', $nextPath, $pathKind)
            }
            # Put this installation first for the current session, without dropping other entries.
            $env:PATH = $installDirectory + ';' + (($env:PATH -split ';' | Where-Object {
                $_.TrimEnd('\', '/') -ine $installDirectory.TrimEnd('\', '/')
            }) -join ';')
        }

        Write-Host "Installed $versionOutput to $destination"
        if ($env:JEVTO_NO_PATH -eq '1') {
            Write-Host 'PATH was left unchanged (JEVTO_NO_PATH=1).'
        } else {
            Write-Host 'Ready in this PowerShell window. Restart other terminals or editors to refresh PATH.'
        }
        Write-Host 'Next: jevto doctor'
        Write-Host 'Claude Code setup: https://jevto.xyz/docs/#claude-code'
        Write-Host 'Agent hooks were not changed. Rerun this installer to update.'
    } finally {
        if ($environmentKey) { $environmentKey.Dispose() }
        # Only exact files created by this invocation are removed; no recursive directory deletion.
        foreach ($temporaryFile in @($stagedPath, $candidatePath, $archivePath)) {
            if ($temporaryFile -and [IO.File]::Exists($temporaryFile)) { [IO.File]::Delete($temporaryFile) }
        }
        if ([IO.Directory]::Exists($temporaryDirectory)) { [IO.Directory]::Delete($temporaryDirectory, $false) }
    }
}
