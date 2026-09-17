# Paper Companion Marketplace · 论文研读助手（分享版）

一个 WorkBuddy 插件市场，目前包含一个专家：**论文研读助手 · 分享版**（`paper-companion-share`）。

> 按意图检索论文（多源校验）→ 精读逐段流畅中文转写 → 亮点与批判注记 → 多篇汇成可长期回访的**研读门户**（每篇一个深度解析页 + 中英双语对照 + 原文 PDF 内嵌）→ 全部笔记合并为单文件 **HTML 解析版全集**。
> 铁律：**绝不幻觉**，所有外部信息可溯源；笔记只写入工作区专属子目录，**不污染你的工作区**。

---

## 安装

### 方式一：一行命令（推荐）

在 WorkBuddy 对话框中输入：

```
/plugin marketplace add BANG644/paper-companion-marketplace
```

然后安装专家：

```
/plugin install paper-companion-share@paper-companion-marketplace
```

### 方式二：粘贴链接

在「专家 · 技能 · 连接器」→ 插件页面右上角 **+**，输入市场地址：

```
https://raw.githubusercontent.com/BANG644/paper-companion-marketplace/main/.codebuddy-plugin/marketplace.json
```

### 方式三：下载离线包

在 [Releases](https://github.com/BANG644/paper-companion-marketplace/releases) 页面下载 `paper-companion-share.zip`，解压后得到 `paper-companion-share/` 目录，放进：

```
~/.workbuddy/plugins/marketplaces/my-experts/plugins/
```

重启 WorkBuddy 后即可在「我的专家」中看到。

---

## 首次使用

专家会在**首次会话、或首次用到某项外部能力**时自动做环境自检，并一步步引导你补齐依赖。完整手册见专家包内的 `BOOTSTRAP.md`。

需要留意的三类依赖：

| 依赖 | 什么时候需要 | 怎么处理 |
|------|--------------|----------|
| **Python 3.8+** | 检索 / 下载 / 门户 / digest 脚本（纯标准库，零三方依赖） | 专家自动定位 `python3` / `python`；都没有则引导安装 |
| **网络 / 代理** | 访问 arXiv / OpenAlex / Crossref 等外网学术 API | 受限网络需代理。端口请在你本机代理客户端（如 Clash）查看，专家会引导你填入，**不硬编码** |
| **`pinme` CLI**（可选） | 仅当你要把门户部署成公网链接时 | 专家自动 `npm i -g pinme`；`pinme login` 需你本人完成 |

> **PyMuPDF 是可选的**：研读门户「双语对照」的英文原文默认由专家直接读你的 PDF 完成，**不需要任何额外依赖**；仅当一次批量处理 ≥3 篇时，装上它会更快更一致。

---

## 目录约定

- **你的原文**放在工作区根目录（PDF / 源文件 / 手写清单）。
- **专家生成的派生内容**只写入 `<workspace>/.paper-companion/`（`papers/`、`portal/`、`visuals/`、`index.md`、`digest.html`），绝不污染根目录。
- 其余工作各归其子目录，本专家不触碰、不混入。

---

## English

An academic paper reading companion for WorkBuddy: intent-driven literature discovery with multi-source validation, paragraph-by-paragraph fluent Chinese translation, highlight and critique notes, multi-paper study portals (per-paper deep-dive pages with bilingual side-by-side views and embedded PDFs), and a single-file HTML digest. Strict no-hallucination policy; layered memory confined to a dedicated subdirectory so your workspace stays clean.

### Install

```
/plugin marketplace add BANG644/paper-companion-marketplace
/plugin install paper-companion-share@paper-companion-marketplace
```

Or paste the raw marketplace URL into the plugins page (**+**):

```
https://raw.githubusercontent.com/BANG644/paper-companion-marketplace/main/.codebuddy-plugin/marketplace.json
```

---

## License

MIT
