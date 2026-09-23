using System;
using System.IO;
using System.Security.Cryptography;
using Velopack;
using Velopack.Sources;
using Velopack.Locators;
using Velopack.Logging;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Text;
using Newtonsoft.Json;

internal sealed class FixtureDownloader : IFileDownloader {
    internal VelopackAsset Asset;
    internal bool Offline;
    internal bool Corrupt;
    internal string RequestedChannel;
    public Task<string> DownloadString(string url,IDictionary<string,string> headers,double timeout){
        if(Offline)throw new IOException("Synthetic network failure");
        if(url.Contains("api.github.com"))return Task.FromResult(JsonConvert.SerializeObject(new object[]{
            new {tag_name="v2026.07.29",name="Old learning release",prerelease=false,published_at="2026-07-29T00:00:00Z",assets=new object[0]},
            new {tag_name="v0.2.1",name="New preview",prerelease=true,published_at="2026-09-23T00:00:00Z",assets=new[]{
                new {name="releases.win-preview.json",browser_download_url="https://example.test/releases.win-preview.json",url="https://example.test/releases.win-preview.json"},
                new {name=Asset.FileName,browser_download_url="https://example.test/"+Asset.FileName,url="https://example.test/"+Asset.FileName}
            }}
        }));
        RequestedChannel=url;
        return Task.FromResult(JsonConvert.SerializeObject(new {Assets=new[]{new {PackageId=Asset.PackageId,Version=Asset.Version.ToString(),Type="Full",FileName=Asset.FileName,Size=Asset.Size,SHA1=Asset.SHA1,SHA256=Asset.SHA256}}}));
    }
    public async Task<byte[]> DownloadBytes(string url,IDictionary<string,string> headers,double timeout){return Encoding.UTF8.GetBytes(await DownloadString(url,headers,timeout));}
    public Task DownloadFile(string url,string target,Action<int> progress,IDictionary<string,string> headers,double timeout,CancellationToken cancel){
        if(Offline)throw new IOException("Synthetic download failure");
        File.WriteAllBytes(target,Corrupt?new byte[]{4,5,6}:new byte[]{1,2,3});
        if(progress!=null)progress(100);
        return Task.FromResult(0);
    }
}

internal static class UpdateChecks {
    private static int count;
    private static void Require(bool value){count++;if(!value)throw new Exception("Update check failed #"+count);}
    public static int Main(){
        try{Run();return 0;}
        catch(Exception error){for(var e=error;e!=null;e=e.InnerException)Console.WriteLine(e.GetType().FullName+": "+e.Message);return 1;}
    }
    private static void Run(){
        Require(UpdatePolicy.IsLocalPage("http://127.0.0.1:1234/#manage/settings","http://127.0.0.1:1234"));
        Require(!UpdatePolicy.IsLocalPage("https://evil.example/","http://127.0.0.1:1234"));
        Require(!UpdatePolicy.IsLocalPage("http://127.0.0.1:4567/","http://127.0.0.1:1234"));
        Require(!UpdatePolicy.IsLocalPage("http://127.0.0.1.evil.example:1234/","http://127.0.0.1:1234"));
        var current=SemanticVersion.Parse("0.2.0");
        var asset=new VelopackAsset{PackageId="MediaWorkbench.Desktop",Version=SemanticVersion.Parse("0.2.1"),Type=VelopackAssetType.Full,FileName="MediaWorkbench.Desktop-0.2.1-full.nupkg",Size=3,SHA256=new string('a',64)};
        Require(UpdatePolicy.Accept(asset,current));
        asset.PackageId="old-learning-edition";Require(!UpdatePolicy.Accept(asset,current));asset.PackageId="MediaWorkbench.Desktop";
        asset.Version=SemanticVersion.Parse("0.1.1");Require(!UpdatePolicy.Accept(asset,current));
        asset.Version=current;Require(!UpdatePolicy.Accept(asset,current));asset.Version=SemanticVersion.Parse("0.2.1");
        asset.FileName="../outside-full.nupkg";Require(!UpdatePolicy.Accept(asset,current));asset.FileName="good-full.nupkg";
        asset.SHA256="";Require(!UpdatePolicy.Accept(asset,current));
        string file=Path.GetTempFileName();
        try {
            File.WriteAllBytes(file,new byte[]{1,2,3});
            using(var sha=SHA256.Create())asset.SHA256=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(file))).Replace("-","");
            UpdatePolicy.VerifyFile(file,asset);count++;
            File.WriteAllBytes(file,new byte[]{1,2,4});
            bool rejected=false;try{UpdatePolicy.VerifyFile(file,asset);}catch(InvalidDataException){rejected=true;}
            Require(rejected);
        }finally{File.Delete(file);}
        FeedChecks();
        Console.WriteLine("Desktop update checks passed: "+count);
    }
    private static void FeedChecks(){
        string folder=Path.Combine(Path.GetTempPath(),"workbench-update-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        try {
            var asset=new VelopackAsset{PackageId="MediaWorkbench.Desktop",Version=SemanticVersion.Parse("0.2.1"),Type=VelopackAssetType.Full,FileName="MediaWorkbench.Desktop-0.2.1-full.nupkg",Size=3};
            using(var hash=SHA256.Create())asset.SHA256=BitConverter.ToString(hash.ComputeHash(new byte[]{1,2,3})).Replace("-","");
            using(var hash=SHA1.Create())asset.SHA1=BitConverter.ToString(hash.ComputeHash(new byte[]{1,2,3})).Replace("-","");
            var transport=new FixtureDownloader{Asset=asset};
            var source=new WorkbenchGithubSource(transport);
            Require(System.Net.ServicePointManager.SecurityProtocol==System.Net.SecurityProtocolType.SystemDefault);
            var locator=new TestVelopackLocator("MediaWorkbench.Desktop","0.2.0",folder,null);
            var manager=new UpdateManager(source,new UpdateOptions{ExplicitChannel=UpdatePolicy.Channel,AllowVersionDowngrade=false},locator);
            var candidate=manager.CheckForUpdatesAsync().GetAwaiter().GetResult();
            Require(candidate!=null && candidate.TargetFullRelease.Version==asset.Version);
            Require(transport.RequestedChannel.EndsWith("releases.win-preview.json"));
            transport.Offline=true;
            bool rejected=false;try{manager.CheckForUpdatesAsync().GetAwaiter().GetResult();}catch{rejected=true;}
            Require(rejected);transport.Offline=false;
            transport.Corrupt=true;rejected=false;
            try{manager.DownloadUpdatesAsync(candidate).GetAwaiter().GetResult();}catch{rejected=true;}
            Require(rejected);
            transport.Corrupt=false;
            manager.DownloadUpdatesAsync(candidate).GetAwaiter().GetResult();
            UpdatePolicy.VerifyFile(Path.Combine(folder,asset.FileName),asset);count++;
            asset.Version=SemanticVersion.Parse("0.1.0");
            Require(manager.CheckForUpdatesAsync().GetAwaiter().GetResult()==null);
        }finally{Directory.Delete(folder,true);}
    }
}
