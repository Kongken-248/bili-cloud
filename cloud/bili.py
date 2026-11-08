"""B 站公开接口最小客户端 —— 云端专用，全程不带 cookie（buvid3 例外，见下）。

设计要点：
1. 首次请求前先取 buvid3：**没有 buvid3 的请求会被直接判 HTTP 412**（2026-11-08
   在 GitHub runner 实测：裸请求 412，补上 buvid3 后 view/newlist 均 code=0，
   与出口 IP 无关）。buvid3 由免登录的 finger/spi 接口发放，不是账号凭据；
2. 每个请求前 sleep（限速落在请求之前，不是之后），带随机抖动；
3. 失败退避 5*2^n + 抖动；
4. 补了 buvid3 仍 412 / code -412 才抛 Blocked —— 那才是真的出口 IP 被风控；
5. 稿件不可见（-404/62002/62004/62012）当作"跳过"，不算错误。
"""

import random
import time

import requests

API = "https://api.bilibili.com"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 稿件已删除 / 私密 / 审核中：重试也没有意义
SKIP_CODES = {-404, 62002, 62004, 62012}


class Blocked(Exception):
    """IP 被风控拦截，整个 job 应立即失败退出。"""


def _backoff(attempt):
    return 5 * (2 ** attempt) + random.uniform(0, 1)


class Client:
    def __init__(self, delay=(1.2, 2.2), retries=3):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        self.delay = delay
        self.retries = retries
        self.requests = 0
        self._buvid_done = False

    def ensure_buvid3(self):
        """取一次 buvid3/buvid4。spi 免登录、不消耗风控额度，失败也不阻断。"""
        if self._buvid_done:
            return
        self._buvid_done = True
        try:
            d = (self.s.get(API + "/x/frontend/finger/spi", timeout=20).json() or {}).get("data") or {}
        except (requests.RequestException, ValueError):
            return
        for key, name in (("b_3", "buvid3"), ("b_4", "buvid4")):
            if d.get(key):
                self.s.cookies.set(name, d[key], domain=".bilibili.com")

    def _get(self, path, params):
        self.ensure_buvid3()
        last = None
        for attempt in range(self.retries):
            time.sleep(random.uniform(*self.delay))
            self.requests += 1
            try:
                r = self.s.get(API + path, params=params, timeout=20)
            except requests.RequestException as e:
                last = {"code": -1, "message": str(e), "data": None}
                if attempt == self.retries - 1:
                    return last
                time.sleep(_backoff(attempt))
                continue

            if r.status_code == 412:
                raise Blocked("HTTP 412（已带 buvid3 仍被拦，判断为出口 IP 风控）")
            if r.status_code == 429:
                if attempt == self.retries - 1:
                    return {"code": -429, "message": "rate limited", "data": None}
                time.sleep(_backoff(attempt))
                continue

            try:
                j = r.json()
            except ValueError:
                last = {"code": -1, "message": "非 JSON 响应", "data": None}
                if attempt == self.retries - 1:
                    return last
                time.sleep(_backoff(attempt))
                continue

            code = j.get("code")
            if code == -412:
                raise Blocked("code -412（已带 buvid3 仍被拦，判断为出口 IP 风控）")
            if code == 0:
                return j
            if code in SKIP_CODES:
                return j                      # 稿件不可见：调用方按 failed 记录
            last = j
            if attempt == self.retries - 1:
                return last
            time.sleep(_backoff(attempt))

        return last or {"code": -1, "message": "unknown", "data": None}

    # ---------------- 具体接口 ----------------

    def view(self, bvid):
        """稿件详情：元数据 + stat 计数，一次拿齐。"""
        return self._get("/x/web-interface/view", {"bvid": bvid})

    def tags(self, bvid):
        return self._get("/x/tag/archive/tags", {"bvid": bvid})

    def card(self, mid):
        """UP 主：粉丝数、投稿数。按 mid 去重后再调用。"""
        return self._get("/x/web-interface/card", {"mid": mid})

    def newlist(self, rid, pn=1, ps=20):
        """分区最新投稿。注意：第 2 页几乎全部与第 1 页重复，翻页收益极低。"""
        return self._get("/x/web-interface/newlist",
                         {"rid": rid, "pn": pn, "ps": ps, "type": 0})
