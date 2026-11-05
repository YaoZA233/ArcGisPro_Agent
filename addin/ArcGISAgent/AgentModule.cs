using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace ArcGISAgent;

internal sealed class AgentModule : Module { }

internal sealed class OpenAgentButton : Button
{
    protected override void OnClick() => FrameworkApplication.DockPaneManager.Find("ArcGISAgent_DockPane")?.Activate();
}

internal sealed class AgentDockPane : DockPane { }
