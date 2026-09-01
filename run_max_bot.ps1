param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("polling", "webhook", "cli")]
    [string]$Mode,

    [string]$BotToken = "",

    [string]$PublicUrl = "",
    [int]$Port = 8080,
    [string]$Path = "/webhook",
    [string]$Secret = ""
)

$ErrorActionPreference = "Stop"

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) {
            return
        }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) {
            return
        }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

Import-DotEnv ".env"

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*max_bot_core.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

if ($Mode -eq "cli") {
    $env:MAX_MODE = "cli"
    Write-Host "CLI mode: open library API, no MAX token required."
    & ".\.venv\Scripts\python.exe" ".\max_bot_core.py"
    exit $LASTEXITCODE
}

if ([string]::IsNullOrWhiteSpace($BotToken)) {
    $BotToken = $env:MAX_BOT_TOKEN
}

if ([string]::IsNullOrWhiteSpace($BotToken)) {
    throw "BotToken is required (set MAX_BOT_TOKEN in .env or pass -BotToken)."
}

$env:MAX_API_BASE_URL = if ($env:MAX_API_BASE_URL) { $env:MAX_API_BASE_URL } else { "https://platform-api2.max.ru" }
$env:MAX_BOT_TOKEN = $BotToken

if ($Mode -eq "polling") {
    $env:MAX_MODE = "long_polling"
    Write-Host "Starting MAX bot in long polling mode..."
}
else {
    if ([string]::IsNullOrWhiteSpace($PublicUrl)) {
        $PublicUrl = $env:MAX_WEBHOOK_PUBLIC_URL
    }
    if ([string]::IsNullOrWhiteSpace($PublicUrl)) {
        throw "PublicUrl is required in webhook mode (set MAX_WEBHOOK_PUBLIC_URL in .env or pass -PublicUrl)."
    }

    $env:MAX_MODE = "webhook"
    $env:MAX_WEBHOOK_HOST = "0.0.0.0"
    $env:MAX_WEBHOOK_PORT = "$Port"
    $env:MAX_WEBHOOK_PATH = $Path
    $env:MAX_WEBHOOK_PUBLIC_URL = $PublicUrl

    if ($Secret -ne "") {
        $env:MAX_WEBHOOK_SECRET = $Secret
    }
    elseif ($env:MAX_WEBHOOK_SECRET) {
        $env:MAX_WEBHOOK_SECRET = $env:MAX_WEBHOOK_SECRET
    }

    Write-Host "Starting MAX bot in webhook mode..."
}

& ".\.venv\Scripts\python.exe" ".\max_bot_core.py"
