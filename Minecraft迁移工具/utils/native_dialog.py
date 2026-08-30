# utils/native_dialog.py
"""Windows 原生对话框封装（comtypes，安全调用 COM）。

用 Vista 的新式 IFileOpenDialog 实现"选文件夹 + 多选"。
tkinter 的 filedialog.askdirectory 用的是原生文件夹选择器，但它不支持多选；
只有通过 IFileOpenDialog 并组合 FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT 才能做到。

注意：comtypes 会自动处理 COM vtable 与参数编组，比手写 ctypes 稳定得多，
不会因为调用约定/索引问题导致访问违例。若不支持则抛异常，由调用方回退。
"""
import ctypes
from ctypes import POINTER, byref, c_void_p, c_int, c_uint, c_ulong, c_wchar_p

import comtypes
from comtypes import GUID, HRESULT, IUnknown, COMMETHOD

# GUID 常量
CLSID_FileOpenDialog = GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
IID_IFileOpenDialog = GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")
IID_IFileDialog = GUID("{42F85136-DB7E-439C-85F1-E4075D135FC8}")
IID_IShellItem = GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
IID_IShellItemArray = GUID("{B63EA76D-1F85-456F-A19C-48159EFA858B}")

CLSCTX_INPROC_SERVER = 0x1
FOS_PICKFOLDERS = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040
FOS_ALLOWMULTISELECT = 0x00000200
FOS_PATHMUSTEXIST = 0x00000800
SIGDN_FILESYSPATH = 0x80058000
S_OK = 0


class IShellItem(IUnknown):
    _iid_ = IID_IShellItem
    _methods_ = [
        COMMETHOD([], HRESULT, 'BindToHandler',
                  (['in'], c_void_p, 'pbc'), (['in'], c_void_p, 'bhid'),
                  (['in'], c_void_p, 'riid'), (['out'], POINTER(c_void_p), 'ppv')),
        COMMETHOD([], HRESULT, 'GetParent', (['out'], POINTER(c_void_p), 'ppsi')),
        COMMETHOD([], HRESULT, 'GetDisplayName',
                  (['in'], c_int, 'sigdnName'),
                  (['out', 'retval'], POINTER(c_wchar_p), 'ppszName')),
        COMMETHOD([], HRESULT, 'GetAttributes',
                  (['in'], c_uint, 'sfgaoMask'), (['out'], POINTER(c_uint), 'psfgaoAttribs')),
        COMMETHOD([], HRESULT, 'Compare',
                  (['in'], c_void_p, 'psi'), (['in'], c_uint, 'hint'),
                  (['out'], POINTER(c_int), 'piOrder')),
    ]


class IShellItemArray(IUnknown):
    _iid_ = IID_IShellItemArray
    _methods_ = [
        COMMETHOD([], HRESULT, 'BindToHandler',
                  (['in'], c_void_p, 'pbc'), (['in'], c_void_p, 'bhid'),
                  (['in'], c_void_p, 'riid'), (['out'], POINTER(c_void_p), 'ppv')),
        COMMETHOD([], HRESULT, 'GetPropertyStore',
                  (['in'], c_int, 'flags'), (['in'], c_void_p, 'riid'),
                  (['out'], POINTER(c_void_p), 'ppv')),
        COMMETHOD([], HRESULT, 'GetPropertyDescriptionList',
                  (['in'], c_void_p, 'keyType'), (['in'], c_void_p, 'riid'),
                  (['out'], POINTER(c_void_p), 'ppv')),
        COMMETHOD([], HRESULT, 'GetAttributes',
                  (['in'], c_uint, 'attribFlags'), (['in'], c_uint, 'sfgaoMask'),
                  (['out'], POINTER(c_uint), 'psfgaoAttribs')),
        COMMETHOD([], HRESULT, 'GetCount', (['out', 'retval'], POINTER(c_uint), 'pdwNumItems')),
        COMMETHOD([], HRESULT, 'GetItemAt',
                  (['in'], c_uint, 'dwIndex'),
                  (['out', 'retval'], POINTER(POINTER(IShellItem)), 'ppsi')),
        COMMETHOD([], HRESULT, 'EnumItems', (['out', 'retval'], POINTER(POINTER(IUnknown)), 'ppenum')),
    ]


class IFileDialog(IUnknown):
    _iid_ = IID_IFileDialog
    _methods_ = [
        COMMETHOD([], HRESULT, 'Show', (['in'], c_void_p, 'hwnd')),
        COMMETHOD([], HRESULT, 'SetFileTypes',
                  (['in'], c_uint, 'cFileTypes'), (['in'], c_void_p, 'rgFilterSpec')),
        COMMETHOD([], HRESULT, 'SetFileTypeIndex', (['in'], c_uint, 'iFileType')),
        COMMETHOD([], HRESULT, 'GetFileTypeIndex', (['out'], POINTER(c_uint), 'piFileType')),
        COMMETHOD([], HRESULT, 'Advise',
                  (['in'], c_void_p, 'pfde'), (['out'], POINTER(c_uint), 'pdwCookie')),
        COMMETHOD([], HRESULT, 'Unadvise', (['in'], c_uint, 'dwCookie')),
        COMMETHOD([], HRESULT, 'SetOptions', (['in'], c_ulong, 'fos')),
        COMMETHOD([], HRESULT, 'GetOptions', (['out'], POINTER(c_ulong), 'pfos')),
        COMMETHOD([], HRESULT, 'SetDefaultFolder', (['in'], c_void_p, 'psi')),
        COMMETHOD([], HRESULT, 'SetFolder', (['in'], c_void_p, 'psi')),
        COMMETHOD([], HRESULT, 'GetFolder', (['out'], POINTER(c_void_p), 'ppsi')),
        COMMETHOD([], HRESULT, 'GetCurrentSelection', (['out'], POINTER(c_void_p), 'ppsi')),
        COMMETHOD([], HRESULT, 'SetFileName', (['in'], c_wchar_p, 'pszName')),
        COMMETHOD([], HRESULT, 'GetFileName', (['out', 'retval'], POINTER(c_wchar_p), 'pszName')),
        COMMETHOD([], HRESULT, 'SetTitle', (['in'], c_wchar_p, 'pszTitle')),
        COMMETHOD([], HRESULT, 'SetOkButtonLabel', (['in'], c_wchar_p, 'pszText')),
        COMMETHOD([], HRESULT, 'SetFileNameLabel', (['in'], c_wchar_p, 'pszLabel')),
        COMMETHOD([], HRESULT, 'GetResult', (['out'], POINTER(c_void_p), 'ppsi')),
        COMMETHOD([], HRESULT, 'AddPlace',
                  (['in'], c_void_p, 'psi'), (['in'], c_uint, 'fdap')),
        COMMETHOD([], HRESULT, 'SetDefaultExtension', (['in'], c_wchar_p, 'pszDefaultExtension')),
        COMMETHOD([], HRESULT, 'Close', (['in'], HRESULT, 'hr')),
        COMMETHOD([], HRESULT, 'SetClientGuid', (['in'], POINTER(GUID), 'guid')),
        COMMETHOD([], HRESULT, 'ClearClientData'),
        COMMETHOD([], HRESULT, 'SetFilter', (['in'], c_void_p, 'pSetFilter')),
    ]


class IFileOpenDialog(IFileDialog):
    _iid_ = IID_IFileOpenDialog
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetResults',
                  (['out', 'retval'], POINTER(POINTER(IShellItemArray)), 'ppenum')),
        COMMETHOD([], HRESULT, 'GetSelectedItems',
                  (['out', 'retval'], POINTER(POINTER(IShellItemArray)), 'ppsai')),
    ]


def _shell_item_from_path(path):
    """用 SHCreateItemFromParsingName 从路径创建 IShellItem（原生指针）。"""
    iid = GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")  # IID_IShellItem
    item = c_void_p()
    hr = ctypes.windll.shell32.SHCreateItemFromParsingName(
        path, None, byref(iid), byref(item))
    if hr != S_OK or not item:
        return None
    return item


def _extract_hresult(exc):
    """从异常中提取 HRESULT（负数）。"""
    hres = getattr(exc, "hresult", None)
    if hres is None:
        args = getattr(exc, "args", None)
        if args:
            hres = args[0]
    try:
        return int(hres)
    except (TypeError, ValueError):
        return None


def pick_folders(parent_hwnd=0, initial_dir=None, title="选择文件夹（可多选）"):
    """用原生 Windows 对话框选择多个文件夹。

    返回：选中的绝对路径字符串列表；用户取消或对话框未完成返回 None。
    仅当无法创建原生对话框（COM 不可用）时才抛异常，由调用方回退。
    """
    # 只有这一步失败才抛异常 → 调用方才会回退到自定义对话框
    dlg = comtypes.CoCreateInstance(CLSID_FileOpenDialog, interface=IFileOpenDialog,
                                    clsctx=CLSCTX_INPROC_SERVER)
    try:
        # 以下均为可选设置，失败不致命
        try:
            opts = (FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
                    | FOS_ALLOWMULTISELECT | FOS_PATHMUSTEXIST)
            dlg.SetOptions(opts)
        except Exception:
            pass
        try:
            if title:
                dlg.SetTitle(title)
        except Exception:
            pass
        try:
            if initial_dir:
                shell_item = _shell_item_from_path(initial_dir)
                if shell_item:
                    # SetFolder 强制把对话框导航到该文件夹（比 SetDefaultFolder 可靠，
                    # 后者常被 Windows 的"最近访问位置"覆盖）
                    dlg.SetFolder(shell_item)
                    dlg.SetDefaultFolder(shell_item)
        except Exception:
            pass

        try:
            hr = dlg.Show(ctypes.c_void_p(parent_hwnd or 0))
        except Exception as e:
            hr = _extract_hresult(e)

        if hr is None:
            return None
        hr_u = int(hr) & 0xFFFFFFFF
        if hr_u != S_OK:
            # 用户取消或对话框未完成：视为未选择，直接返回，绝不回退弹出第二个窗口
            return None

        results = dlg.GetResults()
        if results is None:
            return []
        paths = []
        try:
            count = results.GetCount()
        except Exception:
            count = 0
        for i in range(int(count or 0)):
            try:
                item = results.GetItemAt(i)
                name = item.GetDisplayName(SIGDN_FILESYSPATH)
                if name:
                    paths.append(name)
            except Exception:
                continue
        return paths
    finally:
        if dlg is not None:
            try:
                dlg.Release()
            except Exception:
                pass
