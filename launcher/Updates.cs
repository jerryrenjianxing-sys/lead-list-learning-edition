using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Security.Cryptography;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using Velopack;
using Velopack.Sources;

internal static class UpdatePolicy {
    internal const string Repository = "https://github.com/jerryrenjianxing-sys/lead-list-learning-edition";
    internal const string Channel = "win-preview";
    internal static bool IsLocalPage(string source, string baseUrl) {
        Uri a,b;
        return Uri.TryCreate(source,UriKind.Absolute,out a) && Uri.TryCreate(baseUrl,UriKind.Absolute,out b)
            && a.Scheme=="http" && a.IsLoopback && a.Scheme==b.Scheme && a.Host==b.Host && a.Port==b.Port;
    }
    internal static bool Accept(VelopackAsset asset, SemanticVersion current) {
        return asset!=null && asset.PackageId=="MediaWorkbench.Desktop" && asset.Version>current
            && asset.Type==VelopackAssetType.Full && asset.Size>0
            && !String.IsNullOrWhiteSpace(asset.SHA256)
            && Path.GetFileName(asset.FileName)==asset.FileName && asset.FileName.EndsWith("-full.nupkg",StringComparison.OrdinalIgnoreCase);
    }
    internal static void VerifyFile(string path,VelopackAsset asset) {
        using(var file=File.OpenRead(path))using(var sha=SHA256.Create()){
            string hash=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","");
            if(file.Length!=asset.Size || !String.Equals(hash,asset.SHA256,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Update checksum mismatch");
        }
    }
}

internal sealed class WorkbenchGithubSource : GithubSource {
    internal WorkbenchGithubSource(IFileDownloader downloader=null) : base(UpdatePolicy.Repository,null,true,downloader) {}
    protected override async Task<GithubRelease[]> GetReleases(bool includePrereleases) {
        var releases=await base.GetReleases(includePrereleases);
        return releases.Where(r=>r.Assets!=null && r.Assets.Any(a=>a.Name=="releases."+UpdatePolicy.Channel+".json"))
            .OrderByDescending(r=>r.PublishedAt).ToArray();
    }
}

internal sealed class CheckedUpdateManager : UpdateManager {
    internal CheckedUpdateManager() : base(new WorkbenchGithubSource(),new UpdateOptions{ExplicitChannel=UpdatePolicy.Channel,AllowVersionDowngrade=false}) {}
    internal Task Verify(VelopackAsset asset) {
        return Task.Run(delegate {
            string path=Path.Combine(Locator.PackagesDir,asset.FileName);
            UpdatePolicy.VerifyFile(path,asset);
        });
    }
}

internal sealed partial class WorkbenchForm {
    private CheckedUpdateManager updater;
    private UpdateInfo availableUpdate;
    private bool updateBusy;
    private bool updatesInitialized;
    private bool downloaded;
    private Dictionary<string,object> updateState = new Dictionary<string,object>{{"state","idle"},{"message","可检查是否有新版本。"}};

    private void SendUpdateState() {
        if(IsDisposed || !IsHandleCreated)return;
        if(InvokeRequired){BeginInvoke((Action)SendUpdateState);return;}
        if(web.CoreWebView2!=null)web.CoreWebView2.PostWebMessageAsJson(Program.Json.Serialize(new {type="workbench-update",value=updateState}));
    }
    private void UpdateState(string state,string text,int progress=0) {
        if(InvokeRequired){BeginInvoke((Action)(()=>UpdateState(state,text,progress)));return;}
        updateState=new Dictionary<string,object>{{"state",state},{"message",text},{"progress",progress}};
        if(availableUpdate!=null){var a=availableUpdate.TargetFullRelease;updateState["version"]=a.Version.ToString();updateState["size"]=a.Size;updateState["notes"]=a.NotesMarkdown??"";}
        SendUpdateState();
    }
    private void InitializeUpdates() {
        if(updatesInitialized)return;
        updatesInitialized=true;
        updater=new CheckedUpdateManager();
        web.CoreWebView2.WebMessageReceived+=async delegate(object sender,Microsoft.Web.WebView2.Core.CoreWebView2WebMessageReceivedEventArgs e){
            if(instance==null || !UpdatePolicy.IsLocalPage(e.Source,(string)instance["base_url"]))return;
            try {
                var value=Program.Json.Deserialize<Dictionary<string,object>>(e.WebMessageAsJson);
                if(!value.ContainsKey("type") || Convert.ToString(value["type"])!="workbench-update" || !value.ContainsKey("action"))return;
                string action=Convert.ToString(value["action"]);
                if(action=="status")SendUpdateState();
                else if(action=="check")await CheckUpdateCore(false);
                else if(action=="download")await DownloadUpdate();
                else if(action=="install")await InstallUpdate();
            }catch{UpdateState("error","更新操作未完成，请重试或打开 GitHub 发行页。");}
        };
    }
    private async Task AutomaticUpdateCheck() {
        string file=Path.Combine(Program.Data,"update-check.txt");
        DateTime previous;
        if(File.Exists(file) && DateTime.TryParse(File.ReadAllText(file),null,System.Globalization.DateTimeStyles.RoundtripKind,out previous)
            && DateTime.UtcNow-previous.ToUniversalTime()<TimeSpan.FromHours(24))return;
        await CheckUpdateCore(true);
    }
    private async Task CheckUpdates() {
        Restore();
        if(web.CoreWebView2!=null && instance!=null)web.CoreWebView2.Navigate((string)instance["base_url"]+"/#manage/settings");
        await CheckUpdateCore(false);
    }
    private async Task CheckUpdateCore(bool automatic) {
        if(updateBusy || updater==null)return;
        updateBusy=true;
        try {
            if(!updater.IsInstalled){UpdateState("unavailable","当前为源码或未安装版本，请从 GitHub 下载完整安装包。");return;}
            File.WriteAllText(Path.Combine(Program.Data,"update-check.txt"),DateTime.UtcNow.ToString("O"));
            UpdateState("checking","正在检查 GitHub 预览版本…");
            var candidate=await updater.CheckForUpdatesAsync();
            if(candidate==null){availableUpdate=null;downloaded=false;UpdateState("current","当前已是此通道最新版本。");return;}
            if(!UpdatePolicy.Accept(candidate.TargetFullRelease,updater.CurrentVersion) || candidate.IsDowngrade)throw new InvalidDataException("Unexpected update package");
            availableUpdate=candidate;
            downloaded=false;
            var pending=updater.UpdatePendingRestart;
            if(pending!=null && pending.Version==candidate.TargetFullRelease.Version){
                await updater.Verify(candidate.TargetFullRelease);
                downloaded=true;
            }
            UpdateState(downloaded?"downloaded":"available",downloaded?"更新已下载，可在任务空闲时安装。":"发现新版本，点击下载后再安装。");
            if(automatic)tray.ShowBalloonTip(4000,"Media Deep Researcher 有新版本","在设置 → 软件更新中查看并下载。",ToolTipIcon.Info);
        }catch(Exception ex){UpdateState("error","暂时无法检查更新（"+ex.GetType().Name+"）。本地功能仍可使用，请重试或打开发行页。");}
        finally{updateBusy=false;}
    }
    private async Task DownloadUpdate() {
        if(updateBusy || availableUpdate==null)return;
        updateBusy=true;
        try {
            UpdateState("downloading","正在下载更新…");
            await updater.DownloadUpdatesAsync(availableUpdate,p=>UpdateState("downloading","正在下载更新…",p));
            await updater.Verify(availableUpdate.TargetFullRelease);
            downloaded=true;
            UpdateState("downloaded","更新已下载并校验，点击安装并重启。");
        }catch(Exception ex){downloaded=false;UpdateState("available","下载或校验未完成（"+ex.GetType().Name+"），可重新下载。当前版本未改变。");}
        finally{updateBusy=false;}
    }
    private async Task InstallUpdate() {
        if(updateBusy || !downloaded || availableUpdate==null || instance==null)return;
        updateBusy=true;
        bool prepared=false;
        bool recover=false;
        try {
            await updater.Verify(availableUpdate.TargetFullRelease);
            using(var client=Program.Client(instance)){
                var response=await client.PostAsync("/api/v1/host/prepare-update",new StringContent("{}",Encoding.UTF8,"application/json"));
                if(response.StatusCode==System.Net.HttpStatusCode.Conflict){UpdateState("waiting_idle","采集或自检尚未结束。更新已保存，空闲后点击安装。");return;}
                response.EnsureSuccessStatusCode();prepared=true;
            }
            UpdateState("installing","已备份数据，正在退出后台并安装更新…");
            await StopHost();
            exiting=true;tray.Visible=false;
            updater.ApplyUpdatesAndRestart(availableUpdate.TargetFullRelease);
        }catch(Exception ex){
            exiting=false;tray.Visible=true;
            UpdateState("downloaded","安装未完成（"+ex.GetType().Name+"），正在恢复当前版本。");
            recover=prepared;
        }finally{updateBusy=false;}
        if(recover){
            try{await StopHost();}catch{}
            instance=null;backend=null;
            if(log!=null){lock(log){log.Dispose();log=null;}}
            await StartApp();
        }
    }
}
