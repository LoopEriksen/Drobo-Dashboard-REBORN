<!--
  The body of every GitHub Release, published by .github/workflows/installer.yml
  when a v* tag is pushed. The workflow fills in the four names in braces
  (version, MSI file name, its SHA-256 and the link to this tag's CHANGELOG.md)
  from the build it has just tested; nothing else is changed, and this comment
  does not show on the release page. Edit the wording here, in the same commit
  as the release, so the notes are tracked like everything else.
-->
**Uninstall 1.0.0 first** if you have it: {VERSION} installs for everyone on the PC into Program Files, and Windows cannot upgrade the old per-user install in place. Your settings and access token are kept. **1.1.0 upgrades in place**: install {VERSION} over it, and your settings and access token are kept.

Download `{MSI_NAME}` and double-click it. It bundles its own Python and .NET 10 runtime, so nothing else needs installing. It is unsigned on purpose (this is not a for-profit project, so it is not code-signed), so SmartScreen will say Unknown publisher (More info > Run anyway). To check the download, compare its SHA-256 with the one below: in PowerShell, `Get-FileHash {MSI_NAME}` prints it (SHA-256 is the default; it prints capital letters, and the case does not matter), or with Git Bash run `sha256sum -c SHA256SUMS.txt` beside it.

SHA-256: `{SHA256}`

What changed: see [CHANGELOG.md]({CHANGELOG_URL}). Report problems as issues on this repository.
