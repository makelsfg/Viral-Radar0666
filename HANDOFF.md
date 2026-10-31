# 爆款雷达 · 交接说明（HANDOFF）

> 当前版本：**0.12.1**（2026-11-01）
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
- 版本探针：`GET /api/health` → 应显示 `0.12.1`、`engine=patchright`、`channel=msedge`
- 前端四个页签：采集 / 数据 / 分析 / 热词

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

- **拼多多**：H5 依赖登录态与移动端布局，可能需要多轮选择器校准；若持续 0 解析或跳登录页，先看 `data/logs/{batch}_pdd.log` 与 `data/debug/`
- **排序**：天猫/1688/拼多多均改为页面点击“销量”，日志 `[排序]` 与 `[校验]` 用于核对；若平台改版导致点击失效，会在日志中告警并退化为本地按已售下限排序
- **报告口径**：三个平台“已售/成交/已拼”统计口径不同；合并报告只按平台分组，不做跨平台销量排名

## 后续待办

- 1688 / 拼多多选择器实测校准（失败时 `data/debug` 保存现场）
- 跨批次趋势 / 命中率回测（数据积累后进行）
- 节奏预设 safe/balanced/fast 已可用（默认 safe，采集页选择）

## 关键代码位置

- `bkr/browser.py`：引擎选择（patchright/rebrowser/playwright）、launch/close、keep-alive、wait_for_login、风控检测；platform=pdd 时移动端 UA/视口
- `bkr/collector.py`：列表→详情编排、价格护栏、异常入库
- `bkr/platforms/alibaba.py`：1688 搜索/详情适配（关键词 GBK 编码）
- `bkr/platforms/pinduoduo.py`：拼多多 H5 适配（实验版）
- `bkr/sort_ui.py`：页面排序点击/选中态校验（天猫销量、1688 销量/成交额、拼多多销量）
- `server/routes/data.py`：`/api/report` 合并报告、`/api/report/meta`、导出命名
- `bkr/auth.py`：平台授权判定（tmall cookie2+unb；1688 __cn_logon__/_cn_；读 cookie 带锁副本）
- `server/jobs.py`：auth 前置 → 并行 worker、SSE 事件、日志
- `server/routes/collect.py`：/api/collect、/api/jobs/{id}/continue、resume
- `web/src/panels/CollectPanel.tsx`：平台勾选、节奏、auth/采集进度、重新登录

## Git

- 最近提交：`0.8.4`（本文件生成后如未提交，请先 `git add -A && git commit`）
- 数据/日志/浏览器 profile 均已在 `.gitignore` 中