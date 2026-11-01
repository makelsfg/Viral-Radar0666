"""采集监督 Agent：确定性路径失败后接管，用「观察 → 决策 → 工具」loop 做页面恢复。

红线（写死）：
  - 不调用平台接口、不执行任意 JS、不绕过滑块/登录；
  - 工具白名单 + 预算（重载/人工等待/步骤数），超限即 escalate；
  - 拿不到数据就上报人工，绝不编造。

P1 范围：拼多多列表阶段；详情阶段与其它平台后续接入。
"""
from .runner import agent_enabled, supervised_recover  # noqa: F401