using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace DroboDashboardReborn;

/// <summary>
/// Browse a share's files inside the app.
///
/// This reverses a 2026-07-28 decision ("File browser: not building one --
/// Explorer is better"). The maintainer asked for it on 2026-08-17: Open should
/// show the files right here, and Connect-as should be the mount flow. So
/// this window is deliberately modest -- it is a VIEWER, not a file manager.
/// It lists, it navigates, it hands a file to its usual program, and it
/// offers a one-click escape to Explorer for anything beyond that. It never
/// copies, moves, renames or deletes; there is no code path here that can
/// change a byte on the share.
///
/// It reads the share through Windows' own SMB client (ordinary
/// System.IO over a UNC path), which means it needs the same thing Explorer
/// needs: a Windows session to the share. MainWindow gets that session
/// first -- prompting for the Drobo login if necessary -- and only then
/// opens this window. A share that is not reachable shows its reason in
/// the list area rather than an exception.
///
/// Every directory read happens off the UI thread. An SMB share on a
/// sleepy NAS can take seconds to answer, and the app must not freeze
/// while it does.
/// </summary>
public partial class ShareBrowserWindow : Window
{
    private readonly string _shareRootUnc;   // the share's UNC root, e.g. \\host\Photos
    private readonly string _shareName;
    private string _currentPath;             // the folder being shown
    private readonly ObservableCollection<Entry> _entries = new();
    private int _generation;                 // stale-read guard, same pattern as MainWindow

    /// <summary>One row in the listing.</summary>
    public sealed class Entry
    {
        public string Name { get; init; } = "";
        public string FullPath { get; init; } = "";
        public bool IsDirectory { get; init; }
        public long? SizeBytes { get; init; }
        public DateTime? ModifiedAt { get; init; }

        public string Glyph => IsDirectory ? "\u25B8" : "\u00B7";   // ▸ for folders, · for files
        public string SizeText => IsDirectory ? "" : FormatSize(SizeBytes);
        public string ModifiedText => ModifiedAt?.ToString("yyyy-MM-dd HH:mm") ?? "";

        /// <summary>A file's size for the listing, blank when it could not be
        /// read. It steps by 1024, while JsonHelpers.FormatBytes (the Status
        /// tab) steps by 1000, so the two can show different figures for the
        /// same number of bytes.</summary>
        private static string FormatSize(long? sizeBytes)
        {
            if (sizeBytes is null) return "";
            double scaledSize = sizeBytes.Value;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int unitIndex = 0;
            while (scaledSize >= 1024 && unitIndex < units.Length - 1) { scaledSize /= 1024; unitIndex++; }
            return unitIndex == 0 ? $"{scaledSize:0} {units[unitIndex]}" : $"{scaledSize:0.0} {units[unitIndex]}";
        }
    }

    /// <summary>Opens on the share's root. A trailing backslash is trimmed
    /// from <paramref name="uncPath"/> because the root guard in NavigateTo
    /// and GoUp both compare paths against it as text, and Windows' own
    /// paths for the folders inside never end in one.</summary>
    public ShareBrowserWindow(string shareName, string uncPath)
    {
        InitializeComponent();
        _shareName = shareName;
        _shareRootUnc = uncPath.TrimEnd('\\');
        _currentPath = _shareRootUnc;
        Header.Text = "Files on " + shareName;
        Entries.ItemsSource = _entries;
        Loaded += async (_, _) => await NavigateTo(_shareRootUnc);
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    /// <summary>
    /// Shows the folder at <paramref name="path"/>. The read runs off the UI
    /// thread and can take seconds on a sleeping NAS, so by the time it
    /// returns the user may have clicked somewhere else: each call takes a
    /// new generation number and a reply for an older one is dropped, so a
    /// slow folder never overwrites the one now on screen.
    /// </summary>
    private async Task NavigateTo(string path)
    {
        // Never allow navigation above the share root. A UNC path can be
        // walked up to \\host, which lists OTHER shares, and that is not what
        // "browse this share" means.
        if (!path.StartsWith(_shareRootUnc, StringComparison.OrdinalIgnoreCase))
            path = _shareRootUnc;

        int generation = ++_generation;
        _currentPath = path;
        PathText.Text = path;
        UpBtn.IsEnabled = !string.Equals(path, _shareRootUnc, StringComparison.OrdinalIgnoreCase);
        RenderCrumbs();
        ShowState("Reading\u2026");
        _entries.Clear();

        var (listing, error) = await ReadListingOrError(path);

        if (generation != _generation) return;   // the user moved on while we were reading

        foreach (var entry in listing) _entries.Add(entry);

        if (error != null) ShowState(error);
        else if (listing.Count == 0) ShowState("This folder is empty.");
        else HideState();
    }

    /// <summary>Reads the folder off the UI thread and turns the three
    /// failures a share commonly gives into a sentence for the list area,
    /// with an empty listing, instead of an exception.</summary>
    private static async Task<(List<Entry> listing, string? error)> ReadListingOrError(string path)
    {
        try
        {
            return (await Task.Run(() => ReadDirectory(path)), null);
        }
        catch (UnauthorizedAccessException)
        {
            return (new List<Entry>(),
                "Windows would not let this account read that folder. "
                + "The share may need a different login -- use Connect as\u2026 on the Shares tab.");
        }
        catch (DirectoryNotFoundException)
        {
            return (new List<Entry>(), "That folder is not there any more.");
        }
        catch (IOException ex)
        {
            return (new List<Entry>(), "Could not read the share right now: " + ex.Message);
        }
    }

    /// <summary>Runs OFF the UI thread. Folders first, then files, each A-Z.</summary>
    private static List<Entry> ReadDirectory(string path)
    {
        var directory = new DirectoryInfo(path);
        var result = new List<Entry>();
        foreach (var subdirectory in directory.EnumerateDirectories().OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase))
        {
            result.Add(new Entry
            {
                Name = subdirectory.Name, FullPath = subdirectory.FullName, IsDirectory = true,
                ModifiedAt = SafeModified(subdirectory),
            });
        }
        foreach (var file in directory.EnumerateFiles().OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase))
        {
            result.Add(new Entry
            {
                Name = file.Name, FullPath = file.FullName, IsDirectory = false,
                SizeBytes = SafeLength(file), ModifiedAt = SafeModified(file),
            });
        }
        return result;
    }

    // A single unreadable entry must not sink the whole listing.
    private static DateTime? SafeModified(FileSystemInfo info) { try { return info.LastWriteTime; } catch { return null; } }
    private static long? SafeLength(FileInfo file) { try { return file.Length; } catch { return null; } }

    /// <summary>Rebuilds the breadcrumb row for the current folder: the share
    /// name, then one button per folder below it. Built from the path
    /// relative to the root, so a crumb can never point above the
    /// share.</summary>
    private void RenderCrumbs()
    {
        Crumbs.Children.Clear();
        // Root crumb is the share name; each deeper segment is one folder.
        var relativePath = _currentPath.Length > _shareRootUnc.Length ? _currentPath.Substring(_shareRootUnc.Length).Trim('\\') : "";
        var segments = relativePath.Length == 0 ? Array.Empty<string>() : relativePath.Split('\\');

        AddCrumb(_shareName, _shareRootUnc, isLast: segments.Length == 0);
        var crumbPath = _shareRootUnc;
        for (int segmentIndex = 0; segmentIndex < segments.Length; segmentIndex++)
        {
            crumbPath = crumbPath + "\\" + segments[segmentIndex];
            Crumbs.Children.Add(new TextBlock
            {
                Text = " \u203A ",   // the > separator
                Foreground = new System.Windows.Media.SolidColorBrush(
                    (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString("#6B7683")),
                VerticalAlignment = VerticalAlignment.Center,
                FontFamily = new System.Windows.Media.FontFamily("Consolas"),
                FontSize = 12,
            });
            AddCrumb(segments[segmentIndex], crumbPath, isLast: segmentIndex == segments.Length - 1);
        }
    }

    /// <summary>One clickable crumb that navigates to
    /// <paramref name="target"/>. The last one is drawn white, to mark the
    /// folder being shown.</summary>
    private void AddCrumb(string text, string target, bool isLast)
    {
        var crumbButton = new Button { Content = text, Style = (Style)FindResource("Crumb"), Tag = target };
        if (isLast) crumbButton.Foreground = System.Windows.Media.Brushes.White;
        crumbButton.Click += async (_, _) => await NavigateTo((string)crumbButton.Tag);
        Crumbs.Children.Add(crumbButton);
    }

    /// <summary>Shows a sentence in the list area (reading, empty, or why the
    /// folder could not be read) so the list is never just blank.</summary>
    private void ShowState(string text) { StateText.Text = text; StateText.Visibility = Visibility.Visible; }
    private void HideState() => StateText.Visibility = Visibility.Collapsed;

    // ------------------------------------------------------------------
    // Interaction
    // ------------------------------------------------------------------

    private async void Entries_MouseDoubleClick(object sender, MouseButtonEventArgs e) => await ActivateSelected();

    private async void Entries_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) { e.Handled = true; await ActivateSelected(); }
        else if (e.Key == Key.Back) { e.Handled = true; await GoUp(); }
    }

    /// <summary>Enter or a double-click: a folder opens in this window, a
    /// file is handed to the program Windows has for it.</summary>
    private async Task ActivateSelected()
    {
        if (Entries.SelectedItem is not Entry entry) return;
        if (entry.IsDirectory)
        {
            await NavigateTo(entry.FullPath);
            return;
        }
        // A file opens with whatever Windows has registered for it -- the same
        // as double-clicking it in Explorer. Read-only from our side: we hand
        // the path over and do nothing else with it.
        try
        {
            Process.Start(new ProcessStartInfo(entry.FullPath) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "Windows could not open that file: " + ex.Message,
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void Up_Click(object sender, RoutedEventArgs e) => await GoUp();

    /// <summary>Up one folder, stopping at the share's root: above it is the
    /// server's list of shares, which this window is not for.</summary>
    private async Task GoUp()
    {
        if (string.Equals(_currentPath, _shareRootUnc, StringComparison.OrdinalIgnoreCase)) return;
        var parent = Path.GetDirectoryName(_currentPath);
        await NavigateTo(string.IsNullOrEmpty(parent) ? _shareRootUnc : parent);
    }

    /// <summary>The escape hatch for anything this viewer deliberately does
    /// not do (copy, move, rename, delete): the same folder in
    /// Explorer.</summary>
    private void Explorer_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo(_currentPath) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "Could not open Explorer here: " + ex.Message,
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
