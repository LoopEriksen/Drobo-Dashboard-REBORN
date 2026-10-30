using System.Windows;
using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// One drive-bay row bound into the compact ItemsControl on the Status tab --
/// one dense line per bay rather than a tall card, so all five bays (the 5N
/// has exactly five, never six -- see esatm._trim_phantom_slots) fit without
/// scrolling at the smaller window size.
/// </summary>
public sealed class DriveRow
{
    /// <summary>"Bay 3" -- the row's first column, so an empty bay still
    /// says which slot it is.</summary>
    public string BayLabel { get; set; } = "";

    /// <summary>"ACME DISK-3000A  ·  serial" -- model with internal whitespace
    /// runs collapsed (the agent's model strings have padding baked in) and
    /// serial appended, built once in MainWindow.RenderStatus so the row stays a
    /// plain binding.</summary>
    public string ModelSerial { get; set; } = "";

    /// <summary>The drive's own firmware revision; blank for an empty bay.</summary>
    public string Firmware { get; set; } = "";

    /// <summary>The drive's capacity as display text (e.g. "4.00 TB"), not a
    /// byte count. Blank for an empty bay. The XAML binds to this name.</summary>
    public string Size { get; set; } = "";

    /// <summary>The drive's reported state, with its temperature when known
    /// ("ok  38.0°C"), or "empty".</summary>
    public string StateText { get; set; } = "";

    /// <summary>The colour of <see cref="StateText"/>, from the drive's own
    /// reported state.</summary>
    public Brush StateBrush { get; set; } = Brushes.Gray;

    /// <summary>The coloured bar at the row's edge: the same colour as
    /// <see cref="StateBrush"/>, so a failed bay stands out at a glance, and
    /// dark grey for an empty one. A Brush despite the bare name; the XAML
    /// binds to this name.</summary>
    public Brush Stripe { get; set; } = Brushes.Gray;

    /// <summary>"3 errors" and/or "life 42%" -- only populated when there's
    /// actually something to flag (a healthy drive shows neither; see the
    /// "don't print 0 errors on every row" rule). Empty means nothing to show.</summary>
    public string Flags { get; set; } = "";

    /// <summary>The colour of <see cref="Flags"/>: amber, because errors or
    /// reduced SSD life are worth a look, not a failure.</summary>
    public Brush FlagsBrush { get; set; } = Brushes.Gray;

    /// <summary>Collapsed when there is nothing to flag, so a healthy row
    /// carries no empty gap.</summary>
    public Visibility FlagsVisibility { get; set; } = Visibility.Collapsed;
}
