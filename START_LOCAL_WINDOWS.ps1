$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path .venv/Scripts/python.exe)) { python -m venv .venv }
$touchlinePython=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $touchlinePython -m pip install -r requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& $touchlinePython -m app.workflow backup
& $touchlinePython -m alembic upgrade head
if ($LASTEXITCODE) { throw 'Migration failed' }
& $touchlinePython -m app.workflow init
if ($LASTEXITCODE) { throw 'Initialization failed' }
$env:LOCAL_PREVIEW='true'
$env:SEED_DEMO='false'
$env:APP_ENV='development'
Write-Host 'In another PowerShell: cd frontend; npm install; npm run dev'
& $touchlinePython -m uvicorn app.main:app --host 127.0.0.1 --reload
