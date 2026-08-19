# ============================================================
# One-click pgvector deployment for PostgreSQL 18 (Windows)
# Usage: Right-click -> Run with PowerShell (Administrator required)
# ============================================================

$ErrorActionPreference = "Stop"

$extractDir = Join-Path $env:TEMP "pgvector_extract"
$pgRoot = "D:\PostgreSQL\18"
$binPsql = Join-Path $pgRoot "bin\psql.exe"
$pgData = Join-Path $pgRoot "data"
$pgHba  = Join-Path $pgData "pg_hba.conf"
$pass    = "JGYjw820125"

# --- 0. Pre-check: PostgreSQL 18 exists? ---
if (-not (Test-Path $pgRoot)) {
    throw "PostgreSQL 18 not found at $pgRoot . Please install it first."
}
if (-not (Test-Path $binPsql)) {
    throw "psql.exe not found at $binPsql . Check PG install."
}

# --- 1. Download & extract pgvector 0.8.6 for PG18 if not cached ---
if (-not (Test-Path $extractDir)) {
    $downloadUrl = "https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/0.8.6_18/vector.v0.8.6-pg18.zip"
    $destZip = Join-Path $env:TEMP "pgvector_pg18.zip"
    Write-Host "[1/7] Downloading pgvector prebuilt zip -> $destZip" -ForegroundColor Cyan
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $downloadUrl -OutFile $destZip -UseBasicParsing
    Write-Host "[1/7] Download done, size: $((Get-Item $destZip).Length) bytes"
    Expand-Archive -Path $destZip -DestinationPath $extractDir -Force
}

# --- 2. Copy plugin files into PG root (lib / share / include) ---
Write-Host "[2/7] Copying include/lib/share into $pgRoot ..." -ForegroundColor Cyan
Copy-Item -Path (Join-Path $extractDir "include\*") -Destination (Join-Path $pgRoot "include") -Recurse -Force -ErrorAction Stop
Copy-Item -Path (Join-Path $extractDir "lib\*")     -Destination (Join-Path $pgRoot "lib")     -Recurse -Force -ErrorAction Stop
Copy-Item -Path (Join-Path $extractDir "share\*") -Destination (Join-Path $pgRoot "share") -Recurse -Force -ErrorAction Stop

# --- 3. Verify plugin files exist ---
Write-Host "[3/7] Verifying plugin files ..." -ForegroundColor Cyan
$dll  = Test-Path (Join-Path $pgRoot "lib\vector.dll")
$ctrl = Test-Path (Join-Path $pgRoot "share\extension\vector.control")
Write-Host "  vector.dll      : $dll"
Write-Host "  vector.control  : $ctrl"
if (-not $dll -or -not $ctrl) {
    throw "pgvector plugin files missing after copy. Check write permission to $pgRoot"
}

# --- 4. Temporarily switch pg_hba.conf to TRUST auth (no-password) ---
Write-Host "[4/7] Setting pg_hba.conf to TRUST (temporarily, to reset postgres password + create extension) ..." -ForegroundColor Cyan
if (-not (Test-Path $pgHba)) {
    throw "pg_hba.conf not found at $pgHba . PG data folder mismatch?"
}
$hbaContent = Get-Content -Path $pgHba -Raw -Encoding UTF8
$hbaBakPath = "$pgHba.bak_before_pgvector_$(Get-Date -Format yyyyMMddHHmmss)"
Copy-Item -Path $pgHba -Destination $hbaBakPath -Force
Write-Host "  (Backup saved to $hbaBakPath)"
function Replace-Hba([ref]$content, [string]$pattern, [string]$repl) {
    $content.Value = [regex]::Replace($content.Value, $pattern, $repl, [System.Text.RegularExpressions.RegexOptions]::Multiline)
}
Replace-Hba ([ref]$hbaContent) "(?m)^local\s+all\s+all\s+\S+$"                   "local   all             all                                     trust"
Replace-Hba ([ref]$hbaContent) "(?m)^host\s+all\s+all\s+127\.0\.0\.1/32\s+\S+$"    "host    all             all             127.0.0.1/32            trust"
Replace-Hba ([ref]$hbaContent) "(?m)^host\s+all\s+all\s+::1/128\s+\S+$"             "host    all             all             ::1/128                 trust"
Replace-Hba ([ref]$hbaContent) "(?m)^local\s+replication\s+all\s+\S+$"              "local   replication     all                                     trust"
Replace-Hba ([ref]$hbaContent) "(?m)^host\s+replication\s+all\s+127\.0\.0\.1/32\s+\S+$" "host    replication     all             127.0.0.1/32            trust"
Replace-Hba ([ref]$hbaContent) "(?m)^host\s+replication\s+all\s+::1/128\s+\S+$"        "host    replication     all             ::1/128                 trust"
Set-Content -Path $pgHba -Value $hbaContent -Encoding UTF8 -NoNewline
Write-Host "  pg_hba.conf switched to TRUST. Sandbox CANNOT reload PG service - you MUST restart postgresql-x64-18 AFTER this script manually (services.msc), then come back and re-run this script again from step 5." -ForegroundColor Yellow

# --- 5. Create database + reset postgres password (TRUST must be active first) ---
Write-Host "[5/7] Create DB teaching_warning if missing, then ALTER postgres PASSWORD to $pass ..." -ForegroundColor Cyan
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
# 5a. Ensure DB exists
& $binPsql -U postgres -h 127.0.0.1 -p 5432 -d postgres -c "SELECT 1 FROM pg_database WHERE datname = 'teaching_warning'" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  TRUST auth NOT working yet. This means you need to: 1) services.msc -> restart postgresql-x64-18 first,  2) RE-RUN this ps1 script again. Stopping here for now." -ForegroundColor Red
    Write-Host ""
    Write-Host "=== MANUAL STEP (DO THIS BEFORE RE-RUN) ===" -ForegroundColor Yellow
    Write-Host "  1. Win+R  type  services.msc  Enter"
    Write-Host "  2. Right-click  [postgresql-x64-18]  -> Restart"
    Write-Host "  3. After restart -> re-run:  & `"D:\A教育agent\_setup_pgvector.ps1`""
    Write-Host ""
    Pause
    exit 1
}
# (If we reach here, TRUST auth OK now)
& $binPsql -U postgres -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE teaching_warning;" 2>&1 | Out-Null
$alterSql = "ALTER USER postgres WITH PASSWORD '$pass';"
& $binPsql -U postgres -h 127.0.0.1 -p 5432 -d postgres -c $alterSql
# 5b. Create vector extension inside teaching_warning
& $binPsql -U postgres -h 127.0.0.1 -p 5432 -d teaching_warning -c "CREATE EXTENSION IF NOT EXISTS vector;"
Write-Host "  pg_extension listing:"
& $binPsql -U postgres -h 127.0.0.1 -p 5432 -d teaching_warning -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"

# --- 6. Restore pg_hba.conf to scram-sha-256 (secure) ---
Write-Host "[6/7] Restoring pg_hba.conf to scram-sha-256 auth ..." -ForegroundColor Cyan
$hbaFinal = Get-Content -Path $pgHba -Raw -Encoding UTF8
Replace-Hba ([ref]$hbaFinal) "(?m)^local\s+all\s+all\s+trust$"                   "local   all             all                                     scram-sha-256"
Replace-Hba ([ref]$hbaFinal) "(?m)^host\s+all\s+all\s+127\.0\.0\.1/32\s+trust$"    "host    all             all             127.0.0.1/32            scram-sha-256"
Replace-Hba ([ref]$hbaFinal) "(?m)^host\s+all\s+all\s+::1/128\s+trust$"             "host    all             all             ::1/128                 scram-sha-256"
Replace-Hba ([ref]$hbaFinal) "(?m)^local\s+replication\s+all\s+trust$"              "local   replication     all                                     scram-sha-256"
Replace-Hba ([ref]$hbaFinal) "(?m)^host\s+replication\s+all\s+127\.0\.0\.1/32\s+trust$" "host    replication     all             127.0.0.1/32            scram-sha-256"
Replace-Hba ([ref]$hbaFinal) "(?m)^host\s+replication\s+all\s+::1/128\s+trust$"        "host    replication     all             ::1/128                 scram-sha-256"
Set-Content -Path $pgHba -Value $hbaFinal -Encoding UTF8 -NoNewline

# --- 7. Apply 001_init.sql DDL (tables + views + HNSW index) ---
Write-Host "[7/7] Applying 001_init.sql DDL to teaching_warning ..." -ForegroundColor Cyan
$env:PGPASSWORD = $pass
$initSql = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "server\db\migrations\001_init.sql"
if (Test-Path $initSql) {
    & $binPsql -U postgres -h 127.0.0.1 -p 5432 -d teaching_warning -f $initSql
} else {
    Write-Host "  WARNING: 001_init.sql not found at $initSql . Skipped DDL (you can import it later via pgAdmin)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================" -ForegroundColor Green
Write-Host " pgvector deploy script DONE  " -ForegroundColor Green
Write-Host "==============================" -ForegroundColor Green
Write-Host ""
Write-Host "MANUAL STEP (REQUIRED NOW):" -ForegroundColor Yellow
Write-Host "  1. Win+R -> services.msc -> Enter"
Write-Host "  2. Right-click [postgresql-x64-18] -> Restart"
Write-Host "     (reloads pg_hba.conf scram-sha-256 + loads new vector.so DLL)"
Write-Host "  3. Then back to TRAE, click Continue to validate env + ingest real CSV"
Write-Host ""
Write-Host "Quick verify cmd (after restart):"
Write-Host "  $binPsql -U postgres -h 127.0.0.1 -d teaching_warning -c `"SELECT * FROM pg_extension WHERE extname='vector';`""
Write-Host "  Password: $pass"
Write-Host ""
Pause