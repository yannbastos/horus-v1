# Baixa apenas o ZIP público EDB; nunca extrai/executa um artefato sem hash publicado.
# PowerShell 7: -Resume envia Range e retoma o OutFile entre tentativas.
param(
    [string]$Uri = 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260667',
    [string]$Destination = (Join-Path ([IO.Path]::GetTempPath()) 'horus-i0-pg17.zip.part'),
    [int]$Attempts = 2
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    $bytes = if (Test-Path -LiteralPath $Destination) { (Get-Item -LiteralPath $Destination).Length } else { 0 }
    Write-Output "$(Get-Date -Format o) attempt=$attempt resume_bytes=$bytes"
    try {
        $response = Invoke-WebRequest -Uri $Uri -OutFile $Destination -Resume -PassThru `
            -ConnectionTimeoutSeconds 600 -OperationTimeoutSeconds 600
        Write-Output "status=$($response.StatusCode) bytes=$((Get-Item -LiteralPath $Destination).Length)"
        Write-Output "source=$($response.BaseResponse.RequestMessage.RequestUri)"
        Write-Output "sha256_local=$((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash)"
        Write-Output 'Download concluído; comparar com checksum publicado antes de extrair/executar.'
        exit 0
    } catch {
        Write-Output "$(Get-Date -Format o) $($_.Exception.Message)"
    }
}
exit 1
