using System;
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

    /// <summary>The string at <paramref name="name"/>, or
    /// <paramref name="fallback"/> when it is missing, null or not a
    /// string.</summary>
    public static string GetStringOr(JsonElement element, string name, string fallback) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? (value.GetString() ?? fallback) : fallback;

    /// <summary>The integer at <paramref name="name"/>, or 0 when it is
    /// missing or not a number -- see the note above on why 0 cannot mean
    /// "absent" to a caller that needs to know.</summary>
    public static long GetInt64OrZero(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number ? value.GetInt64() : 0;

    /// <summary>The number at <paramref name="name"/>, or 0 when it is
    /// missing or not a number (the same falsy-zero caveat).</summary>
    public static double GetDoubleOrZero(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number ? value.GetDouble() : 0;

    /// <summary>True only for a JSON <c>true</c>. A missing key, null, or
    /// a truthy-looking string or number is false, so a flag the agent
    /// did not send never switches anything on.</summary>
    public static bool IsTrue(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;

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
