# Peak working set of the study's engine processes, sampled every 0.5 s until the stop file appears.
#   powershell -File mem_sampler.ps1 -Out peaks.json -StopFile stop.flag
# A process is labelled by its name (llama-server) or, for python, by the script it runs
# (detect.py / recognize.py) and its --model / --engine argument.
param([string]$Out, [string]$StopFile)
$peaks = @{}
while (-not (Test-Path $StopFile)) {
    foreach ($p in Get-CimInstance Win32_Process -Filter "Name='llama-server.exe' or Name='python.exe'") {
        $cmd = [string]$p.CommandLine
        $label = $null
        if ($p.Name -eq 'llama-server.exe') {
            if ($cmd -match '\s-t\s+(\d+)') { $label = "llama-server t$($Matches[1])" } else { $label = 'llama-server tdefault' }
            if ($cmd -match 'mmproj') { if ($cmd -match '([\w\.-]+)\.gguf') { $label += " $($Matches[1])" } }
        } elseif ($cmd -match '(detect|recognize)\.py' -and $cmd -match '--(model|engine)\s+(\S+)') {
            $label = "$($Matches[2])"
        }
        if ($label) {
            $ws = [int64]$p.WorkingSetSize
            if (-not $peaks.ContainsKey($label) -or $peaks[$label] -lt $ws) { $peaks[$label] = $ws }
        }
    }
    Start-Sleep -Milliseconds 500
}
$res = @{}
foreach ($k in $peaks.Keys) { $res[$k] = [math]::Round($peaks[$k] / 1MB) }
$res | ConvertTo-Json | Out-File -Encoding utf8 $Out
