# 爆款雷达 · 交接说明（HANDOFF）

> 当前版本：**0.11.0**（2026-10-31）
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
- 版本探针：`GET /api/health` → 应显示 `0.11.0`、`engine=patchright`、`channel=msedge`
- 前端四个页签：采集 / 数据 / 分析 / 热词

## 当前流程

1. 点击开始采集 → **auth 阶段**（服务端），已授权平台跳过；未授权平台逐个弹登录窗口，成功后自动关闭；关闭/超时 → `auth_required`，前端按平台「重新登录」
2. 全部授权 → **collect 阶段**：每个平台一个 worker 子进程 + 独立 Edge profile（`.browser-profile-{tmall|1688}-msedge`），1688 错峰 75s，并行
3. 每个平台日志：`data/logs/{batch}_{platform}.log`；worker 异常会写 batch_run 并附日志路径

## 最近变更（0.9.0）

- 详情样本默认按**已售下限**排序选取（移除 UI 的"详情顺序""排除词"；后端 `detail_order` 默认 sold）
- 授权状态落库 `platform_auth`；采集期间顶部账号状态以库为准，不会因 Edge 锁 Cookies 翻红
- 1688 图片走本地代理 `/api/image`（CDN 防盗链），CSV 导出保持原始 URL

## 最近变更（0.10.0）

- 每平台仅保留一个工作标签页（列表/详情复用；详情弹窗临时关闭），移除 keep-alive/乱码标签
- 1688 销量解析兼容"成交 N 笔 / 已售 N 个 / N万+"；详情已售/加购回填快照，数据页可显示并按已售排序

## 最近变更（0.11.0）

- 淘宝/天猫忽略 URL `sort=sale-desc`：改为页面加载后真实点击“天猫 → 销量”，DOM 选中态确认，日志输出 `[排序]` 与每页前 8 个已售下限
- 1688 依次尝试点击“销量 / 成交额”页签；无法确认时打印 `[排序][警告]`
- 数据表“已售”列默认降序；详情样本仍按本地已售下限排序兜底
- 新增 `bkr/sort_ui.py`（find_text_elements / ensure_sort / ensure_mall_tab）

## 已知问题（正在验证）

**现象**：批次列表阶段成功（如 1/1、候选 30+），进入详情阶段报
`TargetClosedError: BrowserContext.new_page: Target page, context or browser has been closed`。
用户观察：浏览器显示搜索页后刷新一次就关闭。

**已做修复（0.8.4）**：
- 列表页/详情列表页不再提前 `close()`，保持到任务结束统一关闭
- keep-alive 标签保留
- `ctx.on("close")` 日志：`[browser] context closed（由程序关闭=True/False）`

**下一步验证**：
1. 运行新批次，查看平台日志与 `[browser] context closed` 一行：
   - `由程序关闭=False`：浏览器被外部/Edge 自身关闭（可能是 patchright + Edge channel 组合），把 `settings.yaml` 的 `browser.engine` 改为 `"playwright"` 做 A/B，再试
   - `由程序关闭=True`：检查 close_context 调用栈（理论上只应在任务结束时）
2. 如仍失败，把 `data/logs/{batch}_{platform}.log` 完整发开发者

## 后续待办

- 1688 选择器首轮实测校准（`bkr/platforms/alibaba.py`，失败时 `data/debug` 保存现场）
- 拼多多移动网页实验版（UI 勾选已占位禁用）
- v0.6 跨批次趋势 / v0.7 命中率回测（数据积累后进行）
- 节奏预设 safe/balanced/fast 已可用（默认 safe，采集页选择）

## 关键代码位置

- `bkr/browser.py`：引擎选择（patchright/rebrowser/playwright）、launch/close、keep-alive、wait_for_login、风控检测
- `bkr/collector.py`：列表→详情编排、价格护栏、异常入库
- `bkr/platforms/alibaba.py`：1688 搜索/详情适配（关键词 GBK 编码）
- `bkr/auth.py`：平台授权判定（tmall cookie2+unb；1688 __cn_logon__/_cn_；读 cookie 带锁副本）
- `server/jobs.py`：auth 前置 → 并行 worker、SSE 事件、日志
- `server/routes/collect.py`：/api/collect、/api/jobs/{id}/continue、resume
- `web/src/panels/CollectPanel.tsx`：平台勾选、节奏、auth/采集进度、重新登录

## Git

- 最近提交：`0.8.4`（本文件生成后如未提交，请先 `git add -A && git commit`）
- 数据/日志/浏览器 profile 均已在 `.gitignore` 中