# 干活提示词：采集监督 Agent（BaokuanRadar Agent Supervisor）

> 版本：v1.0 定稿（2026-11-01，P1 已实现）
> 用途：交编码 Agent 执行前先过评审。实现前必须读仓库现有代码，不允许凭想象改结构。
> 定位：在现有确定性采集管线之上加一层**恢复型 Agent**——不是重写采集器，而是当固定脚本遇到"没见过/走不通"的页面状态时，由一个带工具的 LLM loop 接手做恢复决策，直到拿到可用数据或明确上报人工。

---

## 0. 背景与动机

拼多多 H5 已实测出四类"脚本失败但人有办法"的状态：空壳页（等待）、滑块验证（人工拖）、登录墙（人工登录）、滑动通过后的客户端渲染页（重载）。每遇到一种，我们就加一个 if 分支——分支会无限膨胀，且新形态出现时脚本"直接判死"。

目标：把这些「看页面 → 选动作」的决策交给 Agent loop，使系统在**红线之内**具备自愈能力。

反面案例（必须避免）：Agent 自由发挥构造请求、猜选择器硬抓、绕过验证——这些是红线，不是"智能"。

## 1. 目标与非目标

**目标（成功标准）**
- 给定一次搜索采集（如 pdd/手表），Agent 能在预算内把列表阶段推进到"拿到 ≥1 条有效候选并入库"，或给出可执行的人工升级指引
- 全过程可观测：Web 端能看到 Agent 每一步（观察了什么→调了什么工具→结果如何）
- LLM 不可用时，退化为**同一套动作表的规则引擎**，行为与用 LLM 时一致（LLM 只是更会选工具，不是唯一决策者）

**非目标（红线，写进代码）**
- 不调用/不逆向任何平台接口，不构造签名，不用 fetch/XHR 取数
- 不实现滑块/验证码绕过；滑块与登录一律 `wait_human`，人不动就不动
- 不编造数据：任何字段拿不到就是 None；Agent 不得用标题词/猜测值填充
- 不刷新同一 URL 超过预算（默认：每批每 URL ≤2 次加载，即"初次 + 失败恢复 1 次"）
- 不改变采集节奏预设（safe/balanced/fast 的间隔下限由 `pace.py` 钉死，Agent 只能加长不能缩短）

## 2. 架构总览

```
collector.run_task
   └─ pdd 列表阶段
        ├─ 确定性路径（现状）：open_search_page → 等待 → 排序 → 读 SSR → normalize
        └─ 失败信号（SSR 缺失 / 0 条 / 滑块超时 / 登录墙 / 降级 / 排序不可用）
              └─ AgentSupervisor.run(context)   ← 本模块
                    loop: observe → decide(tool) → act → observe …
                    成功 → 返回 candidates 给 collector 继续详情阶段
                    失败 → escalate：batch=paused_captcha + 人工指引
```

新增包 `bkr/agent/`：

| 文件 | 职责 |
| --- | --- |
| `observe.py` | `observe_page(page)`：把当前页面压缩成结构化状态（状态分类 + 计数 + 证据摘要） |
| `tools.py` | 工具函数（见 §4），每个工具带预算计数与审计日志 |
| `policy.py` | `decide(state, history)`：LLM 决策；超时/异常回退 `rule_decide(state)` |
| `prompts.py` | system prompt + 状态渲染模板 |
| `store.py` | SQLite `agent_run` / `agent_step` 表，记录每次决策与工具结果 |
| `runner.py` | `supervisor.run()` 主循环、预算、终止条件、escalate |
| `replay.py` | 离线回放：用冻结的页面状态序列回放规则引擎（CI 用） |

## 3. 页面状态分类（deterministic 观察器先分类，Agent 只信分类结果）

`observe_page` 输出固定字段（不交给 LLM 自由发挥）：

```python
{
  "url": str,
  "state": "ssr_ready | shell | slider | login_wall | degraded | client_rendered | risk_page | empty_results | unknown",
  "signals": {
    "has_rawdata_window": bool, "has_ssr_script": bool,
    "ssr_count": int, "dom_product_texts": int,      # innerText 中 "已拼" 次数
    "slider_text": bool, "login_text": bool, "degraded_text": bool,
    "sort_labels": list[str], "sort_clicked_ever": bool,
    "dom_image_count": int, "url_changed_after_sort": bool,
  },
  "budget": {"reloads_used": int, "reloads_left": int, "steps_left": int, "human_waits_used": int},
  "history": ["observe(ssr_ready, ssr_count=0)", "reload()", ...]   # 最近 8 步
}
```

分类规则全部可单测；`unknown` 才真正需要 LLM 判断。

## 4. 工具清单（Agent 可调用的全部动作，预算与红线下限内置）

| 工具 | 签名 | 作用 | 预算/约束 |
| --- | --- | --- | --- |
| `extract_candidates` | `(strategy)` → `{count, sample, mode}` | strategy ∈ `ssr_window / ssr_script / dom_cards / anchors`；跑对应提取器 | 不耗页面加载 |
| `reload_search` | `(reason)` → 新状态 | 重载当前搜索 URL（"无 SSR 的客户端渲染页"恢复） | 每 URL 最多 1 次恢复重载 |
| `wait_human` | `(kind, timeout_s)` → `solved/timeout` | kind ∈ `slider / login`；窗口前置 + 触摸模拟 + 轮询 | 每次 ≤180s，每批 ≤2 次 |
| `click_sort` | `(label)` → `{url_changed, dom_order_captured}` | 点击"销量"并测 URL/图序 | 每批 ≤1 次 |
| `scroll_more` | `(n)` → `{ssr_count, dom_image_count}` | 滚动触发更多加载 | n ≤ 3，每批 ≤2 次 |
| `check_login` | `()` → `{cookie_ok, page_markers}` | 双证据登录判断（PDDAccessToken + 页面登录区文案） | 只读 |
| `save_evidence` | `(tag)` → path | HTML+PNG+状态 JSON 存 `data/debug/agent_{tag}_*` | 每批 ≤2 次 |
| `finish` | `(summary)` | 正常结束，把结果交回 collector | 终态 |
| `escalate` | `(reason_code, message)` | 终态：paused_captcha + 面向用户的中文指引 | 终态 |

**Agent 不能做的事**：goto 任意新 URL（只允许通过 `reload_search`/`click_sort` 触发的同域 URL）、执行 JS、点击任意元素、改节奏、写数据库、改任务参数。

## 5. Loop 规范

- 每步：LLM 拿到 `policies` 渲染的状态 + 可用工具 schema + 最近 history，输出 **一个** JSON：`{"tool": "...", "args": {...}, "thought": "≤60字"}`
- 终止条件（满足其一）：`finish` / `escalate` / steps 用尽（默认 8）/ 总耗时 >10 分钟 / 连续两步无进展（状态哈希相同）→ 规则兜底动作或 escalate
- **规则兜底表**（LLM 超时/异常/无 key 时执行，也是回放测试对象）：

| 状态 | 首选动作 | 次选 |
| --- | --- | --- |
| ssr_ready 且 count>0 | finish | — |
| ssr_ready 且 count=0 | scroll_more(1) | escalate(empty_results) |
| shell | 等待（observe 间隔 3s ×3） | reload_search |
| client_rendered | reload_search | save_evidence + escalate |
| slider | wait_human(slider) | escalate |
| login_wall | wait_human(login) | escalate |
| risk_page | save_evidence + escalate | — |
| degraded | save_evidence + escalate | — |
| unknown | save_evidence | escalate |

- LLM 的价值：在 `unknown`、多信号冲突（如"有 SSR 但 0 条且 DOM 有商品"）、排序不可用时选更优动作；**不得**推翻红线与预算。

## 6. 提示词模板（prompts.py）

system：
```
你是爆款雷达的采集监督 Agent。你的唯一目标是：在预算和红线内，把拼多多搜索页推进到
"拿到 ≥1 条有效候选"或给出明确的人工升级理由。你只能通过工具行动，每次输出一个 JSON。
红线：不调用平台接口、不绕过滑块/登录（只能 wait_human 请人处理）、不重载超过预算、
不缩短节奏、不编造数据。拿不到数据就 escalate，绝不硬抓。
当前任务：{platform} / 关键词 {keyword} / 批次 {batch}
预算：{budget}
最近步骤：{history}
可用工具：{tools_schema}
```
user（每步注入）：
```
当前观察：{state_json}
请输出：{"tool": ..., "args": {...}, "thought": "..."}
```
要求：`thought` ≤60 字，用中文；只输出 JSON，不要 markdown。

## 7. 集成点

1. `bkr/platforms/pinduoduo.collect_pages`：确定性路径在"重载一次后仍无 SSR / 0 条"时调用 `supervisor.run(page, task, budget)`，成功则拿回列表继续；失败则抛出带 escalate 信息的 `BlockedError`
2. CLI：`python -m bkr.cli agent-collect --platform pdd --keyword 手表 [--max-steps 8] [--no-llm]`（调试用，`--no-llm` 强制规则引擎）
3. Web：SSE 新增 `type=agent` 事件（step/thought/tool/result），RunModal 增加一条"AI 接管中"时间线；不改数据表结构
4. 配置（settings.yaml 只追加）：
```yaml
agent:
  enabled: true
  model: ""            # 留空回退 llm 配置；建议用便宜快模型
  max_steps: 8
  max_reloads_per_url: 1
  max_human_wait_s: 180
  max_total_seconds: 600
```
5. 数据库：`agent_run(run_id, batch, platform, status, steps, llm_calls, started_at, finished_at, summary)` + `agent_step(run_id, idx, state_hash, tool, args_json, result_json, thought, created_at)`；只增不改

## 8. 可观测与审计

- 每次工具调用都写 `agent_step`（含输入参数与结果摘要），可在数据页/日志中检索
- 日志格式：`[agent] step=3 state=client_rendered tool=reload_search result=ssr_ready,count=20 thought=...`
- 成本：每次决策 1 次 LLM 调用；单批上限 8 次；`/api/health` 增加 `agent_enabled`

## 9. 测试与验收

**离线（CI，不联网）**
1. `observe_page` 分类器：用 `data/debug` 里真实保存的四种页面 HTML 构造 DOM fixture（或抽取出的信号字典），断言 state 判定
2. 规则兜底表：构造状态序列 → 断言动作序列（replay.py）
3. 预算：reload 超限、wait_human 超限、steps 用尽 → escalate 且消息含人工指引
4. 工具红线：断言 `goto`/`evaluate` 不在工具实现里出现（静态检查 tools.py）

**实机冒烟（必须真跑并贴日志）**
- `agent-collect --no-llm`：人为制造"滑块→通过→客户端渲染页"路径，验证 reload 恢复
- `agent-collect`（LLM 开）：同一路径，验证 LLM 步数 ≤ 规则引擎 +2，无越界动作
- Web 端：采集弹窗能看到 agent 时间线；escalate 时提示语包含"等待冷却/点击登录/继续采集"三要素

## 10. 分阶段交付

- **P1（本次）**：`bkr/agent/` 全骨架 + PDD 列表阶段接管 + 规则引擎 + CLI + 单测；Web 时间线可延后
- **P2**：SSE/前端时间线；详情阶段的接入口（详情页出现 unknown 状态时）
- **P3**：天猫/1688 接入同一 supervisor（它们的失败信号已具备）

## 11. 待讨论（需要拍板）

1. **启用时机**：A) 仅在确定性路径失败后接管（推荐，省 token、可预测）；B) 从列表页打开就全权交给 Agent（更智能但每批都花 LLM）。我倾向 A，并把"强制 AI 接管"做成一个按钮/参数，便于演示。
2. **模型**：复用 `llm` 配置还是单独配 `agent.model`？建议单独留空回退，默认用便宜快模型（决策只需分类能力）。
3. **人工等待上限**：escalate 前允许几次 wait_human？建议每批 slider ≤2 次、login ≤1 次，超了就停批冷却——防止 Agent 变成"无限等人工"。
4. **LLM 失败策略**：LLM 超时 2 次即永久切规则引擎（本批），还是每步重试？
5. **数据口径**：Agent 成功恢复的批次在报告/数据页是否打"agent 恢复"标记（便于统计自愈率）？建议打，作为面试作品的评测量化项。

---

## 12. 定稿决策（2026-11-01）

以下为评审拍板结论，实现以此为准：

1. **启用时机**：仅在确定性路径失败后接管（`collect_pages` 的"0 条"分支调用 supervisor）；不做全时段接管。调试入口 `python -m bkr.cli agent-collect`。
2. **模型**：`settings.agent.model` 留空回退 `llm` 配置；LLM 连续失败 2 次后本批永久切换规则引擎。
3. **人工等待上限**：滑块每批 ≤2 次、登录 ≤1 次（超限 escalate，防止"无限等人工"）。
4. **恢复打标**：`batch_run.agent_recovered=1` + `agent_run/agent_step` 全程留痕，用于统计自愈率。
5. **预算**：steps ≤8、每 URL 恢复重载 ≤1、总时长 ≤600s（settings.agent 可调）。

实现位置：`bkr/agent/`（observe/tools/policy/prompts/store/runner/replay）；表 `agent_run/agent_step`；
CLI `agent-collect --keyword 手表 [--no-llm]`；单测 `tests/test_agent.py`。

P2 待办：SSE/前端时间线；详情阶段接管；天猫/1688 接入同一 supervisor。
