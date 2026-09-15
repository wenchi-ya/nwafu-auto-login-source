"""NWAFU campus login helper. Windows 10+, Python 3.10+, Microsoft Edge."""
import argparse
import ctypes
from ctypes import wintypes
import getpass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORTAL = "https://portal.nwafu.edu.cn/srun_portal_pc?ac_id=1&theme=pro"
HOST = "portal.nwafu.edu.cn"
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NWAFUAutoLogin"


def trusted_url(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme == "https" and p.hostname == HOST and p.port in (None, 443) and not p.username


def protect(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(dst.data)


def write_json(name, value):
    target = DATA / name
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)


def profiles():
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "ConvertTo-Json -InputObject @(Get-NetConnectionProfile | Select-Object -ExpandProperty Name) -Compress"
    ], capture_output=True, timeout=15, creationflags=0x08000000)
    if result.returncode:
        return []
    return json.loads(result.stdout.decode("utf-8-sig").strip() or "[]")


def configure():
    print("账号和密码仅在本机使用，由 Windows DPAPI 按当前用户加密保存。")
    user = input("校园网账号：").strip()
    password = getpass.getpass("校园网密码（输入不显示）：")
    if not user or not password:
        raise ValueError("账号和密码不能为空")
    try:
        names = profiles()
    except Exception:
        names = []
    print("当前网络名称：" + ("、".join(names) or "未识别"))
    print("如果当前连接校园网，可填写上面的校园网络名称；不要填写家庭网络或热点名称。")
    network = input("校园网络名称（回车跳过，仅根据认证跳转判断）：").strip()
    encrypted = protect(json.dumps({"username": user, "password": password}).encode("utf-8"))
    (DATA / "credentials.bin").write_bytes(encrypted)
    write_json("config.json", {"network_name": network})
    write_json("state.json", {"failures": 0, "next_attempt": 0, "paused": False})
    print("配置已保存。")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe():
    """No redirect following; credential-free probes only."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    captive = False
    for url, expected in [
        ("http://www.msftconnecttest.com/connecttest.txt", b"Microsoft Connect Test"),
        ("http://detectportal.firefox.com/success.txt", b"success")
    ]:
        try:
            with opener.open(url, timeout=6) as r:
                body = r.read(8192).strip()
                # Microsoft provides a stable exact response for Windows connectivity checks.
                if r.status == 200 and body == expected:
                    return True, False
                # Some portals redirect through HTML rather than an HTTP Location header.
                if HOST.encode() in body:
                    captive = True
        except urllib.error.HTTPError as e:
            loc = urllib.parse.urljoin(url, e.headers.get("Location", ""))
            if 300 <= e.code < 400 and urllib.parse.urlsplit(loc).hostname == HOST:
                captive = True
        except (OSError, ValueError):
            pass
    return False, captive


def login(credentials, visible=False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=not visible)
        try:
            ctx = browser.new_context(ignore_https_errors=False)
            # Keep credential-bearing requests restricted to the confirmed HTTPS portal.
            ctx.route("**/*", lambda route: route.continue_() if trusted_url(route.request.url) else route.abort())
            page = ctx.new_page()
            page.set_default_timeout(12000)
            page.goto(PORTAL, wait_until="domcontentloaded", timeout=30000)
            if not trusted_url(page.url):
                raise RuntimeError("unexpected portal origin")
            password = page.locator('input[type="password"]:visible')
            password.first.wait_for(state="visible")
            username = page.locator(
                'input#username:visible, input[name="username"]:visible, '
                'input#user_name:visible, input[name="user_name"]:visible'
            )
            if username.count() != 1:
                username = page.locator('input[type="text"]:visible, input[type="tel"]:visible, input:not([type]):visible')
            if username.count() != 1 or password.count() != 1:
                raise RuntimeError("ambiguous login fields")
            username.fill(credentials["username"])
            password.fill(credentials["password"])
            button = page.locator('#login:visible, #login-account:visible, #login-btn:visible')
            if button.count() != 1:
                button = page.get_by_role("button", name="登录", exact=True)
            if button.count() != 1:
                button = page.get_by_text("登录", exact=True)
            if button.count() != 1 or not trusted_url(page.url):
                raise RuntimeError("ambiguous login button")
            page.on("dialog", lambda dialog: dialog.dismiss())
            button.click()
            # Never export page text, URLs or screenshots: they may contain credentials.
            for _ in range(4):
                time.sleep(3)
                online, _ = probe()
                if online:
                    return "success"
                body = page.locator("body").inner_text(timeout=5000)
                if any(s in body for s in ("密码错误", "密码不正确", "用户名或密码", "账号或密码错误", "E2531", "E2553")):
                    return "bad_credentials"
            return "failed"
        finally:
            browser.close()


def run(force=False, visible=False):
    config = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
    state_path = DATA / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    online, captive = probe()
    if online:
        logging.info("网络正常，无需认证。")
        return 0
    if not force:
        if state.get("paused"):
            logging.info("自动认证已暂停，请重新配置账号密码。")
            return 1
        if time.time() < state.get("next_attempt", 0):
            logging.info("等待重试间隔。")
            return 0
        network = config.get("network_name")
        if not captive and not (network and network in profiles()):
            logging.info("未识别到校园网络，跳过认证。")
            return 0
    # Save a cooldown before attempting, including when the browser exits unexpectedly.
    failures = state.get("failures", 0) + 1
    state.update(failures=failures, next_attempt=time.time() + min(3600, 300 * 2 ** min(failures - 1, 4)))
    if failures >= 5:
        state["paused"] = True
    write_json("state.json", state)
    credentials = json.loads(protect((DATA / "credentials.bin").read_bytes(), decrypt=True))
    try:
        result = login(credentials, visible)
    except Exception as e:
        logging.error("认证未完成（%s）。请检查 Edge、门户证书或页面结构；可运行可视测试。", type(e).__name__)
        return 1
    if result == "success":
        write_json("state.json", {"failures": 0, "next_attempt": 0, "paused": False})
        logging.info("认证成功，网络已恢复。")
        return 0
    if result == "bad_credentials" or failures >= 5:
        state["paused"] = True
        logging.error("账号密码提示异常或已连续失败 5 次，暂停自动认证。请检查后重新配置。")
    else:
        logging.warning("未确认网络恢复，稍后重试。")
    write_json("state.json", state)
    return 1


def acquire_lock(lock, wait_seconds=0):
    """Lock byte zero without reading it: another process may already own it.

    Windows supports byte-range locks beyond EOF, including an empty file.
    """
    import errno
    import msvcrt
    deadline = time.monotonic() + wait_seconds
    while True:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--login", action="store_true", help="Explicitly attempt portal login when offline, ignoring network/cooldown gates")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    # Acquire before opening the rotating log as well, to avoid concurrent rotation.
    manual = args.configure or args.login or args.visible
    try:
        lock = (DATA / "run.lock").open("a+b")
    except OSError:
        if sys.stdout is not None:
            print("无法打开锁文件，请检查本机数据目录权限。")
        return 1
    with lock:
        if not acquire_lock(lock):
            if not manual:
                return 0
            if sys.stdout is not None:
                print("后台认证正在运行，等待最多 30 秒……", flush=True)
            if not acquire_lock(lock, wait_seconds=30):
                if sys.stdout is not None:
                    print("后台仍在运行，本次操作未执行，请稍后再试。", flush=True)
                return 2
        handlers = [RotatingFileHandler(DATA / "activity.log", maxBytes=200000, backupCount=2, encoding="utf-8")]
        if sys.stdout is not None:
            handlers.append(logging.StreamHandler(sys.stdout))
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
        try:
            if args.configure:
                configure()
                return 0
            return run(args.login, args.visible)
        except Exception as e:
            logging.error("无法运行（%s）。请先首次配置；需要 Python 3.10+ 和 Microsoft Edge。", type(e).__name__)
            return 1


if __name__ == "__main__":
    sys.exit(main())
