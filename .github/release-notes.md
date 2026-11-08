<!--
  The body of every GitHub Release, published by .github/workflows/installer.yml
  when a v* tag is pushed. The workflow fills in the four names in braces
  (version, MSI file name, its SHA-256 and the link to this tag's CHANGELOG.md)
  from the build it has just tested; nothing else is changed, and this comment
  does not show on the release page. Edit the wording here, in the same commit
  as the release, so the notes are tracked like everything else.
-->
A free replacement for the discontinued Drobo Dashboard: a Windows 10/11 x64 app plus a browser dashboard for network Drobos. It is verified on a Drobo 5N; other network models are untested.

**Start here:** the [tester guide](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/blob/v{VERSION}/windows/installer/TESTING.md), the [tester tasks](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/blob/v{VERSION}/docs/testers-tasks.md) for hardware this project lacks, and the [Tester report](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/issues/new?template=tester-report.yml) form for what you find.

**Uninstall 1.0.0 first** if you have it: {VERSION} installs for everyone on the PC into Program Files, and Windows cannot upgrade the old per-user install in place. Your settings and access token are kept. **1.1.0 upgrades in place**: install {VERSION} over it, and your settings and access token are kept.

Download `{MSI_NAME}` and double-click it. It bundles its own Python and .NET 10 runtime, so nothing else needs installing. It is unsigned on purpose (this is not a for-profit project, so it is not code-signed), so SmartScreen will say Unknown publisher (More info > Run anyway). To check the download, compare its SHA-256 with the one below: in PowerShell, `Get-FileHash {MSI_NAME}` prints it (SHA-256 is the default; it prints capital letters, and the case does not matter), or with Git Bash run `sha256sum -c SHA256SUMS.txt` beside it.

SHA-256: `{SHA256}`

With **Smart App Control** on, Windows will not run the app at all. Start the agent from the Start Menu entry instead and use the browser dashboard at http://127.0.0.1:7420, which asks once for the access token kept in `%LOCALAPPDATA%\DroboDashboardReborn\config.json`; or switch Smart App Control off while you test.

What changed: see [CHANGELOG.md]({CHANGELOG_URL}). Report problems with the [Tester report](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/issues/new?template=tester-report.yml) form.
