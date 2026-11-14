# 自主上传引擎（Mac M2）

本目录将开源 gotohp v0.11.0（源码提交 `68ad61fc844a060ce0e09c0b0524dd799c14170c`）的 CLI 必要 Go 源码固定在 `gotohp-src/`，保留上游 MIT LICENSE 和版权。我们可以自行审查、修改、编译和运行，但并不是宣称 49 个上游文件完全由我们原创。

本机运行 `bash scripts/build_local_uploader.sh`，生成 Git 忽略的 `engine/bin/gotohp-cli-owned-arm64` 和 `engine/bin/owned-build.json`，记录 Go 版本、源码树摘要和二进制 SHA-256。WebUI 以 `--engine owned` 明确选择自行编译的产物，启用上传前检查 SHA 和路径，不会自动回退到陌生发行二进制。

需强调：本地编译不改变 gotohp 采用 Android 风格非公开认证及 Photos 协议的事实；不能据此断言 Mac 上传获得小米 3 模拟 Pixel 5 的 Storage Saver 免费资格。必须另用一张图片实际比较 Google Photos 云端相册与用量，未验证前不启用批量。

本目录不保存账号 Cookie、Google Token、Android ADB 凭据、照片或 SQLite 数据库。
