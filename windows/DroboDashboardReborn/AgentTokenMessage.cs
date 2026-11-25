namespace DroboDashboardReborn;

/// <summary>
/// What the app says when the agent refuses its access token, and how it
/// names the file the token came from.
///
/// Why it exists: until 2026-11-25 the refusal read "Token rejected. Check
/// the token from the agent's config.json." On the maintainer's PC that sent
/// them looking in the wrong place. The real cause was order: until that
/// day the repository's launcher started this app before the agent, so on
/// a fresh checkout the app looked for agent\config.json before the agent
/// had written it, and every request after that was refused. (The launcher
/// now starts the agent first and waits for it; an agent that writes its
/// first config.json after the app has started -- one the launcher gave up
/// waiting for -- is why MainWindow still reads the token again once on a
/// refusal.) The message named neither the other common cause of a
/// refusal -- a second agent, such as an installed copy or the simulated
/// Drobo, already holding the port -- nor
/// which config.json the app had read. Both are said now, and the file is
/// named exactly.
///
/// Kept apart from MainWindow so the wording and the path shortening can be
/// checked without a window (see DroboDashboardReborn.Tests).
/// </summary>
public static class AgentTokenMessage
{
    /// <summary>
    /// The refusal, for the screen. <paramref name="tokenSourceForDisplay"/>
    /// is the config file the token in use was read from, already shortened
    /// with <see cref="ShortenForDisplay"/>, or null when the token was not
    /// read from a file (typed in, or no file was found).
    /// </summary>
    public static string Rejected(string agentUrl, string? tokenSourceForDisplay, bool tokenIsBlank)
    {
        if (tokenIsBlank)
            return $"The agent at {agentUrl} needs an access token, and this app has none: "
                   + "it found no agent config.json with a token in it. "
                   + "Paste the token from the agent's config.json into the TOKEN box "
                   + "under SETTINGS on the page that lists your Drobos.";

        var refusal = $"The agent at {agentUrl} refused this app's access token. ";
        var otherAgent = "If another agent is running on this port (an installed copy, "
                         + "or the simulated Drobo), close it and start again; ";
        if (tokenSourceForDisplay is null)
            return refusal
                   + "This app's token was typed in, not read from a config file. "
                   + otherAgent
                   + "otherwise paste the token from the agent's config.json into the TOKEN box "
                   + "under SETTINGS on the page that lists your Drobos.";

        return refusal
               + $"This app read its token from {tokenSourceForDisplay}. "
               + otherAgent
               + "otherwise check the token in that file.";
    }

    /// <summary>
    /// <paramref name="path"/> with the Windows user's own folders written as
    /// %LOCALAPPDATA%, %OneDriveCommercial%, %OneDrive% and %USERPROFILE%.
    /// A folder under the user's profile carries the Windows user name, and
    /// an on-screen message is exactly what a tester copies into a public
    /// report. A work OneDrive's folder also carries the organisation's name
    /// ("OneDrive - Example Org"), and a checkout cloned under Documents is
    /// often inside it, so the OneDrive folders are tried before the
    /// profile. Local AppData is tried first of all because it sits inside
    /// the profile. A path outside every folder is returned unchanged; a
    /// blank folder is never matched.
    /// </summary>
    public static string ShortenForDisplay(string path, string? localAppDataFolder, string? userProfileFolder,
                                           string? oneDriveCommercialFolder = null, string? oneDriveFolder = null)
    {
        var folders = new (string? Folder, string Variable)[]
        {
            (localAppDataFolder, "%LOCALAPPDATA%"),
            (oneDriveCommercialFolder, "%OneDriveCommercial%"),
            (oneDriveFolder, "%OneDrive%"),
            (userProfileFolder, "%USERPROFILE%"),
        };
        foreach (var (folder, variable) in folders)
        {
            var shortened = ReplaceFolder(path, folder, variable);
            if (shortened is not null) return shortened;
        }
        return path;
    }

    /// <summary>The path with <paramref name="folder"/> at its start replaced
    /// by <paramref name="variable"/>, or null when it does not start there.
    /// Compared without regard to case, as Windows does, and only on a whole
    /// folder name: a profile folder "name" must not match "name2".</summary>
    private static string? ReplaceFolder(string path, string? folder, string variable)
    {
        if (string.IsNullOrWhiteSpace(folder)) return null;
        var folderWithoutSeparator = folder.TrimEnd('\\', '/');
        if (folderWithoutSeparator.Length == 0) return null;
        if (!path.StartsWith(folderWithoutSeparator, StringComparison.OrdinalIgnoreCase)) return null;

        var rest = path[folderWithoutSeparator.Length..];
        if (rest.Length > 0 && rest[0] != '\\' && rest[0] != '/') return null;
        return variable + rest;
    }
}
