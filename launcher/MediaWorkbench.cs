// Desktop lifecycle adapted from the user's MediaFlow WinForms/WebView2 launcher.
// The product identity, service lifecycle and update integration are independent.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Velopack;

[assembly: AssemblyTitle("Media Deep Researcher")]
[assembly: AssemblyProduct("Media Deep Researcher")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName=".NET Framework 4.8")]
internal static class Program {
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal static readonly string Data = Environment.GetEnvironmentVariable("MEDIAWORKBENCH_DATA_DIR") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MediaWorkbench", "data");
    internal static readonly string Instance = Environment.GetEnvironmentVariable("MEDIAWORKBENCH_INSTANCE") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MediaWorkbench", "instance.json");
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    [STAThread]
    private static int Main(string[] args) {
        VelopackApp.Build().Run();
        if(Array.IndexOf(args,"--quit")>=0){try{EventWaitHandle.OpenExisting("Local\\MediaWorkbench.Desktop.Exit").Set();}catch{}return 0;}
        bool owner;
        using (var mutex = new Mutex(true, "Local\\MediaWorkbench.Desktop.Singleton", out owner)) {
            if (!owner) { try { EventWaitHandle.OpenExisting("Local\\MediaWorkbench.Desktop.Activate").Set(); } catch {} return 0; }
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            using (var activate = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\MediaWorkbench.Desktop.Activate"))
            using (var form = new WorkbenchForm(activate)) Application.Run(form);
        }
        return 0;
    }
    internal static Dictionary<string,object> ReadInstance() {
        var value = Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Instance,Encoding.UTF8));
        Uri uri = new Uri((string)value["base_url"]);
        if ((string)value["product"] != "MediaWorkbench" || uri.Scheme != "http" || !uri.IsLoopback) throw new InvalidDataException("Invalid local instance");
        return value;
    }
    internal static HttpClient Client(Dictionary<string,object> instance) {
        var client = new HttpClient(new HttpClientHandler { UseProxy=false });
        client.Timeout=TimeSpan.FromSeconds(8); client.BaseAddress=new Uri((string)instance["base_url"]);
        client.DefaultRequestHeaders.Authorization=new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer",(string)instance["token"]);
        return client;
    }
    internal static void Open(string target) { Process.Start(new ProcessStartInfo {FileName=target,UseShellExecute=true}); }
}
internal sealed partial class WorkbenchForm : Form {
    private readonly WebView2 web = new WebView2();
    private readonly Label message = new Label();
    private readonly NotifyIcon tray;
    private readonly EventWaitHandle activate;
    private readonly System.Windows.Forms.Timer timer;
    private readonly EventWaitHandle quit = new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\MediaWorkbench.Desktop.Exit");
    private Dictionary<string,object> instance;
    private Process backend;
    private bool exiting;
    private bool closing;
    private StreamWriter log;
    public WorkbenchForm(EventWaitHandle activation) {
        activate=activation; Text="Media Deep Researcher"; Width=1280; Height=850; MinimumSize=new Size(760,540); StartPosition=FormStartPosition.CenterScreen; AutoScaleMode=AutoScaleMode.Dpi;
        Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        message.Text="正在启动Media Deep Researcher…";message.Dock=DockStyle.Fill;message.TextAlign=ContentAlignment.MiddleCenter;message.Font=new Font("Microsoft YaHei",12);
        Controls.Add(message);web.Dock=DockStyle.Fill;
        var menu=new ContextMenuStrip();menu.Items.Add("打开Media Deep Researcher",null,delegate{Restore();});menu.Items.Add("检查更新",null,async delegate{await CheckUpdates();});menu.Items.Add("打开数据目录",null,delegate{Program.Open(Program.Data);});menu.Items.Add(new ToolStripSeparator());menu.Items.Add("退出并保存进度",null,async delegate{await ExitApp();});
        tray=new NotifyIcon{Icon=Icon,Text="Media Deep Researcher · 后台运行中",Visible=true,ContextMenuStrip=menu};tray.DoubleClick+=delegate{Restore();};
        timer=new System.Windows.Forms.Timer{Interval=300};timer.Tick+=async delegate{if(activate.WaitOne(0))Restore();if(quit.WaitOne(0))await ExitApp();};timer.Start();
        Shown+=async delegate{await StartApp();};
        FormClosing+=delegate(object sender,FormClosingEventArgs e){if(!exiting){e.Cancel=true;Hide();tray.ShowBalloonTip(2000,"Media Deep Researcher","已进入托盘，后台任务继续运行。",ToolTipIcon.Info);}};
        FormClosed+=delegate{timer.Stop();quit.Dispose();tray.Dispose();if(log!=null){lock(log){log.Dispose();log=null;}}};
    }
    private void Restore(){Show();WindowState=FormWindowState.Normal;Activate();}
    private async Task<bool> Ready() {
        try {
            var candidate=Program.ReadInstance();
            using(var client=Program.Client(candidate)) {
                var response=await client.GetAsync("/api/v1/health");response.EnsureSuccessStatusCode();
                var value=Program.Json.Deserialize<Dictionary<string,object>>(await response.Content.ReadAsStringAsync());
                if((string)value["product"]!="MediaWorkbench")return false;
                instance=candidate;return true;
            }
        } catch{return false;}
    }
    private async Task StartApp() {
        try {
            Directory.CreateDirectory(Program.Data);
            if(!await Ready()) {
                string python=Path.Combine(Program.Root,"runtime","python","python.exe");
                if(!File.Exists(python))throw new FileNotFoundException("软件运行环境缺失，请重新安装完整版本。",python);
                Directory.CreateDirectory(Path.Combine(Program.Data,"logs"));log=new StreamWriter(Path.Combine(Program.Data,"logs","host.log"),true,Encoding.UTF8){AutoFlush=true};
                var info=new ProcessStartInfo {FileName=python,Arguments="-m workbench --data-dir \""+Program.Data+"\"",WorkingDirectory=Program.Root,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,RedirectStandardOutput=true,RedirectStandardError=true};
                info.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";info.EnvironmentVariables["PYTHONUNBUFFERED"]="1";info.EnvironmentVariables["MEDIAWORKBENCH_INSTANCE"]=Program.Instance;
                info.EnvironmentVariables["PYTHONUTF8"]="1";
                info.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"]=Path.Combine(Program.Root,"runtime","browsers");
                info.EnvironmentVariables["PATH"]=Path.Combine(Program.Root,"runtime","node")+";"+Environment.GetEnvironmentVariable("PATH");
                backend=new Process{StartInfo=info};backend.OutputDataReceived+=Log;backend.ErrorDataReceived+=Log;backend.Start();backend.BeginOutputReadLine();backend.BeginErrorReadLine();
                for(int i=0;i<100&&!await Ready();i++){if(backend.HasExited)throw new Exception("后台启动失败，请查看数据目录中的 logs/host.log。");await Task.Delay(250);}
                if(instance==null)throw new Exception("后台未能及时就绪，请重新启动并查看日志。");
            }
            bool needsRuntime=false;
            try { CoreWebView2Environment.GetAvailableBrowserVersionString(); }
            catch(WebView2RuntimeNotFoundException) { needsRuntime=true; }
            if(needsRuntime) {
                message.Text="正在准备内置浏览器运行环境…";
                string installer=Path.Combine(Program.Root,"runtime","WebView2RuntimeInstallerX64.exe");
                using(var process=Process.Start(new ProcessStartInfo{FileName=installer,Arguments="/silent /install",UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden}))await Task.Run(()=>process.WaitForExit());
            }
            var environment=await CoreWebView2Environment.CreateAsync(null,Path.Combine(Program.Data,"webview"));
            Controls.Add(web);web.BringToFront();await web.EnsureCoreWebView2Async(environment);
            web.CoreWebView2.Settings.IsStatusBarEnabled=false;
            InitializeUpdates();
            web.CoreWebView2.NewWindowRequested+=delegate(object sender,CoreWebView2NewWindowRequestedEventArgs e){e.Handled=true;Uri target;if(Uri.TryCreate(e.Uri,UriKind.Absolute,out target)&&(target.Scheme=="https"||target.Scheme=="http"))Program.Open(e.Uri);};
            web.CoreWebView2.NavigationStarting+=delegate(object sender,CoreWebView2NavigationStartingEventArgs e){Uri target;if(Uri.TryCreate(e.Uri,UriKind.Absolute,out target)&&target.Scheme!="about"&&!e.Uri.StartsWith((string)instance["base_url"]+"/",StringComparison.OrdinalIgnoreCase)){e.Cancel=true;if(target.Scheme=="https"||target.Scheme=="http")Program.Open(e.Uri);}};
            web.Source=new Uri((string)instance["base_url"]+"/");message.Visible=false;
            await AutomaticUpdateCheck();
        }catch(Exception ex){message.Visible=true;message.BringToFront();message.Text="启动未完成\n\n"+ex.Message+"\n\n可从托盘打开数据目录查看日志，或退出后重新启动。";}
    }
    private void Log(object sender,DataReceivedEventArgs e){var writer=log;if(e.Data!=null&&writer!=null){lock(writer){if(log!=null)writer.WriteLine(e.Data);}}}
    private async Task StopHost(){
        if(instance==null)return;
        try{using(var client=Program.Client(instance)){await client.PostAsync("/api/v1/host/stop",new StringContent("{}",Encoding.UTF8,"application/json"));}}catch{}
        for(int i=0;i<120;i++){
            bool alive=await Ready();
            if(!alive&&(backend==null||backend.HasExited))return;
            await Task.Delay(250);
        }
        throw new Exception("后台尚未退出，已暂停关闭或更新。请稍候再试，数据仍保留。");
    }
    private async Task ExitApp(){if(closing)return;closing=true;try{await StopHost();exiting=true;Close();}catch(Exception ex){closing=false;MessageBox.Show(ex.Message,"Media Deep Researcher");}}
}
