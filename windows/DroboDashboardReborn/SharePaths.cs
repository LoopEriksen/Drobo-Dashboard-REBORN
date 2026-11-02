using System;
using System.Linq;

namespace DroboDashboardReborn;

/// <summary>
/// Path rules for the in-app file browser, kept apart from the window so the
/// test program can check them without WPF.
/// </summary>
internal static class SharePaths
{
    /// <summary>
    /// Whether <paramref name="path"/> is the share root itself or a folder
    /// inside it, compared case-insensitively as Windows compares paths.
    ///
    /// The root must be followed by a backslash, not just be a prefix: until
    /// 2026-11-02 the browser checked StartsWith alone, so with the root
    /// <c>\\host\Photos</c> it accepted <c>\\host\Photos2</c>, a different
    /// share. A path with a "." or ".." segment is refused outright, because
    /// <c>\\host\Photos\..\Other</c> starts with the root and is not inside
    /// it. Nothing navigates by such a path today (every move comes from a
    /// listed entry, a crumb or Up); this keeps the guard true to its word.
    /// </summary>
    public static bool IsWithinShareRoot(string path, string shareRootUnc)
    {
        if (string.IsNullOrEmpty(path) || string.IsNullOrEmpty(shareRootUnc)) return false;
        if (HasDotSegment(path)) return false;
        var root = shareRootUnc.TrimEnd('\\');
        return string.Equals(path, root, StringComparison.OrdinalIgnoreCase)
               || path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>True when any segment of the path is "." or "..", which
    /// Windows would resolve to somewhere the text does not say.</summary>
    private static bool HasDotSegment(string path) =>
        path.Split('\\', '/').Any(segment => segment is "." or "..");
}
