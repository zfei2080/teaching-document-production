"""
批处理监控脚本 — 解决"跑完不知道、挂了不知道、进度看不到"的铁三角问题。

用法：
    python monitor.py -- python batch_processor.py --dir ... --grade 初中 ...

功能：
1. 实时写进度文件（heartbeat.jsonl），每文件一个条目
2. 完成后发 PushPlus 通知（需 PUSHPLUS_TOKEN 环境变量）
3. 超时自动终止（默认 60 分钟）
4. 退出码透传，方便 CI 判断
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _notify_pushplus(token: str, title: str, content: str) -> bool:
    """通过 PushPlus 发送微信通知。"""
    try:
        import urllib.request

        payload = json.dumps({"token": token, "title": title, "content": content}).encode()
        req = urllib.request.Request(
            "https://www.pushplus.plus/send",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[monitor] PushPlus 通知失败: {e}", file=sys.stderr)
        return False


def _read_heartbeats(heartbeat_file: Path):
    """读取进度文件，返回最后一条有效 JSON。"""
    if not heartbeat_file.exists():
        return None
    lines = []
    with open(heartbeat_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
    if lines:
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    return None


def monitor(
    cmd: list[str],
    heartbeat_file: Path,
    pushplus_token: Optional[str] = None,
    timeout_minutes: int = 60,
):
    """启动子进程并监控。"""

    print(f"[monitor] {_now()} 启动: {' '.join(cmd)}")
    print(f"[monitor] 进度文件: {heartbeat_file}")

    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    log_file = heartbeat_file.with_suffix(".log")
    with open(heartbeat_file, "a", encoding="utf-8") as f:
        json.dump(
            {"ts": _now(), "event": "started", "cmd": " ".join(cmd)},
            f,
            ensure_ascii=False,
        )
        f.write("\n")

    start_time = time.time()
    last_heartbeat = start_time
    all_output = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        line_queue: queue.Queue[str | None] = queue.Queue()

        def _reader():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line_queue.put(line)
            finally:
                line_queue.put(None)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        with open(log_file, "w", encoding="utf-8") as log_fp:
            while True:
                try:
                    line = line_queue.get(timeout=1)
                except queue.Empty:
                    line = None

                if isinstance(line, str):
                    line = line.rstrip("\n")
                    print(line)
                    log_fp.write(line + "\n")
                    log_fp.flush()
                    all_output.append(line)
                elif proc.poll() is not None and line_queue.empty():
                    break

                now = time.time()
                if now - last_heartbeat >= 10:
                    with open(heartbeat_file, "a", encoding="utf-8") as f:
                        json.dump(
                            {
                                "ts": _now(),
                                "event": "alive",
                                "elapsed_s": int(now - start_time),
                                "output_lines": len(all_output),
                            },
                            f,
                            ensure_ascii=False,
                        )
                        f.write("\n")
                    last_heartbeat = now

                if now - start_time > timeout_minutes * 60:
                    print(f"[monitor] {_now()} 超时 ({timeout_minutes}min)，强制终止")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise TimeoutError(f"进程超过 {timeout_minutes} 分钟未完成")

        exit_code = proc.wait(timeout=30)
        duration_s = int(time.time() - start_time)
        with open(heartbeat_file, "a", encoding="utf-8") as f:
            json.dump(
                {
                    "ts": _now(),
                    "event": "finished",
                    "exit_code": exit_code,
                    "duration_s": duration_s,
                    "output_lines": len(all_output),
                },
                f,
                ensure_ascii=False,
            )
            f.write("\n")

        if pushplus_token:
            title = f"lecture-generator 批处理{'完成' if exit_code == 0 else '失败'}"
            content = "\n".join(
                [
                    f"退出码: {exit_code}",
                    f"耗时: {duration_s}s",
                    f"输出行数: {len(all_output)}",
                    f"日志: {log_file}",
                    "",
                    "最后 10 行输出:",
                ]
                + all_output[-10:]
            )
            _notify_pushplus(pushplus_token, title, content)

        print(f"[monitor] {_now()} 完成, exit_code={exit_code}, 耗时 {duration_s}s")
        return exit_code

    except Exception as e:
        with open(heartbeat_file, "a", encoding="utf-8") as f:
            json.dump(
                {
                    "ts": _now(),
                    "event": "error",
                    "error": str(e),
                    "output_lines": len(all_output),
                },
                f,
                ensure_ascii=False,
            )
            f.write("\n")
        print(f"[monitor] {_now()} 异常: {e}", file=sys.stderr)
        if pushplus_token:
            _notify_pushplus(
                pushplus_token,
                "lecture-generator 批处理异常",
                f"异常: {e}\n日志: {log_file}",
            )
        return 1


def main():
    parser = argparse.ArgumentParser(description="批处理监控包装器")
    parser.add_argument(
        "--heartbeat", default="output/heartbeat.jsonl", help="心跳文件路径"
    )
    parser.add_argument("--timeout", type=int, default=60, help="超时分钟数")
    subp = parser.add_argument_group("子进程命令（-- 之后的部分）")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="要监控的命令")

    args = parser.parse_args()

    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]

    if not args.cmd:
        parser.error("缺少子进程命令。示例: python monitor.py -- python batch_processor.py --dir ...")

    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if token:
        print(f"[monitor] PushPlus 已配置 (token: {token[:8]}...)")
    else:
        print("[monitor] PushPlus 未配置，跳过微信通知（设置 PUSHPLUS_TOKEN 环境变量即可启用）")

    exit_code = monitor(
        cmd=args.cmd,
        heartbeat_file=Path(args.heartbeat),
        pushplus_token=token,
        timeout_minutes=args.timeout,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
