param(
    [string]$OutputDirectory = "reports/rallymate-current-docs"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PandocCommand = Get-Command pandoc -ErrorAction Stop
$ResolvedOutput = Join-Path $ProjectRoot $OutputDirectory
New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null

# Keep the document-center shell and shared stylesheet under docs/. The reports/
# directory is generated output and may be safely removed or excluded from Git.
$SiteSource = Join-Path $ProjectRoot "docs/site"
$OutputAssets = Join-Path $ResolvedOutput "assets"
New-Item -ItemType Directory -Force -Path $OutputAssets | Out-Null
Copy-Item -LiteralPath (Join-Path $SiteSource "index.html") `
    -Destination (Join-Path $ResolvedOutput "index.html") -Force
Copy-Item -LiteralPath (Join-Path $SiteSource "docs.css") `
    -Destination (Join-Path $OutputAssets "docs.css") -Force

$CommonArguments = @(
    "--from=gfm",
    "--to=html5",
    "--standalone",
    "--toc",
    "--toc-depth=3",
    "--css=assets/docs.css"
)

& $PandocCommand.Source `
    (Join-Path $ProjectRoot "docs/RallyMate产品说明书_当前态_v1.0.md") `
    @CommonArguments `
    "--metadata=title:RallyMate 产品说明书（当前态）" `
    "--output=$(Join-Path $ResolvedOutput 'product.html')"
if ($LASTEXITCODE -ne 0) {
    throw "Pandoc failed to build the RallyMate product document"
}

& $PandocCommand.Source `
    (Join-Path $ProjectRoot "docs/RallyMate技术架构与实现说明书_当前态_v1.0.md") `
    @CommonArguments `
    "--metadata=title:RallyMate 技术架构与实现说明书（当前态）" `
    "--output=$(Join-Path $ResolvedOutput 'technical.html')"
if ($LASTEXITCODE -ne 0) {
    throw "Pandoc failed to build the RallyMate technical document"
}

& $PandocCommand.Source `
    (Join-Path $ProjectRoot "docs/RallyMate接口与端到端链路_当前态_v1.0.md") `
    @CommonArguments `
    "--metadata=title:RallyMate 接口与端到端链路（当前态）" `
    "--output=$(Join-Path $ResolvedOutput 'api.html')"
if ($LASTEXITCODE -ne 0) {
    throw "Pandoc failed to build the RallyMate API document"
}

& $PandocCommand.Source `
    (Join-Path $ProjectRoot "docs/RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md") `
    @CommonArguments `
    "--metadata=title:RallyMate M96 RTMPose M/L/X 同帧诊断" `
    "--output=$(Join-Path $ResolvedOutput 'm96-model-diagnostic.html')"
if ($LASTEXITCODE -ne 0) {
    throw "Pandoc failed to build the M96 model diagnostic"
}

& $PandocCommand.Source `
    (Join-Path $ProjectRoot "docs/RTMPOSE_X_OPERATIONAL_COMPARISON_M97.md") `
    @CommonArguments `
    "--metadata=title:RallyMate M97 RTMPose-X 下游残差比较" `
    "--output=$(Join-Path $ResolvedOutput 'm97-model-comparison.html')"
if ($LASTEXITCODE -ne 0) {
    throw "Pandoc failed to build the M97 model comparison"
}

$EvidenceFiles = @(
    "user-demo-v1.2/summary.md",
    "user-demo-v1.2/demo-contract.json",
    "user-demo-v1.2/field-change-record.json",
    "m96-user-demo-v1.1/summary.md",
    "m96-user-demo-v1.1/demo-contract.json",
    "m96-user-demo-v1.1/field-change-record.json",
    "m96-pose-pilot/summary.md",
    "m96-pose-pilot/pilot-contract.json",
    "m96-pose-pilot/field-change-record.json",
    "m96-rtmpose-same-frame-diagnostic/summary.md",
    "m96-rtmpose-same-frame-diagnostic/field-change-record.json",
    "measurement-recovery-m97/summary/summary.md",
    "measurement-recovery-m97/summary/report.json",
    "measurement-recovery-m97/field-change-record.json"
)
foreach ($RelativeEvidence in $EvidenceFiles) {
    $EvidenceSource = Join-Path (Join-Path $ProjectRoot "reports") $RelativeEvidence
    if (-not (Test-Path -LiteralPath $EvidenceSource -PathType Leaf)) {
        throw "Required current-docs evidence is missing: $RelativeEvidence"
    }
    $EvidenceDestination = Join-Path (Join-Path $ResolvedOutput "evidence") $RelativeEvidence
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EvidenceDestination) | Out-Null
    Copy-Item -LiteralPath $EvidenceSource -Destination $EvidenceDestination -Force
}

# Markdown sources keep repository-relative links for local editing. The generated
# document center is one level deeper, so point its safe evidence links at the
# explicit copies inside the current-docs allowlist instead of leaving broken URLs.
$ApiHtmlPath = Join-Path $ResolvedOutput "api.html"
$ApiHtml = [System.IO.File]::ReadAllText($ApiHtmlPath)
$ApiHtml = $ApiHtml.Replace('href="../reports/', 'href="evidence/')
$ApiHtml = $ApiHtml.Replace(
    'href="RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md"',
    'href="m96-model-diagnostic.html"'
)
$ApiHtml = $ApiHtml.Replace(
    'href="RTMPOSE_X_OPERATIONAL_COMPARISON_M97.md"',
    'href="m97-model-comparison.html"'
)
[System.IO.File]::WriteAllText(
    $ApiHtmlPath,
    $ApiHtml,
    [System.Text.UTF8Encoding]::new($false)
)

Copy-Item -LiteralPath (
    Join-Path $ProjectRoot "docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio"
) -Destination (
    Join-Path $ResolvedOutput "RallyMate完整系统架构_当前态_v1.0.drawio"
) -Force

$ArchitectureDiagramOutput = Join-Path $ResolvedOutput "diagrams"
New-Item -ItemType Directory -Force -Path $ArchitectureDiagramOutput | Out-Null
foreach ($DiagramName in @(
    "RallyMate系统总体架构流程图_当前态_v1.0.svg",
    "RallyMate系统总体架构流程图_当前态_v1.0.png"
)) {
    Copy-Item -LiteralPath (
        Join-Path $ProjectRoot "docs/diagrams/$DiagramName"
    ) -Destination (
        Join-Path $ArchitectureDiagramOutput $DiagramName
    ) -Force
}

Write-Host "Built current RallyMate documents in $ResolvedOutput"
