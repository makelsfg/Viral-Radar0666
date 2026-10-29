# 爆款雷达 BaokuanRadar · 天猫品类采集

本地前后端应用：输入商品关键词和采样量，自动采集天猫搜索结果与头部商品详情（标题、到手价、已售、加购、店铺、SKU、参数、首张主图），实时进度展示，一行一商品浏览、详情查看、CSV 导出。

技术栈：Python 3.12 · FastAPI · Playwright · SQLite ／ React 18 · TypeScript · Vite · Ant Design 5 · SSE

## 快速开始（Windows）

1. 安装 Node.js 18+（仅首次构建前端需要）：https://nodejs.org
2. 双击 **setup.bat**（或 安装环境.bat）：创建 venv、安装 Python 依赖、npm install、构建前端
3. 双击 **start.bat**（或 启动.bat）→ 浏览器自动打开 `http://127.0.0.1:8765`
4. 右上角点「扫码登录」→ 在弹出的浏览器里扫码，检测到登录后自动完成
5. 在「采集」页输入关键词、搜索页数（默认 2）、详情样本数（默认 20），点开始采集
6. 完成后到「数据」页：切换品类/批次查看表格，点击行看参数、SKU、历史批次，右上角导出 CSV

以后每次使用只需要双击 **start.bat**。

> 脚本内容为纯 ASCII（避免 cmd 在 GBK 代码页下解析 UTF-8 中文批处理出错）；中文文件名 `安装环境.bat` / `启动.bat` 只是转发到对应英文脚本。

## 页面功能

- **采集**：商品关键词、搜索页数（1–10）、详情样本数（0–200）、详情顺序（页面顺序/已售优先）、可选价格带、排除词；SSE 实时进度（搜索页进度、候选数、详情进度）；触发验证/登录失效时暂停，处理完点「继续采集」
- **保守模式**：采集页右上角开关（默认开启），只采搜索结果列表、不访问任何详情页，风控最低；数据页关掉「只看已采详情」即可查看候选商品
- **数据**：品类/批次切换、主图与标题、到手价、已售、加购、店铺评分/好评率、SKU 数、商品链接；点击行打开抽屉看参数与 SKU、历史批次；支持删除品类/批次（二次确认）；「只看已采详情」开关；一键导出 CSV
- **分析**：批次概览指标、价格带/销量区间分布、店铺集中度、标题关键词、参数画像、卖点组合聚类、爆款候选 Top10；可选 AI 解读（需配置 LLM）
- **热词**：查询淘宝搜索联想词并落库回看，用于发现细分需求方向；单次查询仅一次轻量请求，建议按需使用

## 配置文件（推荐）

编辑 `config/settings.yaml`，改完重启 `start.bat` 生效：

```yaml
profile: ""                # 留空=默认账号；alt=副号 profile
server:
  port: 8765
llm:
  base_url: "https://api.deepseek.com"
  api_key: ""              # 填入你的 API Key（如 sk-...），留空则 AI 解读不可用
  model: "deepseek-chat"
```

环境变量 `BK_*` 优先级高于配置文件，适合临时覆盖。

## 分析页与 LLM 配置（可选）

「分析」页对选中批次输出：概览指标、价格带/销量分布、店铺集中度、关键词、参数画像、卖点组合、爆款候选 Top10。

AI 解读为可选项，需要 OpenAI 兼容接口（如 DeepSeek）：在启动前设置环境变量并重启服务：

```powershell
$env:BK_LLM_API_KEY="sk-..."
$env:BK_LLM_BASE_URL="https://api.deepseek.com"
$env:BK_LLM_MODEL="deepseek-chat"
.\start.bat
```

未配置时分析页照常可用，AI 卡片会显示配置指引。

## 开发模式

```bash
# 终端 1：后端（自动重载）
venv\Scripts\python.exe -m uvicorn server.main:app --reload --port 8765

# 终端 2：前端热更新（http://localhost:5173，/api 代理到 8765）
cd web
npm run dev
```

API 文档：`http://127.0.0.1:8765/docs`

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

包含采集解析（价格/销量区间）与分析计算（分布/参数/聚类）单测。

## 导出范围

- 网页「导出 CSV」跟随「只看已采详情」开关：开=仅详情行，关=含全部候选
- CLI：`python -m bkr.cli export --task ... --batch latest` 默认导出全部候选；加 `--only-detail` 仅导出详情

## 目录结构

```
baokuan-radar/
├─ bkr/                # 采集核心：Playwright、解析、评分预留、SQLite
├─ server/             # FastAPI：任务管理、SSE、数据接口、静态托管
│  ├─ main.py          #   入口（python -m server.main）
│  ├─ jobs.py          #   采集/登录线程 + 单会话锁 + 事件总线
│  └─ routes/          #   collect / data / env / events
├─ web/                # React + TS + Ant Design 5（Vite）
│  ├─ src/panels/      #   采集页、数据页、分析页
│  └─ dist/            #   构建产物（npm run build 生成，由 FastAPI 托管）
├─ legacy/             # 旧版 Streamlit 页面（不维护，依赖见 requirements-legacy.txt）
├─ config/tasks/       # 品类 YAML（命令行方式使用；网页方式按关键词动态建任务）
├─ data/               # radar.db、raw 原始数据、debug 诊断文件
├─ 安装环境.bat         # 首次安装
└─ 启动.bat            # 日常启动
```

## 采集机制与口径

- 入口：`s.taobao.com/search?q=关键词&tab=mall&sort=sale-desc`，天猫 tab、销量降序，按配置翻页
- 详情对象：默认按**页面顺序**取 Top K（下拉可切"已售优先"）
- 进入详情：在搜索结果页上模拟 hover + 点击商品卡进入（保留 Referer 与浏览历史），找不到卡片时兜底直开（仍带 Referer）
- 到手价：优先"券后价"，否则"展示价 − 明示立减"；只存一个价格字段（原始价格文本留档 `data/raw/`）
- 已售/加购：页面区间文本解析为下限整数（如 `500+` → 500）
- SKU：只采名称列表；主图：只存首张 URL；参数：参数面板键值对（偶发像真人一样跳过）
- 节奏：列表翻页 5–9 秒；详情 20–40 秒，每采 2 个休息 90–180 秒；触发滑块/访问异常/登录失效自动暂停，可在页面续采
- 采样默认：搜索 1 页 + 详情 6 个（Web 表单与 CLI 的 `config/tasks/*.yaml` 保持一致）；每日 ≤1 批，同日不连跑
- 登录态：保存在 `.browser-profile/`，失效时页面右上角重新登录

## 命令行（保留，可脚本化）

```bash
python -m bkr.cli login
python -m bkr.cli collect --task kids-bottle
python -m bkr.cli collect --task all --batch 2026-11-03_093000 --resume
python -m bkr.cli debug-search --task kids-bottle
python -m bkr.cli export --task kids-bottle --batch latest
python -m bkr.cli seed-demo        # 演示数据（不联网）
```

## 切换采集账号

**方案 A（推荐）：副号用独立 profile，主号登录态原样保留**

```powershell
cd E:\agent_培训代码库\爆款分析\baokuan-radar
$env:BK_PROFILE="alt"
.\venv\Scripts\python.exe -m bkr.cli login     # 扫码登录副号
.\start.bat                                    # 本窗口启动的服务也用 alt profile
```

- 默认 profile 在 `.browser-profile/`，`BK_PROFILE=alt` 对应 `.browser-profile-alt/`，互不干扰
- 想切回主号：新开一个 PowerShell（不设置 BK_PROFILE），直接运行 `.\start.bat`

**方案 B：清掉当前账号，在同一 profile 里换号**

```powershell
.\venv\Scripts\python.exe -m bkr.cli logout   # 先关闭所有使用该 profile 的浏览器窗口
.\venv\Scripts\python.exe -m bkr.cli login    # 扫码新账号
```

注意：同一台机器、同一个 IP 下，换号并不能绕过平台的风控关联；换号的价值是把风险隔离到备用号上，冷却期和低频采集仍然要遵守。新号登录后先正常浏览 2–3 天再开始采集，触发概率会低很多。

## 详情通道与反检测引擎

**详情通道（detail.provider）**

- `local`（默认）：在搜索结果页点击进入详情，本地采集（受风控影响）。
- `apify`：列表仍本地采；详情字段改由 Apify 托管采集补全，**本地不访问详情页**。需要在 `config/settings.yaml` 配置 `apify.token`（参考费用：全详情约 $0.004/条，6 条/天≈$0.7/月）。

**浏览器选择（browser.channel）**

- `msedge`（默认）：用本机 Microsoft Edge；`chrome`：本机 Chrome；留空：内置 Chromium
- 切换浏览器会使用独立 profile（如 `.browser-profile-msedge`），**需要重新扫码登录一次**；旧的 Chromium 登录态不受影响
- 指定浏览器不可用时会自动回退内置 Chromium（控制台有提示）

**反检测引擎（browser.engine）**

- `auto`（默认）：检测到 `patchright` 或 `rebrowser-playwright` 就用，否则回落原版 Playwright。
- 安装可选引擎：`pip install -r requirements-stealth.txt`（patchright）或 `pip install rebrowser-playwright`，然后执行一次 `patchright install chromium`（或复用已装浏览器）。
- 可强制指定：`engine: "patchright"` / `"rebrowser"` / `"playwright"`。

**CDP 接管模式（browser.cdp_url）**

先用真实 Chrome 启动调试端口并登录，再启动本服务：

```powershell
Start-Process chrome -ArgumentList '--remote-debugging-port=9222', "--user-data-dir=D:\chrome-debug-profile"
```

```yaml
browser:
  cdp_url: "http://127.0.0.1:9222"
```

此模式复用你自己的浏览器（不使用自动化启动参数），结束后不会关闭你的 Chrome。

`/api/health` 会显示当前 `engine` / `cdp` / `detail_provider`，方便确认配置是否生效。

## 常见问题

- **页面出现"访问异常提示"**：账号或 IP 被天猫风控标记。立即停止采集，**冷却 24–48 小时**（期间不要用脚本反复重试），用同一浏览器手动打开详情页确认恢复后再以更低强度继续：每天 1 批、1–2 页、5–10 个详情、间隔加大。长期方案见"合规"：网页采集只做低频，详情数据可补充淘宝客 API/第三方数据做对照。
- **启动.bat 提示未安装环境**：先双击 setup.bat（安装环境.bat 等价）
- **前端样式/功能没更新**：重新 `npm run build` 后需要重启服务（start.bat 重新打开）
- **端口被占用**：`set BK_PORT=8766` 后重新启动
- **SSE 断线**：浏览器自动重连；采集页还有 3 秒轮询兜底
- **搜索页 0 解析/批次失败**：运行 `debug-search`，把 `data/debug/` 下的 html/png 发给开发者调选择器
- **图片不显示**：天猫图片有防盗链时可能直显失败，可查看 `data/raw/` 中记录的图片 URL
- **采集暂停**：到弹出的浏览器里完成验证或重新登录，然后在采集页点「继续采集」

## 合规

仅个人研究/选品参考使用，低频、单账号；不逆向接口、不破解验证措施；不转售或公开发布采集数据。