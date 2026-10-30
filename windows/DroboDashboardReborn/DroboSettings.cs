using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace DroboDashboardReborn;

/// <summary>
/// Everything this app remembers between launches: the last Drobo you
/// connected to, any addresses you typed in by hand, and whether automatic
/// network discovery is turned on -- all in one settings.json under
/// %LOCALAPPDATA%, never in the repo (same rule as the agent token, see
/// MainWindow.CandidateConfigPaths) and never a password.
///
/// Loaded and saved as one whole document (LoadAll/SaveAll) rather than
/// field-by-field, so adding a manual address can never accidentally wipe
/// out the remembered device, or vice versa.
/// </summary>
internal static class DroboSettings
{
    /// <summary>
    /// The last Drobo the user connected to, kept so the app can open it
    /// again on the next launch. Identified by its serial; the address and
    /// name are kept only to explain things when it does not show up.
    /// </summary>
    public sealed class RememberedDevice
    {
        /// <summary>The Drobo's permanent hardware serial. Matched on this, not
        /// the IP -- if DHCP hands the Drobo a new address, esa_id is still how
        /// we recognise it's the same one on the next /api/discover.</summary>
        public string EsaId { get; set; } = "";

        /// <summary>The address it had last time: what the app checks when the
        /// device is missing, never what it is matched on.</summary>
        public string Host { get; set; } = "";

        /// <summary>The device's name last time, used to name it in the
        /// "isn't showing up" message.</summary>
        public string Name { get; set; } = "";

        /// <summary>The device's model last time, for display.</summary>
        public string Model { get; set; } = "";
    }

    /// <summary>
    /// A Drobo address typed in by hand rather than found by mDNS -- the way in
    /// on networks that filter multicast, where browsing returns nothing however
    /// long it listens.
    ///
    /// These are checked properly now. The agent's /api/discover takes a
    /// <c>?host=</c> parameter meaning "go and look at this address": it
    /// connects, reads the device's own greeting, and reports the name, model
    /// and serial the Drobo claims for itself. So a manual entry is confirmed
    /// the moment it is added, and again on every search, rather than waiting
    /// for an automatic pass to stumble across the same host.
    ///
    /// (Until 2026-07-28 the agent had no such parameter, so this app could
    /// only ever label a typed address "not verified" and hope. The badge still
    /// exists, but it now means the address was asked directly and did not
    /// answer -- a real measurement rather than an absence of one.)
    ///
    /// The app still never opens a socket to the Drobo itself; the agent does
    /// the looking, as it does for everything else.
    /// </summary>
    public sealed class ManualDevice
    {
        /// <summary>The address as the user typed it. Blank entries are dropped
        /// when the file is read.</summary>
        public string Host { get; set; } = "";

        /// <summary>0 means "not specified" -- displayed without a port suffix
        /// rather than as port 0, which would read as a real (if useless) port.</summary>
        public int Port { get; set; }

        /// <summary>"host:port", or just the host when no port was given, so
        /// the list never shows a meaningless ":0".</summary>
        public string DisplayText => Port > 0 ? $"{Host}:{Port}" : Host;
    }

    /// <summary>
    /// The whole settings.json as one object. Every property starts at the
    /// value a first-ever launch should have, so whatever LoadAll cannot
    /// read simply stays at that default.
    /// </summary>
    public sealed class Settings
    {
        /// <summary>The Drobo to reopen at launch; null when none is
        /// remembered (first launch, or after "Forget this Drobo").</summary>
        public RememberedDevice? Remembered { get; set; }

        /// <summary>Addresses typed in by hand, in the order they were added.</summary>
        public List<ManualDevice> ManualDevices { get; set; } = new();

        /// <summary>Default ON, per the maintainer's ask. Only ever OFF when the file
        /// explicitly says so -- see the presence check in LoadAll, which exists
        /// specifically so a missing key can't be misread as "off".</summary>
        public bool AutoDiscoveryEnabled { get; set; } = true;

        /// <summary>What "Copy diagnostics" on the Tools tab does without asking.
        /// Defaults to Ask -- the first click always shows the choice dialog until
        /// the user either ticks "remember my choice" there, or sets one of the
        /// other two here.</summary>
        public DiagnosticsPreference DiagnosticsPreference { get; set; } = DiagnosticsPreference.Ask;

        /// <summary>Whether the window's close button (X) hides to the tray
        /// instead of exiting. Opt-in, default OFF -- the maintainer has already
        /// complained twice about this app being intrusive, so the X really
        /// closes it unless this is explicitly turned on. See TrayIconManager.</summary>
        public bool CloseToTrayEnabled { get; set; } = false;

        /// <summary>Master switch for desktop notifications. Default OFF, like
        /// every individual category below it. Allowed back in on 2026-07-28,
        /// narrowed to alerts only; see AlertNotifier for the four rules that
        /// make that acceptable rather than a reversal.</summary>
        public bool NotificationsEnabled { get; set; } = false;

        /// <summary>Per-category switches, keyed by AlertNotifier category id.
        /// A category missing from here counts as OFF -- so a category added in
        /// a later version stays silent until it is deliberately turned on,
        /// rather than surprising someone who upgraded.</summary>
        public Dictionary<string, bool> NotificationCategories { get; set; }
            = AlertNotifier.DefaultEnabled();
    }

    /// <summary>Tools tab, "Copy diagnostics": where the result goes, and whether
    /// that's decided every time or remembered.</summary>
    public enum DiagnosticsPreference { Ask, Clipboard, File }

    /// <summary>settings.json under %LOCALAPPDATA%\DroboDashboardReborn: per
    /// Windows user, outside the install folder (which an upgrade replaces)
    /// and outside any repository.</summary>
    private static string SettingsFilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "DroboDashboardReborn", "settings.json");

    /// <summary>Never throws -- a missing or corrupt file just means the same
    /// safe defaults a first-ever launch would have: nothing remembered,
    /// nothing manual, discovery on.</summary>
    public static Settings LoadAll()
    {
        var result = new Settings();
        try
        {
            var path = SettingsFilePath;
            if (!File.Exists(path)) return result;

            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            var root = doc.RootElement;

            ReadRememberedDevice(root, result);
            ReadManualDevices(root, result);

            // Explicit presence + kind check, not a bare GetBoolean() default:
            // an ABSENT key must mean "on" (the documented default), while a
            // PRESENT "false" must be honoured. Collapsing those two cases
            // would be exactly the falsy-default trap this codebase has been
            // bitten by before -- see the SeverityRank comment in
            // MainWindow.xaml.cs for the earlier version of the same mistake.
            if (ReadOptionalBool(root, "auto_discovery_enabled") is bool autoDiscoveryEnabled)
                result.AutoDiscoveryEnabled = autoDiscoveryEnabled;

            ReadDiagnosticsPreference(root, result);

            // Same explicit presence check as auto_discovery_enabled above --
            // an absent key must mean the documented default (OFF), not just
            // whatever GetBoolean() would fall back to.
            if (ReadOptionalBool(root, "close_to_tray_enabled") is bool closeToTrayEnabled)
                result.CloseToTrayEnabled = closeToTrayEnabled;

            if (ReadOptionalBool(root, "notifications_enabled") is bool notificationsEnabled)
                result.NotificationsEnabled = notificationsEnabled;

            ReadNotificationCategories(root, result);
        }
        catch
        {
            // Corrupt or unreadable settings just means "start from the safe
            // defaults already set on `result`" -- never a crash.
        }
        return result;
    }

    /// <summary>
    /// The value of a JSON true/false property, or null when the key is
    /// absent or holds anything else. Null lets the caller keep its own
    /// documented default instead of a bare GetBoolean() fallback, which is
    /// what makes "absent" and "false" two different answers.
    /// </summary>
    private static bool? ReadOptionalBool(JsonElement element, string key) =>
        element.TryGetProperty(key, out var valueElement)
        && (valueElement.ValueKind == JsonValueKind.True || valueElement.ValueKind == JsonValueKind.False)
            ? valueElement.ValueKind == JsonValueKind.True
            : (bool?)null;

    /// <summary>A string property's value, or "" when it is absent, null or
    /// not a string, so a hand-edited file cannot put a null into a row.</summary>
    private static string ReadStringOrEmpty(JsonElement element, string key) =>
        element.TryGetProperty(key, out var valueElement) && valueElement.ValueKind == JsonValueKind.String
            ? (valueElement.GetString() ?? "") : "";

    /// <summary>Reads remembered_device. An entry without an esa_id is
    /// ignored: there is nothing worth remembering without a serial to match
    /// on.</summary>
    private static void ReadRememberedDevice(JsonElement root, Settings result)
    {
        if (!root.TryGetProperty("remembered_device", out var rememberedElement)
            || rememberedElement.ValueKind != JsonValueKind.Object) return;

        var esaId = ReadStringOrEmpty(rememberedElement, "esa_id");
        if (string.IsNullOrEmpty(esaId)) return;

        result.Remembered = new RememberedDevice
        {
            EsaId = esaId,
            Host = ReadStringOrEmpty(rememberedElement, "host"),
            Name = ReadStringOrEmpty(rememberedElement, "name"),
            Model = ReadStringOrEmpty(rememberedElement, "model"),
        };
    }

    /// <summary>Reads manual_devices, skipping any entry that is not an
    /// object or has no host. A missing or non-numeric port reads as 0
    /// ("not specified").</summary>
    private static void ReadManualDevices(JsonElement root, Settings result)
    {
        if (!root.TryGetProperty("manual_devices", out var manualDevicesElement)
            || manualDevicesElement.ValueKind != JsonValueKind.Array) return;

        foreach (var item in manualDevicesElement.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object) continue;
            var host = ReadStringOrEmpty(item, "host");
            if (string.IsNullOrWhiteSpace(host)) continue;
            var port = item.TryGetProperty("port", out var portElement) && portElement.ValueKind == JsonValueKind.Number
                ? portElement.GetInt32() : 0;
            result.ManualDevices.Add(new ManualDevice { Host = host, Port = port });
        }
    }

    /// <summary>Reads diagnostics_preference. Any value other than
    /// "clipboard" or "file" (in any case) means Ask, the safe choice.</summary>
    private static void ReadDiagnosticsPreference(JsonElement root, Settings result)
    {
        if (!root.TryGetProperty("diagnostics_preference", out var preferenceElement)
            || preferenceElement.ValueKind != JsonValueKind.String) return;

        result.DiagnosticsPreference = (preferenceElement.GetString() ?? "").ToLowerInvariant() switch
        {
            "clipboard" => DiagnosticsPreference.Clipboard,
            "file" => DiagnosticsPreference.File,
            _ => DiagnosticsPreference.Ask,
        };
    }

    /// <summary>
    /// Reads notification_categories onto the defaults rather than replacing
    /// them, and only for ids we recognise. A category added in a later
    /// version is therefore absent from an older settings file and stays OFF,
    /// instead of an upgrade quietly starting to notify about something
    /// nobody asked for.
    /// </summary>
    private static void ReadNotificationCategories(JsonElement root, Settings result)
    {
        if (!root.TryGetProperty("notification_categories", out var categoriesElement)
            || categoriesElement.ValueKind != JsonValueKind.Object) return;

        foreach (var (id, _, _) in AlertNotifier.Categories)
        {
            if (ReadOptionalBool(categoriesElement, id) is bool isCategoryOn)
                result.NotificationCategories[id] = isCategoryOn;
        }
    }

    /// <summary>
    /// Writes the whole document. Never throws: failing to save is not
    /// worth interrupting the user for, because whatever was on screen a
    /// moment ago still works -- the setting just will not survive a
    /// restart. The JSON keys are the settings.json format and must match
    /// what LoadAll reads.
    /// </summary>
    public static void SaveAll(Settings settings)
    {
        try
        {
            var path = SettingsFilePath;
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var json = JsonSerializer.Serialize(new
            {
                remembered_device = settings.Remembered is { } remembered ? new
                {
                    esa_id = remembered.EsaId,
                    host = remembered.Host,
                    name = remembered.Name,
                    model = remembered.Model,
                } : null,
                manual_devices = settings.ManualDevices.Select(manualDevice => new { host = manualDevice.Host, port = manualDevice.Port }).ToArray(),
                auto_discovery_enabled = settings.AutoDiscoveryEnabled,
                diagnostics_preference = settings.DiagnosticsPreference switch
                {
                    DiagnosticsPreference.Clipboard => "clipboard",
                    DiagnosticsPreference.File => "file",
                    _ => "ask",
                },
                close_to_tray_enabled = settings.CloseToTrayEnabled,
                notifications_enabled = settings.NotificationsEnabled,
                notification_categories = settings.NotificationCategories,
            }, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(path, json);
        }
        catch
        {
            // Not being able to save is not worth interrupting the flow for --
            // whatever was on screen a moment ago still works.
        }
    }

    // ---- convenience mutators: each does a full load-modify-save so no
    // caller has to worry about clobbering the other fields in the file ----

    /// <summary>Stores the Drobo to reopen at the next launch, replacing any
    /// earlier one: only one device is remembered.</summary>
    public static void RememberDevice(RememberedDevice device)
    {
        var settings = LoadAll();
        settings.Remembered = device;
        SaveAll(settings);
    }

    /// <summary>"Forget this Drobo" -- turns off the auto-open behaviour without
    /// making the user go hunt down the settings file themselves.</summary>
    public static void ForgetDevice()
    {
        var settings = LoadAll();
        settings.Remembered = null;
        SaveAll(settings);
    }

    /// <summary>Adds a typed-in address unless the same host and port are
    /// already listed. Hosts compare case-insensitively because host names
    /// are, so "Drobo" and "drobo" do not become two rows for one device.</summary>
    public static void AddManualDevice(string host, int port)
    {
        var settings = LoadAll();
        if (!settings.ManualDevices.Any(m => string.Equals(m.Host, host, StringComparison.OrdinalIgnoreCase) && m.Port == port))
            settings.ManualDevices.Add(new ManualDevice { Host = host, Port = port });
        SaveAll(settings);
    }

    /// <summary>Removes every entry with this host (any case) and port, so
    /// duplicates from a hand-edited file go too.</summary>
    public static void RemoveManualDevice(string host, int port)
    {
        var settings = LoadAll();
        settings.ManualDevices.RemoveAll(m => string.Equals(m.Host, host, StringComparison.OrdinalIgnoreCase) && m.Port == port);
        SaveAll(settings);
    }

    /// <summary>Saves the Settings tab's automatic-discovery switch.</summary>
    public static void SetAutoDiscoveryEnabled(bool enabled)
    {
        var settings = LoadAll();
        settings.AutoDiscoveryEnabled = enabled;
        SaveAll(settings);
    }

    /// <summary>Saves what "Copy diagnostics" does without asking, from the
    /// Settings tab or the choice dialog's "remember my choice".</summary>
    public static void SetDiagnosticsPreference(DiagnosticsPreference preference)
    {
        var settings = LoadAll();
        settings.DiagnosticsPreference = preference;
        SaveAll(settings);
    }

    /// <summary>Saves the Settings tab's close-to-tray switch.</summary>
    public static void SetCloseToTrayEnabled(bool enabled)
    {
        var settings = LoadAll();
        settings.CloseToTrayEnabled = enabled;
        SaveAll(settings);
    }
}
