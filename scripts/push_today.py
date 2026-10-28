#!/usr/bin/env python3
"""
push_today.py — GitHub Actions 每天 9:00 调用：决定今天推哪天的卡，然后推。

策略（与本地 WorkBuddy 自动化的进度无缝衔接）：
1. 读 cards/.push-log.jsonl（由 git 维护，跨次运行持久）。
2. 已推的最大 day 编号 N → 今天推 day N+1（该天的 A、B 两张卡）。
3. 今天日期已有 state=ok 的记录 → 全部跳过，防止重复。
4. 某张卡发送后超时（退出码 2）→ 台账已记 uncertain，不再重试，继续下一张。

图片与跟读页地址：
- 图片  https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/site/<file>   （公开仓库即可，零配置）
- 跟读页 https://<owner>.github.io/<repo>/<file>                       （需在仓库开一次 Pages，见 README）
"""
import datetime
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "cards"
LOG = CARDS / ".push-log.jsonl"
SCRIPTS = ROOT / "scripts"


def read_log():
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def today_cn():
    d = os.environ.get("TZ_DATE", "")
    if d:
        return d
    return datetime.datetime.utcnow().astimezone(
        datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def main():
    today = today_cn()
    log = read_log()

    ok_records = [r for r in log if r.get("state", "ok") == "ok" or "state" not in r]
    days_done = sorted({int(r["day"]) for r in ok_records if r.get("day")})
    today_ok = {int(r["day"]) for r in ok_records
                if r.get("date") == today and r.get("day")}

    if today_ok:
        print(f"[跳过] 北京时间 {today} 已推送过 DAY{min(today_ok):02d}，不重复。")
        return 0

    next_day = (days_done[-1] + 1) if days_done else 1
    print(f"[进度] 已推至 DAY{days_done[-1]:02d}，今天推 DAY{next_day:02d}（{today}）。")

    nn = f"{next_day:02d}"
    files = sorted(glob.glob(str(CARDS / f"day{nn}-*.json"))) + \
            sorted(glob.glob(str(CARDS / f"day{nn}b-*.json")))
    if not files:
        print(f"[缺卡] cards/ 里没有 DAY{nn} 的 JSON，请先补内容再运行。")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    owner, _, name = repo.partition("/")
    img_base = f"https://cdn.jsdelivr.net/gh/{repo}@main/site/"
    page_base = f"https://{owner}.github.io/{name}/"

    first = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    theme = first.get("theme", "")

    failed = 0
    for f in files:
        p = Path(f)
        cand = ROOT / "site" / (p.stem + ".png")
        cmd = [sys.executable, str(SCRIPTS / "push_dingtalk.py"),
               "--data", str(p), "--skip-if-pushed",
               "--title", f"DAY{next_day:02d} · {json.loads(p.read_text(encoding='utf-8'))['base']['word']} 词族｜{theme}",
               "--text", "今日打卡：读完在群里回个「1」"]
        if cand.exists():
            cmd += ["--img-url", img_base + cand.name,
                    "--page-url", page_base + p.stem + ".html"]
        print(f"\n=== 推送 {p.name} ===")
        r = subprocess.run(cmd)
        if r.returncode not in (0, 2):
            failed += 1
        if r.returncode == 2:
            print(f"[警告] {p.stem} 超时无回执，可能已送达，台账已记 uncertain，请到群里核对。")

    print("\n[完成] 今天推送结束。" + ("（有失败项，见上方日志）" if failed else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
