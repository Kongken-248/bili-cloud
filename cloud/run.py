"""云端采集入口：只做两件事 —— 快照（数值）与扩样（新视频元数据）。

不做的事（有意为之）：
  * 不采弹幕正文（seg.so）
  * 不采评论正文（reply/wbi/main 需要 SESSDATA）
  * 不写数据库 —— 只产出 JSON，由本地 `crawler.py pull-cloud` 合并

用法：
  python cloud/run.py --mode snapshot --shard 0 --total 4 --tag "2026-11-08 15:00"
  python cloud/run.py --mode expand --limit 50
"""

import argparse
import datetime
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili  # noqa: E402

# 12 个分区的一级 id。newlist 只认一级 id 查询，返回的视频挂的是二级 tid。
PARTITIONS = {
    1: "动画", 3: "音乐", 4: "游戏", 5: "娱乐", 36: "知识", 119: "鬼畜",
    129: "舞蹈", 155: "时尚", 160: "生活", 167: "国创", 181: "影视", 188: "科技",
}

NEWLIST_PAGES = 2      # 第 2 页几乎全是重复，再翻没有收益
NEWLIST_PS = 20


def cst_now():
    """GitHub runner 是 UTC，批次标签统一按北京时间。"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def fmt_tag(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def file_stamp(dt):
    return dt.strftime("%Y%m%d_%H%M")


def load_bvids(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_bvids(path, bvids):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(set(bvids)), f, ensure_ascii=False, indent=0)


def shard_of(bvid, total):
    """稳定分片：同一 bvid 永远落到同一片，不会重采也不会漏采。"""
    return zlib.crc32(bvid.encode("utf-8")) % total


def build_snapshot(stat):
    return {
        "view": stat.get("view"),
        "like": stat.get("like"),
        "coin": stat.get("coin"),
        "fav": stat.get("favorite"),
        "share": stat.get("share"),
        "reply": stat.get("reply"),
        "danmaku": stat.get("danmaku"),
        "his_rank": stat.get("his_rank"),
        "now_rank": stat.get("now_rank"),
    }


def build_video(bvid, v):
    dim = v.get("dimension") or {}
    owner = v.get("owner") or {}
    return {
        "bvid": bvid,
        "aid": v.get("aid"),
        "cid": v.get("cid"),
        "title": v.get("title"),
        "descr": v.get("desc"),
        "pubdate": v.get("pubdate"),
        "duration": v.get("duration"),
        "tid": v.get("tid"),
        "tid_v2": v.get("tid_v2"),
        "tname": v.get("tname"),
        "width": dim.get("width"),
        "height": dim.get("height"),
        "videos_cnt": v.get("videos"),
        "copyright": v.get("copyright"),
        "owner_mid": owner.get("mid"),
        "owner_name": owner.get("name"),
    }


def run_snapshot(client, args, batch):
    bvids = load_bvids(args.data)
    if args.total > 1:
        bvids = [b for b in bvids if shard_of(b, args.total) == args.shard]
    if args.limit:
        bvids = bvids[: args.limit]

    print(f"[snapshot] 批次 {batch} 分片 {args.shard}/{args.total} 目标 {len(bvids)} 个")
    snaps, failed = [], []
    for i, bvid in enumerate(bvids, 1):
        d = client.view(bvid)
        if d.get("code") == 0 and isinstance(d.get("data"), dict):
            snaps.append({"bvid": bvid, **build_snapshot(d["data"].get("stat") or {})})
        else:
            failed.append({"bvid": bvid, "code": d.get("code"), "message": d.get("message")})
        if i % 100 == 0:
            print(f"  -- {i}/{len(bvids)}，累计请求 {client.requests}")
    print(f"[snapshot] 完成 {len(snaps)}/{len(bvids)}，失败 {len(failed)}")
    return {"snapshots": snaps, "failed": failed}


def run_expand(client, args, batch):
    known = set(load_bvids(args.data))
    print(f"[expand] 批次 {batch} 已知 {len(known)} 个，开始扫描 12 区 newlist")

    seen, candidates = set(), []
    for rid, name in PARTITIONS.items():
        for pn in range(1, NEWLIST_PAGES + 1):
            d = client.newlist(rid, pn)
            arcs = ((d.get("data") or {}).get("archives")) or []
            new = 0
            for a in arcs:
                bvid = a.get("bvid")
                if not bvid or bvid in known or bvid in seen:
                    continue
                seen.add(bvid)
                candidates.append({"bvid": bvid, "rid": rid, "source": "newlist",
                                   "tid": a.get("tid"), "tname": a.get("tname")})
                new += 1
            print(f"  [{name}] 第{pn}页 {len(arcs)} 条，新增 {new}")

    if args.limit:
        candidates = candidates[: args.limit]

    print(f"[expand] 待采新视频 {len(candidates)} 个（每个 3 请求：view / tags / card）")
    videos, tags, uppers, snaps, failed = [], {}, {}, [], []
    seen_mids = set()

    for i, c in enumerate(candidates, 1):
        bvid = c["bvid"]
        d = client.view(bvid)
        if d.get("code") != 0 or not isinstance(d.get("data"), dict):
            failed.append({"bvid": bvid, "code": d.get("code"), "message": d.get("message")})
            continue
        v = d["data"]
        videos.append(build_video(bvid, v))
        snaps.append({"bvid": bvid, **build_snapshot(v.get("stat") or {})})

        t = client.tags(bvid)
        if t.get("code") == 0 and isinstance(t.get("data"), list):
            tags[bvid] = [{"tag_id": x.get("tag_id"), "tag_name": x.get("tag_name"),
                           "type": x.get("type")} for x in t["data"]]

        mid = (v.get("owner") or {}).get("mid")
        if mid and mid not in seen_mids:
            seen_mids.add(mid)
            card = client.card(mid)
            cd = card.get("data") or {}
            if card.get("code") == 0:
                uppers[str(mid)] = {"name": (cd.get("card") or {}).get("name"),
                                    "fans": cd.get("follower"),
                                    "archive_count": cd.get("archive_count")}
        if i % 20 == 0:
            print(f"  -- {i}/{len(candidates)}，累计请求 {client.requests}")

    print(f"[expand] 完成 {len(videos)}/{len(candidates)}，失败 {len(failed)}")
    return {"videos": videos, "tags": tags, "uppers": uppers,
            "snapshots": snaps, "candidates": candidates, "failed": failed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["snapshot", "expand"], required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--total", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个，dry-run 用")
    ap.add_argument("--tag", default="", help="批次标签，留空取北京时间当前分钟")
    ap.add_argument("--data", default="data/bvids.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--delay", default="1.2,2.2", help="请求间隔区间，单位秒")
    args = ap.parse_args()

    if args.tag:
        batch = args.tag
    else:
        batch = fmt_tag(cst_now())
    stamp = batch.replace("-", "").replace(":", "").replace(" ", "_")[:13]

    lo, hi = (float(x) for x in args.delay.split(","))
    client = bili.Client(delay=(lo, hi))

    # dry-run（--limit）的产物放进 out/dryrun/：pull-cloud 只扫 out/*.json，
    # 子目录不会被合并。否则试跑会造出一个只有几十条的假批次，污染时间序列。
    if args.limit:
        args.out = os.path.join(args.out, "dryrun")

    try:
        result = run_snapshot(client, args, batch) if args.mode == "snapshot" \
            else run_expand(client, args, batch)
    except bili.Blocked as e:
        print(f"!!! 致命：{e}")
        print("!!! 出口 IP 被 B 站拦截，GitHub Actions 方案在本仓库不可用。")
        sys.exit(2)

    payload = {
        "batch": batch,
        "mode": args.mode,
        "shard": args.shard,
        "total": args.total,
        "generated_at": fmt_tag(cst_now()),
        "requests": client.requests,
        **result,
    }

    os.makedirs(args.out, exist_ok=True)
    name = f"snap_{stamp}_shard{args.shard}.json" if args.mode == "snapshot" \
        else f"expand_{stamp}.json"
    path = os.path.join(args.out, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[写入] {path}")

    # 扩样的成果要回写已知清单，否则下一轮会重复采同一批
    if args.mode == "expand" and result["videos"]:
        save_bvids(args.data, list(known_all(result, args.data)))
        print(f"[清单] {args.data} 已更新")


def known_all(result, data_path):
    cur = set(load_bvids(data_path))
    cur.update(v["bvid"] for v in result["videos"])
    return cur


if __name__ == "__main__":
    main()
