# 汽车销售多国看板 · 操作指南

一个配置驱动的 Python 工具。**两种用法**，代码共用：

- **Web App 模式**（推荐分享场景）：FastAPI + SQLite，支持浏览器上传累积数据、时间筛选、环比/同比、公网链接分享。见 [第 2 节](#2-部署成-web-app)。
- **CLI 模式**（原有用法）：一次性生成静态 HTML/PNG/PDF 到 `output/`，适合离线归档或手动发文件。见 [第 3 节](#3-cli-日常使用)。

两种模式读同一套 `config/countries.json` 和同一套归一化逻辑，所以列映射只写一次，CLI 和 Web 都能用。

---

## 目录

1. [首次安装](#1-首次安装)
2. [部署成 Web App](#2-部署成-web-app)
3. [CLI 日常使用](#3-cli-日常使用)
4. [输入数据要求](#4-输入数据要求)
5. [上传时的列映射](#5-上传时的列映射)
6. [输出产物（CLI）](#6-输出产物cli)
7. [分享给别人](#7-分享给别人)
8. [新增 / 修改一个国家](#8-新增--修改一个国家)
9. [命令行参数完整列表](#9-命令行参数完整列表)
10. [常见问题](#10-常见问题)
11. [项目结构](#11-项目结构)

---

## 1. 首次安装

```bash
cd car-sales-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 仅在想用 CLI 导出 PNG/PDF 时才需要：
playwright install chromium       # 约 90MB
```

核心依赖：`pandas`、`openpyxl`（读 xlsx）、`plotly`、`jinja2`、`fastapi`、`sqlalchemy`、`uvicorn`。`playwright` 只给 CLI 导出 PNG/PDF 用，Web App 模式不需要。

跑测试：

```bash
pytest tests/                     # periods 计算 + FastAPI 路由
```

---

## 2. 部署成 Web App

Web App 模式把数据存进 SQLite 数据库（支持反复上传累积）、提供浏览器 UI（时间筛选 / 环比同比 / 国家多选 / PNG 截图 / PDF 打印 / 复制分享链接）、生成**公网可分享 URL**（读只读，写要管理员账号密码）。

### 2.1 本地跑一下

```bash
source .venv/bin/activate
uvicorn src.app:app --reload
# 浏览器打开 http://localhost:8000
```

第一次访问数据库是空的，页面会引导你去 `/upload`。管理员登录入口在 `/admin/login`，**默认账号**：

| 字段 | 默认值 | 如何覆盖 |
|---|---|---|
| 用户名 | `Kirby` | 环境变量 `ADMIN_USERNAME` |
| 密码 | `Kirby123` | 环境变量 `ADMIN_PASSWORD` |
| Session 签名盐 | `ADMIN_PASSWORD` 的值 | 环境变量 `SESSION_SECRET`（可选） |

- 数据文件默认写到 `car-sales-dashboard/data/app.db`，不会污染 `output/`
- 想用别的路径：`export DB_PATH=/somewhere/app.db`
- **部署到公网前一定要改密码**（见下一节）。默认账号只是方便你第一次登录。

### 2.2 部署到 Render（免费层）

仓库里已经有 [Dockerfile](Dockerfile) 和 [render.yaml](render.yaml)。步骤：

1. 把 `car-sales-dashboard/` 推到一个 GitHub 仓库
2. 打开 https://render.com/ → **New** → **Blueprint** → 选刚才的仓库
3. Render 读到 `render.yaml` 后会：
   - 按 Dockerfile 构建镜像
   - 挂一块 1GB 持久盘到 `/var/data`（SQLite 存这里，重启不丢数据）
4. 构建完拿到一个 `https://<your-app>.onrender.com` URL
5. **Render Dashboard → Environment** 加两个变量覆盖默认账号：
   - `ADMIN_USERNAME` = 你的用户名
   - `ADMIN_PASSWORD` = 强密码（建议 16+ 字符随机串）
6. 打开 `/admin/login`，输入用户名密码 → 跳到 `/upload` → 上传第一个文件 → 完事

分享的时候把根 URL（或带 query params 的筛选 URL）发给别人即可。他们看到的页面**不会有 Upload 按钮**（因为没登录），但依然可以用 **Export PNG / Export PDF / Copy link** 三个按钮导出/分享当前视图。

### 2.3 Web App 路由速查

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 总览页，支持 `?start=...&end=...&granularity=M&countries=USA&countries=France` |
| `GET` | `/country/{slug}` | 单国详情，同上筛选参数 |
| `GET` | `/upload` | 上传页（需 token） |
| `POST` | `/upload/preview` | 解析文件 + 返回列映射建议（JSON） |
| `POST` | `/upload/commit` | 落库 |
| `POST` | `/admin/batches/{id}/delete` | 回滚某次上传 |
| `POST` | `/admin/login` | 把 token 存成 cookie |
| `GET` | `/healthz` | 健康检查 |

Admin 鉴权：带 HTTP header `X-Admin-Token: <token>` 或通过 `/admin/login` 表单设置的 cookie。

### 2.4 环比 / 同比

KPI 卡片会根据当前粒度显示两个徽章：

| 粒度 | 徽章 A | 徽章 B |
|---|---|---|
| 月 (M) | **MoM**（与上月比） | **YoY**（与上年同月比） |
| 季 (Q) | **QoQ**（与上季比） | **YoY**（与上年同季比） |
| 年 (Y) | **YoY**（与上年比） | — |

计算是在 pandas Period 上做的，**即使中间月份缺数据也能算**（会用 `period_range` 补齐再 shift，然后对缺口输出 NaN 而不是错位对齐）。

---

## 3. CLI 日常使用

**最常用**（生成 HTML + PNG + PDF 全套）：

```bash
source .venv/bin/activate
python -m src.cli --input "../HTML/Auto Sales data.csv"
open output/index.html
```

**只要 HTML，不导出图片**（快，几秒钟）：

```bash
python -m src.cli --input data/*.xlsx --formats html
```

**只生成指定国家**：

```bash
python -m src.cli --input data/q1.xlsx --countries USA France China
```

**多个文件一起处理**（自动合并）：

```bash
python -m src.cli --input data/q1.xlsx data/q2.xlsx data/q3.csv
```

---

## 4. 输入数据要求

工具对输入 Excel/CSV 的要求**很宽松**：

- 扩展名支持：`.xlsx` / `.xls` / `.csv`
- 表头行位置：自动探测（扫前 10 行找列名匹配最多的一行）
- 一个文件可以只含一国，也可以含多国混合
- 列名不需要一致（配置里给了候选列表），大小写不敏感

**必需字段**（归一化后至少要有日期和销售额，其它是加分项）：

| 标准字段 | 含义 | 示例列名（都能识别） |
|---|---|---|
| `date` | 订单/销售日期 | ORDERDATE, Order Date, 日期, 销售日期 |
| `country` | 国家 | COUNTRY, Country, 国家 |
| `sales` | 销售额 | SALES, Amount, Revenue, 销售额, 销售金额 |
| `quantity` | 销量台数 | QUANTITYORDERED, Qty, Quantity, 数量 |
| `model` | 车型 | PRODUCTLINE, Model, 车型 |

没识别到的字段会走 `config/countries.json` 里 `default` 的配置；识别不到必需字段的行会被跳过，并在日志里提示。

---

## 5. 上传时的列映射

上传的文件**不必**完全符合模板，两种方式让列映射适配你的实际数据：

### A. UI 交互式（推荐，Web App 模式）

1. 打开 `/upload`，选文件，点 **Preview**
2. 系统会：
   - 自动探测表头行（扫前 10 行找列名匹配最多的那行）
   - 从数据里读出所有出现的国家
   - 为每个国家的每个标准字段（date / sales / quantity / model）猜一个最可能的原始列名，其他原始列会列进下拉候选
   - 同时给出前 5 行的数据预览，方便你对照
3. 你可以对每个字段用下拉菜单**手动改正**。改完点 **Commit** 入库
4. 入库走 SQLite `ON CONFLICT DO NOTHING`，相同 `(date, country, model, sales, quantity, source_file)` 的行会被自动去重 —— 同一个文件传两次不会翻倍

标准字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `date` | ✅ | 日期列，支持 `%Y-%m-%d` / `%d.%m.%Y` / `%m-%d-%Y` 等常见格式，也支持 Excel 日期格子 |
| `sales` | ✅ | 销售额（数字） |
| `quantity` | | 销量台数；缺失默认 0 |
| `model` | | 车型名；缺失默认 `Unknown` |

### B. 编辑 `config/countries.json`（高级用户 / CLI 批量）

如果你经常处理同一种格式的数据，可以把映射写死在配置里 —— CLI 和 Web 都会读同一份配置。示例见 [第 8 节](#8-新增--修改一个国家)。配置里写的 `column_map` 会作为**候选列表**参与自动匹配，UI 预览会优先选其中存在于原始文件的那一列。

### 映射流程示意

```
上传文件 → 自动探测表头 → 读原始列 → 对每国每字段：
                                      ├─ 用户显式覆盖？→ 用它
                                      ├─ 配置 column_map 里有匹配？→ 用它
                                      └─ 都没有 → 按字段名做模糊匹配（英中混合）
```

---

## 6. 输出产物（CLI）

每次运行完，`output/` 目录长这样：

```
output/
├── index.html                    # 总览页：所有国家卡片 + 对比图
├── style.css
├── countries/
│   ├── usa.html                  # 单国详情页
│   ├── france.html
│   └── ...
└── exports/
    ├── overview.png              # 总览页的图片版
    ├── overview.pdf              # 总览页的 PDF 版
    ├── usa.png
    ├── usa.pdf
    └── ...                       # 每国各一份 PNG + PDF
```

- **HTML** 带完整交互：hover 看数值、双击图表区缩放、点卡片跳转详情页
- **PNG** 是满页截图，分辨率 2880px 宽（retina），可直接丢进 PPT / 文档
- **PDF** 是 A4 排版，适合打印或归档

---

## 7. 分享给别人

按**对方要什么**选一种：

### A. 只要一张图 / 一份报告 → 发 PNG 或 PDF

直接把 `output/exports/` 里对应文件发邮件 / 微信。每个文件完全独立、离线可看。

- 想要全局快照：`overview.png` 或 `overview.pdf`
- 想要某国详情：`{country}.png` 或 `{country}.pdf`

### B. 要可交互的网页（能 hover、能点下钻） → 打包整个 output

```bash
cd car-sales-dashboard
zip -r dashboard.zip output
```

把 `dashboard.zip` 发给对方，对方解压后双击 `output/index.html`。

**⚠️ 注意**：当前 HTML 通过 CDN (`cdn.plot.ly`) 加载 Plotly，对方打开时**需要联网**。如果要完全离线，打开 [templates/base.html.j2](templates/base.html.j2)，把

```html
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
```

改成把 `plotly.min.js` 下载到 `assets/` 再引用，或者在 [src/renderer.py](src/renderer.py) 把 `_PLOTLY_KW` 里的 `include_plotlyjs=False` 改成 `include_plotlyjs='inline'`（每页 HTML 会增大约 3MB，但完全离线）。

### C. 要一个可分享的 URL（多人、手机也能看） → 扔静态托管

`output/` 是一个标准静态站点，任何静态托管服务都能放：

| 服务 | 操作 | 耗时 |
|---|---|---|
| **Netlify Drop** | 打开 https://app.netlify.com/drop，把 `output/` 文件夹拖进去 | 30 秒 |
| **Cloudflare Pages** | 命令行 `npx wrangler pages deploy output` | 1 分钟 |
| **GitHub Pages** | 推到仓库 → Settings → Pages → source 选 `/output` | 2 分钟 |
| **Vercel** | `vercel deploy output` | 1 分钟 |

Netlify Drop 最快、免注册，适合临时分享；要长期、自定义域名就用 Pages / Vercel。

### D. 定期更新给同一批人 → 建议 C + 每次重跑覆盖

如果同一个看板每周/每月要更新给同样一批同事，推荐方案 C：给对方一个固定 URL，每次 `python -m src.cli ...` 重跑后重新上传 `output/` 即可。

---

## 8. 新增 / 修改一个国家

打开 [config/countries.json](config/countries.json)，在 `countries` 里加一条。**只写和 default 不同的部分**即可。

### 最常见：本地化日期格式 + 币种

```json
"Germany": {
  "display_name": "Deutschland",
  "currency": "EUR",
  "date_format": "%d.%m.%Y"
}
```

### 列名完全不同（比如中文表头）

```json
"China": {
  "display_name": "中国",
  "currency": "CNY",
  "column_map": {
    "date":     ["销售日期", "日期"],
    "sales":    ["销售金额", "金额"],
    "quantity": ["销量", "数量"],
    "model":    ["车型名称", "车型"]
  }
}
```

写了的键会覆盖 default，没写的键走 default 的候选列表。改完不用重启，直接重跑 CLI 就生效。

`date_format` 用 Python `strptime` 语法：`%Y-%m-%d` / `%d/%m/%Y` / `%m-%d-%Y` 等。

---

## 9. 命令行参数完整列表

```
python -m src.cli --input FILE [FILE ...] [选项]
```

| 参数 | 简写 | 默认值 | 说明 |
|---|---|---|---|
| `--input` | `-i` | （必填） | 一个或多个输入文件路径 |
| `--output-dir` | `-o` | `./output` | 输出目录 |
| `--formats` | `-f` | `html png pdf` | 产物类型，可选 `html` / `png` / `pdf`，空格分隔 |
| `--countries` |  | （全部） | 只渲染指定国家，例：`--countries USA France` |
| `--config` | `-c` | `config/countries.json` | 自定义配置文件 |
| `--top-n` |  | `10` | 每国 TOP 车型数量 |
| `--log-level` |  | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## 10. 常见问题

**Q: 跑了但 `output/exports/` 是空的？**
忘了 `playwright install chromium`。再跑一次这条命令即可。或者你只想要 HTML，把 `--formats html` 带上就跳过导出。

**Q: 某国数据没出现在看板里？**
看日志里有没有 `Skipping N rows ... missing required columns`，通常是日期或销售额列没识别到。在 `config/countries.json` 该国条目里把实际列名加到 `column_map.date` / `column_map.sales` 候选列表。

**Q: 日期全解析失败？**
日志会提示 `date_format %s matched <50% of rows; falling back to infer`。打开配置把该国 `date_format` 改对，或者干脆删掉（让 pandas 自动推断，支持常见格式）。

**Q: 想改颜色 / 字体？**
改 [config/theme.json](config/theme.json) 的 `colors.primary` / `colors.palette` / `font.family`；图表层的风格在 [src/renderer.py](src/renderer.py) 的 `_BASE_LAYOUT`。

**Q: 导出的 PNG 太高、太大？**
改 [src/exporter.py](src/exporter.py) 的 `VIEWPORT`（默认 1440×900，`device_scale_factor=2`），降到 `{"width": 1200, "height": 800}` 和 `device_scale_factor=1` 文件会小很多。

**Q: 想把工具注册成 Claude Code skill？**
根目录已经有 [SKILL.md](SKILL.md)。把整个 `car-sales-dashboard/` 拷到 `~/.claude/skills/` 即可被 Claude Code 自动发现。

---

## 11. 项目结构

```
car-sales-dashboard/
├── README.md               # 就是这个文件
├── SKILL.md                # Claude Code skill 元数据
├── requirements.txt
├── Dockerfile              # Web App 部署用
├── render.yaml             # Render Blueprint 部署配置
├── config/
│   ├── countries.json      # 字段映射 + 各国覆盖
│   └── theme.json          # 配色、字体
├── src/
│   ├── loader.py           # 读 xlsx/csv，自动探测表头行
│   ├── normalizer.py       # 套配置归一化；detect_columns / normalize_with_mapping
│   ├── analyzer.py         # pandas 聚合 + 日期筛选 + 粒度
│   ├── periods.py          # 环比 / 同比（MoM / QoQ / YoY）
│   ├── filters.py          # Web 端 query params 解析
│   ├── renderer.py         # Plotly 出图 + Jinja2 渲染
│   ├── exporter.py         # Playwright → PNG + PDF（仅 CLI 用）
│   ├── db.py               # SQLAlchemy 模型 + session（Web App）
│   ├── app.py              # FastAPI 入口（Web App）
│   └── cli.py              # 入口（CLI 模式）
├── templates/
│   ├── base.html.j2
│   ├── overview.html.j2 / _overview_body.html.j2
│   ├── country.html.j2  / _country_body.html.j2
│   ├── upload.html.j2 / login.html.j2 / empty.html.j2
│   └── partials/
│       ├── filter_bar.html.j2
│       ├── kpi_cards.html.j2
│       └── trend_table.html.j2
├── assets/
│   └── style.css
├── tests/
│   ├── test_periods.py     # MoM / QoQ / YoY 计算
│   └── test_api.py         # FastAPI 路由 / 上传 / 权限
├── data/                   # Web App 模式下 SQLite 落地（gitignored）
└── output/                 # CLI 模式生成物
```

数据流：

```
CLI 模式：  输入文件 → loader → normalizer → analyzer → renderer → exporter
Web 模式：  浏览器 → app.py → loader → normalize_with_mapping → db.py（SQLite 累积）
                ↑                                              ↓
         filter_bar + HTMX  ←  renderer  ←  analyzer  ←  load_sales_df
```
