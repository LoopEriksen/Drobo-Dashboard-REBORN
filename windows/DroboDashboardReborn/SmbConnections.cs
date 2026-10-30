using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;

namespace DroboDashboardReborn;

/// <summary>
/// Talks to Windows' own SMB connection manager (the "mpr.dll" WNet* APIs)
/// so this app can connect to a Drobo share as a named user instead of the
/// guest logon Windows 11 refuses. See shares.py / docs/accessing-your-files.md
/// for the full story of why guest shares stopped working.
///
/// Why P/Invoke instead of shelling out to `net use \\host\share pass /user:name`:
/// a password passed as a command-line argument sits in that process's
/// argument list, which every other process on the machine can read for as
/// long as it runs (Task Manager, Process Explorer, WMI queries all show
/// it). WNetAddConnection2 hands the password to Windows directly, in
/// memory, for the single call -- it never touches a command line or a log.
///
/// It reaches disk in exactly one place, and only when asked: with "Save this
/// login on this PC" ticked, <see cref="CredentialStore"/> writes it to
/// Windows' Credential Manager, encrypted by Windows and scoped to the
/// signed-in account. This class still writes nothing itself. (Before
/// 2026-09-08 nothing was stored at all; the header used to end "We don't
/// write it anywhere either", which stopped being true that day.)
/// </summary>
internal static class SmbConnections
{
    // ---------------------------------------------------------------
    // Win32 declarations (mpr.dll)
    // ---------------------------------------------------------------

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NETRESOURCE
    {
        public int dwScope;
        public int dwType;
        public int dwDisplayType;
        public int dwUsage;
        public string? lpLocalName;
        public string? lpRemoteName;
        public string? lpComment;
        public string? lpProvider;
    }

    // Win32 values from winnetwk.h: disk shares only (not printers), and
    // only the connections that exist right now.
    private const int RESOURCETYPE_DISK = 0x00000001;
    private const int RESOURCE_CONNECTED = 0x00000001;

    /// <summary>
    /// Asks Windows to persist the CONNECTION -- specifically, to restore a
    /// mapped drive letter at the next sign-in. Windows applies it only to a
    /// connection that redirects a local device; on a deviceless connection
    /// it is silently ignored, which is why it was never a credential-storage
    /// mechanism on its own.
    ///
    /// Storing the LOGIN is a separate job and belongs to
    /// <see cref="CredentialStore"/>, which writes the credential explicitly
    /// and works with or without a drive letter. The two are set together by
    /// the same checkbox but do different things.
    /// </summary>
    public const int CONNECT_UPDATE_PROFILE = 0x00000001;

    [DllImport("mpr.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int WNetAddConnection2(
        ref NETRESOURCE lpNetResource, string? lpPassword, string? lpUsername, int dwFlags);

    [DllImport("mpr.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int WNetCancelConnection2(
        string lpName, int dwFlags, [MarshalAs(UnmanagedType.Bool)] bool fForce);

    [DllImport("mpr.dll", SetLastError = true)]
    private static extern int WNetOpenEnum(
        int dwScope, int dwType, int dwUsage, IntPtr lpNetResource, out IntPtr lphEnum);

    [DllImport("mpr.dll")]
    private static extern int WNetCloseEnum(IntPtr hEnum);

    [DllImport("mpr.dll", CharSet = CharSet.Unicode)]
    private static extern int WNetEnumResource(
        IntPtr hEnum, ref int lpcCount, IntPtr lpBuffer, ref int lpBufferSize);

    private const int NO_ERROR = 0;   // winerror.h: success

    /// <summary>
    /// The buffer WNetEnumResource fills, in bytes. 64KB comfortably holds
    /// the handful of shares a home NAS offers plus whatever else Windows
    /// has mapped. If there were ever more than that, WNetEnumResource
    /// returns an error and we simply stop -- a partial list beats crashing
    /// the Shares tab over an edge case this app will never actually see on
    /// a home network.
    /// </summary>
    private const int EnumBufferSizeBytes = 65536;

    // ---------------------------------------------------------------
    // Reading Windows' current connections
    // ---------------------------------------------------------------

    /// <summary>One currently-active Windows connection to a network share.
    /// LocalDeviceName is the drive letter it is mapped to (Win32's
    /// lpLocalName, e.g. "Z:"), or null when the share is connected without
    /// consuming one.</summary>
    public readonly record struct ActiveConnection(string RemoteUnc, string? LocalDeviceName);

    /// <summary>
    /// Every network share Windows currently has open, drive-lettered or
    /// not. Read fresh each time -- this is Windows' own live state, not
    /// something we cache, so it stays correct even if the share was mapped
    /// from Explorer instead of from this app.
    /// </summary>
    public static List<ActiveConnection> GetActiveConnections()
    {
        var results = new List<ActiveConnection>();
        if (WNetOpenEnum(RESOURCE_CONNECTED, RESOURCETYPE_DISK, 0, IntPtr.Zero, out var handle) != NO_ERROR)
            return results; // nothing connected, or enumeration unavailable -- an empty list is the safe default

        try
        {
            ReadAllConnections(handle, results);
        }
        finally
        {
            WNetCloseEnum(handle);
        }
        return results;
    }

    /// <summary>Drains an open enumeration into <paramref name="results"/>,
    /// one buffer-full at a time, freeing the buffer whatever happens. The
    /// caller owns, and closes, <paramref name="enumHandle"/>.</summary>
    private static void ReadAllConnections(IntPtr enumHandle, List<ActiveConnection> results)
    {
        var buffer = Marshal.AllocHGlobal(EnumBufferSizeBytes);
        try
        {
            while (ReadOneBatch(enumHandle, buffer, results))
            {
                // Each pass appends one batch; ReadOneBatch says when to stop.
            }
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    /// <summary>One WNetEnumResource call: appends every entry that names a
    /// share and returns true, or returns false once the call reports
    /// anything but NO_ERROR (the end of the list included) -- NO_ERROR is
    /// the only "keep going" result.</summary>
    private static bool ReadOneBatch(IntPtr enumHandle, IntPtr buffer, List<ActiveConnection> results)
    {
        int entryCount = -1;
        int bufferSizeBytes = EnumBufferSizeBytes;
        int resultCode = WNetEnumResource(enumHandle, ref entryCount, buffer, ref bufferSizeBytes);
        if (resultCode != NO_ERROR) return false;

        int entrySizeBytes = Marshal.SizeOf<NETRESOURCE>();
        for (int entryIndex = 0; entryIndex < entryCount; entryIndex++)
        {
            var resource = Marshal.PtrToStructure<NETRESOURCE>(buffer + entryIndex * entrySizeBytes);
            if (!string.IsNullOrEmpty(resource.lpRemoteName))
                results.Add(new ActiveConnection(resource.lpRemoteName!, resource.lpLocalName));
        }
        return true;
    }

    /// <summary>The first unused drive letter from Z down to D, or null if none are
    /// free. D upward is skipped to stay clear of A/B (floppy-era conventions some
    /// software still assumes) and C (the system drive).</summary>
    public static string? NextFreeDriveLetter()
    {
        var usedLetters = new HashSet<char>(
            DriveInfo.GetDrives().Select(drive => char.ToUpperInvariant(drive.Name[0])));
        for (char letter = 'Z'; letter >= 'D'; letter--)
            if (!usedLetters.Contains(letter))
                return letter + ":";
        return null;
    }

    // ---------------------------------------------------------------
    // Connect / disconnect
    // ---------------------------------------------------------------

    /// <summary>
    /// Connects to <paramref name="uncPath"/> as a named user.
    ///
    /// <paramref name="driveLetter"/> may be null/empty to connect without
    /// consuming a drive letter -- Explorer can still open the UNC path
    /// directly once Windows has a session to it.
    ///
    /// <paramref name="username"/> and <paramref name="password"/> may BOTH be
    /// null, which is a documented instruction to WNetAddConnection2 rather
    /// than a missing argument: it tells Windows to authenticate with whatever
    /// credential it already holds for that server, which is how a login saved
    /// through <see cref="CredentialStore"/> gets used without this app ever
    /// seeing the password again. Windows does not hand a stored share
    /// password back to a caller, so this is the only way to reuse one.
    ///
    /// Returns 0 on success, or a Win32 error code -- pass that to
    /// <see cref="DescribeError"/> to turn it into plain English.
    /// </summary>
    public static int Connect(string uncPath, string? driveLetter, string? username, string? password, bool rememberConnection)
    {
        var netResource = new NETRESOURCE
        {
            dwType = RESOURCETYPE_DISK,
            lpRemoteName = uncPath,
            lpLocalName = string.IsNullOrWhiteSpace(driveLetter) ? null : driveLetter,
        };
        int connectFlags = rememberConnection ? CONNECT_UPDATE_PROFILE : 0;
        return WNetAddConnection2(ref netResource, password, username, connectFlags);
    }

    /// <summary>Disconnects a drive letter ("Z:") or a UNC-only session (pass the UNC
    /// path itself). Returns 0 on success, or a Win32 error code.</summary>
    public static int Disconnect(string nameOrUnc, bool force = false) =>
        WNetCancelConnection2(nameOrUnc, 0, force);

    // ---------------------------------------------------------------
    // Error codes -> plain English
    // ---------------------------------------------------------------

    // The Win32 codes (winerror.h) that DescribeError maps to a sentence of
    // its own: the ones Connect and Disconnect are known to return against a
    // Drobo. Anything else gets Windows' own wording plus the number.
    public const int ERROR_ACCESS_DENIED = 5;
    public const int ERROR_BAD_NETPATH = 53;
    public const int ERROR_ALREADY_ASSIGNED = 85;
    public const int ERROR_EXTENDED_ERROR = 1208; // 0x4B8
    public const int ERROR_SESSION_CREDENTIAL_CONFLICT = 1219;
    public const int ERROR_LOGON_FAILURE = 1326;

    /// <summary>
    /// Turns a Win32 error code from Connect/Disconnect into a sentence the
    /// user can act on instead of a bare number. <paramref name="guestAdvice"/>
    /// is the explanation the agent already produced for this exact device
    /// (shares.py's access_advice.detail) -- reused here because
    /// ERROR_ACCESS_DENIED is usually that same guest-logon story.
    /// </summary>
    public static string DescribeError(int win32ErrorCode, string? guestAdvice)
    {
        switch (win32ErrorCode)
        {
            case NO_ERROR:
                return "";
            // WNetAddConnection2 commonly returns 1208 (ERROR_EXTENDED_ERROR) for a
            // rejected SMB logon rather than 1326 -- Windows is saying "ask the
            // provider for the real reason" (WNetGetLastError), but a wrong Drobo
            // username/password is the overwhelmingly common cause on this device,
            // so both codes get the same plain-English message.
            case ERROR_EXTENDED_ERROR:
            case ERROR_LOGON_FAILURE:
                return "Windows says the username or password is wrong. Double-check " +
                       "both and try again -- this is your Drobo login, not your Windows login.";
            case ERROR_BAD_NETPATH:
                return "The Drobo didn't answer. Check that it's powered on and connected to the network.";
            case ERROR_SESSION_CREDENTIAL_CONFLICT:
                return "Windows already has a different connection open to this Drobo, " +
                       "under a different login. Disconnect it first, then try again.";
            case ERROR_ACCESS_DENIED:
                return !string.IsNullOrWhiteSpace(guestAdvice)
                    ? guestAdvice!
                    : "Windows was refused access to this share. Check the username and " +
                      "password, and that this user has permission to this share on the Drobo.";
            case ERROR_ALREADY_ASSIGNED:
                return "That drive letter is already in use for something else. Pick a different one.";
            default:
                return $"Windows reported an error connecting: {new Win32Exception(win32ErrorCode).Message} (code {win32ErrorCode}).";
        }
    }
}
