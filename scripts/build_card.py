#!/usr/bin/env python3
"""
build_card.py — 把一份词族 JSON 渲染成「单词归纳小记录贴」HTML

用法：
    python build_card.py --data family.json --out daily-card.html
    python build_card.py --demo --out demo.html

JSON 结构见 SKILL.md 第四步 Step 4。
模板默认取 assets/card-template.html 同目录相对路径。
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TPL = HERE.parent / "assets" / "card-template.html"

REQUIRED_KEYS = ["base", "root", "family", "rule"]
MAX_FAMILY = 6


def die(msg):
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(1)


def validate(data):
    for k in REQUIRED_KEYS:
        if k not in data:
            die(f"缺少字段：{k}")
    b = data["base"]
    for k in ["word", "phonetic", "pos", "meaning", "example", "exampleZh"]:
        if k not in b:
            die(f"base 缺少字段：{k}")
    if not data["family"]:
        die("family 不能为空，至少 4 个衍生词")
    if len(data["family"]) > MAX_FAMILY:
        die(f"family 最多 {MAX_FAMILY} 个（A4 单页放不下更多）")
    for i, f in enumerate(data["family"]):
        for k in ["word", "formula", "phonetic", "pos", "meaning", "example", "exampleZh"]:
            if k not in f:
                die(f"family[{i}] 缺少字段：{k}")
    for k in ["formula", "tip"]:
        if k not in data["rule"]:
            die(f"rule 缺少字段：{k}")

    # 例句长度提醒（不阻断，只提示）
    longs = []
    if len(b["example"].split()) > 12:
        longs.append(f"母词例句 {len(b['example'].split())} 词")
    for f in data["family"]:
        n = len(f["example"].split())
        if n > 12:
            longs.append(f"{f['word']} 例句 {n} 词")
    if longs:
        print("[提示] 以下例句超过 12 词，建议精简：" + "；".join(longs))


def js_safe(obj):
    """转成可直接嵌入 <script> 的 JSON，防止 </script> 截断。"""
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s.replace("</", "<\\/")


def render(tpl_text, data):
    payload = f"const CARD = {js_safe(data)};"
    # 替换模板里 const CARD = {...}; 这一段（非贪婪，匹配到第一处 };）
    pattern = re.compile(r"const CARD = \{.*?\n\};", re.S)
    if not pattern.search(tpl_text):
        die("模板中找不到 `const CARD = {...};` 数据块，无法注入")
    out = pattern.sub(lambda m: payload, tpl_text, count=1)

    title = f"DAY{data.get('day', 1):02d} {data['base']['word']} 词族记录贴"
    out = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", out, count=1, flags=re.S)
    return out


DEMO = {
    "day": 1,
    "theme": "加 -ful 与 -less：有和没有",
    "stage": "小学高年级 · 每日 1 组",
    "date": "",
    "base": {
        "word": "help",
        "phonetic": "/help/",
        "pos": "v. / n.",
        "meaning": "帮助；帮忙",
        "example": "Can you help me carry this box?",
        "exampleZh": "你能帮我搬这个箱子吗？",
    },
    "root": {
        "part": "help",
        "meaning": "帮、搭把手",
        "logic": "help 就是这条绳子的起点。在它后面加不同的小尾巴，就变出了“爱帮忙的人”“能帮忙的”“帮不上忙的”。本质没变，只是换了个说法。",
    },
    "family": [
        {"word": "helpful", "formula": "help + ful", "phonetic": "/ˈhelpfl/", "pos": "adj.",
         "meaning": "有帮助的；乐于帮忙的",
         "example": "Your map is very helpful, thank you.", "exampleZh": "你的地图很有用，谢谢。"},
        {"word": "helpless", "formula": "help + less", "phonetic": "/ˈhelpləs/", "pos": "adj.",
         "meaning": "帮不上忙的；无助的",
         "example": "The little cat looked helpless in the rain.", "exampleZh": "小猫在雨里看起来好无助。"},
        {"word": "helper", "formula": "help + er", "phonetic": "/ˈhelpə(r)/", "pos": "n.",
         "meaning": "帮手；帮忙的人",
         "example": "You are a great helper in class.", "exampleZh": "你在班里是个好帮手。"},
        {"word": "helpfully", "formula": "helpful + ly", "phonetic": "/ˈhelpfəli/", "pos": "adv.",
         "meaning": "热心地；有帮助地",
         "example": "She helpfully showed me the way.", "exampleZh": "她热心地给我指了路。"},
        {"word": "helpline", "formula": "help + line", "phonetic": "/ˈhelplaɪn/", "pos": "n.",
         "meaning": "求助热线",
         "example": "Call the helpline if you get lost.", "exampleZh": "如果迷路了就打求助热线。"},
    ],
    "rule": {
        "formula": "词 + <em>-ful</em> = 充满…的　｜　词 + <em>-less</em> = 没有…的",
        "tip": "把这条公式搬到别的词上试试：care → <b>careful / careless</b>，use → <b>useful / useless</b>，hope → <b>hopeful / hopeless</b>。你会发现，只要记住一个词，就能自己“造”出一个反义词。",
    },
    "nextWord": "care",
    "speakRate": 0.85,
}


def main():
    ap = argparse.ArgumentParser(description="渲染单词归纳小记录贴")
    ap.add_argument("--data", help="词族 JSON 文件路径")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--tpl", default=str(DEFAULT_TPL), help="模板路径")
    ap.add_argument("--demo", action="store_true", help="用内置示例数据渲染")
    args = ap.parse_args()

    tpl_path = Path(args.tpl)
    if not tpl_path.exists():
        die(f"模板不存在：{tpl_path}")
    tpl_text = tpl_path.read_text(encoding="utf-8")

    if args.demo:
        data = DEMO
    else:
        if not args.data:
            die("请用 --data 指定 JSON，或用 --demo 生成示例")
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))

    validate(data)
    html = render(tpl_text, data)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[完成] {out}  （{len(html)/1024:.1f} KB，{len(data['family'])} 个衍生词）")


if __name__ == "__main__":
    main()
