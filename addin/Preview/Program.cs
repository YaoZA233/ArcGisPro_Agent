using System.Windows;
using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Text.Json;
using System.Windows.Controls;
using System.Reflection;

namespace ArcGISAgent;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        var app = new Application();
        var view = new AgentView();
        var window = new Window { Title = "GIS Agent · 界面验证", Width = 450, Height = 920, MinWidth = 325, MinHeight = 600, Content = view, WindowStartupLocation = WindowStartupLocation.CenterScreen };
        if (args.Length > 0 && args[0] == "--test-markdown")
        {
            var rendered = MessageRenderer.Create("# 标题\n\n| 列 | 值 |\n| --- | --- |\n| **粗体** | a\\|b |\n\n- [x] 完成\n\n> *引用*\n\n```python\nprint(1)\n```", view);
            var blocks = rendered.Document.Blocks.Cast<System.Windows.Documents.Block>().ToList();
            if (!blocks.OfType<System.Windows.Documents.BlockUIContainer>().Any(b => b.Child is ScrollViewer)) throw new Exception("Missing table");
            var scroll = (ScrollViewer)blocks.OfType<System.Windows.Documents.BlockUIContainer>().First(b => b.Child is ScrollViewer).Child;
            var grid = (Grid)scroll.Content;
            var cells = grid.Children.OfType<Border>().Select(b => (TextBlock)b.Child).ToList();
            if (cells.Count != 4 || !cells[2].Inlines.OfType<System.Windows.Documents.Span>().Any() || !new System.Windows.Documents.TextRange(cells[3].ContentStart, cells[3].ContentEnd).Text.Contains("a|b")) throw new Exception("Table cells or inline formatting failed");
            if (!blocks.OfType<System.Windows.Documents.List>().Any() || !blocks.OfType<System.Windows.Documents.Section>().Any() || !blocks.OfType<System.Windows.Documents.BlockUIContainer>().Any(b => b.Child is StackPanel)) throw new Exception("Missing Markdown blocks");
            return;
        }
        bool captureSettings = args.Length >= 2 && args[0] == "--capture-settings";
        bool captureSelection = args.Length >= 2 && args[0] == "--capture-selection";
        bool captureApproval = args.Length >= 2 && (args[0] == "--capture-approval" || captureSelection);
        if (args.Length >= 2 && args[0] == "--capture-markdown")
        {
            const string sample =
                "# 流域分析报告\n\n" +
                "对 **DEM.tif** 完成处理，结果*可靠*，代码见 `Fill()`。\n\n" +
                "## 关键指标\n\n" +
                "| 指标 | 数值 | 单位 |\n" +
                "| --- | --- | --- |\n" +
                "| 流域面积 | 1234.5 | 平方公里 |\n" +
                "| 河网密度 | 0.87 | km/km² |\n\n" +
                "### 处理步骤\n\n" +
                "1. 填充洼地\n" +
                "2. 计算流向 [Esri 文档](https://pro.arcgis.com)\n\n" +
                "- 输出目录\n" +
                "  - 嵌套子项\n" +
                "  - 另一个子项\n\n" +
                "> 注意：写入已启用，成果保存在任务目录。\n\n" +
                "```python\nimport arcpy\narcpy.sa.Fill(\"dem\")\n```\n\n" +
                "---\n\n完成。";
            window.Loaded += (_, _) =>
            {
                var host = new Grid { Width = 430, Background = (Brush)view.FindResource("Surface") };
                host.Children.Add(MessageRenderer.Create(sample, view));
                window.Content = host;
                host.Measure(new Size(430, double.PositiveInfinity));
                double height = double.IsInfinity(host.DesiredSize.Height) ? 900 : host.DesiredSize.Height;
                host.Arrange(new Rect(0, 0, 430, height));
                host.UpdateLayout();
                var target = new RenderTargetBitmap(430, Math.Max(1, (int)Math.Ceiling(height)), 96, 96, PixelFormats.Pbgra32);
                target.Render(host);
                var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(target));
                using (var file = File.Create(args[1])) encoder.Save(file);
                app.Shutdown(0);
            };
            app.Run(window);
            return;
        }
        if (captureSettings || captureApproval)
        {
            window.ShowActivated = false; window.ShowInTaskbar = false;
            window.WindowStartupLocation = WindowStartupLocation.Manual;
            window.Left = -20000; window.Top = -20000;
            if (args.Length >= 3 && int.TryParse(args[2], out int width)) window.Width = Math.Clamp(width, 325, 1000);
        }
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        int attempts = 0;
        timer.Tick += (_, _) =>
        {
            string? root = Environment.GetEnvironmentVariable("ARCGIS_AGENT_HOME");
            if (root is null) return;
            if (captureSettings || captureApproval)
            {
                var combo = (ComboBox)view.FindName("ProviderCombo");
                if (combo.Items.Count == 0 && ++attempts < 20) return;
                ((Expander)view.FindName("SettingsPanel")).IsExpanded = true;
                ((Expander)view.FindName("ConnectionManager")).IsExpanded = true;
                if (args.Length >= 4 && args[3] == "compact") ((Expander)view.FindName("ConnectionManager")).IsExpanded = false;
                view.UpdateLayout();
                if (args.Length >= 4 && args[3] == "lower")
                {
                    ((ScrollViewer)view.FindName("SettingsScroll")).ScrollToEnd();
                    view.UpdateLayout();
                }
                if (captureApproval)
                {
                    object parameters = captureSelection ? new { selection_token = new string('a', 32), output_name = "selected_points" } :
                        new { dataset = Path.Combine(root, "workspace", "smoke", "input.gdb", "points"), distance = "500 Meters", output_name = "buffers" };
                    using var approval = JsonDocument.Parse(JsonSerializer.Serialize(new { request_id = "ui-fixture", title = captureSelection ? "导出已确认的选择集快照（界面测试）" : "创建测地线缓冲区（界面测试）",
                        tool = captureSelection ? "export_selection" : "buffer", arcgis_tool = captureSelection ? "management.MakeFeatureLayer + CopyFeatures" : "analysis.Buffer", writes = true, effect = "界面验证：未执行工具。确认后在独立任务目录创建新成果，保留原始数据。",
                        arguments = parameters,
                        selection = captureSelection ? new { layer_name = "人工测试点", selected_count = 2, dataset = Path.Combine(root, "workspace", "smoke", "input.gdb", "points"), captured_at = "2026-11-03T00:00:00Z",
                            semantics = "冻结捕获时的所选 ID；读取已保存的数据，不包含未提交编辑。" } : null,
                        expected_outputs = new[] { Path.Combine(root, "workspace", "smoke", "runs", "example", "outputs", "results.gdb", "buffers") } }));
                    typeof(AgentView).GetMethod("ShowApproval", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, new object[] { approval.RootElement });
                    if (args.Length >= 4 && args[3] == "dark")
                    {
                        typeof(AgentView).GetField("dark", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(view, true);
                        typeof(AgentView).GetMethod("ApplyTheme", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(view, null);
                    }
                    view.UpdateLayout();
                }
                var bitmap = new RenderTargetBitmap((int)view.ActualWidth, (int)view.ActualHeight, 96, 96, PixelFormats.Pbgra32);
                bitmap.Render(view);
                var image = new PngBitmapEncoder(); image.Frames.Add(BitmapFrame.Create(bitmap));
                using (var output = File.Create(args[1])) image.Save(output);
                File.WriteAllText(Path.ChangeExtension(args[1], ".json"), JsonSerializer.Serialize(new { width = view.ActualWidth, height = view.ActualHeight, provider_count = combo.Items.Count,
                    selected = combo.Text, status = ((TextBlock)view.FindName("ProviderStatusText")).Text,
                    approval_fixture = captureApproval, approval_visible = ((Border)view.FindName("ApprovalCard")).IsVisible,
                    composer_visible = ((Border)view.FindName("ComposerPanel")).IsVisible,
                    deny_has_focus = ((Button)view.FindName("DenyOperationButton")).IsKeyboardFocused }));
                app.Shutdown(combo.Items.Count > 0 ? 0 : 1);
                return;
            }
            string request = Path.Combine(root, "workspace", "capture.request");
            if (!File.Exists(request) || view.ActualWidth < 1 || view.ActualHeight < 1) return;
            File.Delete(request);
            var dpi = VisualTreeHelper.GetDpi(view);
            var target = new RenderTargetBitmap((int)(view.ActualWidth * dpi.DpiScaleX), (int)(view.ActualHeight * dpi.DpiScaleY), 96 * dpi.DpiScaleX, 96 * dpi.DpiScaleY, PixelFormats.Pbgra32);
            target.Render(view);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(target));
            using var file = File.Create(Path.Combine(root, "workspace", "studio-preview.png")); encoder.Save(file);
        };
        window.Loaded += (_, _) => timer.Start(); window.Closed += (_, _) => timer.Stop();
        app.Run(window);
    }
}

internal static class ProContext
{
    internal static bool SupportsHostTools => false;
    internal static string ProjectPath() => "";
    internal static Task AddToMap(string path) => throw new InvalidOperationException("独立预览不连接地图；请在 ArcGIS Pro 面板中添加成果。");
    internal static Task<object> ExecuteAsync(string tool, JsonElement arguments, Func<bool> cancelled) => throw new InvalidOperationException("独立预览不连接 Pro 当前地图。");
}
