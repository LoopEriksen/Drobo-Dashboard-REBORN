using System;
using System.Globalization;
using System.Text.Json;

namespace DroboDashboardReborn;

/// <summary>
/// Small helpers for pulling typed values out of the agent's JSON without
/// every call site having to check TryGetProperty and the value kind by
/// hand. Used by the Status tab (device/capacity/drives/alerts) and the
/// Shares tab alike -- pulled out of MainWindow.xaml.cs so both can use it
/// without that file becoming a dumping ground.
/// </summary>
internal static class JsonHelpers
{
    // Every getter below falls back silently -- to the caller's fallback,
    // 0 or false -- when the key is missing or holds another kind of
    // value. That keeps an older or newer agent's reply from throwing
    // mid-render, but it also means 0 and false cannot tell "the agent
    // said zero" from "the agent said nothing": the falsy-zero trap.
    // Call sites where that difference matters do not lean on a zero:
    // a volume's partition count checks TryGetProperty itself, and the
    // performance card goes by the agent's "measured" flag (IsTrue)
    // before it reads any counter.
    //
    // "Another kind of value" includes the element itself. Until
    // 2026-11-02 each getter called TryGetProperty straight away, which
    // throws when the element is a JSON null (`"capacity": null`) or a
    // default JsonElement, and GetInt64 threw on a number with a
    // fraction; RefreshStatus caught either as "Cannot reach the agent".

    /// <summary>The member <paramref name="name"/> of an object, or false
    /// when <paramref name="element"/> is not an object at all (null, an
    /// array, a default element), where TryGetProperty would throw.</summary>
    private static bool TryGetMember(JsonElement element, string name, out JsonElement value)
    {
        value = default;
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out value);
    }

    /// <summary>The string at <paramref name="name"/>, or
    /// <paramref name="fallback"/> when it is missing, null or not a
    /// string.</summary>
    public static string GetStringOr(JsonElement element, string name, string fallback) =>
        TryGetMember(element, name, out var value) && value.ValueKind == JsonValueKind.String
            ? (value.GetString() ?? fallback) : fallback;

    /// <summary>The integer at <paramref name="name"/>, or 0 when it is
    /// missing, not a number, or a number that is not a whole 64-bit one
    /// -- see the note above on why 0 cannot mean "absent" to a caller
    /// that needs to know.</summary>
    public static long GetInt64OrZero(JsonElement element, string name) =>
        TryGetMember(element, name, out var value) && value.ValueKind == JsonValueKind.Number
        && value.TryGetInt64(out var wholeNumber) ? wholeNumber : 0;

    /// <summary>The integer at <paramref name="name"/>, or
    /// <paramref name="fallback"/> when it is missing, not a number, or not
    /// a whole number that fits in 32 bits (41.5, 1e10). GetInt32 throws on
    /// those; this is what every call site that wants an int uses
    /// instead.</summary>
    public static int GetInt32Or(JsonElement element, string name, int fallback) =>
        TryGetMember(element, name, out var value) && value.ValueKind == JsonValueKind.Number
        && value.TryGetInt32(out var wholeNumber) ? wholeNumber : fallback;

    /// <summary>The number at <paramref name="name"/>, or 0 when it is
    /// missing or not a number (the same falsy-zero caveat).</summary>
    public static double GetDoubleOrZero(JsonElement element, string name) =>
        GetDoubleOr(element, name, 0);

    /// <summary>The number at <paramref name="name"/>, or
    /// <paramref name="fallback"/> when it is missing or not a
    /// number.</summary>
    public static double GetDoubleOr(JsonElement element, string name, double fallback) =>
        TryGetMember(element, name, out var value) && value.ValueKind == JsonValueKind.Number
        && value.TryGetDouble(out var number) ? number : fallback;

    /// <summary>True only for a JSON <c>true</c>. A missing key, null, or
    /// a truthy-looking string or number is false, so a flag the agent
    /// did not send never switches anything on.</summary>
    public static bool IsTrue(JsonElement element, string name) =>
        TryGetMember(element, name, out var value) && value.ValueKind == JsonValueKind.True;

    /// <summary>
    /// A TCP port at <paramref name="name"/>: a whole number from 1 to
    /// 65535, written either as a number or as text ("7420"), or
    /// <paramref name="fallback"/> for anything else.
    ///
    /// Text is accepted because the agent accepts it: it reads its own
    /// config.json with <c>int(config["agent"]["port"])</c>, so
    /// <c>"port": "7420"</c> runs. Until 2026-11-02 the app called
    /// TryGetInt32 on that string, which throws, and the whole agent config
    /// was skipped -- the token with it.
    /// </summary>
    public static int GetPortOr(JsonElement element, string name, int fallback)
    {
        if (!TryGetMember(element, name, out var value)) return fallback;
        int port = fallback;
        bool parsed = value.ValueKind switch
        {
            JsonValueKind.Number => value.TryGetInt32(out port),
            JsonValueKind.String => int.TryParse(value.GetString(), NumberStyles.AllowLeadingWhite | NumberStyles.AllowTrailingWhite,
                                                 CultureInfo.InvariantCulture, out port),
            _ => false,
        };
        return parsed && port is >= 1 and <= 65535 ? port : fallback;
    }

    /// <summary>Auto-scaling byte formatter -- B/KB/MB/GB/TB/PB, same thresholds
    /// and rounding as webui.py's own `bytes()`. The old version always divided
    /// by 1e12, so anything under ~5 GB rendered as a flat "0.00 TB", which
    /// reads as "zero free space" on a near-full array when it's anything but.
    /// Zero bytes is a real reading here, not a missing one, so it prints as
    /// "0 B" rather than an em dash.</summary>
    public static string FormatBytes(long sizeBytes)
    {
        double scaledValue = sizeBytes;
        string[] units = { "B", "KB", "MB", "GB", "TB", "PB" };
        int unitIndex = 0;
        while (Math.Abs(scaledValue) >= 1000 && unitIndex < units.Length - 1) { scaledValue /= 1000; unitIndex++; }
        string text = unitIndex == 0
            ? scaledValue.ToString("0", System.Globalization.CultureInfo.InvariantCulture)
            : scaledValue.ToString(Math.Abs(scaledValue) < 10 ? "0.00" : "0.0", System.Globalization.CultureInfo.InvariantCulture);
        return $"{text} {units[unitIndex]}";
    }
}
