# Ajoute %USERPROFILE%\.local\bin au PATH UTILISATEUR, sans risque.
#
# On n'utilise deliberement PAS "setx PATH %PATH%;..." :
#  - %PATH% dans une invite de commandes est la FUSION du PATH machine
#    et du PATH utilisateur. L'ecrire dans le PATH utilisateur y recopie
#    tout le PATH machine, qui se retrouve alors en double.
#  - setx tronque silencieusement au-dela de 1024 caracteres, ce qui
#    detruit purement et simplement la fin du PATH.
# On lit donc uniquement la portee "User" et on n'ecrit que celle-la.

$dossier = Join-Path $env:USERPROFILE '.local\bin'

Write-Host "Dossier vise : $dossier"
Write-Host ""

if (-not (Test-Path $dossier)) {
    Write-Host "ERREUR : ce dossier n'existe pas. Rien n'a ete modifie."
    exit 1
}

$pathUtilisateur = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $pathUtilisateur) { $pathUtilisateur = '' }

$sauvegarde = Join-Path $PSScriptRoot 'path_utilisateur_sauvegarde.txt'
$pathUtilisateur | Out-File -FilePath $sauvegarde -Encoding UTF8
Write-Host "PATH utilisateur actuel sauvegarde dans :"
Write-Host "  $sauvegarde"
Write-Host ""

$entrees = $pathUtilisateur -split ';' | Where-Object { $_ -ne '' }
$deja = $entrees | Where-Object { $_.TrimEnd('\') -ieq $dossier.TrimEnd('\') }

if ($deja) {
    Write-Host "Le dossier figure DEJA dans le PATH utilisateur enregistre."
    Write-Host ""
    Write-Host "Il ne manque donc qu'une session neuve. Fermez toutes vos"
    Write-Host "fenetres d'invite de commandes, ou fermez puis rouvrez"
    Write-Host "votre session Windows."
    exit 0
}

if ($pathUtilisateur -eq '') {
    $nouveau = $dossier
} else {
    $nouveau = $pathUtilisateur.TrimEnd(';') + ';' + $dossier
}

[Environment]::SetEnvironmentVariable('Path', $nouveau, 'User')

Write-Host "Dossier ajoute au PATH utilisateur."
Write-Host ""

$verif = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($verif -like "*$dossier*") {
    Write-Host "Verification : OK, l'entree est bien enregistree."
} else {
    Write-Host "Verification : ECHEC, l'entree n'a pas ete ecrite."
    Write-Host "Le PATH d'origine est dans le fichier de sauvegarde."
}
