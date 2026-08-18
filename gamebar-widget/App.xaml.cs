using System;
using System.IO;
using Windows.ApplicationModel;
using Windows.ApplicationModel.Activation;
using Windows.Storage;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Navigation;
using Microsoft.Gaming.XboxGameBar;

namespace ValorantTranslator
{
    sealed partial class App : Application
    {
        private const string WidgetExtensionId = "ValorantTranslatorWidget";
        private XboxGameBarWidget _widget = null;

        public App()
        {
            this.InitializeComponent();
            this.Suspending += OnSuspending;
            Log("App constructed");
        }

        protected override void OnActivated(IActivatedEventArgs args)
        {
            Log("OnActivated kind=" + args.Kind);
            XboxGameBarWidgetActivatedEventArgs widgetArgs = null;

            try
            {
                if (args.Kind == ActivationKind.Protocol)
                {
                    var protocolArgs = args as IProtocolActivatedEventArgs;
                    Log(
                        "Protocol uri="
                        + (protocolArgs == null ? "<null>" : protocolArgs.Uri.ToString()));
                    string scheme = protocolArgs == null || protocolArgs.Uri == null
                        ? string.Empty
                        : protocolArgs.Uri.Scheme;
                    if (scheme.Equals("ms-gamebarwidget", StringComparison.OrdinalIgnoreCase))
                    {
                        widgetArgs = args as XboxGameBarWidgetActivatedEventArgs;
                    }
                }

                if (widgetArgs == null)
                {
                    Log("Activation is not a Game Bar widget activation");
                    base.OnActivated(args);
                    return;
                }

                Log(
                    "Widget activation extension=" + widgetArgs.AppExtensionId
                    + ", launch=" + widgetArgs.IsLaunchActivation);

                // Align with the Game Bar lifecycle used by the reference
                // implementation: repeated activations only foreground the
                // existing widget. The bridge is owned by SubtitleWidget and
                // must not be started again from the activation callback.
                if (!widgetArgs.IsLaunchActivation
                    || !string.Equals(
                        widgetArgs.AppExtensionId,
                        WidgetExtensionId,
                        StringComparison.OrdinalIgnoreCase))
                {
                    Log("Existing or unknown widget activation; activating current window");
                    Window.Current.Activate();
                    return;
                }

                var rootFrame = CreateRootFrame();
                Window.Current.Content = rootFrame;

                _widget = new XboxGameBarWidget(
                    widgetArgs,
                    Window.Current.CoreWindow,
                    rootFrame);
                Window.Current.Closed += WidgetWindow_Closed;
                rootFrame.Navigate(typeof(SubtitleWidget), _widget);

                Window.Current.Activate();
                Log("Widget window activated");
            }
            catch (Exception ex)
            {
                Log("OnActivated failed: " + ex);
                throw;
            }
        }

        private Frame CreateRootFrame()
        {
            var rootFrame = Window.Current.Content as Frame;
            if (rootFrame == null)
            {
                rootFrame = new Frame();
                rootFrame.NavigationFailed += OnNavigationFailed;
            }

            return rootFrame;
        }

        private void WidgetWindow_Closed(
            object sender,
            Windows.UI.Core.CoreWindowEventArgs e)
        {
            _widget = null;
            Window.Current.Closed -= WidgetWindow_Closed;
            Log("Widget window closed");
        }

        protected override void OnLaunched(LaunchActivatedEventArgs e)
        {
            // 参考 Game Bar 小组件的标准分流：普通前台启动和 Game Bar
            // 的协议激活是两条路径。Sage 没有独立的 UWP 设置页，因此
            // 普通启动必须立即退出，避免把启动图留成一个大窗口；真正的
            // 小组件只通过 OnActivated 创建。
            Log("OnLaunched prelaunch=" + e.PrelaunchActivated);

            if (e.PrelaunchActivated)
            {
                return;
            }

            try
            {
                Log("Normal launch ignored; Sage is widget-only");
                Application.Current.Exit();
            }
            catch (Exception ex)
            {
                Log("Normal launch exit failed: " + ex);
            }
        }

        void OnNavigationFailed(object sender, NavigationFailedEventArgs e)
        {
            throw new Exception("Failed to load Page " + e.SourcePageType.FullName);
        }

        private void OnSuspending(object sender, SuspendingEventArgs e)
        {
            var deferral = e.SuspendingOperation.GetDeferral();
            _widget = null;
            Log("App suspending");
            deferral.Complete();
        }

        internal static void Log(string message)
        {
            try
            {
                string path = Path.Combine(
                    ApplicationData.Current.LocalFolder.Path,
                    "sage-widget.log");
                File.AppendAllText(
                    path,
                    string.Format(
                        "[{0:O}] {1}{2}",
                        DateTime.UtcNow,
                        message,
                        Environment.NewLine));
            }
            catch
            {
            }
        }
    }
}
