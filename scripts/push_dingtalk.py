#!/usr/bin/env python3
"""
push_dingtalk.py — 把单词记录贴推送到指定钉钉群

方案：钉钉群「自定义机器人」Webhook（不依赖任何连接器/插件，老师自己就能配）
官方文档：https://open.dingtalk.com/document/orgapp/custom-robot-access

============================================================
两种消息形态（脚本会自动选）
============================================================
A. Markdown 文字卡（**始终可用**）
   把整张记录贴的内容排成群里可读的格式：母词、词族、公式、例句、明日预告。
   同学不用点开任何东西就能读，手机上是原生排版，转发也方便。

B. 图片消息（需要机器人支持 media/upload）
   钉钉部分自定义机器人的 webhook token 调 media/upload 会返回 40014，
   此时脚本**自动降级**为只发文字卡，不中断、不报错。

============================================================
用法
============================================================
    python push_dingtalk.py --data day01-care.json          # 发文字卡，同时尝试发图
    python push_dingtalk.py --data day01-care.json --no-img  # 只发文字卡
    python push_dingtalk.py --img day01-care.png             # 只发图（不推荐，群里没上下文）
    python push_dingtalk.py --data x.json --at 13800138000
    python push_dingtalk.py --self-test                      # 测通路

============================================================
配置（一次性，约 1 分钟）
============================================================
1. 钉钉群 → 群设置 → 机器人 → 添加机器人 → 自定义（Webhook）
2. 安全设置勾选「加签」，复制 Webhook 与 SEC 密钥
3. 二选一：
   a) 环境变量 DINGTALK_WEBHOOK / DINGTALK_SECRET
   b) 技能目录 dingtalk.json：{"webhook": "...", "secret": "SEC..."}
"""
import argparse
import base64
import datetime
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
CONF_CANDIDATES = [
    SKILL_DIR / "dingtalk.json",
    Path.home() / ".workbuddy" / "dingtalk.json",
]
UPLOAD_URL = "https://oapi.dingtalk.com/media/upload?access_token={token}&type=image"
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def die(msg):
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"[提示] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- 配置

def load_conf(webhook_override="", secret_override=""):
    if webhook_override:
        return webhook_override.strip(), (secret_override or "").strip()
    webhook = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    secret = os.environ.get("DINGTALK_SECRET", "").strip()
    if webhook:
        return webhook, secret
    for p in CONF_CANDIDATES:
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                die(f"配置文件解析失败：{p} → {e}")
            w = (d.get("webhook") or "").strip()
            s = (d.get("secret") or "").strip()
            if w:
                return w, s
    die(
        "没找到钉钉机器人配置。请任选一种方式：\n"
        "  1) 设置环境变量 DINGTALK_WEBHOOK / DINGTALK_SECRET\n"
        f"  2) 新建配置文件 {CONF_CANDIDATES[0]}\n"
        '     {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx", "secret": "SECxxx"}'
    )


def signed_url(webhook, secret):
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}"
    h = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256)
    sign = urllib.parse.quote_plus(base64.b64encode(h.digest()))
    joiner = "&" if "?" in webhook else "?"
    return f"{webhook}{joiner}timestamp={ts}&sign={sign}"


def access_token(webhook):
    q = urllib.parse.urlparse(webhook).query
    return urllib.parse.parse_qs(q).get("access_token", [""])[0]


# ---------------------------------------------------------------- 发送台账
# 每次成功推送都落一行 JSONL，用于「今天推过没」的判定与事后对账。

def log_path_for(data_path):
    if data_path:
        return Path(data_path).parent / ".push-log.jsonl"
    return SKILL_DIR / "push-log.jsonl"


def read_log(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_log(p, rec):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def already_pushed_today(log_file, day_num, word):
    """查今天是否已经发过这个词。

    返回最近一条相关记录；state 为 uncertain 表示「发出去了但没回执」，
    调用方应按「已发送」处理，除非人工确认没收到。
    """
    import datetime
    today = datetime.date.today().isoformat()
    hit = None
    for rec in read_log(log_file):
        # 按日期+day+词三元组判重：同一天两张卡（同 day 号、不同词）都应放行，
        # 只有同一张卡重复推送才拦截。旧逻辑「day 或 word 任一命中」会误拦第二张卡。
        if rec.get("date") == today and rec.get("day") == day_num and rec.get("word") == word:
            hit = rec
    return hit


# ---------------------------------------------------------------- 网络

class UncertainError(Exception):
    """请求已发出但没等到回执：钉钉那边可能已经投递成功了。

    这种情况绝不能自动重发——群里会收到两条。必须让人去群里核对。"""


def _is_uncertain(e):
    """判断异常是否属于「可能已送达」。

    超时 / 读响应失败 = 请求已经完整发出去了，服务端大概率已处理完并投递到群，
    只是回执在路上丢了。这类不能重试。
    连接被拒 / DNS 解析失败 / 网络不可达 = 请求根本没出去，可以安全重试。
    """
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return True
        # 网络不可达、连接被拒这类是「没出去」，交给调用方重试
        return False
    # 读响应阶段被断开（RemoteDisconnected / ConnectionResetError 发生在写出之后）
    if isinstance(e, (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError)):
        return True
    return False


def post_json(url, payload, timeout=25, retries=3):
    """带重试的 POST。

    只对「请求肯定没送达」的异常重试（DNS、连接被拒、网络不可达）。
    对「可能已送达」的异常（超时、连接被重置）**立即抛出 UncertainError，不重试**，
    否则钉钉已经投递、本地又重发，群里就会出现两条一样的消息。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "ignore")
            break
        except urllib.error.HTTPError as e:
            # HTTP 层明确返回了状态码，说明服务端已响应，5xx 也不重试
            die(f"HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:300]}")
        except Exception as e:
            if _is_uncertain(e):
                raise UncertainError(f"{type(e).__name__} {e}") from e
            last_err = e
            if attempt < retries:
                wait = attempt * 2
                print(f"[重试] 第 {attempt} 次连接失败（{type(e).__name__}，请求未送达），"
                      f"{wait}s 后重试…", file=sys.stderr)
                time.sleep(wait)
            else:
                die(f"连接失败（已重试 {retries} 次，消息未送达）：{type(last_err).__name__} {last_err}")
    try:
        res = json.loads(body)
    except Exception:
        die(f"返回不是 JSON：{body[:300]}")
    if res.get("errcode", 0) != 0:
        hint = {
            310000: "签名校验失败，检查 secret 是否正确、服务器时间是否准确",
            300001: "access_token 无效，检查 webhook 地址",
            130101: "机器人被停用或已被移出群",
        }.get(res.get("errcode"), "")
        die(f"钉钉返回错误 {res.get('errcode')}: {res.get('errmsg')}　{hint}")
    return res


def upload_image(webhook, img_path: Path):
    """上传图片拿 media_id。失败返回 None（降级为纯文字，不中断）。"""
    token = access_token(webhook)
    if not token:
        warn("webhook 里没有 access_token，跳过图片")
        return None
    size = img_path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        warn(f"图片 {size/1024/1024:.1f}MB 超过 20MB，跳过图片")
        return None

    boundary = "----wb" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(img_path))[0] or "image/png"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="media"\r\n\r\n0\r\n'
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="media"; filename="{img_path.name}"\r\n'.encode()
    body += f"Content-Type: {ctype}\r\n\r\n".encode()
    body += img_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        UPLOAD_URL.format(token=token), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        warn(f"图片上传失败（{e}），本次只发文字卡")
        return None
    if res.get("errcode", 0) != 0:
        code = res.get("errcode")
        if code == 40014:
            warn("该机器人的 access_token 不支持 media/upload（钉钉 40014），本次只发文字卡。\n"
                 "        如需发图片：改用企业内部应用机器人，或用公网图片链接走 markdown 插图。")
        else:
            warn(f"图片上传失败 {code}: {res.get('errmsg')}，本次只发文字卡")
        return None
    return res.get("media_id")


# ---------------------------------------------------------------- 消息

def send_markdown(url, title, text, at_mobiles):
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": {"atMobiles": at_mobiles, "isAtAll": False},
    }
    post_json(url, payload)


def send_image(url, media_id):
    post_json(url, {"msgtype": "image", "image": {"media_id": media_id}})


def build_card_md(data, extra_text="", img_url="", page_url=""):
    """把词族 JSON 排成群内可读的 Markdown 卡片。"""
    b = data["base"]
    fam = data.get("family", [])
    rule = data.get("rule", {})
    n = int(data.get("day", 1))

    def clean(s):
        """去掉模板里给网页用的 <em>/<b> 标签，markdown 里换成加粗。"""
        import re
        s = re.sub(r"</?(em|b|strong)>", "**", str(s))
        return s.replace("****", "")

    lines = []
    lines.append(f"### DAY{n:02d} · {b['word']} 词族")
    if data.get("theme"):
        lines.append(f"> {data['theme']}")
    lines.append("")

    # 有公网图片就放在最上面，群里一眼看到思维导图
    if img_url:
        lines.append(f"![{b['word']} 词族思维导图]({img_url})")
        lines.append("")
    if page_url:
        lines.append(f"[点这里打开跟读版（可点读发音）]({page_url})")
        lines.append("")

    lines.append(f"**母词 {b['word']}** {b.get('phonetic','')} {b.get('pos','')}　{b.get('meaning','')}")
    lines.append(f"{b.get('example','')}")
    if b.get("exampleZh"):
        lines.append(f"{b['exampleZh']}")
    lines.append("")

    root = data.get("root", {})
    if root:
        lines.append(f"**树枝在哪**：{root.get('part','')} = {root.get('meaning','')}")
        lines.append("")

    if fam:
        lines.append("**衍生词族**")
        for f in fam:
            fx = f"　`{f.get('formula','')}`" if f.get("formula") else ""
            lines.append(f"- **{f['word']}** {f.get('phonetic','')} {f.get('pos','')}　{f.get('meaning','')}{fx}")
            lines.append(f"  {f.get('example','')}")
        lines.append("")

    if rule:
        lines.append(f"**规律**：{clean(rule.get('formula',''))}")
        if rule.get("tip"):
            lines.append("")
            lines.append(f"{clean(rule['tip'])}")
        lines.append("")

    lines.append("---")
    if extra_text:
        lines.append(extra_text)
        lines.append("")
    lines.append("**今天怎么做**：读母词 3 遍 → 顺着树枝往下读 → 拿纸自己在留白处造 2 个词")
    if data.get("nextWord"):
        lines.append(f"\n明日预告：**{data['nextWord']}**")
    return "\n".join(lines)


def self_test(url, webhook):
    print("[1/3] 测试文字消息…")
    send_markdown(url, "通路测试", "### 通路测试\n\n机器人已连通，可在群里忽略本条。", [])
    print("      文字消息 OK")

    print("[2/3] 测试加签时间戳…")
    print(f"      secret {'已配置' if 'sign=' in url else '未配置'}")

    print("[3/3] 测试图片上传…")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAH"
        "ggJ/lWuJlQAAAABJRU5ErkJggg=="
    )
    tmp = Path("_dingtalk_selftest.png")
    tmp.write_bytes(png)
    try:
        mid = upload_image(webhook, tmp)
        if mid:
            send_image(url, mid)
            print("      图片消息 OK —— 完整能力可用")
        else:
            print("      图片不可用 —— 将只发文字卡（不影响每日推送）")
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="推送单词记录贴到钉钉群")
    ap.add_argument("--data", help="词族 JSON（发文字卡，推荐）")
    ap.add_argument("--img", nargs="*", help="图片路径（可选，附在文字卡后）")
    ap.add_argument("--title", default="", help="消息标题")
    ap.add_argument("--text", default="", help="附加说明")
    ap.add_argument("--at", nargs="*", default=[], help="要 @ 的手机号")
    ap.add_argument("--img-url", default="", help="公网图片地址（markdown 内嵌大图，推荐）")
    ap.add_argument("--page-url", default="", help="公网页面地址（可点读的交互版）")
    ap.add_argument("--no-img", action="store_true", help="只发文字卡，不尝试图片")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--webhook", default="", help="临时指定群机器人地址（覆盖配置文件），用于换群试推")
    ap.add_argument("--secret", default="", help="配套的加签密钥")
    ap.add_argument("--skip-if-pushed", action="store_true",
                    help="今天已经推过同一天号就跳过（防重复，配合每日多次触发使用）")
    ap.add_argument("--force", action="store_true",
                    help="忽略今日已推送记录，强制重发（仅在确认群里没收到时用）")
    args = ap.parse_args()

    webhook, secret = load_conf(args.webhook, args.secret)
    url = signed_url(webhook, secret)
    log_file = log_path_for(args.data)

    if args.dry_run:
        print(f"[DRY] webhook ok: {webhook[:46]}...")
        print(f"[DRY] 加签: {'已启用' if secret else '未启用'}")
        return

    if args.self_test:
        self_test(url, webhook)
        return

    if not args.data and not args.img:
        die("请用 --data 指定词族 JSON，或 --img 指定图片")

    data = None
    title = args.title
    if args.data:
        p = Path(args.data)
        if not p.exists():
            die(f"找不到 {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not title:
            title = f"DAY{int(data.get('day',1)):02d} · {data['base']['word']} 词族"

    md = build_card_md(data, args.text, args.img_url, args.page_url) if data else None
    if not title:
        title = "单词记录贴"

    # 防重复：同一天号今天已发过就跳过（每日多次触发时用）
    if data and not args.force:
        hit = already_pushed_today(log_file, int(data.get("day", 0)), data["base"]["word"])
        if hit and args.skip_if_pushed:
            uncertain = hit.get("state") == "uncertain"
            label = "可能已送达（上次超时）" if uncertain else "已推送"
            print(f"[跳过] 今天{label}：DAY{int(hit.get('day',0)):02d} "
                  f"{hit.get('word','')}（{hit.get('ts','')}），不重复发送")
            if uncertain:
                print("[说明] 上次是超时中断，钉钉那边很可能已经投递到群。"
                      "请先到群里看一眼；如果确实没有，再加 --force 补发。")
            return

    if md:
        try:
            send_markdown(url, title, md, args.at)
        except UncertainError as e:
            # 请求已发出但没等到回执：钉钉极可能已投递，绝不能自动重发
            if data:
                append_log(log_file, {
                    "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "date": datetime.date.today().isoformat(),
                    "day": int(data.get("day", 0)),
                    "word": data["base"]["word"],
                    "title": title,
                    "img_url": args.img_url,
                    "page_url": args.page_url,
                    "state": "uncertain",
                })
            print(f"\n[可能已送达] 请求发出后没有等到钉钉回执（{e}）。", file=sys.stderr)
            print("  钉钉大概率已经投递到群了，所以脚本不会自动重发——否则群里会出现两条。",
                  file=sys.stderr)
            print("  处理办法：先到群里确认。", file=sys.stderr)
            print("    · 收到了 → 什么都不用做，已记入台账，今天的任务算完成",
                  file=sys.stderr)
            print("    · 没收到 → 加 --force 重发一次（不要反复重试）", file=sys.stderr)
            sys.exit(2)
        print(f"[1] 文字卡已发送：{title}")

    if args.img and not args.no_img:
        imgs = [Path(p) for p in args.img]
        sent = 0
        for p in imgs:
            if not p.exists():
                warn(f"找不到图片 {p}，跳过")
                continue
            mid = upload_image(webhook, p)
            if not mid:
                continue
            try:
                send_image(url, mid)
            except UncertainError as e:
                warn(f"图片发送后无回执（{e}），可能已送达，不再重试")
                break
            sent += 1
            print(f"[{sent+1}] 图片已发送：{p.name}  ({p.stat().st_size/1024:.0f} KB)")
            time.sleep(0.35)
        if sent == 0:
            print("[完成] 仅文字卡（图片通道不可用）")
        else:
            print(f"[完成] 文字卡 + {sent} 张图片")
    else:
        print("[完成] 仅文字卡")

    # 记台账
    if md and data:
        append_log(log_file, {
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "date": datetime.date.today().isoformat(),
            "day": int(data.get("day", 0)),
            "word": data["base"]["word"],
            "title": title,
            "img_url": args.img_url,
            "page_url": args.page_url,
            "state": "ok",
        })
        print(f"[台账] 已记录到 {log_file.name}")

    print("[提示] 机器人限 20 条/分钟，多天连发请分批。")


if __name__ == "__main__":
    main()
