[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [switch]$Sign,
    [string]$PfxPath,
    [string]$PfxPassword,
    [string]$CertificatePath
)

$ErrorActionPreference = 'Stop'
$Platform = 'x64'

$projectDir = $PSScriptRoot
$projectPath = Join-Path $projectDir 'SageGameBar.Package.wapproj'
$vswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$msbuildCandidates = @()

if (Test-Path -LiteralPath $vswherePath) {
    $installations = @(& $vswherePath -latest -products * -property installationPath)
    foreach ($installation in $installations) {
        if ($installation) {
            $msbuildCandidates += Join-Path $installation 'MSBuild\Current\Bin\MSBuild.exe'
            $msbuildCandidates += Join-Path $installation 'MSBuild\Current\Bin\amd64\MSBuild.exe'
        }
    }
}

$msbuildCandidates += @(
    'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe',
    'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe'
)

$msbuild = $msbuildCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $msbuild) {
    throw 'Visual Studio MSBuild was not found. Install the Visual Studio 2022 UWP workload.'
}

$manifestPath = Join-Path $projectDir 'Package.appxmanifest'
$manifest = [xml](Get-Content -LiteralPath $manifestPath)
$packageVersion = $manifest.Package.Identity.Version
$packagePublisher = $manifest.Package.Identity.Publisher
if (-not $packageVersion -or -not $packagePublisher) {
    throw 'Package.appxmanifest does not contain a complete Identity.'
}

$msbuildArgs = @(
    $projectPath,
    '/restore',
    '/t:Build',
    "/p:Configuration=$Configuration",
    "/p:Platform=$Platform",
    '/p:AppxBundle=Never',
    '/v:minimal'
)

if ($Sign) {
    if (-not $PfxPath -or -not (Test-Path -LiteralPath $PfxPath)) {
        throw 'With -Sign, provide a valid -PfxPath. Never commit the private key.'
    }
    if (-not $CertificatePath -or -not (Test-Path -LiteralPath $CertificatePath)) {
        throw 'With -Sign, provide the public -CertificatePath for the installer.'
    }

    $pfx = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        (Resolve-Path -LiteralPath $PfxPath),
        $PfxPassword,
        [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
    $publicCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        (Resolve-Path -LiteralPath $CertificatePath))
    if (-not $pfx.HasPrivateKey) {
        throw 'The signing PFX does not contain a private key.'
    }
    if ($pfx.Thumbprint -ne $publicCertificate.Thumbprint) {
        throw 'The signing PFX and public CER do not contain the same certificate.'
    }
    if ($pfx.Subject -ne $packagePublisher) {
        throw "Signing certificate subject must be $packagePublisher, but was $($pfx.Subject)."
    }
    if ($pfx.NotAfter -le (Get-Date).AddDays(30)) {
        throw "Signing certificate expires too soon: $($pfx.NotAfter)."
    }

    $msbuildArgs += '/p:AppxPackageSigningEnabled=true'
    $msbuildArgs += "/p:PackageCertificateKeyFile=$((Resolve-Path -LiteralPath $PfxPath).Path)"
    $msbuildArgs += "/p:PackageCertificatePassword=$PfxPassword"
    $msbuildArgs += "/p:PackageCertificateThumbprint=$($pfx.Thumbprint)"
}
else {
    $msbuildArgs += '/p:AppxPackageSigningEnabled=false'
}

Write-Host "[MSBuild] $msbuild"
& $msbuild @msbuildArgs
if ($LASTEXITCODE -ne 0) {
    $firstExitCode = $LASTEXITCODE
    Write-Warning "MSBuild failed once with exit code $firstExitCode; retrying after the .NET Native warm-up pass."
    & $msbuild @msbuildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "MSBuild failed twice; final exit code $LASTEXITCODE"
    }
}

$packageDir = Join-Path $projectDir "AppPackages\SageGameBar.Package_${packageVersion}_${Platform}_Test"
$package = Get-ChildItem -LiteralPath $packageDir -File -Filter '*.msix' -ErrorAction Stop | Select-Object -First 1
if (-not $package) {
    $package = Get-ChildItem -LiteralPath $packageDir -File -Filter '*.appx' -ErrorAction Stop | Select-Object -First 1
}

if (-not $package) {
    throw "No MSIX/AppX output was found in $packageDir"
}

if ($Sign) {
    Copy-Item -LiteralPath $CertificatePath -Destination (Join-Path $packageDir (Split-Path $CertificatePath -Leaf)) -Force
}

Write-Host "[Done] $($package.FullName)"
Write-Host '[Info] For local development registration, install dependencies first, then run Add-AppxPackage -Register AppxManifest.xml from the package root.'
if (-not $Sign) {
    Write-Host '[Info] The output is unsigned. A self-signed production test package must be signed and shipped with its public .cer file.'
}
