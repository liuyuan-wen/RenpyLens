# GitHub REST API 发布示例

以下是没有 `gh` 时的 PowerShell 流程示例；执行前替换版本、Highlights 和文件名：

```powershell
$releaseVersion = 'vX.Y.Z'
$releaseAsset = "RenpyLens_$releaseVersion.exe"
$credentialInput = "protocol=https`nhost=github.com`n`n"
$credentialOutput = $credentialInput | git credential fill
$githubTokenLine = $credentialOutput |
    Where-Object { $_ -like 'password=*' } |
    Select-Object -First 1
if (-not $githubTokenLine) {
    throw 'GitHub credential token not available.'
}

$githubToken = $githubTokenLine.Substring(9)
$githubHeaders = @{
    Authorization = "Bearer $githubToken"
    Accept = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$releaseBody = @'
## Highlights

- 第一项实际更新。
- 第二项实际更新。
'@
$releasePayload = @{
    tag_name = $releaseVersion
    target_commitish = 'main'
    name = "RenpyLens $releaseVersion"
    body = $releaseBody
    draft = $false
    prerelease = $false
    make_latest = 'true'
} | ConvertTo-Json

$githubRelease = Invoke-RestMethod `
    -Method Post `
    -Uri 'https://api.github.com/repos/liuyuan-wen/RenpyLens/releases' `
    -Headers $githubHeaders `
    -ContentType 'application/json' `
    -Body $releasePayload

$uploadBase = $githubRelease.upload_url -replace '\{.*$', ''
$uploadUri = $uploadBase + '?name=' + [uri]::EscapeDataString($releaseAsset)
$githubAsset = Invoke-RestMethod `
    -Method Post `
    -Uri $uploadUri `
    -Headers $githubHeaders `
    -ContentType 'application/octet-stream' `
    -InFile (Join-Path (Get-Location) $releaseAsset)

Remove-Variable githubToken, githubTokenLine, credentialOutput -ErrorAction SilentlyContinue
```
