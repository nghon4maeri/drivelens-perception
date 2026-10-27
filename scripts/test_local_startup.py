"""Script kiểm tra khởi động thực tế Backend (Uvicorn) và Frontend (Vite) trên Local.

Chức năng:
1. Khởi động tiến trình Backend: `uvicorn src.rav.api:app --host 127.0.0.1 --port 8000`
2. Khởi động tiến trình Frontend: `npm run dev` (cổng 3000) trong `src/web`
3. Kiểm tra HTTP Liveness & Readiness của Backend (8000)
4. Kiểm tra HTTP GET trang Frontend (3000)
5. Kiểm tra Reverse Proxy từ Frontend sang Backend (/api/* qua cổng 3000)
6. Dọn dẹp tiến trình an toàn sau khi hoàn thành.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "src" / "web"


def check_url(url: str, timeout: float = 10.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StartupVerifier/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            return resp.status, content
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8", errors="replace")
        return e.code, content
    except Exception as e:
        return -1, str(e)


def main():
    print(f"[STARTUP CHECK] Repo root: {ROOT_DIR}")
    print(f"[STARTUP CHECK] Web dir: {WEB_DIR}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)

    # 1. Khởi động Backend
    print("\n--- 1. Khởi động Backend (uvicorn src.rav.api:app) ---")
    be_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.rav.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--log-level",
        "warning",
    ]
    be_proc = subprocess.Popen(
        be_cmd,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print(f"[BE] Process PID: {be_proc.pid}")

    # 2. Khởi động Frontend
    print("\n--- 2. Khởi động Frontend (npm run dev) ---")
    fe_cmd = "npm run dev"
    fe_proc = subprocess.Popen(
        fe_cmd,
        cwd=str(WEB_DIR),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print(f"[FE] Process PID: {fe_proc.pid}")

    results = {}
    be_ready = False
    fe_ready = False
    try:
        # Chờ Backend sẵn sàng
        print("\n[WAIT] Đang chờ Backend (127.0.0.1:8000/health)...")
        for i in range(25):
            time.sleep(0.5)
            status, body = check_url("http://127.0.0.1:8000/health")
            if status == 200:
                print(f"[BE] Ready sau {(i+1)*0.5:.1f}s! Status: 200, Body: {body}")
                be_ready = True
                break
        if not be_ready:
            print("[BE] Thất bại: Backend không phản hồi sau 12.5s!")

        # Chờ Frontend sẵn sàng
        print("\n[WAIT] Đang chờ Frontend (127.0.0.1:3000)...")
        for i in range(25):
            time.sleep(0.5)
            status, body = check_url("http://127.0.0.1:3000/")
            if status == 200:
                print(f"[FE] Ready sau {(i+1)*0.5:.1f}s! Status: 200, HTML length: {len(body)}")
                fe_ready = True
                break
        if not fe_ready:
            print("[FE] Thất bại: Frontend không phản hồi sau 12.5s!")

        # 3. Kiểm tra chi tiết endpoints Backend
        print("\n--- 3. Kiểm tra chi tiết endpoints Backend (Port 8000) ---")
        endpoints = [
            ("Liveness Probe (/health)", "http://127.0.0.1:8000/health"),
            ("Readiness Probe (/health/ready)", "http://127.0.0.1:8000/health/ready"),
            ("V2 Health Probe (/api/v2/health)", "http://127.0.0.1:8000/api/v2/health"),
            ("API Videos List (/api/v1/videos)", "http://127.0.0.1:8000/api/v1/videos"),
            ("API Sessions List (/api/v1/sessions)", "http://127.0.0.1:8000/api/v1/sessions"),
            ("API Models List (/api/v1/models)", "http://127.0.0.1:8000/api/v1/models"),
        ]
        for name, url in endpoints:
            st, res = check_url(url)
            print(f"  * {name}: HTTP {st}")
            try:
                parsed = json.loads(res)
                print(f"    Payload: {json.dumps(parsed, indent=2)[:200]}...")
            except Exception:
                print(f"    Content preview: {res[:120]}...")
            results[name] = (st == 200)

        # 4. Kiểm tra Frontend & Vite Proxy sang Backend (Port 3000)
        print("\n--- 4. Kiểm tra Frontend & Proxy (Port 3000) ---")
        fe_checks = [
            ("FE Index HTML", "http://127.0.0.1:3000/"),
            ("FE Proxy -> /api/v1/videos", "http://127.0.0.1:3000/api/v1/videos"),
            ("FE Proxy -> /api/v1/sessions", "http://127.0.0.1:3000/api/v1/sessions"),
        ]
        for name, url in fe_checks:
            st, res = check_url(url)
            print(f"  * {name}: HTTP {st}")
            results[name] = (st == 200)

    finally:
        # Dọn dẹp tiến trình
        print("\n--- Dọn dẹp tiến trình ---")
        be_proc.terminate()
        try:
            be_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            be_proc.kill()
        print("[BE] Đã dừng tiến trình Backend.")

        # Trên Windows, fe_proc chạy qua shell=True nên dùng taskkill để diệt cả cây process
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(fe_proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            fe_proc.terminate()
        print("[FE] Đã dừng tiến trình Frontend.")

    all_passed = all(results.values()) and be_ready and fe_ready
    print("\n==========================================")
    print(f"KẾT QUẢ TỔNG THỂ: {'THÀNH CÔNG (PASSED)' if all_passed else 'THẤT BẠI (FAILED)'}")
    print("==========================================")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
