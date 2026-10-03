"""擷取聲音(Windows 10 2004 以後的 WASAPI):直接要 16kHz 單聲道 16 位元,Whisper 不用再轉。

三種來源:
- system:電腦正在播放的聲音,排除 Naiz Studio 自己(和它開的程式);YouTube、遊戲、Discord 都抓得到
- app:只抓某一個程式(連同它開的子程式),例如只翻 Discord、不翻遊戲音樂
- mic:預設的麥克風

用 comtypes 直接呼叫 Windows 的音訊介面,不需要虛擬音效卡或額外驅動。
注意:comtypes 一載入就會設定所在執行緒的 COM 模式,所以本模組只在背景執行緒載入、使用(不碰畫面的執行緒)。
"""

import ctypes
import os
import sys
import threading
import time
from ctypes import HRESULT, POINTER, Structure, Union, byref, c_uint32, c_ulonglong, c_void_p, wintypes

sys.coinit_flags = 0            # 多執行緒模式(Windows 音訊的啟用回呼需要);在載入 comtypes 之前設定
import comtypes  # noqa: E402
from comtypes import COMMETHOD, GUID, IUnknown  # noqa: E402

RATE = 16000                    # 每秒取樣數
SOURCES = [("system", "電腦播放的聲音"), ("app", "單一程式"), ("mic", "麥克風")]
SOURCE_NOTES = {"system": "YouTube、B站、遊戲、Discord 等，電腦正在播放的聲音都會翻譯",
                "app": "只翻譯選的程式，例如只翻 Discord、不翻遊戲音樂",
                "mic": "翻譯麥克風收到的聲音"}

VT_BLOB = 0x41
PROCESS_LOOPBACK = 1
INCLUDE_TREE, EXCLUDE_TREE = 0, 1
LOOPBACK = 0x00020000
AUTOCONVERTPCM = 0x80000000
SRC_DEFAULT_QUALITY = 0x08000000
SILENT = 0x2
CLSCTX_ALL = 23
E_CAPTURE, E_CONSOLE = 1, 0
SESSION_ACTIVE = 1
SESSION_EXPIRED = 2
# 開著視窗但不會出聲音、列出來只會讓清單變長的系統程式
HIDDEN_PROGRAMS = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
                   "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe"}


class WAVEFORMATEX(Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationUnion(Union):
    _fields_ = [("ProcessLoopbackParams", PROCESS_LOOPBACK_PARAMS)]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("u", _ActivationUnion)]


class BLOB(Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("blob", BLOB), ("pad", c_ulonglong)]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p), "data"), (["out"], POINTER(c_uint32), "frames"),
                  (["out"], POINTER(wintypes.DWORD), "flags"), (["out"], POINTER(c_ulonglong), "pos"),
                  (["out"], POINTER(c_ulonglong), "qpc")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int, "mode"), (["in"], wintypes.DWORD, "flags"),
                  (["in"], ctypes.c_longlong, "duration"), (["in"], ctypes.c_longlong, "period"),
                  (["in"], POINTER(WAVEFORMATEX), "fmt"), (["in"], c_void_p, "session")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "frames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "latency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "frames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "mode"), (["in"], POINTER(WAVEFORMATEX), "fmt"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "closest")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "fmt")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_longlong), "default"),
                  (["out"], POINTER(ctypes.c_longlong), "minimum")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "event")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "iid"), (["out"], POINTER(c_void_p), "service")),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "result"),
                           (["out"], POINTER(POINTER(IUnknown)), "interface"))]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                           (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "operation"))]


class IAgileObject(IUnknown):
    """標記介面(沒有方法):回呼物件沒有宣告它,啟用會失敗(E_ILLEGAL_METHOD_CALL)。"""
    _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
    _methods_ = []


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], wintypes.DWORD, "clsctx"),
                  (["in"], c_void_p, "params"), (["out"], POINTER(c_void_p), "interface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], wintypes.DWORD, "access"),
                  (["out"], POINTER(c_void_p), "store")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(wintypes.LPWSTR), "id")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(wintypes.DWORD), "state")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int, "flow"), (["in"], wintypes.DWORD, "mask"),
                  (["out"], POINTER(c_void_p), "devices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int, "flow"), (["in"], ctypes.c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "device")),
    ]


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(ctypes.c_int), "state")),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(wintypes.LPWSTR), "name")),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], wintypes.LPCWSTR, "name"), (["in"], c_void_p, "context")),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(wintypes.LPWSTR), "path")),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], wintypes.LPCWSTR, "path"), (["in"], c_void_p, "context")),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "group")),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "group"), (["in"], c_void_p, "context")),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p, "client")),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p, "client")),
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(wintypes.LPWSTR), "id")),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(wintypes.LPWSTR), "id")),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(wintypes.DWORD), "pid")),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], wintypes.BOOL, "optout")),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(ctypes.c_int), "count")),
        COMMETHOD([], HRESULT, "GetSession", (["in"], ctypes.c_int, "index"), (["out"], POINTER(POINTER(IUnknown)), "session")),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], c_void_p, "group"), (["in"], wintypes.DWORD, "flags"),
                  (["out"], POINTER(c_void_p), "control")),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], c_void_p, "group"), (["in"], wintypes.DWORD, "flags"),
                  (["out"], POINTER(c_void_p), "volume")),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "sessions")),
    ]


CLSID_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class _Handler(comtypes.COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()
        self.client = None
        self.error = None

    def ActivateCompleted(self, operation):
        try:
            hr, unknown = operation.GetActivateResult()
            if hr < 0:
                self.error = hr
            else:
                self.client = unknown.QueryInterface(IAudioClient)
        except Exception as exc:
            self.error = getattr(exc, "hresult", None) or str(exc)
        self.done.set()
        return 0


def _init_thread():
    """這個執行緒用多執行緒模式初始化 COM(載入本模組的那個執行緒已經初始化過)。"""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass


def _format():
    return WAVEFORMATEX(1, 1, RATE, RATE * 2, 2, 16, 0)


def _loopback_client(pid, include):
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = PROCESS_LOOPBACK
    params.u.ProcessLoopbackParams.TargetProcessId = pid
    params.u.ProcessLoopbackParams.ProcessLoopbackMode = INCLUDE_TREE if include else EXCLUDE_TREE
    variant = PROPVARIANT()
    variant.vt = VT_BLOB
    variant.blob.cbSize = ctypes.sizeof(params)
    variant.blob.pBlobData = ctypes.cast(ctypes.pointer(params), c_void_p)
    handler = _Handler()
    activate = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
    activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
    activate.restype = HRESULT
    operation = c_void_p()
    iid = IAudioClient._iid_
    activate("VAD\\Process_Loopback", byref(iid), byref(variant),
             handler.QueryInterface(IActivateAudioInterfaceCompletionHandler), byref(operation))
    if not handler.done.wait(5):
        raise RuntimeError("Windows 沒有回應聲音擷取的要求")
    if handler.error is not None:
        raise RuntimeError("無法擷取這個程式的聲音(需要 Windows 10 2004 以後的版本)")
    client = handler.client
    client.Initialize(0, LOOPBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY, 200 * 10000, 0, byref(_format()), None)
    return client


def _mic_client():
    enumerator = comtypes.CoCreateInstance(CLSID_ENUMERATOR, IMMDeviceEnumerator, CLSCTX_ALL)
    try:
        device = enumerator.GetDefaultAudioEndpoint(E_CAPTURE, E_CONSOLE)
    except OSError:
        raise RuntimeError("找不到麥克風，請確認有接上並在 Windows 設定裡啟用") from None
    pointer = device.Activate(byref(IAudioClient._iid_), CLSCTX_ALL, None)
    client = ctypes.cast(pointer, POINTER(IAudioClient))
    client.Initialize(0, AUTOCONVERTPCM | SRC_DEFAULT_QUALITY, 200 * 10000, 0, byref(_format()), None)
    return client


class Capture:
    """在背景擷取聲音,每收到一段就呼叫 on_audio(16kHz 單聲道 16 位元的 bytes)。"""

    def __init__(self, source, on_audio, pid=None):
        self.source, self.pid, self.on_audio = source, pid, on_audio
        self.error = ""
        self._stop = threading.Event()
        self._thread = None
        self.started = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        _init_thread()
        try:
            if self.source == "mic":
                client = _mic_client()
            elif self.source == "app":
                client = _loopback_client(self.pid, include=True)
            else:
                client = _loopback_client(os.getpid(), include=False)      # 排除自己(和自己開的程式)
            service = client.GetService(byref(IAudioCaptureClient._iid_))
            capture = ctypes.cast(service, POINTER(IAudioCaptureClient))
            client.Start()
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            self.started.set()
            return
        self.started.set()
        try:
            while not self._stop.is_set():
                time.sleep(0.02)
                chunk = bytearray()
                while capture.GetNextPacketSize():
                    data, frames, flags, _, _ = capture.GetBuffer()
                    chunk += bytes(frames * 2) if flags & SILENT else ctypes.string_at(data, frames * 2)
                    capture.ReleaseBuffer(frames)
                if chunk:
                    self.on_audio(bytes(chunk))
        except Exception as exc:
            self.error = f"聲音擷取中斷：{exc}"
        finally:
            try:
                client.Stop()
            except Exception:
                pass


# ------------------------------------------------------------ 正在發出聲音的程式

class PROCESSENTRY32W(Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _processes():
    """{pid: (父程式 pid, 執行檔名稱)}"""
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result = {}
    try:
        ok = kernel.Process32FirstW(snapshot, byref(entry))
        while ok:
            result[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel.Process32NextW(snapshot, byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def _exe_path(pid):
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)):
            return buffer.value
        return ""
    finally:
        kernel.CloseHandle(handle)


def _description(path):
    """執行檔的說明(例如「Google Chrome」),拿不到時用檔名。"""
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return name
        data = ctypes.create_string_buffer(size)
        version.GetFileVersionInfoW(path, 0, size, data)
        pointer, length = c_void_p(), c_uint32()
        if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", byref(pointer), byref(length)) or not length:
            return name
        lang, page = ctypes.cast(pointer, POINTER(wintypes.WORD * 2)).contents
        key = f"\\StringFileInfo\\{lang:04x}{page:04x}\\FileDescription"
        if version.VerQueryValueW(data, key, byref(pointer), byref(length)) and length.value > 1:
            text = ctypes.wstring_at(pointer, length.value - 1).strip()
            return text or name
    except Exception:
        pass
    return name


def _window_pids():
    """有開著(看得到、有標題)視窗的程式。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pids = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 \
                and not user32.GetWindow(hwnd, 4):                               # 不算附屬在別的視窗下的
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return pids


def audio_programs():
    """可以單獨抓聲音的程式:[(pid, 名稱)],正在播放的排前面、名稱後面標「播放中」。
    除了正在播放的,也列出出過聲音(現在安靜)的程式和開著視窗的程式:選了之後它一出聲就抓得到
    (沒有聲音的程式本來就抓不到東西,不會出錯)。pid 是同一個程式最上層的那個(瀏覽器會開很多子程式,一起抓)。
    要在背景執行緒呼叫。"""
    _init_thread()
    enumerator = comtypes.CoCreateInstance(CLSID_ENUMERATOR, IMMDeviceEnumerator, CLSCTX_ALL)
    device = enumerator.GetDefaultAudioEndpoint(0, E_CONSOLE)                  # 預設的播放裝置
    manager = ctypes.cast(device.Activate(byref(IAudioSessionManager2._iid_), CLSCTX_ALL, None),
                          POINTER(IAudioSessionManager2))
    sessions = manager.GetSessionEnumerator()
    processes = _processes()
    own = os.getpid()
    candidates = {}                                     # pid → 是否正在播放
    for index in range(sessions.GetCount()):
        control = sessions.GetSession(index).QueryInterface(IAudioSessionControl2)
        pid = control.GetProcessId()
        state = control.GetState()
        if pid and state != SESSION_EXPIRED:
            candidates[pid] = candidates.get(pid, False) or state == SESSION_ACTIVE
    try:
        for pid in _window_pids():
            candidates.setdefault(pid, False)
    except OSError:
        pass
    found = {}
    for pid, playing in candidates.items():
        if pid == own or pid not in processes:
            continue
        exe = processes[pid][1].lower()
        root = pid
        seen = set()
        while root in processes and root not in seen:           # 往上找同一個執行檔的最上層
            seen.add(root)
            parent = processes[root][0]
            if parent in processes and processes[parent][1].lower() == exe:
                root = parent
            else:
                break
        if root == own or exe in HIDDEN_PROGRAMS:
            continue
        key = exe or str(pid)
        if key in found:
            found[key] = (found[key][0], found[key][1], found[key][2] or playing)
            continue
        path = _exe_path(root) or _exe_path(pid)
        found[key] = (root, _description(path) if path else exe or str(pid), playing)
    ordered = sorted(found.values(), key=lambda item: (not item[2], item[1].lower()))
    return [(pid, f"{name}（播放中）" if playing else name) for pid, name, playing in ordered]
