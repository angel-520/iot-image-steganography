"""以一个前台 Python 进程运行 ISTG Studio，按 Ctrl+C 停止。"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import os
import ipaddress


ROOT = Path(__file__).resolve().parent


def port_number(value: str) -> int:
    """限制为可用的 TCP 端口范围。"""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须为整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1–65535 之间")
    return port


def main() -> int:
    # Windows 的终端沿用其原生 Unicode 输出；重定向日志统一使用 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="在本机运行 ISTG Studio。前端与 API 由同一个 Python 进程提供，按 Ctrl+C 停止。",
    )
    parser.add_argument("--port", type=port_number, default=8000, help="监听端口，默认 8000")
    parser.add_argument('--host', default='127.0.0.1', help='默认回环；局域网可显式设置 0.0.0.0')
    parser.add_argument('--public-host', action='append', default=[], help='允许访问的 IP / 域名，可重复设置；不含协议或端口')
    parser.add_argument('--ssl-certfile', type=Path, help='HTTPS 证书 PEM；远程摄像头需要安全上下文')
    parser.add_argument('--ssl-keyfile', type=Path, help='HTTPS 证书私钥 PEM')
    args = parser.parse_args()
    try:
        ipaddress.ip_address(args.host)
    except ValueError:
        parser.error('--host 必须是本机监听 IP')
    if any(not host or any(char in host for char in '/*:?#@ \\') for host in args.public_host):
        parser.error('--public-host 应为明确的 IPv4 或域名，不允许通配符、协议或端口')
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error('HTTPS 需同时提供 --ssl-certfile 和 --ssl-keyfile')
    if args.host != '127.0.0.1' and not args.public_host:
        parser.error('远程监听时需用 --public-host 明确列出网站访问地址')
    if args.public_host:
        os.environ['ISTG_PUBLIC_HOSTS'] = ','.join(args.public_host)

    if sys.version_info < (3, 11):
        print("需要 Python 3.11 或更新版本。请使用项目 .venv 中的 Python 运行。", file=sys.stderr)
        return 1

    dependencies = {
        "fastapi": "FastAPI", "uvicorn": "Uvicorn", "pydantic": "Pydantic",
        "multipart": "python-multipart", "PIL": "Pillow", "numpy": "NumPy",
        "scipy": "SciPy", "skimage": "scikit-image", "cbor2": "cbor2",
        "cryptography": "cryptography",
        "imageio_ffmpeg": "imageio-ffmpeg",
        "scapy": "Scapy", "sklearn": "scikit-learn",
        'websockets': 'websockets',
    }
    missing = [label for module, label in dependencies.items() if importlib.util.find_spec(module) is None]
    if missing:
        print("缺少 Python 依赖：" + "、".join(missing), file=sys.stderr)
        print("请在项目根目录使用同一个 Python 解释器安装：", file=sys.stderr)
        print(f'  "{sys.executable}" -m pip install -r requirements.txt', file=sys.stderr)
        return 1

    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        print("尚未找到构建后的网页。请在项目根目录依次执行：", file=sys.stderr)
        print("  cd frontend\n  npm ci\n  npm run build\n  cd ..", file=sys.stderr)
        print("然后再次运行本 Python 命令。", file=sys.stderr)
        return 1

    import uvicorn

    scheme = 'https' if args.ssl_certfile else 'http'
    print(f"ISTG Studio：{scheme}://{args.public_host[0] if args.public_host else '127.0.0.1'}:{args.port}", flush=True)
    print("在浏览器打开以上地址；在此终端按 Ctrl+C 停止并清理本次临时文件。", flush=True)
    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        access_log=False,
        timeout_graceful_shutdown=30,
        ws_max_size=40 * 1024,
        ws_max_queue=16,
        ssl_certfile=str(args.ssl_certfile) if args.ssl_certfile else None,
        ssl_keyfile=str(args.ssl_keyfile) if args.ssl_keyfile else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
