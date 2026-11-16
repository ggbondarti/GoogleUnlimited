# GoogleUnlimited

**维护者：[@samni728](https://github.com/samni728)**

在自己的 Mac 上运行 Google Photos 图片、视频备份工具。可扫描本地磁盘、移动硬盘及已挂载的 NAS/SMB 目录，通过本地 WebUI 管理上传和进度。

## 工作方式

- 文件由本机直接上传到 Google Photos，不经过项目作者的中转服务器。
- SQLite 保存文件索引、去重信息、相册绑定和任务状态；重新挂载后可续传，不删除源文件。
- 上传程序基于固定版本的开源 [gotohp](https://github.com/xob0t/gotohp)，在本机自行编译并校验源码和二进制。
- 本项目不是 Google 官方客户端。Google 可随时调整接口、账号资格及存储政策，不承诺永久免费或无限容量。

## 安装与首次授权（macOS Apple Silicon）

在项目目录打开 Mac 本机 Terminal：

```bash
bash scripts/build_local_uploader.sh
python3 engine/verify_local_build.py
```

### 获取 Google 授权 Token

1. 使用**准备备份的 Google 账号**打开 Chrome 对应配置文件，访问 [Google Embedded Setup](https://accounts.google.com/EmbeddedSetup)。
2. **页面一直转圈、停留在账号选择页，或没有显示登录成功，不一定代表授权失败。** 不必只等页面跳转；保持该页面打开，继续检查 Cookie。
3. 打开 Chrome 开发者工具（macOS：`⌥⌘I`），进入 **Application → Storage → Cookies → https://accounts.google.com**。
4. 在 Cookie 列表找到 **`oauth_token`**，只复制这一行的 **Value**。不要复制整份 Cookie，也不要复制其他名称的 Token。
5. 如果没有 `oauth_token` 或 Value 为空，说明当前尚未取得可用凭据；返回 Embedded Setup 重新尝试，不要拿其他 Cookie 代替。

### 将 Token 保存到本机

在项目根目录运行：

```bash
bash scripts/authorize_local.sh
```

脚本提示粘贴时，把刚才复制的 `oauth_token` **粘贴到 Mac Terminal 提示符**并按 Return。输入过程不会显示字符。这一步不是把 Token 粘贴回网页。

授权成功后，凭据保存在本机私有文件 `webui/state/gotohp.config`，目录权限为 `700`、配置文件权限为 `600`。可按脚本提示运行 `creds list` 核对账号。**不要把 Token、Cookie 或配置文件内容发送给 ChatGPT、Agent、他人或 GitHub。** 授权脚本必须在本机交互式 Terminal 运行，不要通过远程命令执行。

## 启动与同步

```bash
python3 webui/server.py --port 8796 --engine owned --allow-real-upload
```

在同一台电脑打开 <http://127.0.0.1:8796>。服务仅监听本机地址。

- **添加来源只扫描和建立索引，不会立即上传。** 先检查目录、确认相册名称和画质。
- 上传必须同时满足：顶部 **「自动同步 · 总开关」** 开启、来源 **「此来源同步」** 开启、相册已确认。
- 想先测试时，关闭全局同步，使用 **「仅试传 1 张照片」**。确认文件、目标相册和画质后再执行。
- 顶部停止会阻止领取新文件；当前已开始的单个文件可能完成。暂停来源不会清除队列或历史。
- 已处理来源会收起到 **「查看已处理」**，不是删除或停用；以后新增文件仍可重新发现。
- 隐藏、移除或断开来源不会删除原文件和 SQLite 历史。重新添加来源后，上传许可需重新确认。
- **API 已接收不等于 Google 云端已人工核验。** 请在 Google Photos 媒体详情中抽查。

## 上传画质

- **Original quality（默认）**：不传 `--saver`，请求按原始画质上传。
- **Storage saver**：传入 `--saver`，Google 可能压缩或调整分辨率。

画质选择只影响之后上传的文件，不会修改或重传已完成项目。首次使用或切换画质，建议先单张测试；最终画质和是否占用配额，以 Google Photos 对应媒体的详情及账号存储页面为准。单张测试结果不能保证其他文件、视频或账号获得相同结果。

## 离线测试

以下测试不会上传媒体：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 来源与许可

- 原项目来源：[shishir0x/GoogleUnlimited](https://github.com/shishir0x/GoogleUnlimited)。该上游仓库未提供 LICENSE 文件；此处仅注明来源，不将其原始代码误标为 MIT。上游代码的再分发权限应以原作者授权为准。
- 上传引擎：[xob0t/gotohp](https://github.com/xob0t/gotohp)，固定源码提交见 `engine/gotohp-src/UPSTREAM_REVISION`；保留 MIT 许可证及版权声明：`engine/gotohp-src/LICENSE`。
- SQLite PowerShell 模块：[RamblingCookieMonster/PSSQLite](https://github.com/RamblingCookieMonster/PSSQLite)，保留 MIT 许可证及版权声明：`Modules/PSSQLite/LICENSE`。
- 本项目与 Google 无隶属或官方合作关系。
