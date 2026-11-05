using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using System.Text.Json;

namespace ArcGISAgent;

internal static class ProContext
{
    internal static bool SupportsHostTools => true;
    internal static string ProjectPath() => Project.Current?.URI ?? "";
    internal static async Task AddToMap(string path)
    {
        await QueuedTask.Run(() =>
        {
            var map = MapView.Active?.Map ?? throw new InvalidOperationException("请先打开一个地图视图。");
            LayerFactory.Instance.CreateLayer(new Uri(path), map);
        });
    }

    internal static async Task<object> ExecuteAsync(string tool, JsonElement arguments, Func<bool> cancelled)
    {
        return await QueuedTask.Run<object>(() =>
        {
            if (cancelled()) throw new OperationCanceledException("任务已取消。");
            var project = Project.Current ?? throw new InvalidOperationException("请先打开 Pro 工程。");
            var maps = project.GetItems<MapProjectItem>().Take(50).Select(item => item.GetMap()).ToList();
            if (tool == "map_context")
                return new { project = project.URI, active_map_uri = MapView.Active?.Map?.URI ?? "",
                    maps = maps.Select(map => new { name = map.Name, map_uri = map.URI,
                        layers = map.GetLayersAsFlattenedList().Take(200).Select(layer => new {
                            name = layer.Name, layer_uri = layer.URI, visible = layer.IsVisible,
                            dataset = DatasetPath(layer), selection_supported = SupportsSelection(layer) }).ToArray(),
                        layers_truncated = map.GetLayersAsFlattenedList().Count > 200 }).ToArray(),
                    maps_truncated = project.GetItems<MapProjectItem>().Skip(50).Any() };
            string mapUri = arguments.GetProperty("map_uri").GetString() ?? "";
            var target = maps.SingleOrDefault(map => map.URI == mapUri)
                ?? throw new InvalidOperationException("目标地图已关闭或不属于当前工程；请重新获取 map_context。");
            if (cancelled()) throw new OperationCanceledException("任务已取消。");
            if (tool == "get_selection")
            {
                string layerUri = arguments.GetProperty("layer_uri").GetString() ?? "";
                var feature = target.GetLayersAsFlattenedList().SingleOrDefault(item => item.URI == layerUri) as FeatureLayer
                    ?? throw new InvalidOperationException("目标要素图层已移除或不属于指定地图。");
                if (!SupportsSelection(feature)) throw new InvalidOperationException("当前仅支持无连接的本地普通要素图层；服务、连接表或非要素图层请先另存为本地要素类。");
                using var selection = feature.GetSelection();
                if (selection.GetCount() > 100000) throw new InvalidOperationException("单次选择集最多支持 100000 个要素，请缩小选择范围后重试。");
                var ids = selection.GetObjectIDs().OrderBy(id => id).ToArray();
                if (ids.Length > 100000) throw new InvalidOperationException("选择集已变化且超过上限，请重新获取。");
                if (cancelled()) throw new OperationCanceledException("任务已取消。");
                return new { dataset = DatasetPath(feature), object_ids = ids, layer_name = feature.Name,
                    map_uri = target.URI, layer_uri = feature.URI };
            }
            if (tool == "add_to_map")
            {
                string path = arguments.GetProperty("dataset").GetString() ?? "";
                if (!System.IO.Path.IsPathFullyQualified(path)) throw new InvalidOperationException("数据必须使用绝对路径。");
                var layer = LayerFactory.Instance.CreateLayer(new Uri(path), target);
                return new { map_uri = target.URI, layer_uri = layer.URI, name = layer.Name, dataset = path, project_saved = false };
            }
            if (tool == "set_layer_visibility")
            {
                string layerUri = arguments.GetProperty("layer_uri").GetString() ?? "";
                var layer = target.GetLayersAsFlattenedList().SingleOrDefault(item => item.URI == layerUri)
                    ?? throw new InvalidOperationException("目标图层已移除或不属于指定地图。");
                bool visible = arguments.GetProperty("visible").GetBoolean();
                layer.SetVisibility(visible);
                return new { map_uri = target.URI, layer_uri = layer.URI, visible = layer.IsVisible, project_saved = false };
            }
            throw new InvalidOperationException("未支持的 Pro 宿主工具。");
        });
    }

    private static bool SupportsSelection(Layer layer)
    {
        if (layer is not FeatureLayer feature || DatasetPath(layer) is null) return false;
        try
        {
            // Joined/query data connections cannot safely map OIDs to the source dataset.
            var definition = feature.GetDefinition() as ArcGIS.Core.CIM.CIMFeatureLayer;
            return definition?.FeatureTable?.DataConnection is ArcGIS.Core.CIM.CIMStandardDataConnection;
        }
        catch (Exception) { return false; }
    }

    private static string? DatasetPath(Layer layer)
    {
        try
        {
            var path = layer is FeatureLayer feature ? feature.GetPath() : layer is RasterLayer raster ? raster.GetPath() : null;
            return path?.IsFile == true ? path.LocalPath : null;
        }
        catch (Exception) { return null; }
    }
}
