#!/usr/bin/env python3
"""
独立登录脚本 - 为 DJ Set Curator 提供二维码登录

用法:
    # 1. 获取二维码并自动等待扫码
    python3 scripts/login.py
    
    # 2. 仅检查当前登录状态
    python3 scripts/login.py --check
"""

import sys
import time
import json
import os
import argparse
from pathlib import Path

# 找到 MCP server 的 storage 目录
MCP_STORAGE_DIR = Path(
    os.path.expanduser("~/Desktop/Projects/ncm-dj-toolkit/cloud-music-mcp-extended/src/cloud_music_mcp/storage")
)
COOKIE_FILE = MCP_STORAGE_DIR / "cookies.json"


def ensure_mcp_storage():
    MCP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def save_session_to_mcp(cookies: dict):
    ensure_mcp_storage()
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"✅ 登录状态已保存到: {COOKIE_FILE}")


def check_login_status():
    """检查当前是否已登录"""
    try:
        from pyncm import apis, GetCurrentSession
        if COOKIE_FILE.exists():
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            GetCurrentSession().cookies.update(cookies)
        
        user_info = apis.login.GetCurrentLoginStatus()
        if user_info.get("code") == 200 and user_info.get("profile"):
            nickname = user_info["profile"]["nickname"]
            return True, nickname
    except Exception:
        pass
    return False, None


def do_login():
    print("=" * 50)
    print("网易云音乐 - DJ Set Curator 登录")
    print("=" * 50)

    try:
        from pyncm import apis, GetCurrentSession
    except ImportError:
        print("❌ 错误: 未找到 pyncm")
        print("   请先激活 cloud-music-mcp-extended 的虚拟环境:")
        print("   cd ../cloud-music-mcp-extended && source .venv/bin/activate")
        sys.exit(1)

    # 1. 获取二维码
    print("\n📱 正在获取登录二维码...")
    result = apis.login.LoginQrcodeUnikey(1)
    if result.get("code") != 200:
        print("❌ 获取二维码失败")
        sys.exit(1)

    uuid = result["unikey"]
    qr_url = f"https://music.163.com/login?codekey={uuid}"

    print(f"\n{'=' * 50}")
    print("请用网易云音乐 App 扫描以下二维码:")
    print(f"\n   {qr_url}\n")
    print("=" * 50)

    # 2. 自动轮询（60 秒）
    print("\n⏳ 等待扫码中... (60 秒自动检测)")
    max_retries = 30
    for i in range(max_retries):
        result = apis.login.LoginQrcodeCheck(uuid)
        code = result.get("code")

        if code == 800:
            print("\n❌ 二维码已过期")
            sys.exit(1)
        elif code == 803:
            print("\n✅ 扫码成功！正在保存...")
            if "cookie" in result:
                apis.login.WriteLoginInfo(result["cookie"])
            cookies = GetCurrentSession().cookies.get_dict()
            save_session_to_mcp(cookies)

            try:
                user_info = apis.login.GetCurrentLoginStatus()
                nickname = user_info.get("profile", {}).get("nickname", "用户")
                print(f"\n🎉 欢迎回来，{nickname}！")
            except Exception:
                print("\n🎉 登录成功！")

            print("\n现在可以运行:")
            print('   dj-curator -a "WANTCHU" --count 10 --arrange warm-up')
            return

        # 每 5 秒显示一次进度
        if i > 0 and i % 5 == 0:
            print(f"   ... 已等待 {i * 2} 秒", end="\r")

        time.sleep(2)

    print("\n\n❌ 登录超时，请重新运行脚本并尽快扫码")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="DJ Set Curator 登录工具")
    parser.add_argument("--check", action="store_true", help="仅检查登录状态")
    args = parser.parse_args()

    if args.check:
        logged_in, nickname = check_login_status()
        if logged_in:
            print(f"✅ 已登录: {nickname}")
            sys.exit(0)
        else:
            print("❌ 未登录")
            sys.exit(1)
    else:
        do_login()


if __name__ == "__main__":
    main()
