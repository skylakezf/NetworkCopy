#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python script to handle download and installation of selected software packages
"""

import os
import sys
import json
import base64
import ctypes
import subprocess
import time
from ctypes import wintypes
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== lingtong 身份安装配置 ====================
LINGTONG_USER = r".\lingtong"      # 运行安装程序所用的账号
PASSWORD_FILE = "password.pem"     # 密码文件（内容为密码的 base64 编码）
INSTALL_TEMP_DIR = os.path.join('F:/Appl', 'Installers')   # 安装包落盘/临时文件目录
SILENT_ARGS_FILE = 'silent_args.json'   # 可选：覆盖/补充静默安装参数（放脚本同目录）

# CreateProcessWithLogonW / schtasks 常用的标志位
_LOGON_WITH_PROFILE = 0x00000001
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_LOGON32_LOGON_BATCH = 4   # 批处理登录：Task Scheduler 走的就是它，UAC 不过滤

# ==================== 调试开关 ====================
# True：启动安装程序前后打印详细诊断信息，并追加写入 install_debug.log
# 排查完成后可改回 False（不影响功能，只是少打日志）
DEBUG = True
DEBUG_LOG = "install_debug.log"


def _script_dir():
    """返回本脚本所在目录的绝对路径"""
    return os.path.dirname(os.path.abspath(__file__))


def _get_lingtong_password():
    """读取 password.pem 并做 base64 解码，返回 lingtong 的明文密码"""
    pem_path = os.path.join(_script_dir(), PASSWORD_FILE)
    with open(pem_path, 'r', encoding='utf-8') as f:
        encoded = f.read().strip()
    # 补齐 base64 padding，兼容不含换行的单行文件
    encoded += '=' * (-len(encoded) % 4)
    return base64.b64decode(encoded).decode('utf-8')


def _ps_quote(text):
    """PowerShell 单引号字符串转义"""
    return "'" + str(text).replace("'", "''") + "'"


# ==================== 日志 / 输出解码 ====================

def _log(msg=''):
    """同时输出到控制台，并追加写入 install_debug.log"""
    text = str(msg)
    try:
        print(text, flush=True)
    except Exception:
        pass
    if not DEBUG:
        return
    try:
        with open(os.path.join(_script_dir(), DEBUG_LOG), 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except Exception:
        pass


def _decode_console(raw) -> str:
    """把子进程输出解码为文本（修复中文乱码）

    中文 Windows 下 PowerShell / net / schtasks 的原始输出是 GBK(cp936)。
    原先用 encoding='utf-8', errors='ignore' 读取，GBK 字节里恰好构成合法
    UTF-8 序列的部分被解出来、其余被丢弃，于是中文全丢，只剩
    "ڳ´޷д: ĲҪ" 这类乱码（原文是"…无法运行此命令: 请求的操作需要提升。"）。
    这里按 utf-8 -> gbk -> mbcs 依次尝试。
    """
    if raw is None:
        return ''
    if isinstance(raw, str):
        return raw
    for enc in ('utf-8', 'gbk', 'mbcs'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', 'replace')


def _run(cmd):
    """执行命令，返回 (返回码, 合并后的输出文本)"""
    try:
        p = subprocess.run(cmd, capture_output=True, shell=False)
        return p.returncode, _decode_console(p.stdout) + _decode_console(p.stderr)
    except Exception as e:
        return -1, f"<执行 {cmd} 失败: {e}>"


def _mask(text, secret):
    """日志里屏蔽密码明文"""
    text = str(text)
    if secret:
        text = text.replace(secret, '******')
    return text


# ==================== 环境诊断 ====================

def _is_process_elevated():
    """当前 python 进程是否以管理员（已提升）身份运行"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _read_uac_settings():
    """读取 UAC 相关注册表设置"""
    import winreg
    names = ('EnableLUA', 'ConsentPromptBehaviorAdmin', 'FilterAdministratorToken',
             'PromptOnSecureDesktop', 'EnableInstallerDetection')
    info = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System") as key:
            for n in names:
                try:
                    info[n] = winreg.QueryValueEx(key, n)[0]
                except FileNotFoundError:
                    info[n] = '<未设置>'
    except Exception as e:
        info['_error'] = str(e)
    return info


def _task_user_name():
    """账号名（供 net user 等命令行使用）：去掉 '.\\' 前缀后的纯账号名"""
    if LINGTONG_USER.startswith('.\\') or LINGTONG_USER.startswith('./'):
        return LINGTONG_USER[2:]
    return LINGTONG_USER


# ==================== 令牌探测：判定"能不能静默提权" ====================
#
# 关键结论（MSDN + 实测）：
#   CreateProcessWithLogonW 的执行链是
#       调用方进程 -> seclogon(Secondary Logon) 服务 -> LogonUser -> CreateProcessAsUser
#   UAC 开启时它**只产生被过滤的普通令牌**(Medium IL)，于是：
#     - lingtong 是管理员但令牌被过滤 -> 启动 requireAdministrator 的安装包必然 740
#     - lingtong 根本不是管理员      -> 同样 740
#   **调用方自己是不是管理员完全不影响结果**，这不是代码能绕的。
#   唯一能在"不弹窗"前提下解决的办法是预先配置目标机，二选一：
#     A. EnableLUA=0（关掉 UAC）
#     B. lingtong 就是内置 Administrator(RID 500) 且 FilterAdministratorToken=0
#   下面这段探测就是用来明确告诉用户"现在卡在哪一种情况"。

_LOGON32_LOGON_INTERACTIVE = 2
_LOGON32_PROVIDER_DEFAULT = 0
_TOKEN_QUERY = 0x0008
_TOKEN_GROUPS = 2
_TOKEN_ELEVATION = 20
_TOKEN_INTEGRITY_LEVEL = 25
_WIN_BUILTIN_ADMINISTRATORS_SID = 26
_SE_GROUP_ENABLED = 0x00000004
_SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010

_INTEGRITY_NAMES = {
    0x0000: 'Untrusted',
    0x1000: 'Low',
    0x2000: 'Medium',
    0x2100: 'Medium Plus',
    0x3000: 'High(已提升)',
    0x4000: 'System',
    0x5000: 'Protected',
}


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", wintypes.LPVOID),
        ("Attributes", wintypes.DWORD),
    ]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [
        ("Label", _SID_AND_ATTRIBUTES),
    ]


class _TOKEN_GROUPS_STRUCT(ctypes.Structure):
    _fields_ = [
        ("GroupCount", wintypes.DWORD),
        ("Groups", _SID_AND_ATTRIBUTES * 1),
    ]


def _split_account(username):
    """把 '.\\lingtong' 拆成 (域, 账号) —— LogonUser 需要分开传"""
    if username.startswith('.\\') or username.startswith('./'):
        return '.', username[2:]
    if '\\' in username:
        domain, _, user = username.partition('\\')
        return domain, user
    return None, username


def _sid_last_rid(psid):
    """取 SID 的最后一个子授权（RID）。内置 Administrator 的 RID 是 500"""
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        get_count = advapi32.GetSidSubAuthorityCount
        get_count.restype = ctypes.POINTER(ctypes.c_ubyte)
        get_count.argtypes = [wintypes.LPVOID]
        get_sub = advapi32.GetSidSubAuthority
        get_sub.restype = ctypes.POINTER(wintypes.DWORD)
        get_sub.argtypes = [wintypes.LPVOID, wintypes.DWORD]
        cnt = get_count(psid)
        if not cnt:
            return None
        n = cnt.contents.value
        if not n:
            return None
        return int(get_sub(psid, n - 1).contents.value)
    except Exception:
        return None


def _well_known_sid(sid_type):
    """CreateWellKnownSid -> SID 缓冲区（返回的 buffer 自行持有生命周期）"""
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        create = advapi32.CreateWellKnownSid
        create.restype = wintypes.BOOL
        create.argtypes = [ctypes.c_int, wintypes.LPVOID,
                           wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD)]
        need = wintypes.DWORD(0)
        create(sid_type, None, None, ctypes.byref(need))
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if create(sid_type, None, buf, ctypes.byref(need)):
            return buf
        return None
    except Exception:
        return None


def _token_admin_group_state(h_token):
    """检查令牌里 Administrators(S-1-5-21-...-544) 的真实状态

    **不要用 CheckTokenMembership**：它要求传入**模拟令牌**，而 LogonUser 返回的是
    主令牌，调用会直接失败——这正是之前日志里打印出 "有效属于 Administrators = None"
    的原因。改用 GetTokenInformation(TokenGroups) 直接看组列表和它的属性位。

    :return: (状态, 说明)
        'absent'    令牌里没有 Administrators 组 -> 该账户**根本不是管理员**
        'deny_only' 有组但被标记 deny-only      -> 是管理员，但被 UAC 过滤了
        'enabled'   有组且已启用                -> 完整的管理员令牌（已提升）
        'error'     探测失败
    """
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        get_info = advapi32.GetTokenInformation
        get_info.restype = wintypes.BOOL
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

        admin_sid = _well_known_sid(_WIN_BUILTIN_ADMINISTRATORS_SID)
        if admin_sid is None:
            return 'error', 'CreateWellKnownSid 失败'

        need = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        get_info(h_token, _TOKEN_GROUPS, None, 0, ctypes.byref(need))
        if not need.value:
            return 'error', f'TokenGroups 取长度失败(Win32 {ctypes.get_last_error()})'
        buf = ctypes.create_string_buffer(need.value)
        ctypes.set_last_error(0)
        if not get_info(h_token, _TOKEN_GROUPS, buf, need.value, ctypes.byref(need)):
            return 'error', f'TokenGroups 调用失败(Win32 {ctypes.get_last_error()})'

        groups = ctypes.cast(buf, ctypes.POINTER(_TOKEN_GROUPS_STRUCT)).contents
        # 数组相对结构体起始的偏移（x64=8 / x86=4，不用硬编码，直接从实例算）
        base = ctypes.addressof(buf)
        arr = ctypes.addressof(groups.Groups) - base
        step = ctypes.sizeof(_SID_AND_ATTRIBUTES)

        equal = advapi32.EqualSid
        equal.restype = wintypes.BOOL
        equal.argtypes = [wintypes.LPVOID, wintypes.LPVOID]

        attrs = None
        count = int(groups.GroupCount)
        for i in range(count):
            item = ctypes.cast(base + arr + i * step,
                               ctypes.POINTER(_SID_AND_ATTRIBUTES)).contents
            if item.Sid and equal(item.Sid, admin_sid):
                attrs = int(item.Attributes)
                break

        if attrs is None:
            return 'absent', f'令牌 {count} 个组里没有 Administrators'
        if attrs & _SE_GROUP_USE_FOR_DENY_ONLY:
            return 'deny_only', f'Administrators 为 deny-only(属性 0x{attrs:X})'
        if attrs & _SE_GROUP_ENABLED:
            return 'enabled', f'Administrators 已启用(属性 0x{attrs:X})'
        return 'disabled', f'Administrators 未启用(属性 0x{attrs:X})'
    except Exception as e:
        return 'error', f'TokenGroups 异常: {e}'


def _current_process_admin_group_state():
    """当前进程令牌里 Administrators 组的状态

    这个值直接决定"计划任务 /RL HIGHEST 提权"这条路通不通：
      deny_only / enabled -> 当前账户是管理员（哪怕被 UAC 过滤），schtasks 放行
      absent              -> 当前账户不是管理员，schtasks /create 会回"拒绝访问"

    :return: (状态, 说明)，状态取值同 _token_admin_group_state
    """
    h_token = wintypes.HANDLE()
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        ctypes.set_last_error(0)
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY,
                                         ctypes.byref(h_token)):
            return 'error', f'OpenProcessToken 失败(Win32 {ctypes.get_last_error()})'
        return _token_admin_group_state(h_token)
    except Exception as e:
        return 'error', f'当前令牌探测异常: {e}'
    finally:
        if h_token:
            try:
                ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(h_token)
            except Exception:
                pass


def _account_sid(username):
    """LookupAccountNameW 取账号 SID —— 普通用户权限即可调用（不走 SAM ACL）

    :return: (SID 字符串, RID, 错误信息)
    """
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        lookup = advapi32.LookupAccountNameW
        lookup.restype = wintypes.BOOL
        lookup.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                           wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD),
                           wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
                           ctypes.POINTER(wintypes.DWORD)]
        cb_sid = wintypes.DWORD(0)
        cb_dom = wintypes.DWORD(0)
        use = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        lookup(None, username, None, ctypes.byref(cb_sid),
               None, ctypes.byref(cb_dom), ctypes.byref(use))
        if not cb_sid.value:
            return '', None, f"LookupAccountNameW 预查询失败(Win32 {ctypes.get_last_error()})"
        sid_buf = ctypes.create_string_buffer(cb_sid.value)
        dom_buf = ctypes.create_unicode_buffer(cb_dom.value + 1)
        ctypes.set_last_error(0)
        if not lookup(None, username, sid_buf, ctypes.byref(cb_sid),
                      dom_buf, ctypes.byref(cb_dom), ctypes.byref(use)):
            return '', None, f"LookupAccountNameW 失败(Win32 {ctypes.get_last_error()})"

        convert = advapi32.ConvertSidToStringSidW
        convert.restype = wintypes.BOOL
        convert.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        str_ptr = wintypes.LPWSTR()
        sid_text = ''
        if convert(sid_buf, ctypes.byref(str_ptr)):
            sid_text = str_ptr.value or ''
            try:
                ctypes.WinDLL('kernel32', use_last_error=True).LocalFree(str_ptr)
            except Exception:
                pass
        return sid_text, _sid_last_rid(sid_buf), ''
    except Exception as e:
        return '', None, f"LookupAccountNameW 异常: {e}"


def _probe_logon_token(password):
    """用 lingtong 凭据 LogonUser，检查拿到的令牌有没有被 UAC 过滤

    :return: dict(ok, elevated, integrity, admin_group, admin_group_detail, error)
    """
    result = {'ok': False, 'elevated': None, 'integrity': '',
              'admin_group': '', 'admin_group_detail': '', 'error': ''}
    h_token = wintypes.HANDLE()
    try:
        domain, user = _split_account(LINGTONG_USER)
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

        logon = advapi32.LogonUserW
        logon.restype = wintypes.BOOL
        logon.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                          wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        ctypes.set_last_error(0)
        if not logon(user, domain, password, _LOGON32_LOGON_INTERACTIVE,
                     _LOGON32_PROVIDER_DEFAULT, ctypes.byref(h_token)):
            result['error'] = f"LogonUser 失败: Win32 {ctypes.get_last_error()}"
            return result
        result['ok'] = True

        get_info = advapi32.GetTokenInformation
        get_info.restype = wintypes.BOOL
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

        # 1) TokenElevation
        elev = wintypes.DWORD(0)
        need = wintypes.DWORD(0)
        if get_info(h_token, _TOKEN_ELEVATION, ctypes.byref(elev),
                    ctypes.sizeof(elev), ctypes.byref(need)):
            result['elevated'] = bool(elev.value)
        else:
            result['elevated'] = False

        # 2) TokenIntegrityLevel -> 完整性级别
        need = wintypes.DWORD(0)
        get_info(h_token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(need))
        if need.value:
            buf = ctypes.create_string_buffer(need.value)
            if get_info(h_token, _TOKEN_INTEGRITY_LEVEL, buf, need.value, ctypes.byref(need)):
                label = ctypes.cast(buf, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
                rid = _sid_last_rid(label.Label.Sid)
                if rid is not None:
                    result['integrity'] = _INTEGRITY_NAMES.get(rid, f'0x{rid:04X}')

        # 3) Administrators 组在令牌里的真实状态（走 TokenGroups，见该函数注释）
        state, detail = _token_admin_group_state(h_token)
        result['admin_group'] = state
        result['admin_group_detail'] = detail
    except Exception as e:
        result['error'] = f"探测异常: {e}"
    finally:
        if h_token:
            try:
                ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(h_token)
            except Exception:
                pass
    return result


def _report_lingtong_token(password):
    """探测 lingtong 令牌并打印"能不能静默安装"的结论，返回探测结果

    这是排查 740 的关键一步：把"令牌被过滤"和"lingtong 不是管理员"区分开。
    """
    _log("-" * 70)
    _log("[令牌] 以 lingtong 凭据做一次 LogonUser 探测（只读，不影响后续流程）")

    sid_text, rid, sid_err = _account_sid(_task_user_name())
    is_builtin_admin = None
    if sid_text:
        is_builtin_admin = (rid == 500)
        _log(f"[令牌] lingtong SID = {sid_text}（RID={rid}"
             f"{'，即内置 Administrator' if is_builtin_admin else ''}）")
    else:
        _log(f"[令牌] 取 lingtong SID 失败: {sid_err}")

    probe = _probe_logon_token(password)
    if not probe.get('ok'):
        _log(f"[令牌] 探测失败: {probe.get('error')}")
        _log("-" * 70)
        return probe

    _log(f"[令牌] 完整性级别 = {probe.get('integrity') or '未知'}"
         f" / TokenElevation = {probe.get('elevated')}")
    _log(f"[令牌] Administrators 组状态 = {probe.get('admin_group')}"
         f"（{probe.get('admin_group_detail')}）")

    if probe.get('elevated'):
        _log("[令牌] 结论: 令牌**已提升**(High IL)，可以静默启动要求管理员权限的安装包")
        _log("-" * 70)
        return probe

    state = probe.get('admin_group')
    _log("[令牌] 结论: 令牌**未提升** —— 以 lingtong 身份启动 requireAdministrator 的")
    _log("[令牌]       安装包（QQ.exe / QQ音乐.exe 等）必然失败并返回 740。")
    _log("[令牌]       调用方自己是不是管理员**不影响**结果，这是")
    _log("[令牌]       CreateProcessWithLogonW/seclogon 的固有行为，代码绕不过去。")

    if state == 'absent':
        _log("[令牌] 原因: 令牌里**没有 Administrators 组** —— lingtong 在这台机器上")
        _log(f"[令牌]       根本不是管理员（SID RID={rid}，内置 Administrator 是 RID 500）。")
        _log("[令牌]       这种情况**光关 UAC 也没用**，必须先把它加进管理员组。")
    elif state == 'deny_only':
        _log("[令牌] 原因: Administrators 组在令牌里被标成 **deny-only** —— 这正是 UAC")
        _log("[令牌]       令牌过滤的表现：lingtong 是管理员，但拿到的是受限令牌。")
    elif state in ('enabled', 'disabled'):
        _log(f"[令牌] 原因: Administrators 组属性异常"
             f"（{probe.get('admin_group_detail')}），但 TokenElevation 仍为 False")
    else:
        _log(f"[令牌] 原因: 组成员判定失败（{probe.get('admin_group_detail')}）")

    _log("[令牌] 解决（需在**目标机**上用管理员身份做，做完重启）:")
    if state == 'absent':
        _log("[令牌]   0. 先加入管理员组: net localgroup Administrators lingtong /add")
    _log('[令牌]   A. 关闭 UAC：HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion'
         '\\Policies\\System 下 EnableLUA=0')
    _log("[令牌]   B. 让 lingtong 成为**内置 Administrator(RID 500)** —— 内置管理员默认")
    _log("[令牌]      不受令牌过滤，UAC 开着也能拿到完整令牌。做法: 启用并把内置管理员")
    _log("[令牌]      重命名为 lingtong，密码保持与 password.pem 一致")
    _log("-" * 70)
    return probe


def _log_startup_banner():
    """服务器启动时打印一次基础环境信息"""
    _log("=" * 70)
    _log(f"[环境] 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _log(f"[环境] python: {sys.executable}")
    _log(f"[环境] 版本: {sys.version.splitlines()[0]}")
    _log(f"[环境] 脚本目录: {_script_dir()}")
    _log(f"[环境] 当前目录: {os.getcwd()}")
    _log(f"[环境] 当前用户: {os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}")
    _log(f"[环境] 本进程是否已提升(管理员): {_is_process_elevated()}")
    grp_state, grp_detail = _current_process_admin_group_state()
    _log(f"[环境] 当前账户 Administrators 组状态 = {grp_state}（{grp_detail}）")
    if grp_state == 'deny_only':
        _log("[环境] 当前账户是**管理员但被 UAC 过滤**(deny_only)：可尝试『计划任务"
             " /RL HIGHEST』——Task Scheduler 会给出未过滤的完整令牌")
    elif grp_state == 'enabled':
        _log("[环境] 当前账户持有**完整管理员令牌**：计划任务 /RL HIGHEST 同样可行")
    elif grp_state == 'absent':
        _log("[环境] 当前账户**不在 Administrators 组**：计划任务 /RL HIGHEST 会被"
             "『拒绝访问』回绝，本机没有后台静默安装 requireAdministrator 包的可行路径")
    uac = _read_uac_settings()
    for k, v in uac.items():
        _log(f"[环境] UAC {k} = {v}")
    if uac.get('EnableLUA') == 1:
        _log("[环境] UAC 已开启(EnableLUA=1)：以 lingtong 身份启动的进程会拿到被过滤的普通令牌，"
             "若安装包清单要求管理员权限则会返回 740；"
             "FilterAdministratorToken=0 时内置 Administrator 账户不受该过滤影响")
    _log("=" * 70)


def _collect_diagnostics(exe_path, args):
    """启动安装程序前打印环境诊断"""
    _log("-" * 70)
    _log("[诊断] 启动安装程序前的检查")
    _log("-" * 70)

    _log(f"[诊断] 下载服务进程是否已提升: {_is_process_elevated()}（与安装身份无关，仅作参考）")
    _log(f"[诊断] 安装将以 {LINGTONG_USER} 身份在后台静默运行，不弹 UAC、不弹任何确认框")

    _log(f"[诊断] 目标程序: {exe_path}")
    _log(f"[诊断] 目标程序存在: {os.path.exists(exe_path)}")
    if os.path.exists(exe_path):
        try:
            _log(f"[诊断] 目标程序大小: {os.path.getsize(exe_path) / (1024 * 1024):.2f} MB")
        except Exception:
            pass
    if args:
        _log(f"[诊断] 安装参数: {' '.join(args)}")

    # 注意：普通用户执行 `net user <name>` / `net localgroup Administrators` 会
    # 返回 WinError 5 拒绝访问（缺 SAM 读权限），所以账号存在性、管理员组成员
    # 判断一律改走 ctypes（LookupAccountNameW + GetTokenInformation(TokenGroups)），
    # 见上面的 _report_lingtong_token()。
    rc, out = _run(['query', 'user'])
    _log(f"[诊断] query user -> 返回码 {rc}")
    for line in out.splitlines():
        if line.strip():
            _log(f"[诊断]   {line.rstrip()}")
    _log("-" * 70)


# ==================== 首选：ctypes 直接调用 CreateProcessWithLogonW（后台静默） ====================

class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_WIN32_ERR_HINTS = {
    2: "ERROR_FILE_NOT_FOUND：找不到指定的文件（检查安装包路径）",
    5: "ERROR_ACCESS_DENIED：拒绝访问",
    740: "ERROR_ELEVATION_REQUIRED：目标程序清单要求管理员权限，"
         "而 CreateProcessWithLogonW 无法创建提升的进程"
         "（UAC 开启时它只产生被过滤的普通令牌）",
    1326: "ERROR_LOGON_FAILURE：用户名或密码错误",
    1327: "ERROR_ACCOUNT_RESTRICTION：账户限制（空密码 / 账户被禁用 / 登录时段限制）",
    1385: "ERROR_LOGON_TYPE_NOT_GRANTED：该账户没有『允许本地登录』的权限",
    1907: "ERROR_PASSWORD_MUST_CHANGE：用户必须在首次登录前修改密码",
    1909: "ERROR_ACCOUNT_LOCKED_OUT：账户已被锁定",
    1789: "ERROR_NO_SUCH_LOGON_SESSION / 计划任务方式下常见：该账户缺少『作为批处理作业登录』权限",
    1783: "RPC_X_BAD_STUB_DATA：seclogon 服务没能把参数/目标用户配置传下去"
          "（多与 LOGON_WITH_PROFILE 加载用户配置失败有关，代码会自动换参数重试）",
}


def _cpwl_once(username, password, logon_flags, creation_flags, desktop, cmdline,
               cwd=None):
    """调用一次 CreateProcessWithLogonW，返回 (ok, err, err_text)

    lpUsername / lpDomain 拆开传（'.\\lingtong' -> domain='.', user='lingtong'）：
    把 '.\\lingtong' 整体当用户名传时 seclogon 侧解析容易失败，实测会返回 1783。

    cwd 是 **必须显式指定** 的：传 None 时子进程会继承调用方的当前目录，而调用方
    的目录往往在某个用户的 profile 下（例：D:\\...\\d10460_chenzhiyu\\Desktop\\...），
    lingtong 拿到的是被 UAC 过滤的令牌、Administrators 那条 ACE 不生效，
    很可能连进都进不去 —— cmd.exe 会因此立刻退出，表现为"进程起来了但什么都没干"。
    """
    domain, user = _split_account(username)
    cmd_buf = ctypes.create_unicode_buffer(cmdline)
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    si.lpDesktop = desktop
    pi = _PROCESS_INFORMATION()

    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    fn = advapi32.CreateProcessWithLogonW
    fn.restype = wintypes.BOOL
    fn.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    ctypes.set_last_error(0)
    ok = fn(user, domain, password, logon_flags, None, cmd_buf,
            creation_flags, None, cwd, ctypes.byref(si), ctypes.byref(pi))
    err = ctypes.get_last_error()

    if ok:
        pid = pi.dwProcessId
        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
        except Exception:
            pass
        return True, 0, f"已启动 (PID={pid})"

    try:
        text = ctypes.FormatError(err)
    except Exception:
        text = ''
    hint = _WIN32_ERR_HINTS.get(err, '')
    return False, err, f"Win32 错误码 {err}: {text}" + (f"  ==> {hint}" if hint else '')


def _launch_with_createprocesswithlogonw(exe_path, args, username, password):
    """ctypes 直接调用 CreateProcessWithLogonW（与 Start-Process -Credential 同一 API）

    相比 PowerShell 封装，这里能拿到原始 Win32 错误码，可以精确区分
    1326(密码错) / 1385(无本地登录权) / 740(需要提升) 等情况。

    先用 LOGON_WITH_PROFILE 调一次；若报 1783(RPC_X_BAD_STUB_DATA，多与加载
    目标用户配置有关)，再退化为不带 profile、显式指定 winsta0\\default 重试。

    :return: (ok, err_code, err_text)
    """
    cmdline = subprocess.list2cmdline([exe_path] + list(args))
    _log(f"[API ] CreateProcessWithLogonW 命令行: {cmdline}")

    LOGON_WITH_PROFILE = 0x00000001
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    plan = (
        ("带 profile", LOGON_WITH_PROFILE, CREATE_UNICODE_ENVIRONMENT, None),
        ("不带 profile", 0, 0, "winsta0\\default"),
    )

    last = (False, -1, '')
    for label, logon_flags, creation_flags, desktop in plan:
        try:
            ok, err, err_text = _cpwl_once(username, password, logon_flags,
                                           creation_flags, desktop, cmdline)
        except Exception as e:
            ok, err, err_text = False, -1, f"调用异常: {e}"
        _log(f"[API ] ({label}) {'成功' if ok else '失败'} -> {err_text}")
        if ok:
            return True, 0, err_text
        last = (False, err, err_text)
        # 只有"参数/结构"类错误才值得换一组参数重试，凭据类错误重试没意义
        if err not in (1783, 87, 123):
            break
    return last


# ============ 首选（需本进程已提权）：Batch 登录 + CreateProcessWithTokenW ============

def _token_elevation_and_integrity(h_token):
    """读一个令牌的"是否已提升"和完整性级别

    :return: (elevated: bool|None, integrity: str)
    """
    elev = None
    integrity = ''
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        get_info = advapi32.GetTokenInformation
        get_info.restype = wintypes.BOOL
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        ev = wintypes.DWORD(0)
        need = wintypes.DWORD(0)
        if get_info(h_token, _TOKEN_ELEVATION, ctypes.byref(ev),
                    ctypes.sizeof(ev), ctypes.byref(need)):
            elev = bool(ev.value)
        need = wintypes.DWORD(0)
        get_info(h_token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(need))
        if need.value:
            ibuf = ctypes.create_string_buffer(need.value)
            if get_info(h_token, _TOKEN_INTEGRITY_LEVEL, ibuf, need.value,
                        ctypes.byref(need)):
                label = ctypes.cast(ibuf,
                                    ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
                rid = _sid_last_rid(label.Label.Sid)
                if rid is not None:
                    integrity = _INTEGRITY_NAMES.get(rid, f'0x{rid:04X}')
    except Exception:
        pass
    return elev, integrity


def _launch_direct_elevated(exe_path, args):
    """本进程已提权时最直接的一条：直接用本进程自己的令牌启动安装程序

    已提升的令牌去创建"要求管理员"的子进程不会被拦（740 只发生在令牌未提升时），
    所以这条最简单、最不容易出岔子。安装身份 = 当前账户：

      · 本进程身份已经是 lingtong（普通账户走 UAC 时填 lingtong 凭据提权启动，
        就是这种情形）→ 身份完全符合"以 lingtong 安装"的设计；
      · 本进程是别的管理员账户 → 软件照样能装上，但 HKCU 等会落在那个账户下，
        与设计不一致，调用方会明确提示。

    :return: (ok, 提示信息)
    """
    cmd = [exe_path] + list(args)
    try:
        proc = subprocess.Popen(cmd)
    except OSError as e:
        _log(f"[直启] Popen 失败: {e}")
        return False, f'直接启动失败: {e}'
    _log(f"[直启] 已用本进程的完整令牌启动: {subprocess.list2cmdline(cmd)} (PID={proc.pid})")
    return True, f'已用本进程的完整令牌直接启动安装程序 (PID={proc.pid})'


def _launch_with_batch_token(exe_path, args, username, password):
    """**本进程已提权时**的首选：Batch 登录拿未过滤令牌，再以 lingtong 身份启动安装程序

    为什么必须本进程已提权：`CreateProcessWithTokenW` 要求调用方持有
    `SeImpersonatePrivilege`，普通用户令牌里没有这个权限，提权后的管理员令牌才有。

    为什么 Batch 登录不过滤：UAC 的令牌过滤只作用于**交互式**登录。Task Scheduler
    就是靠 Batch 登录拿到完整令牌的（所谓"计划任务绕过 UAC"的原理）。所以这里用
    LOGON32_LOGON_BATCH，而不是 LOGON32_LOGON_INTERACTIVE。

    前提：lingtong 要有"作为批处理作业登录"(SeBatchLogonRight)，否则 LogonUser
    返回 1385 ERROR_LOGON_TYPE_NOT_GRANTED。

    :return: (ok, 提示信息)
    """
    domain, user = _split_account(username)
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    h_token = wintypes.HANDLE()
    try:
        logon = advapi32.LogonUserW
        logon.restype = wintypes.BOOL
        logon.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                          wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(wintypes.HANDLE)]
        ctypes.set_last_error(0)
        if not logon(user, domain, password, _LOGON32_LOGON_BATCH,
                     _LOGON32_PROVIDER_DEFAULT, ctypes.byref(h_token)):
            err = ctypes.get_last_error()
            try:
                text = ctypes.FormatError(err)
            except Exception:
                text = ''
            hint = ''
            if err == 1385:
                hint = ('  ==> lingtong 缺少"作为批处理作业登录"权限。已提权时可以用:'
                        ' secedit 或在"本地安全策略-用户权限分配"里加上；'
                        '这一步不行就退回方式 3（计划任务）')
            _log(f"[Batch] LogonUser(BATCH) 失败 Win32 {err}: {text}")
            return False, f'Batch 登录失败: Win32 {err}: {text}{hint}'

        elev, integrity = _token_elevation_and_integrity(h_token)
        _log(f"[Batch] lingtong Batch 令牌: 已提升={elev} 完整性级别={integrity or '未知'}"
             "（Batch 登录不做 UAC 过滤，这里应该看到 True/High）")
        if elev is False:
            _log("[Batch] 警告: 拿到的令牌仍然未提升 —— 这台机器可能对批处理登录"
                 "也做了过滤，后续启动仍可能 740")

        cmdline = subprocess.list2cmdline([exe_path] + list(args))
        cmd_buf = ctypes.create_unicode_buffer(cmdline)
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        pi = _PROCESS_INFORMATION()
        fn = advapi32.CreateProcessWithTokenW
        fn.restype = wintypes.BOOL
        fn.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPCWSTR,
                       wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
                       ctypes.POINTER(_PROCESS_INFORMATION)]
        ctypes.set_last_error(0)
        ok = fn(h_token, _LOGON_WITH_PROFILE, None, cmd_buf,
                _CREATE_UNICODE_ENVIRONMENT, None, None,
                ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        if ok:
            pid = pi.dwProcessId
            try:
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.CloseHandle(pi.hProcess)
                kernel32.CloseHandle(pi.hThread)
            except Exception:
                pass
            _log(f"[Batch] CreateProcessWithTokenW 成功: {cmdline} (PID={pid})")
            return True, f'已以 {LINGTONG_USER} 的完整令牌启动安装程序 (PID={pid})'

        try:
            text = ctypes.FormatError(err)
        except Exception:
            text = ''
        hint2 = ''
        if err == 740:
            hint2 = ('  ==> 连 Batch 令牌也被判定为"未提升"，说明这台机器对批处理'
                     '登录同样过滤，退回方式 3（计划任务）')
        elif err == 1260:
            hint2 = ('  ==> ERROR_ACCESS_DISABLED_BY_POLICY：被软件限制策略/AppLocker '
                     '拦住，需要把安装包放到策略允许的目录')
        _log(f"[Batch] CreateProcessWithTokenW 失败 Win32 {err}: {text}{hint2}")
        return False, f'CreateProcessWithTokenW 失败: Win32 {err}: {text}{hint2}'
    finally:
        if h_token:
            try:
                ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(h_token)
            except Exception:
                pass


# ==================== 回退：PowerShell Start-Process -Credential（同一 API） ====================

def _launch_via_powershell_credential(exe_path, args, password):
    """原有实现：PowerShell Start-Process -Credential，保留用于对照"""
    start_process = f"Start-Process -FilePath {_ps_quote(exe_path)}"
    if args:
        start_process += " -ArgumentList " + ",".join(_ps_quote(a) for a in args)
    start_process += " -Credential $cred"

    # 说明：
    # 1) 用 .NET 的 SecureString 逐字符构造密码，而不用 ConvertTo-SecureString。
    #    后者依赖 Microsoft.PowerShell.Security 模块的自动加载，在部分环境下会加载失败
    #    （报 "the module could not be loaded"）；SecureString 是 .NET 基础类型，始终可用。
    # 2) 用 try/catch 把错误信息写入标准输出，避免 PowerShell 把错误序列化成 CLIXML 噪声。
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "try {",
        "    $ss = New-Object System.Security.SecureString",
        f"    foreach ($c in {_ps_quote(password)}.ToCharArray()) {{ $ss.AppendChar($c) }}",
        "    $ss.MakeReadOnly()",
        f"    $cred = New-Object System.Management.Automation.PSCredential({_ps_quote(LINGTONG_USER)}, $ss)",
        "    " + start_process,
        "} catch {",
        "    Write-Output ('ERR: ' + $_.Exception.Message)",
        "    exit 1",
        "}",
    ])

    _log("[PS  ] 脚本:\n" + _mask(script, password))

    # 使用 -EncodedCommand 传递，避免密码中的特殊字符被命令行解析
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
        capture_output=True
    )
    out = _decode_console(result.stdout)
    err = _decode_console(result.stderr)
    _log(f"[PS  ] 返回码: {result.returncode}")
    if out.strip():
        _log(f"[PS  ] 标准输出: {out.strip()}")
    if err.strip():
        _log(f"[PS  ] 标准错误: {err.strip()}")

    if result.returncode == 0:
        return True, f"安装程序已以 {LINGTONG_USER} 身份启动"

    detail = ''
    for line in out.splitlines():
        if line.startswith('ERR:'):
            detail = line[len('ERR:'):].strip()
            break
    if not detail:
        detail = (err or out).strip()
    message = f"以 {LINGTONG_USER} 身份启动安装程序失败，返回代码: {result.returncode}"
    if detail:
        message += f"\n错误信息: {detail}"
    return False, message


# ============ 方式三：计划任务 /RL HIGHEST（Task Scheduler 给出未过滤令牌） ============

_SCHTASK_NAME = 'NetworkzCopy_LingtongInstall'
_SCHTASK_LAUNCHER = 'lingtong_install_launcher.cmd'
_SCHTASK_ELEVATE = 'lingtong_elevate.cmd'
_SCHTASK_SENTINEL = 'lingtong_install_launcher.ran'
_SCHTASK_RESULT = 'lingtong_install_result.txt'
_SCHTASK_LOG = 'lingtong_elevate.log'
_CHANNEL_READY = False


def _schtask_channel_dir():
    """当前用户 ↔ lingtong 之间的"通道目录"（双方都必须能读写）

    为什么单独开一个子目录，而且必须显式授权：
      跑 schtasks 的是 **被 UAC 过滤的 lingtong**（中等完整性令牌），它身上
      Administrators 那条 ACE **不生效**，只有 Users 组成员身份算数。如果目录只对
      创建者本人 + Administrators 开放，lingtong 连一个日志文件都建不出来 ——
      实测表现就是"lingtong 进程起来了、PID 也有、但一条输出都看不到、计划任务也没建"。
    只在这个专用子目录上授权，安装包本身仍放在上层目录，不额外扩大暴露面。
    """
    return os.path.join(INSTALL_TEMP_DIR, 'lingtong_channel')


def _ensure_channel_dir():
    """建好通道目录并给 Users 授予 Modify（幂等，进程内只做一次）"""
    global _CHANNEL_READY
    d = _schtask_channel_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        _log(f"[通道] 创建目录失败: {e}")
        return d
    if _CHANNEL_READY:
        return d
    # S-1-5-32-545 = Users；加 (OI)(CI) 让子对象继承
    rc, out = _run(['icacls', d, '/grant', '*S-1-5-32-545:(OI)(CI)M'])
    if rc == 0:
        _log(f"[通道] 已把 Modify 权限授予 Users: {d}")
    else:
        _log(f"[通道] icacls 授权失败(返回码 {rc})，lingtong 可能写不进这个目录: "
             f"{out.strip()[:200]}")
    _CHANNEL_READY = True
    return d


def _schtask_sentinel_path():
    """启动脚本"已执行"标记文件路径（用于确定性判断任务有没有真的跑起来）

    放在通道目录（不是 %TEMP%）有两个原因：
      ① 写它的是**已提升**的任务进程，读它的是当前普通账户，这个目录双方都够得着；
      ② 路径不含空格 —— 当前用户 %TEMP% 形如 C:\\Users\\<用户名>\\AppData\\...，
         用户名带空格时 schtasks 的 `/tr` 会被拆开。
    """
    return os.path.join(_schtask_channel_dir(), _SCHTASK_SENTINEL)


def _schtask_result_path():
    """安装程序退出码落盘路径

    计划任务方式下安装进程在 session 0，我们看不到它的任何输出，而任务本身是
    "启动完就返回"的 —— 光知道"启动了"没有意义，必须让安装程序退出时把
    `%ERRORLEVEL%` 落盘，才能判断到底装成功没有（0 = 安装包自报成功）。
    """
    return os.path.join(_schtask_channel_dir(), _SCHTASK_RESULT)


def _schtask_log_path():
    """lingtong 侧 schtasks 输出日志路径（必须在通道目录里，见 _schtask_channel_dir）"""
    return os.path.join(_schtask_channel_dir(), _SCHTASK_LOG)


def _read_elevate_log(path):
    """读 lingtong 侧 schtasks 的输出日志（中文 Windows 下是 GBK），读不到返回 ''"""
    try:
        with open(path, 'r', encoding='gbk', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return ''


def _cpwl_run_wait(username, password, cmdline, cwd=None, timeout=30):
    """以指定账户启动进程并**等它结束**，返回 (ok, exit_code, 说明)

    和 `_cpwl_once` 的唯一区别是保留句柄、WaitForSingleObject + GetExitCodeProcess。
    像"直接调 schtasks.exe"这种没有脚本包着的调用，拿不到任何输出，**退出码就是
    唯一线索**，所以必须等它结束。
    """
    domain, user = _split_account(username)
    cmd_buf = ctypes.create_unicode_buffer(cmdline)
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    pi = _PROCESS_INFORMATION()
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    fn = advapi32.CreateProcessWithLogonW
    fn.restype = wintypes.BOOL
    fn.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    ctypes.set_last_error(0)
    ok = fn(user, domain, password, _LOGON_WITH_PROFILE, None, cmd_buf,
            _CREATE_UNICODE_ENVIRONMENT, None, cwd, ctypes.byref(si), ctypes.byref(pi))
    err = ctypes.get_last_error()
    if not ok:
        try:
            text = ctypes.FormatError(err)
        except Exception:
            text = ''
        return False, None, f"CreateProcessWithLogonW 失败 Win32 {err}: {text}"

    code = None
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    try:
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        if kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == 0:
            buf = wintypes.DWORD(0)
            if kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(buf)):
                code = int(buf.value)
    finally:
        try:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
        except Exception:
            pass
    return True, code, (f"已结束，退出码 {code}" if code is not None
                        else f"{timeout} 秒内没结束")


def _write_bat(path, lines):
    """按 cmd 原生编码(mbcs)写一个批处理（中文路径不乱码）"""
    content = '\r\n'.join(list(lines) + [''])
    for enc in ('mbcs', 'utf-8-sig'):
        try:
            with open(path, 'w', encoding=enc) as f:
                f.write(content)
            return True
        except (UnicodeEncodeError, LookupError):
            continue
        except OSError:
            return False
    return False


_PROBE_TASK = 'NetworkzCopy_Probe'
_PROBE1 = 'probe_1_write.txt'
_PROBE2 = 'probe_2_script.txt'
_PROBE_SCRIPT = 'probe_2.cmd'


def _channel_selftest(password):
    """把"lingtong 侧什么都没发生"拆成三个可独立验证的环节

    现象：lingtong 的 cmd.exe 起来了（有 PID），但提权脚本连一行输出都没有、计划任务
    也没建。三种可能完全不同，必须分开验，否则每轮都只能猜：
      T1 进程能不能起来 / 目录能不能写 —— 用 `cmd /c echo > 文件`，**不涉及脚本文件**
      T2 .cmd 脚本能不能执行 —— 写一个 .cmd 由它自己落文件（软件限制策略/SRP 常拦脚本）
      T3 schtasks 能不能以 lingtong 身份建最高权限任务 —— 直接调 schtasks.exe，不套脚本

    :return: (t1, t2, t3) 三个布尔
    """
    ch = _ensure_channel_dir()
    principal = f'{os.environ.get("COMPUTERNAME") or "."}\\{_task_user_name()}'
    _log("-" * 70)
    _log("[自检] lingtong 通道自检（每一项都以文件是否落下作为证据）")

    # ---- T1: 能不能起来 + 能不能往通道目录写 ----
    p1 = os.path.join(ch, _PROBE1)
    try:
        if os.path.exists(p1):
            os.remove(p1)
    except OSError:
        pass
    ok1, code1, text1 = _cpwl_run_wait(LINGTONG_USER, password,
                                       f'cmd /c echo t1 > "{p1}"', cwd=ch)
    t1 = os.path.exists(p1)
    _log(f"[自检] T1 起进程+写目录: {'通过' if t1 else '失败'}"
         f"  (ok={ok1} 退出码={code1} {text1})")

    # ---- T2: .cmd 脚本能不能执行 ----
    p2 = os.path.join(ch, _PROBE2)
    script2 = os.path.join(ch, _PROBE_SCRIPT)
    for f in (p2,):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    if _write_bat(script2, ['@echo off', f'echo t2 > "{p2}"']):
        ok2, code2, text2 = _cpwl_run_wait(LINGTONG_USER, password,
                                           f'cmd /c "{script2}"', cwd=ch)
        t2 = os.path.exists(p2)
        _log(f"[自检] T2 执行 .cmd 脚本: {'通过' if t2 else '失败'}"
             f"  (ok={ok2} 退出码={code2} {text2})")
    else:
        t2 = False
        _log("[自检] T2 执行 .cmd 脚本: 失败（连探针脚本都写不出来）")

    # ---- T3: 以 lingtong 身份直接调 schtasks（不套脚本）----
    _run_as_lingtong(['schtasks', '/delete', '/f', '/tn', _PROBE_TASK], password)
    create = subprocess.list2cmdline([
        'schtasks', '/create', '/tn', _PROBE_TASK, '/tr',
        os.path.join(ch, _SCHTASK_LAUNCHER), '/sc', 'ONCE', '/st', '23:59',
        '/ru', principal, '/rp', password, '/rl', 'HIGHEST', '/f'])
    ok3, code3, text3 = _cpwl_run_wait(LINGTONG_USER, password, create, cwd=ch)
    rc_q, out_q = _run(['schtasks', '/query', '/tn', _PROBE_TASK])
    t3 = (rc_q == 0)
    _log(f"[自检] T3 以 lingtong 身份建最高权限任务: {'通过' if t3 else '失败'}"
         f"  (ok={ok3} 退出码={code3} {text3})")
    if not t3:
        _log(f"[自检]     query 复核 -> 返回码 {rc_q}: {out_q.strip()[:200]}")
    _run_as_lingtong(['schtasks', '/delete', '/f', '/tn', _PROBE_TASK], password)

    # ---- 结论 ----
    if t1 and t2 and t3:
        _log("[自检] 结论: 三项全通 —— 通道本身没问题，失败发生在更后面")
    elif t1 and not t2:
        _log("[自检] 结论: 起进程/写目录没问题，但 **.cmd 脚本被拦**。"
             "典型原因是软件限制策略(SRP)/AppLocker：允许 .exe 但不允许批处理。"
             "需要改成不依赖脚本的路径（直接用 .exe 作为计划任务动作）。")
    elif not t1 and not t2:
        _log("[自检] 结论: lingtong 进程连写文件都做不到 —— 目录权限或"
             "策略把 cmd.exe 本身拦住了")
    elif t1 and t2 and not t3:
        _log("[自检] 结论: 脚本能跑，但 **Task Scheduler 不接受 lingtong 注册"
             "最高权限任务**（多半是组策略限制），这条路走不通")
    _log("-" * 70)
    return t1, t2, t3


def _run_as_lingtong(cmd, password):
    """以 lingtong 身份执行一条命令（不等待输出，用于清理类操作）"""
    cmdline = subprocess.list2cmdline(cmd)
    ok, _err, text = _cpwl_once(LINGTONG_USER, password, _LOGON_WITH_PROFILE,
                                _CREATE_UNICODE_ENVIRONMENT, None, cmdline)
    _log(f"[任务] 以 lingtong 身份执行 {cmdline} -> {'成功' if ok else '失败'} {text}")
    return ok


def _write_task_launcher(exe_path, args):
    """写一次性启动脚本，供计划任务调用

    为什么不让计划任务直接指向安装包：`/tr` 是一条**命令行**，路径里带空格、
    中文时，引号要多层转义，极易出错。写成脚本文件后路径只出现一次，按 cmd 的
    原生 ANSI 编码(mbcs)写盘即可，中文路径不会乱码。

    脚本做四件事：
      ① `cd /d` 到通道目录 —— 任务默认工作目录是 System32，显式固定住免得出岔子；
      ② 落一个"已执行"标记文件 —— 让调用方能**确定性**地知道任务跑到哪一步了，
         而不是靠 sleep 猜（Task Scheduler 启动延迟可达数十秒，猜早了会误删任务）；
      ③ `start /wait` 启动安装程序，退出后把 `%ERRORLEVEL%` 追加到结果文件 ——
         session 0 里看不到任何界面和输出，退出码是判断"装成功没有"的唯一依据；
      ④ 删掉自己所属的计划任务 —— 不给交付给用户的机器留下一个存着管理员凭据的
         最高权限任务。

    :return: 脚本绝对路径，失败返回 ''
    """
    channel = _ensure_channel_dir()
    path = os.path.join(channel, _SCHTASK_LAUNCHER)
    sentinel = _schtask_sentinel_path()
    result_path = _schtask_result_path()
    try:
        # 只清 sentinel；结果文件保留并追加，方便一次装多个包之后回看历史
        if os.path.exists(sentinel):
            os.remove(sentinel)
    except OSError:
        pass
    argpart = ''
    if args:
        argpart = ' ' + ' '.join(('"%s"' % a) if ' ' in a else a for a in args)
    lines = [
        '@echo off',
        f'cd /d "{channel}"',
        'rem 由 软件下载附件/install.py 生成，一次性使用，执行完自删',
        f'echo ran >"{sentinel}"',
        f'start /wait "" "{exe_path}"{argpart}',
        f'echo %DATE% %TIME% exitcode=%ERRORLEVEL% >>"{result_path}"',
        f'schtasks /delete /f /tn "{_SCHTASK_NAME}" >nul 2>&1',
        'del /f /q "%~f0" >nul 2>&1',
        'exit /b 0',
        '',
    ]
    content = '\r\n'.join(lines)
    for enc in ('mbcs', 'utf-8-sig'):
        try:
            with open(path, 'w', encoding=enc) as f:
                f.write(content)
            _log(f"[任务] 已写启动脚本({enc}): {path}")
            return path
        except (UnicodeEncodeError, LookupError):
            continue
        except OSError as e:
            return ''
    return ''


def _write_elevate_script(launcher, principal, password):
    """写"注册并触发计划任务"的脚本 —— 这个脚本由 **lingtong 身份**执行

    为什么要逐步写日志：这条链路一旦失败，除了这一步的输出我们没有任何别的线索
    （lingtong 侧完全看不见）。所以每一步都追加一行 stage + 返回码到日志文件，
    失败时才能一眼看出是"进程没跑起来"、"create 被拒" 还是 "run 失败"。

    :return: 脚本绝对路径，失败返回 ''
    """
    channel = _ensure_channel_dir()
    path = os.path.join(channel, _SCHTASK_ELEVATE)
    log_path = _schtask_log_path()
    create_cmd = subprocess.list2cmdline([
        'schtasks', '/create', '/tn', _SCHTASK_NAME, '/tr', launcher,
        '/sc', 'ONCE', '/st', '23:59', '/ru', principal, '/rp', password,
        '/rl', 'HIGHEST', '/f'])
    run_cmd = subprocess.list2cmdline(['schtasks', '/run', '/tn', _SCHTASK_NAME])
    lines = [
        '@echo off',
        f'cd /d "{channel}"',
        f'set "LOG={log_path}"',
        'echo [stage] entered user=%USERNAME% cwd=%CD% >>"%LOG%"',
        f'{create_cmd} >>"%LOG%" 2>&1',
        'echo [stage] create rc=%ERRORLEVEL% >>"%LOG%"',
        f'{run_cmd} >>"%LOG%" 2>&1',
        'echo [stage] run rc=%ERRORLEVEL% >>"%LOG%"',
        'exit /b 0',
        '',
    ]
    content = '\r\n'.join(lines)
    for enc in ('mbcs', 'utf-8-sig'):
        try:
            with open(path, 'w', encoding=enc) as f:
                f.write(content)
            _log(f"[任务] 已写提权脚本({enc}): {path}")
            # 密码要写进 .cmd，cmd 对这几个字符是**引号也保护不了**的，先提醒一声
            for bad in ('%', '!', '"', '\r', '\n'):
                if bad in password:
                    _log(f"[任务] 警告: lingtong 密码含 {bad!r}，写进 .cmd 可能被 cmd "
                         "解析坏；若这步失败请优先怀疑密码字符问题")
                    break
            return path
        except (UnicodeEncodeError, LookupError):
            continue
        except OSError:
            return ''
    return ''


def _launch_task_without_script(exe_path, args, principal, password, channel):
    """不依赖任何脚本文件的备用路径：直接以 lingtong 身份调 schtasks.exe

    用在"lingtong 能起进程、但 .cmd 被软件限制策略拦住"的场景。
    代价是拿不到安装过程的任何输出（没有脚本可重定向、没有 sentinel、没有退出码
    落盘），所以只能确认"任务已建出并触发"，安装是否真的成功要另行确认。

    :return: (ok, 提示信息)
    """
    action = subprocess.list2cmdline([exe_path] + list(args))
    _log("[任务] 备用路径：计划任务动作直接指向安装包，不经过任何脚本")
    _log(f"[任务]   动作 = {action}")
    for step, cmd_args in (
        ('create', ['schtasks', '/create', '/tn', _SCHTASK_NAME, '/tr', action,
                    '/sc', 'ONCE', '/st', '23:59', '/ru', principal, '/rp', password,
                    '/rl', 'HIGHEST', '/f']),
        ('run', ['schtasks', '/run', '/tn', _SCHTASK_NAME]),
    ):
        cmdline = subprocess.list2cmdline(cmd_args)
        _log('[任务]   ' + _mask(cmdline, password))
        ok, code, text = _cpwl_run_wait(LINGTONG_USER, password, cmdline, cwd=channel)
        _log(f"[任务] 以 lingtong 身份 schtasks /{step} -> ok={ok} 退出码={code} {text}")
        if not ok:
            return False, f'无法以 {LINGTONG_USER} 身份执行 schtasks /{step}: {text}'

    rc_q, out_q = _run(['schtasks', '/query', '/tn', _SCHTASK_NAME])
    _log(f"[任务] query 复核 -> 返回码 {rc_q}: {out_q.strip()[:200]}")
    if rc_q != 0:
        return False, ('已以 lingtong 身份调用 schtasks，但任务没建出来 —— '
                       '多半是 Task Scheduler 不接受该账户注册最高权限任务（组策略限制）')

    # 这个任务的动作直接是安装包，没有脚本负责自删，只能我们自己回头请 lingtong 删
    def _delayed_delete():
        import threading as _th
        _th.Timer(600, lambda: _run_as_lingtong(
            ['schtasks', '/delete', '/f', '/tn', _SCHTASK_NAME], password)).start()

    try:
        _delayed_delete()
        _log("[任务] 已安排 10 分钟后请 lingtong 删除该任务（防止留下带凭据的最高权限任务）")
    except Exception as e:
        _log(f"[任务] 警告: 安排清理失败({e})，目标机会残留任务 {_SCHTASK_NAME}，"
             "请手工删除")
    return True, (f'已用不依赖脚本的方式，以 {principal} 的最高权限触发安装。'
                  '注意：脚本被策略拦截，拿不到安装过程的任何输出，'
                  '请以软件是否真的装上为准')


def _launch_task_elevated_direct(exe_path, args, principal, password):
    """**本进程已提权时**直接调 schtasks 注册最高权限任务（不经 lingtong 侧脚本）

    提权带来的两个好处：① schtasks 的输出与退出码全部可见（不用再靠对方落文件回传）；
    ② 任务动作可以直接指向安装包本身，不依赖 .cmd —— 正好避开软件限制策略拦脚本的坑。
    代价：动作不是脚本，就没有"落 sentinel / 自删任务"这些能力，改由本进程延迟删除。

    :return: (ok, 提示信息)
    """
    action = subprocess.list2cmdline([exe_path] + list(args))
    _log(f"[任务] 本进程已提权，直接调 schtasks；动作(不经脚本) = {action}")

    cmd = ['schtasks', '/create', '/tn', _SCHTASK_NAME, '/tr', action,
           '/sc', 'ONCE', '/st', '23:59', '/ru', principal, '/rp', password,
           '/rl', 'HIGHEST', '/f']
    _log('[任务] ' + _mask(subprocess.list2cmdline(cmd), password))
    rc, out = _run(cmd)
    _log(f"[任务] schtasks /create -> 返回码 {rc}: {out.strip()[:400]}")
    if rc != 0:
        hint = ''
        if '拒绝访问' in out or 'Access is denied' in out:
            hint = '  ==> 本进程虽然提权了，但组策略不允许注册最高权限任务'
        elif '策略' in out or 'policy' in out.lower():
            hint = '  ==> 被软件限制策略/AppLocker 拦住'
        return False, f'schtasks /create 失败（返回码 {rc}）{hint}'

    rc2, out2 = _run(['schtasks', '/run', '/tn', _SCHTASK_NAME])
    _log(f"[任务] schtasks /run -> 返回码 {rc2}: {out2.strip()[:400]}")
    if rc2 != 0:
        _run(['schtasks', '/delete', '/f', '/tn', _SCHTASK_NAME])
        return False, f'schtasks /run 失败（返回码 {rc2}）'

    def _delayed_delete():
        import threading as _th
        _th.Timer(600, lambda: _run(
            ['schtasks', '/delete', '/f', '/tn', _SCHTASK_NAME])).start()

    try:
        _delayed_delete()
        _log("[任务] 已安排 10 分钟后删除该任务（动作不是脚本，无法自删）")
    except Exception as e:
        _log(f"[任务] 警告: 安排清理失败({e})，请手工删除任务 {_SCHTASK_NAME}")

    return True, (f'已通过计划任务以 {principal} 的最高权限触发安装（本进程已提权，'
                  'schtasks 输出可见）。动作直接指向安装包，'
                  '拿不到安装过程的输出，请以软件是否真的装上为准')


def _launch_via_scheduled_task(exe_path, args, username, password):
    """由 lingtong 身份注册并触发计划任务，以**最高权限**启动安装程序（不弹 UAC）

    为什么不能在当前进程里直接调 schtasks：
      下载服务跑在 Users/Power Users 账户下。Task Scheduler 只接受**管理员**
      （哪怕是被 UAC 过滤成 deny-only 的中等完整性令牌）注册 `/RL HIGHEST` 任务，
      普通账户发起只会拿到"拒绝访问"。所以这里先用 CreateProcessWithLogonW 把
      **lingtong 身份**的进程拉起来，由它去调 schtasks —— 这一步必然成功，因为
      cmd.exe / schtasks.exe 的清单都不要求管理员，seclogon 能正常创建它们，
      而 lingtong 是"被 UAC 过滤的管理员"，正是 Task Scheduler 认的身份。

    为什么提权能成功：
      Task Scheduler 拿到管理员密码后走 **Batch 登录**，加上任务被标记为
      "以最高权限运行"(`/RL HIGHEST`)，交出来的是**未过滤的完整令牌**(High IL)。
      全程不弹 UAC 确认框，也不改动任何 UAC 设置。

    :return: (ok, 提示信息)
    """
    if not exe_path or not os.path.exists(exe_path):
        return False, f'目标程序不存在: {exe_path}'

    machine = os.environ.get('COMPUTERNAME') or '.'
    principal = f'{machine}\\{username}'

    # 本进程已提权时，不必再绕 lingtong 侧脚本（那条路依赖 .cmd，会被策略拦）
    if _is_process_elevated():
        return _launch_task_elevated_direct(exe_path, args, principal, password)

    channel = _ensure_channel_dir()
    sentinel = _schtask_sentinel_path()
    log_path = _schtask_log_path()

    # 先把通道能力逐项验一遍：t2 为假说明 .cmd 脚本被策略拦住，那就别在脚本上耗，
    # 直接走不依赖脚本的备用路径
    _t1, t2, _t3 = _channel_selftest(password)
    if not t2:
        return _launch_task_without_script(exe_path, args, principal, password, channel)

    launcher = _write_task_launcher(exe_path, args)
    if not launcher:
        return False, '写计划任务启动脚本失败'
    elevate = _write_elevate_script(launcher, principal, password)
    if not elevate:
        return False, '写提权脚本失败'
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    reg_cmd = f'cmd /c "{elevate}"'
    _log("[任务] 以 lingtong 身份注册并触发计划任务"
         "（当前账户不是管理员，自己调 schtasks 会被拒绝访问）:")
    _log('[任务]   ' + _mask(reg_cmd, password))
    _log(f"[任务]   显式工作目录 = {channel}"
         "（不能让它继承本进程的目录：lingtong 被 UAC 过滤后可能连那个目录都进不去，"
         "cmd 会直接退出，什么都不做）")
    ok_cp, _code, text_cp = _cpwl_once(LINGTONG_USER, password, _LOGON_WITH_PROFILE,
                                       _CREATE_UNICODE_ENVIRONMENT, None, reg_cmd,
                                       cwd=channel)
    _log(f"[任务] 以 lingtong 身份启动 schtasks -> {'成功' if ok_cp else '失败'} -> {text_cp}")
    # 提权脚本里有明文密码，用完立刻删掉（日志里已遮蔽）
    try:
        os.remove(elevate)
    except OSError:
        pass
    if not ok_cp:
        return False, f'无法以 {LINGTONG_USER} 身份注册计划任务: {text_cp}'

    # 等"已执行"标记出现（确定性判断，不靠 sleep 猜：Task Scheduler 启动延迟可达数十秒）
    deadline = time.time() + 120
    while time.time() < deadline and not os.path.exists(sentinel):
        time.sleep(2)

    if os.path.exists(sentinel):
        _log("[任务] 启动脚本已执行（任务自删生效）")
        return True, (f'已通过计划任务以 {principal} 的最高权限启动安装程序'
                      '（Task Scheduler 提供的令牌不受 UAC 过滤）')

    detail = _read_elevate_log(log_path)
    _log("[任务] 120 秒内未见启动脚本落标记。lingtong 侧输出:")
    if detail:
        for line in detail.splitlines():
            if line.strip():
                _log(f"[任务]   {line.strip()}")
    else:
        _log(f"[任务]   （{log_path} 不存在或为空 —— 说明 lingtong 那个进程连日志都没"
             "写出来，问题出在它启动/目录权限这一步，而不是 schtasks 本身）")
    rc_q, out_q = _run(['schtasks', '/query', '/tn', _SCHTASK_NAME])
    _log(f"[任务] schtasks /query 复核 -> 返回码 {rc_q}: {out_q.strip()[:300]}")
    # 任务可能已注册但没跑起来（例如要等到 23:59 才触发），删掉它别留残余；
    # 任务属于 lingtong，当前账户没有删它的权限，所以还是请 lingtong 来删。
    _run_as_lingtong(['schtasks', '/delete', '/f', '/tn', _SCHTASK_NAME], password)
    hint = ''
    if '拒绝访问' in detail or 'Access is denied' in detail:
        hint = '  ==> Task Scheduler 拒绝了 lingtong：多半有组策略限制普通管理员注册最高权限任务'
    elif '密码' in detail or 'password' in detail.lower():
        hint = '  ==> 凭据被拒，检查 password.pem'
    return False, f'计划任务未在 120 秒内执行。{hint}'


# ==================== 静默安装参数 ====================
# 为什么必须带静默开关：方式三（计划任务）下安装进程跑在 **session 0**，它的界面
# 不在任何可见桌面上。不带静默参数的安装包会停在一个看不见的对话框上等鼠标点击，
# 日志却显示"启动成功" —— 表现为"等了好久什么都没发生"。
#
# 键 = 安装包文件名（小写），值 = 参数列表。可在脚本同目录放 silent_args.json
# 覆盖/补充，格式：{"qq.exe": ["/S"], "某某.exe": ["/SILENT", "/NORESTART"]}
SILENT_ARGS = {
    # NSIS 系（腾讯系、多数国产装机包）用 /S
    'qq.exe': ['/S'],
    'qq音乐.exe': ['/S'],
    'wechat.exe': ['/S'],
}
EXE_DEFAULT_SILENT_ARGS = ['/S']   # 表里找不到时的兜底猜测（NSIS 最常见）


def _silent_args_for(filename):
    """返回某安装包应当使用的静默参数

    优先级：silent_args.json > 内置 SILENT_ARGS 表 > EXE_DEFAULT_SILENT_ARGS

    :return: (参数列表, 来源说明)
    """
    key = (filename or '').lower()
    table = dict(SILENT_ARGS)
    source = '内置表'
    override = os.path.join(_script_dir(), SILENT_ARGS_FILE)
    if os.path.exists(override):
        try:
            with open(override, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    table[str(k).lower()] = v if isinstance(v, list) else [str(v)]
                source = SILENT_ARGS_FILE
        except Exception as e:
            _log(f"[静默] 读取 {SILENT_ARGS_FILE} 失败(已忽略): {e}")

    if key in table:
        return list(table[key]), source
    return list(EXE_DEFAULT_SILENT_ARGS), '默认猜测(未在表中找到)'


def run_installer_as_lingtong(exe_path, args=None):
    """以 lingtong 身份在后台静默启动安装程序（不等待、不弹任何窗口）

    为什么只有这一条路径：
      下载服务由当前登录用户运行，安装由 lingtong 身份运行。lingtong 是本地管理员
      账户，直接用它的凭据启动安装程序即可——不需要、也不应该去申请 UAC 提权
      （提权会弹确认框打断批量安装，与"后台静默"的目标冲突）。

    实现方式（按成功率排序，失败的会逐条打印原因后继续下一条）：
      0. **本进程已提权**时的首选：LogonUser(BATCH) + CreateProcessWithTokenW
         —— Batch 登录不过滤令牌，CreateProcessWithTokenW 需要 SeImpersonatePrivilege
         （提权令牌才有）。这是唯一能"以 lingtong 身份 + 已提升"启动安装程序的机制。
      1. PowerShell Start-Process -Credential
      2. ctypes 直调 advapi32.CreateProcessWithLogonW（能取到原始 Win32 错误码）
      3. 计划任务 /RL HIGHEST —— 前几条都因 UAC 令牌过滤返回 740 时的出路；
         **本进程已提权时**直接调 schtasks（输出可见、动作不经脚本）；
         未提权时才绕 lingtong 侧脚本（并先做通道自检）

    详细日志同时输出到控制台并追加写入 install_debug.log

    :param exe_path: 可执行文件路径（.msi 时为 msiexec.exe）
    :param args: 命令行参数列表
    :return: (是否成功, 提示信息)
    """
    args = list(args or [])
    _log("")
    _log("=" * 70)
    _log(f"[安装] 以 {LINGTONG_USER} 身份后台启动: {exe_path} {' '.join(args)}".rstrip())
    _log("=" * 70)

    try:
        password = _get_lingtong_password()
        _log(f"[安装] 已读取 {PASSWORD_FILE}（密码长度 {len(password)}，内容不打印）")
    except Exception as e:
        return False, f"读取 {PASSWORD_FILE} 失败，无法以 {LINGTONG_USER} 身份安装: {str(e)}"

    _report_lingtong_token(password)
    _collect_diagnostics(exe_path, args)

    # ============ 提权后的三条路（按"身份最正确 + 最不容易失败"排序）============
    elevated = _is_process_elevated()
    cur_user = os.environ.get('USERNAME', '')
    target_user = _task_user_name()
    same_identity = elevated and cur_user.lower() == target_user.lower()

    # 方式 0a：已提权且身份就是 lingtong —— 直接用自己的令牌启动
    if same_identity:
        _log("")
        _log(f"[方式 0a] 本进程已提权且账户就是 {target_user}，直接用本进程令牌启动")
        ok0a, msg0a = _launch_direct_elevated(exe_path, args)
        _log(f"[方式 0a] {'成功' if ok0a else '失败'} -> {msg0a}")
        if ok0a:
            return True, msg0a

    # 方式 0b：已提权 —— Batch 登录拿未过滤令牌，再以 lingtong 身份启动
    if elevated:
        _log("")
        _log("[方式 0b] Batch 登录 + CreateProcessWithTokenW（本进程已提权）")
        ok0b, msg0b = _launch_with_batch_token(exe_path, args, LINGTONG_USER, password)
        _log(f"[方式 0b] {'成功' if ok0b else '失败'} -> {msg0b}")
        if ok0b:
            return True, msg0b
    else:
        _log("")
        _log("[方式 0] 跳过：本进程未提权。已提升的令牌才能直接启动要求管理员的安装包，"
             "CreateProcessWithTokenW 也需要 SeImpersonatePrivilege —— 请用 "
             "Boot_Software_Install.cmd 启动（它会在开头请求一次 UAC 授权）")

    # 方式 0c：已提权但上两条都没成 —— 仍用本进程令牌直启（身份可能不是 lingtong）
    if elevated:
        _log("")
        _log(f"[方式 0c] 兜底：直接用本进程令牌启动（安装身份是 {cur_user}，"
             f"不是 {target_user}，HKCU 等会落在 {cur_user} 下）")
        ok0c, msg0c = _launch_direct_elevated(exe_path, args)
        _log(f"[方式 0c] {'成功' if ok0c else '失败'} -> {msg0c}")
        if ok0c:
            return True, msg0c + f'（注意：安装身份是 {cur_user}，不是 {target_user}）'

    # 方式一：PowerShell Start-Process -Credential（按部署方要求的首选）
    _log("")
    _log("[方式 1] PowerShell Start-Process -Credential")
    ok1, msg1 = _launch_via_powershell_credential(exe_path, args, password)
    _log(f"[方式 1] {'成功' if ok1 else '失败'} -> {msg1}")
    if ok1:
        return True, msg1

    # 方式二：ctypes CreateProcessWithLogonW（同一 API，回退，精确错误码）
    _log("")
    _log("[方式 2] ctypes CreateProcessWithLogonW（回退）")
    ok2, _code2, err2 = _launch_with_createprocesswithlogonw(exe_path, args, LINGTONG_USER, password)
    _log(f"[方式 2] {'成功' if ok2 else '失败'} -> {err2}")
    if ok2:
        return True, f"安装程序已以 {LINGTONG_USER} 身份在后台启动"

    # 方式三：计划任务 /RL HIGHEST（Task Scheduler 给出未过滤令牌）
    # 前两条都栽在 UAC 令牌过滤上时，这是唯一还能拿到提升令牌且不弹窗的机制。
    _log("")
    _log("[方式 3] 计划任务 /RL HIGHEST（Task Scheduler 未过滤令牌）")
    ok3, msg3 = _launch_via_scheduled_task(exe_path, args,
                                           _task_user_name(), password)
    _log(f"[方式 3] {'成功' if ok3 else '失败'} -> {msg3}")
    if ok3:
        return True, msg3

    return False, (
        f"无法以 {LINGTONG_USER} 身份启动安装程序。"
        f" PowerShell -> {msg1}"
        f" | CreateProcessWithLogonW -> {err2}"
        f" | 计划任务 -> {msg3}"
    )


class InstallHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            print("\n" + "="*80)
            print("收到新的POST请求")
            print("时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
            print("="*80)
            
            data = json.loads(post_data.decode('utf-8'))
            print(f"请求数据: {json.dumps(data, ensure_ascii=False, indent=2)}")
            
            packages = data.get('packages', [])
            exit_after_install = data.get('exitAfterInstall', False)
            exit_server = data.get('exitServer', False)
            check_admin = data.get('checkAdmin', False)
            
            # 处理检查管理员权限请求
            if check_admin:
                print("\n=== 处理检查管理员权限请求 ===")
                # 检查当前用户是否具有管理员权限
                
                is_admin = False
                current_username = os.getenv('USERNAME')
                current_domain = os.getenv('USERDOMAIN')
                
                # 构建完整的用户名（包括域）
                full_username = f"{current_domain}\\{current_username}" if current_domain else current_username
                print(f"当前用户名: {current_username}")
                if current_domain:
                    print(f"当前域: {current_domain}")
                    print(f"完整用户名: {full_username}")
                
                try:
                    # 尝试读取管理员组的成员列表
                    result = subprocess.run(['net', 'localgroup', 'Administrators'], 
                                          capture_output=True, text=True, shell=False)
                    
                    if result.returncode == 0:
                        # 检查输出中是否包含当前用户名（两种格式：仅用户名或域\用户名）
                        if current_username and (current_username in result.stdout or full_username in result.stdout):
                            is_admin = True
                            print(f"成功: 当前用户在管理员组中")
                        else:
                            print(f"失败: 当前用户不在管理员组中")
                    else:
                        print(f"失败: 无法读取管理员组，错误代码: {result.returncode}")
                        print(f"错误输出: {result.stderr}")
                except Exception as e:
                    print(f"失败: 访问被拒绝，当前用户无管理员权限")
                    print(f"错误信息: {str(e)}")
                
                print(f"管理员权限检测结果: {'是' if is_admin else '否'}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                # 注意：isAdmin 只表示"当前账号在 Administrators 组内"，
                # 与"本进程是否已提升"是两回事（UAC 开启时两者可能不一致）。
                # 额外返回 isElevated / uac 供前端与排查使用。
                admin_payload: dict[str, object] = {'isAdmin': is_admin}
                if DEBUG:
                    admin_payload['isElevated'] = _is_process_elevated()
                    admin_payload['uac'] = _read_uac_settings()
                    _log(f"[检查] isAdmin={is_admin} isElevated={admin_payload['isElevated']} "
                         f"uac={admin_payload['uac']}")
                self.wfile.write(json.dumps(admin_payload).encode('utf-8'))
                print("已返回管理员权限状态响应")
                return
            
            # 处理退出服务器请求
            if exit_server:
                print("\n=== 处理退出服务器请求 ===")
                print("收到退出服务器请求...")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'message': '服务器正在退出'}).encode('utf-8'))
                
                # 延迟退出，确保响应已发送
                import threading
                print("设置延迟退出...")
                threading.Timer(1, self.exit_server,
                                args=('前端发来的退出请求(exitServer=true)',)).start()
                print("退出请求处理完成")
                return
            
            if not packages:
                print("\n=== 错误: 未选择软件包 ===")
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'No packages selected'}).encode('utf-8'))
                print("已返回错误响应: No packages selected")
                return
            
            print(f"\n=== 开始下载 {len(packages)} 个软件包 ===")
            
            # Create temporary directory for downloads
            temp_dir = INSTALL_TEMP_DIR
            print(f"临时目录: {temp_dir}")
            
            if not os.path.exists(temp_dir):
                print("临时目录不存在，正在创建...")
                os.makedirs(temp_dir)
                print("临时目录创建成功")
            else:
                print("临时目录已存在")

            # 分别统计，避免"下载成功但安装失败(如 740)"被误报成全部成功
            install_started = []
            install_failed = []

            # Process each package
            for pkg in packages:
                pkg_path = pkg.get('path')
                pkg_name = pkg.get('name')
                
                if not pkg_path:
                    continue
                
                try:
                    # Extract filename from path
                    filename = os.path.basename(pkg_path)
                    save_path = os.path.join(temp_dir, filename)
                    
                    # Download the file using PowerShell Copy-Item
                    print(f"\n=== 开始下载: {filename} ===")
                    print(f"源路径: {pkg_path}")
                    print(f"目标路径: {save_path}")
                    
                    # 计算文件大小（如果可能）
                    try:
                        file_size = os.path.getsize(pkg_path)
                        print(f"文件大小: {file_size / (1024*1024):.2f} MB")
                    except Exception as e:
                        print(f"无法获取文件大小: {str(e)}")
                    
                    # 下载文件
                    print("开始下载...")
                    start_time = time.time()
                    
                    # 使用 PowerShell 的 Start-BitsTransfer 命令复制文件
                    # Start-BitsTransfer 提供更好的文件传输功能，包括进度显示、断点续传等
                    print("使用 PowerShell 的 Start-BitsTransfer 命令复制文件...")
                    
                    # 检查目标文件是否存在，如果存在则先删除
                    if os.path.exists(save_path):
                        print(f"目标文件已存在，正在删除: {save_path}")
                        try:
                            os.remove(save_path)
                            print("目标文件删除成功")
                        except Exception as e:
                            print(f"删除目标文件失败: {str(e)}")
                    
                    # 构建 PowerShell 命令，添加进度显示
                    # 注意：Start-BitsTransfer 需要使用 -Source 和 -Destination 参数
                    # 注意：Start-BitsTransfer 不支持 -Force 参数
                    powershell_cmd = f"powershell -Command \"Start-BitsTransfer -Source '{pkg_path}' -Destination '{save_path}' -DisplayName '{filename}'\""
                    print(f"执行命令: {powershell_cmd}")
                    
                    # 执行命令并显示 PowerShell 的内置进度
                    process = subprocess.Popen(powershell_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                    
                    # 实时读取和显示输出
                    while True:
                        output = process.stdout.readline()
                        if output == '' and process.poll() is not None:
                            break
                        if output:
                            print(output.strip())
                    
                    result = process
                    
                    download_time = time.time() - start_time
                    
                    if result.returncode == 0:
                        print(f"下载完成! 耗时: {download_time:.2f} 秒")
                        print(f"已下载 {pkg_name} 到 {save_path}")
                        
                        # 检查文件是否存在且大小合理
                        try:
                            if os.path.exists(save_path):
                                final_size = os.path.getsize(save_path)
                                print(f"目标文件大小: {final_size / (1024*1024):.2f} MB")
                            else:
                                print("警告: 目标文件不存在")
                        except Exception as e:
                            print(f"无法验证目标文件: {str(e)}")
                        
                        # Run the installer as .\lingtong
                        print(f"开始安装 {filename}（以 {LINGTONG_USER} 身份运行）...")
                        
                        # Check file extension and run appropriately
                        file_ext = os.path.splitext(filename)[1].lower()
                        print(f"文件类型: {file_ext}")
                        
                        if file_ext == ".msi":
                            # Try to run MSI with silent options
                            install_exe = "msiexec.exe"
                            install_args = ["/i", save_path, "/qn", "/l*v", save_path + ".log"]
                            print("使用静默安装参数: /qn /l*v (日志文件)")
                        elif file_ext == ".exe":
                            # 必须带静默参数：方式三下安装进程在 session 0，
                            # 界面不可见，不带开关的安装包会卡在看不见的对话框上
                            install_exe = save_path
                            pkg_args = pkg.get('args')
                            if pkg_args:
                                install_args = (list(pkg_args)
                                                if isinstance(pkg_args, list)
                                                else [str(pkg_args)])
                                arg_source = '来自请求(packages[].args)'
                            else:
                                install_args, arg_source = _silent_args_for(filename)
                            print(f"静默安装参数: {' '.join(install_args) or '(无)'}  [{arg_source}]")
                            if not install_args:
                                print("警告: 该安装包没有静默参数，而安装界面在 session 0 "
                                      "不可见，安装会卡住不动！请在 "
                                      f"{SILENT_ARGS_FILE} 里补上对应参数")
                        else:
                            # Just run the file
                            install_exe = save_path
                            install_args = []
                            print("直接运行文件（无静默参数）")
                        
                        print(f"安装程序: {install_exe}")
                        if install_args:
                            print(f"安装参数: {' '.join(install_args)}")
                        
                        # Run installer in background as lingtong
                        try:
                            install_ok, install_msg = run_installer_as_lingtong(install_exe, install_args)
                            if install_ok:
                                install_started.append(pkg_name)
                                print(install_msg + "!")
                                print(f"{pkg_name} 安装已开始，请等待安装完成...")
                            else:
                                install_failed.append(f"{pkg_name}: {install_msg}")
                                print(install_msg)
                        except Exception as e:
                            install_failed.append(f"{pkg_name}: {e}")
                            print(f"启动安装程序失败: {str(e)}")
                    else:
                        print(f"下载失败! {pkg_name}")
                        print(f"错误代码: {result.returncode}")
                        if result.stdout:
                            print(f"标准输出: {result.stdout}")
                        if result.stderr:
                            print(f"错误输出: {result.stderr}")
                        
                except Exception as e:
                    print(f"处理 {pkg_name} 时发生错误:")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误信息: {str(e)}")
                    import traceback
                    print(f"错误堆栈: {traceback.format_exc()}")
            
            print(f"\n=== 处理完成 ===")
            print(f"安装成功启动: {len(install_started)} 个 {install_started}")
            if install_failed:
                print(f"安装启动失败: {len(install_failed)} 个")
                for item in install_failed:
                    print(f"  - {item}")
                print("提示: 失败信息里若出现 740 / 请求的操作需要提升 / 拒绝访问，"
                      "说明目标机当前账户不是管理员、lingtong 提权链路被 UAC 挡住，"
                      "安装包本身没有问题")
            else:
                print("全部安装包均已成功启动")
            print(f"安装结果请查看: {_schtask_result_path()}")
            print("  （每装完一个包会追加一行 exitcode=N；0 = 安装包自报成功。"
                  "若文件里没有对应记录，说明安装还在跑或卡住了 —— 安装进程在 session 0，"
                  "界面不可见，所以必须靠这个退出码判断，不能靠是否看到界面）")

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            if install_failed:
                response_message = (
                    f'已下载 {len(packages)} 个软件包；安装成功启动 {len(install_started)} 个，'
                    f'失败 {len(install_failed)} 个，详见后端控制台')
            else:
                response_message = (f'已启动 {len(install_started)} 个软件的下载和安装过程，'
                                    '请查看后端控制台了解详细进度')
            self.wfile.write(json.dumps({'message': response_message}).encode('utf-8'))
            print(f"已返回响应: {response_message}")
            
            # 如果需要在安装后退出
            if exit_after_install:
                print("所有软件安装完成，正在退出...")
                # 延迟退出，确保响应已发送
                import threading
                threading.Timer(2, self.exit_server,
                                args=('勾选了"安装后退出"(exitAfterInstall=true)',)).start()
            
        except Exception as e:
            print(f"\n=== 严重错误 ===")
            print(f"处理请求时发生异常: {str(e)}")
            import traceback
            print(f"错误堆栈: {traceback.format_exc()}")
            
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            error_message = f'处理请求时发生错误: {str(e)}'
            self.wfile.write(json.dumps({'error': error_message}).encode('utf-8'))
            print(f"已返回错误响应: {error_message}")
    
    def exit_server(self, reason='未知'):
        """退出服务器进程（**"交付前自我清理"设计，会清空程序文件夹**）

        依次执行：
        1. 忘记 GTMC-VIP 的 WiFi 连接
        2. 删除 password.pem（含 lingtong 密码，必须先删除）
        3. 删除程序文件夹内的所有文件（含子目录、含随包 Python）
        4. os._exit(0) 结束进程

        ⚠ 为什么它看起来像"闪退"：os._exit 不刷新输出、也不走任何清理流程，而窗口
        是 Boot_Software_Install.cmd 用 `start` 拉起来的 —— 进程一退窗口立刻消失，
        只看到文字一闪。**这不是崩溃，是被主动触发的正常退出**，触发点只有两个：
        ① 前端发来的退出请求(exitServer) ② 勾选了"安装后退出"(exitAfterInstall)。

        所以这里做了两件保命的事：
        ① 先把"谁触发的、什么时候、要删哪个目录"写到一个**程序文件夹之外**的痕迹
           文件里 —— 否则它把自己的日志一起删了，事后无从查证；
        ② 打印 10 秒倒计时，误触发时还来得及按 Ctrl+C 中止。
        """
        import os
        import shutil
        import subprocess

        print("退出服务器进程...")
        script_dir = _script_dir()
        _append_exit_trace('自我清理退出', f'触发原因: {reason}\n程序文件夹: {script_dir}')
        print("=" * 70)
        print("注意: 接下来会删除 password.pem 并清空程序文件夹（含子目录）")
        print(f"      目录: {script_dir}")
        print(f"      触发原因: {reason}")
        print("      删除后本工具无法再运行，需要重新拷贝一份。")
        print("      10 秒后开始，若是误触发请立刻按 Ctrl+C 中止。")
        print("=" * 70)
        for i in range(10, 0, -1):
            print(f"\r      {i} ...", end='', flush=True)
            time.sleep(1)
        print()
        
        # 忘记GTMC-VIP的WiFi连接
        print("正在忘记GTMC-VIP的WiFi连接...")
        try:
            # 使用netsh命令删除GTMC-VIP WiFi配置文件
            wifi_cmd = 'netsh wlan delete profile name="GTMC-VIP"'
            print(f"执行命令: {wifi_cmd}")
            
            # 执行命令并显示输出
            wifi_result = subprocess.run(wifi_cmd, shell=True, capture_output=True, text=True)
            if wifi_result.returncode == 0:
                print("成功忘记GTMC-VIP的WiFi连接")
                if wifi_result.stdout:
                    print(f"命令输出: {wifi_result.stdout.strip()}")
            else:
                print(f"忘记WiFi连接失败，错误代码: {wifi_result.returncode}")
                if wifi_result.stderr:
                    print(f"错误信息: {wifi_result.stderr.strip()}")
        except Exception as e:
            print(f"执行WiFi命令时发生错误: {str(e)}")
        
        script_dir = _script_dir()

        # 1) 先删除保存 lingtong 密码的 password.pem
        print("\n=== 删除密码文件 ===")
        pem_path = os.path.join(script_dir, PASSWORD_FILE)
        try:
            if os.path.exists(pem_path):
                os.remove(pem_path)
                print(f"已删除密码文件: {pem_path}")
            else:
                print(f"密码文件不存在，跳过: {pem_path}")
        except Exception as e:
            print(f"删除密码文件失败: {str(e)}")
        
        # 2) 删除程序文件夹内的所有文件（含子目录）
        print("\n=== 删除程序文件夹内所有文件 ===")
        print(f"程序文件夹: {script_dir}")
        failed_items = []
        try:
            for name in os.listdir(script_dir):
                target = os.path.join(script_dir, name)
                try:
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                        if os.path.exists(target):
                            # 目录内仍有文件被占用，未能完全删除
                            failed_items.append(target)
                            print(f"未能完全删除目录: {target}")
                        else:
                            print(f"已删除目录: {target}")
                    else:
                        os.remove(target)
                        print(f"已删除文件: {target}")
                except Exception as e:
                    failed_items.append(target)
                    print(f"删除失败: {target} ({str(e)})")
        except Exception as e:
            print(f"遍历程序文件夹时发生错误: {str(e)}")
        
        if failed_items:
            print("\n以下内容正在被占用（通常是运行中的 python.exe 及其依赖），无法自动删除，请退出后手动清理:")
            for item in failed_items:
                print(f"  - {item}")
        else:
            print("程序文件夹已清空")
        
        print("\n正在关闭服务器进程...")
        os._exit(0)
    
    def do_GET(self):
        # 提供前端页面
        if self.path == '/':
            self.path = '/index.html'
            
        
        try:
            # 打开并读取文件
            with open(self.path[1:], 'rb') as f:
                content = f.read()
            
            # 先发送响应码
            self.send_response(200)
            
            # 设置正确的Content-Type
            if self.path.endswith('.html'):
                self.send_header('Content-type', 'text/html')
            elif self.path.endswith('.js'):
                self.send_header('Content-type', 'application/javascript')
            elif self.path.endswith('.css'):
                self.send_header('Content-type', 'text/css')
            elif self.path.endswith('.xlsx'):
                self.send_header('Content-type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            elif self.path.endswith('xlsx.full.min.js'):
                self.send_header('Content-type', 'application/javascript')
            self.end_headers()
            self.wfile.write(content)
            
        except Exception as e:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f'File not found: {str(e)}'.encode('utf-8'))

EXIT_TRACE_NAME = 'install_server_last_exit.log'


def _exit_trace_path():
    """退出/崩溃痕迹文件路径 —— 必须落在**程序文件夹之外**

    exit_server() 会把程序文件夹整个清空（含子目录），写在里面的日志会跟着被删掉，
    事后什么都留不下，只能看到"窗口闪了一下就没了"。所以痕迹写到上一级目录
    （一般是桌面）里，删不掉。
    """
    try:
        return os.path.join(os.path.dirname(_script_dir()), EXIT_TRACE_NAME)
    except Exception:
        return os.path.join(_script_dir(), EXIT_TRACE_NAME)


def _append_exit_trace(title, detail):
    """把退出/崩溃痕迹追加写入文件（写失败也不影响主流程）"""
    try:
        with open(_exit_trace_path(), 'a', encoding='utf-8') as f:
            f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {title} =====\n")
            f.write(detail.rstrip() + "\n\n")
        print(f"[痕迹] 已写入: {_exit_trace_path()}")
    except Exception as e:
        print(f"[痕迹] 写入失败: {e}")


def _hold_window(prompt='按回车键关闭此窗口...'):
    """留住控制台窗口

    Boot_Software_Install.cmd 是用 `start` 拉起本进程的 —— 进程一退出，那个窗口
    立刻消失（就是"闪退"），报错根本来不及看。致命情况下必须停下来等一次回车。
    """
    try:
        input(prompt)
    except Exception:
        pass


def _port_in_use(host='127.0.0.1', port=8888, timeout=1.5):
    """8888 上是否已经有人在监听

    不能靠"bind 失败"判断：Windows 的 SO_REUSEADDR 允许**两个进程绑同一个端口**，
    实测第二个实例照样能起来。这时浏览器请求可能被**旧实例**接走，看到的行为就
    不是最新代码 —— 排查时这是个极大的干扰源，必须主动连一下才知道。
    """
    import socket as _socket
    s = _socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _report_fatal(detail):
    """启动/运行期致命错误：留痕 + 打印 + 留住窗口（绝不再"闪退"）"""
    _append_exit_trace('致命错误', detail)
    print('=' * 70)
    print('[致命] 安装服务异常退出，窗口已保留，请把下面的信息截图反馈：')
    print(detail)
    if '10048' in detail or 'WSAEADDRINUSE' in detail:
        print('-' * 70)
        print('原因看起来是端口 8888 已被占用 —— 多半已经有一个安装服务在运行。')
    print(f'已留痕到: {_exit_trace_path()}')
    print('=' * 70)
    _hold_window()


def run_server():
    # 每次启动清空调试日志，排查时只看本次运行
    if DEBUG:
        try:
            with open(os.path.join(_script_dir(), DEBUG_LOG), 'w', encoding='utf-8') as f:
                f.write('')
        except Exception:
            pass
    _log_startup_banner()

    # 已经有实例在跑就别再起一个：Windows 下两个实例会**同时监听 8888**，
    # 浏览器可能连到旧实例（不带最新改动），行为完全对不上
    if _port_in_use():
        _log('[启动] 警告: 8888 端口已有服务在监听')
        print('=' * 70)
        print('[启动] 8888 端口已经有服务在运行（多半是上一次那个窗口还没关）。')
        print('       浏览器打开的可能是**旧实例**，它不带最新改动。')
        print('       请先在任务管理器结束旧的 python.exe，再重新运行本程序。')
        print('=' * 70)
        _hold_window()
        return

    server_address = ('', 8888)
    httpd = HTTPServer(server_address, InstallHandler)
    print('Server running at http://localhost:8888/')
    _log('Server running at http://localhost:8888/')
    httpd.serve_forever()
    _log('[退出] serve_forever 已返回，服务结束')


if __name__ == '__main__':
    try:
        run_server()
    except Exception:
        import traceback
        _report_fatal(traceback.format_exc())