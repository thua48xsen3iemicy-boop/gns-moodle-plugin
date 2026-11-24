<#
.SYNOPSIS
Builds viogpudo, the VirtIO GPU display driver for Windows, from a commit of virtio-win/kvm-guest-drivers-windows and
test-signs it.

.DESCRIPTION
The default commit is the head of virtio-win PR #1715 ("wait for the cursor image upload, support monochrome
cursors"): the released driver shows the hardware cursor one shape behind, because UPDATE_CURSOR overtakes the upload
of the new image. The build is Win10 Release x64 (the package installs on Windows 10 and 11). The INF is patched so
that HWCursor is on by default. The driver is signed with a fresh self-signed certificate; the guest needs test
signing on and that certificate in its stores (README.md).

Runs on a GitHub-hosted windows-2022 runner (Visual Studio 2022 with the WDK), see
.github/workflows/viogpu-cursor-fix.yml. Inputs come from parameters or from the environment (VIOGPU_REF,
VIOGPU_HWCURSOR), so that the workflow does not paste them into the script.
#>
param(
    [string]$Ref = $env:VIOGPU_REF,
    [string]$HwCursor = $env:VIOGPU_HWCURSOR,
    [string]$Out = (Join-Path (Get-Location) 'out'),
    [string]$Work = $(if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() })
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = 'https://github.com/virtio-win/kvm-guest-drivers-windows'
$Pr = '1715'
$DefaultRef = 'f01037a8fb8295b6323c02f9d5f20f0411500957'  # head of PR #1715 on 2026-11-24

if (-not $Ref) { $Ref = $DefaultRef }
if ($Ref -notmatch '^[0-9a-f]{40}$') { throw "Ref must be a full commit hash, got '$Ref'" }
if (-not $HwCursor) { $HwCursor = '1' }
if ($HwCursor -notin @('0', '1')) { throw "HwCursor must be 0 or 1, got '$HwCursor'" }

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $Exe) failed with exit code $LASTEXITCODE" }
}

function Step([string]$Text) { Write-Host "==== $Text" }

# ---------------------------------------------------------------- sources
Step "sources: $Repo @ $Ref"
$src = Join-Path $Work 'kvm-guest-drivers-windows'
if (Test-Path $src) { Remove-Item -Recurse -Force $src }
Invoke-Native git @('init', '--quiet', $src)
Invoke-Native git @('-C', $src, 'remote', 'add', 'origin', $Repo)
Invoke-Native git @('-C', $src, 'fetch', '--quiet', '--depth', '1', 'origin', $Ref)
Invoke-Native git @('-C', $src, 'checkout', '--quiet', 'FETCH_HEAD')
$head = (& git -C $src rev-parse HEAD).Trim()
if ($head -ne $Ref) { throw "checked out $head instead of $Ref" }
$subject = (& git -C $src log -1 --format=%s).Trim()

# ---------------------------------------------------------------- HWCursor on by default
# The INF writes HWCursor into the driver key on every install; with 0 the new cursor code does not run at all.
Step "INF: HWCursor = $HwCursor"
$inx = Join-Path $src 'viogpu\viogpudo\viogpudo.inx'
$text = [IO.File]::ReadAllText($inx)
$pattern = '(?m)^(HKR,,\s*HWCursor,\s*%REG_DWORD%,\s*)\d+'
if (([regex]::Matches($text, $pattern)).Count -ne 1) { throw "no single HWCursor line in $inx" }
[IO.File]::WriteAllText($inx, [regex]::Replace($text, $pattern, "`${1}$HwCursor"), [Text.Encoding]::ASCII)

# ---------------------------------------------------------------- toolchain: Visual Studio 2022 and the WDK
Step 'toolchain'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = (& $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath | Select-Object -First 1)
if (-not $vs) { throw 'Visual Studio with MSBuild not found' }
$msbuild = Join-Path $vs 'MSBuild\Current\Bin\amd64\MSBuild.exe'
$kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
$toolset = Get-ChildItem -Path (Join-Path $vs 'MSBuild\Microsoft\VC') -Recurse -Directory -Filter 'WindowsKernelModeDriver10.0' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\Platforms\\x64\\' } | Select-Object -First 1
$inf2cat = Get-ChildItem -Path (Join-Path $kits 'bin') -Recurse -Filter 'inf2cat.exe' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x86\\' } | Sort-Object FullName -Descending | Select-Object -First 1
$signtool = Get-ChildItem -Path (Join-Path $kits 'bin') -Recurse -Filter 'signtool.exe' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
Write-Host "Visual Studio: $vs"
Write-Host "WDK toolset:   $(if ($toolset) { $toolset.FullName } else { 'MISSING' })"
Write-Host "inf2cat:       $(if ($inf2cat) { $inf2cat.FullName } else { 'MISSING' })"
Write-Host "signtool:      $(if ($signtool) { $signtool.FullName } else { 'MISSING' })"
$wdk = (Get-ChildItem -Path (Join-Path $kits 'build') -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
    Select-Object -First 1)
Write-Host "WDK build:     $(if ($wdk) { $wdk.Name } else { 'MISSING' })"
if (-not ($toolset -and $inf2cat -and $signtool)) { throw 'the WDK (kernel mode toolset, inf2cat, signtool) is not installed' }
# The PackOne step of the virtio-win build calls inf2cat by name.
$env:PATH = "$($inf2cat.DirectoryName);$($signtool.DirectoryName);$env:PATH"

# ---------------------------------------------------------------- build
Step 'build: viogpu.sln, Win10 Release, x64'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$log = Join-Path $Work 'viogpu-build.log'
Invoke-Native $msbuild @((Join-Path $src 'viogpu\viogpu.sln'), '-m', '-nologo', '-t:Build',
    '-p:Configuration=Win10 Release', '-p:Platform=x64', '-v:minimal', "-flp:Verbosity=normal;LogFile=$log")

# ---------------------------------------------------------------- package
Step 'package'
$sys = Get-ChildItem -Path (Join-Path $src 'viogpu\Install') -Recurse -Filter 'viogpudo.sys' |
    Where-Object { $_.FullName -match '\\Win10\\amd64\\' } | Select-Object -First 1
if (-not $sys) {
    Get-ChildItem -Path (Join-Path $src 'viogpu') -Recurse -Include '*.sys', '*.inf', '*.cat' | ForEach-Object { Write-Host $_.FullName }
    throw 'viogpudo.sys not found under viogpu\Install\Win10\amd64'
}
$driver = Join-Path $Out 'viogpudo'
New-Item -ItemType Directory -Force -Path $driver | Out-Null
Get-ChildItem -Path $sys.DirectoryName -File | Copy-Item -Destination $driver
# The resolution helper (a service that starts viogpuap.exe in the user session), unsigned user-mode programs.
$tools = Join-Path $Out 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
foreach ($name in @('viogpusc.exe', 'viogpuap.exe')) {
    $exe = Get-ChildItem -Path (Join-Path $src 'viogpu') -Recurse -Filter $name |
        Where-Object { $_.FullName -match 'win10_amd64' } | Select-Object -First 1
    if ($exe) { Copy-Item $exe.FullName -Destination $tools } else { Write-Host "note: $name was not built" }
}

# ---------------------------------------------------------------- test signing
Step 'test signing'
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=GNS3 lab viogpudo test signing' `
    -CertStoreLocation 'Cert:\CurrentUser\My' -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(10)
$cer = Join-Path $Out 'gns3lab-viogpudo-test.cer'
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
$sysFile = Join-Path $driver 'viogpudo.sys'
$catFile = Join-Path $driver 'viogpudo.cat'
if (-not (Test-Path $catFile)) { throw 'viogpudo.cat was not made by the build' }
# The catalog holds the Authenticode hash of the .sys, which an embedded signature does not change.
Invoke-Native $signtool.FullName @('sign', '/q', '/fd', 'SHA256', '/sha1', $cert.Thumbprint, '/s', 'My', $sysFile)
Invoke-Native $signtool.FullName @('sign', '/q', '/fd', 'SHA256', '/sha1', $cert.Thumbprint, '/s', 'My', $catFile)
# Check that the catalog covers the signed driver, with the certificate trusted the way the guest will trust it.
Import-Certificate -FilePath $cer -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
Invoke-Native $signtool.FullName @('verify', '/q', '/pa', '/c', $catFile, $sysFile)

# ---------------------------------------------------------------- what was built
$version = (Select-String -Path (Join-Path $driver 'viogpudo.inf') -Pattern '^\s*DriverVer\s*=\s*(.+)$' | Select-Object -First 1).Matches[0].Groups[1].Value.Trim()
$hashes = Get-ChildItem -Path $Out -Recurse -File | Sort-Object FullName | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.FullName.Substring($Out.Length + 1)
}
@(
    'viogpudo, test-signed build of mod_gns3lab (tools/viogpu-cursor-fix)',
    '',
    "source:      $Repo",
    "commit:      $Ref  ($subject)",
    "pull:        $Repo/pull/$Pr",
    "built:       $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm')) UTC, Win10 Release x64, WDK $(if ($wdk) { $wdk.Name })",
    "DriverVer:   $version",
    "HWCursor:    $HwCursor (written by the INF on install)",
    "certificate: CN=GNS3 lab viogpudo test signing, thumbprint $($cert.Thumbprint)",
    '',
    'SHA-256:'
) + $hashes | Set-Content -Path (Join-Path $Out 'BUILD.txt') -Encoding utf8
Get-Content (Join-Path $Out 'BUILD.txt') | Write-Host
