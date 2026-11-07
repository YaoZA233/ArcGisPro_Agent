using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;

namespace ArcGISAgent;

internal sealed record UpdateInfo(string Version, string ReleaseUrl, string PackageUrl, string Notes, string MinimumArcGisPro);

internal static class UpdateService
{
    internal static string CurrentVersion => typeof(UpdateService).Assembly.GetName().Version?.ToString(3) ?? "0.1.0";
    internal const string ManifestUrl = "https://raw.githubusercontent.com/YaoZA233/ArcGisPro_Agent/main/update.json";
    private static readonly string CacheFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ArcGISAgent", "update-check.json");
    private static readonly HttpClient Client = CreateClient();

    private sealed record CacheEntry(DateTimeOffset CheckedAt, UpdateInfo? Update);

    private static HttpClient CreateClient()
    {
        var client = new HttpClient { Timeout = TimeSpan.FromSeconds(4) };
        client.DefaultRequestHeaders.UserAgent.ParseAdd("ArcGISAgent-UpdateCheck/1.0");
        return client;
    }

    internal static async Task<UpdateInfo?> CheckAsync(CancellationToken cancellationToken = default)
    {
        CacheEntry? cached = ReadCache();
        if (cached is not null && DateTimeOffset.UtcNow - cached.CheckedAt < TimeSpan.FromHours(24)) return cached.Update;
        try
        {
            using var response = await Client.GetAsync(ManifestUrl, cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode) return null;
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken).ConfigureAwait(false);
            var root = document.RootElement;
            if (root.GetProperty("schema").GetInt32() != 1) return null;
            string version = Required(root, "version");
            if (!Version.TryParse(version, out var remote) || !Version.TryParse(CurrentVersion, out var local) || remote <= local)
            {
                WriteCache(null);
                return null;
            }
            string releaseUrl = Optional(root, "release_url");
            string packageUrl = Optional(root, "package_url");
            if (releaseUrl.Length == 0 && packageUrl.Length == 0) return null;
            var update = new UpdateInfo(version, releaseUrl, packageUrl, Optional(root, "notes"), Optional(root, "min_arcgis_pro"));
            WriteCache(update);
            return update;
        }
        catch (OperationCanceledException) { return null; }
        catch (HttpRequestException) { return null; }
        catch (JsonException) { return null; }
        catch (KeyNotFoundException) { return null; }
        catch (InvalidOperationException) { return null; }
    }

    internal static void Open(UpdateInfo update)
    {
        string url = update.ReleaseUrl.Length > 0 ? update.ReleaseUrl : update.PackageUrl;
        if (url.Length == 0) return;
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    private static string Required(JsonElement root, string name) => root.GetProperty(name).GetString() ?? "";
    private static string Optional(JsonElement root, string name) => root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";

    private static CacheEntry? ReadCache()
    {
        try
        {
            if (!File.Exists(CacheFile)) return null;
            return JsonSerializer.Deserialize<CacheEntry>(File.ReadAllText(CacheFile));
        }
        catch (JsonException) { return null; }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    private static void WriteCache(UpdateInfo? update)
    {
        try
        {
            string? directory = Path.GetDirectoryName(CacheFile);
            if (directory is null) return;
            Directory.CreateDirectory(directory);
            string temporary = CacheFile + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new CacheEntry(DateTimeOffset.UtcNow, update)));
            File.Move(temporary, CacheFile, true);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
