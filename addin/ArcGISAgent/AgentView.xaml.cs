using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;

namespace ArcGISAgent;

public partial class AgentView : UserControl
{
    public static RoutedCommand SendMessageCommand = new();
    private Process? worker;
    private TaskCompletionSource<JsonElement>? pending;
    private string expectedResponse = "";
    private readonly SemaphoreSlim startupGate = new(1, 1);
    private readonly SemaphoreSlim inputGate = new(1, 1);
    private bool busy, operation, cancelling, dark;
    private string sessionId = "", latestRun = "";
    private readonly List<string> attachments = new();
    private readonly StringBuilder export = new(), answerBuffer = new(), reasoningBuffer = new();
    private TextBox? liveReasoning;
    private ContentControl? liveAnswer;
    private StackPanel? liveCard;
    private Expander? reasoningPanel;
    private StackPanel? liveSteps;
    private StepRow? activeStep;
    private bool reasoningCollapsed;
    private readonly Stopwatch clock = new(), stepClock = new();
    private readonly DispatcherTimer renderTimer = new() { Interval = TimeSpan.FromMilliseconds(80) };
    private int renderedAnswer, renderedReasoning;
    private long turnInput, turnOutput, turnCache;
    private bool populatingProviders;
    private string activeProvider = "";
    private readonly Dictionary<string, JsonElement> providerProfiles = new(), providerPresets = new();
    private string approvalId = "";
    private bool approvalWrites, approvalSent;
    private readonly HashSet<string> handledHostRequests = new();
    private long turnGeneration;

    public AgentView()
    {
        InitializeComponent();
        CommandBindings.Add(new CommandBinding(SendMessageCommand, Send_Click));
        InputBindings.Add(new KeyBinding(SendMessageCommand, Key.Enter, ModifierKeys.Control));
        renderTimer.Tick += (_, _) => FlushStreaming();
        Loaded += OnLoaded;
        Unloaded += (_, _) => { renderTimer.Stop(); StopWorker(); };
        AddWelcome();
    }

    private static string FindRoot()
    {
        string? configured = Environment.GetEnvironmentVariable("ARCGIS_AGENT_HOME");
        if (!string.IsNullOrWhiteSpace(configured)) return configured;
        string location = Path.GetDirectoryName(typeof(AgentView).Assembly.Location)!;
        string marker = Path.Combine(location, "agent-home.txt");
        if (File.Exists(marker)) return File.ReadAllText(marker, Encoding.UTF8).Trim();
        throw new InvalidOperationException("找不到工程路径。请设置 ARCGIS_AGENT_HOME 后重新打开面板。");
    }

    private static string FindProPython()
    {
        // Explicit configuration must take precedence over installation discovery.
        string? configured = Environment.GetEnvironmentVariable("ARCGIS_AGENT_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            if (File.Exists(configured)) return configured;
            throw new InvalidOperationException("ARCGIS_AGENT_PYTHON 指向的 Python 不存在：" + configured);
        }

        // 自动发现 ArcGIS Pro Python（从注册表）
        try
        {
            using var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\ESRI\ArcGISPro");
            if (key != null)
            {
                string? installDir = key.GetValue("InstallDir") as string;
                if (!string.IsNullOrEmpty(installDir))
                {
                    string proPython = Path.Combine(installDir, @"bin\Python\envs\arcgispro-py3\python.exe");
                    if (File.Exists(proPython)) return proPython;
                }
            }
        }
        catch { }

        // 回退到常见安装位置
        string[] candidates = new[]
        {
            @"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe",
        };
        foreach (string candidate in candidates)
        {
            if (File.Exists(candidate)) return candidate;
        }

        throw new InvalidOperationException("找不到 ArcGIS Pro Python。请设置 ARCGIS_AGENT_PYTHON 环境变量。");
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        LoadAppearance();
        try { await EnsureWorkerAsync(); }
        catch (Exception ex) { Notice(ex.Message); StatusText.Text = "等待配置或重连"; StopWorker(); }
    }

    private async Task EnsureWorkerAsync()
    {
        await startupGate.WaitAsync();
        try
        {
            if (worker is { HasExited: false }) return;
            string root = FindRoot();
            string pythonExe = FindProPython();
            var start = new ProcessStartInfo(pythonExe)
            {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            foreach (string arg in new[] { "-u", "-m", "arcgis_agent", "rpc" }) start.ArgumentList.Add(arg);

            // Retain normal user settings (including proxies, credentials and explicit
            // worker configuration), while removing Pro's Python interpreter context.
            foreach (string key in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE",
                "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "ESRI_SOFTWARE_CLASS", "ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION" })
                start.Environment.Remove(key);

            // 添加 Python 特定的环境变量
            start.Environment["PYTHONPATH"] = Path.Combine(root, "src");
            start.Environment["PYTHONIOENCODING"] = "utf-8";
            start.Environment["PYTHONUNBUFFERED"] = "1";
            worker = Process.Start(start) ?? throw new InvalidOperationException("无法启动 Python 3.11+。");
            _ = worker.StandardError.ReadToEndAsync();
            string? line = await worker.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(20));
            if (line is null) throw new InvalidOperationException("后台进程已退出。请检查 Python 和 ArcGIS 配置。");
            using var doc = JsonDocument.Parse(line);
            var ready = doc.RootElement;
            if (ready.GetProperty("type").GetString() != "ready") throw new InvalidOperationException(Text(ready, "message"));
            PopulateOptions(ready);
            _ = ReadLoopAsync(worker);
            if (sessionId.Length > 0)
            {
                try { await RequestAsync(new { type = "load", session_id = sessionId }, "loaded"); }
                catch (Exception) { sessionId = Text(ready, "session_id"); }
            }
            else sessionId = Text(ready, "session_id");
            StatusText.Text = "已连接 · 本地 GIS 执行";
        }
        catch
        {
            // Never reuse a process whose ready handshake failed or timed out.
            StopWorker();
            throw;
        }
        finally { startupGate.Release(); }
    }

    private async Task ReadLoopAsync(Process process)
    {
        try
        {
            while (true)
            {
                string? line = await process.StandardOutput.ReadLineAsync();
                if (line is null) throw new IOException("后台连接已断开，可重新发送任务。" );
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                string type = Text(root, "type");
                if (type == "event")
                {
                    var item = root.GetProperty("event");
                    if (Text(item, "event") == "host_requested") _ = HandleHostAsync(item.Clone(), process);
                    else HandleEvent(item);
                }
                else if (type == "control_error") Notice(Text(root, "message"));
                else if (type == "error") pending?.TrySetException(new InvalidOperationException(Text(root, "message")));
                else if (type == expectedResponse) pending?.TrySetResult(root.Clone());
            }
        }
        catch (Exception ex)
        {
            if (ReferenceEquals(worker, process))
            {
                pending?.TrySetException(ex);
                if (!busy) StatusText.Text = "连接已关闭 · 发送时自动重连";
            }
        }
    }

    private async Task<JsonElement> RequestAsync(object request, string response, int timeoutSeconds = 600)
    {
        if (pending is not null) throw new InvalidOperationException("另一个操作尚未完成。");
        var wait = new TaskCompletionSource<JsonElement>();
        pending = wait; expectedResponse = response;
        try
        {
            await WriteRequestAsync(worker!, request);
            return await wait.Task.WaitAsync(TimeSpan.FromSeconds(timeoutSeconds));
        }
        finally { if (ReferenceEquals(pending, wait)) pending = null; }
    }

    private async Task WriteRequestAsync(Process process, object request)
    {
        await inputGate.WaitAsync();
        try { await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request)); }
        finally { inputGate.Release(); }
    }

    private void ShowApproval(JsonElement item)
    {
        approvalId = Text(item, "request_id"); approvalSent = false;
        approvalWrites = item.TryGetProperty("writes", out var writes) && writes.GetBoolean();
        string stepLabel = Text(item, "step");
        ApprovalTitle.Text = "等待授权" + (stepLabel.Length > 0 ? " · 步骤 " + stepLabel : "") + " · " + Text(item, "title");
        string parameters = string.Join("\n", item.GetProperty("arguments").EnumerateObject().Select(property => property.Name + "：" +
            (property.Value.ValueKind == JsonValueKind.String ? property.Value.GetString() : JsonSerializer.Serialize(property.Value, new JsonSerializerOptions { WriteIndented = true }))));
        ApprovalDetails.Text = Text(item, "effect") + "\n\nArcGIS 工具：" + Text(item, "arcgis_tool") + "\n参数：\n" + parameters;
        ApprovalSelectionSummary.Visibility = Visibility.Collapsed;
        if (item.TryGetProperty("selection", out var selection) && selection.ValueKind == JsonValueKind.Object)
        {
            ApprovalSelectionSummary.Text = Text(selection, "layer_name") + " · " + Text(selection, "selected_count") + " 个选中要素";
            ApprovalSelectionSummary.Visibility = Visibility.Visible;
            ApprovalDetails.Text += "\n\n选择集：" + Text(selection, "layer_name") + "\n源数据：" + Text(selection, "dataset") +
                "\n选中数量：" + Text(selection, "selected_count") + "\n捕获时间（UTC）：" + Text(selection, "captured_at") +
                "\n" + Text(selection, "semantics");
        }
        if (item.TryGetProperty("expected_outputs", out var outputs) && outputs.GetArrayLength() > 0)
            ApprovalDetails.Text += "\n\n预期输出：\n" + string.Join("\n", outputs.EnumerateArray().Select(path => path.GetString()));
        AllowOperationButton.Content = approvalWrites ? "允许本次写入" : "允许本次读取";
        ApprovalButtons.IsEnabled = true; ApprovalCard.Visibility = Visibility.Visible;
        ComposerPanel.Visibility = Visibility.Collapsed; SettingsPanel.IsExpanded = false;
        HistoryPanel.Visibility = Visibility.Collapsed;
        StatusText.Text = "等待你确认 · 工具尚未执行";
        ActivityBar.Visibility = Visibility.Collapsed;
        DenyOperationButton.Focus(); Animate(ApprovalCard);
    }

    private void ClearApproval()
    {
        approvalId = ""; approvalSent = false;
        ApprovalCard.Visibility = Visibility.Collapsed; ComposerPanel.Visibility = Visibility.Visible;
    }

    private async Task AnswerApprovalAsync(string decision)
    {
        if (approvalId.Length == 0 || approvalSent || worker is null) return;
        approvalSent = true; ApprovalButtons.IsEnabled = false;
        try
        {
            await WriteRequestAsync(worker, new { type = "approval_response", request_id = approvalId,
                response = new { decision, allow_writes = decision == "allow" && approvalWrites } });
            StatusText.Text = "已发送本次决定，等待执行器确认…";
        }
        catch (Exception ex) { Notice(ex.Message); StopWorker(); }
    }
    private async void AllowOperation_Click(object sender, RoutedEventArgs e) => await AnswerApprovalAsync("allow");
    private async void DenyOperation_Click(object sender, RoutedEventArgs e) => await AnswerApprovalAsync("deny");
    private async void CancelOperation_Click(object sender, RoutedEventArgs e) => await CancelAsync();

    private async Task HandleHostAsync(JsonElement request, Process process)
    {
        string id = Text(request, "request_id");
        if (!handledHostRequests.Add(id)) return;
        long requestTurn = turnGeneration;
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(55));
        object response;
        try
        {
            if (!ReferenceEquals(worker, process) || !busy || cancelling) throw new OperationCanceledException("任务已停止。");
            string tool = Text(request, "tool");
            if (tool is not ("map_context" or "get_selection" or "add_to_map" or "set_layer_visibility")) throw new InvalidOperationException("未知 Pro 宿主工具。");
            var data = await ProContext.ExecuteAsync(tool, request.GetProperty("arguments"),
                () => deadline.IsCancellationRequested || cancelling || !busy || requestTurn != turnGeneration || !ReferenceEquals(worker, process));
            response = new { ok = true, data };
        }
        catch (Exception ex) { response = new { ok = false, error = new { type = ex.GetType().Name, message = ex.Message } }; }
        try { await WriteRequestAsync(process, new { type = "host_result", request_id = id, response }); }
        catch (Exception ex) { if (ReferenceEquals(worker, process)) Notice(ex.Message); }
    }

    private object ReadOptions()
    {
        if (!int.TryParse(MaxTokensCombo.Text, out int max) || max < 256 || max > 65536) throw new InvalidOperationException("输出上限须为 256 至 65536 的整数。");
        return new { model = ModelCombo.Text.Trim(), thinking = SelectedTag(ThinkingCombo), max_tokens = max, image_detail = SelectedTag(ImageDetailCombo) };
    }

    private static string SelectedTag(ComboBox combo) => (combo.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "auto";
    private static string Text(JsonElement item, string name) => item.TryGetProperty(name, out var value) ? value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.ToString() : "";
    private static long Number(JsonElement item, string name) => item.TryGetProperty(name, out var value) && value.TryGetInt64(out long n) ? n : 0;

    private void PopulateOptions(JsonElement state)
    {
        PopulateProviders(state);
        ModelCombo.Text = Text(state, "model");
        MaxTokensCombo.Text = Text(state, "max_tokens");
        foreach (ComboBoxItem item in ThinkingCombo.Items) if (item.Tag?.ToString() == Text(state, "thinking")) ThinkingCombo.SelectedItem = item;
        foreach (ComboBoxItem item in ImageDetailCombo.Items) if (item.Tag?.ToString() == Text(state, "image_detail")) ImageDetailCombo.SelectedItem = item;
        ActualModelText.Text = activeProvider + " / " + ModelCombo.Text + " · 思考 " + Text(state, "thinking");
        if (state.TryGetProperty("models", out var models))
        {
            string current = ModelCombo.Text;
            ModelCombo.Items.Clear();
            foreach (var model in models.EnumerateArray()) ModelCombo.Items.Add(new ComboBoxItem { Content = model.GetString() });
            ModelCombo.Text = current;
        }
        bool reasoningEnabled = Text(state, "reasoning_mode") != "none";
        ThinkingCombo.IsEnabled = reasoningEnabled;
        ThinkingCombo.ToolTip = reasoningEnabled ? "按所选推理参数模式发送；具体档位支持取决于模型" : "此连接使用模型默认推理行为，可在管理连接中选择推理参数模式";
    }

    private static void SelectTag(ComboBox combo, string tag)
    {
        foreach (ComboBoxItem item in combo.Items) if (item.Tag?.ToString() == tag) { combo.SelectedItem = item; return; }
    }

    private void PopulateProviders(JsonElement state)
    {
        if (!state.TryGetProperty("providers", out var profiles)) return;
        populatingProviders = true;
        try
        {
            activeProvider = Text(state, "provider");
            providerProfiles.Clear(); ProviderCombo.Items.Clear();
            foreach (var profile in profiles.EnumerateArray())
            {
                string id = Text(profile, "id"); providerProfiles[id] = profile.Clone();
                ProviderCombo.Items.Add(new ComboBoxItem { Content = Text(profile, "name") + " · " + id, Tag = id });
            }
            SelectTag(ProviderCombo, activeProvider);
            if (state.TryGetProperty("presets", out var presets))
            {
                providerPresets.Clear(); PresetCombo.Items.Clear();
                foreach (var preset in presets.EnumerateArray())
                {
                    string id = Text(preset, "preset"); providerPresets[id] = preset.Clone();
                    PresetCombo.Items.Add(new ComboBoxItem { Content = Text(preset, "name"), Tag = id });
                }
            }
            ReasoningModeCombo.Items.Clear();
            if (state.TryGetProperty("reasoning_modes", out var modes))
                foreach (var mode in modes.EnumerateArray()) ReasoningModeCombo.Items.Add(new ComboBoxItem { Content = mode.GetString(), Tag = mode.GetString() });
            if (providerProfiles.TryGetValue(activeProvider, out var current)) FillConnection(current);
            string key = state.TryGetProperty("key_configured", out var configured) && configured.GetBoolean() ? "密钥已配置" : "尚未配置密钥";
            string protocol = Text(state, "protocol") == "anthropic" ? "Claude Messages" : "Chat Completions";
            ProviderStatusText.Text = protocol + " · " + key + " · 图片 " + (state.GetProperty("vision").GetBoolean() ? "开启" : "关闭") + " · 工具 " + (state.GetProperty("tools").GetBoolean() ? "开启" : "关闭");
            DestinationText.Text = "Ctrl+Enter 发送 · 发送至 " + Text(state, "base_url");
        }
        finally { populatingProviders = false; }
    }

    private void FillConnection(JsonElement profile)
    {
        ConnectionIdBox.Text = Text(profile, "id"); ConnectionNameBox.Text = Text(profile, "name");
        EndpointBox.Text = Text(profile, "base_url"); DefaultModelBox.Text = Text(profile, "model");
        SelectTag(PresetCombo, Text(profile, "preset")); SelectTag(ProtocolCombo, Text(profile, "protocol"));
        SelectTag(ReasoningModeCombo, Text(profile, "reasoning"));
        VisionCapability.IsChecked = profile.GetProperty("vision").GetBoolean();
        ToolsCapability.IsChecked = profile.GetProperty("tools").GetBoolean();
        StreamUsageCapability.IsChecked = profile.GetProperty("stream_usage").GetBoolean();
        ApiKeyBox.Clear();
    }

    private async void Provider_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (populatingProviders || worker is null || SelectedTag(ProviderCombo) == activeProvider) return;
        string id = SelectedTag(ProviderCombo);
        await IdleOperation(async () =>
        {
            var state = await RequestAsync(new { type = "provider_select", provider = id }, "providers", 20);
            PopulateOptions(state); Notice("已切换连接。后续请求使用新连接，会话文字与工具结果继续保留。");
        });
    }

    private void Preset_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (populatingProviders || !providerPresets.TryGetValue(SelectedTag(PresetCombo), out var preset)) return;
        string id = ConnectionIdBox.Text;
        populatingProviders = true;
        try { FillConnection(preset); ConnectionIdBox.Text = id; }
        finally { populatingProviders = false; }
    }

    private void NewConnection_Click(object sender, RoutedEventArgs e)
    {
        if (providerPresets.TryGetValue(SelectedTag(PresetCombo), out var preset))
        {
            populatingProviders = true;
            try { FillConnection(preset); ConnectionIdBox.Text = Text(preset, "preset") + "-" + Guid.NewGuid().ToString("N")[..6]; }
            finally { populatingProviders = false; }
            Notice("已创建待保存的连接配置。填写地址、模型和密钥后保存。");
        }
    }

    private async void SaveConnection_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        var profile = new { id = ConnectionIdBox.Text.Trim(), name = ConnectionNameBox.Text.Trim(), preset = SelectedTag(PresetCombo),
            protocol = SelectedTag(ProtocolCombo), base_url = EndpointBox.Text.Trim(), model = DefaultModelBox.Text.Trim(),
            reasoning = SelectedTag(ReasoningModeCombo), vision = VisionCapability.IsChecked == true,
            tools = ToolsCapability.IsChecked == true, stream_usage = StreamUsageCapability.IsChecked == true };
        string key = ApiKeyBox.Password;
        var state = await RequestAsync(new { type = "provider_save", profile, api_key = key, activate = true }, "providers", 20);
        ApiKeyBox.Clear(); PopulateOptions(state); Notice("连接已保存并启用。请刷新模型列表或发送消息验证。");
    });

    private async void DeleteConnection_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        var state = await RequestAsync(new { type = "provider_delete", provider = ConnectionIdBox.Text.Trim() }, "providers", 20);
        PopulateOptions(state); Notice("连接配置已删除；本机密钥保留。");
    });

    private async void Send_Click(object sender, RoutedEventArgs e)
    {
        if (busy) { await CancelAsync(); return; }
        if (operation || string.IsNullOrWhiteSpace(PromptBox.Text)) return;
        string prompt = PromptBox.Text.Trim();
        object options;
        try { await EnsureWorkerAsync(); options = ReadOptions(); }
        catch (Exception ex) { Notice(ex.Message); return; }
        turnGeneration++; SetBusy(true); cancelling = false; NoticeText.Visibility = Visibility.Collapsed;
        HistoryPanel.Visibility = Visibility.Collapsed;
        PromptBox.Text = "";
        AddUser(prompt, attachments);
        BeginAssistant();
        export.AppendLine("## 你\n\n" + prompt + "\n");
        turnInput = turnOutput = turnCache = 0; InputTokens.Text = OutputTokens.Text = CacheTokens.Text = "—";
        clock.Restart(); renderTimer.Start();
        try
        {
            var envelope = await RequestAsync(new { type = "send", message = prompt, options,
                attachments = attachments.ToArray(), context = ProContext.ProjectPath(), allow_writes = AllowWrites.IsChecked == true,
                host_tools = ProContext.SupportsHostTools }, "result", 7200);
            var result = envelope.GetProperty("result");
            CompleteAssistant(result);
            if (Text(result, "status") == "completed") { attachments.Clear(); RenderAttachments(); }
        }
        catch (Exception ex)
        {
            FlushStreaming();
            string message = cancelling ? "任务已停止。可从历史记录继续已保存的对话；部分 GIS 成果可能保留。" : ex.Message;
            if (liveAnswer is not null) liveAnswer.Content = MessageRenderer.Create(message, this);
            StatusText.Text = cancelling ? "已停止" : "任务未完成";
            export.AppendLine("## 提示\n\n" + message + "\n");
            if (!cancelling) Notice(message);
            StopWorker();
        }
        finally { ClearApproval(); clock.Stop(); renderTimer.Stop(); FlushStreaming(); SetBusy(false); SaveAppearance(); }
    }

    private async Task CancelAsync()
    {
        if (cancelling) return;
        turnGeneration++; cancelling = true; StatusText.Text = "正在停止…"; SendButton.Content = "停止中";
        ClearApproval();
        try { await WriteRequestAsync(worker!, new { type = "cancel" }); }
        catch (Exception) { StopWorker(); return; }
        await Task.Delay(4000);
        if (busy && cancelling) StopWorker();
    }

    private void SetBusy(bool value)
    {
        busy = value;
        SettingsContent.IsEnabled = !value;
        SendButton.Content = value ? "停止" : "发送";
        ActivityBar.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        foreach (var control in new Control[] { NewButton, HistoryButton, ApplySettingsButton, RefreshModelsButton, AttachButton, ModelCombo, MaxTokensCombo, ImageDetailCombo }) control.IsEnabled = !value;
        AllowWrites.IsEnabled = !value;
    }

    private void BeginAssistant()
    {
        answerBuffer.Clear(); reasoningBuffer.Clear(); renderedAnswer = renderedReasoning = 0;
        liveCard = AddCard("AGENT", false);
        liveSteps = null; activeStep = null; reasoningCollapsed = false; stepClock.Reset();
        liveReasoning = Selectable("", true);
        reasoningPanel = new Expander { Header = "推理过程", Content = liveReasoning, IsExpanded = true, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 4, 0, 8) };
        reasoningPanel.SetResourceReference(ForegroundProperty, "Muted");
        reasoningPanel.Collapsed += (_, _) => reasoningCollapsed = true;
        liveCard.Children.Add(reasoningPanel);
        liveAnswer = new ContentControl { Content = MessageRenderer.Create("处理中…", this) };
        liveCard.Children.Add(liveAnswer);
    }

    private void HandleEvent(JsonElement item)
    {
        switch (Text(item, "event"))
        {
            case "model_started": StatusText.Text = "正在生成 · " + Text(item, "thinking"); break;
            case "content_delta": answerBuffer.Append(Text(item, "text")); break;
            case "reasoning_delta":
                if (ShowReasoning.IsChecked == true)
                {
                    reasoningBuffer.Append(Text(item, "text"));
                    if (reasoningPanel is not null)
                    {
                        reasoningPanel.Visibility = Visibility.Visible;
                        if (!reasoningCollapsed) reasoningPanel.IsExpanded = true;
                    }
                }
                break;
            case "tool_requested":
                FlushStreaming();
                string requestedArgs = item.TryGetProperty("arguments", out var requested) ? JsonSerializer.Serialize(requested, new JsonSerializerOptions { WriteIndented = true }) : "";
                activeStep = AddStepRow((int)Number(item, "step"), Text(item, "tool"), requestedArgs);
                stepClock.Restart();
                StatusText.Text = "请求工具 · 步骤 " + Number(item, "step") + " · " + Text(item, "tool");
                break;
            case "approval_requested":
                ShowApproval(item);
                if (activeStep is not null)
                {
                    activeStep.RequestId = Text(item, "request_id");
                    SetStepState(activeStep, "● 等待授权", "Muted");
                }
                break;
            case "approval_decided":
                if (Text(item, "request_id") == approvalId) ClearApproval();
                if (activeStep is not null)
                {
                    bool allowed = Text(item, "decision") == "allow";
                    string stateLabel = Text(item, "decision") switch
                    { "allow" => "✓ 已授权 · 等待执行", "deny" => "⊘ 已拒绝 · 任务停止", "cancel" => "⊘ 已取消 · 任务停止", _ => "⊘ 授权超时 · 任务停止" };
                    SetStepState(activeStep, stateLabel, allowed ? "Accent" : "Muted");
                }
                break;
            case "tool_executing":
                if (activeStep is not null)
                {
                    SetStepState(activeStep, "… 执行中", "Accent");
                    activeStep.Log.Clear();
                    activeStep.Details.Visibility = Visibility.Visible;
                    activeStep.Details.IsExpanded = false;
                    activeStep.DetailText.Text = activeStep.ArgsJson;
                }
                StatusText.Text = "正在执行 · 步骤 " + Number(item, "step") + " · " + Text(item, "tool");
                ActivityBar.Visibility = Visibility.Visible;
                break;
            case "tool_progress":
                if (activeStep is not null)
                {
                    string line = Text(item, "text");
                    activeStep.Progress.Text = line;
                    activeStep.Progress.Visibility = Visibility.Visible;
                    activeStep.Log.AppendLine(line);
                    activeStep.DetailText.Text = activeStep.ArgsJson + "\n\n运行日志：\n" + activeStep.Log;
                    if (!activeStep.SawProgress)
                    {
                        activeStep.SawProgress = true;
                        activeStep.Details.Header = "参数与运行日志";
                    }
                }
                break;
            case "tool_result":
                bool ok = item.TryGetProperty("ok", out var good) && good.GetBoolean();
                if (activeStep is not null)
                {
                    SetStepState(activeStep, ok ? "✓ 完成" : "✗ 失败", ok ? "Accent" : "Ink");
                    activeStep.Elapsed.Text = stepClock.Elapsed.TotalSeconds.ToString("F1") + "s";
                    activeStep.Details.Visibility = Visibility.Visible;
                    activeStep.Details.IsExpanded = !ok;
                    activeStep.Details.Header = ok ? (activeStep.SawProgress ? "参数、运行日志与结果" : "参数与结果") : "参数与错误";
                    string outcome = item.TryGetProperty("data", out var data) ? data.ToString() : Text(item, "error");
                    activeStep.DetailText.Text = activeStep.ArgsJson
                        + (activeStep.Log.Length > 0 ? "\n\n运行日志：\n" + activeStep.Log : "")
                        + "\n\n结果：\n" + outcome;
                }
                break;
            case "model_usage":
                ActualModelText.Text = activeProvider + " / 实际返回：" + Text(item, "model");
                if (item.TryGetProperty("usage", out var usage) && usage.TryGetProperty("prompt_tokens", out _))
                {
                    turnInput += Number(usage, "prompt_tokens"); turnOutput += Number(usage, "completion_tokens");
                    turnCache += usage.TryGetProperty("prompt_cache_hit_tokens", out _) ? Number(usage, "prompt_cache_hit_tokens") : usage.TryGetProperty("prompt_tokens_details", out var details) ? Number(details, "cached_tokens") : 0;
                    UpdateUsage();
                }
                break;
        }
    }

    private void FlushStreaming()
    {
        bool follow = IsFollowing();
        if (liveAnswer is not null && answerBuffer.Length != renderedAnswer) { liveAnswer.Content = MessageRenderer.Create(answerBuffer.ToString(), this); renderedAnswer = answerBuffer.Length; }
        if (liveReasoning is not null && reasoningBuffer.Length != renderedReasoning) { liveReasoning.Text = reasoningBuffer.ToString(); renderedReasoning = reasoningBuffer.Length; }
        if (clock.IsRunning) ElapsedText.Text = clock.Elapsed.TotalSeconds.ToString("F1") + "s";
        if (follow) ScrollEnd();
    }

    private void UpdateUsage()
    {
        InputTokens.Text = turnInput.ToString("N0"); OutputTokens.Text = turnOutput.ToString("N0"); CacheTokens.Text = turnCache.ToString("N0");
    }

    private void CompleteAssistant(JsonElement result)
    {
        FlushStreaming();
        if (reasoningPanel is not null) reasoningPanel.Header = "推理过程";
        string reply = Text(result, "reply");
        if (liveCard is not null && liveAnswer is not null)
        {
            liveCard.Children.Remove(liveAnswer); liveAnswer = null;
            liveCard.Children.Add(MessageRenderer.Create(reply, this));
            var actions = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
            actions.Children.Add(ActionButton("复制答复", () => Clipboard.SetText(reply)));
            latestRun = Text(result, "run_dir");
            actions.Children.Add(ActionButton("任务目录", () => OpenFolder(latestRun)));
            if (result.TryGetProperty("observations", out var observations))
                foreach (var observation in observations.EnumerateArray())
                    if (observation.TryGetProperty("data", out var data) && data.TryGetProperty("output", out var output))
                    {
                        string path = output.GetString() ?? "";
                        actions.Children.Add(ActionButton("添加到地图", async () => {
                            if (MessageBox.Show("将以下成果添加到当前地图？\n" + path, "GIS Agent · 本次地图修改", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No) != MessageBoxResult.Yes) return;
                            try { await ProContext.AddToMap(path); Notice("成果已添加到当前地图。"); } catch (Exception ex) { Notice(ex.Message); } }));
                    }
            liveCard.Children.Add(actions);
            var meta = new TextBlock { Text = Text(result, "model") + " · " + Text(result, "thinking") + " · " + Text(result, "elapsed_seconds") + "s", FontSize = 11, Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap };
            meta.SetResourceReference(TextBlock.ForegroundProperty, "Muted"); liveCard.Children.Add(meta);
        }
        sessionId = Text(result, "session_id");
        StatusText.Text = Text(result, "status") switch { "completed" => "任务完成 · 可继续追问", "cancelled" => "任务已停止", "denied" => "已拒绝本次操作", "approval_expired" => "授权等待已超时", "approval_required" => "操作需要单次授权", "blocked" => "等待允许创建成果", _ => "任务未完成 · 查看详情" };
        if (result.TryGetProperty("usage", out var usage)) { turnInput = Number(usage, "input"); turnOutput = Number(usage, "output"); turnCache = Number(usage, "cache_hit"); UpdateUsage(); }
        if (result.TryGetProperty("usage_available", out var available) && !available.GetBoolean()) InputTokens.Text = OutputTokens.Text = CacheTokens.Text = "—";
        if (result.TryGetProperty("total_usage", out var total)) InputTokens.ToolTip = OutputTokens.ToolTip = "本会话累计输入 " + Number(total, "input") + " / 输出 " + Number(total, "output");
        ElapsedText.Text = Text(result, "elapsed_seconds") + "s";
        export.AppendLine("## Agent\n\n" + reply + "\n\n> 模型 " + Text(result, "model") + " · 输入 " + turnInput + " / 输出 " + turnOutput + " tokens\n");
        cancelling = false; ScrollEnd();
    }

    private StackPanel AddCard(string role, bool user)
    {
        var stack = new StackPanel();
        var title = new TextBlock { Text = role + " · " + DateTime.Now.ToString("HH:mm"), FontSize = 11, FontWeight = FontWeights.Normal, Margin = new Thickness(0, 0, 0, 6), Opacity = 0.7 };
        title.SetResourceReference(TextBlock.ForegroundProperty, "Accent"); stack.Children.Add(title);
        var border = new Border { Child = stack, CornerRadius = new CornerRadius(10), BorderThickness = new Thickness(1), Padding = new Thickness(14), Margin = new Thickness(user ? 24 : 0, 0, user ? 0 : 24, 10) };
        border.SetResourceReference(Border.BackgroundProperty, user ? "AccentSoft" : "Surface"); border.SetResourceReference(Border.BorderBrushProperty, "Line");
        Transcript.Children.Add(border); Animate(border); ScrollEnd(); return stack;
    }

    private TextBox Selectable(string text, bool muted = false)
    {
        var box = new TextBox { Text = text, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Padding = new Thickness(0), Background = Brushes.Transparent, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, FontSize = muted ? 12 : 13 };
        box.SetResourceReference(ForegroundProperty, muted ? "Muted" : "Ink"); return box;
    }

    private Button ActionButton(string label, Action action)
    {
        var button = new Button { Content = label, FontSize = 12, Padding = new Thickness(8, 5, 8, 5), Margin = new Thickness(0, 0, 5, 5) };
        button.Click += (_, _) => action(); return button;
    }

    private void AddUser(string text, IEnumerable<string> paths)
    {
        var card = AddCard("YOU", true); card.Children.Add(Selectable(text));
        foreach (string path in paths) { var line = new TextBlock { Text = "附件 · " + Path.GetFileName(path), FontSize = 12, Margin = new Thickness(0, 7, 0, 0), TextWrapping = TextWrapping.Wrap }; line.SetResourceReference(TextBlock.ForegroundProperty, "Muted"); card.Children.Add(line); }
    }

    private StepRow BuildStepRow(StackPanel parent, int step, string tool, string argsJson)
    {
        var row = new StepRow { Step = step, Tool = tool, ArgsJson = argsJson };
        row.Status = new TextBlock { FontSize = 12, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
        SetStepState(row, "● 等待授权", "Muted");
        row.Elapsed = new TextBlock { FontSize = 11 };
        row.Elapsed.SetResourceReference(TextBlock.ForegroundProperty, "Muted");
        var head = new DockPanel();
        DockPanel.SetDock(row.Elapsed, Dock.Right);
        head.Children.Add(row.Elapsed); head.Children.Add(row.Status);
        row.Progress = new TextBlock { FontSize = 11, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 4, 0, 0) };
        row.Progress.SetResourceReference(TextBlock.ForegroundProperty, "Muted");
        row.DetailText = Selectable("", true);
        row.Details = new Expander { Header = "参数与结果", Content = row.DetailText, IsExpanded = false, FontSize = 12, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 6, 0, 0) };
        row.Details.SetResourceReference(ForegroundProperty, "Muted");
        var content = new StackPanel();
        content.Children.Add(head); content.Children.Add(row.Progress); content.Children.Add(row.Details);
        row.Border = new Border { Child = content, CornerRadius = new CornerRadius(8), Padding = new Thickness(10, 8, 10, 7), Margin = new Thickness(0, 0, 0, 5), BorderThickness = new Thickness(1) };
        row.Border.SetResourceReference(Border.BackgroundProperty, "Hover");
        row.Border.SetResourceReference(Border.BorderBrushProperty, "Line");
        parent.Children.Add(row.Border);
        return row;
    }

    private StepRow AddStepRow(int step, string tool, string argsJson)
    {
        if (liveSteps is null)
        {
            liveSteps = new StackPanel { Margin = new Thickness(0, 0, 0, 8), Visibility = Visibility.Collapsed };
            liveCard?.Children.Add(liveSteps);
        }
        liveSteps.Visibility = Visibility.Visible;
        return BuildStepRow(liveSteps, step, tool, argsJson);
    }

    private static void SetStepState(StepRow row, string state, string color)
    {
        row.Status.Text = "步骤 " + row.Step + " · " + row.Tool + " · " + state;
        row.Status.SetResourceReference(TextBlock.ForegroundProperty, color);
    }

    private void AddWelcome()
    {
        var card = AddCard("WORKSPACE", false);
        var heading = new TextBlock { Text = "GIS Workspace", FontSize = 16, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6), TextWrapping = TextWrapping.Wrap };
        card.Children.Add(heading); card.Children.Add(Selectable("数据检查、空间分析、地图制图。支持拖入图片和文件。", true));
        foreach (string prompt in new[] { "检查我的 ArcGIS 环境", "当前工程有哪些地图和图层？", "我想创建 500 米缓冲区，请先告诉我需要哪些数据。" })
        { var button = ActionButton(prompt, () => { PromptBox.Text = prompt; PromptBox.Focus(); }); button.HorizontalContentAlignment = HorizontalAlignment.Left; button.Margin = new Thickness(0, 8, 0, 0); button.Content = new TextBlock { Text = prompt, TextWrapping = TextWrapping.Wrap }; card.Children.Add(button); }
    }

    private void Notice(string text) { NoticeText.Text = text; NoticeText.Visibility = Visibility.Visible; }
    private bool IsFollowing() => TranscriptScroll.ScrollableHeight - TranscriptScroll.VerticalOffset < 50;
    private void ScrollEnd() => Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() => TranscriptScroll.ScrollToEnd()));
    private void Animate(FrameworkElement element)
    {
        if (MotionToggle.IsChecked != true || !SystemParameters.ClientAreaAnimation) return;
        element.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(180)));
        var translate = new TranslateTransform(); element.RenderTransform = translate;
        translate.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(7, 0, TimeSpan.FromMilliseconds(180)) { EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut } });
    }

    private async Task IdleOperation(Func<Task> action)
    {
        if (busy || operation) return;
        operation = true; SettingsContent.IsEnabled = false;
        try { await EnsureWorkerAsync(); await action(); }
        catch (Exception ex) { Notice(ex.Message); }
        finally { operation = false; SettingsContent.IsEnabled = true; }
    }

    private async void ApplySettings_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        var state = await RequestAsync(new { type = "configure", options = ReadOptions() }, "configured");
        PopulateOptions(state); SaveAppearance(); Notice("设置已保存，将用于下一次请求。");
    });

    private async void RefreshModels_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        Notice("正在获取账户可用模型…");
        var response = await RequestAsync(new { type = "models" }, "models", 130);
        string current = ModelCombo.Text;
        ModelCombo.Items.Clear(); foreach (var model in response.GetProperty("models").EnumerateArray()) ModelCombo.Items.Add(new ComboBoxItem { Content = model.GetString() });
        ModelCombo.Text = current; Notice("可用模型列表已更新。");
    });

    private async void Reset_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        var result = await RequestAsync(new { type = "reset" }, "reset");
        sessionId = Text(result, "session_id"); Transcript.Children.Clear(); export.Clear(); AddWelcome();
        answerBuffer.Clear(); reasoningBuffer.Clear(); liveAnswer = null; liveReasoning = null;
        liveSteps = null; activeStep = null;
        attachments.Clear(); RenderAttachments(); InputTokens.Text = OutputTokens.Text = CacheTokens.Text = ElapsedText.Text = "—";
        HistoryPanel.Visibility = Visibility.Collapsed; StatusText.Text = "新对话 · 准备就绪";
    });

    private sealed record ChatEntry(string Id, string Title);

    private sealed class StepRow
    {
        public Border Border = null!;
        public TextBlock Status = null!;
        public TextBlock Elapsed = null!;
        public TextBlock Progress = null!;
        public Expander Details = null!;
        public TextBox DetailText = null!;
        public string RequestId = "";
        public string ArgsJson = "";
        public int Step;
        public string Tool = "";
        public bool SawProgress;
        public readonly System.Text.StringBuilder Log = new();
    }
    private async void History_Click(object sender, RoutedEventArgs e) => await IdleOperation(async () =>
    {
        var response = await RequestAsync(new { type = "history" }, "history"); HistoryList.Items.Clear();
        foreach (var chat in response.GetProperty("chats").EnumerateArray()) HistoryList.Items.Add(new ChatEntry(Text(chat, "session_id"), Text(chat, "title")));
        HistoryPanel.Visibility = Visibility.Visible; if (HistoryList.Items.Count == 0) Notice("还没有保存的对话。完成第一条任务后会自动保存。");
    });
    private void CloseHistory_Click(object sender, RoutedEventArgs e) => HistoryPanel.Visibility = Visibility.Collapsed;
    private async void LoadHistory_Click(object sender, RoutedEventArgs e)
    {
        if (HistoryList.SelectedItem is not ChatEntry entry) return;
        await IdleOperation(async () =>
        {
            var result = await RequestAsync(new { type = "load", session_id = entry.Id }, "loaded");
            sessionId = entry.Id; Transcript.Children.Clear(); export.Clear();
            foreach (var turn in result.GetProperty("turns").EnumerateArray())
            {
                AddUser(Text(turn, "prompt"), Array.Empty<string>()); var card = AddCard("AGENT", false);
                string reasoning = Text(turn, "reasoning");
                if (!string.IsNullOrWhiteSpace(reasoning) && ShowReasoning.IsChecked == true)
                {
                    var panel = new Expander { Header = "推理过程", Content = Selectable(reasoning, true), Margin = new Thickness(0, 0, 0, 8) };
                    panel.SetResourceReference(ForegroundProperty, "Muted"); card.Children.Add(panel);
                }
                if (turn.TryGetProperty("observations", out var observations))
                {
                    var steps = new StackPanel { Margin = new Thickness(0, 0, 0, 10) };
                    int stepIndex = 0;
                    foreach (var observation in observations.EnumerateArray())
                    {
                        stepIndex++;
                        bool ok = observation.TryGetProperty("ok", out var good) && good.GetBoolean();
                        var row = BuildStepRow(steps, stepIndex, Text(observation, "tool"), "");
                        SetStepState(row, ok ? "✓ 完成" : "✗ 失败", ok ? "Accent" : "Ink");
                        row.Details.Visibility = Visibility.Visible;
                        row.Details.Header = "参数与结果";
                        row.DetailText.Text = observation.ToString();
                    }
                    if (stepIndex > 0) card.Children.Add(steps);
                }
                card.Children.Add(MessageRenderer.Create(Text(turn, "reply"), this));
                string path = Text(turn, "run_dir"); card.Children.Add(ActionButton("任务目录", () => OpenFolder(path)));
                export.AppendLine("## 你\n\n" + Text(turn, "prompt") + "\n\n## Agent\n\n" + Text(turn, "reply") + "\n");
            }
            HistoryPanel.Visibility = Visibility.Collapsed; Notice("对话已恢复；旧图片如需再次查看，请重新附加。"); StatusText.Text = "对话已恢复 · 可继续追问";
            if (result.TryGetProperty("total_usage", out var total)) { turnInput = Number(total, "input"); turnOutput = Number(total, "output"); turnCache = Number(total, "cache_hit"); UpdateUsage(); }
        });
    }

    private void Export_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new SaveFileDialog { Filter = "Markdown 对话|*.md", FileName = "GIS-Agent-" + DateTime.Now.ToString("yyyyMMdd-HHmm") + ".md" };
        if (dialog.ShowDialog() == true) { try { File.WriteAllText(dialog.FileName, "# GIS Agent 对话\n\n" + export, new UTF8Encoding(false)); Notice("对话已导出。"); } catch (Exception ex) { Notice(ex.Message); } }
    }
    private void OpenFolder(string path)
    {
        if (!Directory.Exists(path)) { Notice("任务目录不存在。"); return; }
        var start = new ProcessStartInfo("explorer.exe") { UseShellExecute = false }; start.ArgumentList.Add(path); Process.Start(start);
    }
    private void Attach_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Multiselect = true, Filter = "支持的附件|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.txt;*.md;*.csv;*.json;*.geojson;*.log;*.shp;*.gpkg;*.tif;*.tiff;*.aprx;*.lyrx;*.xlsx|所有文件|*.*" };
        if (dialog.ShowDialog() == true) AddAttachments(dialog.FileNames);
    }
    private void AddAttachments(IEnumerable<string> paths)
    {
        if (busy) return;
        foreach (string path in paths)
        {
            if (attachments.Contains(path)) continue;
            if (attachments.Count == 6) { Notice("每条消息最多添加 6 个附件。"); break; }
            attachments.Add(path);
        }
        RenderAttachments(); Notice("图片以视觉输入发送，文本发送内容，GIS 文件仅引用本地路径。可拖入 .gdb 目录。");
    }
    private void RenderAttachments()
    {
        AttachmentsPanel.Children.Clear();
        foreach (string path in attachments)
        {
            var content = new StackPanel { Orientation = Orientation.Horizontal };
            if (new[] { ".png", ".jpg", ".jpeg", ".webp", ".gif" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
            {
                try { var image = new BitmapImage(); image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad; image.DecodePixelWidth = 70; image.UriSource = new Uri(path); image.EndInit(); image.Freeze(); content.Children.Add(new Image { Source = image, Width = 30, Height = 30, Margin = new Thickness(0, 0, 6, 0) }); } catch (Exception) { }
            }
            content.Children.Add(new TextBlock { Text = Path.GetFileName(path), MaxWidth = 140, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center });
            var button = ActionButton("移除", () => { if (!busy) { attachments.Remove(path); RenderAttachments(); } }); button.ToolTip = "移除 " + Path.GetFileName(path); content.Children.Add(button);
            var border = new Border { Child = content, Padding = new Thickness(6), CornerRadius = new CornerRadius(6), Margin = new Thickness(0, 0, 5, 5), ToolTip = path }; border.SetResourceReference(BackgroundProperty, "Page"); AttachmentsPanel.Children.Add(border);
        }
    }
    private void Files_DragOver(object sender, DragEventArgs e) { if (e.Data.GetDataPresent(DataFormats.FileDrop)) { e.Effects = busy ? DragDropEffects.None : DragDropEffects.Copy; e.Handled = true; } }
    private void Files_Drop(object sender, DragEventArgs e) { if (e.Data.GetData(DataFormats.FileDrop) is string[] files) { AddAttachments(files); e.Handled = true; } }
    private void Prompt_TextChanged(object sender, TextChangedEventArgs e) { if (SendButton is not null) SendButton.ToolTip = PromptBox.Text.Length + " / 16000 字符 · Ctrl+Enter 发送"; }

    private void Theme_Click(object sender, RoutedEventArgs e) { dark = !dark; ApplyTheme(); SaveAppearance(); }
    private void ApplyTheme()
    {
        var colors = dark ? new[] { "#1C1F26", "#24272E", "#E0E2E6", "#8A8F9A", "#363940", "#4A9FD8", "#2A3545", "#2E3138", "#3D2E24" }
                          : new[] { "#F7F8FA", "#FFFFFF", "#1B2838", "#6B7B8D", "#E2E6EB", "#2B6CB0", "#EBF2FA", "#F0F2F5", "#FEF3EB" };
        var keys = new[] { "Page", "Surface", "Ink", "Muted", "Line", "Accent", "AccentSoft", "Hover", "Warning" };
        for (int i = 0; i < keys.Length; i++) Resources[keys[i]] = new SolidColorBrush((Color)ColorConverter.ConvertFromString(colors[i]));
        Resources["OnAccent"] = new SolidColorBrush((Color)ColorConverter.ConvertFromString(dark ? "#E8ECF0" : "#FFFFFF"));
    }
    private void LoadAppearance()
    {
        try { string path = Path.Combine(FindRoot(), "workspace", "appearance.json"); if (File.Exists(path)) { using var doc = JsonDocument.Parse(File.ReadAllText(path)); dark = doc.RootElement.GetProperty("dark").GetBoolean(); ShowReasoning.IsChecked = doc.RootElement.GetProperty("reasoning").GetBoolean(); MotionToggle.IsChecked = doc.RootElement.GetProperty("motion").GetBoolean(); } } catch (Exception) { }
        ApplyTheme();
    }
    private void SaveAppearance()
    {
        try { string folder = Path.Combine(FindRoot(), "workspace"); Directory.CreateDirectory(folder); File.WriteAllText(Path.Combine(folder, "appearance.json"), JsonSerializer.Serialize(new { dark, reasoning = ShowReasoning.IsChecked == true, motion = MotionToggle.IsChecked == true })); } catch (Exception) { }
    }
    private void StopWorker()
    {
        turnGeneration++;
        ClearApproval(); handledHostRequests.Clear();
        pending?.TrySetException(new IOException("后台连接已停止。"));
        var process = worker; worker = null;
        try { if (process is { HasExited: false }) process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
        process?.Dispose();
    }
}
