$ErrorActionPreference = "Stop"

if (!(Test-Path ".venv")) {
    py -3.12 -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Создан .env. Заполни API ключи и запусти start.ps1 еще раз." -ForegroundColor Yellow
    exit 0
}

python -m app.main
