"""一次性诊断：区分"出口 IP 被拦"与"缺少 buvid3"。

四种情形依次打同一个接口，把 HTTP 状态、业务 code、响应片段写进 diag.json。
诊断完即可删除本文件与 .github/workflows/diag.yml。
"""
import json
import time

import requests

BV = "BV1GJ411x7h7"          # 任意公开稿件
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
API = "https://api.bilibili.com"


def brief(r):
    try:
        j = r.json()
        return {"status": r.status_code, "code": j.get("code"),
                "message": str(j.get("message"))[:80],
                "text": r.text[:160]}
    except ValueError:
        return {"status": r.status_code, "code": None,
                "text": r.text[:160].replace("\n", " ")}


def main():
    out = {}
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/",
                      "Origin": "https://www.bilibili.com"})

    time.sleep(1)
    out["A_裸请求_无buvid3"] = brief(s.get(f"{API}/x/web-interface/view", params={"bvid": BV}, timeout=20))

    spi = s.get(f"{API}/x/frontend/finger/spi", timeout=20)
    out["B_spi接口"] = brief(spi)
    b3 = b4 = None
    try:
        d = spi.json()["data"]
        b3, b4 = d.get("b_3"), d.get("b_4")
        out["B_buvid3"] = b3
    except Exception as e:                       # noqa: BLE001
        out["B_err"] = repr(e)

    if b3:
        s.cookies.set("buvid3", b3, domain=".bilibili.com")
        if b4:
            s.cookies.set("buvid4", b4, domain=".bilibili.com")
        time.sleep(1.5)
        out["C_带buvid3_view"] = brief(s.get(f"{API}/x/web-interface/view", params={"bvid": BV}, timeout=20))
        time.sleep(1.5)
        out["D_带buvid3_newlist"] = brief(
            s.get(f"{API}/x/web-interface/newlist", params={"rid": 1, "pn": 1, "ps": 20, "type": 0}, timeout=20))

    out["_出口IP"] = requests.get("https://api.ipify.org", timeout=15).text.strip()
    with open("diag.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
