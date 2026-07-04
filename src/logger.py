# -*- coding: utf-8 -*-
"""
统一日志模块。

参考：https://github.com/YoukoSaint/Logging_Standard_for_Agent 的核心规范。

特性：
- 双 Handler：文件 (DEBUG) + 控制台 (INFO)。
- 每次启动一个时间戳日志文件，避免轮转。
- 子 logger：
  * ``servo.protocol`` —— 协议层 TX/RX 流量，独立文件，DEBUG 级别，含 HEX 转储。
  * ``servo.audit``    —— 用户操作审计，独立文件，INFO 级别。
  * ``servo.health``  —— 周期健康报告（连接状态、TX/RX 计数、错误数）。
- 延迟格式化：调用方传 %s 而非 f-string。

启动后日志落在：
    logs/servo_ctrl_YYYYMMDD_HHMMSS.log          主日志（INFO+）
    logs/servo_ctrl_YYYYMMDD_HHMMSS_protocol.log 协议流量（含 HEX）
    logs/servo_ctrl_YYYYMMDD_HHMMSS_audit.log    审计
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


APP_NAME = "servo_ctrl"
LOGGER_PROTOCOL = "servo.protocol"
LOGGER_AUDIT = "servo.audit"
LOGGER_HEALTH = "servo.health"


def setup_logging(
    log_dir: str | os.PathLike = "logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> Path:
    """初始化全局日志系统，返回本次启动的主日志文件路径。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{APP_NAME}_{ts}.log"
    proto_path = log_dir / f"{APP_NAME}_{ts}_protocol.log"
    audit_path = log_dir / f"{APP_NAME}_{ts}_audit.log"
    health_path = log_dir / f"{APP_NAME}_{ts}_health.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if getattr(root, "_servo_ctrl_configured", False):
        return log_path
    root._servo_ctrl_configured = True  # type: ignore[attr-defined]

    # --- 主日志：DEBUG 级别，写文件 -----------------------------------
    file_fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    # --- 控制台：INFO 级别，简洁输出 ---------------------------------
    console_fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-7s] %(message)s", datefmt="%H:%M:%S"
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_fmt)
    root.addHandler(console_handler)

    # --- 协议独立日志：DEBUG 级别，写独立文件，不传播 ----------------
    proto_logger = logging.getLogger(LOGGER_PROTOCOL)
    proto_logger.setLevel(logging.DEBUG)
    proto_handler = RotatingFileHandler(
        proto_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    proto_handler.setLevel(logging.DEBUG)
    proto_handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
    )
    proto_logger.addHandler(proto_handler)
    proto_logger.propagate = True   # 协议流量同时也写到主日志文件和控制台

    # --- 审计日志：INFO 级别，写独立文件 -----------------------------
    audit_logger = logging.getLogger(LOGGER_AUDIT)
    audit_logger.setLevel(logging.INFO)
    audit_handler = RotatingFileHandler(
        audit_path, maxBytes=1 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d [AUDIT] %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False

    # --- 健康日志：INFO 级别，写独立文件 -----------------------------
    health_logger = logging.getLogger(LOGGER_HEALTH)
    health_logger.setLevel(logging.INFO)
    health_handler = RotatingFileHandler(
        health_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    health_handler.setLevel(logging.INFO)
    health_handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s",
                                                  datefmt="%Y-%m-%d %H:%M:%S"))
    health_logger.addHandler(health_handler)
    health_logger.propagate = False

    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
#  审计便捷函数
# ---------------------------------------------------------------------------

_audit_log = logging.getLogger(LOGGER_AUDIT)
_health_log = logging.getLogger(LOGGER_HEALTH)


def audit(action: str, resource: str = "-", result: str = "OK", **fields) -> None:
    """记一条审计日志。

    用法：
        audit("connect", resource="COM3", baudrate=115200, result="OK")
        audit("move", resource="servo:1", target_pwm=1500, time_ms=1000)
        audit("loop_start", resource="servo:1", cycles=4, period_ms=1000)
    """
    extras = " ".join(f"{k}={v}" for k, v in fields.items())
    msg = f"action={action} resource={resource} result={result}"
    if extras:
        msg += f" {extras}"
    _audit_log.info(msg)


def health(line: str) -> None:
    """记一条 HEALTH 报告。"""
    _health_log.info("HEALTH | %s", line)


# ---------------------------------------------------------------------------
#  协议帧转储工具
# ---------------------------------------------------------------------------

def hex_dump(data: bytes, width: int = 16) -> str:
    """把字节流转成可读 HEX+ASCII 转储。

    例：
        23 30 30 30 50 31 35 30  30 54 31 30 30 30 21 0D 0A  |#000P1500T1000!..|
    """
    parts: list[str] = []
    for i in range(0, len(data), width):
        chunk = data[i: i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        parts.append(f"{i:04X}  {hex_part:<{width * 3}}  |{ascii_part}|")
    return "\n".join(parts) if parts else "(empty)"


# ---------------------------------------------------------------------------
#  自测：单独跑这个文件时打印一个示例
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log_path = setup_logging(log_dir="logs")
    get_logger("servo.main").info("Logger demo started, log=%s", log_path)
    get_logger(LOGGER_PROTOCOL).info("TX #000P1500T1000!")
    get_logger(LOGGER_PROTOCOL).info("RX #OK!")
    audit("connect", resource="COM3", baudrate=115200)
    health("uptime=12s TX=3 RX=2 err=0")
    print(f"demo ok: {log_path}")
