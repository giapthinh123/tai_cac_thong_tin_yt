$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "Chưa có .venv. Tạo venv và cài: pip install -r requirements.txt"
}

# Cài đặt setuptools để sửa lỗi pkg_resources cho các bản PyInstaller cũ
& $py -m pip install setuptools wheel -q
if ($LASTEXITCODE -ne 0) { throw "Lỗi khi cài đặt setuptools!" }

# Cài đặt PyInstaller (bỏ giới hạn >=6.0 nếu bạn tiếp tục dùng Python 3.15)
& $py -m pip install pyinstaller -q
if ($LASTEXITCODE -ne 0) { throw "Lỗi khi cài đặt PyInstaller!" }

& $py -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "YouTubeDownloader" `
    --add-data "thum.qss;." `
    --collect-all yt_dlp `
    "app.py"

Write-Host "Xong: dist\YouTubeDownloader.exe"