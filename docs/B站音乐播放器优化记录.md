# B站音乐播放器（music.py）优化记录

> 从「能用」到「好用、省流量、零缓存、秒暂停」的完整折腾史
> 含全部 API 参数、代码细节、实测数据

---

## 一、架构演进

### 阶段 1：ffmpeg + paplay 管道（初始版）

```
Tkinter GUI → B站 API（3 次请求） → ffmpeg 拉流解码 → 管道 → paplay → 声卡
```

- 暂停：对 ffmpeg 和 paplay 发 `SIGSTOP`（冻结）/ `SIGCONT`（恢复）
- 顺序很重要：先 SIGCONT 再 SIGKILL，否则 SIGSTOP 过的进程 SIGKILL 排队不执行

### 阶段 2：DASH 纯音频（省 65% 流量）

**问题**：playurl 默认 durl = 完整 MP4（含视频轨），一首歌 **14.4MB**

**优化**：`dash.audio[]` 纯音频 M4A（.m4s）约 **5MB**

**完整请求**：
```python
GET /x/player/playurl
params = {
    "bvid": bvid,
    "cid": cid,
    "qn": 30280,       # ← DASH 音轨 id（不是视频清晰度！）
    "fnval": 16,       # DASH 模式：音视频分离
    "fourk": 1,
    # ❌ 不能加 platform=html5 / highbit=1
    #   这俩参数强制返回视频模式（durl），dash.audio 直接消失
}
```

**音质 id 对照表**（dash.audio[].id，非视频码率）：
| id | 音质 | 备注 |
|----|------|------|
| 30216 | 64k | 免费 |
| 30232 | 128k | 免费 |
| 30280 | 192k | 免费最高 |
| 30250 | FLAC | 仅会员 |

**响应的 dash.audio[] 结构**：
```json
{"audio": [
  {"id": 30280, "baseUrl": "https://...", "backupUrl": ["..."],
   "mimeType": "audio/mp4", "duration": 300}
]}
```

**音质降级逻辑**：优先 `prefer_qn` 精确匹配，否则按 `AUDIO_PRIORITY=[192k,128k,64k,FLAC]` 顺序降级，最后兜底第一条。

**必需请求头**（三处都要带）：
```python
_headers = {
  "Referer": "https://search.bilibili.com/",
  "Origin": "https://search.bilibili.com",
  "User-Agent": "Mozilla/5.0 ... Chrome/120.0.0.0",
  "Accept": "application/json, text/plain, */*",
  "Cookie": f"buvid3={uuid.uuid4()}",   # 先 GET 首页拿基础 cookie
}
```

**三个 API 端点**：
| 端点 | 参数 | 用途 |
|------|------|------|
| `/x/web-interface/search/type` | keyword, search_type=video, pagesize=30 | 搜索（25~30 条结果） |
| `/x/web-interface/view` | bvid | 拿 cid |
| `/x/player/playurl` | bvid, cid, qn, fnval, fourk | 拿音频 URL |

### 阶段 3：mpv 后端（最终方案）

```
mpv --no-video --no-terminal --no-audio-display \
     --input-ipc-server=/tmp/music-mpv-<pid>.sock \
     --audio-buffer=0.2 \
     --http-header-fields="Referer: https://search.bilibili.com/, Origin: https://search.bilibili.com, Cookie: buvid3=xxx, Accept: application/json, text/plain, */*" \
     --user-agent="Mozilla/5.0 ... Chrome/120" \
     [--start=30] \
     <音频URL>
```

**笔记**：
- `--http-header-fields`：多个 header 用逗号分隔（注意引号嵌套）
- `--start=N`：启动即 seek（等价 ffmpeg 的 `-ss` 放 `-i` 前）
- `--audio-buffer=0.2`：减少缓冲（200ms），暂停感知更跟手

---

## 二、暂停排查全记录（最硬核的一战）

### 第 1 层：SIGSTOP 机制验证

**实测进程状态转换**（ps stat 字段）：
```
播放中: ffmpeg=Sl paplay=S      （S=sleeping, l=多线程）
暂停:   ffmpeg=Tl paplay=T      （T=stopped 冻结，声音立刻断）
恢复:   ffmpeg=Sl paplay=S      （继续播放，8 秒观察不退出）
```
**结论**：信号冻结机制本身完全可靠

### 第 2 层：ffmpeg -re 与 SIGSTOP 冲突（僵尸进程）

**症状**：暂停后恢复，ffmpeg 变 `Z`（僵尸）→ 声音断了没法续

**根因**：
```
ffmpeg -re = 按「系统时钟」节流读取（每秒读 1 秒的音频）
SIGSTOP 冻结 10 秒 → 系统时钟走了 10 秒
SIGCONT 恢复 → ffmpeg 发现输入"时间戳过期" → 判定 EOF → 退出
```

**修复**：去掉 `-re`。ffmpeg 往管道写满 64KB 后自动阻塞，靠 paplay 的消费速率自然限速，无时钟依赖。

### 第 3 层：暂停响应慢 + pactl 三条路全废

**症状**：即使进程冻结，声音还要过会才停——**PipeWire 缓冲里的音频会继续播完**

**试了三招全被 PipeWire 无视**：
| 命令 | 结果 |
|------|------|
| `pactl suspend-sink @DEFAULT_SINK@ 1` | ❌ State 一直 RUNNING |
| `pactl set-sink-input-mute <id> 1` | ❌ Mute 一直 no |

**根因**：`pactl info` 显示 `Server Name: PulseAudio (on PipeWire 1.4.2)` —— 系统是 PipeWire，pulse 兼容层**静默忽略** suspend/mute 操作。

**教训**：报错不存在的环境问题，先查 `pactl info` / `pw-cli list-objects`。

### 第 4 层：终局——改 mpv

暂停/seek 下沉到播放器内部处理，绕开 PipeWire 音频缓冲这层。

### 第 5 层：mpv IPC 用 JSON 协议（最坑）

**症状**：`set pause yes` 发了，mpv 没反应

**根因**：`--input-ipc-server` 是 **JSON IPC 协议**！
```
❌ 老式命令  "set pause yes"        → mpv 静默忽略
❌ 老式查询  "property-get pause"   → 超时无应答
✅ JSON     {"command":["set_property","pause",true]}
✅ JSON     {"command":["get_property","pause"]}
```

**证明暂停真生效**（用查询反查）：
```json
发送: {"command":["get_property","pause"]}
应答: {"data":true,"request_id":0,"error":"success"}   ← data:true = 真暂停
```

### 第 6 层：性能细节

**fire-and-forget**（set 命令不读应答）：
```python
s.sendall(json.dumps({"command": cmd}).encode() + b"\n")
s.close()        # 立即关，不等 mpv 应答
```
实测：**暂停调用 0ms**（之前读应答要 200ms）

**位置校准**（恢复后消除估算误差）：
```python
r = self._ipc_reply(["get_property", "time-pos"])
self.offset = float(r["data"])        # 用 mpv 真实位置覆盖
self.started_at = time.time()
```
实测：暂停前 3.0s → 恢复后 3.3s（无跳变）；seek 30s → mpv 报 30.97s

---

## 三、零缓存 & 持久化

### 播放绝对不落盘
```
网络 → mpv 内存解码 → 声卡
```
全程管道 + 内存，`iotop` 观察 0 磁盘写入（只有收藏 JSON 那个 KB 级文件）。

### 收藏只存 ID
```json
{"bvid":"BV1xxx","title":"歌名","author":"UP","duration":"3:00","qn":30280}
```
- 无 URL、无音频内容 = 84 字节
- 同 bvid 去重 + 最新置顶
- 收藏夹与搜索结果**共用一套 Listbox**（`_mode` + `_source` 切换数据源）

### 收藏持久化踩坑（静默丢失）

**症状**：点收藏提示成功，重启后收藏夹空

**根因链**：
```
B站标题含孤儿代理项 \ud800（非法 unicode）
→ json.dump(favs, f, ensure_ascii=False)
→ UTF-8 编码抛 UnicodeEncodeError: surrogates not allowed
→ 被 except: pass 吞掉（无任何日志）
→ 文件永远写不进去
```

**复现**：`title = "测试歌 \ud800 特殊字符"` 立刻复现

**修复**：
```python
json.dump(favs, f, ensure_ascii=True)   # 转义为 \ud800，读回自动还原
```
同时 `except` 里 `print(..., file=sys.stderr)`，不再静默。

---

## 四、播放状态 & 播放模式

### 全局播放状态（双击/按钮共享）
- 数据源 `self._source`（搜索 or 收藏）+ 选中索引 `self.current`
- 双击列表 → `_play()`；点播放按钮 → `_play_toggle()` → `_play()` —— **同一入口**

### 播放模式（播完自动切歌）
```python
_play_mode: "order"  列表循环（末尾回开头）
            "random" 随机挑一首
            "single" 单曲循环（重播当前）
```

### 按钮状态机
| 场景 | ▶按钮 |
|------|-------|
| 播放中 | `⏸ 暂停` |
| 暂停后 | `▶ 继续` |
| 播完（_on_end） | `▶ 播放` |

---

## 五、踩坑总表

| 坑 | 教训 | 排查/修法 |
|----|------|-----------|
| durl 还是 dash.audio | platform=html5/highbit 强制视频模式 | 不加这俩参数 |
| ffmpeg -re | 时钟节流与 SIGSTOP 冲突 | 一律去掉 -re |
| pactl 失灵 | PipeWire 兼容层静默忽略 | 先查 `pactl info` |
| mpv 命令无效 | input-ipc-server 是 JSON 协议 | 用 JSON 命令 + 查询反查 |
| 暂停响应慢 | 音频缓冲残留 | 换 mpv 内部暂停 |
| 收藏丢失 | except:pass 吞 UnicodeEncodeError | ensure_ascii=True + 打日志 |
| 进度条不能拖 | ttk.Progressbar 只读 | Canvas 自绘 + 松开放开 seek |
| 位置跳变 | 暂停期间时钟照走 | 恢复用 mpv time-pos 校准 |
| stdout 缓冲 | 子线程 print 看不到 | 用 flush / stderr |