using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

namespace ArcGISAgent;

// Markdown -> WPF FlowDocument renderer for the Agent panel. Covers headings,
// bold/italic, inline code, fenced code blocks, ordered + unordered (nested)
// lists, blockquotes, horizontal rules, links and tables. Anything unsupported
// degrades to readable plain text rather than raw Markdown syntax.
internal static class MessageRenderer
{
    static readonly Regex Fence = new(@"^\s*(```|~~~)\s*([A-Za-z0-9_+#.-]*)\s*$", RegexOptions.Compiled);
    static readonly Regex Heading = new(@"^(#{1,6})\s+(.*?)\s*#*$", RegexOptions.Compiled);
    static readonly Regex Bullet = new(@"^(\s*)[-*+]\s+(.*)$", RegexOptions.Compiled);
    static readonly Regex Numbered = new(@"^(\s*)(\d+)[.)]\s+(.*)$", RegexOptions.Compiled);
    static readonly Regex Quote = new(@"^\s*>\s?(.*)$", RegexOptions.Compiled);
    static readonly Regex Rule = new(@"^\s{0,3}([-*_])(\s*\1){2,}\s*$", RegexOptions.Compiled);
    static readonly Regex TableSep = new(@"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", RegexOptions.Compiled);
    static readonly Regex Inline = new(@"(\*\*.+?\*\*|__.+?__|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*\s][^*]*?\*|(?<!\w)_[^_\s][^_]*?_(?!\w))", RegexOptions.Compiled);
    static readonly Regex Link = new(@"^\[([^\]]+)\]\(([^)]+)\)$", RegexOptions.Compiled);

    public static RichTextBox Create(string markdown, FrameworkElement theme)
    {
        var doc = new FlowDocument { PagePadding = new Thickness(0), FontSize = 13, ColumnWidth = double.MaxValue };
        var lines = (markdown ?? "").Replace("\r", "").Split('\n');
        int i = 0;
        while (i < lines.Length)
        {
            string line = lines[i];

            var fence = Fence.Match(line);
            if (fence.Success)
            {
                string marker = fence.Groups[1].Value, lang = fence.Groups[2].Value;
                var code = new List<string>(); i++;
                while (i < lines.Length && !lines[i].TrimStart().StartsWith(marker)) { code.Add(lines[i]); i++; }
                if (i < lines.Length) i++; // consume closing fence
                AddCode(doc, string.Join("\n", code), lang, theme);
                continue;
            }

            if (line.Trim().Length > 0 && Rule.Match(line).Success) { AddRule(doc, theme); i++; continue; }

            var heading = Heading.Match(line);
            if (heading.Success) { AddHeading(doc, heading.Groups[2].Value.Trim(), heading.Groups[1].Value.Length, theme); i++; continue; }

            if (line.Contains('|') && i + 1 < lines.Length && TableSep.Match(lines[i + 1]).Success && lines[i + 1].Contains('-'))
            {
                var rows = new List<string[]> { SplitRow(line) }; i += 2;
                while (i < lines.Length && lines[i].Contains('|') && lines[i].Trim().Length > 0) { rows.Add(SplitRow(lines[i])); i++; }
                AddTable(doc, rows, theme);
                continue;
            }

            var quote = Quote.Match(line);
            if (quote.Success)
            {
                var block = new List<string> { quote.Groups[1].Value }; i++;
                while (i < lines.Length && Quote.Match(lines[i]).Success) { block.Add(Quote.Match(lines[i]).Groups[1].Value); i++; }
                AddQuote(doc, string.Join(" ", block).Trim(), theme);
                continue;
            }

            if (Bullet.Match(line).Success || Numbered.Match(line).Success) { i = AddList(doc, lines, i, theme); continue; }

            if (string.IsNullOrWhiteSpace(line)) { i++; continue; }

            var para = new List<string> { line.Trim() }; i++;
            while (i < lines.Length && lines[i].Trim().Length > 0 && !IsBlockStart(lines, i)) { para.Add(lines[i].Trim()); i++; }
            AddParagraph(doc, string.Join(" ", para), theme);
        }

        var box = new RichTextBox { Document = doc, IsReadOnly = true, IsDocumentEnabled = false, BorderThickness = new Thickness(0), Padding = new Thickness(0), Background = Brushes.Transparent, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        box.SetResourceReference(Control.ForegroundProperty, "Ink");
        return box;
    }

    static bool IsBlockStart(string[] lines, int i)
    {
        string line = lines[i];
        if (Fence.Match(line).Success || Heading.Match(line).Success || Rule.Match(line).Success && line.Trim().Length > 0) return true;
        if (Bullet.Match(line).Success || Numbered.Match(line).Success || Quote.Match(line).Success) return true;
        return line.Contains('|') && i + 1 < lines.Length && TableSep.Match(lines[i + 1]).Success && lines[i + 1].Contains('-');
    }

    static string[] SplitRow(string line)
    {
        string trimmed = line.Trim();
        if (trimmed.StartsWith("|")) trimmed = trimmed[1..];
        if (trimmed.EndsWith("|")) trimmed = trimmed[..^1];
        return trimmed.Split('|').Select(c => c.Trim()).ToArray();
    }

    static void AddHeading(FlowDocument doc, string text, int level, FrameworkElement theme)
    {
        var p = new Paragraph { Margin = new Thickness(0, level <= 2 ? 10 : 7, 0, level <= 2 ? 6 : 5), FontWeight = level <= 3 ? FontWeights.Bold : FontWeights.SemiBold };
        p.FontSize = level switch { 1 => 20, 2 => 17, 3 => 15, 4 => 13.5, _ => 13 };
        if (level <= 2)
        {
            p.SetResourceReference(Paragraph.ForegroundProperty, "Accent");
            p.BorderBrush = (Brush)theme.FindResource("Line");
            p.BorderThickness = new Thickness(0, 0, 0, 1);
            p.Padding = new Thickness(0, 0, 0, 3);
        }
        AddInline(p.Inlines, text, theme);
        doc.Blocks.Add(p);
    }

    static void AddParagraph(FlowDocument doc, string text, FrameworkElement theme)
    {
        var p = new Paragraph { Margin = new Thickness(0, 0, 0, 9), LineHeight = 21 };
        AddInline(p.Inlines, text, theme);
        doc.Blocks.Add(p);
    }

    static void AddQuote(FlowDocument doc, string text, FrameworkElement theme)
    {
        var p = new Paragraph { Margin = new Thickness(0, 0, 0, 9), Padding = new Thickness(9, 2, 0, 2), LineHeight = 20, BorderThickness = new Thickness(2, 0, 0, 0), FontSize = 12.5 };
        p.SetResourceReference(Paragraph.ForegroundProperty, "Muted");
        p.BorderBrush = (Brush)theme.FindResource("Line");
        AddInline(p.Inlines, text, theme);
        doc.Blocks.Add(p);
    }

    static void AddRule(FlowDocument doc, FrameworkElement theme)
    {
        doc.Blocks.Add(new Paragraph { Margin = new Thickness(0, 4, 0, 10), BorderBrush = (Brush)theme.FindResource("Line"), BorderThickness = new Thickness(0, 0, 0, 1) });
    }

    static int AddList(FlowDocument doc, string[] lines, int start, FrameworkElement theme)
    {
        int i = start;
        while (i < lines.Length)
        {
            var bullet = Bullet.Match(lines[i]);
            var numbered = Numbered.Match(lines[i]);
            if (!bullet.Success && !numbered.Success) break;
            string marker; string text; int depth;
            if (bullet.Success) { depth = bullet.Groups[1].Value.Replace("\t", "  ").Length / 2; marker = "•"; text = bullet.Groups[2].Value; }
            else { depth = numbered.Groups[1].Value.Replace("\t", "  ").Length / 2; marker = numbered.Groups[2].Value + "."; text = numbered.Groups[3].Value; }

            var p = new Paragraph { Margin = new Thickness(18 * depth, 0, 0, 4), LineHeight = 20 };
            var mark = new Run(marker + "  ") { FontWeight = FontWeights.Bold };
            mark.SetResourceReference(Run.ForegroundProperty, "Accent");
            p.Inlines.Add(mark);
            AddInline(p.Inlines, text, theme);
            doc.Blocks.Add(p);
            i++;
            // Fold an indented continuation line into the current item.
            while (i < lines.Length && lines[i].Trim().Length > 0 && !IsBlockStart(lines, i)
                   && (lines[i].StartsWith("  ") || lines[i].StartsWith("\t")))
            {
                p.Inlines.Add(new Run("  " + lines[i].Trim()));
                i++;
            }
        }
        return i;
    }

    static void AddTable(FlowDocument doc, List<string[]> rows, FrameworkElement theme)
    {
        int cols = rows.Max(r => r.Length);
        if (cols < 1) return;
        var line = (Brush)theme.FindResource("Line");

        // One shared Grid keeps columns aligned across rows. Auto sizing avoids the
        // infinite-width collapse that star columns hit inside a FlowDocument block.
        var grid = new Grid { Margin = new Thickness(0, 2, 0, 12), HorizontalAlignment = HorizontalAlignment.Left };
        for (int c = 0; c < cols; c++) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        for (int r = 0; r < rows.Count; r++) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        for (int r = 0; r < rows.Count; r++)
        {
            bool header = r == 0;
            for (int c = 0; c < cols; c++)
            {
                string raw = c < rows[r].Length ? rows[r][c] : "";
                var cell = new TextBlock { TextWrapping = TextWrapping.NoWrap, Margin = new Thickness(8, 4, 8, 5), FontWeight = header ? FontWeights.Bold : FontWeights.Normal, VerticalAlignment = VerticalAlignment.Top };
                cell.SetResourceReference(TextBlock.ForegroundProperty, header ? "Accent" : "Ink");
                AddInline(cell.Inlines, raw, theme);
                var border = new Border
                {
                    Child = cell,
                    BorderBrush = line,
                    BorderThickness = new Thickness(c == 0 ? 1 : 0, header ? 1 : 0, c == cols - 1 ? 1 : 0, r == rows.Count - 1 ? 1 : 0)
                };
                Grid.SetRow(border, r);
                Grid.SetColumn(border, c);
                grid.Children.Add(border);
            }
        }
        doc.Blocks.Add(new BlockUIContainer(grid) { Margin = new Thickness(0, 2, 0, 4) });
    }

    static void AddCode(FlowDocument doc, string text, string lang, FrameworkElement theme)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 4, 0, 10) };
        if (!string.IsNullOrEmpty(lang))
        {
            var caption = new TextBlock { Text = lang, FontSize = 11, FontFamily = new FontFamily("Consolas"), Padding = new Thickness(10, 5, 10, 3), FontWeight = FontWeights.SemiBold };
            caption.SetResourceReference(TextBlock.ForegroundProperty, "Muted");
            caption.SetResourceReference(TextBlock.BackgroundProperty, "Page");
            panel.Children.Add(caption);
        }
        var code = new TextBox { Text = text, IsReadOnly = true, FontFamily = new FontFamily("Consolas"), FontSize = 12, TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Padding = new Thickness(10, string.IsNullOrEmpty(lang) ? 10 : 6, 10, 10) };
        code.SetResourceReference(Control.BackgroundProperty, "Page");
        code.SetResourceReference(Control.ForegroundProperty, "Ink");
        panel.Children.Add(code);
        doc.Blocks.Add(new BlockUIContainer(panel));
    }

    static void AddInline(InlineCollection target, string text, FrameworkElement theme)
    {
        int offset = 0;
        foreach (Match m in Inline.Matches(text))
        {
            if (m.Index > offset) target.Add(new Run(text[offset..m.Index]));
            target.Add(BuildInline(m.Value, theme));
            offset = m.Index + m.Length;
        }
        if (offset < text.Length) target.Add(new Run(text[offset..]));
    }

    static Inline BuildInline(string token, FrameworkElement theme)
    {
        if (token.StartsWith("`") && token.EndsWith("`") && token.Length > 2)
        {
            var code = new Run(token[1..^1]) { FontFamily = new FontFamily("Consolas"), Background = (Brush)theme.FindResource("Page"), Foreground = (Brush)theme.FindResource("Ink") };
            return code;
        }
        if ((token.StartsWith("**") && token.EndsWith("**") || token.StartsWith("__") && token.EndsWith("__")) && token.Length > 4)
            return new Bold(new Run(token[2..^2]));
        var link = Link.Match(token);
        if (link.Success)
        {
            var span = new Span();
            var text = new Run(link.Groups[1].Value) { FontWeight = FontWeights.SemiBold };
            text.SetResourceReference(Run.ForegroundProperty, "Accent");
            span.Inlines.Add(text);
            var url = new Run(" " + link.Groups[2].Value) { FontSize = 11 };
            url.SetResourceReference(Run.ForegroundProperty, "Muted");
            span.Inlines.Add(url);
            return span;
        }
        if ((token.StartsWith("*") && token.EndsWith("*") || token.StartsWith("_") && token.EndsWith("_")) && token.Length > 2)
            return new Italic(new Run(token[1..^1]));
        return new Run(token);
    }
}
