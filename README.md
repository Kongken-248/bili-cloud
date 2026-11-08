# bili-cloud —— 毕设数据采集的云端补充

只用 GitHub Actions 做两件事，**不碰数据库、不带任何密钥**：

| 模式 | 做什么 | 每轮请求量 |
|---|---|---|
| `snapshot` | 对已知视频清单逐个取 `view`，存 9 项计数 | 视频数 × 1 |
| `expand` | 扫 12 区 `newlist`，新视频取 `view`+`tags`+`card` | 24 + 新视频数 × 3 |

**有意不做**：弹幕正文（`seg.so`）、评论正文（`reply/wbi/main` 需要 SESSDATA 才能翻页）。
这两样永远留在本地 `bili-crawler-v2`，云端零账号风险。

产出是纯 JSON，由本地 `python crawler.py pull-cloud` 合并入库，两种取数方式：
`--dir <本仓库>/out`（git pull 之后）或 `--remote Kongken-248/bili-cloud`（走 API，免 git）。

---

## 数据流

```
GitHub Actions（01:00 / 13:00 UTC = 北京 09:00 / 21:00）
  ├─ prep    生成统一批次标签（4 个分片必须共用同一个，否则一轮会变成 4 个假批次）
  ├─ snap    4 分片并行，每片约 1/4 清单，间隔 1.2~2.2s
  ├─ expand  12 区 newlist，新视频补元数据（本地不再重复干这件事）
  └─ commit  汇总 artifact → git commit 回 out/ 与 data/bvids.json
        ↓  git pull
本地  python crawler.py pull-cloud --dir ../cloud-snapshot/out
        ↓  按 (bvid, collect_time) 去重写入
      bili.db
```

云端**永远拿不到** 491MB 的库，也不需要它——只需要 `data/bvids.json`（2042 个 bvid，34KB）。

---

## 部署状态：已上线（2026-11-08）

仓库 <https://github.com/Kongken-248/bili-cloud> 已是公开仓库，`main` 已推上去，
`permissions: contents: write` 写在 workflow 里（无需去 Settings 手工开权限），
定时 `0 1,13 * * *` 已生效，无需再做任何配置。

要重建一份同样的仓库，按下表照做即可：

| 步骤 | 做法 |
|---|---|
| 建仓库 | 公开仓库，**不要**勾选初始化 README |
| 推送 | 本目录 `git remote add origin <url>` 后 `git push -u origin main`<br>⚠️ 上级目录 `C:\Users\window` 本身是个空 git 仓库，**不要**在上层 `git add .` |
| 权限 | 已由 workflow 内的 `permissions: contents: write` 覆盖 |
| 验证 | Actions → `bili-cloud` → Run workflow，`mode=snapshot`、`limit=20` |

### 遗留的坑：`-412` 不是 IP 问题

首次推送触发的运行里 4 个分片全部 3 秒内失败。当时的判断是"海外 IP 被风控"，
**实测证明判断错了**：2026-11-08 在 GitHub runner（出口 `52.188.87.19`）上做对照实验——

| 情形 | 结果 |
|---|---|
| 不带 `buvid3` 请求 `view` | **HTTP 412**，返回拦截页 HTML |
| 请求 `x/frontend/finger/spi`（免登录） | `code=0`，拿到 `b_3` / `b_4` |
| 带上 `buvid3` 再请求 `view` | HTTP 200，正常应答 |
| 带上 `buvid3` 请求 `newlist` | `code=0`，返回真实数据 |

所以 B 站判的是"这个客户端没有 buvid3"，与出口 IP 无关。`cloud/bili.py` 现在首次请求前
先取一次 buvid3（`ensure_buvid3()`），补上后 4 分片全部通过。
**推断**：本地一直能跑，是因为本地客户端早就有这一步，云端客户端漏抄了。

---

## 日常使用（本地）

最快路径是一条命令，不需要 git：

```
cd C:\Users\window\WorkBuddy\2026-10-13-14-39-14\bili-crawler-v2
python crawler.py pull-cloud --remote Kongken-248/bili-cloud
```

它走 GitHub contents API 把 `out/*.json` 拉到本地缓存目录再入库，重复执行幂等
（同一 `(bvid, batch)` 只写一次），已下载且大小一致的文件不会重下。

不想走 API 时，先 `git pull` 再指向本地目录：

```
cd ..\cloud-snapshot && git pull && cd ..\bili-crawler-v2
python crawler.py pull-cloud --dir ..\cloud-snapshot\out
```

本地新增了视频后，要把清单同步回云端，否则云端不知道它们：

```
python crawler.py export-cloud
cd ..\cloud-snapshot && git add data/bvids.json && git commit -m "sync bvids" && git push
```

第 3 步只在**本地新增了视频**时才需要。顺序必须是"先 pull-cloud 再 export-cloud"，
否则云端刚发现的新视频会被本地清单覆盖掉。

---

## 合并后会多出一种状态：`meta_only`

云端扩样的新视频只有元数据 + 数值，没有弹幕/评论正文，`fetch_log.status` 记为 `meta_only`。

```sql
-- 看有多少个只有壳、没内容的视频
SELECT COUNT(*) FROM fetch_log WHERE status='meta_only';
```

要不要补正文，由本地决定：`python crawler.py detail --bvid BV1xxx` 会把 `dm_ok/cm_ok` 补上，
状态随之更新。**不补也不影响分析**——它们的播放/点赞/评论计数是完整的，只是没有明细文本。

`pull-cloud` 每次结束都会打印这个数，不会悄悄累积。

---

## 已验证的事

以下都是本地实测过的，不是设计推演：

| 项目 | 结果 |
|---|---|
| `view` / `tags` / `card` 无 cookie 可用 | ✅ 均 `code=0` |
| 快照脚本（shard 0/4, limit 5） | ✅ 5 条，失败 0 |
| 扩样脚本（12 区 newlist） | ✅ 新增 3 个，三件套齐全，失败 0 |
| `pull-cloud` 合并 | ✅ videos +3、snapshots +8、tags +10、uppers +3 |
| 重复合并幂等 | ✅ 第二次 0 新增、8 条判重，总数不变 |
| 分片稳定 | ✅ 按 `crc32(bvid) % total`，同 bvid 永远同片，不漏不重 |
| GitHub runner 能访问 B 站 | ✅ 出口 `52.188.87.19`，补 `buvid3` 后 `view` / `newlist` 均 `code=0` |
| 云端 dry-run（4 片 × limit 20） | ✅ 80 条快照、0 失败、`commit` job 推回成功 |
| 全量一轮（2,042 个视频） | ✅ 快照 2,028/2,042（14 个稿件不可见）、扩样 +256、整轮 25.5 分钟 |

`samples/` 里是试跑产物，仅作格式参考，**不要**和 `out/` 混在一起合并——
它们只覆盖 5~8 个视频，混进去会产生一个只有几条记录的假批次。

---

## 目录

```
cloud/bili.py              无 cookie 客户端（限速/退避/412 熔断）
cloud/run.py               snapshot / expand 两个入口
data/bvids.json            已知视频清单（本地 export-cloud 生成）
out/                       云端产出（本地 pull-cloud 消费）
samples/                   试跑样例，仅供参考
.github/workflows/bili.yml 定时与分片编排
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--delay` | `1.2,2.2` | 请求间隔区间（秒），落在**请求之前** |
| `--total` | `1` | 分片总数，workflow 里用 4 |
| `--limit` | `0` | **每个分片**只跑前 N 个（4 片即 4N），dry-run 用 |
| `--tag` | 空 | 批次标签，留空取北京时间当前分钟 |

单轮实测（2026-11-09 00:20，清单 2,042 个）：

| job | 耗时 | 说明 |
|---|---|---|
| snap（4 片） | 15.9 / 16.6 / 16.7 / 18.6 分钟 | 每片约 500 个，`--delay` 1.2–2.2s |
| expand | **25.2 分钟** | 24 次 newlist + 256 个新视频 × 3 请求 = 778 次，**瓶颈在这里** |
| commit | 0.1 分钟 | |
| 整轮 | **25.5 分钟** | 快照与扩样并行，取较慢者 |

清单从 2,042 涨到 3,750 时，每片约 937 个 ≈ 27 分钟，仍在 `snap` 的 45 分钟超时内；
`expand` 的耗时取决于**当轮新视频数**（越往后越少），已把它的超时从 30 提到 45 分钟。
