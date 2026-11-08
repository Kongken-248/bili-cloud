"""一次性脚本：用 GitHub REST API 推送首次提交（沙箱只放行 api.github.com）。

用法：GITHUB_TOKEN=xxx python cloud/_push_once.py
不是采集器的一部分，推完即可删。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER, REPO, BRANCH = "Kongken-248", "bili-cloud", "main"
API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        print(f"  !! {method} {path} -> {e.code} {e.read().decode()[:300]}")
        raise


def tracked_files():
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [p for p in out.split("\n") if p.strip()]


def seed_empty_repo():
    """空仓库上 git/blobs 返回 409，必须先用 contents 接口建出第一个提交。"""
    try:
        call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
        print("仓库已有提交，跳过初始化")
        return False
    except urllib.error.HTTPError:
        pass
    seed = ".gitattributes"
    call("PUT", f"/repos/{OWNER}/{REPO}/contents/{seed}", {
        "message": "init: 仓库初始化",
        "content": base64.b64encode(open(seed, "rb").read()).decode(),
        "branch": BRANCH,
    })
    print(f"初始提交已建立（{seed}）")
    return True


def main():
    seeded = seed_empty_repo()
    files = [p for p in tracked_files() if not (seeded and p == ".gitattributes")]
    print(f"文件 {len(files)} 个")

    tree = []
    for p in files:
        raw = open(p, "rb").read()
        blob = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                    {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree.append({"path": p, "mode": "100755" if p.endswith(".py") else "100644",
                     "type": "blob", "sha": blob["sha"]})
        print(f"  blob {p:45s} {len(raw):>7d} B  {blob['sha'][:8]}")

    ref = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    parent, base_tree = ref["object"]["sha"], ref["object"]["sha"]
    t = call("POST", f"/repos/{OWNER}/{REPO}/git/trees",
             {"base_tree": call("GET", f"/repos/{OWNER}/{REPO}/git/commits/{parent}")["tree"]["sha"],
              "tree": tree})
    c = call("POST", f"/repos/{OWNER}/{REPO}/git/commits",
             {"message": os.environ.get("COMMIT_MSG", "sync files"),
              "tree": t["sha"], "parents": [parent]})
    print("commit", c["sha"][:8])

    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
         {"sha": c["sha"], "force": True})
    print(f"branch {BRANCH} updated -> {c['sha'][:8]}")

    repo = call("GET", f"/repos/{OWNER}/{REPO}")
    print("done:", repo["html_url"], "default_branch =", repo["default_branch"])


if __name__ == "__main__":
    sys.exit(main())
