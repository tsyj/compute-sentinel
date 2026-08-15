$d = 'C:\gpu_stall_demo'
Set-Location $d
Remove-Item "$d\run" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$d\signals.jsonl" -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$d\run" | Out-Null

function Kill-ByCmdline($m) {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($m) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Kill-ByCmdline 'stall_train'; Kill-ByCmdline 'collect_signals'

Start-Process -FilePath 'D:\Anaconda\python.exe' `
  -ArgumentList "$d\stall_train.py","--outdir","$d\run","--good-epochs","3","--steps","60","--stall-seconds","1200" `
  -WorkingDirectory $d -RedirectStandardOutput "$d\train_out.txt" -RedirectStandardError "$d\train_err.txt" -WindowStyle Hidden
Start-Sleep -Seconds 3
Start-Process -FilePath 'D:\Anaconda\python.exe' `
  -ArgumentList "$d\collect_signals.py","--rundir","$d\run","--procmatch","stall_train","--interval","20","--out","$d\signals.jsonl" `
  -WorkingDirectory $d -RedirectStandardOutput "$d\collect_out.txt" -RedirectStandardError "$d\collect_err.txt" -WindowStyle Hidden

Write-Output "LAUNCHED $(Get-Date -Format HH:mm:ss)"
# 不依赖 PID：固定等到训练必然结束（约 25s 正常 + 1200s 卡死）之后再收尾
Start-Sleep -Seconds 1320
Write-Output "WAIT_DONE $(Get-Date -Format HH:mm:ss)"
Start-Sleep -Seconds 45
Kill-ByCmdline 'stall_train'; Kill-ByCmdline 'collect_signals'
Write-Output "SIGNALS_LINES=$((Get-Content "$d\signals.jsonl" -ErrorAction SilentlyContinue).Count)"
