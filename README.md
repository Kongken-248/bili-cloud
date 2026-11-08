# bili-cloud —— 毕设数据采集的云端补充

只用 GitHub Actions 做两件事，**不碰数据库、不带任何密钥**：

| 模式 | 做什么 | 每轮请求量 |
|---|---|---|
| `snapshot` | 对已知视频清单逐个取 `view`，存 9 项计数 | 视频数 × 1 |
| `expand` | 扫 12 区 `newlist`，新视频取 `view`+`tags`+`card` | 24 + 新视频数 × 3 |

**有意不做**：弹幕正文（`seg.so`）、评论正文（`reply/wbi/main` 需要 SESSDATA 才能翻页）。
这两样永远留在本地 `bili-crawler-v2`，云端零账号风险。

产出是纯 JSON，由本地 `python crawler.py pull-cloud --dir <本仓库>/out` 合并入库。

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

## 首次部署（5 步）

### 1. 在 GitHub 建一个**公开**仓库

公开的原因：快照一轮约 15 分钟，一天两轮 ≈ 900 分钟/月，私有仓库免费额度只有 2000 分钟且不叠加。
公开仓库 Actions 不限量，而且这个仓库里**没有密钥、没有 cookie、没有数据库**，公开无风险。

### 2. 把这个目录单独推上去

⚠️ 注意：`cloud-snapshot` 的上级目录（`C:\Users\window`）本身是一个空的 git 仓库。
所以**不要**在上层执行 `git add .`，一定要进入本目录单独初始化：

```powershell
cd C:\Users\window\WorkBuddy\2026-10-13-14-39-14\cloud-snapshot
git init -b main
git add .
git commit -m "init: bili cloud snapshot + expand"
git remote add origin https://github.com/<你的用户名>/bili-cloud.git
git push -u origin main
```

### 3. 开权限

仓库 → Settings → Actions → General → Workflow permissions → 选 **Read and write permissions**
（否则 `commit` job 推不回去）。

### 4. 先 dry-run，别急着开定时

仓库 → Actions → `bili-cloud` → Run workflow：

| 输入 | 填什么 |
|---|---|
| `mode` | `snapshot` |
| `limit` | `20` |

**这一步是唯一能否决整个方案的地方**：GitHub runner 出口在海外，B 站对海外 IP 风控更严。
- 如果正常返回 20 条快照 → 方案可用，进入第 5 步；
- 如果日志出现 `412` 或 `!!! 致命：出口 IP 被 B 站拦截` → 海外 IP 被拦，改用国内云函数（腾讯云 SCF / 阿里云 FC），代码可原样复用，只换入口。

### 5. 定时已内置

`cron: '0 1,13 * * *'` 已写在 workflow 里，推上去即生效，无需再配置。
（GitHub 定时任务常有 5–15 分钟延迟，对快照无影响——`collect_time` 记的是批次标签，不是执行时刻。）

---

## 日常使用（本地）

```powershell
cd C:\Users\window\WorkBuddy\2026-10-13-14-39-14\bili-crawler-v2

# 1) 拉云端产出
cd ..\cloud-snapshot && git pull && cd ..\bili-crawler-v2

# 2) 合并入库（可重复执行，幂等）
python crawler.py pull-cloud --dir ..\cloud-snapshot\out

# 3) 本地视频变多了，同步清单回云端
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
| `--limit` | `0` | 只跑前 N 个，dry-run 用 |
| `--tag` | 空 | 批次标签，留空取北京时间当前分钟 |

单轮耗时估算：2042 个 bvid ÷ 4 片 × 1.7s ≈ **15 分钟/轮**。
扩样每天新增约 245 个视频，清单会持续变大，但每片只多跑 60 个 ≈ 2 分钟，可忽略。
