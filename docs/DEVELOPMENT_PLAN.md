# GoogleUnlimited / Mac M2 原生 Google Photos 备份试验 — 唯一开发与交接文档

> 最后更新：2026-11-14 07:36 (America/Los_Angeles)
> 负责执行节点：NexusDock mac-M2（`node_18eb67f64516bf229adb4927b6e076ff`）
> 当前恢复任务 ID：`tsk_6c3a5c2675bfaefd`（前期任务 ID 和验收历史见下文）
> 工作目录：`/Users/samni/Desktop/开发项目/GoogleUnlimited`
> 上游仓库：<https://github.com/shishir0x/GoogleUnlimited>
> 发布目标：<https://github.com/samni728/GoogleUnlimited> （已确认用户仓库空仓，GitHub 有 push 权限）
> 当前上游基准 commit：`5b7f5387e218a7d407a27e576989cae707319dc6`（2026-11-05 提交）
> 当前阶段：WebUI 网络共享入口与可恢复 SQLite 媒体索引已实现，本机 17 项测试与 HTTP 验证已通过；**未连接 Google 账号、未开展真实上传，也尚未实测真实 NAS 登录。**

## 1. 项目目标与边界

目标：在 Mac M2 上评估 GoogleUnlimited（负责文件发现、去重、SQLite 任务与重试）和 gotohp（非官方上传引擎），尽可能避免现有小米 3 / USB / Android FUSE / 媒体扫描引起的传输瓶颈。首要衡量指标是真实、可复核的上传任务状态，不以本地扫描数、上传命令退出码或项目自称「无限容量」充当 Google Photos 云端完成证明。

**必须保持不变的现有业务：** MI3MountMac、小米 3、Pixel 5 模拟、qtscrcpy/ADB、已有 USB/VPN、Google Photos 正在运行的批次及自动巡检、原有界面均不受本试验影响。不得复用正在上传的正式目录作为新引擎输入，不得自动更改 LaunchAgent、Chrome、Google 账号、官方 Photos App 配置，且无明确上线验收前不可关闭或迁移现有链路。

**安全边界：** 不通过 Chrome 插件持续读取/上传账号 Cookie；不要收集、记录、复制或提交 Google `oauth_token`、master token、设备绑定密钥、账号密码。优先只进行无账号 / 无网络上传的测试。认证失效才由用户主动执行登录；gotohp 代码支持在已有持久凭据有效时按需刷新短期 BearerToken，但不保证持久凭据始终有效。未来实际上传只在独立账号和隔离样例下、单独记录确认后进行。

**免配额事实边界：** Pixel 5 的官方无限政策仅涉及 Storage Saver。gotohp 的 `--use-quota=false` 与 `--saver=false` 是客户端请求设置，不等于 Google 官方已批准无限原画质或云端配额确实不增加。不得称未经核对的结果为「已验证无限」。

## 2. 当前架构及拟议解耦架构

```text
现有生产:
Mac 原片/外盘 → MI3MountMac 分批/映射 → USB/VPN → Xiaomi 3W
 → Android Google Photos → Google 账号 [云端独立核验尚需加强]

试验（完全独立）:
Mac 专用 test-fixtures/ 图片样本 → GoogleUnlimited watcher / SQLite
 → gotohp CLI (初期仅 mock / dry-run) → 独立任务事件与状态文件
 → [仅完成前述验收后] 测试账号小规模真实上传

未来可选：
MI3MountMac UI／调度层 -> 可替换的上传引擎接口
                  ├── android-mi3 (旧引擎、保留)
                  └── mac-native (经验证再启用)
Google 账号凭据只保存在本机受限文件/系统安全存储，不经 NexusDock 通道同步；
管理后台只展示掩码账号和上传状态。
```

不能因为上传引擎更换而使旧引擎、既有历史、已完成批次记录消失；新旧状态分别管理。源文件只读，禁止测试时开启 `--delete` / `--force` / 大规模 `--resync`。

## 3. 分阶段实施与验收

| 阶段 | 工作项 | 完成标准 | 状态 |
| --- | --- | --- | --- |
| P0 | 拉取精确上游版本，检查安全、运行时依赖和本地目录 | Git commit、环境和不触碰生产服务的边界可确认 | **已完成仓库克隆与基本检查** |
| P1 | 识别 shell 脚本入口、潜在网络/凭据行为、测试影响；把风险写入本文 | 不运行未审阅的安装脚本及 token 测试 | **已完成首轮审查** |
| P2 | 本地离线单元/回归测试：路径扩展、文件准备、去重、成功判断、监控恢复 | 可重复的通过/失败结果与原因；无真实上传 | **七个白名单套件单独通过** |
| P3 | Mac ARM64 `gotohp-cli` 签名/哈希/发布来源审计和沙箱验证；依赖 `fswatch` | 能解释架构兼容性、性能测量方式与安装风险 | **已完成：SHA 校验、沙箱 --help/version、fswatch 安装** |
| P4 | 本地模拟上传接口/队列，证明扫描、哈希去重、SQLite 任务恢复、进度事件 | 控制面可观测，明确区分本地完成和云端验收 | **audit 和 mock 失败→重试→成功→去重测试通过；进度事件需后续加强** |
| P5 | 独立测试账号小规模云端验证（额外授权步骤） | Google Photos 内看到独立文件，比较相册元数据、配额前后变化及延迟，无重复上传 | **未授权真实账号试验** |
| P6 | 适配 MI3MountMac 引擎抽象（灰度、回滚、保留小米 3） | 并行新旧引擎不相互覆盖，测试通过且可回滚 | 待 P5 成功后 |

### 本轮执行范围

本轮已完成 P0、P1 首轮和 P2 白名单测试，并实现 P4 的第一个只读能力；P3 真实 Mac 引擎、P4 上传 mock/队列继续按风险优先级推进。所有实际命令、发现、错误和后续待办一律**追加更新本文**，不为每次会话新建进度副本。

## 4. 认证与账号方案

- gotohp 初次加入 Google 账号通过 Embedded Setup 页面取得 `oauth_token`，换取持久化账号凭据；实际请求的短期访问令牌可过期后按需更新。
- 不开发「Chrome 插件周期读取 Google Cookie -> Server」模式。未来可开发 Chrome 端可见的授权状态和用户主动重新授权入口；Chrome Profile 必须明确与账号绑定。
- 禁止对真实账号用 mock token 触发远程认证测试；如测试要使用假凭据，需 mock 掉所有网络请求。
- 对于涉及设备绑定、Google 服务条款和逆向接口变动的情况，保持失败可检测、可停机、可回滚，不承诺绕过。

## 5. 初始安全审查发现（2026-11-13 22:47）

- 源码仓库最初为空；已浅克隆上游 `5b7f538`；工作区根目录无既有 AGENTS.md，适用 `~/.agentdock/AGENTS.md`。
- 上游包含 macOS `google-unlimited.sh`、Unix 回归测试和 Windows x64 可执行二进制；尚无 Mac ARM64 gotohp 本机 CLI。
- 本机 `sqlite3`、`curl`、`git`、`jq` 已可用；`fswatch`、`shellcheck` 尚未安装。**不直接运行 `./google-unlimited.sh setup`**：其实现会下载/运行外部二进制、安装依赖并移除 macOS Gatekeeper quarantine 标记。
- `tests/run_all_tests.sh` 包含 `test_creds.sh`：使用假 token 调用真实 `creds add -`，可能触发 Google 网络请求；包含 `test_process_stop.sh`：模拟/杀死匹配命令行的进程。**未确认完全隔离前不可一键执行全部测试**。
- `tests/test_watch_paths.sh` 会 source 主脚本（主脚本有 `BASH_SOURCE` 调用保护）；运行前仍要审查顶层副作用。
- 源码自身的 SQLite 上传记录并不等于 Google Photos 后台可独立证明的成功或免配额状态。
- CodeGraph 已在**指定仓库**初始化本地索引，但该版本只识别了 2 个 XML/YAML 文件（未分析 Bash）；尝试 `codegraph_explore` 时本机动态 MCP 无响应，因此后续 Bash 修改基于直接读取源码、`bash -n` 和离线回归；`.codegraph/` 已加入 Git 忽略。
- 主脚本曾使用 `wait "$upload_pid" || true; exit_code=$?`，会把真正的失败退出码覆盖为 0；已改为保留原始退出码并补回归用例。
- Mac 没有 Linux `/proc/<pid>/io`，原进度为时间估算且上游不会特别标示；已明确输出 `time estimate`／`local I/O estimate`，**没有实现真正的网络字节精确进度**。
- 上游根目录未看到独立 `LICENSE` 文件；复制／重发布上游代码前仍需确认授权条款和第三方组件许可。
- 发现上游 `get_gotohp_bin` 在 Mac 上缺少 Mac CLI 时可能从通配目录中误选仓库自带的 Linux ELF/Windows PE，导致误报可用。已增加 macOS Mach-O 架构过滤，并通过 `tests/test_macos_binary_selection.sh` 独立测试；初次测试样本假设 `/bin/true` 存在，但该 Mac 只有 `/bin/ls`，替换后测试 PASS。
- 已通过 GitHub Release API 确认 `xob0t/gotohp v0.11.0` 的 Apple Silicon Universal CLI 发布包，大小 36,386,722 字节、SHA-256 `d697e23c648881cf5e7db7b93f815f6d3dcbd9d7718a40d0edffee90456b0557`；Mac M2 下载后 SHA-256 完全一致，`file` 显示 x86_64+arm64 Mach-O。该二进制的 `codesign` 为 ad-hoc、没有 Team ID，**没有运行 CLI 或连接 Google 账号**；`.gitignore` 排除该本地下载产物，防止误提交。哈希一致只能证明与发布资产相同，不能消除供应链风险。

## 6. 质量门禁和状态定义

任务状态严格区分：
1. `discovered` — 在本地扫描到；
2. `ready` — 文件写入结束、格式和大小合法；
3. `queued` — 已纳入待传输队列；
4. `uploading` — 传输中（可取得实际字节数才展示“真实%”）；
5. `api_accepted` — API 明确接受/返回 media key；不能冒称已云端确认；
6. `cloud_verified` — 在 Google Photos 独立查询/人工检查相册和画质；
7. `quota_verified` — Google 账号实际配额前后对照并记录核验时刻、误差范围；
8. `failed` / `retrying` — 错误、原因、次数与重试时间。

成功率、吞吐和容量数字必须标注来源（本地目录、CLI、API、Google 官方界面或人工核验）。进度 UI 不可用基于耗时估算的百分比冒充真实传输字节比率。

## 7. 环境恢复和工作纪律

恢复顺序：打开本文件 -> 读取最新更新记录 -> `git status --short` / `git rev-parse HEAD` -> 只读审查待执行命令 -> 优先继续本任务 ID。不要无条件重新 clone、重复建文件夹、重置 DB 或执行自动更新命令。未取得 Google 授权前不运行 `watch` 或 `enable`，不更改已有 Google Photos / 小米 3 LaunchAgent。

本机校验示例（不会触发登录或上传）：

```bash
cd "/Users/samni/Desktop/开发项目/GoogleUnlimited"
git status --short
bash -n google-unlimited.sh
bash google-unlimited.sh help
```

## 8. 更新日志（按时间顺序，持续追加）

- **2026-11-13 22:43 - P0**：NexusDock 已连接 mac-M2，发现指定目录是空目录、无 docs、无 Git；记录本机平台 macOS arm64。
- **2026-11-13 22:44 - P0**：克隆 `shishir0x/GoogleUnlimited` 到用户指定目录，确定 commit `5b7f5387`，确认未写入真实账号凭据。发现缺少 fswatch，暂不安装。
- **2026-11-13 22:47 - P1**：审阅 `tests/run_all_tests.sh`、`test_creds.sh`、`test_process_stop.sh` 和 `google-unlimited.sh` 运行入口；由于真实网络认证/进程误杀风险，决定只挑选经审计的无网络测试。下一步：静态检查 + 白名单离线回归，记录每个测试结果。
- **2026-11-13 22:50 - P2**：首次白名单回归 6 组，5 组通过、`test_watch_paths.sh` 在非登录 AgentDock 环境因 `USER` 未定义失败；补上 `export USER="${USER:-$(id -un)}"` 后单独复测 6/6 用例通过。
- **2026-11-13 22:51 - P2**：`test_readiness.sh` 5/5、`test_dedup.sh` 8/8、`test_permissions.sh` 4/4、`test_upload_progress.sh` 11/11、`test_upload_verification.sh` 原有 4/4 均通过；测试均不连接 Google，也不运行启动器。
- **2026-11-13 22:52 - P4 部分**：为 `google-unlimited.sh` 新增 `audit|dry-run <目录>` 只读扫描，可显示 scanned/eligible/bytes/skipped；新增 `tests/test_offline_audit.sh`，4 个样例文件中正确选出 2 个媒体、46,080 字节，排除非媒体／过小文件和符号链接。运行不会产生 `gotohp.config`、`gphotos.db` 或 watcher PID。
- **2026-11-13 22:53 - P2 修复**：修复上传失败退出码被 `|| true` 覆盖的问题；新回归模拟「进程失败但输出包含 mediaKey」时应仍判为失败，`test_upload_verification.sh` 新增到 6/6 通过。新进度行会标示本地 I/O 或时间估算，当前 Mac 不保证精确进度。
- **2026-11-13 22:55 - 验证**：Bash 脚本语法检查、`git diff --check` 已通过。七组安全白名单测试（包括新 audit 套件）均已经分别验证通过；未运行含认证与进程终止测试的上游一键测试。没有 Google 凭据、没有真实照片上传、没有接管现有 MI3MountMac。
- **2026-11-13 22:56 - 复跑限制**：后续将七个白名单套件串联的整轮复跑在 Mac AgentDock 会话中执行时间过长而超时；超时前 `test_offline_audit.sh`、`test_readiness.sh`、`test_dedup.sh`、`test_permissions.sh`、`test_upload_progress.sh` 均显示 PASS，`test_upload_verification.sh` 的新增 6/6 用例在另一单独调用中 PASS。**不能把这次串行超时当成已完整跑通**；后续应使用每套 15–30 秒上限的独立命令重复验证，并排查 Mac 节点的间歇性命令延迟。
- **2026-11-13 22:58 - P3 预检**：修复 Mac 误选 Linux/Windows 二进制逻辑，增加无账号平台格式回归；Mac Mach-O/不兼容格式选择测试 PASS，原有 `audit` 回归和 `git diff --check` 也通过。
- **2026-11-13 22:59 - P3 预检**：下载并核对 gotohp v0.11.0 原生 Universal CLI，与发布 SHA-256 相符；只检查 `file`、`codesign`，没有授予可执行权限、没有实际执行、不接触凭据。上游启动器仍未部署。
- **2026-11-13 23:15 - P3 完成**：在 macOS `sandbox-exec` 禁用网络、临时 HOME 条件下执行 gotohp CLI 的 `--help` 与 `--version`，确认 `gotohp v0.11.0` 正常。未添加 Google 凭据或上传文件；检查 CLI 默认并发 3，`--saver`、`--use-quota` 为独立配置开关。
- **2026-11-13 23:17 - P3 完成**：通过 Homebrew 官方 fswatch formula 安装 `fswatch 1.22.0`，只安装监控依赖，未加载任何 LaunchAgent 或启动监控。
- **2026-11-13 23:22 - 文档**：README 增加原作者及上游 GitHub 地址、当前变体、精确基线版本、gotohp 引擎链接、使用边界与单一计划入口；独立测试 Google 账号会在将来由用户自行添加，当前不读取、不使用现有小米 3 Cookie。
- **2026-11-13 23:23 - P4 mock**：新增 `tests/test_mock_upload.sh`；通过覆写 shell 内 `run_gotohp`，全程没有调用真实引擎、没有网络访问，SQLite 使用独立临时目录。实测「进程退出 23 但 JSON 带 mediaKey」不会登记成功、会进入 retry_queue；下一次模拟 exit 0 且返回 mediaKey，会登记到 uploads、清空 retry；再扫描同一文件保持去重。测试 PASS。该结果只说明本地状态逻辑，**不代表 Google 云端或配额已验证**。
- **2026-11-13 23:24 - 复核**：完整保留上游 44 条历史提交；安全白名单的 9 个离线测试套件分别通过（不运行上游认证/进程终止测试）；`git diff --check` 通过。提交前只暂存 10 个源码、测试、文档文件，无 Cookie、`gotohp.config`、数据库、照片、`.codegraph` 或 Mac 下载的二进制。
- **2026-11-13 23:26 - GitHub 发布**：本地提交 `6dc381fd79678b68f0bacb9ce828b25c55ecc678` 成功非强制推送至 `https://github.com/samni728/GoogleUnlimited` 的 `main`。通过 GitHub 再次读取远端分支 SHA、README 及本 docs 验证均正常；README 已注明 `https://github.com/shishir0x/GoogleUnlimited` 为原项目。发布内容是**离线测试可用的开发基线**，不是 Google Photos 真实账号上线验收。

## 9. 独立 WebUI：2026-11-14 首期开发

> 当前阶段：已实现并完成 9 项 Python 无凭据测试；本机 127.0.0.1 HTTP 验证和 GitHub CI 结果以最新验收记录为准。本节是后续接手 WebUI 工作的主入口。

### 9.1 设计与访问范围

- 技术栈：Python 3 标准库 + SQLite + 原生 HTML/CSS/JS；无新的外部 Python 依赖。入口 webui/server.py，前端 webui/index.html、webui/style.css、webui/app.js；测试 tests/test_webui.py。
- 仅绑定本机 127.0.0.1:8796；浏览器访问 http://127.0.0.1:8796。所有写入 API 需要随机 CSRF 令牌及同源校验，静态页面限制 CSP 和 iframe。
- 所有账号凭据、SQLite 队列只位于 webui/state/（Git 忽略，目录 700、数据库 600）；不得共用旧 google-unlimited.sh 的 gphotos.db 或 MI3MountMac 的任何目录、数据库或任务状态。
- 浏览范围仅为用户 HOME 和 /Volumes；隐藏项、符号链接和 MI3MountMac 程序数据目录受限制。用户手动指定文件夹或单文件，添加时触发只读扫描（照片/视频至少 10KB），开启“目录监控”后每 30 秒轮询新文件。
- 记录 SHA-256 摘要、文件大小及修改时间；无变化则直接跳过重复 hash。跨目录内容相同的项目仅在已有同内容 API 接收成功后才标记 duplicate。目录禁用会暂停相关队列任务；**“删除显示”只停用来源映射并隐藏页面，原 SQLite 上传队列/状态原样保留**。详见最新 9.6 节。
- SQLite 任务状态：queued、uploading、retrying、failed、paused、api_accepted、duplicate。失败最多三次指数退避后进入 failed，用户可手动重新入队。重启后队列持久化，但 **监控与上传开关均默认为 OFF**。
- 显示的总体百分比 = (api_accepted + duplicate) / 已登记任务 × 100；属于本地任务处理率，**不是 Google 云端独立核验百分比**。单文件真实网络字节百分比未获得可靠的 gotohp CLI 来源，因此显示“实际字节比例未知”，不得虚构具体数字。
- 启用上传的三重条件：启动参数 --allow-real-upload、webui/state/gotohp.config 独立账号凭据文件存在且权限 600、用户在 WebUI 手动打开上传开关；Mac CLI 需符合固定 SHA-256。启动时默认 OFF，避免恢复过程突然上传。
- 真正上传使用 gotohp --saver（Storage Saver），不传 --delete、--force。只有 CLI 成功返回 mediaKey，才登记 api_accepted。尚无自动官方云端二次验证、配额核对，也没有证据表明无限容量政策得到服务器支持。
- 单次目录扫描限制 10,000 个文件，避免误选 HOME 根目录而无限扫描。对大量外接媒体继续优化性能、目录树浏览和精细网络进度。
- 运行进程和 webui/state/ 都与 Xiaomi 3 现有备份服务隔离；WebUI 没有任何 MI3MountMac 导入或修改代码。

### 9.2 本机启动、离线验收

在本项目目录：

    python3 webui/server.py --port 8796
    python3 -m unittest discover -s tests -p test_webui.py -v

只在未来准备测试**另一个** Google 账号时，通过用户自己的 Mac 终端加入 webui/state/gotohp.config，并显式重启 WebUI --allow-real-upload。不得发送 Cookie 到聊天或服务器，不得将账户资料提交到 GitHub。正式批量使用前先选 1–3 张测试图，核对 Google Photos 实际出现及账号存储变化。浏览器上点击监控或扫描绝不代表已授权上传。

### 9.3 预期验收与未完成项

- 已有本机自动测试覆盖：默认双开关关闭、目录/单文件、文件修改、同内容去重、失败→重试、暂停/移除不删除源文件、无凭据重启保留队列、浏览范围与隐藏文件、HTTP 同源 CSRF。
- 需要真实账号后才能验证：Gotohp 通过独立账号上传、网络吞吐、API 接收→Google Photos 官方页面独立核验、配额前后变化；不能用 mock 状态替代。
- WebUI 当前以本机目录树选择为主，不是把 Android、NAS 映射为新文件系统。外接盘必须先在 Mac 挂载。
- 若需要让远端其它设备访问，必须先增加身份鉴权与 HTTPS，并审计安全后才允许代理；本版本禁止把 127.0.0.1 服务直接公开。
- 后续如要与 MI3MountMac UI 合并，应另设计可撤回的适配器、独立 DB 与来源标记，不得直接共用原有批次状态。

### 9.4 本轮实现与实际验收记录

- 本轮开发任务 ID：tsk_9cf6278b7075ed34。目标仓库 samni728/GoogleUnlimited，现有小米 3/MI3MountMac 服务未操作。
- 已实现 Python 本地 API/SQLite 后端、浏览器目录/单文件选择、目录监控开关、独立上传开关、扫描与 SHA-256 去重、修改时间缓存、失败最多 3 次指数退避重试、任务列表和总处理率、来源暂停/移除不删真实文件。
- Python 自动测试通过 9/9，覆盖文件夹、单文件、内容修改、跨目录同内容去重、重试恢复、服务重启持久化、来源暂停删除、路径保护、HTTP/CSRF、无账号拒绝上传。
- Mac 端 8787 端口已被现有服务占用且返回 401；不强行关闭它，也不复用端口，改用独立 127.0.0.1:8796。
- 已在 Mac M2 使用默认安全配置启动本机 WebUI，直接 HTTP 实测 /、/app.js、/style.css、/api/status、/api/jobs、/api/browse 均返回 200。WebUI 监控 OFF、上传 OFF、无来源；独立 WebUI 服务在 webui/state/server.pid 内记录进程号。服务默认仅本机可访问，不自动后台永久托管。
- **远端 CI 已验收通过**：GitHub Actions https://github.com/samni728/GoogleUnlimited/actions/runs/43574573385 ，对应提交 da8a3b4ed06f27a11e8b42fbb82307f21662349b；静态检查、macOS、Linux 和 Windows 测试全部 success，且 macOS/Linux 已执行新增 Python WebUI 单测。仅表示开发基线通过，不代表真实 Google Photos 上传或免配额通过。
- 安全说明：上游 CLI 目前没有可靠的单文件传输字节 API，本版本不输出假百分比；任何 API 接收状态也不代表 Google 官方云端独立核验。P5 独立账号真实上传、存储配额和正式生产链路切换仍为后续阶段。
- 严禁将 webui/state/ 下的队列/账号凭据/PID/log、Python 缓存、测试用户目录内容提交到 GitHub。README 已加入本地启动命令、独立账号授权示例和风险说明。

### 9.5 网络共享挂载与可恢复 SQLite 媒体索引（2026-11-14）

> NexusDock 任务 ID：`tsk_6c3a5c2675bfaefd`。本阶段继续在唯一仓库与本文更新，不新建独立项目或进度副本。

**WebUI 新入口：** 在“添加监控来源”中切换“本机 / 外接硬盘”和“网络共享 / NAS”。`GET /api/volumes` 从 macOS 的挂载表识别 SMBFS、NFS、AFP、WebDAV、SSHFS/MacFUSE 等已挂载网络卷，列出可选择的挂载点。需要连接新的 SMB 共享时，在页面输入不含用户名或密码的 `smb://host/share`，由 `POST /api/share/connect` 请求 Finder 打开并由 macOS 弹窗完成登录；只有刷新挂载卷列表后才允许选择实际挂载成功的路径。Mac M2 本轮实际测试时无已挂载的 SMB/NFS 网络卷（`/api/volumes` 返回 network 0）；**模拟连接测试通过不等于已验证某台真实 NAS 成功挂载。** 本地或网络盘都由 Mac 读取，不映射到小米 3。

**持久化状态结构：**

- `sources`：持久保存来源 ID、目录/单文件、原始路径、本地或网络卷、挂载根目录、可用性、归档状态、是否监控；“移除”现在是 `archived=1` 的逻辑归档，不删除该来源的任务与目录索引。同一来源恢复使用原 ID。
- `jobs`：按源文件路径登记大小、照片/视频类别、原扩展名、SHA-256、修改/更改时间、首次/最近发现时间、文件可访问性、去重引用、入队/重试/失败/API 接收状态和云端人工核验方法及时间。SQLite 内容不取决于源文件当前是否在线；文件丢失或共享断开只更新可用性，历史上传状态不清零。
- `folder_index`：按来源 ID + 每个子目录保存汇总（媒体数、照片数、视频数、字节数、API 接收数、人工云端核验数、失败数、不可访问数）。`GET /api/folders?source_id=...` 可直接从数据库展示历史，不必重新读取 80+GB 的文件。
- `/api/jobs`：支持来源、照片/视频、任务状态过滤及 `limit/offset` 分页，WebUI 每页 100 项；不再只能查看最早限制的部分数据。云端“人工已核验”只能由用户先去 Google Photos 官方页面核对后手动标记，不能冒充 Google 官方自动核验或无限容量。
- 服务启动时 `uploading` 的任务变为 `interrupted`（结果未知，待人工核对后可重新入队），**不会自动重复上传**。监控和上传开关始终默认 OFF；网络盘离线时任务与文件摘要继续保存在本机数据库，并被排除自动上传。再次挂载后需要扫描才更新文件可用性。
- 测试与状态库在 `webui/state/`（已 Git 忽略）；禁止将 SQLite、共享密码、Google 账号 Cookie、照片或日志提交 GitHub。任何时候“移除来源”均不删除真实照片。

**发现并修复的旧数据问题：** 实际 Mac M2 的 `webui/state/queue.sqlite3` 最初有 586 个媒体任务，但 `sources` 表为 0 行，是旧版直接删除来源造成的孤立任务。按历史媒体路径共同前缀恢复了来源 ID 1：`/Volumes/WIN10/photo`，**归档且禁用**；恢复后共 9 个目录、586 个任务（266 张照片、320 段视频），总索引 84,966,018,121 字节（约 84.97 GB），其中 564 项 queued、22 项 paused、0 项 API 接收、0 项人工云端核验。数据库迁移前通过 SQLite 官方备份接口保存一份本地回滚快照 `webui/state/queue.pre-orphan-recovery.sqlite3`。**恢复只修改元数据，没有启动监控、上传或重新扫描这 84.97 GB 文件**，与 MI3MountMac 完全隔离。

**验证范围：** Python WebUI 回归从原有 9 项扩展到 17 项，已通过旧 SQLite schema 迁移、删除来源后的孤立任务恢复、断线/重连、图片与视频目录索引、服务中断后禁止盲重传、人工云端核验、路径保护、网络卷解析和拒绝带凭据的 SMB URL。HTTP 路径和分页的最终部署验证、GitHub 推送及 CI 以本节后续补充的时间戳记录为准。CodeGraph MCP 在该时段连接失败，因此 Bash/Python/JS 变更由直接源码读取、静态检查和可重复运行的测试核验。

**后续真实 NAS 验收：** 将某个 SMB/NFS 远程目录挂载到 Mac 的 `/Volumes/...`，核对目录、图片/视频数量、断线/重连后的索引保持情况；只用另一个独立 Google 账号测试少量非隐私样本的真实上传，并在官方 Google Photos 页面及配额中核对，不能预设真实账号无限容量。

**2026-11-14 00:51 本机最终验收：** macOS Python 17 项单测全部 PASS；Python 编译、Node.js JS 语法和 `git diff --check` 均通过。服务仅通过验证过的自身 PID 进行重启；`127.0.0.1:8796` 返回 HTTP 200，`/api/status` 586 条历史任务、来源 ID 1 归档禁用；`/api/jobs?limit=100&offset=100` 返回 100/586，`/api/jobs?source_id=1&kind=video&limit=100&offset=300` 返回 20/320；`/api/folders?source_id=1` 返回 9 个目录；`/api/volumes` 返回 200、当前网络共享数 0。新版 HTML 含网络盘入口和分页控件，JS 已加载；监控 OFF / 上传 OFF，没有真实 Google 请求、没有改动 MI3MountMac。GitHub 远端的新版 CI/提交必须另行核验。

**2026-11-14 00:55 GitHub 验收：** 源码提交 `be9f1c795259a7740421ac636c4f6f3eac4547f5` 已非强制推送到 `samni728/GoogleUnlimited` 的 `main`，并通过 GitHub 重新读取 SHA、README/docs 验证。GitHub Actions [43577910219](https://github.com/samni728/GoogleUnlimited/actions/runs/43577910219) 已完成且结论 **success**：Lint & Static Analysis、macOS Regression、Linux Regression、Windows Regression 四组全部通过。该结论仅覆盖软件静态与无凭据回归测试，**未覆盖真实 NAS 的 Finder 授权挂载，也未覆盖任何 Google Photos 实际上传或云端配额核对**。

### 9.6 WebUI 清爽展示、软隐藏与界面移除（2026-11-14）

> 本轮任务：`tsk_054181dd504c42d9`。用户要求来源和历史索引可保留、隐藏或“删除显示”，但 SQLite 里的媒体、目录、SHA-256、上传结果和人工核验状态**永久保留**；重新添加相同的共享目录时自动恢复原来的历史和实际进度。

- **页面默认简洁**：`#index-details`（目录索引）和 `#jobs-details`（媒体任务明细）使用原生 HTML `details`，默认收起；用户点击展开时才展示详细数据，任务详情在收起时不会轮询拉取。首次访问只显示简洁的来源卡片、摘要和状态，不再自动排出九层目录和 100 条文件。
- **三种显示方式（仅 WebUI 管理状态）**：
  - 保留 `visible`：来源卡片与其备份统计正常显示。
  - 隐藏 `hidden`：卡片、对应目录明细和任务从常规界面及界面汇总数字中消失，**监控配置和 SQLite 原任务状态保持不变**。
  - 删除显示 `removed`：卡片和相应明细从常规页面消失，来源映射标记为停用/归档，**不对 jobs/folder_index 执行 DELETE，不重置成功状态，不删除任何真实文件**；即使历史卡片原本已归档，亦可单独执行。
- **历史显隐**：`显示隐藏记录` 按钮切换是否查看 `hidden` / `removed` 的来源及其 SQLite 历史；每个来源提供显示操作和可展开的“操作记录”。请求需 `/api/status?include_hidden=1`、`/api/jobs?include_hidden=1`、`/api/folders?include_hidden=1` 才看到隐藏内容；常规 API 不主动返回其目录/任务记录。
- **数据库增量迁移**：`sources.ui_visibility TEXT DEFAULT 'visible'` 保留旧表所有来源、文件和历史。新增 `source_events` 记录每次添加、隐藏、移除和重新加载的动作及时间戳，避免将“删除显示”误认为已经删除数据库。所有显示操作修改只限 `sources` 显示/映射状态及新审计表，不修改原有 `jobs` 的 `queued` / `paused` / `api_accepted` / 人工核验记录。
- **重新挂载同一路径**：`POST /api/sources` 的唯一键为解析后的绝对路径，找到既有来源时**沿用原 source_id**，恢复 UI 显示和来源映射，仅写审计记录，不清除原任务状态、不立即重扫或重新 hash 大批文件。后续显式“扫描”或启用监控才检查磁盘变化；新来源保持原有首次扫描逻辑。注意目前只有**同一路径**匹配，不声称网络盘换名/换挂载点后也能自动匹配。
- **验证**：Python 20 项无 Google 凭据测试（旧 17 项 + 隐藏保留状态、删除显示后重新加载和非法删除请求），同时新增 HTTP CSRF+显示/隐藏过滤回归，实际 Mac 运行验证与 CI 以本节后面的结果为准。
- **安全**：从不接触现有 MI3MountMac 应用或其数据库；所有账号凭据、SQLite 数据与备份快照继续在本地 `webui/state/` 且被 Git 忽略。Google Photos 云端自动核验/免费配额尚未验证。
- **2026-11-14 06:43 本机实测**：Mac M2 的 Python 20 项测试全部 PASS；`python3 -m py_compile`、`node --check webui/app.js` 和 `git diff --check` 均通过。修改的只有本项目源码/测试/文档；重启前已由 SQLite 官方备份方法保存 `webui/state/queue.pre-display-visibility-20261114.sqlite3`，不提交 Git。
- **2026-11-14 06:44 WebUI 真实服务验收**：只重启经 PID+本机端口确认属于 GoogleUnlimited 的 `8796` 服务；`http://127.0.0.1:8796` HTTP 200，首页检查到目录/文件两个 `<details>` 默认关闭、隐藏记录按钮和保留数量提示；`/api/status`、`/api/status?include_hidden=1`、`/api/jobs`、`/api/source/events?source_id=1` 返回有效数据。生产 SQLite 原始历史完全未变：来源 ID 1 `/Volumes/WIN10/photo` 仍显示为已归档（archived=1，enabled=0，ui_visibility=visible），媒体 586、目录 9，`queued=564`、`paused=22`，没有 Google API 成功/云端人工核验记录，监控/上传 OFF。**没有替用户自动点击隐藏/删除。**
- **后续真实行为**：用户在 WebUI 手动点“隐藏”或“删除显示”，正常页面就不显示该来源/对应明细，按钮“显示隐藏记录”仍可检查并恢复；再重新添加同一挂载路径即复用旧数据库索引和任务状态，不自动重新 hash 整个目录。已补充浏览器端清理：即使在操作前用户展开过该来源的目录或文件列表，也会立即关闭并清空当前页面的明细 DOM，避免残留显示隐藏来源的数据；SQLite 仍不变。

### 9.7 来源卡片纵排 UI 回归修复（2026-11-14）

- **用户截图证据**：来源卡片内 `/Volumes/WIN10/photo` 的路径与“已索引 586 项”等摘要被挤成逐字竖排，产生超高白色卡片。上版只进行了 API/单元验收、没有实际 CSS 布局验收，是疏漏。
- **根因**：`.source-entry` 使用 `display:flex`，却把查看索引、恢复、隐藏、删除按钮与具有 `flex-basis:100%` 的操作记录 `details` 全部作为同级 flex 子元素。在容器宽度有限时，`.source-meta` 被压缩为几像素，导致 CJK 与英文字母按字逐行换行。
- **修复**：`webui/app.js` 将全部操作按钮装入独立 `.source-controls` 容器，`webui/style.css` 将来源卡片改成 `42px minmax(0, 1fr) auto` 三列 CSS Grid，目录与状态摘要添加单行省略/最小宽度约束；操作记录置于单独 grid 行。视口小于 1110px 时按钮移到第二行；小于 540px 时按钮和操作记录横跨整卡，避免手机端压缩文字。
- **真实环境与视觉验收边界**：Mac M2 Google Chrome 的 headless/CDP 截图因 macOS `CVDisplayLinkCreateWithCGDisplay failed` 无法启动；因此**不能宣称已经拿到 Mac M2 实际页面截图**。改用独立 Linux Chromium 渲染同结构/CSS 的来源卡片复现，检查 1440/900/390px 三种视口，三组截图均肉眼复核无竖排：来源卡高度 128/178/178px，文字列宽约 768/761/257px，标题高度均 16px。Chromium 复现图**不是实际 Mac 页面截图**。
- **测试保护**：`tests/test_webui.py::test_source_card_layout_regression_guard` 检查按钮容器与 Grid 关键定义，不再只用 API 测试判断 UI 健康；Mac M2 本机 Python 21 项测试通过，JS/Python 语法及 `git diff --check` 通过。正式访问 `http://127.0.0.1:8796` 需采用浏览器强制刷新以排除旧 CSS 缓存；服务使用 `no-store`。
- **数据安全**：本轮只修改前端 HTML/CSS/JS、无凭据测试和本文，不更改 `webui/state/` 的历史 SQLite；既有 586 个媒体任务及 9 个目录、Google 上传和 MI3MountMac 逻辑不受影响。

### 9.8 Google Photos 相册参数和独立账号首次试传（2026-11-14）

> NexusDock 任务 `tsk_7acc76dcc45f3140`；限定在 mac-M2、项目 `/Users/samni/Desktop/开发项目/GoogleUnlimited`，沿用唯一 docs 和 SQLite，不迁移小米 3/MI3MountMac 业务。

**第一手 CLI 验证**：本机 `gotohp-cli-macos-universal upload --help` 对 v0.11.0 明确列出 `-a, --album string add uploads to album (creates if needed); 'AUTO' creates albums from folder names`，同时支持 `--saver`。因此按**固定相册名**上传是上游已有的能力，不必自行反向实现 Google Photos Album API。应用对每个文件调用 `gotohp-cli ... upload --no-tui --saver --album <已确认名称> <文件路径>`。该参数语义只根据 gotohp 命令帮助验证；真正的云端相册归属尚需有凭据后检查。

**WebUI / SQLite 设计与防误上传**：

- `sources.album_name`、`album_confirmed`、`album_confirmed_at` 持久化为每个来源的固定相册名、明确确认标记和确认时间。历史来源的缺省建议为文件夹最后一层名字，**确认标志初始为 0**；`set_album` 严格校验长度和控制字符，上传开关打开或该来源存在上传中任务时禁止改名；更改/确认事件写入 `source_events`。
- 来源卡片增加默认收起的“相册名称与单张试传”。展开可编辑名称，点击“保存并确认相册”后才可能上传；不变的历史上传状态不被清零。重挂载同一路径按原 `source_id` 复用原来的相册确认和媒体任务。
- 后台 `_next_job` 筛选仅允许 `enabled=1,archived=0,availability=online,album_confirmed=1` 的来源。即便有人打开全局上传开关，未确认来源绝不自动上传。单张试传需用户在 WebUI 再次主动确认，确保本机独立凭据存在、CLI 哈希与路径验签成功、进程以 `--allow-real-upload` 启动且全局批量上传仍为 OFF；只挑本来源最小的可用未上传照片，上传 1 次，不会执行 217 张/视频的批次。
- 提交上传后的 `api_accepted` 只是 gotohp/Google 非官方 API 的接受证据，不等于相册在 Google 官方界面实际存在或云端配额零增长。每次真实测试都应去 Google Photos 网页查看目标相册里的测试媒体、所属账号和存储使用量。更改相册名不会自动搬移以前上传的文件；SQLite 的全局内容去重也未确保所有重复文件自动关联到不同相册，这部分属于后续专项改造，不可冒称已完成。
- 本项目`webui/state/gotohp.config` 不与 MI3MountMac/小米 3 共用账号。Cookie/Token **不得经过 NexusDock 远程工具、聊天、WebUI、GitHub 提交或公开服务器**。

**Chrome Profile 8pndk8ba 安全手动授权步骤**（对应 README 新章节）：

1. 用户本人切换 Mac Chrome `8pndk8ba`，在该 Chrome 里打开 `https://accounts.google.com/EmbeddedSetup`，核对是本轮**另一测试 Google 账号**并完成同意。
2. Chrome DevTools → Application → Cookies → `https://accounts.google.com`，**仅复制 Embedded Setup 产生的单个 `oauth_token` 的 Value**；不导出整个 Google 账号 Cookie 数据库，不复制主 Token；该 Cookie 可能短期有效，马上使用。
3. 在物理 Mac M2 的 Terminal **本人运行** `cd "/Users/samni/Desktop/开发项目/GoogleUnlimited" && bash scripts/authorize_local.sh`。脚本检查 TTY，用不可见提示接收临时 token，并通过 STDIN 调用独立 gotohp `creds add -`；持久凭据只写项目 Git 忽略的 `webui/state/gotohp.config`，权限 0600，不会把凭据放在 argv 或远程命令里。
4. 用户本人可运行 `gotohp-cli-macos-universal --config ... creds list` 检查独立账号邮箱是否正确，然后以 `--allow-real-upload` 安全重启本项目服务（默认上传仍关闭）。先确认对应来源相册，再测试**一张图片**；完成官方网页的云端与配额核验之前，绝不整批开启。

**指定来源**：WebUI 来源 ID 2，实际路径为 `/Volumes/WIN10/photo/2020@11月行程 南宁武汉贵州长白山 已nas`，在当前 SQLite 中已有 217 条 `queued` 记录，目录可访问。用户拟定云端相册名称为 **`2020@11月行程 南宁武汉贵州长白山`**（不带磁盘目录的“已nas”后缀）。本轮仅预填 SQLite 相册建议，保持 `album_confirmed=0`，等待用户本人在 WebUI 点击确认。项目独立 `gotohp.config` 此前不存在；因此无法执行真实 Google Photos 上传/配额核查，不能谎称已完成第一波云端上传。

**本机实测（2026-11-14）**：Python 24 项测试全部通过（较前次新增 3 项相册相关测试；HTTP/API 验证也覆盖无凭据拒绝试传和确认保存），JS/Python/shell 语法及 `git diff --check` 通过。初始生产 DB 在更改相册设置前使用 SQLite `backup()` 备份到 `webui/state/queue.pre-album-20261114.sqlite3`，不提交 Git。只重启 Mac M2 上经进程名和端口核验的 GoogleUnlimited 8796 服务；现场 HTTP 200，上传/目录监控 OFF，`allow_real_upload=false`，独立凭据文件不存在。针对目标来源 ID 2 预填 `2020@11月行程 南宁武汉贵州长白山`、`album_confirmed=0`，原任务状态严格不变：来源 1 有 369 项 queued，来源 2 有 217 项 queued，合计 586 项；目录和照片均未删除。首个安全单图候选为来源 2 中约 3,763,142 字节的 JPG（仅核对存在和大小，不上传）。第一波“试传 1 张照片”目前**只完成模拟上传测试，真实试传受独立账号授权缺失阻断**；本地 Chrome Profile 登录不能自动替代 gotohp 认证。后续用户本人运行 `bash scripts/authorize_local.sh`，确认正确账号后再使用 WebUI 的相册确认与单张上传按钮。跨平台 GitHub CI 最终已验收：源码提交 `c0dba3d` 最初的 Linux/macOS 测试因 Runner 没有本地 gotohp 二进制而触发模拟测试门禁（非真实上传错误）；修正测试改为创建独立假 CLI 文件，生产安全门禁不变。补丁提交 `ae5cb6b8358e710043c81ffd5e094f8bf7ce329e` 已推送主分支，GitHub Actions [43610882190](https://github.com/samni728/GoogleUnlimited/actions/runs/43610882190) 的 Lint、macOS、Linux 和 Windows 四组测试全部 **success**。独立 Google 凭据尚未建立，首批真实上传仍为等待用户本机授权。

### 9.9 自主构建 Mac 直传引擎（2026-11-14）

**目标**：把 GoogleUnlimited 现有自研 WebUI/SQLite/NAS/相册管理与第三方预编译 CLI 解耦，用自己固定版本源码、自己编译的 Go 程序，在 Mac M2 上直接发送照片到 Google Photos。**不能**因为小米 3 已获得免配额，就自动假设 Mac 同一账号直接上传也免费；须以实际云端用量验证。

- **上游来源核验**：gotohp v0.11.0，Git tag `68ad61fc844a060ce0e09c0b0524dd799c14170c`，MIT LICENSE Copyright 2024 xob0t。上游 CLI 通过 `go build -tags cli` 构建，核心 Go 源码目录为 `backend/`、`generated/`、`internal/`。固定约 50 个 CLI 必要源码/资源文件到 `engine/gotohp-src/`（约 721KB），不提交临时克隆目录的 .git、前端 GUI 或构建产物。源码仍是第三方作者的代码；我们的自主性在于本地保存、持续审查、修改、编译、运行及完全控制队列、日志、账号、服务器，**不是宣称所有上游代码原创**。
- **部分静态安全检查**：核心代码可见的 HTTP 访问位置包括 `android.clients.google.com/auth`、`android.googleapis.com/auth`、`photos.googleapis.com`、`photosdata-pa.googleapis.com`，这只是字符串级检查，不代表完整依赖/动态网络流量审计、也不保证不会外泄。编译和源码审查不读取用户 Google Cookie、MasterToken 或 Android AccountManager 数据。
- **构建**：`scripts/build_local_uploader.sh` 在 Mac ARM64 核对源版本和 MIT 声明、使用 `CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -tags cli -trimpath -buildvcs=false`。上游 go.mod 要求 Go 1.27；Mac M2 原来安装 Go 1.25.6，因此需要下载 Go 1.27 工具链和 Go 依赖，编译结果视真实测试而定。输出 `engine/bin/gotohp-cli-owned-arm64` + `engine/bin/owned-build.json`，两者被 Git 忽略，清单存源版本、源文件集合摘要、二进制 SHA256、平台与 Go 版本，不存用户账号信息。
- **运行时校验**：`engine/verify_local_build.py` 通过标准库重新计算源码树与本地二进制的 SHA-256，拒绝替换文件、软链接、版本不匹配和编译清单失效。WebUI 新增 `--engine owned|release`；`owned` 必须使用固定目录的已验证本地产物，`release` 只接受之前固定 SHA-256 的发布版，选择不成功时不会静默回退；更换引擎需重启 WebUI，**真实上传开关重启后始终 OFF**。上传仍需独立账号、启动参数 `--allow-real-upload`、来源相册人工确认。
- **本地授权**：`scripts/authorize_local.sh` 已改为只调用通过校验的本地编译 CLI，禁止脚本从远程 NexusDock 导入/上传 Cookie。原手工授权流程保留，但不意味着自动实现“小米 3 签名设备身份”的迁移；未来如需 Android 端授权桥接，应先论证设备绑定/可撤销性，明确密钥不得写入仓库/日志，不能以伪造 User-Agent 代替服务器认可。
- **第一张照片验证流程**：先独立授权账号，再从已确认相册的来源主动点击单张测试，分别记录 `mac_api_accepted`、`cloud_visible` 和 `storage_usage_delta` 三个独立指标；任何一个失败，保留原文件和队列，不开启大批量。用户此前验证小米 3 多 GB Storage Saver 不占配额，作为**手机端参考对照，不代表 Mac 端得到相同待遇**。
- **持续隔离**：不修改 MI3MountMac、Pixelify、手机当前 Google Photos 或任何既有设备凭据；不复用小米 3 的数据库；原 586 条媒体、NAS 子目录、相册和历史记录保持完整。真实构建/本机 HTTP/CI 状态应记录在本节追加验收行，不凭代码存在宣称成功。

**2026-11-14 本机编译与隔离部署完成：** 已从上游 v0.11.0 提交 `68ad61fc844a060ce0e09c0b0524dd799c14170c` 的固定 Go 源码独立编译 Mac ARM64 CLI，使用 Go **1.27.0**，`--version` 输出 `gotohp v0.11.0`，支持 `--saver` 与 `--album`。本机二进制 SHA-256：`13a342126a28930a1a503b2d9b6dba0b0fccd758123d9783edbe8c9b22628d7d`，`engine/verify_local_build.py` 实测 **PASS**（源码与二进制均校验）；构建产物与 manifest 在 `engine/bin/`，被 Git 忽略。项目增加 6 个纯离线 Python 校验测试，加上原 WebUI 测试总计 **31 项全部通过**，并把这组测试加入 Linux/macOS CI。上传引擎以 `--engine owned` 显式选择，默认认证和上传门禁维持。

**Mac M2 WebUI 实例验收：** 重启前对运行于 `127.0.0.1:8796` 的已确认本项目进程检查监控/上传双 OFF，使用 SQLite 原生备份生成 `webui/state/queue.pre-owned-engine-20261114.sqlite3`；重启后以 `python3 webui/server.py --port 8796 --engine owned` 运行。HTTP **200**，API 返回 `upload_engine=owned`、`owned_build_verified=true`，监控/上传/允许真实上传全部 false；SQLite `queued=586`、`folder_index=9`、来源数 2、相册设置均与重启前严格一致。该部署尚无独立账号凭据，**未进行 Google Photos 真实上传，也未证明 Mac 继承小米 3 的 Pixel 免费容量政策**。

**设备只读对照：** 小米 3 当前 ADB 可连，`ro.build.version.release=11`，`ro.product.model=MI 3W`；因此应用内 Pixelify 身份模拟未体现在此全局属性。**复制 Android 系统型号或 gotohp 里 Pixel XL 的 User-Agent，均不能替代云端对实际备份来源及资格的判断。** 目前没有提取、复制、储存或通过 NexusDock 传输手机 Google AccountManager/Photos 的任何认证凭据。

**Go CLI 单元测试验收边界**：额外尝试在临时 HOME 中执行 `go test -tags cli -count=1 ./backend ./internal/cli`，因第一次完整测试构建大型依赖触发执行超时，未形成成功结果；本机 `go build -tags cli` 已成功，二进制版本和源码 SHA 校验也通过。**不能把超时的 Go 测试写成通过。** GitHub 端只运行不需要 Google 凭据的 Python/接口安全测试，源码构建结果以本机真实编译为证。

**仍待完成的关键验收**：在用户自己批准并完成本机安全凭据配置后，从 Mac 自编译 CLI 指定相册主动上传一张照片，核查官方相册出现与相同 Google 账号的实际存储用量前后差（留出统计延迟）；若容量增加或认证失败，就不能宣称已实现 Mac 免费无限备份。未来可以研究手机授权桥接或独立认证模块，但需确认 Android 硬件绑定/签名能力与安全性，不能仅复制可见型号。Go 源码来自 MIT 上游，不能以“自己编译”代替完整的安全审计。

**GitHub 核验（源码提交 `b97028b1ed678862e13752443da310e83065c49a`）**：CI [43619202820](https://github.com/samni728/GoogleUnlimited/actions/runs/43619202820) 在 Linux、macOS、Windows 和 Lint 四组均 **success**。GitHub main 远端已重新读取到固定的上游源码修订和 MIT LICENSE，证明代码同步成功；编译后的本机可执行文件、Google 凭据和 SQLite 从未提交。由于账号与手机设备绑定机制未验证，真实上传和免费配额仍是**未完成验收项**。

**后续 WebUI 小修正**：安全状态从三项扩展为四项后，将 `security-grid` 桌面布局调整为两列两行，避免第四项单独落行，并添加无浏览器凭据的前端布局回归断言。该更新只涉及 CSS、测试及这份 docs，不触碰队列/相册/上传路径。

### 9.10 Chrome Profile 8pndk8ba 与小米 3 首次联合预检（2026-11-15）

> 本次用户明确要求使用 Mac M2 本机的 Chrome Profile `8pndk8ba` 与小米 3 完成 Mac 自编译 gotohp 直传验证。联合测试只允许可审核的本机只读验证和凭据已合法准备时的**单张试传**，不能把 Chrome 登录或手机系统备份资格直接当成 Mac CLI 凭据。任务：`tsk_e6fc582279e22990`。

**实际已核验（不读取账号密钥）**：

- **Chrome**：`opencli profile list` 明确报告 `8pndk8ba — connected v1.0.27`（另一个 `e7jvevtb` Profile 单独存在，不混用）；使用 `--profile 8pndk8ba` 在独立浏览器会话打开 `https://photos.google.com/` 成功，标题 `Photos - Google Photos`，页面有账号控件且未显示登录提示。读取该 Profile 的 `https://photos.google.com/quotamanagement`，页面标题 `Manage storage - Google Photos`，显示 **88.8 MB / 15 GB 已用、14.9 GB 剩余**；这是 Chrome 账号的上传前参考基线，不代表小米 3 账号，也不能单靠这一时点证实未来免配额。
- **Android**：ADB 设备 `bb4b70c` 处于 `device` 状态，小米 3 安装了 `com.google.android.apps.photos`。在 Mac 本机内存中对 Chrome 当前页面控件的邮箱标识与 Android AccountManager 的 `com.google` 账号标识进行**仅相等性比较**：Chrome 找到 1 个账号标识，Android 找到 1 个 Google 系统账号标识，结果 **不匹配**。此次没有把任一邮箱、Cookie、Master Token 或 Android 验证令牌输出到 Agent、日志、文档或聊天。该结果仍需注意 Chrome 网页账号与 Android 系统账号，不必然等于 Photos 应用选中的备份账号；但明确**不能假设两者是同一账户**。
- **独立 Mac 引擎**：`engine/verify_local_build.py` PASS；GoogleUnlimited WebUI `http://127.0.0.1:8796` 返回 `upload_engine=owned`、`owned_build_verified=true`、`stored_files=586`；`credentials_present=false`、`allow_real_upload=false`、`upload_enabled=false`、`monitor_enabled=false`。目标旅行相册来源 ID 2 的 `album_name=2020@11月行程 南宁武汉贵州长白山` 仍处于 `album_confirmed=0`，没有任何上传状态被改动。
- **本次真实上传状态**：**没有上传照片**；不能用浏览器现有 Google Web Session 自动代替 gotohp 的非公开 Android 风格凭据，更不能从未匹配的 Android 账号复制长期令牌来“冒充”与 Chrome 账号相同。未经可核验、可撤销的授权以及账号归属匹配，保持批量和单张上传安全阻断。

**后续判定门槛**：用户清醒时先确定 Mac 测试需要使用小米 3 当前 Google Photos 备份账号，还是有意使用 `8pndk8ba` 中的另一个测试账号；如果使用另一账号，不能套用小米 3 那个账号已经确认的零配额经验。只有账号明确且本机安全授权完成后，才能通过 WebUI 人工确认相册并执行 1 张图片的真实上传，再回到对应 Google Photos 官方页面核查相册归属与实际空间增量；全过程不重置现有 586 条任务或改变小米 3、MI3MountMac 备份状态。

### 9.11 独立 Chrome 测试账号：共享配额基线与首次试传阻断（2026-11-14 晚间）

- **用户确认账号用途**：Chrome Profile `8pndk8ba` 是**另一独立 Google 测试账号**，不要求与小米 3 的 Google 系统账号相同；前文 9.10 的“账号不匹配”只是客观比较结果，**不能作为阻止该独立账号试传的理由**。但独立账号的 Mac 上传，也不能直接证明继承小米 3 当前账号的免费备份资格。
- **纠正用量含义**：Google Photos `/quotamanagement` 显示的 **88.8 MB / 15 GB** 是该 Google 账号的**共享存储总量**（Gmail、Drive、Google Photos 等），**不是 Google Photos 自身已使用 88.8 MB**。用户确认 Google Photos 当前没有照片，因此该 88.8 MB 可能来自其他 Google 服务，具体归属未逐项核对。保留该数据仅用于单张 Mac 上传前后对比，不把它当作相册存量。
- **已主动完成的现场配置**：用户已经给定固定相册名 `2020@11月行程 南宁武汉贵州长白山`，本次通过本地 Store 的 `set_album(2, ...)` 完成确认，`sources.id=2` 的 `album_confirmed` 已由 0 改为 **1**，且其 **217 条 queued** 任务状态未变；文件未被上传。全局监控、真实上传开关仍为 OFF。
- **实际授权测试**：Mac M2 自编译 owned 引擎哈希通过，但 `webui/state/gotohp.config` **不存在**。尝试在已连接的 OpenCLI Profile `8pndk8ba` 中加载 Google `EmbeddedSetup` 登录授权页，浏览器 Bridge 的新建标签/页面状态及当前 URL 读取均发生超时，无法取得有效本机 gotohp 授权；**没有提取或输出任何 Cookie、oauth_token 或 Google Master Token**。已登录 Chrome 不等于 gotohp CLI 获得独立访问凭据，因此单张真实上传**没有发生**。不要通过无人值守命令绕过 Google 的重新登录、安全验证或用户同意。
- **只剩一个真正前置条件**：目标测试账号需在本机完成 Google Embedded Setup 授权，并仅将一次性 `oauth_token` 经**本机 Terminal** 的 `bash scripts/authorize_local.sh` 输入（不要发送给 AI/远程服务器）。确认 `gotohp.config` 建立、账号匹配之后，才以 `--engine owned --allow-real-upload` 启动项目服务（批量仍 OFF）并点击来源 2 的单张试传。最后回到**同一独立账号**的 Google Photos，核对是否显示照片和共享空间是否变化；统计可能延迟。需要有凭据才能继续，不能谎报已完成上传。

### 9.12 独立账号首张 Mac 直传与官方云端核验（2026-11-15 北京时间）

**真实验收结果：成功上传一张图片，已看到目标相册与一项媒体；共享配额前后显示一致，但免费容量资格尚需复核统计延迟。**

- **账号与凭据**：用户本人通过 Mac M2 的 `scripts/authorize_local.sh` 完成 Google Embedded Setup 临时凭据交换，本机 `webui/state/gotohp.config` 文件存在，权限 `0600`。CLI `creds list` 返回 1 个已选中账号，并与用户之前截图的 Chrome Profile `8pndk8ba` 账号一致（仅在本机作相等性比对；这里不记录邮箱或任何 Token）。先前“Chrome 登录不能代表 gotohp 授权”的阻断已解除。用户曾在 ChatGPT 聊天中粘贴一次性 `oauth_token`，**不在日志/代码中复制该值**；后续应避免再发送凭据，视需要在 Google 账号安全设置中结束有关登录会话。
- **源与备份**：测试来源 ID 2，NAS 路径 `/Volumes/WIN10/photo/2020@11月行程 南宁武汉贵州长白山 已nas`，相册 `2020@11月行程 南宁武汉贵州长白山` 已由用户指定并在 WebUI 明确确认为 `album_confirmed=1`。测试前使用 SQLite 原生 `backup()` 备份数据库到本机 Git 忽略的 `webui/state/queue.pre-first-owned-live-test-20261114.sqlite3`（409600 bytes），基线来源 1：369 queued、来源 2：217 queued。
- **真正执行过程**：安全重启本项目 WebUI（先确认 8796 端口与 PID 所属，命令 `python3 webui/server.py --port 8796 --engine owned --allow-real-upload`），保持全局 `upload_enabled=false`、`monitor_enabled=false`。通过带 CSRF 的 `POST /api/source/test-upload`，仅为 ID 2 选取本来源最小的一张未上传 JPEG（job ID 2，约 **3,763,142 bytes**），自编译 gotohp v0.11.0 CLI 以 `upload --no-tui --saver --album <指定相册名>` 发起真实上传，耗时 **13.7 秒**，HTTP 200，JSON 返回 `status=api_accepted`，有 44 字符长度的 provider mediaKey（**不记录其实际值**）。
- **官方 Google Photos 页面核对**：OpenCLI Browser Bridge 重启后重新连接 Profile `8pndk8ba`。官方 `https://photos.google.com/albums` 的相册列表出现本次目标相册，并显示 **1 item**；随后打开该相册的 `https://photos.google.com/album/…` 页面，标题与目标旅行相册名称一致，页面 DOM 存在照片图像内容。因此将 job ID 2 的 `cloud_verified_at` 标为已核验，方法 `manual`，不是仅凭 CLI 返回就认定云端成功。
- **存储配额实测**：试传前，Chrome 官方 `https://photos.google.com/quotamanagement` 显示 Google 全账号共享存储 **88.8 MB of 15 GB used**，Google Photos 本身据用户确认原先没有照片；试传并显示该相册 1 item 后，同一 Chrome Profile 官方页面仍显示 **88.8 MB of 15 GB used**，剩余 14.9 GB。这个数值是 Gmail/Drive/Photos **全账号共享**用量，不是照片本身用量；上传的是约 3.76 MB JPEG，故前后“显示没有增长”是有价值的**初步结果**。但 Google 可能缓存、延迟更新或随后重新核算，因此**不能据一次快照断言 Mac 将永久获得 Pixel 免费无限配额**，应稍后再核对。
- **最终安全收尾**：再次安全重启项目 8796 服务到 `--engine owned`（**没有 `--allow-real-upload` 参数**），`allow_real_upload=false`、`upload_enabled=false`、`monitor_enabled=false`，授权文件保留在 Mac 本机未提交。数据库核验：来源 1 **369 queued**，来源 2 **1 api_accepted + 216 queued**，试传 job ID 2 `cloud_verify_method=manual` 且时间戳非空；其它媒体任务未开始上传、内容未删除或重置。MI3MountMac 和小米 3 本机 Google Photos 链路未被触碰。
- **下一步**：用 `8pndk8ba` 在数小时或下一日重新打开官方存储管理页面比较 88.8 MB；如仍维持不变，才是进一步支持“该单张 Mac Storage Saver 上传不计额度”的证据，但仍非永久保证。若开始增长，应停止整批测试并检查 gotohp Upload Saver/Google 端政策。不应在未核验延迟用量前自动启动 216 张的批量上传。

### 9.13 简化为挂载即同步、停用保留、重挂载增量续传（2026-11-15）

**用户需求定版**：GoogleUnlimited 只针对用户在 WebUI **添加/登记过的**本地目录、外置盘、SMB/NFS/NAS 映射或单文件工作；不能看到新磁盘就擅自上传整个磁盘。新增来源默认用文件夹名作为 Google Photos 相册名，不再每次要求点击相册确认。自动同步运行中每 30 秒增量检查所有已启用来源：发现新照片/视频直接入 SQLite 队列并开始上传。挂载断开保留此前媒体/进度，重挂载到相同路径即自动检查、继续未完成任务。

**实现**：
- 新建 SQLite `settings(key,value)` 保存 `auto_sync=1|0`，WebUI 用单一的「自动同步」开关 `POST /api/sync` 同时控制目录监控与后续上传，替代两个独立开关。启动后持续运行，无需在添加目录和确认相册之间反复切换。停止时持久设为 OFF，停止发起下一文件；已经启动的当前文件可自然结束以避免因强行终止而产生未知云端结果。
- 仅当先前明确开启过、并且重启时具备**本地账号凭据、通过 SHA-256 校验的上传引擎、显式 `--allow-real-upload` 权限**时，启动 WebUI 自动恢复监控和上传；不满足条件则安全停止、不绕过授权。为兼容旧客户端，原 `/api/upload` 开关映射为统一自动同步；`/api/monitor` 只保留旧的监控用途。
- 新来源和重新添加原路径都会调用增量扫描；以前 `add_source` 遇到同一路径就完全不扫描，无法识别新增文件，现在改为读取 SQLite 已保存的源 ID、元数据和哈希，只有 size/mtime_ns/ctime_ns 变化才重新读取文件进行 SHA-256。新来源自动设置 `album_confirmed=1`，旧来源保留原自定义相册。重新添加时把自己以前明确暂停、仍待处理的 `paused` 任务转回 `queued`，已 `api_accepted`、`duplicate` 和 `cloud_verified_at` 保持原样。
- 单来源「暂停」禁用该路径，其余来源继续。选择「删除显示」将映射禁用并归档、从常规页面隐藏，**绝不删除原图、数据库、相册或任务历史**；在隐藏记录中点击「重新启用」或重新添加同一路径即可恢复旧 ID 和进度。已映射目录短暂离线时 `file_available=0`，恢复后重新扫描恢复可用性。若软件断电后出现 `interrupted`，仍然需要核对云端再重试，不在结果未知时无条件自动重传。
- 首页「文件夹与单文件」显示每来源媒体数及 API 接收进度（不同于谷歌云端人工核验）；高级相册、单张试传和任务历史仍默认收起。WebUI 本机地址为 `http://127.0.0.1:8796`，不会直接暴露给局域网。

**验证边界**：当前 Chrome 测试账号的共享存储在已上传照片/视频后仍显示 **88.8 MB** 是值得持续观察的实测现象，但该指标是整个 Google 账号 Gmail/Drive/Photos 的合计，可能有统计延迟和显示精度问题。Google 官方关于 Pixel 3a–5 的免配额说明限定从合格设备备份的 Storage Saver 媒体；因此 gotohp 的 Offline/Master Token 被服务器接受并且上传成功，**不等于已经证明 Google 识别它为实体 Pixel 设备或保证永久免费配额**。参见 Google Photos 官方帮助 https://support.google.com/pixelphone/answer/6220791 。

**兼容与回归（2026-11-15 北京时间已验收）**：Mac M2 本地 **33 项 Python 测试全部通过**（含首次自动使用文件夹名相册、停止/重启后设置持久化、旧路径重添加后只哈希新文件、停用来源保留照片/SQLite、网络盘脱机重挂载、已 API 接收及手工云端核验的媒体不重新排队、CSRF 保护）；`node --check webui/app.js`、`git diff --check` 通过。源码提交 `a7a35292fa87954e627ed808884f28db5649452d` 已推送 `samni728/GoogleUnlimited main`，GitHub Actions [43685999288](https://github.com/samni728/GoogleUnlimited/actions/runs/43685999288) 的 Lint/macOS/Linux/Windows 全部 **success**。

**Mac M2 实机安全部署验收**：检查旧 `8796` PID 和端口所有者，待当前约 124 MB 视频自然上传完成、确认无 `uploading` 后备份 `webui/state/queue.pre-auto-sync-20261115.sqlite3`（409600 bytes），停止旧服务。仅在 SQLite `settings` 中把 `auto_sync` 设置为 `1`（用户已明确要求自动上传），再用 `python3 webui/server.py --port 8796 --engine owned --allow-real-upload` 重新启动。本机 HTTP 200、首页已实际服务一个 `sync-switch`（旧 `monitor-switch` 和 `upload-switch` 已移除），API 验证 `auto_sync_enabled=true`、`auto_sync_requested=true`、`monitor_enabled=true`、`upload_enabled=true`、`owned_build_verified=true`、`credentials_present=true`。SQLite 全库仍为 586 项，来源 1 已归档的 369 项不动，来源 2 的 217 项保持进度；第一次切换后核对 11 项 API 接收、205 项 queued 和 1 项上传中、没有失败。OpenCLI Chrome Profile `8pndk8ba` 实际打开 WebUI，页面可见单一「自动同步 · 启动 / 停止」和来源进度 11/217（5%）。**部署完成且已实际继续上传，不只是通过测试。**

## 10. 之前阶段待办与交接


当前已可使用（仅检查**明确指定**的样本文件夹，不上传）：

```bash
cd "/Users/samni/Desktop/开发项目/GoogleUnlimited"
bash google-unlimited.sh audit "/path/to/test-photos"
bash tests/test_offline_audit.sh
bash tests/test_macos_binary_selection.sh
bash tests/test_mock_upload.sh
```

后续按顺序执行：
1. P3 已完成离线启动验证；尚需真实用户独立账号凭据时才进入 P5。禁止运行未经审核的一键 `setup`、自动获取 Chrome Cookie 或自动启用 LaunchAgent。
2. P4：用 mock 进程模拟真正的进度／失败／断点恢复，将状态与数据库隔离在本项目测试目录，永远不得从历史 MI3MountMac 数据库推断 Google 云端成功。
3. P5：仅在用户可见的独立账号授权与小样本测试边界建立后，验证真实 Google 云端、配额变化和兼容性；否则维持离线开发结论。
4. 发布以本地 `git log -1`、`git status --short` 与远端 `samni728/GoogleUnlimited` 的 `main` 实际提交为准；下一位 Agent 不要直接 reset、覆盖或强制 push。Google 真实账号测试尚未执行，**GitHub 推送不等于云端上传服务上线**。
