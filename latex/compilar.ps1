<#
    compilar.ps1 - Compila a documentacao tecnica (PMX-DOC-001).

    Executa as quatro passagens necessarias:
      1. pdflatex - primeira passagem (coleta rotulos, citacoes e indices)
      2. biber    - resolve a bibliografia ABNT
      3. pdflatex - insere referencias e bibliografia
      4. pdflatex - estabiliza sumario, listas e referencias cruzadas

    Uso:
      .\compilar.ps1              # compilacao completa
      .\compilar.ps1 -Rapido      # uma passagem apenas (iteracao de escrita)
      .\compilar.ps1 -Limpar      # remove os arquivos auxiliares e sai
#>
[CmdletBinding()]
param(
    [switch]$Rapido,
    [switch]$Limpar
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$documento = 'main'
$auxiliares = @('aux', 'bbl', 'bcf', 'blg', 'lof', 'log', 'lot', 'out',
                'run.xml', 'toc', 'synctex.gz')

function Remove-Auxiliares {
    foreach ($extensao in $auxiliares) {
        $caminho = "$documento.$extensao"
        if (Test-Path -LiteralPath $caminho) {
            Remove-Item -LiteralPath $caminho -Force
        }
    }
    Write-Host 'Arquivos auxiliares removidos.' -ForegroundColor DarkGray
}

if ($Limpar) {
    Remove-Auxiliares
    return
}

foreach ($executavel in @('pdflatex', 'biber')) {
    if (-not (Get-Command $executavel -ErrorAction SilentlyContinue)) {
        throw "'$executavel' nao encontrado no PATH. Instale MiKTeX ou TeX Live."
    }
}

function Invoke-Passagem {
    param([string]$Rotulo, [string]$Executavel, [string[]]$Argumentos)

    Write-Host "==> $Rotulo" -ForegroundColor Cyan
    & $Executavel @Argumentos | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$Rotulo retornou codigo $LASTEXITCODE." -ForegroundColor Yellow
        Write-Host "Consulte $documento.log para o detalhe." -ForegroundColor Yellow
    }
}

$argumentosLatex = @('-interaction=nonstopmode', '-file-line-error', "$documento.tex")

Invoke-Passagem 'pdflatex (1/4)' 'pdflatex' $argumentosLatex

if (-not $Rapido) {
    Invoke-Passagem 'biber (2/4)'    'biber'    @($documento)
    Invoke-Passagem 'pdflatex (3/4)' 'pdflatex' $argumentosLatex
    Invoke-Passagem 'pdflatex (4/4)' 'pdflatex' $argumentosLatex
}

if (-not (Test-Path -LiteralPath "$documento.pdf")) {
    throw "Compilacao falhou: $documento.pdf nao foi gerado. Veja $documento.log."
}

$pdf = Get-Item -LiteralPath "$documento.pdf"
$tamanhoMB = [math]::Round($pdf.Length / 1MB, 2)

# Referencias nao resolvidas e avisos relevantes do log
$log = Get-Content -LiteralPath "$documento.log" -ErrorAction SilentlyContinue
$pendentes = @($log | Select-String -SimpleMatch 'undefined' -CaseSensitive:$false)

Write-Host ''
Write-Host "PDF gerado: $($pdf.Name) ($tamanhoMB MB)" -ForegroundColor Green
if ($pendentes.Count -gt 0) {
    Write-Host "Atencao: $($pendentes.Count) referencia(s) ou citacao(oes) pendentes." -ForegroundColor Yellow
    Write-Host 'Execute novamente sem -Rapido para estabilizar.' -ForegroundColor Yellow
}
