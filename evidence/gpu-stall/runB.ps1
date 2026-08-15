# Run B: longer GPU-busy phase + denser sampling, so gpu_ever_busy is observable.
# Data kept separate from run A.
$d = 'C:\gpu_stall_demo'
Set-Location $d
function Kill-ByCmdline($m) {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($m) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Kill-ByCmdline 'stall_train'; Kill-ByCmdline 'collect_signals'
Remove-Item "$d\runB" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$d\signalsB.jsonl" -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$d\runB" | Out-Null

Start-Process -FilePath 'D:\Anaconda\python.exe' `
  -ArgumentList "$d\stall_train.py","--outdir","$d\runB","--good-epochs","10","--steps","200","--stall-seconds","600" `
  -WorkingDirectory $d -RedirectStandardOutput "$d\trainB_out.txt" -RedirectStandardError "$d\trainB_err.txt" -WindowStyle Hidden
Start-Sleep -Seconds 2
Start-Process -FilePath 'D:\Anaconda\python.exe' `
  -ArgumentList "$d\collect_signals.py","--rundir","$d\runB","--procmatch","runB","--interval","10","--out","$d\signalsB.jsonl" `
  -WorkingDirectory $d -RedirectStandardOutput "$d\collectB_out.txt" -RedirectStandardError "$d\collectB_err.txt" -WindowStyle Hidden

Write-Output "LAUNCHED_B $(Get-Date -Format HH:mm:ss)"
Start-Sleep -Seconds 820
Write-Output "WAIT_DONE_B $(Get-Date -Format HH:mm:ss)"
Start-Sleep -Seconds 30
Kill-ByCmdline 'stall_train'; Kill-ByCmdline 'collect_signals'
Write-Output "SIGNALS_B=$((Get-Content "$d\signalsB.jsonl" -ErrorAction SilentlyContinue).Count)"
