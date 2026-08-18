using System;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Windows.Foundation;
using Windows.Foundation.Metadata;

namespace ValorantTranslator.Services
{
    internal static class ServiceLauncher
    {
        private const string RuntimeClassName = "Windows.ApplicationModel.FullTrustProcessLauncher";
        private const string ParameterGroupId = "SageSubtitleService";
        private static readonly Guid StaticsGuid = new Guid("D784837F-1100-3C6B-A455-F6262CC331B6");

        public static async Task<bool> LaunchAsync()
        {
            try
            {
                if (!ApiInformation.IsTypePresent(RuntimeClassName)) return false;
                IAsyncAction action = LaunchWithParameters(ParameterGroupId);
                if (action == null) return false;
                await action;
                App.Log("Sage subtitle service launch request completed");
                return true;
            }
            catch (Exception ex)
            {
                App.Log("Sage subtitle service launch failed: " + ex);
                return false;
            }
        }

        private static IAsyncAction LaunchWithParameters(string parameterGroupId)
        {
            IntPtr runtimeClass = IntPtr.Zero;
            IFullTrustProcessLauncherStatics launcher = null;
            try
            {
                Marshal.ThrowExceptionForHR(WindowsCreateString(
                    RuntimeClassName, RuntimeClassName.Length, out runtimeClass));
                Guid iid = StaticsGuid;
                Marshal.ThrowExceptionForHR(RoGetActivationFactory(runtimeClass, ref iid, out launcher));
                return launcher.LaunchFullTrustProcessForCurrentAppWithParametersAsync(parameterGroupId);
            }
            finally
            {
                if (runtimeClass != IntPtr.Zero) WindowsDeleteString(runtimeClass);
                if (launcher != null) Marshal.ReleaseComObject(launcher);
            }
        }

        [DllImport("api-ms-win-core-winrt-string-l1-1-0.dll", ExactSpelling = true)]
        private static extern int WindowsCreateString(
            [MarshalAs(UnmanagedType.LPWStr)] string sourceString, int length, out IntPtr hstring);

        [DllImport("api-ms-win-core-winrt-string-l1-1-0.dll", ExactSpelling = true)]
        private static extern int WindowsDeleteString(IntPtr hstring);

        [DllImport("api-ms-win-core-winrt-l1-1-0.dll", ExactSpelling = true)]
        private static extern int RoGetActivationFactory(
            IntPtr activatableClassId, ref Guid iid,
            [MarshalAs(UnmanagedType.Interface)] out IFullTrustProcessLauncherStatics factory);

        [ComImport]
        [System.Runtime.InteropServices.Guid("D784837F-1100-3C6B-A455-F6262CC331B6")]
        [InterfaceType(ComInterfaceType.InterfaceIsIInspectable)]
        private interface IFullTrustProcessLauncherStatics
        {
            [return: MarshalAs(UnmanagedType.Interface)]
            IAsyncAction LaunchFullTrustProcessForCurrentAppAsync();

            [return: MarshalAs(UnmanagedType.Interface)]
            IAsyncAction LaunchFullTrustProcessForCurrentAppWithParametersAsync(
                [MarshalAs(UnmanagedType.HString)] string parameterGroupId);

            [return: MarshalAs(UnmanagedType.Interface)]
            IAsyncAction LaunchFullTrustProcessForAppAsync(
                [MarshalAs(UnmanagedType.HString)] string fullTrustPackageRelativeAppId);

            [return: MarshalAs(UnmanagedType.Interface)]
            IAsyncAction LaunchFullTrustProcessForAppWithParametersAsync(
                [MarshalAs(UnmanagedType.HString)] string fullTrustPackageRelativeAppId,
                [MarshalAs(UnmanagedType.HString)] string parameterGroupId);
        }
    }
}
