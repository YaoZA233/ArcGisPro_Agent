using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

namespace ArcGISAgent;

internal static class MessageRenderer
{
    public static RichTextBox Create(string markdown, FrameworkElement theme)
    {
        var document = new FlowDocument { PagePadding = new Thickness(0), FontSize = 13, ColumnWidth = double.MaxValue };
        bool code = false;
        var codeLines = new List<string>();
        foreach (string line in markdown.Replace("\r", "").Split('\n'))
        {
            if (line.TrimStart().StartsWith("```"))
            {
                if (code) { AddCode(document, string.Join("\n", codeLines), theme); codeLines.Clear(); }
                code = !code; continue;
            }
            if (code) { codeLines.Add(line); continue; }
            if (string.IsNullOrWhiteSpace(line)) continue;
            var heading = Regex.Match(line, @"^(#{1,4})\s+(.+)$");
            var paragraph = new Paragraph { Margin = new Thickness(0, 0, 0, 9), LineHeight = 21 };
            string text = line;
            if (heading.Success) { paragraph.FontSize = 19 - heading.Groups[1].Length; paragraph.FontWeight = FontWeights.SemiBold; paragraph.Margin = new Thickness(0, 7, 0, 8); text = heading.Groups[2].Value; }
            else if (Regex.IsMatch(line, @"^\s*[-*]\s+")) text = "•  " + Regex.Replace(line, @"^\s*[-*]\s+", "");
            else if (line.StartsWith("> ")) { paragraph.Foreground = (Brush)theme.FindResource("Muted"); paragraph.BorderThickness = new Thickness(2, 0, 0, 0); paragraph.BorderBrush = (Brush)theme.FindResource("Line"); paragraph.Padding = new Thickness(8, 0, 0, 0); text = line[2..]; }
            int offset = 0;
            foreach (Match match in Regex.Matches(text, @"\*\*(.+?)\*\*|`([^`]+)`"))
            {
                if (match.Index > offset) paragraph.Inlines.Add(new Run(text[offset..match.Index]));
                if (match.Groups[1].Success) paragraph.Inlines.Add(new Bold(new Run(match.Groups[1].Value)));
                else paragraph.Inlines.Add(new Run(match.Groups[2].Value) { FontFamily = new FontFamily("Consolas"), Background = (Brush)theme.FindResource("Page") });
                offset = match.Index + match.Length;
            }
            if (offset < text.Length) paragraph.Inlines.Add(new Run(text[offset..]));
            document.Blocks.Add(paragraph);
        }
        if (codeLines.Count > 0) AddCode(document, string.Join("\n", codeLines), theme);
        var box = new RichTextBox { Document = document, IsReadOnly = true, IsDocumentEnabled = false, BorderThickness = new Thickness(0), Padding = new Thickness(0), Background = Brushes.Transparent, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        box.SetResourceReference(Control.ForegroundProperty, "Ink");
        return box;
    }

    private static void AddCode(FlowDocument document, string text, FrameworkElement theme)
    {
        var code = new TextBox { Text = text, IsReadOnly = true, FontFamily = new FontFamily("Consolas"), FontSize = 12, TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Padding = new Thickness(10) };
        code.SetResourceReference(Control.BackgroundProperty, "Page"); code.SetResourceReference(Control.ForegroundProperty, "Ink");
        document.Blocks.Add(new BlockUIContainer(code) { Margin = new Thickness(0, 4, 0, 10) });
    }
}
