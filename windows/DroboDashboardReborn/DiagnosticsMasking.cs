using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace DroboDashboardReborn;

/// <summary>
/// Makes a copy of the agent's /api/status JSON that a tester can post in a
/// public issue as it is.
///
/// Why it exists: until 2026-11-20 "Copy diagnostics" put the raw JSON on
/// the clipboard, and the saved file carried a summary followed by that same
/// JSON, so the Drobo's serial, name and address, every drive serial, the
/// volume names and the event-log lines all appeared twice. The report form
/// asked testers to remove them by hand. Realistically they posted it
/// unedited, missed the second copy, or left diagnostics out altogether, and
/// the last of those leaves the maintainer with nothing to go on.
///
/// Every identifying value becomes a placeholder of the same shape (a string
/// stays a string, so the JSON keeps its structure), and the same real value
/// always gets the same placeholder within one report: two drives with one
/// serial read DRIVE-SERIAL-1 twice, which is itself worth seeing. Pure and
/// UI-free, so the test program runs it on any machine.
/// </summary>
internal static class DiagnosticsMasking
{
    /// <summary>Fields named here are left as they are wherever they appear:
    /// they describe hardware or state, never a person, and the generic scrub
    /// below could mistake a four-part version number for an address.</summary>
    private static readonly HashSet<string> KeysNeverScrubbed = new(StringComparer.Ordinal)
    {
        "model", "firmware", "firmware_rev", "state", "severity", "ts_iso",
        "taken_at_iso", "source", "overall_state", "partition_scheme",
    };

    /// <summary>Indented, and without escaping apostrophes or accented
    /// letters as \u0027 and the like: this text is read by people in an
    /// issue, never placed in a web page.</summary>
    private static readonly JsonSerializerOptions IndentedOutput = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    /// <summary>
    /// The JSON with every identifying value replaced, indented for reading.
    /// Throws JsonException when the text is not JSON at all; the caller then
    /// leaves the text out of the public copy rather than post what it could
    /// not inspect.
    ///
    /// <paramref name="rememberedDevice"/> is the Drobo this PC remembers.
    /// Its serial, name and address are known values even when the reply's
    /// device block is empty, which is exactly when the agent's error text
    /// quotes them: an identity mismatch leaves the device block blank and
    /// names the expected serial in the error.
    /// </summary>
    public static string MaskJson(string json, DroboSettings.RememberedDevice? rememberedDevice = null)
    {
        var root = JsonNode.Parse(json) ?? throw new JsonException("The reply was empty (JSON null).");
        var placeholders = new PlaceholderMap();
        if (root is JsonObject rootObject)
            MaskIdentifyingFields(rootObject, placeholders);
        AddRememberedDevice(rememberedDevice, placeholders);
        ScrubEveryString(root, placeholders, keyName: null);
        return root.ToJsonString(IndentedOutput);
    }

    /// <summary>
    /// Free text that is not a status reply, such as an error entry, with
    /// identifying values replaced: the remembered Drobo's serial, name and
    /// address, and every shape ScrubText knows. For the error dialog, whose
    /// text a tester is asked to paste into a public report.
    /// </summary>
    public static string MaskText(string text, DroboSettings.RememberedDevice? rememberedDevice = null)
    {
        var placeholders = new PlaceholderMap();
        AddRememberedDevice(rememberedDevice, placeholders);
        return ScrubText(text, placeholders);
    }

    /// <summary>The remembered Drobo's values, made known after the
    /// reply's own fields so they share a placeholder when they agree, and
    /// the tester's serial is SERIAL-1 when the reply's device block is
    /// empty. Empty values are skipped, as in MaskField.</summary>
    private static void AddRememberedDevice(DroboSettings.RememberedDevice? rememberedDevice,
                                            PlaceholderMap placeholders)
    {
        if (rememberedDevice is null) return;
        AddKnownValue(placeholders, "SERIAL", rememberedDevice.EsaId);
        AddKnownValue(placeholders, "NAME", rememberedDevice.Name);
        AddKnownValue(placeholders, "ADDRESS", rememberedDevice.Host);
    }

    private static void AddKnownValue(PlaceholderMap placeholders, string kind, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value)) placeholders.For(kind, value);
    }

    /// <summary>
    /// Pass one: the fields known to identify the unit or its owner, in a
    /// fixed order so the numbering is the same for the same reply (the
    /// Drobo's own serial is always SERIAL-1, bay order sets DRIVE-SERIAL-n).
    /// They go first so that pass two can find the same values inside free
    /// text and give them the same placeholder.
    /// </summary>
    private static void MaskIdentifyingFields(JsonObject root, PlaceholderMap placeholders)
    {
        if (root["device"] is JsonObject device)
        {
            MaskField(device, "serial", "SERIAL", placeholders);
            MaskField(device, "name", "NAME", placeholders);
            MaskField(device, "ip", "ADDRESS", placeholders);
        }
        MaskFieldInEach(root["drives"], "serial", "DRIVE-SERIAL", placeholders);
        MaskFieldInEach(root["volumes"], "name", "VOLUME", placeholders);
        MaskFieldInEach(root["shares"], "name", "SHARE", placeholders);
        MaskFieldInEach(root["event_log"], "message", "EVENT", placeholders);
        if (root["network_hint"] is JsonObject networkHint)
        {
            MaskField(networkHint, "target", "ADDRESS", placeholders);
            MaskEachString(networkHint["addresses"], "ADDRESS", placeholders);
            MaskEachString(networkHint["networks"], "NETWORK", placeholders);
        }
    }

    /// <summary>Replaces one string member with its placeholder. A missing,
    /// empty or non-string member is left alone: an empty serial identifies
    /// nobody, and reading "" as SERIAL-1 would claim there was one.</summary>
    private static void MaskField(JsonObject parent, string key, string kind, PlaceholderMap placeholders)
    {
        if (parent[key] is not JsonValue value || !value.TryGetValue(out string? text)) return;
        if (string.IsNullOrEmpty(text)) return;
        parent[key] = placeholders.For(kind, text);
    }

    /// <summary>MaskField on every object of an array, for the per-bay,
    /// per-volume and per-event lists.</summary>
    private static void MaskFieldInEach(JsonNode? list, string key, string kind, PlaceholderMap placeholders)
    {
        if (list is not JsonArray array) return;
        foreach (var item in array.OfType<JsonObject>())
            MaskField(item, key, kind, placeholders);
    }

    /// <summary>Replaces every non-empty string of an array of strings, for
    /// the PC's addresses and networks in the network hint.</summary>
    private static void MaskEachString(JsonNode? list, string kind, PlaceholderMap placeholders)
    {
        if (list is not JsonArray array) return;
        for (int index = 0; index < array.Count; index++)
        {
            if (array[index] is not JsonValue value || !value.TryGetValue(out string? text)) continue;
            if (string.IsNullOrEmpty(text)) continue;
            array[index] = placeholders.For(kind, text);
        }
    }

    /// <summary>
    /// Pass two: every other string in the reply, whatever its key, goes
    /// through ScrubText. This is what catches an address inside the agent's
    /// error text, the network hint's sentences or a field added to the reply
    /// after this was written; a list of fields alone would miss those.
    /// Strings are replaced where they stand and containers walked in place,
    /// so no node is ever moved to a new parent, which System.Text.Json
    /// refuses.
    /// </summary>
    private static void ScrubEveryString(JsonNode? node, PlaceholderMap placeholders, string? keyName)
    {
        if (node is JsonObject jsonObject)
        {
            ScrubMembers(jsonObject, placeholders);
            return;
        }
        if (node is not JsonArray jsonArray) return;
        for (int index = 0; index < jsonArray.Count; index++)
        {
            var replacement = ScrubbedStringOrNull(jsonArray[index], keyName, placeholders);
            if (replacement is not null) jsonArray[index] = replacement;
            else ScrubEveryString(jsonArray[index], placeholders, keyName);
        }
    }

    /// <summary>The object case of ScrubEveryString, kept apart so neither
    /// walk nests more than two deep. A member's own name is passed down,
    /// because KeysNeverScrubbed is decided by the key a string sits
    /// under.</summary>
    private static void ScrubMembers(JsonObject jsonObject, PlaceholderMap placeholders)
    {
        foreach (var memberName in jsonObject.Select(member => member.Key).ToList())
        {
            var replacement = ScrubbedStringOrNull(jsonObject[memberName], memberName, placeholders);
            if (replacement is not null) jsonObject[memberName] = replacement;
            else ScrubEveryString(jsonObject[memberName], placeholders, memberName);
        }
    }

    /// <summary>A new string node for a string, or null for anything else
    /// (the caller walks containers and keeps numbers as they are). An empty
    /// string, a placeholder, or a key that never identifies anyone comes
    /// back unchanged.</summary>
    private static JsonValue? ScrubbedStringOrNull(JsonNode? node, string? keyName, PlaceholderMap placeholders)
    {
        if (node is not JsonValue value || !value.TryGetValue(out string? text)) return null;
        bool keep = text.Length == 0 || placeholders.IsPlaceholder(text)
                    || (keyName is not null && KeysNeverScrubbed.Contains(keyName));
        return JsonValue.Create(keep ? text : ScrubText(text, placeholders));
    }

    // The shapes a value can have inside free text. Each is replaced with a
    // placeholder from the same map, so an address the hint quotes in its
    // sentence reads as the same ADDRESS-n as the field that holds it.
    private static readonly Regex MacAddress = new(
        @"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])",
        RegexOptions.CultureInvariant);
    // Not after "Version=": an exception naming an assembly writes
    // Version=1.2.0.0, which is a version, not an address.
    private static readonly Regex Ipv4Address = new(
        @"(?<![\d.])(?<!Version=)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:/\d{1,2})?(?![\d.])",
        RegexOptions.CultureInvariant);
    // Either eight full groups or a "::" somewhere. A clock time such as
    // 20:42:23 is three hex-looking groups too, which is why two colons
    // alone are not enough.
    private static readonly Regex Ipv6Address = new(
        @"(?<![\w:])(?=[0-9A-Fa-f:]*[0-9A-Fa-f])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
        + @"|(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{0,4}::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{0,4})"
        + @"(?:%[0-9A-Za-z]+)?(?:/\d{1,3})?(?![\w:])",
        RegexOptions.CultureInvariant);
    private static readonly Regex EmailAddress = new(
        @"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", RegexOptions.CultureInvariant);
    // \\server\share; the server and the share are both names somebody
    // chose. Backslashes only: two forward slashes are far more often a URL
    // (http://...), whose host the address and host-name shapes catch.
    private static readonly Regex UncPath = new(
        @"(?<!\\)\\\\(?<server>[^\\/\s""'<>|]+)(?:\\(?<share>[^\\/\s""'<>|]+))?",
        RegexOptions.CultureInvariant);
    // What the agent and the SDK say when an address answers with another
    // Drobo's serial (discovery.IdentityMismatchError): "it identifies as
    // esa_id='X' ('Name'), not the expected 'Y'". The reply's device block is
    // empty then, so neither serial is a known value; both are caught here
    // by where they stand. Quoted the way Python's repr quotes, either quote
    // mark, or bare as in a log line's "(esa_id=X)".
    private static readonly Regex SerialAfterEsaId = new(
        @"(?<label>\besa_id=)(?:(?<quote>['""])(?<serial>(?:(?!\k<quote>)[^\r\n])*)\k<quote>"
        + @"|(?<serial>[A-Za-z0-9._-]*))"
        + @"(?:(?<gap>\s*\()(?<nameQuote>['""])(?<name>(?:(?!\k<nameQuote>)[^\r\n])*)\k<nameQuote>\))?",
        RegexOptions.CultureInvariant);
    private static readonly Regex ExpectedSerial = new(
        @"(?<label>\bnot the expected )(?<quote>['""])(?<serial>(?:(?!\k<quote>)[^\r\n])+)\k<quote>",
        RegexOptions.CultureInvariant);
    // C:\Users\<name>\..., the Windows user name inside a profile path.
    // A local account's folder can hold a space ("First Last"), so where a
    // path separator ends the segment the whole segment is the name;
    // stopping at the space left the surname in the copy. With no
    // separator after it (a path cut short in a sentence) the name stops
    // at whitespace as before, so the rest of the sentence stays readable.
    private static readonly Regex UserProfilePath = new(
        @"(?i)(?<prefix>\b[A-Z]:[\\/](?:Users|Documents and Settings)[\\/])"
        + @"(?<user>[^\\/\r\n""':*?<>|]+?(?=[\\/])|[^\\/\s""':*?<>|]+)",
        RegexOptions.CultureInvariant);
    // A .local (mDNS), .lan or .localdomain host name, which is usually the
    // Drobo's or the PC's own name.
    private static readonly Regex LocalHostName = new(
        @"(?i)\b[A-Z0-9][A-Z0-9-]*\.(?:local|lan|localdomain)\b", RegexOptions.CultureInvariant);

    /// <summary>
    /// One piece of free text with identifying values replaced. Known values
    /// (from pass one) first, longest first, so a serial inside a longer
    /// string is caught whole; then the identity-mismatch serials, the
    /// expected one first so the tester's own Drobo has the lower number;
    /// then the shapes above. Addresses come before UNC paths, so
    /// \\192.0.2.10\Photos reads \\ADDRESS-1\SHARE-1 rather than calling
    /// the address a NAME. Kept separate from the JSON walk so a test can
    /// feed it a sentence directly.
    /// </summary>
    internal static string ScrubText(string text, PlaceholderMap placeholders)
    {
        var scrubbed = placeholders.ReplaceKnownValues(text);
        scrubbed = ExpectedSerial.Replace(scrubbed, match => match.Groups["label"].Value
            + QuotedPlaceholder(match.Groups["quote"].Value, "SERIAL", match.Groups["serial"].Value, placeholders));
        scrubbed = SerialAfterEsaId.Replace(scrubbed, match => DescribeEsaId(match, placeholders));
        scrubbed = UserProfilePath.Replace(scrubbed, match =>
            match.Groups["prefix"].Value + placeholders.For("USER", match.Groups["user"].Value));
        scrubbed = EmailAddress.Replace(scrubbed, match => placeholders.For("EMAIL", match.Value));
        scrubbed = MacAddress.Replace(scrubbed, match => placeholders.For("MAC", match.Value));
        scrubbed = Ipv6Address.Replace(scrubbed, match => placeholders.For("ADDRESS", match.Value));
        scrubbed = Ipv4Address.Replace(scrubbed, match => placeholders.For(
            match.Value.Contains('/') ? "NETWORK" : "ADDRESS", match.Value));
        scrubbed = UncPath.Replace(scrubbed, match => DescribeUncPath(match, placeholders));
        scrubbed = LocalHostName.Replace(scrubbed, match => placeholders.For("NAME", match.Value));
        return scrubbed;
    }

    /// <summary>esa_id=<quote>SERIAL-n<quote>, and the name in brackets
    /// after it as NAME-n when the message carries one.</summary>
    private static string DescribeEsaId(Match match, PlaceholderMap placeholders)
    {
        var masked = match.Groups["label"].Value + QuotedPlaceholder(
            match.Groups["quote"].Value, "SERIAL", match.Groups["serial"].Value, placeholders);
        if (!match.Groups["nameQuote"].Success) return masked;
        return masked + match.Groups["gap"].Value + QuotedPlaceholder(
            match.Groups["nameQuote"].Value, "NAME", match.Groups["name"].Value, placeholders) + ")";
    }

    /// <summary>The value between its quote marks, masked. An empty value
    /// and the "?" the SDK writes for a missing name identify nobody and
    /// stay as they are; For keeps a placeholder as it is.</summary>
    private static string QuotedPlaceholder(string quote, string kind, string value, PlaceholderMap placeholders)
    {
        var masked = value.Length == 0 || value == "?" ? value : placeholders.For(kind, value);
        return quote + masked + quote;
    }

    /// <summary>\\NAME-n\SHARE-n for a UNC path; a server that is already a
    /// placeholder (an address caught earlier) is kept as it is.</summary>
    private static string DescribeUncPath(Match match, PlaceholderMap placeholders)
    {
        var server = match.Groups["server"].Value;
        var maskedServer = placeholders.IsPlaceholder(server) ? server : placeholders.For("NAME", server);
        if (!match.Groups["share"].Success) return @"\\" + maskedServer;
        var share = match.Groups["share"].Value;
        var maskedShare = placeholders.IsPlaceholder(share) ? share : placeholders.For("SHARE", share);
        return @"\\" + maskedServer + @"\" + maskedShare;
    }

    /// <summary>
    /// Real value to placeholder, per kind, for one report. Case is ignored
    /// when matching, so "drobo5n" in an error and "Drobo5N" in the name
    /// field are one value, as they are to a person reading them.
    /// </summary>
    internal sealed class PlaceholderMap
    {
        /// <summary>A known value shorter than this is masked in its own
        /// field but not hunted for inside free text: a volume called "A"
        /// would otherwise turn every "a" in a sentence into VOLUME-1, and a
        /// value that short identifies nobody.</summary>
        private const int ShortestValueSearchedInText = 3;

        /// <summary>Known values that are masked in their own field but not
        /// hunted for in free text: a Drobo left with the name "Drobo"
        /// identifies nobody, and every sentence the agent writes says
        /// "the Drobo", which would read "the NAME-1".</summary>
        private static readonly HashSet<string> ValuesNotSearchedInText =
            new(StringComparer.OrdinalIgnoreCase) { "Drobo" };

        private readonly Dictionary<string, string> _placeholderByValue = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, int> _countByKind = new(StringComparer.Ordinal);
        private readonly HashSet<string> _placeholders = new(StringComparer.Ordinal);

        /// <summary>The known-values pattern, built once per set of values
        /// rather than once per string: a long event log would otherwise
        /// rebuild a pattern of every message for every string in the
        /// reply. Null when a value has been added since.</summary>
        private Regex? _knownValuesPattern;

        /// <summary>The placeholder for <paramref name="value"/>, made the
        /// first time the value is seen. A value seen under one kind keeps
        /// that kind's placeholder when it turns up as another.</summary>
        public string For(string kind, string value)
        {
            if (_placeholderByValue.TryGetValue(value, out var existing)) return existing;
            if (_placeholders.Contains(value)) return value;
            int number = _countByKind.TryGetValue(kind, out var count) ? count + 1 : 1;
            _countByKind[kind] = number;
            var placeholder = $"{kind}-{number}";
            _placeholderByValue[value] = placeholder;
            _placeholders.Add(placeholder);
            _knownValuesPattern = null;
            return placeholder;
        }

        /// <summary>True for a string this map produced, so a second pass
        /// never masks a placeholder again.</summary>
        public bool IsPlaceholder(string text) => _placeholders.Contains(text);

        /// <summary>Every known value of searchable length, replaced where it
        /// stands as a whole word, in one pass so a replacement is never
        /// itself searched again.</summary>
        public string ReplaceKnownValues(string text)
        {
            _knownValuesPattern ??= BuildKnownValuesPattern();
            return _knownValuesPattern is null
                ? text
                : _knownValuesPattern.Replace(text, match => PlaceholderForMatch(match.Value));
        }

        /// <summary>
        /// The placeholder for text the known-values pattern matched. The
        /// dictionary ignores case ordinally and the pattern by culture-
        /// invariant rules, and the two disagree on a few characters (the
        /// Kelvin sign matches "k" in one and not the other), so a direct
        /// lookup can miss. Then the values are compared the pattern's own
        /// way; until 2026-11-20 the miss threw, and the tester lost the
        /// whole report.
        /// </summary>
        private string PlaceholderForMatch(string matchedText)
        {
            if (_placeholderByValue.TryGetValue(matchedText, out var placeholder)) return placeholder;
            foreach (var (value, valuePlaceholder) in _placeholderByValue)
            {
                if (Regex.IsMatch(matchedText, "^" + Regex.Escape(value) + "$",
                                  RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
                    return valuePlaceholder;
            }
            return "MASKED";
        }

        /// <summary>One alternation of the searchable values, longest first
        /// so a serial inside a longer value is not matched on its own; null
        /// when there is nothing to search for. A value is whole when no
        /// letter or digit touches it: \w would count "_" as part of a word,
        /// so a serial in "TST5N0000001_backup" would slip through.</summary>
        private Regex? BuildKnownValuesPattern()
        {
            var searchable = _placeholderByValue.Keys
                .Where(value => value.Length >= ShortestValueSearchedInText
                                && !ValuesNotSearchedInText.Contains(value))
                .OrderByDescending(value => value.Length)
                .Select(Regex.Escape)
                .ToList();
            if (searchable.Count == 0) return null;
            return new Regex(@"(?<![\p{L}\p{N}])(?:" + string.Join("|", searchable) + @")(?![\p{L}\p{N}])",
                             RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        }
    }
}
