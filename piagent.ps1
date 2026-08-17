[CmdletBinding()]
param(
    [ValidateSet("review", "full", "edit")]
    [string]$Mode = "review",
    [string]$EditPath,
    [string]$Session = "chat-main",
    [switch]$Mcp,
    [switch]$Check,
    [switch]$Help
)

function Show-Usage {
    @"
Usage: .\piagent.ps1 [-Mode review|full|edit] [-EditPath <path>] [-Session <name>] [-Mcp] [-Check]

Start PiAgent chat through Docker Compose.

  -Mode review   Read, search, and plan only (default)
  -Mode full     Allow configured tools, including file creation and tests
  -Mode edit     Allow one focused edit in an existing file; requires -EditPath
  -Session name  Reuse a session (default: chat-main)
  -Mcp           Enable MCP connections (disabled by default)
  -Check         Verify the Docker/PiAgent setup without calling a model
  -Help          Show this help
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

if ($Mode -eq "edit" -and [string]::IsNullOrWhiteSpace($EditPath)) {
    Write-Error "-Mode edit requires -EditPath <existing workspace-relative path>."
    exit 2
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker Desktop (with Docker Compose v2) is required."
    exit 1
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker Compose v2 is not available."
    exit 1
}

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example."
    Write-Host "Add OPENAI_API_KEY or the three Local Bedrock variables, then run this command again."
    exit 2
}

& docker compose up -d --build pi_agent
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$containerArgs = @("exec", "pi_agent", "/app/scripts/piagent-container.sh")
if ($Check) {
    $containerArgs += "--check"
} else {
    $containerArgs += "--$Mode"
    $containerArgs += @("--session", $Session)
}
if ($Mode -eq "edit") {
    $containerArgs += $EditPath
}
if ($Mcp) {
    $containerArgs += "--mcp"
}

& docker compose @containerArgs
exit $LASTEXITCODE
