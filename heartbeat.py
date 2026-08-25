#!/usr/bin/env python3
"""
Technocore DID 心跳脚本 —— 用 calmanzeng 的 Ed25519 DID 定期在房间发签名"alive"消息，
保持 DID 在 Technocore 的活跃度（项目方若按活跃度筛选空投资格则有用）。

特性：
  - 轮换房间（lobby 为主，偶尔 technocore），贴近其他 agent 的"alive"节奏
  - 自动重试瞬时 HTTP 500（最多 3 次）
  - 单调 nonce 状态文件，避免同一毫秒重复 nonce 被拒
  - 日志留存 heartbeat.log（含 seq，用于日后核对参与证据）
  - 两种模式：单次（默认，交给系统定时任务触发）/ --loop 常驻（自带随机间隔）

用法：
  .venv\\Scripts\\python heartbeat.py            # 发一条心跳
  .venv\\Scripts\\python heartbeat.py --loop     # 常驻，每 ~20-40 分钟一条（Ctrl+C 退出）
  .venv\\Scripts\\python heartbeat.py --room lobby --text "自定义消息"
"""

from __future__ import annotations
import argparse, json, math, os, pathlib, random, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # import technocore_agent
import technocore_agent as tc

# ---- 配置（按你本次贡献的固定凭据）----
KEY_PATH = HERE / "identity.pem"
PASSPHRASE = "FlopAirdrop2026!CalmanZenG"
BASE_URL = "https://technocore.chat"
DID = "did:key:z6MkqPq4euKHBSakLjX2uLHx1726jHaUiS8SSzUE6VMuvXS7"
STATE_FILE = HERE / "heartbeat_nonce.json"
LOG_FILE = HERE / "heartbeat.log"

ROOMS_PRIMARY = "lobby"
ROOMS_ROTATE = ("lobby", "technocore")

ALIVE_TEXT = f"FLOP agent node for calmanzeng alive — DID {DID}"


def load_key() -> "tc.Ed25519PrivateKey":
    if not KEY_PATH.exists():
        raise SystemExit(f"[heartbeat] 未找到私钥 {KEY_PATH}，请先运行 driver.py 创建 DID")
    return tc.load_identity(KEY_PATH, passphrase=PASSPHRASE.encode("utf-8"), allow_prompt=False)


def next_nonce() -> int:
    """纳秒时间戳为基准，并用状态文件保证严格单调递增。

    关键：原 technocore_agent.next_nonce() 用的是 time.time_ns()（纳秒，13-19位），
    服务端记录的"上次 nonce"也是纳秒级。若只用毫秒*1000 会远小于服务端值而 400。
    这里 base 用纳秒；状态文件存上次发出的 nonce，确保不回退、不碰撞。
    """
    base = time.time_ns()
    last = 0
    if STATE_FILE.exists():
        try:
            last = int(json.loads(STATE_FILE.read_text())["nonce"])
        except Exception:
            last = 0
    n = max(base, last + 1)
    STATE_FILE.write_text(json.dumps({"nonce": n}))
    return n


def post_with_retry(key, room: str, text: str, tries: int = 3) -> dict:
    last_err = None
    for i in range(tries):
        try:
            return tc.post_signed_message(
                key, room, text, nonce=next_nonce(), base_url=BASE_URL
            )
        except (tc.NetworkError, tc.ProtocolError) as e:
            last_err = e
            time.sleep(2 + i * 2)  # 退避后重试（覆盖偶发 500）
    raise last_err or RuntimeError("unknown post failure")


def log_line(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"[{ts}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.rstrip())


def beat_once(room: str | None = None, text: str | None = None) -> bool:
    key = load_key()
    target = room or random.choices(ROOMS_ROTATE, weights=[4, 1])[0]
    payload = text or ALIVE_TEXT
    try:
        resp = post_with_retry(key, target, payload)
        seq = resp.get("posted", {}).get("seq")
        log_line(f"OK room={target} seq={seq} did={DID}")
        return True
    except Exception as e:
        log_line(f"FAIL room={target} err={e}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Technocore DID heartbeat")
    ap.add_argument("--loop", action="store_true", help="常驻循环模式")
    ap.add_argument("--room", default=None, help="指定房间（默认轮换）")
    ap.add_argument("--text", default=None, help="自定义消息文本")
    ap.add_argument("--min", type=int, default=20, help="循环最小间隔（分钟）")
    ap.add_argument("--max", type=int, default=40, help="循环最大间隔（分钟）")
    args = ap.parse_args()

    if not args.loop:
        ok = beat_once(args.room, args.text)
        raise SystemExit(0 if ok else 1)

    log_line("heartbeat loop started (Ctrl+C to stop)")
    try:
        while True:
            beat_once(args.room, args.text)
            wait = random.uniform(args.min, args.max) * 60
            time.sleep(wait)
    except KeyboardInterrupt:
        log_line("heartbeat loop stopped by user")


if __name__ == "__main__":
    main()
