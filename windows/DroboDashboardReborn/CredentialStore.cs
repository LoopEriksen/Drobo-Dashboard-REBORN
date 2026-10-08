using System;
using System.Runtime.InteropServices;
using System.Text;

namespace DroboDashboardReborn;

/// <summary>
/// Saves a Drobo share login on this PC, in Windows' own Credential Manager.
///
/// WHY THIS EXISTS, AND WHY IT IS NOT A FILE
/// -----------------------------------------
/// Until 2026-09-08 this app stored no credential at all: you retyped your
/// Drobo username and password every session, and the one "remember me" path
/// was a flag handed to Windows (CONNECT_UPDATE_PROFILE, see
/// <see cref="SmbConnections"/>) that Windows honours only for a share mounted
/// on a drive letter. Connect without a drive letter and the flag is silently
/// discarded, so the tick box had to be greyed out in that case. The maintainer
/// asked for the app to be able to store credentials on the machine itself.
/// This is that, done through the operating system rather than around it.
///
/// The credential is written to Credential Manager, which is where Windows
/// keeps its own network logins. Concretely that means:
///
///   * It is encrypted at rest by Windows, keyed to the signed-in Windows
///     account. Another user on the same PC cannot read it, and neither can
///     this program once the account is locked.
///   * It is visible and deletable by the user without this app, in Control
///     Panel > User Accounts > Credential Manager > Windows Credentials.
///     A credential a user cannot find is a credential they cannot revoke.
///   * Windows itself uses it. That is the real prize: once saved, a
///     deviceless connection, a mapped drive, and File Explorer all
///     authenticate without asking again, which the old flag could never do.
///
/// What this deliberately does NOT do is invent a store. No encrypted blob in
/// settings.json, no DPAPI call of our own, no key material this project has
/// to get right. Rolling storage by hand is how a hobby project ends up with
/// somebody's NAS password in a JSON file next to a comment claiming it is
/// safe.
///
/// A NOTE ON READING BACK
/// ----------------------
/// These are CRED_TYPE_DOMAIN_PASSWORD entries, the kind Windows uses for
/// network shares. Windows does not hand the password back to a caller that
/// reads one; the blob comes back empty by design, and only the operating
/// system's own authentication path ever sees the plaintext again. So this
/// class can tell you a login is saved, and for whom, and can delete it, but
/// it cannot show you the password. That is a feature: this app never needs
/// to hold the password again after saving it, so it never asks to.
/// </summary>
internal static class CredentialStore
{
    // ---------------------------------------------------------------
    // Win32 declarations (advapi32.dll)
    // ---------------------------------------------------------------

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct CREDENTIAL
    {
        public int Flags;
        public int Type;
        public IntPtr TargetName;
        public IntPtr Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public IntPtr TargetAlias;
        public IntPtr UserName;
    }

    /// <summary>The kind Windows uses for network shares, so Windows will
    /// authenticate with it on our behalf rather than us replaying it.</summary>
    private const int CRED_TYPE_DOMAIN_PASSWORD = 2;

    /// <summary>Survives a sign-out and a reboot, for this user on this PC.
    /// Not CRED_PERSIST_SESSION, which would vanish at sign-out and make
    /// "save this login" a lie by morning.</summary>
    private const int CRED_PERSIST_LOCAL_MACHINE = 2;

    /// <summary>Windows' own cap for a domain-password blob: 512 bytes, and
    /// the blob is UTF-16, so 256 characters. Longer is rejected by
    /// CredWrite with ERROR_INVALID_PARAMETER rather than truncated, and a
    /// silently truncated password would be far worse than a refusal.</summary>
    private const int MaxBlobBytes = 512;

    private const int ERROR_NOT_FOUND = 1168;

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredWrite(ref CREDENTIAL credential, int flags);

    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredRead(string targetName, int type, int reservedFlag, out IntPtr credential);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredDelete(string targetName, int type, int flags);

    [DllImport("advapi32.dll", EntryPoint = "CredFree")]
    private static extern void CredFree(IntPtr buffer);

    // ---------------------------------------------------------------
    // Target naming
    // ---------------------------------------------------------------

    /// <summary>
    /// The server part of a UNC path: <c>\\10.0.0.5\Documents</c> gives
    /// <c>10.0.0.5</c>. Returns null for anything that is not a UNC path.
    ///
    /// The credential is stored against the SERVER, not the share, because
    /// that is the key Windows looks up when authenticating to any share on
    /// that machine. Storing per-share would mean a Drobo with six shares
    /// asked for the same password six times, and Windows would ignore five
    /// of the entries.
    /// </summary>
    public static string? HostOf(string uncPath)
    {
        if (string.IsNullOrWhiteSpace(uncPath)) return null;
        var p = uncPath.Replace('/', '\\').TrimStart();
        if (!p.StartsWith(@"\\", StringComparison.Ordinal)) return null;
        var rest = p.Substring(2);
        if (rest.Length == 0) return null;
        var slash = rest.IndexOf('\\');
        var host = slash < 0 ? rest : rest.Substring(0, slash);
        return host.Length == 0 ? null : host;
    }

    // ---------------------------------------------------------------
    // The three operations
    // ---------------------------------------------------------------

    /// <summary>
    /// Saves (or replaces) the login for one server. Returns null on success,
    /// or a sentence explaining the failure, suitable for showing the user.
    ///
    /// Returning a message rather than throwing or returning a bare false:
    /// this runs right after a share connection SUCCEEDED, so a failure here
    /// must not read like the connection failed. The caller says so in those
    /// words.
    /// </summary>
    public static string? Save(string host, string userName, string password)
    {
        if (string.IsNullOrWhiteSpace(host)) return "No server name to save the login against.";
        if (string.IsNullOrEmpty(userName)) return "No username to save.";

        var passwordBytes = Encoding.Unicode.GetBytes(password ?? string.Empty);
        if (passwordBytes.Length > MaxBlobBytes)
        {
            Array.Clear(passwordBytes, 0, passwordBytes.Length);
            return "Windows will not store a password longer than 256 characters. "
                 + "The share is connected for this session; the login was not saved.";
        }

        IntPtr blob = IntPtr.Zero, target = IntPtr.Zero, user = IntPtr.Zero, comment = IntPtr.Zero;
        try
        {
            // AllocCoTaskMem(0) is legal but returns a pointer we would then
            // describe as zero-length; Windows accepts an empty blob for an
            // account with no password, so keep one byte to point at.
            blob = Marshal.AllocCoTaskMem(Math.Max(passwordBytes.Length, 1));
            Marshal.Copy(passwordBytes, 0, blob, passwordBytes.Length);

            target = Marshal.StringToCoTaskMemUni(host);
            user = Marshal.StringToCoTaskMemUni(userName);
            comment = Marshal.StringToCoTaskMemUni("Saved by Drobo Dashboard REBORN");

            var cred = new CREDENTIAL
            {
                Type = CRED_TYPE_DOMAIN_PASSWORD,
                TargetName = target,
                UserName = user,
                Comment = comment,
                CredentialBlob = blob,
                CredentialBlobSize = passwordBytes.Length,
                Persist = CRED_PERSIST_LOCAL_MACHINE,
            };

            if (CredWrite(ref cred, 0)) return null;

            var err = Marshal.GetLastWin32Error();
            return $"Windows would not save the login (error {err}). "
                 + "The share is connected for this session.";
        }
        finally
        {
            // Zero the unmanaged copy before releasing it. Freed heap memory
            // is not cleared by the allocator, and this block held a
            // plaintext password.
            if (blob != IntPtr.Zero)
            {
                for (int i = 0; i < Math.Max(passwordBytes.Length, 1); i++) Marshal.WriteByte(blob, i, 0);
                Marshal.FreeCoTaskMem(blob);
            }
            if (target != IntPtr.Zero) Marshal.FreeCoTaskMem(target);
            if (user != IntPtr.Zero) Marshal.FreeCoTaskMem(user);
            if (comment != IntPtr.Zero) Marshal.FreeCoTaskMem(comment);
            Array.Clear(passwordBytes, 0, passwordBytes.Length);
        }
    }

    /// <summary>
    /// The username of the saved login for this server, or null if there is
    /// none. Never returns the password: see the class remarks.
    /// </summary>
    public static string? SavedUserName(string host)
    {
        if (string.IsNullOrWhiteSpace(host)) return null;
        if (!CredRead(host, CRED_TYPE_DOMAIN_PASSWORD, 0, out IntPtr handle)) return null;
        try
        {
            var cred = Marshal.PtrToStructure<CREDENTIAL>(handle);
            return cred.UserName == IntPtr.Zero
                ? string.Empty
                : Marshal.PtrToStringUni(cred.UserName) ?? string.Empty;
        }
        finally
        {
            CredFree(handle);
        }
    }

    /// <summary>Whether a login is saved for this server.</summary>
    public static bool HasSavedLogin(string host) => SavedUserName(host) is not null;

    /// <summary>
    /// Removes the saved login. Returns true if it is gone afterwards,
    /// including when there was nothing there to begin with, since "forget
    /// this" and "there is nothing to forget" leave the user in the same
    /// place and should not be reported differently.
    /// </summary>
    public static bool Forget(string host)
    {
        if (string.IsNullOrWhiteSpace(host)) return false;
        if (CredDelete(host, CRED_TYPE_DOMAIN_PASSWORD, 0)) return true;
        return Marshal.GetLastWin32Error() == ERROR_NOT_FOUND;
    }
}
