# Connect DeepSeek Harness on this computer to a Gamma server: add the Gamma
# plugin to the dsh profiles here and save the connection, which the plugin
# reads whenever dsh starts. Gamma's Settings > AI > Integrations > DeepSeek
# Harness gives the command that runs this with the server's address; it
# opens Gamma's sign-in in the browser, where the user allows the connection,
# and collects its token, which is never shown.
# -Remove undoes it: the plugin leaves every profile that has it, and the
# saved connection is deleted.
[CmdletBinding(DefaultParameterSetName = 'Connect')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Connect')][string]$ServerUrl,
    # The one-time code a command from a Gamma without this sign-in carries.
    [Parameter(ParameterSetName = 'Connect')][string]$SetupCode = '',
    [Parameter(Mandatory = $true, ParameterSetName = 'Remove')][switch]$Remove,
    # The profiles to add Gamma to (default: the desktop app's, and web where it
    # exists) or to remove it from (default: every one that has it).
    [string[]]$Profiles = @(),
    # A dsh-gamma tarball to install instead of the release's (development).
    [Parameter(ParameterSetName = 'Connect')][string]$Bundle = ''
)
$ErrorActionPreference = 'Stop'

# Release packaging pins the version, the tarball and its digest into this file.
$gammaVersion = '__GAMMA_DSH_VERSION__'
$gammaDownload = '__GAMMA_DSH_URL__'
$gammaDigest = '__GAMMA_DSH_SHA256__'

$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME '.dsh' }
# The desktop app's own CLI manages its profile; a dsh on PATH or npx the others.
$desktopCli = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd'
} else { '' }
if (-not ($desktopCli -and (Test-Path -LiteralPath $desktopCli))) { $desktopCli = '' }
$pathCli = Get-Command dsh -CommandType Application, ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
$npx = Get-Command npx -CommandType Application, ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1

function Invoke-Dsh([string]$ProfileName, [string[]]$Arguments) {
    if (-not ($desktopCli -or $pathCli -or $npx)) {
        throw 'Install DeepSeek Harness first: https://github.com/deepseek-ai/deepseek-harness'
    }
    if ($ProfileName -eq 'desktop' -and $desktopCli) { & $desktopCli @Arguments | Out-Host }
    elseif ($pathCli) { & $pathCli.Source @Arguments | Out-Host }
    elseif ($npx) { & $npx.Source --yes '@deepseek-ai/dsh' @Arguments | Out-Host }
    else { & $desktopCli @Arguments | Out-Host }
    return $LASTEXITCODE
}

function Get-InstalledVersion([string]$ProfileName) {
    $manifest = Join-Path $dshHome "profiles\$ProfileName\node_modules\dsh-gamma\package.json"
    if (-not (Test-Path -LiteralPath $manifest)) { return '' }
    try { return [string](Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json).version } catch { return '' }
}

# Whether a profile depends on the plugin, which is what `remove` needs.
function Test-HasGamma([string]$ProfileName) {
    $manifest = Join-Path $dshHome "profiles\$ProfileName\package.json"
    return (Test-Path -LiteralPath $manifest) -and ((Get-Content -Raw -LiteralPath $manifest) -match '"dsh-gamma"')
}

$quitHint = 'If DeepSeek Harness is open, quit it completely, also from its tray icon, then run this command again.'

if ($Remove) {
    if (-not $Profiles) {
        $Profiles = @(Get-ChildItem -Directory -LiteralPath (Join-Path $dshHome 'profiles') -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Name })
    }
    foreach ($profileName in @($Profiles | Where-Object { Test-HasGamma $_ })) {
        Write-Host "Removing Gamma from the DeepSeek Harness $profileName profile..."
        if ((Invoke-Dsh $profileName @('plugin', '--profile', $profileName, 'remove', 'dsh-gamma')) -ne 0) {
            throw "Could not remove Gamma from the $profileName profile (see above). $quitHint"
        }
    }
    foreach ($file in @('gamma.json', 'dsh-gamma.tgz')) {
        Remove-Item -LiteralPath (Join-Path $dshHome $file) -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'Gamma is removed from DeepSeek Harness. In Gamma, disconnect it under Settings > AI > Integrations to end its access.'
    return
}

$gammaUri = $null
if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$gammaUri) -or $gammaUri.UserInfo -or
    $gammaUri.Query -or $gammaUri.Fragment -or $gammaUri.AbsolutePath -ne '/mcp' -or
    $gammaUri.Scheme -notin @('http', 'https')) {
    throw 'Copy the setup command from Gamma again: Settings > AI > Integrations > DeepSeek Harness.'
}
if ($SetupCode -and $SetupCode -notmatch '^[A-Za-z0-9_-]{20,128}$') {
    throw 'Copy the whole setup command from Gamma again.'
}

if (-not $Profiles) {
    $Profiles = @()
    if ($desktopCli) { $Profiles += 'desktop' }
    if (Test-Path -LiteralPath (Join-Path $dshHome 'profiles\web')) { $Profiles += 'web' }
    if (-not $Profiles) { $Profiles = @('web') }
}
if ('desktop' -in $Profiles -and -not (Test-Path -LiteralPath (Join-Path $dshHome 'profiles\desktop\package.json'))) {
    throw 'Open DeepSeek Harness once, quit it, and run this command again.'
}

# Add the plugin where it is missing or older; a profile that has this
# version keeps it, so connecting again needs no restart of the app first.
$pending = @($Profiles | Where-Object { $Bundle -or (Get-InstalledVersion $_) -ne $gammaVersion })
if ($pending -and -not ($desktopCli -or $pathCli -or $npx)) {
    throw 'Install DeepSeek Harness first: https://github.com/deepseek-ai/deepseek-harness'
}
if ($pending) {
    New-Item -ItemType Directory -Force -Path $dshHome | Out-Null
    # One file every profile names: pnpm cannot add a remote tarball it resolved before.
    $target = Join-Path $dshHome 'dsh-gamma.tgz'
    $partial = "$target.part"
    if ($Bundle) {
        Copy-Item -LiteralPath $Bundle -Destination $partial -Force
    } else {
        Write-Host 'Downloading the Gamma plugin...'
        Invoke-WebRequest -UseBasicParsing -Uri $gammaDownload -OutFile $partial
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $gammaDigest) {
            Remove-Item -LiteralPath $partial -Force
            throw 'The Gamma plugin download is incomplete or changed. Run the command again.'
        }
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
    foreach ($profileName in $pending) {
        Write-Host "Adding Gamma to the DeepSeek Harness $profileName profile..."
        if ((Invoke-Dsh $profileName @('plugin', '--profile', $profileName, 'add', $target)) -ne 0) {
            throw "Could not add Gamma to the $profileName profile (see above). $quitHint"
        }
    }
}

$gammaBase = $gammaUri.GetLeftPart([UriPartial]::Authority)
function Invoke-Gamma([string]$Path, [hashtable]$Body) {
    try {
        return Invoke-RestMethod -Method Post -Uri "$gammaBase$Path" -ContentType 'application/json' `
            -Body (ConvertTo-Json $Body)
    } catch {
        $detail = $null
        try { $detail = (ConvertFrom-Json $_.ErrorDetails.Message).detail } catch { }
        if ($detail) { throw $detail }
        throw "Could not reach Gamma at $gammaBase. $($_.Exception.Message)"
    }
}

Write-Host 'Connecting to Gamma...'
if ($SetupCode) {
    $claim = Invoke-Gamma '/api/integrations/claim' @{ code = $SetupCode }
} else {
    # The browser sign-in: the page shows the code printed here, and the
    # user ticks the workspaces DeepSeek Harness may read and allows it.
    $device = Invoke-Gamma '/api/integrations/device' @{ name = 'DeepSeek Harness' }
    if ([string]$device.user_code -cnotmatch '^[A-Z]{4}-[A-Z]{4}$' -or
        [string]$device.device_code -notmatch '^[A-Za-z0-9_-]{20,128}$') {
        throw "Gamma at $gammaBase did not start a sign-in. Copy the command from Gamma again."
    }
    # The address is built here, never taken from the reply, since it is opened.
    $signIn = "$gammaBase/?gamma_device=$($device.user_code)"
    Write-Host "Allow DeepSeek Harness in Gamma, in your browser. Check that the page shows $($device.user_code)."
    Write-Host "If no browser opens, go to $signIn"
    try { Start-Process $signIn } catch { }
    $interval = [Math]::Min([Math]::Max([int]$device.interval, 1), 10)
    $until = (Get-Date).AddSeconds([int]$device.expires_in)
    $claim = $null
    while (-not $claim.token) {
        if ((Get-Date) -gt $until) { throw 'The sign-in expired. Run the command again.' }
        Start-Sleep -Seconds $interval
        $claim = Invoke-Gamma '/api/integrations/device/token' @{ device_code = $device.device_code }
    }
}
New-Item -ItemType Directory -Force -Path $dshHome | Out-Null
# No byte-order mark: dsh reads the file as JSON.
[IO.File]::WriteAllText((Join-Path $dshHome 'gamma.json'), (ConvertTo-Json @{ url = $ServerUrl; token = $claim.token }),
    (New-Object Text.UTF8Encoding $false))
Write-Host 'Gamma is connected. Start DeepSeek Harness, or restart it if it is open: Gamma''s tools are mcp__gamma__...'
