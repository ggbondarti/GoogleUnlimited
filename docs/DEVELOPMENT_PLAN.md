# GoogleUnlimited / Mac M2 原生 Google Photos 备份试验 — 唯一开发与交接文档

> 最后更新：2026-11-13 23:26 (America/Los_Angeles)
> 负责执行节点：NexusDock mac-M2（`node_18eb67f64516bf229adb4927b6e076ff`）
> 恢复任务 ID：`tsk_067d6529aa96eb48`
> 工作目录：`/Users/samni/Desktop/开发项目/GoogleUnlimited`
> 上游仓库：<https://github.com/shishir0x/GoogleUnlimited>
> 发布目标：<https://github.com/samni728/GoogleUnlimited> （已确认用户仓库空仓，GitHub 有 push 权限）
> 当前上游基准 commit：`5b7f5387e218a7d407a27e576989cae707319dc6`（2026-11-05 提交）
> 当前阶段：P0/P1/P2 基线已通过，P3 Mac CLI/监控依赖已验证，P4 mock 状态流转已通过；**未连接 Google 账号、未开展真实上传**。

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

## 9. 当前下一步

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
