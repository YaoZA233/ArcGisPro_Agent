using Markdig;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using Markdig.Extensions.Tables;
using Markdig.Extensions.TaskLists;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

namespace ArcGISAgent;

// Parse CommonMark and the common GitHub extensions into selectable WPF content.
// Raw HTML is displayed as text: model output must never execute active content.
internal static class MessageRenderer
{
    static readonly MarkdownPipeline Pipeline = new MarkdownPipelineBuilder()
        .UsePipeTables().UseTaskLists().UseAutoLinks().UseEmphasisExtras().UsePreciseSourceLocation().Build();

    public static RichTextBox Create(string markdown, FrameworkElement theme)
    {
        var doc = new FlowDocument { PagePadding = new Thickness(0), FontSize = 13, ColumnWidth = double.MaxValue };
        RenderBlocks(doc.Blocks, Markdown.Parse(markdown ?? "", Pipeline), theme);
        var box = new RichTextBox { Document = doc, IsReadOnly = true, IsDocumentEnabled = true, BorderThickness = new Thickness(0), Padding = new Thickness(0), Background = Brushes.Transparent, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        box.SetResourceReference(Control.ForegroundProperty, "Ink");
        return box;
    }

    static void RenderBlocks(BlockCollection output, ContainerBlock blocks, FrameworkElement theme)
    {
        foreach (var block in blocks)
        {
            switch (block)
            {
                case HeadingBlock heading:
                    var hp = new Paragraph { Margin = new Thickness(0, 8, 0, 6), FontWeight = FontWeights.Bold, FontSize = heading.Level switch { 1 => 20, 2 => 17, 3 => 15, _ => 13 } };
                    hp.SetResourceReference(TextElement.ForegroundProperty, "Accent");
                    RenderInlines(hp.Inlines, heading.Inline, theme); output.Add(hp); break;
                case ParagraphBlock paragraph:
                    var p = new Paragraph { Margin = new Thickness(0, 0, 0, 9), LineHeight = 21 };
                    RenderInlines(p.Inlines, paragraph.Inline, theme); output.Add(p); break;
                case FencedCodeBlock fenced:
                    AddCode(output, fenced.Lines.ToString(), fenced.Info ?? "", theme); break;
                case CodeBlock code:
                    AddCode(output, code.Lines.ToString(), "", theme); break;
                case ThematicBreakBlock:
                    output.Add(new Paragraph { Margin = new Thickness(0, 4, 0, 10), BorderBrush = (Brush)theme.FindResource("Line"), BorderThickness = new Thickness(0, 0, 0, 1) }); break;
                case QuoteBlock quote:
                    var section = new Section { Margin = new Thickness(0, 0, 0, 9), Padding = new Thickness(9, 2, 0, 2), BorderBrush = (Brush)theme.FindResource("Line"), BorderThickness = new Thickness(2, 0, 0, 0) };
                    RenderBlocks(section.Blocks, quote, theme); output.Add(section); break;
                case ListBlock list:
                    var wpfList = new System.Windows.Documents.List { MarkerStyle = list.IsOrdered ? TextMarkerStyle.Decimal : TextMarkerStyle.Disc, Margin = new Thickness(20, 0, 0, 8), Padding = new Thickness(0) };
                    if (list.IsOrdered && int.TryParse(list.OrderedStart, out int start)) wpfList.StartIndex = start;
                    foreach (var item in list.OfType<ListItemBlock>()) { var li = new ListItem(); RenderBlocks(li.Blocks, item, theme); wpfList.ListItems.Add(li); }
                    output.Add(wpfList); break;
                case Markdig.Extensions.Tables.Table table:
                    AddTable(output, table, theme); break;
                case HtmlBlock html:
                    output.Add(new Paragraph(new Run(html.Lines.ToString()))); break;
                case ContainerBlock container:
                    RenderBlocks(output, container, theme); break;
            }
        }
    }

    static void AddTable(BlockCollection output, Markdig.Extensions.Tables.Table table, FrameworkElement theme)
    {
        var grid = new Grid { HorizontalAlignment = HorizontalAlignment.Left };
        int cols = Math.Max(table.ColumnDefinitions.Count, table.OfType<Markdig.Extensions.Tables.TableRow>().Select(r => r.Count).DefaultIfEmpty(0).Max());
        for (int c = 0; c < cols; c++) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        int rowIndex = 0;
        foreach (var row in table.OfType<Markdig.Extensions.Tables.TableRow>())
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            int col = 0;
            foreach (var cell in row.OfType<Markdig.Extensions.Tables.TableCell>())
            {
                var content = new TextBlock { TextWrapping = TextWrapping.Wrap, MaxWidth = 240, FontWeight = row.IsHeader ? FontWeights.Bold : FontWeights.Normal };
                content.SetResourceReference(TextBlock.ForegroundProperty, row.IsHeader ? "Accent" : "Ink");
                foreach (var paragraph in cell.OfType<ParagraphBlock>()) RenderInlines(content.Inlines, paragraph.Inline, theme);
                var border = new Border { Child = content, Padding = new Thickness(8, 5, 8, 0), BorderBrush = (Brush)theme.FindResource("Line"), BorderThickness = new Thickness(1, 1, col == cols - 1 ? 1 : 0, 1) };
                Grid.SetRow(border, rowIndex); Grid.SetColumn(border, col++); grid.Children.Add(border);
            }
            rowIndex++;
        }
        var scroll = new ScrollViewer { Content = grid, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, MaxWidth = 700 };
        output.Add(new BlockUIContainer(scroll) { Margin = new Thickness(0, 2, 0, 10) });
    }

    static void AddCode(BlockCollection output, string text, string language, FrameworkElement theme)
    {
        var panel = new StackPanel();
        if (!string.IsNullOrWhiteSpace(language)) { var caption = new TextBlock { Text = language, Padding = new Thickness(10, 5, 10, 3), FontSize = 11 }; caption.SetResourceReference(TextBlock.ForegroundProperty, "Muted"); panel.Children.Add(caption); }
        var box = new TextBox { Text = text.TrimEnd('\n', '\r'), IsReadOnly = true, FontFamily = new FontFamily("Consolas"), FontSize = 12, TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Padding = new Thickness(10) };
        box.SetResourceReference(Control.BackgroundProperty, "Page"); box.SetResourceReference(Control.ForegroundProperty, "Ink"); panel.Children.Add(box);
        output.Add(new BlockUIContainer(panel) { Margin = new Thickness(0, 4, 0, 10) });
    }

    static void RenderInlines(InlineCollection output, ContainerInline? container, FrameworkElement theme)
    {
        if (container is null) return;
        foreach (var inline in container)
        {
            switch (inline)
            {
                case LiteralInline literal: output.Add(new Run(literal.Content.ToString())); break;
                case LineBreakInline br: output.Add(br.IsHard ? new LineBreak() : new Run(" ")); break;
                case CodeInline code: output.Add(new Run(code.Content) { FontFamily = new FontFamily("Consolas"), Background = (Brush)theme.FindResource("Page") }); break;
                case TaskList task: output.Add(new Run(task.Checked ? "☑ " : "☐ ")); break;
                case EmphasisInline emphasis:
                    var span = new Span();
                    if (emphasis.DelimiterChar == '~') span.TextDecorations = TextDecorations.Strikethrough;
                    else if (emphasis.DelimiterCount >= 2) { span.FontWeight = FontWeights.Bold; if (emphasis.DelimiterCount >= 3) span.FontStyle = FontStyles.Italic; }
                    else span.FontStyle = FontStyles.Italic;
                    RenderInlines(span.Inlines, emphasis, theme); output.Add(span); break;
                case LinkInline link:
                    var linkSpan = new Span(); linkSpan.SetResourceReference(TextElement.ForegroundProperty, "Accent");
                    if (link.IsImage) linkSpan.Inlines.Add(new Run("图片: "));
                    RenderInlines(linkSpan.Inlines, link, theme);
                    if (!string.IsNullOrEmpty(link.Url)) linkSpan.Inlines.Add(new Run(" (" + link.Url + ")") { FontSize = 11 });
                    output.Add(linkSpan); break;
                case AutolinkInline auto: output.Add(new Run(auto.Url)); break;
                case HtmlInline html: output.Add(new Run(html.Tag)); break;
                case ContainerInline nested: RenderInlines(output, nested, theme); break;
            }
        }
    }
}
