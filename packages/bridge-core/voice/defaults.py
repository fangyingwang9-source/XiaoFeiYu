"""Windows 默认录制端点临时切换（只切录制，切完立刻还原）。

微信 4.x 没有应用内麦克风选择，只能跟系统默认设备。发送语音时把「默认录制设备」
临时指向 CABLE Output，发完在 finally 里还原；默认播放设备全程不动。

实现：读当前默认用 miniaudio 的 is_default（走 eConsole 角色），
切换用未公开的 IPolicyConfig::SetDefaultEndpoint（REG 里能创建，实测可用）。
"""
from __future__ import annotations

import winreg

CAPTURE_FLOW = 1
RENDER_FLOW = 0
ROLE_CONSOLE = 0
ROLE_MULTIMEDIA = 1
ROLE_COMMUNICATIONS = 2
ROLE_NAMES = {0: "console", 1: "multimedia", 2: "communications"}

_CLSID_PolicyConfig = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
_IID_IPolicyConfig = "{F8679F50-850A-41CF-9C72-430F290290C8}"
_FRIENDLY_KEY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_MMDEVICES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"


def endpoint_names(flow: int = CAPTURE_FLOW) -> dict:
    """注册表里的 <端点ID, 友好名>；端点ID 形如 {0.0.0.00000000}.{guid}。"""
    sub = "Capture" if flow == CAPTURE_FLOW else "Render"
    prefix = "{0.0.0.00000000}." if flow == CAPTURE_FLOW else "{0.0.1.00000000}."
    out = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MMDEVICES + "\\" + sub)
    except OSError:
        return out
    with root:
        idx = 0
        while True:
            try:
                guid = winreg.EnumKey(root, idx)
            except OSError:
                break
            idx += 1
            if not guid.startswith("{"):
                continue
            try:
                with winreg.OpenKey(root, guid + "\\Properties") as pk:
                    name = str(winreg.QueryValueEx(pk, _FRIENDLY_KEY)[0])
            except OSError:
                continue
            out[prefix + guid.lower()] = name
    return out


def find_endpoint(keywords, flow: int = CAPTURE_FLOW):
    """按友好名关键词挑端点，返回 (端点ID, 友好名)；按关键词顺序优先。"""
    names = endpoint_names(flow)
    for kw in keywords:
        kw = kw.upper()
        for dev_id, name in names.items():
            if kw in name.upper():
                return dev_id, name
    return None, None


def _refresh_portaudio():
    try:
        import sounddevice as sd
        sd._terminate()
        sd._initialize()
    except Exception:  # noqa: BLE001
        pass


def _sd_default(flow: int = CAPTURE_FLOW) -> str:
    """PortAudio 报的默认端点名（WASAPI 主机 API，等价系统默认设备）。"""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return ""
    key = "default_input_device" if flow == CAPTURE_FLOW else "default_output_device"
    for api in sd.query_hostapis():
        if "WASAPI" not in str(api.get("name", "")).upper():
            continue
        idx = api.get(key, -1)
        if idx is None or idx < 0:
            continue
        return str(sd.query_devices(idx)["name"])
    return ""


def _base(name: str) -> str:
    """去掉 Windows 追加的括号后缀：(Realtek(R) Audio) 之类。"""
    return str(name or "").split(" (")[0].strip()


def default_name(flow: int = CAPTURE_FLOW) -> str:
    return _sd_default(flow)


def _endpoint_state(guid: str, flow: int = CAPTURE_FLOW) -> int:
    sub = "Capture" if flow == CAPTURE_FLOW else "Render"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MMDEVICES + "\\" + sub + "\\" + guid) as k:
            return int(winreg.QueryValueEx(k, "DeviceState")[0]) & 1
    except OSError:
        return 0


def default_endpoint(flow: int = CAPTURE_FLOW):
    """当前默认端点 -> (端点ID, 友好名)。"""
    name = _sd_default(flow)
    if not name:
        return None, None
    names = endpoint_names(flow)
    base = _base(name).upper()
    cands = []
    for dev_id, dev_name in names.items():
        guid = dev_id.split("}.")[-1]
        if _base(dev_name).upper() == base and _endpoint_state(guid, flow):
            cands.append((dev_id, dev_name))
    if cands:
        return cands[0]
    for dev_id, dev_name in names.items():
        if _base(dev_name).upper() == base:
            return dev_id, dev_name
    return None, name


def _policy():
    import comtypes
    from comtypes import GUID, COMMETHOD, HRESULT, IUnknown
    from ctypes import POINTER, c_uint, c_void_p, c_wchar_p, c_int

    class IPolicyConfig(IUnknown):
        _iid_ = GUID(_IID_IPolicyConfig)
        _methods_ = [
            COMMETHOD([], HRESULT, "GetMixFormat", (["in"], c_wchar_p, "dev"), (["out"], POINTER(c_void_p), "fmt")),
            COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], c_wchar_p, "dev"), (["in"], c_int, "default_flag"),
                      (["out"], POINTER(c_void_p), "fmt")),
            COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], c_wchar_p, "dev")),
            COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], c_wchar_p, "dev"), (["in"], c_void_p, "endpoint_fmt"),
                      (["in"], c_void_p, "mix_fmt")),
            COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], c_wchar_p, "dev"), (["in"], c_int, "default_flag"),
                      (["out"], POINTER(c_void_p), "a"), (["out"], POINTER(c_void_p), "b")),
            COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], c_wchar_p, "dev"), (["in"], c_void_p, "period")),
            COMMETHOD([], HRESULT, "GetShareMode", (["in"], c_wchar_p, "dev"), (["out"], POINTER(c_void_p), "mode")),
            COMMETHOD([], HRESULT, "SetShareMode", (["in"], c_wchar_p, "dev"), (["in"], c_void_p, "mode")),
            COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], c_wchar_p, "dev"), (["in"], c_void_p, "key"),
                      (["out"], POINTER(c_void_p), "value")),
            COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], c_wchar_p, "dev"), (["in"], c_void_p, "key"),
                      (["in"], c_void_p, "value")),
            COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p, "dev"), (["in"], c_uint, "role")),
            COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], c_wchar_p, "dev"), (["in"], c_int, "visible")),
        ]

    try:
        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    return comtypes.CoCreateInstance(GUID(_CLSID_PolicyConfig), interface=IPolicyConfig, clsctx=1)


def set_default(device_id: str, flow: int = CAPTURE_FLOW,
                roles=(ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS)) -> None:
    if not device_id:
        raise RuntimeError("端点ID为空，拒绝切换默认设备")
    policy = _policy()
    for role in roles:
        policy.SetDefaultEndpoint(device_id, role)   # 三角色同切：微信 4.x 未必跟 console
    _refresh_portaudio()


CABLE_CAPTURE_HINTS = ("CABLE OUTPUT", "CABLE")
CABLE_RENDER_HINTS = ("CABLE INPUT", "CABLE IN", "CABLE")


def cable_capture():
    return find_endpoint(CABLE_CAPTURE_HINTS, CAPTURE_FLOW)


def cable_render():
    return find_endpoint(CABLE_RENDER_HINTS, RENDER_FLOW)


def snapshot(flow: int = CAPTURE_FLOW):
    """记录当前默认端点，返回 {'id','name'}；没读到就是 {'id':'','name':''}。"""
    dev_id, name = default_endpoint(flow)
    return {"id": dev_id or "", "name": name or ""}


def restore(snap, flow: int = CAPTURE_FLOW, roles=(ROLE_CONSOLE,)) -> str:
    """按快照还原默认录制端点，返回还原后的实际默认名（用于校验）。"""
    if snap and snap.get("id"):
        set_default(snap["id"], flow, roles)
    return default_name(flow)


if __name__ == "__main__":
    before = snapshot(CAPTURE_FLOW)
    print("default_before:", before)
    dev_id, name = cable_capture()
    print("cable_output:", dev_id, name)
    set_default(dev_id, CAPTURE_FLOW)
    print("default_after_switch:", default_name(CAPTURE_FLOW))
    print("restored_to:", restore(before, CAPTURE_FLOW))
    print("snapshot_after_restore:", snapshot(CAPTURE_FLOW))
    print("render_untouched:", default_name(RENDER_FLOW))