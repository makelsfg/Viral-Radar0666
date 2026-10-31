# 爆款雷达 · 交接说明（HANDOFF）

> 当前版本：**0.8.5**（2026-10-31）
> 项目目录：`E:\agent_培训代码库\爆款分析\baokuan-radar`
> 新会话请先读本文件 + 项目记忆 `project_baokuan_radar.md`，再继续。

## 运行方式

```powershell
cd E:\agent_培训代码库\爆款分析\baokuan-radar\web
npm run build          # 前端有改动时（0.8.5 前端有改动，必须重建）
cd ..
Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
.\start.bat
```

- 前端：React + TypeScript + Vite + Ant Design 5（`web/`）
- 后端：FastAPI（`server/`）+ Playwright/patchright（`bkr/`）+ SQLite（`data/radar.db`）
- 配置：`config/settings.yaml`（浏览器 channel=msedge，engine=auto；当前自动使用 **patchright**；detail.provider=local；LLM key 已配置）
- 版本探针：`GET /api/health` → 应显示 `0.8.5`、`engine=patchright`、`channel=msedge`
- 前端四个页签：采集 / 数据 / 分析 / 热词

## 当前流程

1. 点击开始采集 → **auth 阶段**（服务端），已授权平台跳过；未授权平台逐个弹登录窗口，成功后自动关闭；关闭/超时 → `auth_required`，前端按平台「重新登录」
2. 全部授权 → **collect 阶段**：每个平台一个 worker 子进程 + 独立 Edge profile（`.browser-profile-{tmall|1688}-msedge`），1688 错峰 75s，并行
3. 每个平台日志：`data/logs/{batch}_{platform}.log`；worker 异常会写 batch_run 并附日志路径

## 0.8.4 遗留问题：已关闭（2026-10-31 验证）

批次 `2026-10-31_214841` 两平台并行完整跑完（各详情 6/6，状态 done，无异常），日志结尾
`context closed（由程序关闭=True）`。此前"详情阶段 TargetClosedError"不再出现，**无需再 A/B engine**。

## v0.8.5 本版改动（数据质量与口径收口）

1. **1688 详情标题**：优先取 `.title-content h1`，兜底跳过公司名形态 h1（修复商品标题被"XX有限公司"覆盖）
2. **1688 已售解析**：支持「已售 8000+ 个」「成交 1.2万件」等形态，列表卡片与详情页两处生效
3. **加购/已售回写**：详情阶段的 `cart_text/cart_lb` 写入 `fact_snapshot`（爆款分的加购增量自此有数据）；已售仅在列表缺失时用详情补齐（列表值优先）
4. **1688 销量排序**：搜索 URL 追加 `sortType=va_rmdarkgmv`（成交额排序，与天猫 sale-desc 同向）。**参数值未经登录态实机验证**，首跑后确认页面排序页签是否落在"成交额/销量"，不对就改 `bkr/platforms/alibaba.py` 的 `SORT_PARAM`
5. **采集页移除「详情顺序」下拉**：销量排序下两选项收敛，UI 删除；后端 `detail_order` 保留默认 `rank`，历史任务/接口兼容
6. **1688 主图裂图**：根因是 `cbu01.alicdn.com` 对 Referer 为 `127.0.0.1:8765` 的请求返回 403；`web/index.html` 加全局 `<meta name="referrer" content="no-referrer">` 修复
7. **搜索页复用**：详情阶段先找列表阶段遗留的同 URL 搜索页（需仍有效且含商品链接），找到即复用，消除每批每平台 1 次重复列表页加载；续采场景（无现成页面）自动退回新开。日志会打印「复用已打开的搜索结果第 X 页」

注意：**加购数据只有 0.8.5 之后的新批次才有**；老批次不回填。

## 待验证 / 后续待办

- 首跑确认：1688 排序页签（SORT_PARAM）、1688 详情标题与已售、日志出现"复用已打开的搜索结果第 X 页"
- 1688 列表卡片已售格式：若列表仍缺已售但详情有，属可接受（详情会补齐），想列表也有需再校准卡片文本
- 天猫个别商品参数面板仍有键值反转（面板布局变体，低优先级）
- 拼多多移动网页实验版（UI 勾选已占位禁用）
- v0.6 跨批次趋势 / v0.7 命中率回测（数据积累后进行）
- 节奏预设 safe/balanced/fast 已可用（默认 safe，采集页选择）

## 关键代码位置

- `bkr/browser.py`：引擎选择（patchright/rebrowser/playwright）、launch/close、keep-alive、wait_for_login、风控检测
- `bkr/collector.py`：列表→详情编排、价格护栏、异常入库；`_find_open_search_page/_same_search_url` 搜索页复用
- `bkr/platforms/alibaba.py`：1688 搜索/详情适配（关键词 GBK、SORT_PARAM、详情标题/已售选择器）
- `bkr/db.py`：`set_snapshot_detail`（价格覆盖、加购更新、已售补齐）
- `bkr/auth.py`：平台授权判定（tmall cookie2+unb；1688 __cn_logon__/_cn_；读 cookie 带锁副本）
- `server/jobs.py`：auth 前置 → 并行 worker、SSE 事件、日志
- `server/routes/collect.py`：/api/collect、/api/jobs/{id}/continue、resume
- `web/src/panels/CollectPanel.tsx`：平台勾选、节奏、auth/采集进度、重新登录
- `web/index.html`：no-referrer meta（1688 图片防盗链）

## Git

- 最近提交：`0.8.5`（本文件生成后如未提交，请先 `git add -A && git commit`）
- 数据/日志/浏览器 profile 均已在 `.gitignore` 中