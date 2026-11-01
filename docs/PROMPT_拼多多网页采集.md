# 干活提示词：在 baokuan-radar 中实现拼多多网页（H5）采集

> 版本：v0.1（2026-11-01）
> 用途：交给编码 Agent 直接执行。执行前必须先读仓库现有代码与真实页面，不许凭想象改结构、不许猜字段名。
> 来源：由一份运行良好的 WorkBuddy 拼多多采集提示词迁移而来，保留其防封策略，改造成程序代码。

---

## 0. 你的任务

在仓库 `E:\agent_培训代码库\爆款分析\baokuan-radar` 中，把拼多多网页版（`mobile.yangkeduo.com`）的采集流程真正实现并接入现有采集管线：

- 拼多多默认走**网页采集**（移动端 H5，独立 profile、移动 UA）；
- 保留现有官方开放平台（多多进宝）通道作为可选模式，不删除 `bkr/providers/pdd_ddk.py`；
- 采集结果与天猫 / 1688 使用同一套字段、同一张表、同一份报告，不新增平台专属字段；
- 第一优先级是**不被风控**，宁可少抓、报缺，也不能触发滑块、登录拦截或会话降级；
- 抓不到的字段一律留空（`None` / `"未显示"` 只用于日志与报告说明），禁止编造、禁止用标题词冒充参数。

技术栈固定：Python 3.12 + Playwright（可能运行在 patchright / rebrowser 引擎）+ SQLite + FastAPI + React 18。

---

## 1. 开始前必须阅读

- `HANDOFF.md`、`docs/PRD.md`、`README.md`（注意：三处均写着“拼多多放弃 H5 网页采集”，本任务要改这个结论）
- `bkr/platforms/pinduoduo.py`（现有 H5 实验版，核心假设是错的，见第 2 节）
- `bkr/platforms/alibaba.py`（1688 适配器的接口范式：`collect_pages` / `open_search_page` / `click_and_extract`）
- `bkr/collector.py`（平台分发、断点续采、详情节奏、批次状态）
- `bkr/list_collector.py`、`bkr/detail_collector.py`（天猫的字段契约与诊断落盘）
- `bkr/browser.py`（移动 UA、单工作标签页、`check_blocked`、`BlockedError` / `LoginError` / `EmptyResultError`）
- `bkr/pace.py`、`bkr/parse.py`、`bkr/auth.py`、`bkr/config.py`
- `server/jobs.py`（`_login_platform`、`_ensure_state`、平台状态聚合）
- `server/routes/env.py`、`web/src/panels/CollectPanel.tsx`
- `config/settings.yaml`（**只追加配置，不许覆盖已有 key**）

---

## 2. 现状与必须修正的差距

1. **分发层没有走网页流程**：`bkr/collector.py` 的 `run_task` 里 `if task.platform == "pdd": _run_pdd_api(...); return`，浏览器分支永远到不了。`bkr/platforms/pinduoduo.py` 实际是死代码。
2. **列表页抓取假设错误**：现有 `JS_CARDS` 用 `a[href*="goods.html"]` 找商品卡。拼多多 H5 的商品卡不是 `<a>` 标签，正文还插入了零宽字符做混淆。继续用锚点会得到 0 条。
3. **正确数据源是 SSR**：搜索页已渲染的数据在 `window.rawData.stores.store.data.ssrListData.list`，只读本地对象、不产生网络请求，符合“禁止调用 pdd 接口”的约束。
4. **价格单位与口径未处理**：SSR 价格字段单位是“分”，必须 `/100`；详情页还可能出现 `¥3.?` 这种被遮挡价，必须判为缺失，不能解析成 `3`。
5. **销量口径混用风险**：详情页同时有单品的“已拼X件”和店铺累计的“本店已拼X件”。现有代码把所有 `已拼` 都写进 `sold_lb`，会把店铺累计销量当成单品销量。
6. **风控/降级识别缺失**：`check_blocked` 只覆盖淘宝/1688 的标记，没有拼多多的滑块文案、App 打开提示、“前往APP查看价格”降级页。
7. **零宽字符未清洗**：详情页文本提取前必须删除 `\u200b`，否则店铺名、参数、销量正则都会失配。
8. **登录与 UI 假设过时**：`auth.py`、`server/jobs.py`、`server/routes/env.py`、`CollectPanel.tsx` 都把 pdd 当作“已配置 API 即已授权”，网页模式下需要恢复浏览器登录流程。

---

## 3. 从原提示词迁移过来的硬约束（写进代码，不是写进注释）

1. **动作白名单**：只允许页面导航、滚动、读取已渲染 DOM / 已渲染 SSR 数据。
   - 禁止调用任何拼多多接口、`fetch` / XHR、构造签名参数、复用多多进宝接口做网页采集。
   - 禁止并发打开多个商品详情页；同一时刻只有一个详情页在加载。
2. **同一 URL 只访问一次**：同一批次内不刷新、不重试同一搜索页或详情页 URL；补数据靠换关键词或下一批次。
3. **搜索页**：每个关键词只开 1 页；需要更多候选时换关键词，用 `goods_id` 去重，不靠翻页硬刷。
4. **详情页节奏**（页面内动作的固定参数，写成模块常量）：
   - 导航后先等 `2.4–2.6s`；
   - 向下滚动最多 5 次，每次 `700–800px`，间隔 `0.6s`；
   - 页面之间沿用项目 `get_pace(task.pace)`，不得低于既有预设（safe 为 20–40 秒，且周期性长休息）。
5. **单批上限**：拼多多网页模式每批最多采集 **10 个详情**；超过则暂停本批，提示用户至少 10 分钟后 `--resume`，不得在同一批里继续硬抓。
6. **命中以下任一情况立即停止本批，绝不重试、绝不绕过**：
   - 滑块验证（“请拖动下方滑块完成验证”等）；
   - 跳转登录页 / “扫码登录” / “请用手机浏览器扫码在拼多多App打开”；
   - “访问异常”“系统繁忙”等提示；
   - 降级页：详情页出现“前往APP查看价格”，或同时找不到店铺名与评价区。
7. **截图与识图不进入代码流程**：原提示词靠视觉识别，本项目用 DOM 文本提取。截图只作为 0 解析/异常时的诊断产物（复用 `_dump_search_page` 的思路），不做 OCR、不引入视觉模型。

---

## 4. 数据契约

### 4.1 搜索页：只读 SSR 数据

搜索入口：

```text
https://mobile.yangkeduo.com/search_result.html?search_key=<URL 编码关键词>
```

在页面加载并滚动后，用 `page.evaluate` 读取已渲染的 SSR 数据，禁止发请求。首选路径：

```js
() => {
  const strip = (s) => String(s || '').replace(/\u200b/g, '');
  const list = window.rawData?.stores?.store?.data?.ssrListData?.list;
  if (!Array.isArray(list)) return [];
  return list.map((it) => ({
    goodsID: strip(it.goodsID ?? it.goods_id ?? it.goodsId ?? ''),
    goodsName: strip(it.goodsName ?? it.goods_name ?? ''),
    priceCents: it.price ?? it.min_group_price ?? null,
    salesTip: strip(it.salesTip ?? it.sales_tip ?? ''),
    img: strip(it.hdThumbUrl ?? it.thumbUrl ?? it.image_url ?? ''),
  }));
}
```

要求：

- 首次实机校准时，打印一次 `Object.keys(list[0])`（只打 key 名，不打整条数据），确认真实字段名后再固化映射；若首选路径不存在，打印 `Object.keys(window.rawData.stores.store.data)` 排查，**不要提交靠猜测的路径**。
- `goodsID` 最终统一为字符串；详情链接固定为 `https://mobile.yangkeduo.com/goods.html?goods_id=<goodsID>`。
- `priceCents` 必须是整数“分”，统一 `price_yuan = round(priceCents / 100, 2)`；缺失、为 0、明显异常（例如大于 1 亿分）时记 `None`，并在日志打一条可疑值，不写进候选价。
- `salesTip` 交给 `bkr/parse.py` 的 `parse_sold_text`（已支持“已拼 X 件”“已拼 1.2 万件”），卡片仍写入 `sold_text` / `sold_lb`。
- 图片以 `//` 开头时补 `https:`，并把 `pddpic.com` / `yangkeduo.com` 的 `http://` 升级为 `https://`（沿用现有 `_fix_img`）。
- 找不到 SSR 列表时：保存诊断现场并抛 `EmptyResultError`，消息里写明“可能未登录 / 风控 / 改版”。如果页面上确实存在 `goods.html` 锚点，可作为兜底解析，但必须与 SSR 走同一套 `normalize_card`，不得两套字段口径。

### 4.2 搜索卡片归一化结果（必须与天猫/1688 一致）

```python
{
  "item_id": str,          # goods_id
  "title": str,
  "price_text": "¥xx.xx" | None,
  "price_final": float | None,   # 可选，卡片阶段可为 None
  "sold_text": str | None,
  "sold_lb": int | None,
  "shop_name": None,       # 搜索卡片通常没有店铺名，不推断
  "image_url": str,
  "url": "https://mobile.yangkeduo.com/goods.html?goods_id=...",
  "page_no": int,
}
```

沿用现有过滤规则：`exclude_words` 命中标题即丢弃；`price_min` / `price_max` 按元过滤；`item_id` 为空丢弃；跨关键词按 `goods_id` 去重，`rank` 按最终顺序重排。

### 4.3 详情页字段映射

详情页入口：

```text
https://mobile.yangkeduo.com/goods.html?goods_id=<id>
```

返回给 `collector` 的字典必须包含现有全部键：

```python
{
  "item_id", "title", "price_final", "sold_text", "sold_lb",
  "cart_text", "cart_lb", "shop_name", "shop_rating", "positive_rate",
  "sku_names", "params", "image_url", "video_url", "product_url", "_html",
}
```

逐字段规则：

| 项目字段 | 拼多多来源与规则 |
| --- | --- |
| `title` | 详情页标题元素 / `document.title` 去后缀；空则视为异常 |
| `price_final` | 页面 `¥` 后完整数字；显示“券后价”则取券后；被遮挡的 `¥3.?`、缺数字 → `None`；同时把价格口径写进 `params["价格口径"]`（拼单价 / 券后价 / 未知） |
| `price_text` | 卡片阶段保存原始展示价，详情阶段不覆盖 |
| `sold_text` / `sold_lb` | **只取单品“已拼X件”**；用正则排除紧邻“本店已拼”的片段 |
| `cart_text` / `cart_lb` | 拼多多 H5 无加购数，固定 `None`，不得用销量冒充 |
| `shop_name` | “本店已拼…”上方紧邻的非空文本行；长度 2–30、不含价格/销量词；找不到就 `None`，不从标题猜 |
| `shop_rating` | 无明确评分则 `None` |
| `positive_rate` | 无明确好评率则 `None` |
| `params["店铺已拼"]` | “本店已拼X件”原文，**不得写入 `sold_lb`** |
| `params["评价数"]` | `商品评价(16,523)` → 去逗号后的整数或原文 |
| `params["评价标签"]` | “该商品所属店铺评价”下方标签，去重后以 `、` 连接 |
| `params["发货地"]` | `发货地` 后的省级地名；优先截到 `省/市/自治区` 结尾，取不到则保留原文，不补全 |
| `params["服务"]` | 48小时发货、7天无理由、假一赔十、保证金、包邮等命中的原文，去重连接 |
| `params[其他参数]` | “商品详情”参数栏的通用键值对（如保护套质地、款式、适用型号、风格）。跨品类通用，**不得硬编码只适配手机壳** |
| `sku_names` | 只收当前页面已渲染的规格文本；拼多多规格常需点击弹层，本流程禁止点击，拿不到就给 `[]` |
| `image_url` | 优先图集/主图，过滤 logo/icon/avatar；`//` 补协议，pdd CDN 强制 https |
| `video_url` | 页面存在 `video` 时取其 src，否则空串 |
| `product_url` | 固定 goods 详情链接 |
| `_html` | `save_html=True` 时保存页面 HTML，否则 `None` |

### 4.4 文本清洗与正则要求

- 所有 `innerText` 先做 `replace("\u200b", "")`，再做正则。
- “已拼”正则单独写，不要复用店铺累计：先移除 `本店已拼\d+...` 片段，再匹配单品。
- 价格正则必须要求完整数字，匹配后检查紧随字符不是 `?`；`¥3.?` 一律 `None`。
- 评价数允许千分位逗号；标签按行切分，过滤“好评”“查看全部”等按钮词。
- 拿不到的字段返回 `None` / `[]` / `""`，日志统一写“未显示”，不得用 0 或标题词填充。

---

## 5. 采集流程

### A. 搜索页

1. 关键词 URL 编码，逐关键词打开搜索页；`effective_pages = min(task.pages, len(task.keywords))`，超出时打印警告并跳过重复 URL。
2. 页面加载后等 `2.4–2.6s`，截图/诊断仅在异常时执行；尝试 `ensure_sort(page, ("销量", "已拼"))`，找不到排序控件只打印警告，不阻断流程，后续由 `detail_order=sold` 按已售下限选详情。
3. 滚动 3–5 屏，每屏 700–800px、间隔约 0.6s；滚动后读取 SSR 列表。
4. 0 条时调用 `_dump_search_page` 保存 HTML + PNG + 统计，抛 `EmptyResultError`。
5. 关键词之间按 `get_pace(task.pace)` 的 `list_lo/list_hi` 等待。

### B. 详情页

1. 一次只开一个详情页：沿用现有“临时标签页 + `referer`，采完关闭”的范式；不要并发。
2. 详情阶段**不要重新打开搜索页**：列表阶段的搜索页保留在工作标签页，直接作为 referer；为此在适配器暴露 `DIRECT_DETAIL = True`，`collector` 对拼多多跳过 `open_search_page_fn` 的重复导航（见第 6 节）。
3. 打开后等 `2.4–2.6s`，执行风控/降级检查，再按固定节奏滚动最多 5 次。
4. 读取清洗后的页面文本与 DOM，先检查降级，再解析字段。
5. 页面间等待使用 `get_pace(task.pace)`；不得把现有的 20–40 秒安全间隔改小。
6. 当前批次详情数达到 10 个且仍有候选未采：保存已采数据，设置批次为 `paused_captcha`，`error` 写明“拼多多单批上限 10 个详情，请至少 10 分钟后执行 --resume 续采”，然后正常退出，不标 `done`。

### C. 异常与降级

在 `bkr/platforms/pinduoduo.py` 新增平台专属检查（不要改天猫/1688 的判定逻辑）：

```python
class PddDegradedError(BlockedError):
    """会话降级：页面仍可打开，但关键数据不再返回，需停止本批。"""
```

- 滑块 / 验证 → `BlockedError`，提示人工完成验证后 `--resume`。
- 登录页 / 扫码 / 要求 App 打开 → `LoginError`，提示执行 `python -m bkr.cli login --site pdd`。
- 访问异常 / 系统繁忙 → `BlockedError`。
- 降级（“前往APP查看价格”，或店铺名与评价区同时缺失）→ `PddDegradedError`，本条不入库，保留已采条目，暂停批次。
- 任何异常都不得自动重试、刷新同一 URL、切换入口绕过、或调用接口补数。

### D. 节奏与批次常量

放在适配器模块顶部，便于实机调参：

```python
PAGE_SETTLE_MS = (2400, 2600)
SCROLL_STEP_PX = (700, 800)
SCROLL_INTERVAL_MS = 600
MAX_SCROLLS = 5
MAX_DETAILS_PER_BATCH = 10      # 可被 settings.pdd.detail_batch 覆盖，但上限仍是 10
BATCH_COOLDOWN_MIN = 10
```

---

## 6. 集成改造清单

1. `bkr/config.py`
   - 读取 `settings.pdd.mode`（`web` | `api`），写入 `BK_PDD_MODE`，默认 `web`；
   - 读取 `settings.pdd.detail_batch`，写入 `BK_PDD_DETAIL_BATCH`，默认 10，代码内再 `min(..., 10)`；
   - 只追加，不改已有 `client_id/client_secret/pid` 映射。
2. `bkr/auth.py`
   - `is_authorized` / `probe_authorized`：`pdd` 且 `mode=web` 时用 `has_pdd_login_cookies(cookie_names("pdd"))`；`mode=api` 时才用 `pdd_ddk.configured()`；
   - 注释同步修改，不要保留“pdd 不再依赖 H5 登录”的旧说法。
3. `bkr/collector.py`
   - `run_task`：仅当 `task.platform == "pdd"` 且 `mode=api` 时才走 `_run_pdd_api`；否则进入浏览器分支；
   - `_platform_adapters` 返回适配器是否 `DIRECT_DETAIL`；拼多多详情阶段跳过重复 `open_search_page`，用当前工作页作为 referer；
   - 拼多多网页模式按 `MAX_DETAILS_PER_BATCH` 截断本批 `pending`，剩余候选时暂停而不是标 `done`；
   - 保留现有 `_price_guard`：详情价缺失或与列表价偏离过大时回退列表价。
4. `server/jobs.py`
   - `_login_platform`：pdd 在 `web` 模式走真实浏览器登录（与 1688 一致），仅 `api` 模式返回 `pdd_ddk.configured()`；
   - `_ensure_state`：仅 `api` 模式跳过 pdd，`web` 模式正常导出登录态。
5. `server/routes/env.py`
   - 保留 `pdd_api`，新增 `pdd_mode`，供前端展示。
6. `web/src/panels/CollectPanel.tsx`
   - 平台选项文案由“拼多多（官方 API）”改为“拼多多”；
   - 底部说明改为：网页模式需先登录拼多多（手机号验证码）；如需官方 API，可在 `settings.yaml` 将 `pdd.mode` 设为 `api`。
7. 文档同步
   - `README.md`、`HANDOFF.md`、`docs/PRD.md` 中“拼多多放弃 H5 网页采集”的结论改为“网页 H5 为默认，官方 API 为可选模式”，并写清单批 10 个详情、10 分钟冷却、遇滑块/降级即停。
8. 不动的部分
   - `bkr/providers/pdd_ddk.py` 保留可用；
   - 数据表结构、报告列、`web/src/panels/DataPanel.tsx` 均不改；
   - 不新增批次状态枚举，复用 `paused_captcha`（UI 已支持“待人工处理”）。

---

## 7. 测试与验收

### 7.1 单元测试（不访问网络）

在 `tests/test_platforms.py` 或新建 `tests/test_pdd_web.py` 中覆盖：

1. `normalize_card`：
   - SSR 字段“分 → 元”（`2990` → `¥29.90`）；
   - `salesTip` 三种形态（`已拼10万+件` / `已拼 300+ 件` / `已拼1.2万件`）；
   - `goodsID` 缺失返回 `None`；`exclude_words` 与价格区间过滤；
   - `//` 图片补协议。
2. 文本解析纯函数（用固定样本文本，不读真实页面）：
   - `\u200b` 清除后店铺名可识别；
   - “已拼件”与“本店已拼件”分开，前者进 `sold_lb`，后者只进 `params["店铺已拼"]`；
   - `¥3.?` 返回 `None`；
   - `商品评价(16,523)` 解析出评价数；标签去重连接；
   - 发货地只取省级；参数栏键值对进入 `params`。
3. 异常识别纯函数：
   - 滑块文案 → `BlockedError`；
   - 登录/App 打开提示 → `LoginError`；
   - “前往APP查看价格”且缺店铺 → `PddDegradedError`。
4. 模式分支：
   - `pdd.mode=web` 时 `is_authorized("pdd")` 依赖登录 cookie，不依赖 API 配置；
   - `pdd.mode=api` 时行为与现在一致。

### 7.2 实机冒烟（必须真实执行并贴日志）

```powershell
python -m bkr.cli login --site pdd
python -m bkr.cli collect --task <pdd 任务> --pages 1
```

验收点：

- 搜索页解析到 > 0 个候选，日志能看到 SSR 字段校准结果；
- 详情数不超过 10；超过时批次状态为 `paused_captcha`，错误信息含冷却时间与 `--resume` 命令；
- 每批结束后 `data/raw/<batch>/pdd/` 有对应 JSON（`save_html` 时还有 HTML）；
- 随机抽查 2 条详情：价格、销量口径、店铺名、发货地、参数与页面一致；缺失字段为空而不是 0；
- 故意在登录失效状态跑一次：应报 `LoginError` 并暂停，而不是解析 0 条或伪造数据；
- `pytest tests/test_platforms.py tests/test_pdd_web.py -q` 全绿。

### 7.3 日志要求

打印且仅打印关键节点：平台/模式、搜索 URL（关键词已编码）、本次 SSR 条数、去重后候选数、详情进度 `n/N`、每批截断与暂停原因、降级/风控命中原因。禁止把整段 `rawData` 或整页文本打进日志。

---

## 8. 明确不做

- 不调用、不逆向任何拼多多接口，不构造签名，不用 `fetch` / XHR 取数；
- 不实现滑块、验证码、扫码绕过；不自动重试风控页面；
- 不引入 OCR / 视觉模型 / 截图识图链路；
- 不同时开多个详情页，不刷新同一 URL，不在同一批超过 10 个详情；
- 不新增平台专属数据库字段，不改报告口径，不跨平台比较“已拼/已售/成交”；
- 不为拼多多单独重写采集编排；能复用 `collector` / `pace` / `parse` / `browser` 的一律复用；
- 不删除官方 API 通道，不覆盖 `settings.yaml` 已有配置。

---

## 9. 交付物

- 改写后的 `bkr/platforms/pinduoduo.py`（SSR 列表、详情解析、PDD 专属异常与常量）；
- `bkr/collector.py`、`bkr/config.py`、`bkr/auth.py`、`server/jobs.py`、`server/routes/env.py` 的集成改动；
- `web/src/panels/CollectPanel.tsx` 文案调整；
- 新增/补充单元测试；
- `README.md`、`HANDOFF.md`、`docs/PRD.md` 的拼多多说明同步。

完成后输出一份简短变更说明：改了哪些文件、网页/API 两种模式如何切换、实机冒烟结果、仍存在的风险（例如降级阈值需随页面改版校准）。
