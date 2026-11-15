# GoogleUnlimited

一个在**自己电脑上运行**的 Google Photos 图片、视频自动备份工具。可直接读取已添加的本地文件夹、移动硬盘与 NAS/SMB 共享，通过本地 WebUI 管理同步。

## 它是怎样工作的？

1. **自己授权**：用户在本机完成 Google 登录授权，凭据仅保存在本机的私有配置文件。
2. **自己编译**：项目使用固定版本的开源 [gotohp](https://github.com/xob0t/gotohp) 源码，由用户在本机编译上传程序，并验证源码和二进制哈希。**不是下载陌生人编译的程序直接运行。**
3. **直接上传**：本机 gotohp 使用 Google Photos 的移动端上传接口，与 Google 服务通信，照片和视频**不经过本项目作者的中转服务器**。
4. **记录进度**：SQLite 保存路径、文件哈希和上传结果。关闭/移除映射不删除源文件；重新挂载同一路径会识别历史记录，只继续未完成或新出现的媒体。

这是一款非官方工具，gotohp 是第三方开源实现，并非本项目从零重写的协议，也不是 Google 官方客户端。Google 可能更改 API、设备识别及存储政策；**不能承诺永久免费或无限容量**。

## 快速开始（macOS Apple Silicon）

在终端进入你自己克隆的项目目录：

```bash
cd GoogleUnlimited
bash scripts/build_local_uploader.sh
python3 engine/verify_local_build.py
```

首次使用，在**本机**完成 Google 账号的 Embedded Setup 授权，再执行：

```bash
bash scripts/authorize_local.sh
```

授权脚本使用隐藏输入，不要把 Cookie 或令牌发给聊天、远程 Agent 或 GitHub。

启动 WebUI：

```bash
python3 webui/server.py --port 8796 --engine owned --allow-real-upload
```

在**同一台电脑**打开 http://127.0.0.1:8796，开启「自动同步」，选择要备份的文件夹。服务只监听 127.0.0.1，默认不向局域网或公网暴露。

## 简单的同步逻辑

- **添加来源**：自动扫描文件夹，并以文件夹名作为默认相册名称；自动同步开启后直接上传。
- **停止同步**：不会再领取新文件；当前正在传输的文件可正常结束，进度永久保存。
- **移除映射或硬盘离线**：不删除原图和 SQLite 记录。重新添加相同路径或挂载同一路径后自动检查和续传。
- **已上传**：不因重新扫描而重复上传。API 已接收与 Google 官方页面核验是两种不同状态。
- **默认画质**：Storage saver（节省存储空间）；在来源卡片的「高级设置」可选择 Original quality（原始画质）。

## 画质模式

| 模式 | 上传参数 | 说明 |
| --- | --- | --- |
| **Storage saver（默认）** | `--saver` | Google 可能压缩或调整媒体画质，具体配额由 Google 判断 |
| **Original quality** | 不传 `--saver` | 原始画质，**可能或会占用账号配额** |

Original quality 只影响**之后上传的文件**，不会改变已上传的图片或视频。切换前先关闭自动同步、等当前文件完成，建议只做**一张照片**的验证，并在 Google Photos 的「详情」和账号存储管理页面检查结果。尤其不要把单条媒体“不占空间”的结果推断为所有设备、所有质量模式都免费。

上游 gotohp 另有独立的 `--use-quota` 参数，本项目的画质开关**不启用这个参数**；因此“原画质”与“主动计入配额”不是同一个开关。最终画质和计费应以 Google Photos 显示为准。

## 隐私与数据

- `webui/state/`：私有 SQLite、授权配置及运行记录；已被 Git 忽略，不应提交。
- `engine/gotohp-src/`：固定版本的 gotohp 源码及许可文件。
- `engine/bin/`：本机编译产物，已被 Git 忽略。
- 本项目不应收集 Google 密码、完整 Chrome Cookie 库、设备私钥，也不需要使用别人的远程上传服务器。
- Google Photos 不是重要照片的唯一安全副本，建议保留原始文件。

离线回归测试（**不会上传文件**）：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 来源和许可

- GoogleUnlimited 上游：[shishir0x/GoogleUnlimited](https://github.com/shishir0x/GoogleUnlimited)
- 上传引擎：[xob0t/gotohp](https://github.com/xob0t/gotohp)，固定版本见本项目 `engine/gotohp-src/`，保留原版 MIT 许可。
- 项目开发细节与变更记录见 [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)。本项目与 Google 无隶属或官方合作关系。
