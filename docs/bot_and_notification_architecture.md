# 智能盯盘与报告推送系统架构 (Bot & Notification System)

## 1. 系统概述

本模块旨在将量化终端从 **“被动查询”** 升级为 **“主动触达”**，构建一个自动化、高可用的市场监控生态。

系统由三个核心子系统组成：

1. **多渠道消息触达层 (Notification Routing Layer)**
   高可扩展的消息网关，支持策略化接入。
   当前首选 **飞书 (Feishu)**，架构上支持未来无缝接入：

   * Telegram
   * Email
   * 钉钉

2. **定时市场报告引擎 (Scheduled Daily Reporter)**
   基于美股交易关键时间节点（开盘 / 收盘），自动聚合行情与异动数据，并通过 LLM 生成专业研报。

3. **实时盯盘守护进程 (Real-time WebSocket Monitor)**
   长连接实时行情数据流，通过内存级滑动窗口检测短线剧烈波动，实现毫秒级预警。

---

# 2. 目录结构扩展规划

基于现有项目结构，新增以下文件以实现业务解耦：

```plaintext
.
├── core/
│   ├── config.py
│   └── scheduler.py            # [新增] APScheduler 生命周期管理
│
├── services/
│   ├── notifications/          # [新增] 触达层独立包
│   │   ├── __init__.py
│   │   ├── base.py             # [新增] 抽象基类定义 (Interface)
│   │   ├── feishu_bot.py       # [新增] 飞书具体发送逻辑
│   │   └── manager.py          # [新增] 消息路由网关 (NotificationManager)
│   │
│   ├── daily_reporter.py       # [新增] 定时报告业务逻辑封装
│   └── ws_monitor.py           # [新增] WebSocket 盯盘核心守护进程
│
└── main.py                     # [修改] 注入 Scheduler 和 WebSocket 背景任务
```

---

# 3. 核心模块设计规范

## 3.1 多渠道消息触达层 (Notification Routing)

### 设计模式

采用：

* **Strategy（策略模式）**
* **Factory（工厂模式）**

实现多渠道通知的可扩展架构。

---

### 抽象基类 (base.py)

定义统一接口：

```python
class BaseNotifier:
    async def send(self, title: str, content: str, **kwargs) -> bool:
        ...
```

所有通知渠道必须实现该接口。

---

### 飞书实现 (feishu_bot.py)

功能：

* 继承 `BaseNotifier`
* 解析 `.env` 中的 `FEISHU_WEBHOOK_URL`
* 将 Markdown 转换为飞书富文本 JSON
* 通过异步 HTTP 请求发送消息

---

### 统一路由 (manager.py)

封装统一网关：

```python
await NotificationManager.broadcast(
    title,
    content,
    channels=["feishu"]
)
```

职责：

* 统一管理通知渠道
* 支持多渠道并发发送
* 未来扩展无需修改业务代码

---

# 3.2 定时市场报告引擎 (Daily Reporter)

## 时间调度 (scheduler.py)

使用：

* `AsyncIOScheduler`
* 强制锁定时区：`America/New_York`

避免美股夏令时 / 冬令时问题。

### 调度任务

| 任务名称 | 触发时间 (EST) | 核心逻辑                                           |
| ---- | ---------- | ---------------------------------------------- |
| 开盘速递 | 09:35      | 获取 SPY / QQQ 跳空幅度 + 异动股，并由 AI 生成「对冲基金晨会纪要」风格简报 |
| 收盘总结 | 16:05      | 聚合全天涨跌幅、成交量及板块表现，生成深度盘后总结                      |

---

# 3.3 实时盯盘守护进程 (WebSocket Monitor)

## 生命周期管理

与 FastAPI 的 `lifespan` 绑定：

```python
asyncio.create_task(start_ws_monitor())
```

特性：

* 持续长连接
* 自动重连（Auto-Reconnect）
* 后台守护进程运行

---

## 核心算法设计

### 内存滑动窗口

```python
price_window = {
    "AAPL.US": deque(maxlen=300)
}
```

说明：

* 存储近 **5 分钟秒级数据**
* 内存级计算
* 低延迟检测

---

### 异动触发逻辑

```python
abs(change_pct) > 1.5%
```

条件：

* 对比当前价格与 N 分钟前价格
* 若超过阈值，则触发预警通知

---

### 防骚扰机制 (Cooldown)

规则：

* 单只股票触发报警后
* 进入 **15 分钟冷却期**
* 避免高频震荡导致消息轰炸

---

# 4. 技术栈依赖 (Dependencies)

| 依赖库         | 作用                  |
| ----------- | ------------------- |
| websockets  | 高性能处理实时行情 WebSocket |
| apscheduler | 轻量级 Cron 任务调度       |
| httpx       | 异步发送 Webhook 请求     |
| pytz        | 解决美股夏令时 / 冬令时切换问题   |

---

# 5. 系统设计目标

* 高可用（High Availability）
* 低延迟（Low Latency）
* 可扩展（Extensible Architecture）
* 自动化投研（Automated Research Workflow）

---

# 6. 未来扩展方向

计划支持：

* Telegram Bot
* Email 报告订阅
* 钉钉机器人
* Slack
* 多策略组合监控
* 多市场支持（港股 / 加密）

---
