# 爆款雷达 · 交接说明（HANDOFF）

> 当前版本：**0.17.1**（2026-11-01）
> 项目目录：`E:\agent_培训代码库\爆款分析\baokuan-radar`
> 新会话请先读本文件 + 项目记忆 `project_baokuan_radar.md`，再继续。

## 运行方式

```powershell
cd E:\agent_培训代码库\爆款分析\baokuan-radar\web
npm run build          # 前端有改动时
cd ..
Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
.\start.bat
```

- 前端：React + TypeScript + Vite + Ant Design 5（`web/`）
- 后端：FastAPI（`server/`）+ Playwright/patchright（`bkr/`）+ SQLite（`data/radar.db`）
- 配置：`config/settings.yaml`（浏览器 channel=msedge，engine=auto；当前自动使用 **patchright**；detail.provider=local；LLM key 已配置）
- 版本探针：`GET /api/health` → 应显示 `0.17.1`、`engine=patchright`、`channel=msedge`
- 前端五个页签：采集 / 数据 / 分析 / 大佬分析 / 热词

## 当前流程

1. 点击开始采集 → **auth 阶段**（服务端），已授权平台跳过；未授权平台逐个弹登录窗口，成功后自动关闭；关闭/超时 → `auth_required`，前端按平台「重新登录」
2. 全部授权 → **collect 阶段**：每个平台一个 worker 子进程 + 独立 Edge profile（`.browser-profile-{tmall|1688|pdd}-msedge`），并行；拼多多使用移动端 UA/视口
3. 每个平台日志：`data/logs/{batch}_{platform}.log`；worker 异常会写 batch_run 并附日志路径
4. 同一批次聚合为一份报告：采集页「下载报告」或数据页「合并报告」，文件名 `关键词_(平台...)_时间.csv`

## 最近变更（0.9.0）

- 详情样本默认按**已售下限**排序选取（移除 UI 的"详情顺序""排除词"；后端 `detail_order` 默认 sold）
- 授权状态落库 `platform_auth`；采集期间顶部账号状态以库为准，不会因 Edge 锁 Cookies 翻红
- 1688 图片走本地代理 `/api/image`（CDN 防盗链），CSV 导出保持原始 URL

## 最近变更（0.10.0）

- 每平台仅保留一个工作标签页（列表/详情复用；详情弹窗临时关闭），移除 keep-alive/乱码标签
- 1688 销量解析兼容"成交 N 笔 / 已售 N 个 / N万+"；详情已售/加购回填快照，数据页可显示并按已售排序

## 最近变更（0.17.1）

- 拼多多 CSR 页兜底：`extract_dom_react()` 从 React fiber props 恢复货号/标题/价格/图片（顺序=渲染顺序）；collect_pages 在 SSR 失败后先 fiber 提取再交给 Agent；agent 工具支持 strategy=dom_react
- Agent 修正：dom_cards 仅信号（ok=false）；提取成功自动 finish；无进展只统计变更页面动作；probe 打印 fiber 条数
- 已知态势：当前 pdd 会话被固定下发 CSR 页（无 SSR），fiber 提取是主路径
- 纯后端，无需 npm build

## 最近变更（0.17.0）

- **采集监督 Agent（P1）**：`bkr/agent/`（observe/tools/policy/prompts/store/runner/replay）。确定性路径失败后接管：观察分类（滑块/登录/风控/降级/SSR/客户端渲染/空结果/空壳/未知）→ LLM 或规则引擎选工具 → 循环直到拿到候选或 escalate
- 工具白名单 9 个，预算内置：滑块人工等待≤2、登录≤1、每 URL 重载≤1、steps≤8、总时长≤600s；LLM 连续失败 2 次切规则引擎；所有步骤落 `agent_run/agent_step`，恢复成功批次打 `agent_recovered=1`
- CLI：`python -m bkr.cli agent-collect --keyword 手表 [--no-llm]`；配置 `settings.agent`；`/api/health` 增 `agent_enabled`
- 规格：`docs/PROMPT_采集监督Agent.md`（v1.0）；单测 `tests/test_agent.py`。纯后端，无需 npm build

## 最近变更（0.16.6）

- 滑块通过后恢复：实测通过滑块后拼多多返回客户端渲染页（无 SSR 原文），导致 0 条；现自动重载搜索页一次取服务端渲染版（collect 与 probe 均生效，每批最多一次）
- 纯后端改动，无需 npm build

## 最近变更（0.16.5）

- 修复拼多多滑块“拖不动”：移动端仿真下滑块只响应 touch 事件；新增 CDP `Emulation.setEmitTouchEventsForMouse`（人工接管/登录窗口启用），鼠标拖动=触摸拖动
- pdd-probe 遇到滑块进入人工等待并从通过后继续（原版直接退出）
- 纯后端改动，无需 npm build

## 最近变更（0.16.4）

- 拼多多滑块人工接管：检测到滑块（"拼多多安全验证/拖动滑块完成拼图"）时窗口前置并等待人工拖动，最多 3 分钟，通过后自动继续；超时才暂停批次
- 拼多多销量排序：搜索页点击“销量”（ensure_sort）；URL 变排序页则重开一次取销量序 SSR，否则按 DOM 图片渲染顺序重排 SSR 候选（匹配 ≥40% 才采用），不行保持综合序并警告；probe 输出排序测量
- 本版有前端改动（RunModal 滑块提示），需 npm run build

## 最近变更（0.16.3）

- 修复拼多多登录误判：匿名访问会下发 `pdd_user_id/pdd_user_uin`，原“任一 cookie=已登录”导致登录窗口秒关、旧 state 里的过期 token 让列表能跑但点详情被踢回登录页。现在**只认 `PDDAccessToken`**，且登录等待采用基线对比：必须相对打开登录页时新出现/已刷新才算真实登录；CLI `login --site pdd` 同步该判定
- 拼多多登录提示改为手机号验证码并指向右上角「登录拼多多」；登录/待处理提示按平台区分（不再显示 tmall 的 cookie2+unb）
- 注意：本版有前端改动，需 `npm run build` 后重启

## 最近变更（0.16.2）

- 修复第二次实机 0 解析：拼多多前端水合后会删除 `window.rawData`，但 SSR 原文仍在内联 `<script>` 里；`JS_SSR_LIST` 增加 script 括号配平提取通道（mode=script），已用真实页面离线验证 20 条字段映射
- 等待条件加入「页面已渲染出含已拼商品卡」；不再点击销量页签（H5 排序走 XHR，SSR 不跟随，点击只多一次请求），拼多多统一默认排序口径

## 最近变更（0.16.1）

- 修复拼多多首次实机 0 解析：搜索页是**空壳先行、JS 延后注入**（实测 ~10s），原版 2.5s 就抢读。现轮询等待 `window.rawData`/商品锚点（≤20s，纯等待不发请求），SSR 读取 3 次重试；排序后比对 SSR 首商品 goodsID 并打日志（SSR 可能不跟随排序）
- 实测 SSR `salesTip` 全部是店铺累计口径（本店已拼/全店总售），列表 `sold_lb` 统一留空，单品「已拼」以详情页为准；`parse_pdd_sold`/`_SHOP_SOLD_RE` 已排除店铺口径

## 最近变更（0.16.0）

- **拼多多改回网页 H5 采集，删除官方开放平台（多多进宝）API 通道**（`pdd_ddk.py` 已删）
- 重写 `bkr/platforms/pinduoduo.py`：搜索优先读已渲染 SSR（`window.rawData…ssrListData.list`），失败退回锚点兜底并告警；详情零宽清洗、遮挡价判缺失、单品已拼与「本店已拼」分离、评价数/发货地/服务解析、App 降级页识别
- 硬约束：搜索无限滚动固定 1 屏；单批详情 ≤10（`settings.pdd.detail_batch` 可调低），超出暂停 `paused_captcha` 冷却续采；遇滑块/登录/访问异常/降级立即停批，不重试不绕过
- 新增实机校准命令：`python -m bkr.cli pdd-probe --keyword 手表`（打印 SSR keys/条数/滚动前后对比/样例，不写库，现场存 `data/debug/`）
- auth 恢复拼多多登录 cookie 判定（保留 0.12.1 关窗复核修复）；采集页拼多多启用，顶部改为「登录拼多多」按钮

## 最近变更（0.15.0）

- 新增「大佬分析」双 Agent：`bkr/expert/`（evidence/tools/prompts/graph/store/service）
- LangGraph 状态机：证据 → 大佬写稿 → 审查 → 打回重写（最多 3 轮）→ 通过 / needs_human
- 审查严格 JSON（五维评分 + blocker + fix），代码侧再校验通过硬条件
- 新表 `expert_run` / `expert_round`；API `/api/expert/runs`、cancel/delete/export；SSE `type=expert`
- 前端新增「大佬分析」页签：批次多选、上传报告、笔记、轮次时间线、Markdown 报告、下载
- 两个模型分别配置：`settings.yaml → expert.writer / expert.reviewer`（留空回退 `llm`）
- 依赖新增 langchain / langchain-openai / langgraph / rank-bm25；前端新增 react-markdown / remark-gfm

## 最近变更（0.14.0）

- 拼多多放弃 H5 网页采集（安全验证滑块无法稳定通过），改官方开放平台（多多进宝）API
- 新增 `bkr/providers/pdd_ddk.py`：MD5 签名、`pdd.ddk.goods.search`、`pdd.ddk.goods.detail`、字段映射
- 配置 `settings.yaml → pdd.client_id/client_secret/pid`；未配置时拼多多 auth 直接给出配置提示，不弹浏览器
- collector：pdd 平台走 `_run_pdd_api`，不启动浏览器；报告/数据页无需改动
- `/api/env` 增加 `pdd_api`，顶部显示「拼多多 API 已配置/未配置 API」

## 最近变更（0.13.0）

- 修复新平台首次运行报“任务不存在 / worker 提前退出”：`start_run` 先 upsert 全部任务到 `dim_task`，worker 才能按 task_id 加载
- 采集运行弹窗（`web/src/components/RunModal.tsx`）：魔方动画 + 平台进度；× 触发取消
- 取消：`POST /api/jobs/{id}/cancel`，taskkill /T 结束 worker 进程树、清理各平台 profile 的 Edge 进程；任务置 cancelled
- 不保存：`POST /api/jobs/{id}/discard` 删除该批次快照/批次记录并清理孤儿商品与 raw 文件
- 常见报错原因与建议：`RunModal.explainError`，同时用于采集页错误提示
- 平台启动默认错峰 75s→20s（`BK_PARALLEL_STAGGER` 可调）

## 最近变更（0.12.1）

- 修复拼多多登录误判：登录成功后 H5 可能仍停在 `login.html`，此前 `on_login` 条件导致永远判失败。现在 PDD 以 `PDDAccessToken/pdd_user_id/pdd_user_uin` cookie 为准；关窗后 `_login_platform` 再读一次落盘 cookie 复核
- 登录文案改为“完成登录”（拼多多是手机号验证码，不是扫码）

## 最近变更（0.12.0）

- 拼多多实验版：`bkr/platforms/pinduoduo.py`（H5 搜索/详情、移动 UA），auth/env/UI 全面接入；登录 cookie `pdd_user_id / pdd_user_uin / PDDAccessToken`
- 跨平台合并报告：`GET /api/report?batch=`（全部候选）与 `/api/report/meta`；列不变（含平台列），文件名 `关键词_(天猫+1688+拼多多)_时间.csv`
- 采集页完成后显示报告摘要 + 下载；数据页新增「合并报告」按钮
- 图片代理扩展到拼多多 CDN（pddpic/yangkeduo/pinduoduo）

## 最近变更（0.11.0）

- 淘宝/天猫忽略 URL `sort=sale-desc`：改为页面加载后真实点击“天猫 → 销量”，DOM 选中态确认，日志输出 `[排序]` 与每页前 8 个已售下限
- 1688 依次尝试点击“销量 / 成交额”页签；无法确认时打印 `[排序][警告]`
- 数据表“已售”列默认降序；详情样本仍按本地已售下限排序兜底
- 新增 `bkr/sort_ui.py`（find_text_elements / ensure_sort / ensure_mall_tab）

## 已知限制 / 待验证

- **拼多多**：H5 依赖登录态与移动端布局；SSR 路径/字段、详情选择器仍需实机确认（先跑 `python -m bkr.cli pdd-probe`）。若解析 0 条或跳登录页，先看 `data/logs/{batch}_pdd.log` 与 `data/debug/`
- **拼多多节奏**：单批 ≤10 详情；与天猫/1688 并行 worker 时同机双 Edge 同时活动，滑块概率理论上更高，建议先单独跑 pdd 批次观察 2–3 批
- **排序**：天猫/1688/拼多多均改为页面点击“销量”，日志 `[排序]` 与 `[校验]` 用于核对；若平台改版导致点击失效，会在日志中告警并退化为本地按已售下限排序
- **报告口径**：三个平台“已售/成交/已拼”统计口径不同；合并报告只按平台分组，不做跨平台销量排名

## 后续待办

- 拼多多实机校准：先 `pdd-probe`，首跑确认 SSR 字段/条数与详情解析（失败时 `data/debug` 保存现场）
- 1688 详情选择器与排序页签实测复核（失败时 `data/debug` 保存现场）
- 跨批次趋势 / 命中率回测（数据积累后进行）
- 节奏预设 safe/balanced/fast 已可用（默认 safe，采集页选择）

## 关键代码位置

- `bkr/browser.py`：引擎选择（patchright/rebrowser/playwright）、launch/close、keep-alive、wait_for_login、风控检测；platform=pdd 时移动端 UA/视口
- `bkr/collector.py`：列表→详情编排、价格护栏、异常入库
- `bkr/platforms/alibaba.py`：1688 搜索/详情适配（关键词 GBK 编码）
- `bkr/platforms/pinduoduo.py`：拼多多 H5 适配（SSR 列表/详情解析/降级与滑块识别；`probe()` 实机校准）
- `bkr/sort_ui.py`：页面排序点击/选中态校验（天猫销量、1688 销量/成交额、拼多多销量）
- `server/routes/data.py`：`/api/report` 合并报告、`/api/report/meta`、导出命名
- `bkr/expert/`：大佬分析（evidence/tools/prompts/graph/store/service）；`server/routes/expert.py`；`web/src/panels/ExpertPanel.tsx`
- `bkr/auth.py`：平台授权判定（tmall cookie2+unb；1688 __cn_logon__/_cn_；读 cookie 带锁副本）
- `server/jobs.py`：auth 前置 → 并行 worker、SSE 事件、日志
- `server/routes/collect.py`：/api/collect、/api/jobs/{id}/continue、resume
- `web/src/panels/CollectPanel.tsx`：平台勾选、节奏、auth/采集进度、重新登录

## Git

- 最近提交：`0.17.1`（本文件生成后如未提交，请先 `git add -A && git commit`）
- 数据/日志/浏览器 profile 均已在 `.gitignore` 中