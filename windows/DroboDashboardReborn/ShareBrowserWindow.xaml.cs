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
    private readonly string _root;        // the share's UNC root, e.g. \\host\Photos
    private readonly string _shareName;
    private string _current;              // the folder being shown
    private readonly ObservableCollection<Entry> _entries = new();
    private int _generation;              // stale-read guard, same pattern as MainWindow

    /// <summary>One row in the listing.</summary>
    public sealed class Entry
    {
        public string Name { get; init; } = "";
        public string FullPath { get; init; } = "";
        public bool IsDirectory { get; init; }
        public long? SizeBytes { get; init; }
        public DateTime? Modified { get; init; }

        public string Glyph => IsDirectory ? "\u25B8" : "\u00B7";   // ▸ for folders, · for files
        public string SizeText => IsDirectory ? "" : HumanSize(SizeBytes);
        public string ModifiedText => Modified?.ToString("yyyy-MM-dd HH:mm") ?? "";

        private static string HumanSize(long? bytes)
        {
            if (bytes is null) return "";
            double b = bytes.Value;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int i = 0;
            while (b >= 1024 && i < units.Length - 1) { b /= 1024; i++; }
            return i == 0 ? $"{b:0} {units[i]}" : $"{b:0.0} {units[i]}";
        }
    }

    public ShareBrowserWindow(string shareName, string uncPath)
    {
        InitializeComponent();
        _shareName = shareName;
        _root = uncPath.TrimEnd('\\');
        _current = _root;
        Header.Text = "Files on " + shareName;
        Entries.ItemsSource = _entries;
        Loaded += async (_, _) => await NavigateTo(_root);
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    private async Task NavigateTo(string path)
    {
        // Never allow navigation above the share root. A UNC path can be
        // walked up to \\host, which lists OTHER shares, and that is not what
        // "browse this share" means.
        if (!path.StartsWith(_root, StringComparison.OrdinalIgnoreCase))
            path = _root;

        int gen = ++_generation;
        _current = path;
        PathText.Text = path;
        UpBtn.IsEnabled = !string.Equals(path, _root, StringComparison.OrdinalIgnoreCase);
        RenderCrumbs();
        ShowState("Reading\u2026");
        _entries.Clear();

        List<Entry> listing;
        string? error = null;
        try
        {
            listing = await Task.Run(() => ReadDirectory(path));
        }
        catch (UnauthorizedAccessException)
        {
            listing = new List<Entry>();
            error = "Windows would not let this account read that folder. "
                  + "The share may need a different login -- use Connect as\u2026 on the Shares tab.";
        }
        catch (DirectoryNotFoundException)
        {
            listing = new List<Entry>();
            error = "That folder is not there any more.";
        }
        catch (IOException ex)
        {
            listing = new List<Entry>();
            error = "Could not read the share right now: " + ex.Message;
        }

        if (gen != _generation) return;   // the user moved on while we were reading

        foreach (var e in listing) _entries.Add(e);

        if (error != null) ShowState(error);
        else if (listing.Count == 0) ShowState("This folder is empty.");
        else HideState();
    }

    /// <summary>Runs OFF the UI thread. Folders first, then files, each A-Z.</summary>
    private static List<Entry> ReadDirectory(string path)
    {
        var dir = new DirectoryInfo(path);
        var result = new List<Entry>();
        foreach (var d in dir.EnumerateDirectories().OrderBy(d => d.Name, StringComparer.OrdinalIgnoreCase))
        {
            result.Add(new Entry
            {
                Name = d.Name, FullPath = d.FullName, IsDirectory = true,
                Modified = SafeModified(d),
            });
        }
        foreach (var f in dir.EnumerateFiles().OrderBy(f => f.Name, StringComparer.OrdinalIgnoreCase))
        {
            result.Add(new Entry
            {
                Name = f.Name, FullPath = f.FullName, IsDirectory = false,
                SizeBytes = SafeLength(f), Modified = SafeModified(f),
            });
        }
        return result;
    }

    // A single unreadable entry must not sink the whole listing.
    private static DateTime? SafeModified(FileSystemInfo i) { try { return i.LastWriteTime; } catch { return null; } }
    private static long? SafeLength(FileInfo f) { try { return f.Length; } catch { return null; } }

    private void RenderCrumbs()
    {
        Crumbs.Children.Clear();
        // Root crumb is the share name; each deeper segment is one folder.
        var rel = _current.Length > _root.Length ? _current.Substring(_root.Length).Trim('\\') : "";
        var segments = rel.Length == 0 ? Array.Empty<string>() : rel.Split('\\');

        AddCrumb(_shareName, _root, isLast: segments.Length == 0);
        var acc = _root;
        for (int i = 0; i < segments.Length; i++)
        {
            acc = acc + "\\" + segments[i];
            Crumbs.Children.Add(new TextBlock
            {
                Text = " \u203A ",   // the > separator
                Foreground = new System.Windows.Media.SolidColorBrush(
                    (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString("#6B7683")),
                VerticalAlignment = VerticalAlignment.Center,
                FontFamily = new System.Windows.Media.FontFamily("Consolas"),
                FontSize = 12,
            });
            AddCrumb(segments[i], acc, isLast: i == segments.Length - 1);
        }
    }

    private void AddCrumb(string text, string target, bool isLast)
    {
        var b = new Button { Content = text, Style = (Style)FindResource("Crumb"), Tag = target };
        if (isLast) b.Foreground = System.Windows.Media.Brushes.White;
        b.Click += async (_, _) => await NavigateTo((string)b.Tag);
        Crumbs.Children.Add(b);
    }

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

    private async Task GoUp()
    {
        if (string.Equals(_current, _root, StringComparison.OrdinalIgnoreCase)) return;
        var parent = Path.GetDirectoryName(_current);
        await NavigateTo(string.IsNullOrEmpty(parent) ? _root : parent);
    }

    private void Explorer_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo(_current) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "Could not open Explorer here: " + ex.Message,
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
