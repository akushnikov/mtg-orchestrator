# =============================================================================
# verify-infra.ps1 — MTG Orchestrator Phase 1 static verification (Windows)
#
# Usage: .\scripts\verify-infra.ps1 [[-EnvFile] <path>]
#
# Runs deterministic checks against the repository on the local Windows
# development machine. Does NOT start containers or make network requests.
# Prints the manual VPS smoke-test commands for the operator to run on
# the Frankfurt VPS after deploying the stack.
#
# Exit code: 0 if all static checks pass; 1 if any check fails.
# =============================================================================

param(
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Continue"
$ScriptRoot = Split-Path -Parent $PSScriptRoot
if (-not $ScriptRoot) { $ScriptRoot = Get-Location }

$Passed  = 0
$Failed  = 0
$Skipped = 0

function Write-Check {
    param([string]$Description, [bool]$Result, [string]$Detail = "")
    if ($Result) {
        Write-Host "  PASS  $Description" -ForegroundColor Green
        $script:Passed++
    } else {
        Write-Host "  FAIL  $Description" -ForegroundColor Red
        if ($Detail) { Write-Host "        $Detail" -ForegroundColor Yellow }
        $script:Failed++
    }
}

function Write-Skip {
    param([string]$Description, [string]$Reason)
    Write-Host "  SKIP  $Description ($Reason)" -ForegroundColor Cyan
    $script:Skipped++
}

Write-Host ""
Write-Host "=== MTG Orchestrator Phase 1 — Static Verification ===" -ForegroundColor White
Write-Host "Working directory: $ScriptRoot"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. docker compose config
# ---------------------------------------------------------------------------
Write-Host "[ 1 ] Compose configuration" -ForegroundColor White

$HasDocker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $HasDocker) {
    Write-Skip "docker compose config" "docker not found"
} else {
    Push-Location $ScriptRoot
    try {
        # Load .env if it exists so compose can resolve variables
        $EnvPath = Join-Path $ScriptRoot $EnvFile
        if (Test-Path $EnvPath) {
            $ComposeArgs = @("compose", "--env-file", $EnvPath, "config", "--quiet")
        } else {
            # Use .env.example as a fallback for static checking
            $ExampleEnv = Join-Path $ScriptRoot ".env.example"
            if (Test-Path $ExampleEnv) {
                $ComposeArgs = @("compose", "--env-file", $ExampleEnv, "config", "--quiet")
                Write-Host "        Using .env.example (real .env not found — static check only)" -ForegroundColor Yellow
            } else {
                $ComposeArgs = @("compose", "config", "--quiet")
            }
        }
        $Output = & docker @ComposeArgs 2>&1
        $Result = ($LASTEXITCODE -eq 0)
        Write-Check "docker compose config" $Result $(if (-not $Result) { $Output | Out-String })
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# 2. nginx config validation
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 2 ] nginx configuration" -ForegroundColor White

$NginxTmpl = Join-Path $ScriptRoot "infra\nginx\nginx.conf.template"
if (-not (Test-Path $NginxTmpl)) {
    Write-Check "infra/nginx/nginx.conf.template exists" $false "File not found: $NginxTmpl"
} else {
    Write-Check "infra/nginx/nginx.conf.template exists" $true

    if ($HasDocker) {
        # The template is rendered by the nginx image entrypoint (envsubst)
        # into /etc/nginx/nginx.conf at container start. Mount it at
        # /etc/nginx/templates/ and let the entrypoint render before nginx -t,
        # so the test exercises the same rendering the running container uses.
        Push-Location $ScriptRoot
        try {
            $RenderDomain = if ($env:PANEL_DOMAIN) { $env:PANEL_DOMAIN } else { "panel.example.test" }
            $TmplMount = "${ScriptRoot}/infra/nginx/nginx.conf.template:/etc/nginx/templates/nginx.conf.template:ro"
            $NginxOut = & docker run --rm `
                -e "PANEL_DOMAIN=$RenderDomain" `
                -e "NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx" `
                -e "NGINX_ENVSUBST_FILTER=PANEL_DOMAIN" `
                -v $TmplMount nginx:1.27-alpine nginx -t 2>&1
            Write-Check "nginx -t (rendered template syntax)" ($LASTEXITCODE -eq 0) $($NginxOut | Out-String)

            # CR-04: fail if any unsubstituted ${...} placeholder survives in
            # the RENDERED config (missing/empty PANEL_DOMAIN or other vars).
            $Rendered = & docker run --rm `
                -e "PANEL_DOMAIN=$RenderDomain" `
                -e "NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx" `
                -e "NGINX_ENVSUBST_FILTER=PANEL_DOMAIN" `
                -v $TmplMount --entrypoint sh nginx:1.27-alpine `
                -c '/docker-entrypoint.d/20-envsubst-on-templates.sh >/dev/null 2>&1; cat /etc/nginx/nginx.conf' 2>&1 | Out-String
            Write-Check "no unsubstituted `${...} placeholders in rendered nginx.conf" (-not ($Rendered -match '\$\{')) "rendered config still contains `${...} — check PANEL_DOMAIN / envsubst"
        } catch {
            Write-Skip "nginx -t (rendered template syntax)" "docker run failed: $_"
        } finally {
            Pop-Location
        }
    } else {
        Write-Skip "nginx -t (rendered template syntax)" "docker not available"
        Write-Skip "no unsubstituted `${...} placeholders" "docker not available"
    }

    # Check required directives in the template source
    $NginxContent = Get-Content $NginxTmpl -Raw
    Write-Check "ssl_preread on" ($NginxContent -match "ssl_preread\s+on")
    Write-Check "resolver 127.0.0.11 valid=10s ipv6=off" ($NginxContent -match "resolver\s+127\.0\.0\.11.*ipv6=off")
    Write-Check "default routes to mtg-default (not backend)" ($NginxContent -match "default\s+mtg-default")
    Write-Check "http block serves /.well-known/acme-challenge/" ($NginxContent -match "acme-challenge")
}

# ---------------------------------------------------------------------------
# 3. mtg config validation
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 3 ] mtg default config" -ForegroundColor White

$MtgConf = Join-Path $ScriptRoot "infra\mtg\default.config.toml"
if (-not (Test-Path $MtgConf)) {
    Write-Check "infra/mtg/default.config.toml exists" $false "File not found"
} else {
    Write-Check "infra/mtg/default.config.toml exists" $true
    $MtgContent = Get-Content $MtgConf -Raw
    Write-Check "No real secret committed (no 'ee' hex line)" (-not ($MtgContent -match '(?m)^\s*secret\s*=\s*"ee[0-9a-f]{32}'))
    Write-Check "[stats.prometheus] section present" ($MtgContent -match "\[stats\.prometheus\]")
    Write-Check "blocked-subnets = [] (RFC1918 disabled)" ($MtgContent -match "blocked-subnets\s*=\s*\[\s*\]")
    Write-Check "bind-to uses 0.0.0.0 (not 127.0.0.1)" ($MtgContent -match "bind-to\s*=\s*""0\.0\.0\.0")
}

# ---------------------------------------------------------------------------
# 4. .env.example completeness
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 4 ] .env.example" -ForegroundColor White

$EnvExample = Join-Path $ScriptRoot ".env.example"
if (-not (Test-Path $EnvExample)) {
    Write-Check ".env.example exists" $false
} else {
    Write-Check ".env.example exists" $true
    $EnvLines = Get-Content $EnvExample
    $RequiredVars = @(
        "PANEL_DOMAIN", "LE_EMAIL", "MOSCOW_IP", "FRANKFURT_IP",
        "MTG_DEFAULT_SECRET", "MTG_DEFAULT_PORT", "MTG_DEFAULT_DOMAIN",
        "BOT_TOKEN", "OWNER_USER_ID", "WEBHOOK_SECRET", "CERTBOT_STAGING"
    )
    foreach ($Var in $RequiredVars) {
        $Found = $EnvLines | Where-Object { $_ -match "^${Var}=" }
        Write-Check ".env.example has $Var" ($null -ne $Found -and $Found.Count -gt 0)
    }

    # Ensure placeholder values (not real secrets)
    $EnvContent = Get-Content $EnvExample -Raw
    Write-Check "MTG_DEFAULT_SECRET is placeholder (ee + zeros)" ($EnvContent -match "MTG_DEFAULT_SECRET=ee0{38}")
    Write-Check "BOT_TOKEN is placeholder" ($EnvContent -match "BOT_TOKEN=\d+:A{5,}")
}

# ---------------------------------------------------------------------------
# 5. Log rotation on all services
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 5 ] Log rotation" -ForegroundColor White

$ComposeFile = Join-Path $ScriptRoot "docker-compose.yml"
if (-not (Test-Path $ComposeFile)) {
    Write-Check "docker-compose.yml exists" $false
} else {
    Write-Check "docker-compose.yml exists" $true
    $ComposeContent = Get-Content $ComposeFile -Raw

    $Services = @("nginx", "backend", "docker-socket-proxy", "certbot", "mtg-default")
    foreach ($Svc in $Services) {
        # Check that max-size and max-file appear after the service block
        # Simple check: count max-size occurrences (one per service)
        $SvcPattern = "(?s)${Svc}:.*?max-size"
        Write-Check "$Svc has log rotation (max-size)" ($ComposeContent -match $SvcPattern)
    }
}

# ---------------------------------------------------------------------------
# 6. Security invariants
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 6 ] Security invariants" -ForegroundColor White

if (Test-Path $ComposeFile) {
    $ComposeLines = Get-Content $ComposeFile

    # socket-net internal: true
    $SocketNetIdx = ($ComposeLines | Select-String -Pattern "^\s*socket-net:" | Select-Object -First 1).LineNumber
    $InternalFound = $false
    if ($SocketNetIdx) {
        $LookAhead = $ComposeLines[($SocketNetIdx)..([Math]::Min($SocketNetIdx + 10, $ComposeLines.Count - 1))]
        $InternalFound = ($LookAhead | Where-Object { $_ -match "internal:\s*true" }).Count -gt 0
    }
    Write-Check "socket-net declared internal: true" $InternalFound

    # Docker socket only in socket-proxy (count actual volume mount lines, not comments)
    $SockMountLines = $ComposeLines | Where-Object { $_ -match "docker\.sock:/var/run/docker\.sock" }
    Write-Check "docker.sock mount appears only once (socket-proxy)" ($SockMountLines.Count -eq 1)

    # docker-socket-proxy not on proxy-net — use docker compose config output for accuracy
    # (text scan of YAML is unreliable due to comments mentioning network names)
    $DockerAvail = Get-Command docker -ErrorAction SilentlyContinue
    if ($DockerAvail) {
        Push-Location $ScriptRoot
        try {
            $EnvPath = Join-Path $ScriptRoot ".env"
            $ExampleEnv = Join-Path $ScriptRoot ".env.example"
            $EnvFlag = if (Test-Path $EnvPath) { @("--env-file", $EnvPath) } elseif (Test-Path $ExampleEnv) { @("--env-file", $ExampleEnv) } else { @() }
            $RenderedCompose = & docker compose @EnvFlag config 2>&1 | Out-String

            # In rendered compose, check network assignments per-service.
            # Parse line by line: collect each service's networks block.
            $RenderLines = $RenderedCompose -split "`n"
            $InSvc = ""
            $InNetworks = $false
            $SvcNetworks = @{}
            foreach ($Line in $RenderLines) {
                if ($Line -match "^  ([a-z][a-z0-9_-]+):$") {
                    $InSvc = $Matches[1]
                    $InNetworks = $false
                    if (-not $SvcNetworks.ContainsKey($InSvc)) { $SvcNetworks[$InSvc] = @() }
                } elseif ($Line -match "^    networks:$") {
                    $InNetworks = $true
                } elseif ($InNetworks -and $Line -match "^      ([a-z][a-z0-9_-]+):") {
                    $SvcNetworks[$InSvc] += $Matches[1]
                } elseif ($InNetworks -and $Line -notmatch "^      ") {
                    $InNetworks = $false
                }
            }

            $SockProxyNets = if ($SvcNetworks.ContainsKey("docker-socket-proxy")) { $SvcNetworks["docker-socket-proxy"] } else { @() }
            Write-Check "docker-socket-proxy not on proxy-net" (-not ($SockProxyNets -contains "proxy-net"))

            $MtgDefaultNets = if ($SvcNetworks.ContainsKey("mtg-default")) { $SvcNetworks["mtg-default"] } else { @() }
            Write-Check "mtg-default not on socket-net" (-not ($MtgDefaultNets -contains "socket-net"))
        } finally {
            Pop-Location
        }
    } else {
        Write-Skip "docker-socket-proxy not on proxy-net" "docker not available for config render"
        Write-Skip "mtg-default not on socket-net" "docker not available for config render"
    }
}

# ---------------------------------------------------------------------------
# 7. README completeness
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[ 7 ] README deployment guide" -ForegroundColor White

$Readme = Join-Path $ScriptRoot "README.md"
if (-not (Test-Path $Readme)) {
    Write-Check "README.md exists" $false
} else {
    Write-Check "README.md exists" $true
    $ReadmeContent = Get-Content $Readme -Raw
    Write-Check "README has 'docker compose up -d'" ($ReadmeContent -match "docker compose up")
    Write-Check "README has 'openssl s_client'" ($ReadmeContent -match "openssl s_client")
    Write-Check "README has curl https smoke check" ($ReadmeContent -match "curl.*https://")
    Write-Check "README has log rotation check" ($ReadmeContent -match "LogConfig|max-size|log rotation")
    Write-Check "README has network isolation check" ($ReadmeContent -match "Internal.*true|socket-net")
    Write-Check "README has certbot webroot instructions" ($ReadmeContent -match "certonly.*webroot|--webroot")
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Static Check Summary ===" -ForegroundColor White
Write-Host "  Passed:  $Passed" -ForegroundColor Green
Write-Host "  Failed:  $Failed" -ForegroundColor $(if ($Failed -gt 0) { "Red" } else { "Green" })
Write-Host "  Skipped: $Skipped" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# VPS Smoke-Test Checklist (printed for operator reference)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== VPS Smoke-Test Checklist (run on Frankfurt VPS after deploy) ===" -ForegroundColor White
Write-Host @"

The following checks CANNOT be performed locally. Run them on the Frankfurt VPS
after completing the bootstrap steps in README.md.

1. Compose services are up
   docker compose ps
   # Expected: nginx, backend, mtg-default, docker-socket-proxy — Up

2. Backend health
   curl -s http://localhost:8080/healthz
   # Expected: {"status":"ok"}

3. Panel TLS (after certbot issuance)
   # Replace <PANEL_DOMAIN> with your subdomain from .env
   curl -v https://<PANEL_DOMAIN>/healthz
   # Expected: HTTP/2 200, valid Let's Encrypt certificate

4. Empty/no-SNI → mtg (NOT panel)
   # Replace <MOSCOW_IP> with your Moscow relay IP from .env
   openssl s_client -connect <MOSCOW_IP>:443 </dev/null 2>&1 | head -20
   # Expected: fake-TLS handshake from mtg; NOT a panel response.
   # The certificate returned should NOT be your Let's Encrypt panel cert.

5. Panel SNI → backend
   openssl s_client -connect <MOSCOW_IP>:443 -servername <PANEL_DOMAIN> </dev/null 2>&1 | head -20
   # Expected: valid Let's Encrypt certificate for <PANEL_DOMAIN>

6. Log rotation active on all containers
   docker inspect mtg-orchestrator-nginx-1 | grep -A5 LogConfig
   # Expected: "max-size": "10m", "max-file": "3"

7. Network isolation (socket-net internal)
   docker network inspect mtg-orchestrator_socket-net | grep Internal
   # Expected: "Internal": true

8. backend has NO direct docker.sock mount
   docker inspect mtg-orchestrator-backend-1 | grep docker.sock
   # Expected: no output (empty)

9. Moscow port 80 forwarding (from external machine, NOT from Moscow VPS)
   curl -v http://<PANEL_DOMAIN>/.well-known/acme-challenge/test
   # Expected: 404 from nginx (connection reaches Frankfurt — file doesn't exist, that is OK)

10. certbot dry run
    docker compose run --rm certbot certonly --dry-run --webroot -w /var/www/certbot \
      -d <PANEL_DOMAIN> --email <LE_EMAIL> --agree-tos --no-eff-email
    # Expected: "The dry run was successful."
"@

Write-Host ""
if ($Failed -gt 0) {
    Write-Host "Static checks FAILED ($Failed failure(s)). Fix before deploying to VPS." -ForegroundColor Red
    exit 1
} else {
    Write-Host "All static checks PASSED. Proceed with VPS deployment per README.md." -ForegroundColor Green
    exit 0
}
