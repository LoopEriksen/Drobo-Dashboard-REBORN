using System.Windows;
using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// One Drobo shown on the landing page's device-picker list -- found by the
/// agent's /api/discover (mDNS or "in use"), confirmed at a manually-added
/// address the agent was asked to check directly, or a manually-added address
/// that did not answer this pass (see DroboSettings.ManualDevice).
///
/// Host/Port are the Drobo's own address (its command port, not the agent's
/// HTTP port) -- shown for information only. This app never opens a socket
/// to the Drobo directly, so clicking a row doesn't dial this address; it
/// connects to the agent that's already configured (see
/// MainWindow.DeviceConnectButton_Click for why that's the right thing to do
/// with only one agent and one Drobo in the picture).
/// </summary>
public sealed class DiscoveredDeviceRow
{
    /// <summary>The Drobo's address as the agent reported it, or as the user
    /// typed it for a manual entry. Also what a manual entry is matched on.</summary>
    public string Host { get; set; } = "";

    /// <summary>The Drobo's TCP port (its command port, not the agent's), 0
    /// when unknown. Information only: see the class remarks.</summary>
    public int Port { get; set; }

    /// <summary>The device's own name, "Drobo" when it gave none, or the
    /// address for a manual entry that did not answer, so a row is never
    /// unlabelled.</summary>
    public string Name { get; set; } = "";

    /// <summary>The model as the agent reported it; blank when unknown.</summary>
    public string Model { get; set; } = "";

    /// <summary>The firmware version as the agent reported it; blank when
    /// unknown.</summary>
    public string Firmware { get; set; } = "";

    /// <summary>The Drobo's permanent hardware serial -- not a secret (the device
    /// hands it to anyone who connects), and what MainWindow remembers a device by
    /// so a later DHCP-assigned IP change doesn't break "reconnect automatically".</summary>
    public string EsaId { get; set; } = "";

    /// <summary>True for the Drobo the agent is already using.</summary>
    public bool IsCurrent { get; set; }

    /// <summary>Shows the "in use" marker for <see cref="IsCurrent"/>. A
    /// Visibility rather than a bool so the template binds it directly,
    /// without a converter.</summary>
    public Visibility InUseVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>"Drobo 5N  ·  firmware 4.3.1  ·  10.0.0.5" -- assembled once in
    /// MainWindow.RenderDevices so the card's DataTemplate stays a plain binding.</summary>
    public string Summary { get; set; } = "";

    /// <summary>"MANUALLY ADDED" or "NOT VERIFIED" -- blank hides the chip
    /// entirely, same convention as InUseVisibility. Colours are set by
    /// MainWindow.RenderDevices (the same pattern AlertRow already uses for
    /// severity: ChipBackground/SeverityBrush), rather than baking colour
    /// logic into this plain view-model.</summary>
    public string BadgeText { get; set; } = "";

    /// <summary>Shows the badge chip; set together with <see cref="BadgeText"/>.</summary>
    public Visibility BadgeVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>The badge chip's fill, chosen by MainWindow.RenderDevices.</summary>
    public Brush BadgeBackground { get; set; } = Brushes.Transparent;

    /// <summary>The badge chip's text colour, paired with
    /// <see cref="BadgeBackground"/>.</summary>
    public Brush BadgeForeground { get; set; } = Brushes.Transparent;
}
