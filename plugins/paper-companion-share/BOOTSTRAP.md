# 环境初始化引导手册（BOOTSTRAP）

本手册供**拿到这个专家包的使用者**阅读。专家本身会在首次对话时自动做环境自检并引导你补齐依赖；本文件让你提前了解每一项是什么、为什么需要、以及遇到问题时怎么手动处理。

> 设计原则：能自动装的自动装；只有**必须由你本人操作**的（代理端口、账号登录）才需要你动手，且专家会明确提示。

---

## 0. 总览：专家会自动检查这五类依赖

| 依赖 | 什么时候需要 | 自动还是人工 |
|------|--------------|--------------|
| Python 3.8+ | 任何检索 / 下载 / 门户 / digest 操作 | 自动定位；缺了引导你装 |
| 辅助脚本 `scripts/` | 同上（脚本随包附带，无需另装技能） | 自动定位；移动过则请你指定 |
| 网络 / 代理 | 访问外网学术 API（arXiv / OpenAlex / Crossref） | 代理端口需你提供（去 Clash 看） |
| PyMuPDF | **可选加速器**：一次批量处理多篇论文时抽英文原文 | 自动 `pip install`（你同意后）；装不上就回退 |
| `pinme` CLI | **仅**当你要「部署成公网链接」时 | 自动 `npm i -g`；`login` 需你本人 |

> **前 3 类决定「能不能用」，后 2 类只影响「快不快 / 能不能发到公网」。**
> 也就是说：**只装 Python（甚至用系统自带的）就能跑通全部核心链路**；PyMuPDF 与 pinme 都缺失时，专家会走降级路径，不会卡住。

---

## 1. Python 运行时

脚本全部只用 Python 标准库，**零三方依赖**，Python ≥ 3.8 即可。

**检查是否已安装：**
```bash
python3 --version   # 或 python --version
```
只要能看到版本号且 ≥ 3.8，就没问题，专家会自动用它。

**没装的话（按你的系统）：**
- macOS（Homebrew）：`brew install python`
- Windows（推荐）：从 python.org 下载 3.8+ 安装包，安装时勾选 "Add to PATH"；或用 `winget install Python.Python.3.12`
- Linux（Debian/Ubuntu）：`sudo apt install python3`
- 装好后重开终端再跑一次上面的 `python3 --version` 确认。

> 无需虚拟环境、无需 pip 安装任何包。

---

## 2. 辅助脚本 `scripts/`

检索、下载、门户、digest 四个脚本已**打包进本专家包的 `scripts/` 目录**，你不需要单独安装任何技能。

专家会按下面的顺序自动找到它们：
1. 你预设的环境变量 `PAPER_COMPANION_SCRIPTS`；
2. 专家包自带的 `scripts/`（最稳妥，一般不用管）；
3. 若你本机另装了 paper-explorer 技能，也会复用 `~/.workbuddy/skills/paper-explorer/scripts/`。

**如果你把 `scripts/` 挪到了别处**：在对话里告诉专家「脚本在 `<你的路径>`」，它就能继续工作。

---

## 3. 网络与代理（最常见卡点）

访问 arXiv / OpenAlex / Crossref 等外网学术资源，**在受限网络下需要走代理**。

**代理端口怎么拿：**
1. 打开你本机的代理客户端（如 **Clash / Clash Verge / v2rayN** 等）；
2. 在「端口」或「本地代理」设置里看 **HTTP / SOCKS 监听端口**，常见是 `7890` 或 `7897`（以你客户端实际显示为准）；
3. 代理地址格式通常为 `http://127.0.0.1:<端口>`。

**怎么交给专家：**
- 专家检测到网络不可达时，会主动问你：「请提供你的代理地址（端口可在 Clash 中查看）」。
- 你回一句，例如：`http://127.0.0.1:7897`
- 专家会把它记到本次会话（`PAPER_COMPANION_PROXY`）并透传给所有脚本，**不会写死在任何文件里**。
- 也可以自己预设环境变量后启动：`export PAPER_COMPANION_PROXY="http://127.0.0.1:7897"`。

> 如果你处于可直连外网的环境，直接忽略这一步即可，专家会自动直连。

**注意：只有联网脚本接受 `--proxy`。**
`search.py`（检索）与 `download.py`（下载）会走网络，接受 `--proxy`；
`build_portal.py`（生成检索门户）与 `build_digest.py`（生成解析版全集）是**纯本地渲染**，不接受该参数——给它们加 `--proxy` 会直接报「unrecognized arguments」而失败。

---

## 4. PyMuPDF（可选加速器，仅多篇批量时才值得装）

解析页的**中英双语对照**默认由专家**直接读取 PDF 原文**完成，**不需要任何额外软件**。

什么时候装它才有意义：一次要批量处理**多篇（≥3）**论文。此时 PyMuPDF 能一次性把每篇 PDF 抽成带 `===== PAGE N =====` 分页标记的英文文本，速度更快、格式更统一。

**检查是否已装：**
```bash
python3 -c "import fitz; print('ok')"
```

**安装（专家会先征求你同意，再自动执行）：**
```bash
python3 -m pip install pymupdf
```

**装不上怎么办？**
完全不影响使用。专家会如实告知并**回退到零依赖默认路径**（自己逐篇读 PDF），流程照常走完。常见原因：网络受限（先做第 3 节配代理）、pip 被环境限制、Python 太旧。不用反复重试。

---

## 5. `pinme` CLI（仅部署公网链接时需要）

只有当你说「把检索门户 / digest 部署成公网链接」时才需要，日常研读用不到。

**安装（专家可自动执行）：**
```bash
npm install -g pinme
pinme --version   # 看到版本号即成功
```
> 需要 Node.js ≥ 16.13.0。若 `npm` 不存在，先装 Node.js（nodejs.org）。

**登录（必须你本人操作，专家无法代劳）：**
```bash
pinme login          # 浏览器授权，或
pinme set-appkey <你的AppKey>   # 如果你已有 AppKey
```
登录后专家就能执行 `pinme upload <目录>` 生成 `https://<hash>.pinme.dev` 公网链接。

---

## 6. 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 检索一直超时 / 报错 | 没配代理或代理端口错 | 按第 3 节提供正确代理地址 |
| 脚本报 `unrecognized arguments: --proxy` | 给纯本地脚本（`build_portal.py` / `build_digest.py`）加了 `--proxy` | 只给 `search.py` / `download.py` 传 `--proxy`，见第 3 节 |
| 专家找不到脚本 | `scripts/` 被移动 / 包没解压完整 | 告诉专家脚本路径，或重新解压专家包 |
| `python3: command not found` | 没装 Python 或没进 PATH | 按第 1 节安装并确认版本 |
| 解析页双语对照很慢 | 多篇批量 + 未装 PyMuPDF，走了逐篇读 PDF 的默认路径 | 可选：按第 4 节装 PyMuPDF 加速；不装也能跑完 |
| `pinme: command not found` | 没装 pinme（仅部署时报） | 按第 5 节 `npm install -g pinme` |
| `pinme login` 打不开浏览器 | 无图形界面 / 网络问题 | 改用 `pinme set-appkey <AppKey>` |

---

## 7. 分享前提醒（给打包者）

本专家包**不应包含**任何个人笔记或记忆文件：`.paper-companion/`（工作区笔记）与用户级记忆（`~/.workbuddy/memory/paper-companion-registry.md`）都是在**使用者自己机器上运行时**生成的，不会进入此包。打包分享前确认目录里只有专家内容本身即可：

```
paper-companion-share/
├── .codebuddy-plugin/plugin.json
├── agents/paper-companion-share.md
├── README.md
├── BOOTSTRAP.md
├── 功能详解.md
├── avatars/
└── scripts/          # 随包附带的辅助脚本（纯标准库）
```

> 本分享版**不含**作者自用版依赖的私有 HTML 看板索引服务（依赖作者本机私有技能与看板服务器，他人机器上不存在），也不需要任何额外服务即可完整使用研读门户与解析页。
