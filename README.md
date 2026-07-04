# 舵机控制上位机 — ZL Servo Control

基于 PyQt6 的众灵（ZL）舵机控制上位机软件，配套 `protocol.md` 通信协议。

## 目录

- [功能特性](#功能特性)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [循环控制模式](#循环控制模式)
- [日志规范](#日志规范)
- [UI 主题规范](#ui-主题规范)
- [开发与测试](#开发与测试)

## 功能特性

- **基础控制**：单舵机角度 / PWM 运动、释放 / 恢复扭矩、暂停 / 继续 / 停止
- **状态读取**：当前位置、当前工作模式、固件版本
- **总线配置**：波特率选择、ID 修改、中位校准、部分 / 完全复位
- **循环控制模式** ⭐：输入**循环次数、周期时长、起始角度、终止角度**，自动按单程时长往复
- **Mock 模式**：勾选后使用 `MockTransport`，无硬件也能完整自测
- **紧急停止**：一键下发广播停止帧 `#255PDST!`
- **结构化日志**：协议流量独立记录，所有状态变化按规范 START/STOP 成对
- **可折叠日志面板**：实时显示 INFO / WARNING / ERROR 三种颜色

## 项目结构

```
Servo_ctrl/
├── protocol.md                 # 通信协议（PDF 解读产物）
├── README.md                   # 本文件
├── requirements.txt
├── refs/                       # 参考仓库（YoukoSaint/Logging_Standard_for_Agent, UI_palette）
├── logs/                       # 运行时日志（自动创建）
├── tests/
│   └── test_e2e.py             # 端到端自测（20 用例）
└── src/
    ├── main.py                 # 程序入口
    ├── protocol.py             # 协议编解码
    ├── logger.py               # 日志（按 Logging_Standard 规范）
    ├── serial_link.py          # 串口抽象 + 真实 / Mock 实现
    ├── controller.py           # 业务控制（BasicController + LoopRunner）
    ├── theme/
    │   └── color_scheme.py     # 调色板 + QSS（照搬 UI_palette）
    └── ui/
        ├── main_window.py      # 主窗口
        ├── connection_panel.py # 串口连接
        ├── servo_panel.py      # 单舵机控制
        ├── loop_panel.py       # 循环控制模式 ⭐
        └── log_panel.py        # 日志面板
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动软件
python -m src.main
# 或
python src/main.py
```

启动后：

1. **连接面板**（顶部）：
   - 选择串口（点击"刷新"扫描）或勾选"Mock 模式"自测
   - 选择波特率（默认 115200）
   - 点击"连接"

2. **基础控制** 标签页：选 ID、模式、角度、时间，点击"运动到目标角度"

3. **循环控制** 标签页：填入 4 个参数，点击"启动循环"

## 循环控制模式

### 输入参数

| 字段 | 范围 | 含义 |
|------|------|------|
| **循环次数** N | 1 – 9999 | 完整来回次数。`N=1` 单程去不回；`N` 为奇数时最后一次停在终点 |
| **周期时长** T | 1 – 60000 ms | **单程时长**（起点→终点 或 终点→起点 的耗时） |
| **起始角度** a₀ | -135° ~ +135° | 起点角度（270° 模式）或 -90° ~ 90°（180° 模式） |
| **终止角度** a₁ | -135° ~ +135° | 终点角度 |

### 工作流程

```
启动 → 送舵机到 a₀
       ↓ (等 T ms)
       送舵机到 a₁
       ↓ (等 T ms)
       送舵机到 a₀        ← 完成 1 个完整来回
       ↓
       ... 重复 N/2 次
       ↓
完成 → 状态变 Completed，进度条满格
```

### 协议映射

`a → PWM` 转换在 270° 模式下用 `pwm = 1500 + a/135 × 1000`（范围 500–2500）。
写入指令的 `T` 字段直接传 `period_ms`。

| 上位机输入 | 协议层 |
|------------|--------|
| 起始角度 a₀ | PWM `p₀ = 1500 + a₀/135 × 1000` |
| 终止角度 a₁ | PWM `p₁ = 1500 + a₁/135 × 1000` |
| 周期时长 T | 指令时间字段 `T` (ms) |
| 循环次数 N | 程序内循环 N/2 次来回 |

## 日志规范

完整参考 [`refs/logging/AGENT_LOGGING_STANDARD_zh.md`](refs/logging/AGENT_LOGGING_STANDARD_zh.md)。
本项目的实践：

- **双 Handler**：文件 (DEBUG) + 控制台 (INFO)，每次启动一个时间戳文件
- **4 个独立日志文件**：
  - `*.log` 主日志：连接/断开/错误
  - `*_protocol.log` 协议层：TX/RX 字节 + 完整帧 + HEX 转储
  - `*_audit.log` 审计：每条用户操作一行 `action=... resource=...`
  - `*_health.log` 周期：每 60s 一行 `HEALTH | ...`
- **延迟格式化**：所有日志用 `%s` 占位，禁用 f-string
- **长运行操作**：循环控制同时打 `START`（含全部参数）和 `STOP`（含耗时 / 误差 %）
- **异常**：统一 `logger.exception()` 自带 traceback

**详细使用指南见** [`docs/LOGGING.md`](docs/LOGGING.md) —— 包含日志格式说明、故障定位流程、典型信号解读。

## UI 主题规范

完整照搬 [`refs/UI_palette`](refs/UI_palette/README_zh.md)：

- **5 个基础色**（TEXT / LIGHT / DARK / LINE / BTN）+ **5 个图表色**（预留）
- 派生色 `_adjust(hex, ±N)` 内联计算（text2 / dark2 / accent / btn_hover）
- QSS 通过 `get_stylesheet()` 函数生成，每次调用重新读 ColorScheme
- 状态按钮（绿 / 红 / 蓝）用 `objectName` 硬编码，绕过动态主题
- QSS **最后**在所有控件创建后应用到 `QApplication`

颜色修改：直接 `ColorScheme.set_color("TEXT", "#ffffff")` 然后重新 `app.setStyleSheet(get_stylesheet())`。

## 开发与测试

### 运行测试

```bash
python tests/test_e2e.py
```

**结果**：20 用例，19 PASS / 1 SKIP（PyQt6 DLL 加载失败的沙箱） / 0 FAIL。

| 测试类 | 覆盖内容 |
|--------|----------|
| `TestProtocolEncode` | 7 用例：所有指令编码（运动/读取/ID/波特率） |
| `TestProtocolDecode` | 5 用例：应答解析（PWM/模式/版本/ID/写回执） |
| `TestAnglePwm` | 270° 模式角度↔PWM 换算 |
| `TestFrameParser` | 3 用例：流式解析、跨包切分、群控帧 |
| `TestLoopParams` | 3 用例：合法 / 越界 / 广播拒绝 |
| `TestMockTransportRoundtrip` | 1 用例（需 PyQt6）：Mock+LoopRunner 端到端 |

### Mock 模式自测

UI 中勾选"Mock 模式"后无需任何硬件：

1. 启动软件 → 勾选 Mock → 点击连接
2. 切到"循环控制"：填 ID=1, start=0, end=90, period=500ms, cycles=4
3. 点"启动循环"
4. 观察：进度条增长、状态变 Completed、日志面板显示 START/STOP

## 参考资料

- 协议来源：`众灵舵机使用手册-250508.pdf`（本仓库根目录）
- 日志规范：<https://github.com/YoukoSaint/Logging_Standard_for_Agent>
- UI 调色板：<https://github.com/YoukoSaint/UI_palette>

## 许可

本仓库为内部项目，参考仓库遵循各自许可。
