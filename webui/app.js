"use strict";

const $ = id => document.getElementById(id);
let csrf = "";
let currentStatus = null;
let browserDirectory = "";
let selectedFile = "";
let modalKind = "directory";
let selectedSourceId = "";
let showHiddenRecords = false;
const expandedEventSources = new Set();
const expandedAlbumSources = new Set();
const albumDrafts = new Map();
let pageOffset = 0;
const pageSize = 100;

function humanSize(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, value = Number(n);
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return value.toFixed(i ? 1 : 0) + " " + units[i];
}
function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content);
  return node;
}
function clear(node) { node.replaceChildren(); }
function notice(message, ok = false) {
  const node = $("notice");
  node.hidden = !message;
  node.textContent = message || "";
  node.className = ok ? "notice ok" : "notice";
  if (message) window.scrollTo({top: 0, behavior: "smooth"});
}
async function get(url) {
  const response = await fetch(url, {cache: "no-store"});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}
async function post(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-GU-CSRF": csrf},
    body: JSON.stringify(payload)
  });
  const output = await response.json();
  if (!response.ok) throw new Error(output.error || "操作失败");
  return output;
}
async function action(url, payload, message = "") {
  try {
    await post(url, payload);
    if (message) notice(message, true);
  } catch (error) {
    notice(error.message);
  }
  await refresh();
}

function collapseHiddenSourceDetails(sourceId) {
  // Removing/hiding a source must also clear previously expanded DOM tables.
  // Otherwise its rows would remain visible until the user reloads the page.
  if (selectedSourceId === String(sourceId)) {
    selectedSourceId = "";
    $("index-details").open = false;
    $("index-summary").textContent = "目录明细已收起。选择可见来源后按需展开。";
    $("folders-list").replaceChildren();
  }
  $("jobs-details").open = false;
  $("jobs-body").replaceChildren();
}
const statusText = {
  queued: "等待上传", uploading: "正在上传", api_accepted: "API 已接收",
  duplicate: "已去重", retrying: "等待重试", failed: "上传失败",
  interrupted: "中断待人工核对", paused: "来源已暂停"
};

function renderCounts(counts) {
  const sum = key => counts[key]?.count || 0;
  const total = Object.values(counts).reduce((n, v) => n + v.count, 0);
  $("stat-total").textContent = total;
  $("stat-queued").textContent = sum("queued") + sum("paused");
  $("stat-accepted").textContent = sum("api_accepted");
  $("stat-failed").textContent = sum("failed") + sum("retrying") + sum("interrupted");
  const processed = sum("api_accepted") + sum("duplicate");
  const pct = total ? Math.round(processed * 100 / total) : 0;
  $("task-percent").textContent = pct + "%";
  $("task-bar").style.width = pct + "%";
}

function renderSources(sources) {
  const target = $("sources-list");
  clear(target);
  if (!sources.length) {
    target.appendChild(element("div", "empty", "还没有添加文件夹。添加后自动扫描；同步运行时直接上传，历史永久保留。"));
    return;
  }
  for (const source of sources) {
    const row = element("div", "source-entry" + (source.archived ? " archived" : ""));
    row.appendChild(element("span", "source-ic", source.kind === "directory" ? "▣" : "▤"));
    const info = element("div", "source-meta");
    const sourceName = element("strong", null, source.path.split("/").pop() || source.path);
    sourceName.title = source.path;
    info.appendChild(sourceName);
    const badges = [source.location_kind === "network" ? "远程共享" : "本地 / 映射盘"];
    if (source.ui_visibility === "removed") badges.push("已从界面移除 · 历史保留");
    else if (source.ui_visibility === "hidden") badges.push("已隐藏");
    else if (source.archived) badges.push("已归档");
    else if (source.availability === "offline") badges.push("离线 · 历史保留");
    else if (source.availability === "interrupted") badges.push("扫描中断");
    else if (source.availability === "unknown") badges.push("等待重新扫描");
    else if (!source.enabled) badges.push("已暂停");
    const sourcePath = element("small", null, source.path);
    sourcePath.title = source.path; // truncated long paths remain inspectable.
    info.appendChild(sourcePath);
    info.appendChild(element("small", "source-badges", badges.join(" · ")));
    const uploaded = source.api_accepted || 0;
    const all = source.indexed_files || 0;
    const pct = all ? Math.round(uploaded * 100 / all) : 0;
    info.appendChild(element("small", "source-badges",
      "已发现 " + all + " 项（照片 " + source.images + " / 视频 " + source.videos +
      "） · 已接收 " + uploaded + " / " + all + " · " + pct + "% · 云端核验 " + source.cloud_verified));
    info.appendChild(element("small", "source-album-caption",
      "Google Photos 相册：" + (source.album_name || "尚未设定") +
      (source.album_confirmed ? "" : " · 添加此来源后自动使用该名称")));
    row.appendChild(info);
    // Keep every action inside ONE grid cell. Previously they were siblings of
    // the text column and a full-width <details>; flexbox compressed the path
    // into a ~10px column, rendering Chinese and Latin text vertically.
    const controls = element("div", "source-controls");
    const index = element("button", "btn soft", "查看索引");
    index.addEventListener("click", () => {
      $("job-source-filter").value = String(source.id);
      selectedSourceId = String(source.id);
      pageOffset = 0;
      $("index-details").open = true;
      showFolders(source);
      refresh();
      $("index").scrollIntoView({behavior: "smooth", block: "start"});
    });
    controls.appendChild(index);
    if (source.ui_visibility === "removed") {
      const restore = element("button", "btn primary", "重新启用");
      restore.title = "已挂载时单击恢复，接着上传未完成文件；不重传已接收的内容";
      restore.addEventListener("click", () =>
        action("/api/source/restore", {id: source.id},
          "映射已恢复，保留原进度；自动同步运行时继续上传"));
      controls.appendChild(restore);
    } else if (source.ui_visibility === "hidden") {
      const show = element("button", "btn soft", "显示");
      show.addEventListener("click", () =>
        action("/api/source/display", {id: source.id, mode: "visible"},
          "已恢复显示，备份与同步设置保持不变"));
      controls.appendChild(show);
    } else if (source.archived) {
      const restore = element("button", "btn primary", "恢复历史");
      restore.addEventListener("click", () =>
        action("/api/source/restore", {id: source.id},
          "历史来源已恢复。旧文件备份记录仍然保留，不会重新登记已接受的内容。"));
      controls.appendChild(restore);
    } else {
      const checkbox = element("input");
      checkbox.type = "checkbox";
      checkbox.checked = !!source.enabled;
      checkbox.title = source.enabled ? "暂停来源" : "恢复来源";
      checkbox.addEventListener("change", () =>
        action("/api/source/toggle", {id: source.id, enabled: checkbox.checked},
          checkbox.checked ? "来源已恢复" : "来源已暂停，索引和记录保留"));
      const toggle = element("label", "switch");
      toggle.append(checkbox, element("span"));
      controls.appendChild(toggle);
    }
    if (source.ui_visibility === "visible") {
      const hide = element("button", "btn soft", "隐藏");
      hide.title = "只整理界面，不暂停同步、不更改媒体历史";
      hide.addEventListener("click", () => {
        collapseHiddenSourceDetails(source.id);
        action("/api/source/display", {id: source.id, mode: "hidden"},
          "索引已隐藏，数据库和备份历史完整保留");
      });
      controls.appendChild(hide);
      const remove = element("button", "btn warn", "删除显示");
      remove.title = "停止此来源并移除映射；保留照片、SQLite 和已上传历史";
      remove.addEventListener("click", () => {
        if (confirm("停止此来源并移除映射？不删除任何照片、数据库或上传进度。")) {
          collapseHiddenSourceDetails(source.id);
          action("/api/source/display", {id: source.id, mode: "removed"},
            "已停止该来源并保存全部进度，重新添加相同目录即可继续");
        }
      });
      controls.appendChild(remove);
    }
    row.appendChild(controls);
    const events = element("details", "source-event-details");
    const eventHeader = element("summary", null, "操作记录");
    events.appendChild(eventHeader);
    events.addEventListener("toggle", async () => {
      if (events.open) expandedEventSources.add(source.id);
      else expandedEventSources.delete(source.id);
      if (!events.open || events.dataset.loaded) return;
      try {
        const data = await get("/api/source/events?source_id=" + source.id);
        for (const entry of data.events) {
          events.appendChild(element("div", "event-line",
            entry.created_at + " · " + entry.action + " · " + entry.note));
        }
        if (!data.events.length) events.appendChild(element("div", "event-line", "尚无操作历史"));
        events.dataset.loaded = "1";
      } catch (error) { events.appendChild(element("div", "event-line", error.message)); }
    });
    if (expandedEventSources.has(source.id)) events.open = true;
    row.appendChild(events);

    const album = element("details", "source-album-settings");
    const summary = element("summary", null, "相册名称（可选修改）与单张试传");
    album.appendChild(summary);
    const albumBody = element("div", "source-album-form");
    const input = element("input", "album-name-input");
    input.type = "text";
    input.maxLength = 120;
    input.autocomplete = "off";
    input.placeholder = "Google Photos 相册名称";
    input.value = albumDrafts.get(source.id) ?? source.album_name ?? "";
    input.addEventListener("input", () => albumDrafts.set(source.id, input.value));
    input.title = "默认使用来源文件夹名称，可改为旅行名称，再点击保存并确认";
    albumBody.appendChild(input);
    const confirmAlbum = element("button", "btn primary", "保存并确认相册");
    confirmAlbum.type = "button";
    confirmAlbum.addEventListener("click", async () => {
      try {
        const result = await post("/api/source/album",
          {id: source.id, album_name: input.value});
        albumDrafts.delete(source.id);
        notice("已保存相册名：" + input.value + "。已上传文件不重新传。", true);
        await refresh();
      } catch (error) { notice(error.message); }
    });
    albumBody.appendChild(confirmAlbum);
    const test = element("button", "btn soft", "仅试传 1 张照片");
    test.type = "button";
    test.disabled = !source.album_confirmed || !currentStatus?.allow_real_upload
      || !currentStatus?.credentials_present || currentStatus?.upload_enabled;
    test.title = !source.album_confirmed ? "请先确认相册名称" :
      !currentStatus?.credentials_present ? "请先在 Mac 本机完成独立账号授权" :
      !currentStatus?.allow_real_upload ? "需以 --allow-real-upload 重启 WebUI" :
      currentStatus?.upload_enabled ? "请先关闭批量上传开关" : "只上传最小的一张未上传照片";
    test.addEventListener("click", async () => {
      if (!confirm("只从此来源挑选 1 张未上传的照片，使用独立账号上传到相册「" +
        source.album_name + "」。这会产生真实 Google Photos 网络请求。继续吗？")) return;
      test.disabled = true;
      try {
        const result = await post("/api/source/test-upload", {id: source.id});
        const job = result.test;
        notice("单张测试结果：" + job.name + " · " + job.status +
          (job.error ? " · " + job.error : " · 请进入 Google Photos 官方页面核对相册和配额"), true);
        await refresh();
      } catch (error) { notice(error.message); test.disabled = false; }
    });
    albumBody.appendChild(test);
    albumBody.appendChild(element("small", "source-album-hint",
      "默认使用文件夹名称，添加后直接同步。需要改名时先停止自动同步；修改名称不会移动已上传照片。"));
    album.appendChild(albumBody);
    album.addEventListener("toggle", () => {
      if (album.open) expandedAlbumSources.add(source.id);
      else expandedAlbumSources.delete(source.id);
    });
    if (expandedAlbumSources.has(source.id)) album.open = true;
    row.appendChild(album);
    target.appendChild(row);
  }
}

async function showFolders(source) {
  const target = $("folders-list");
  const summary = $("index-summary");
  summary.textContent = source.path + " · " + source.indexed_folders + " 个目录 · " +
    source.indexed_files + " 个媒体 · 云端人工核验 " + source.cloud_verified;
  if (!$("index-details").open) return;
  target.replaceChildren(element("div", "empty", "读取 SQLite 目录索引中…"));
  try {
    const result = await get("/api/folders?source_id=" + encodeURIComponent(source.id)
      + (showHiddenRecords ? "&include_hidden=1" : ""));
    clear(target);
    if (!result.folders.length) {
      target.appendChild(element("div", "empty", "此来源尚未建立目录索引。重新连接硬盘后执行扫描即可。"));
      return;
    }
    const table = element("table", "folder-table");
    const head = element("thead");
    const tr = element("tr");
    for (const col of ["目录路径", "媒体", "照片", "视频", "API 已接收", "人工云端核验", "离线 / 丢失"]) {
      tr.appendChild(element("th", null, col));
    }
    head.appendChild(tr);
    table.appendChild(head);
    const body = element("tbody");
    for (const folder of result.folders) {
      const row = element("tr");
      for (const data of [folder.path, folder.file_count, folder.image_count,
                          folder.video_count, folder.accepted_count, folder.verified_count,
                          folder.missing_count]) {
        row.appendChild(element("td", null, data));
      }
      body.appendChild(row);
    }
    table.appendChild(body);
    target.appendChild(table);
  } catch (error) {
    target.replaceChildren(element("div", "empty", error.message));
  }
}

function renderJobFilters(sources) {
  const select = $("job-source-filter");
  const previous = select.value;
  clear(select);
  const first = element("option", null, "全部来源（包括历史）");
  first.value = "";
  select.appendChild(first);
  for (const source of sources) {
    const option = element("option", null,
      (source.archived ? "[归档] " : "") + source.path.split("/").pop() + " · " + source.id);
    option.value = String(source.id);
    select.appendChild(option);
  }
  select.value = sources.some(s => String(s.id) === previous) ? previous : "";
}

function renderJobs(jobs) {
  const body = $("jobs-body");
  clear(body);
  if (!jobs.length) {
    const tr = element("tr");
    const td = element("td", "empty", "暂无符合条件的历史任务。文件夹或网络盘重新挂载后可扫描。");
    td.colSpan = 7;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const job of jobs) {
    const tr = element("tr");
    const nameCell = element("td");
    nameCell.appendChild(element("div", "filename", job.name));
    nameCell.appendChild(element("div", "path", job.path));
    if (!job.file_available) nameCell.appendChild(element("div", "path", "当前文件不可访问 · 历史保留"));
    tr.appendChild(nameCell);
    tr.appendChild(element("td", null,
      (job.media_kind === "video" ? "视频" : job.media_kind === "image" ? "图片" : "媒体")
      + " · " + (job.extension || "未知").toUpperCase()));
    tr.appendChild(element("td", null, humanSize(job.bytes)));
    const statusCell = element("td");
    statusCell.appendChild(element("span", "status " + job.status,
      statusText[job.status] || job.status));
    if (job.error) {
      const hint = element("div", "path", job.error);
      hint.title = job.error;
      statusCell.appendChild(hint);
    }
    tr.appendChild(statusCell);
    const verifiedCell = element("td");
    if (job.cloud_verified_at) {
      verifiedCell.appendChild(element("span", "status api_accepted", "人工已核验"));
      verifiedCell.appendChild(element("div", "path", job.cloud_verified_at));
    } else {
      verifiedCell.appendChild(element("span", "muted", "未核验"));
    }
    tr.appendChild(verifiedCell);
    const progress = {
      queued: "尚未传输", uploading: "上传中 · 字节比例未知",
      api_accepted: "API 接收 · 未自动云端核验",
      duplicate: "内容相同 · 跳过", retrying: "等待下一次重试",
      failed: "失败，未完成", interrupted: "结果未知 · 需先核对云端", paused: "已暂停"
    };
    tr.appendChild(element("td", "muted", progress[job.status] || "—"));
    const actions = element("td");
    if (["failed", "retrying", "interrupted"].includes(job.status)) {
      const retry = element("button", "btn soft", "重新入队");
      retry.addEventListener("click", () => {
        if (job.status !== "interrupted" || confirm("这项任务在上传中断时云端结果未知，请先检查 Google Photos 是否已经存在，再决定是否重试。确定重新入队？")) {
          action("/api/retry", {id: job.id}, "已重新入队");
        }
      });
      actions.appendChild(retry);
    } else if (job.status === "api_accepted") {
      const verify = element("button", "btn soft", job.cloud_verified_at ? "取消核验" : "人工确认");
      verify.addEventListener("click", () => {
        const next = !job.cloud_verified_at;
        if (!next || confirm("请先在 Google Photos 官方页面核对这项媒体确实存在。此操作只记录人工核验，不代表 Google 官方 API 自动验证。是否已核实？")) {
          action("/api/cloud/verify", {id: job.id, verified: next},
            next ? "已记录人工核验时间" : "已取消人工核验标记");
        }
      });
      actions.appendChild(verify);
    } else {
      actions.appendChild(element("span", "muted", "—"));
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function renderPagination(result) {
  const total = result.total || 0;
  const first = total ? result.offset + 1 : 0;
  const last = Math.min(total, result.offset + result.jobs.length);
  $("page-label").textContent = first + "–" + last + " / " + total + " 条";
  $("page-prev").disabled = result.offset <= 0;
  $("page-next").disabled = last >= total;
}

async function refresh() {
  try {
    const status = await get("/api/status" + (showHiddenRecords ? "?include_hidden=1" : ""));
    csrf = status.csrf;
    currentStatus = status;
    renderCounts(status.counts);
    renderSources(status.sources);
    renderJobFilters(status.sources);
    $("show-hidden-btn").textContent = (showHiddenRecords ? "隐藏历史记录" : "显示隐藏记录")
      + "（" + status.hidden_sources + "）";
    $("show-hidden-btn").setAttribute("aria-pressed", showHiddenRecords ? "true" : "false");
    $("stored-history-label").textContent = " SQLite 全库仍保存 " + status.stored_files + " 项媒体历史。";
    if ($("jobs-details").open) {
      const params = new URLSearchParams();
      if ($("job-source-filter").value) params.set("source_id", $("job-source-filter").value);
      if ($("job-kind-filter").value) params.set("kind", $("job-kind-filter").value);
      if ($("job-status-filter").value) params.set("status", $("job-status-filter").value);
      if (showHiddenRecords) params.set("include_hidden", "1");
      params.set("limit", String(pageSize));
      params.set("offset", String(pageOffset));
      const tasks = await get("/api/jobs?" + params.toString());
      renderJobs(tasks.jobs);
      renderPagination(tasks);
    }
    $("sync-switch").checked = !!status.auto_sync_enabled;
    $("credential-state").textContent = status.credentials_present
      ? "存在本机凭据（有效性未验证）" : "尚未配置独立账号";
    $("engine-state").textContent = status.upload_engine === "owned"
      ? (status.owned_build_verified ? "自行编译 · SHA 校验通过" : "自行编译 · 尚未通过构建验证")
      : "第三方发行版 · 固定 SHA-256";
    $("upload-reason").textContent = status.auto_sync_enabled
      ? "自动扫描并上传已添加的来源；断开硬盘保留记录，重挂载后接着上传。"
      : (status.allow_real_upload && status.credentials_present
        ? "已停止，记录保留。开启后自动检查并继续；正在传输的文件可正常结束。"
        : "尚未完成本机账号授权或上传引擎未就绪。");
    $("scan-label").textContent = status.scan_running
      ? "正在检查新文件" : (status.auto_sync_enabled ? "自动同步中" : "已停止");
    $("last-scan").textContent = status.last_scan
      ? "最近扫描：" + status.last_scan + (status.last_scan_error ? " · " + status.last_scan_error : "")
      : "尚未扫描";
  } catch (error) {
    notice("本地 WebUI 服务暂不可达：" + error.message);
  }
}

async function browse(path) {
  try {
    const data = await get("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
    browserDirectory = data.path;
    selectedFile = "";
    $("browser-path").value = data.path;
    const target = $("browser-items");
    clear(target);
    for (const folder of data.folders) {
      const row = element("div", "browser-row");
      row.appendChild(element("span", null, "▣  " + folder.name));
      row.appendChild(element("small", null, "打开 ›"));
      row.addEventListener("click", () => browse(folder.path));
      target.appendChild(row);
    }
    for (const file of data.files) {
      const row = element("div", "browser-row");
      row.appendChild(element("span", null, "▤  " + file.name));
      row.appendChild(element("small", null, humanSize(file.bytes)));
      row.addEventListener("click", () => {
        if (modalKind === "directory") {
          notice("请选择文件夹，或将类型切换为“单个文件”");
          return;
        }
        selectedFile = file.path;
        $("browser-path").value = file.path;
        target.querySelectorAll(".selected").forEach(node => node.classList.remove("selected"));
        row.classList.add("selected");
      });
      target.appendChild(row);
    }
    if (!target.children.length) target.appendChild(element("div", "empty", "没有可用的子文件夹或媒体文件"));
    $("up-btn").disabled = !data.parent;
    $("up-btn").onclick = () => browse(data.parent);
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}

async function refreshVolumes() {
  const root = $("network-mounts");
  root.replaceChildren(element("div", "empty", "读取 macOS 已挂载网络共享…"));
  try {
    const volumes = await get("/api/volumes");
    clear(root);
    if (!volumes.network.length) {
      root.appendChild(element("div", "empty",
        "当前没有已挂载 SMB/NFS/WebDAV 网络共享。先输入 smb:// 地址，在 Finder 授权后刷新。"));
    }
    for (const volume of volumes.network) {
      const button = element("button", "browser-row network-choice");
      button.type = "button";
      button.appendChild(element("span", null, "▣ " + volume.name + " · " + volume.fs_type.toUpperCase()));
      button.appendChild(element("small", null, "选择网络盘 ›"));
      button.addEventListener("click", () => browse(volume.path));
      root.appendChild(button);
    }
  } catch (error) {
    root.replaceChildren(element("div", "empty", error.message));
  }
}

async function connectShare() {
  const url = $("share-url").value.trim();
  if (!url.startsWith("smb://")) {
    notice("请输入 smb://服务器/共享文件夹；不要在地址中附加账号或密码");
    return;
  }
  try {
    const result = await post("/api/share/connect", {url});
    notice(result.message, true);
    await refreshVolumes();
  } catch (error) {
    notice(error.message);
  }
}

function selectStorage(mode) {
  const network = mode === "network";
  $("storage-local").classList.toggle("selected", !network);
  $("storage-network").classList.toggle("selected", network);
  $("network-mount-panel").hidden = !network;
  if (network) refreshVolumes();
}

function openDialog() {
  modalKind = "directory";
  document.querySelector('input[name="kind"][value="directory"]').checked = true;
  selectStorage("local");
  $("source-dialog").showModal();
  browse();
}

async function addSource() {
  const type = document.querySelector('input[name="kind"]:checked').value;
  const path = $("browser-path").value.trim();
  try {
    await post("/api/sources", {kind: type, path});
    $("source-dialog").close();
    notice(currentStatus?.auto_sync_enabled
      ? "已添加 / 恢复来源，自动扫描并续传。已上传的文件不会重复上传。"
      : "已添加 / 恢复来源，历史保留；启动自动同步即可上传。", true);
    await refresh();
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("add-btn").addEventListener("click", openDialog);
  $("show-hidden-btn").addEventListener("click", () => {
    showHiddenRecords = !showHiddenRecords;
    pageOffset = 0;
    if (!showHiddenRecords) {
      $("index-details").open = false;
      selectedSourceId = "";
      $("index-summary").textContent = "目录明细已收起。选择来源并点击“查看索引”可展开。";
    }
    refresh();
  });
  $("index-details").addEventListener("toggle", () => {
    if ($("index-details").open && currentStatus?.sources?.length) {
      const source = currentStatus.sources.find(s => String(s.id) === selectedSourceId)
        || currentStatus.sources[0];
      selectedSourceId = String(source.id);
      showFolders(source);
    }
  });
  $("jobs-details").addEventListener("toggle", () => {
    if ($("jobs-details").open) refresh();
  });
  $("close-dialog").addEventListener("click", () => $("source-dialog").close());
  $("cancel-btn").addEventListener("click", () => $("source-dialog").close());
  $("confirm-btn").addEventListener("click", addSource);
  $("browse-btn").addEventListener("click", () => browse($("browser-path").value));
  $("storage-local").addEventListener("click", () => selectStorage("local"));
  $("storage-network").addEventListener("click", () => selectStorage("network"));
  $("refresh-volumes").addEventListener("click", refreshVolumes);
  $("connect-share-btn").addEventListener("click", connectShare);
  $("scan-btn").addEventListener("click", () => action("/api/scan", {}, "已请求重新扫描"));
  $("refresh-btn").addEventListener("click", refresh);
  $("page-prev").addEventListener("click", () => {
    pageOffset = Math.max(0, pageOffset - pageSize);
    refresh();
  });
  $("page-next").addEventListener("click", () => {
    pageOffset += pageSize;
    refresh();
  });
  for (const select of ["job-source-filter", "job-kind-filter", "job-status-filter"]) {
    $(select).addEventListener("change", () => { pageOffset = 0; refresh(); });
  }
  $("sync-switch").addEventListener("change", e =>
    action("/api/sync", {enabled: e.target.checked},
      e.target.checked ? "自动同步已启动：新文件直接进入上传队列" :
        "已停止自动扫描和后续上传，历史仍保留"));
  for (const button of document.querySelectorAll('input[name="kind"]')) {
    button.addEventListener("change", e => {
      modalKind = e.target.value;
      $("browser-path").value = browserDirectory;
      selectedFile = "";
      browse(browserDirectory);
    });
  }
  refresh();
  setInterval(refresh, 4000);
});
