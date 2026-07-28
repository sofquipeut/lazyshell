# Active durablement le mode lecteur d'ecran de Claude Code.
#
# Le fichier ~/.claude/settings.json peut deja contenir d'autres reglages :
# on le FUSIONNE au lieu de l'ecraser, apres sauvegarde. Ecrire du JSON a
# la main est le genre de manipulation qui casse silencieusement.

$dossier = Join-Path $env:USERPROFILE '.claude'
$fichier = Join-Path $dossier 'settings.json'

if (-not (Test-Path $dossier)) {
    New-Item -ItemType Directory -Path $dossier -Force | Out-Null
    Write-Host "Dossier cree : $dossier"
}

$reglages = $null

if (Test-Path $fichier) {
    $brut = Get-Content -Path $fichier -Raw -Encoding UTF8

    $sauvegarde = "$fichier.sauvegarde"
    [System.IO.File]::WriteAllText($sauvegarde, $brut, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Sauvegarde ecrite : $sauvegarde"

    if ($brut.Trim().Length -gt 0) {
        try {
            $reglages = $brut | ConvertFrom-Json
        } catch {
            Write-Host ""
            Write-Host "ARRET : le fichier existant n'est pas du JSON valide."
            Write-Host "Rien n'a ete modifie. Corrigez-le ou supprimez-le, puis relancez."
            Write-Host "Detail : $($_.Exception.Message)"
            exit 1
        }
    }
}

if ($null -eq $reglages) {
    $reglages = New-Object PSObject
}

$deja = $reglages.PSObject.Properties.Name -contains 'axScreenReader'
if ($deja -and $reglages.axScreenReader -eq $true) {
    Write-Host ""
    Write-Host "Le mode lecteur d'ecran etait deja active. Rien a faire."
    exit 0
}

$reglages | Add-Member -MemberType NoteProperty -Name 'axScreenReader' -Value $true -Force

# -Depth 100 est indispensable : au-dela de 2 niveaux, la valeur par
# defaut remplace les objets imbriques par du texte et detruit le fichier.
$json = $reglages | ConvertTo-Json -Depth 100

# UTF-8 sans marque d'ordre des octets : certains lecteurs de JSON
# echouent sur le BOM ajoute par Out-File.
[System.IO.File]::WriteAllText($fichier, $json, (New-Object System.Text.UTF8Encoding $false))

Write-Host ""
Write-Host "Mode lecteur d'ecran active dans :"
Write-Host "  $fichier"
Write-Host ""
Write-Host "Contenu du fichier :"
Get-Content -Path $fichier -Encoding UTF8 | ForEach-Object { Write-Host "  $_" }
