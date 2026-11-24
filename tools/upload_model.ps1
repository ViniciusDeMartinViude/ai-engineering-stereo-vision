<#
.SYNOPSIS
Upload a trained YOLO model to the camera server's model library. No Python needed.

.DESCRIPTION
Runs with the PowerShell that ships with Windows (5.1 or newer). Copy
upload_model.ps1 and upload_model.cmd to any computer that reaches the camera
PC over Wi-Fi, then run upload_model.cmd from a Command Prompt or PowerShell.

Settings can come from environment variables, so you set them once:
  setx STEREO_LIBRARY_URL http://192.168.0.102:8765
  setx STEREO_LIBRARY_TOKEN <token from: python tools/model_library.py token>
  setx STEREO_LIBRARY_VIA <this computer's Wi-Fi IP>   (only when Wi-Fi and Ethernet share one subnet)

.EXAMPLE
upload_model.cmd runs\detect\train\weights\best.pt -Name belt-v4 -Description "Nov 24, 120 epochs"

.EXAMPLE
upload_model.cmd -List
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$File,
    [string]$Name,
    [string]$Description = '',
    [string]$Classes = '',
    [int]$Imgsz = 0,
    [string]$Server = $env:STEREO_LIBRARY_URL,
    [string]$Token = $env:STEREO_LIBRARY_TOKEN,
    [string]$Via = $env:STEREO_LIBRARY_VIA,
    [switch]$List
)

$ErrorActionPreference = 'Stop'
$Formats = @('.pt', '.onnx', '.torchscript')

function Format-Size([double]$Bytes) {
    foreach ($unit in 'B', 'KB', 'MB', 'GB') {
        if ($Bytes -lt 1024 -or $unit -eq 'GB') {
            if ($unit -eq 'B') { return ('{0:0} B' -f $Bytes) }
            return ('{0:0.0} {1}' -f $Bytes, $unit)
        }
        $Bytes /= 1024
    }
}

function Invoke-Library([string]$Method, [string]$Path, [hashtable]$Headers = @{}, [string]$BodyFile = '') {
    $target = if ($Server -match '://') { [Uri]$Server } else { [Uri]"http://$Server" }
    $port = if ($target.IsDefaultPort) { 8765 } else { $target.Port }
    $where = "http://$($target.Host):$port" + $(if ($Via) { " via $Via" } else { '' })
    if ($Via) {
        # Bind to this computer's Wi-Fi address so Windows sends the request over Wi-Fi.
        try {
            $local = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse($Via)), 0
            $client = New-Object System.Net.Sockets.TcpClient $local
        } catch {
            throw "-Via $Via is not an address of this computer (check its Wi-Fi IP with ipconfig)."
        }
    } else {
        $client = New-Object System.Net.Sockets.TcpClient
    }
    $client.SendTimeout = 30000
    $client.ReceiveTimeout = 120000
    try {
        try {
            $client.Connect($target.Host, $port)
        } catch {
            $why = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
            throw "Cannot reach the library at ${where}: $why " +
                  '(is the server running, TCP 8765 allowed in its firewall, and are you on the same Wi-Fi? ' +
                  'With Wi-Fi and Ethernet on one subnet, pass -Via YOUR_WIFI_IP)'
        }
        $stream = $client.GetStream()
        $lines = @("$Method $Path HTTP/1.1", "Host: $($target.Host):$port", 'Connection: close',
                   'User-Agent: StereoVisionUpload/1')
        foreach ($key in $Headers.Keys) { $lines += "${key}: $($Headers[$key])" }
        if ($Token) { $lines += "Authorization: Bearer $Token" }
        $length = 0
        if ($BodyFile) { $length = (Get-Item -LiteralPath $BodyFile).Length; $lines += "Content-Length: $length" }
        $head = [Text.Encoding]::ASCII.GetBytes(($lines -join "`r`n") + "`r`n`r`n")
        $stream.Write($head, 0, $head.Length)
        if ($BodyFile) {
            $reader = [IO.File]::OpenRead($BodyFile)
            try {
                $buffer = New-Object byte[] 262144
                $sent = 0; $shown = 0; $started = Get-Date
                while (($count = $reader.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $stream.Write($buffer, 0, $count)
                    $sent += $count
                    if ($sent - $shown -ge 1MB -or $sent -eq $length) {
                        $shown = $sent
                        $mbps = $sent * 8 / [Math]::Max(0.001, ((Get-Date) - $started).TotalSeconds) / 1e6
                        Write-Progress -Activity "Uploading $(Split-Path $BodyFile -Leaf)" `
                            -Status ('{0} of {1}  ({2:0.0} Mbit/s)' -f (Format-Size $sent), (Format-Size $length), $mbps) `
                            -PercentComplete ([int](100 * $sent / [Math]::Max(1, $length)))
                    }
                }
            } finally {
                $reader.Close()
                Write-Progress -Activity 'Uploading' -Completed
            }
        }
        $memory = New-Object IO.MemoryStream
        $stream.CopyTo($memory)  # the server closes the connection after its answer
    } catch [System.IO.IOException] {
        throw "The connection to $where broke: $($_.Exception.Message)"
    } finally {
        $client.Close()
    }
    $text = [Text.Encoding]::UTF8.GetString($memory.ToArray())
    $split = $text.IndexOf("`r`n`r`n")
    if ($split -lt 0) { throw "No valid answer from $where" }
    $status = [int](($text.Substring(0, $text.IndexOf("`r`n")) -split ' ')[1])
    $body = $text.Substring($split + 4)
    $json = $null
    if ($body.Trim()) { try { $json = $body | ConvertFrom-Json } catch { $json = [pscustomobject]@{ error = $body } } }
    if ($status -ge 400) {
        $reason = if ($json -and $json.error) { $json.error } else { 'no details' }
        throw "Server refused ($status): $reason"
    }
    return $json
}

function Show-Models($Models) {
    if (-not $Models) { Write-Host '  (the library is empty)'; return }
    foreach ($m in $Models) {
        $names = if ($m.classes) { $m.classes -join ', ' } else { 'classes unknown' }
        Write-Host ('  {0,-24} {1,9}  {2,-16}  {3}' -f $m.name, (Format-Size $m.size), $m.added, $names)
    }
}

try {
    if (-not $Server) {
        throw 'Give -Server http://CAMERA_PC_WIFI_IP:8765 (or set STEREO_LIBRARY_URL).'
    }
    if ($List) {
        Write-Host "Library at ${Server}:"
        Show-Models (Invoke-Library 'GET' '/api/models').models
        exit 0
    }
    if (-not $File) { throw 'Give the model file to upload, or -List.' }
    $item = Get-Item -LiteralPath $File -ErrorAction SilentlyContinue
    if (-not $item -or $item.PSIsContainer) { throw "$File does not exist." }
    $suffix = $item.Extension.ToLower()
    if ($Formats -notcontains $suffix) {
        throw "$suffix is not accepted; use $($Formats -join ', '). TensorRT .engine files only run on the GPU they were built on."
    }
    if (-not $Token) {
        throw 'An upload token is required: -Token or STEREO_LIBRARY_TOKEN. Get it on the camera PC with: python tools/model_library.py token'
    }
    if (-not $Name) { $Name = $item.BaseName }

    Write-Host "Checking $Server ..."
    $limits = Invoke-Library 'GET' '/api/upload'
    if ($item.Length -gt $limits.max_bytes) {
        throw "$(Format-Size $item.Length) is over the server limit of $(Format-Size $limits.max_bytes)."
    }
    $sha = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLower()
    $headers = @{
        'Content-Type'        = 'application/octet-stream'
        'X-Model-File'        = [Uri]::EscapeDataString($item.Name)
        'X-Model-SHA256'      = $sha
        'X-Model-Description' = [Uri]::EscapeDataString($Description)
    }
    if ($Classes) { $headers['X-Model-Classes'] = [Uri]::EscapeDataString($Classes) }
    if ($Imgsz -gt 0) { $headers['X-Model-Imgsz'] = "$Imgsz" }
    Write-Host ("Uploading {0} as ""{1}"" ({2}, sha256 {3})" -f $item.Name, $Name, (Format-Size $item.Length), $sha.Substring(0, 12))
    $result = Invoke-Library 'PUT' ('/api/models/' + [Uri]::EscapeDataString($Name)) $headers $item.FullName
    if ($result.model.sha256 -ne $sha) { throw 'The server stored a different file than was sent; try again.' }
    $listed = (Invoke-Library 'GET' '/api/models').models | Where-Object { $_.name -eq $result.model.name }
    if (-not $listed -or $listed.sha256 -ne $sha) { throw 'Upload finished but the model is not listed; check the server log.' }
    $verb = if ($result.replaced) { 'Replaced' } else { 'Added' }
    Write-Host "$verb ""$($result.model.name)"" in the library. Students can pick it with the Model button." -ForegroundColor Green
    if ($result.model.classes) { Write-Host ('  classes: ' + ($result.model.classes -join ', ')) }
    exit 0
} catch {
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
