using System;
using System.Threading.Tasks;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Navigation;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Media;
using Microsoft.Gaming.XboxGameBar;
using ValorantTranslator.Services;

namespace ValorantTranslator
{
    public sealed partial class SubtitleWidget : Page
    {
        private XboxGameBarWidget _widget;
        private XboxGameBarWidgetActivity _activity;
        private SubtitleServiceClient _serviceClient;
        private DispatcherTimer _expiryTimer;
        private bool _serviceLaunchRequested;
        public SubtitleStore Store { get; } = new SubtitleStore();

        public SubtitleWidget()
        {
            this.InitializeComponent();
        }

        protected override void OnNavigatedTo(NavigationEventArgs e)
        {
            base.OnNavigatedTo(e);
            App.Log("SubtitleWidget OnNavigatedTo");
            _widget = e.Parameter as XboxGameBarWidget;

            if (_widget != null)
            {
                // 订阅状态变化（规格第 7 节）
                _widget.PinnedChanged += OnPinnedChanged;
                _widget.ClickThroughEnabledChanged += OnClickThroughChanged;
                _widget.GameBarDisplayModeChanged += OnDisplayModeChanged;
                _widget.VisibleChanged += OnVisibleChanged;
                _widget.WindowStateChanged += OnWindowStateChanged;
                _widget.RequestedOpacityChanged += OnOpacityChanged;
                ApplyRequestedOpacity();

                // 长时间字幕会话 activity（规格第 10 节）
                try
                {
                    _activity = new XboxGameBarWidgetActivity(_widget, "sage-live-translation");
                }
                catch
                {
                    // Activity 只用于提升长时间会话的可靠性；不应因此阻止字幕显示。
                    _activity = null;
                }

                UpdateVisualState();
            }

            // 与参考项目一致：小组件通过本机 HTTP 长轮询连接包内全信任服务。
            _serviceClient = new SubtitleServiceClient(Store, OnStatusChanged);
            Store.Changed += OnStoreChanged;
            UpdateSubtitleSections();
            _serviceClient.Start();
            App.Log("SubtitleServiceClient started; requesting local service");
            _ = EnsureServiceAsync();

            _expiryTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(1),
            };
            _expiryTimer.Tick += OnExpiryTimerTick;
            _expiryTimer.Start();
        }

        private void OnExpiryTimerTick(object sender, object e)
        {
            Store.PruneExpired();
        }

        private async void OnStoreChanged()
        {
            // PipeClient 在后台线程接收消息；所有 ObservableCollection 更新必须回到 UI 线程。
            var dispatcher = this.Dispatcher;
            await dispatcher.RunAsync(Windows.UI.Core.CoreDispatcherPriority.Normal, () =>
            {
                Store.RefreshForUi();
                UpdateSubtitleSections();
            });
        }

        private void UpdateSubtitleSections()
        {
            VoiceEmptyText.Visibility = Store.VoiceEntries.Count == 0
                ? Visibility.Visible
                : Visibility.Collapsed;
            ChatEmptyText.Visibility = Store.ChatEntries.Count == 0
                ? Visibility.Visible
                : Visibility.Collapsed;
        }

        private void OnStatusChanged(string status)
        {
            var dispatcher = this.Dispatcher;
            _ = dispatcher.RunAsync(Windows.UI.Core.CoreDispatcherPriority.Normal, () =>
            {
                switch (status)
                {
                    case "service-unavailable":
                        ConnectionStatusText.Text = "字幕服务启动失败";
                        ConnectionStatusText.Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 255, 155, 155));
                        ConnectionDot.Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 235, 92, 92));
                        break;
                    case "connected":
                        ConnectionStatusText.Text = "已连接 Sage";
                        ConnectionStatusText.Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 112, 232, 184));
                        ConnectionDot.Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 86, 218, 164));
                        break;
                    case "reconnecting":
                        ConnectionStatusText.Text = "等待 Sage 后台服务……";
                        ConnectionStatusText.Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 255, 207, 112));
                        ConnectionDot.Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 255, 190, 86));
                        break;
                    default:
                        ConnectionStatusText.Text = "正在连接 Sage……";
                        ConnectionStatusText.Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 166, 194, 222));
                        ConnectionDot.Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 126, 169, 211));
                        break;
                }
            });
        }

        private async void OnPinnedChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                UpdateVisualState);
        }
        private async void OnClickThroughChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                UpdateVisualState);
        }
        private async void OnDisplayModeChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                UpdateVisualState);
        }

        private async void OnVisibleChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                UpdateVisualState);
        }

        private async void OnWindowStateChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                UpdateVisualState);
        }

        private async void OnOpacityChanged(XboxGameBarWidget sender, object args)
        {
            await Dispatcher.RunAsync(
                Windows.UI.Core.CoreDispatcherPriority.Normal,
                ApplyRequestedOpacity);
        }

        private void ApplyRequestedOpacity()
        {
            if (_widget == null) return;

            try
            {
                // 不同 Game Bar / SDK 组合在实机上会返回 0–1 或 0–100。
                // XAML UIElement.Opacity 只接受 0–1，因此同时兼容两种范围并限幅。
                double requestedOpacity = _widget.RequestedOpacity;
                double opacity = requestedOpacity <= 1.0
                    ? requestedOpacity
                    : requestedOpacity / 100.0;
                if (double.IsNaN(opacity) || double.IsInfinity(opacity)) opacity = 1.0;
                RootGrid.Opacity = Math.Max(0.0, Math.Min(1.0, opacity));
            }
            catch
            {
                RootGrid.Opacity = 1.0;
            }
        }

        private async Task EnsureServiceAsync()
        {
            if (_serviceLaunchRequested)
            {
                return;
            }

            _serviceLaunchRequested = true;
            App.Log("ServiceLauncher.LaunchAsync entered");
            bool launched = await ServiceLauncher.LaunchAsync();
            App.Log("ServiceLauncher.LaunchAsync result=" + launched);
            if (!launched)
            {
                OnStatusChanged("service-unavailable");
            }
        }

        private void UpdateVisualState()
        {
            if (_widget == null) return;

            try
            {
                bool pinned = _widget.Pinned;
                bool clickThrough = _widget.ClickThroughEnabled;
                bool pinnedOnly = pinned &&
                                  _widget.GameBarDisplayMode == XboxGameBarDisplayMode.PinnedOnly;

                if (!pinned)
                {
                    SetupHintPanel.Visibility = Visibility.Visible;
                    SetupHintTitle.Text = "先固定，才能边玩边显示";
                    SetupHintText.Text = "点一下小组件标题栏的图钉。固定后关闭游戏栏，字幕仍会保留。";
                    WidgetModeText.Text = "尚未固定";
                }
                else if (!clickThrough)
                {
                    SetupHintPanel.Visibility = Visibility.Visible;
                    SetupHintTitle.Text = "已固定，可开启点击穿透";
                    SetupHintText.Text = "在游戏栏顶部打开“点击穿透”，鼠标就不会被字幕挡住。";
                    WidgetModeText.Text = "已固定";
                }
                else if (pinnedOnly)
                {
                    // Game Bar 已收起，且小组件已固定并穿透：仅保留字幕。
                    SetupHintPanel.Visibility = Visibility.Collapsed;
                    WidgetModeText.Text = "悬浮字幕";
                }
                else
                {
                    SetupHintPanel.Visibility = Visibility.Visible;
                    SetupHintTitle.Text = "已准备好";
                    SetupHintText.Text = "现在可以按 Win+G 收起游戏栏，字幕会继续显示。";
                    WidgetModeText.Text = "已固定 · 已穿透";
                }
            }
            catch
            {
                // 状态查询失败时保留字幕界面，避免再次触发激活崩溃。
                SetupHintPanel.Visibility = Visibility.Visible;
                SetupHintTitle.Text = "小组件已启动";
                SetupHintText.Text = "如需边玩边显示，请点击标题栏的图钉。";
                WidgetModeText.Text = "状态同步中";
            }
        }

        protected override void OnNavigatedFrom(NavigationEventArgs e)
        {
            Store.Changed -= OnStoreChanged;
            if (_expiryTimer != null)
            {
                _expiryTimer.Stop();
                _expiryTimer.Tick -= OnExpiryTimerTick;
                _expiryTimer = null;
            }
            if (_widget != null)
            {
                _widget.PinnedChanged -= OnPinnedChanged;
                _widget.ClickThroughEnabledChanged -= OnClickThroughChanged;
                _widget.GameBarDisplayModeChanged -= OnDisplayModeChanged;
                _widget.VisibleChanged -= OnVisibleChanged;
                _widget.WindowStateChanged -= OnWindowStateChanged;
                _widget.RequestedOpacityChanged -= OnOpacityChanged;
            }
            _serviceClient?.Dispose();
            _activity?.Complete();
            base.OnNavigatedFrom(e);
        }
    }
}
