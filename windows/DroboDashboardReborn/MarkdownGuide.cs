using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace DroboDashboardReborn;

/// <summary>
/// Converts docs/accessing-your-files.md into a small styled HTML page so
/// "Open the guide" shows something readable instead of a wall of raw `#`
/// and `**` in Notepad (Windows' usual default handler for .md).
///
/// This is deliberately NOT a general Markdown engine -- it supports exactly
/// the handful of constructs the guide actually uses: headings, bold/italic,
/// inline code, fenced code blocks, a pipe table, ordered/unordered lists,
/// horizontal rules, links, and plain paragraphs. Anything outside that
/// subset would just fall through as a paragraph.
/// </summary>
internal static class MarkdownGuide
{
    private static readonly Regex HeadingRegex = new(@"^(#{1,6})\s+(.+?)\s*$", RegexOptions.Compiled);
    private static readonly Regex OrderedItemRegex = new(@"^\s*\d+\.\s+(.+)$", RegexOptions.Compiled);
    private static readonly Regex UnorderedItemRegex = new(@"^\s*[-*]\s+(.+)$", RegexOptions.Compiled);
    private static readonly Regex HorizontalRuleRegex = new(@"^-{3,}$", RegexOptions.Compiled);

    private static readonly Regex CodeSpanRegex = new(@"`([^`]+)`", RegexOptions.Compiled);
    private static readonly Regex BoldRegex = new(@"\*\*(.+?)\*\*", RegexOptions.Compiled);
    private static readonly Regex ItalicRegex = new(@"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", RegexOptions.Compiled);
    private static readonly Regex LinkRegex = new(@"\[([^\]]+)\]\(([^)]+)\)", RegexOptions.Compiled);

    /// <summary>Reads the markdown file at <paramref name="markdownPath"/>, converts
    /// it, and writes a standalone HTML file to the user's temp directory (never the
    /// repo, never next to the exe -- an installed copy can sit under Program Files,
    /// which is read-only for a normal user). Returns the path to open.</summary>
    public static string ConvertFileToHtml(string markdownPath)
    {
        var markdown = File.ReadAllText(markdownPath);
        var html = ConvertMarkdownToHtml(markdown, Path.GetFileNameWithoutExtension(markdownPath));

        var outputDirectory = Path.Combine(Path.GetTempPath(), "DroboDashboardReborn");
        Directory.CreateDirectory(outputDirectory);
        var outputPath = Path.Combine(outputDirectory, Path.GetFileNameWithoutExtension(markdownPath) + ".html");
        File.WriteAllText(outputPath, html);
        return outputPath;
    }

    /// <summary>
    /// Walks the guide one block at a time. Each TryConsume* helper looks at
    /// the line at <c>lineIndex</c> and, if it starts that kind of block,
    /// renders the whole block and moves <c>lineIndex</c> past it; otherwise
    /// it changes nothing and the next kind is tried.
    ///
    /// The order is the point: a code fence is tried first so its content is
    /// escaped verbatim and never inline-formatted (a line like
    /// "RequireSecuritySignature : True &lt;-- this one" must not become a
    /// tag), and a paragraph is the fallback that takes anything left.
    /// </summary>
    private static string ConvertMarkdownToHtml(string markdown, string fallbackTitle)
    {
        var lines = markdown.Replace("\r\n", "\n").Split('\n');
        var body = new StringBuilder();
        string? title = null;
        int lineIndex = 0;

        while (lineIndex < lines.Length)
        {
            if (string.IsNullOrWhiteSpace(lines[lineIndex])) { lineIndex++; continue; }

            if (TryConsumeFence(lines, ref lineIndex, body)) continue;
            if (TryConsumeHeading(lines, ref lineIndex, body, ref title)) continue;
            if (TryConsumeRule(lines, ref lineIndex, body)) continue;
            if (TryConsumeTable(lines, ref lineIndex, body)) continue;
            if (TryConsumeList(lines, ref lineIndex, body)) continue;
            ConsumeParagraph(lines, ref lineIndex, body);
        }

        return WrapPage(title ?? EscapeHtml(fallbackTitle), body.ToString());
    }

    /// <summary>A fenced code block. Its content is escaped verbatim, never run
    /// through inline formatting, so a line like "RequireSecuritySignature :
    /// True &lt;-- this one" can't be mistaken for the start of an HTML
    /// tag. An unclosed fence runs to the end of the file.</summary>
    private static bool TryConsumeFence(string[] lines, ref int lineIndex, StringBuilder body)
    {
        if (!lines[lineIndex].TrimStart().StartsWith("```")) return false;

        lineIndex++;
        var codeLines = new List<string>();
        while (lineIndex < lines.Length && !lines[lineIndex].TrimStart().StartsWith("```"))
        {
            codeLines.Add(lines[lineIndex]);
            lineIndex++;
        }
        if (lineIndex < lines.Length) lineIndex++; // skip the closing fence
        body.Append("<pre><code>").Append(EscapeHtml(string.Join("\n", codeLines))).Append("</code></pre>\n");
        return true;
    }

    /// <summary>A "#" to "######" heading. The first heading in the file also
    /// becomes the page's title, with its markdown markers stripped, because a
    /// browser tab cannot show bold or code.</summary>
    private static bool TryConsumeHeading(string[] lines, ref int lineIndex, StringBuilder body, ref string? title)
    {
        var heading = HeadingRegex.Match(lines[lineIndex]);
        if (!heading.Success) return false;

        int level = heading.Groups[1].Value.Length;
        var text = heading.Groups[2].Value;
        title ??= EscapeHtml(text.Replace("**", "").Replace("`", ""));
        body.Append($"<h{level}>").Append(FormatInline(text)).Append($"</h{level}>\n");
        lineIndex++;
        return true;
    }

    /// <summary>A "---" horizontal rule on a line of its own.</summary>
    private static bool TryConsumeRule(string[] lines, ref int lineIndex, StringBuilder body)
    {
        if (!HorizontalRuleRegex.IsMatch(lines[lineIndex].Trim())) return false;

        body.Append("<hr>\n");
        lineIndex++;
        return true;
    }

    /// <summary>A pipe table: a "| cell | cell |" row immediately followed by a
    /// "|---|---|" separator row. The separator is required, so a stray line
    /// that merely starts with "|" stays a paragraph.</summary>
    private static bool TryConsumeTable(string[] lines, ref int lineIndex, StringBuilder body)
    {
        var line = lines[lineIndex];
        if (!line.TrimStart().StartsWith("|")) return false;
        if (lineIndex + 1 >= lines.Length || !IsTableSeparatorRow(lines[lineIndex + 1])) return false;

        var headerCells = SplitRow(line);
        lineIndex += 2; // header + separator
        var rows = new List<string[]>();
        while (lineIndex < lines.Length && lines[lineIndex].TrimStart().StartsWith("|"))
        {
            rows.Add(SplitRow(lines[lineIndex]));
            lineIndex++;
        }

        AppendTable(body, headerCells, rows);
        return true;
    }

    private static void AppendTable(StringBuilder body, string[] headerCells, List<string[]> rows)
    {
        body.Append("<table>\n<thead><tr>");
        foreach (var cell in headerCells) body.Append("<th>").Append(FormatInline(cell)).Append("</th>");
        body.Append("</tr></thead>\n<tbody>\n");
        foreach (var row in rows)
        {
            body.Append("<tr>");
            foreach (var cell in row) body.Append("<td>").Append(FormatInline(cell)).Append("</td>");
            body.Append("</tr>\n");
        }
        body.Append("</tbody>\n</table>\n");
    }

    /// <summary>An ordered ("1.") or unordered ("-" or "*") list. The first
    /// item fixes the kind, and only that kind's marker starts a new item
    /// from then on (see <see cref="ReadListItems"/>).</summary>
    private static bool TryConsumeList(string[] lines, ref int lineIndex, StringBuilder body)
    {
        var line = lines[lineIndex];
        var orderedStart = OrderedItemRegex.Match(line);
        var unorderedStart = orderedStart.Success ? Match.Empty : UnorderedItemRegex.Match(line);
        if (!orderedStart.Success && !unorderedStart.Success) return false;

        bool ordered = orderedStart.Success;
        var marker = ordered ? OrderedItemRegex : UnorderedItemRegex;
        var firstItemText = (ordered ? orderedStart : unorderedStart).Groups[1].Value;
        lineIndex++;
        var items = ReadListItems(lines, ref lineIndex, marker, firstItemText);

        var tag = ordered ? "ol" : "ul";
        body.Append('<').Append(tag).Append(">\n");
        foreach (var item in items) body.Append("<li>").Append(FormatInline(item)).Append("</li>\n");
        body.Append("</").Append(tag).Append(">\n");
        return true;
    }

    /// <summary>Collects a list's items, starting with
    /// <paramref name="firstItemText"/> and the line after it, joining
    /// soft-wrapped continuation lines onto the item they belong to.</summary>
    private static List<string> ReadListItems(string[] lines, ref int lineIndex, Regex marker, string firstItemText)
    {
        var items = new List<string>();
        var current = new StringBuilder(firstItemText);

        while (lineIndex < lines.Length)
        {
            var listLine = lines[lineIndex];

            if (string.IsNullOrWhiteSpace(listLine))
            {
                // A blank line only ends the list if what follows isn't another
                // item of the same kind -- the guide has a couple of "loose"
                // lists with a blank line between items.
                int nextContentIndex = SkipBlankLines(lines, lineIndex + 1);
                if (nextContentIndex < lines.Length && marker.IsMatch(lines[nextContentIndex])) { lineIndex = nextContentIndex; continue; }
                break;
            }

            var itemMatch = marker.Match(listLine);
            if (itemMatch.Success)
            {
                items.Add(current.ToString());
                current = new StringBuilder(itemMatch.Groups[1].Value);
                lineIndex++;
                continue;
            }

            // A new block starting with no blank line ends the list. Doesn't
            // happen anywhere in today's guide (every block is blank-line
            // separated) but costs nothing to guard against.
            if (StartsHeadingFenceRuleOrTable(listLine))
                break;

            current.Append(' ').Append(listLine.Trim()); // soft-wrapped continuation line
            lineIndex++;
        }
        items.Add(current.ToString());
        return items;
    }

    /// <summary>The index of the first non-blank line at or after
    /// <paramref name="startIndex"/>, or <c>lines.Length</c> if there is
    /// none.</summary>
    private static int SkipBlankLines(string[] lines, int startIndex)
    {
        int index = startIndex;
        while (index < lines.Length && string.IsNullOrWhiteSpace(lines[index])) index++;
        return index;
    }

    /// <summary>Whether <paramref name="line"/> opens a heading, code fence,
    /// rule or table -- the blocks that end a list or a paragraph even
    /// without a blank line before them.</summary>
    private static bool StartsHeadingFenceRuleOrTable(string line) =>
        HeadingRegex.IsMatch(line) || line.TrimStart().StartsWith("```")
        || HorizontalRuleRegex.IsMatch(line.Trim()) || line.TrimStart().StartsWith("|");

    /// <summary>A paragraph: the fallback for any line no other block claimed.
    /// Consumes soft-wrapped lines until the next blank line or the start of
    /// another block, and joins them with spaces.</summary>
    private static void ConsumeParagraph(string[] lines, ref int lineIndex, StringBuilder body)
    {
        var paragraphLines = new List<string> { lines[lineIndex].Trim() };
        lineIndex++;
        while (lineIndex < lines.Length && !string.IsNullOrWhiteSpace(lines[lineIndex])
               && !StartsHeadingFenceRuleOrTable(lines[lineIndex])
               && !OrderedItemRegex.IsMatch(lines[lineIndex])
               && !UnorderedItemRegex.IsMatch(lines[lineIndex]))
        {
            paragraphLines.Add(lines[lineIndex].Trim());
            lineIndex++;
        }
        body.Append("<p>").Append(FormatInline(string.Join(" ", paragraphLines))).Append("</p>\n");
    }

    /// <summary>A table separator row is only "-", ":" and "|" characters, with at
    /// least one dash per cell -- e.g. "|---|---|" or "| :--- | ---: |".</summary>
    private static bool IsTableSeparatorRow(string line)
    {
        var trimmed = line.Trim();
        if (!trimmed.Contains('|')) return false;
        var cells = trimmed.Trim('|').Split('|');
        if (cells.Length == 0) return false;
        foreach (var cell in cells)
        {
            var cellText = cell.Trim();
            if (cellText.Length == 0 || !cellText.Contains('-')) return false;
            if (!HasOnlyDashesAndColons(cellText)) return false;
        }
        return true;
    }

    private static bool HasOnlyDashesAndColons(string text)
    {
        foreach (var character in text)
            if (character != '-' && character != ':') return false;
        return true;
    }

    /// <summary>The trimmed cells of a "| a | b |" row. The outer pipes are
    /// stripped first so they do not produce an empty first and last
    /// cell.</summary>
    private static string[] SplitRow(string line)
    {
        var trimmed = line.Trim().Trim('|');
        var cells = trimmed.Split('|');
        for (int cellIndex = 0; cellIndex < cells.Length; cellIndex++) cells[cellIndex] = cells[cellIndex].Trim();
        return cells;
    }

    /// <summary>Escapes HTML-significant characters, then applies inline markdown
    /// (links, code spans, bold, italic) on top of the already-escaped text -- so a
    /// path like "\\10.0.0.5\Share" or a code span containing "&lt;drobo&gt;" comes
    /// through as visible text instead of being parsed as a tag.</summary>
    private static string FormatInline(string raw)
    {
        var escaped = EscapeHtml(raw);
        escaped = LinkRegex.Replace(escaped, m =>
            $"<a href=\"{m.Groups[2].Value}\" target=\"_blank\" rel=\"noopener\">{m.Groups[1].Value}</a>");
        escaped = CodeSpanRegex.Replace(escaped, m => $"<code>{m.Groups[1].Value}</code>");
        escaped = BoldRegex.Replace(escaped, m => $"<strong>{m.Groups[1].Value}</strong>");
        escaped = ItalicRegex.Replace(escaped, m => $"<em>{m.Groups[1].Value}</em>");
        return escaped;
    }

    /// <summary>Escapes the four characters that could open a tag or end an
    /// attribute. It runs before any markdown is applied, so the only tags in
    /// the output are the ones this converter writes.</summary>
    private static string EscapeHtml(string text) => text
        .Replace("&", "&amp;")
        .Replace("<", "&lt;")
        .Replace(">", "&gt;")
        .Replace("\"", "&quot;");

    /// <summary>Styling matches the app itself -- same dark background and greys/blues
    /// as MainWindow.xaml's Window.Resources -- so the guide reads as part of the
    /// product rather than a document that escaped it.</summary>
    private static string WrapPage(string title, string bodyHtml) => $@"<!doctype html>
<html lang=""en"">
<head>
<meta charset=""utf-8"">
<meta name=""viewport"" content=""width=device-width, initial-scale=1"">
<title>{title}</title>
<style>
  html {{ color-scheme: dark; }}
  body {{
    background: #0D1116;
    color: #E6EBF0;
    font-family: ""Segoe UI"", system-ui, -apple-system, ""Helvetica Neue"", Arial, sans-serif;
    font-size: 16px;
    line-height: 1.6;
    margin: 0;
    padding: 40px 20px 80px;
  }}
  .doc {{ max-width: 70ch; margin: 0 auto; }}
  h1, h2, h3, h4, h5, h6 {{ color: #E6EBF0; font-weight: 600; line-height: 1.3; }}
  h1 {{ font-size: 1.6em; border-bottom: 1px solid #262E37; padding-bottom: 0.4em; }}
  h2 {{ font-size: 1.3em; margin-top: 1.8em; }}
  h3 {{ font-size: 1.1em; margin-top: 1.5em; color: #8FB4DC; }}
  p, li {{ color: #C6CDD6; }}
  a {{ color: #5AA6E0; }}
  a:hover {{ text-decoration: none; }}
  strong {{ color: #E6EBF0; }}
  em {{ color: #C6CDD6; }}
  code {{
    font-family: Consolas, ""Cascadia Mono"", ui-monospace, monospace;
    background: #161B21;
    border: 1px solid #262E37;
    border-radius: 3px;
    padding: 0.1em 0.4em;
    font-size: 0.9em;
    color: #E9A83C;
  }}
  pre {{
    background: #161B21;
    border: 1px solid #262E37;
    border-radius: 4px;
    padding: 12px 14px;
    overflow-x: auto;
  }}
  pre code {{ background: none; border: none; padding: 0; color: #C6CDD6; }}
  hr {{ border: none; border-top: 1px solid #262E37; margin: 2em 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.95em; }}
  th, td {{ border: 1px solid #262E37; padding: 6px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #161B21; color: #8B93A1; font-family: Consolas, monospace; font-size: 0.85em; text-transform: uppercase; }}
  ul, ol {{ padding-left: 1.4em; }}
  li {{ margin-bottom: 0.3em; }}
</style>
</head>
<body>
<div class=""doc"">
{bodyHtml}</div>
</body>
</html>
";
}
