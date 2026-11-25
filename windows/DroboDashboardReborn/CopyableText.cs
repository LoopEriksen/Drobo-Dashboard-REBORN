using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;

namespace DroboDashboardReborn;

/// <summary>
/// A message that can be selected with the mouse and copied, with Ctrl+C or
/// a right-click Copy, but looks like plain text.
///
/// Why it exists: every message in this app used to be a TextBlock, which
/// cannot be selected at all. A tester who hit "Token rejected" or a write
/// refusal could only retype it into a report, or take a screenshot, and a
/// retyped error is an error nobody can search for. Found on the
/// maintainer's own PC, 2026-11-25.
///
/// It is a read-only TextBox, because that is the one WPF text element that
/// already knows selection, Ctrl+C and Ctrl+A. Its look lives in App.xaml
/// (the implicit style for this type, and CopyableLabel), so it reads like
/// the TextBlock it replaced: no border, no background, the same colours.
/// It is never a tab stop, so the keyboard order through the buttons is
/// what it was. Labels and headings stay TextBlocks: nobody quotes them.
///
/// The right-click menu is its own rather than TextBox's stock one, whose
/// Cut and Paste would sit greyed out on text that can never change, and
/// whose Copy does nothing until something is selected. Here Copy takes the
/// selection, or the whole message when nothing is selected, which is
/// what someone quoting an error almost always wants.
///
/// A binding to Text must say Mode=OneWay: a TextBox binds two-way by
/// default, and a two-way binding to a property with no setter throws.
/// </summary>
public class CopyableText : TextBox
{
    public CopyableText()
    {
        IsReadOnly = true;
        IsTabStop = false;
        IsUndoEnabled = false;
        ContextMenu = BuildCopyMenu();
    }

    /// <summary>One menu per message, so each copies its own text.</summary>
    private ContextMenu BuildCopyMenu()
    {
        var copyItem = new MenuItem { Header = "Copy", InputGestureText = "Ctrl+C" };
        copyItem.Click += (_, _) => CopySelectionOrAll();
        var selectAllItem = new MenuItem { Header = "Select all", InputGestureText = "Ctrl+A" };
        selectAllItem.Click += (_, _) => SelectAll();

        var menu = new ContextMenu();
        menu.Items.Add(copyItem);
        menu.Items.Add(selectAllItem);
        return menu;
    }

    /// <summary>
    /// Copies what is selected, or the whole message when nothing is.
    /// Another program can hold the clipboard open for a moment, and
    /// SetText then throws; a failed copy of a message is not worth a
    /// second error on top of the first, so it is dropped and can be
    /// tried again.
    /// </summary>
    private void CopySelectionOrAll()
    {
        var textToCopy = SelectionLength > 0 ? SelectedText : Text;
        if (string.IsNullOrEmpty(textToCopy)) return;
        try
        {
            Clipboard.SetText(textToCopy);
        }
        catch (ExternalException)
        {
            // The clipboard was busy; nothing was copied.
        }
    }
}
