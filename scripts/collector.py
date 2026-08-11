#!/usr/bin/env python3
"""
书法集字作品生成器 (Calligraphy Collector) v2
从 shufazidian.com 抓取单字书法图片，按排版格式整合成集字作品
"""

import asyncio
import argparse
import os
import sys
import io
import base64
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---- 配置 ----
BASE_URL = "http://shufazidian.com"
SEARCH_URL = f"{BASE_URL}/s.php"
CHAR_SIZE = 200
PADDING = 20
MARGIN = 40
BG_COLOR = (248, 244, 235)
MAX_VERSIONS = 3  # 每个字符最多保留几个版本

# 书体到下拉框value的映射
FONT_STYLE_VALUES = {
    "行书": "8", "行": "8",
    "楷书": "9", "楷": "9",
    "草书": "7", "草": "7",
    "章草": "1",
    "隶书": "6", "隶": "6",
    "魏碑": "5", "魏": "5",
    "简牍": "4",
    "篆书": "3", "篆": "3",
}

# 书体相似回退链（缺字时按顺序尝试）
FONT_FALLBACK = {
    "魏碑": ["楷书", "行书"],
    "草书": ["行书", "楷书"],
    "篆书": ["隶书", "简牍", "楷书"],
    "简牍": ["隶书", "行书"],
    "章草": ["草书", "行书"],
    "楷书": ["行书", "魏碑"],
    "行书": ["楷书", "草书"],
    "隶书": ["楷书", "行书"],
}

# 相似字形替换（书体回退仍无结果时使用）
SIMILAR_CHARS = {
    "楫": ["揖", "辑", "戢"],
    "揖": ["楫", "辑"],
    "担": ["丹", "但"],
    "奋": ["夺", "奔"],
    "潮": ["朝", "嘲"],
    "魂": ["魄", "鬼"],
    "铸": ["寿", "祷"],
    "创": ["仓", "苍"],
}

# 知名书法家别名
AUTHOR_ALIASES = {
    "王羲之": ["王羲之", "羲之"],
    "颜真卿": ["颜真卿", "真卿", "鲁公"],
    "柳公权": ["柳公权", "公权"],
    "欧阳询": ["欧阳询", "阳询"],
    "赵孟頫": ["赵孟頫", "孟頫", "子昂"],
    "苏轼": ["苏轼", "东坡", "子瞻"],
    "米芾": ["米芾", "元章"],
    "黄庭坚": ["黄庭坚", "山谷"],
    "褚遂良": ["褚遂良"],
    "虞世南": ["虞世南"],
    "董其昌": ["董其昌", "玄宰"],
    "文徵明": ["文徵明", "文征明", "徵明"],
    "怀素": ["怀素"],
    "张旭": ["张旭"],
    "孙过庭": ["孙过庭"],
    "王献之": ["王献之", "献之"],
    "智永": ["智永"],
    "蔡襄": ["蔡襄"],
    "赵佶": ["赵佶", "宋徽宗", "徽宗"],
    "唐寅": ["唐寅", "伯虎"],
    "邓石如": ["邓石如", "完白"],
    "伊秉绶": ["伊秉绶"],
}


def get_author_aliases(author_name):
    for key, aliases in AUTHOR_ALIASES.items():
        if author_name in aliases or key in author_name:
            return aliases
    return [author_name]


def match_author(result_author, target_author):
    aliases = get_author_aliases(target_author)
    result_clean = result_author.strip().replace(" ", "").replace("\u3000", "")
    for alias in aliases:
        if alias in result_clean:
            return True
    return False


def get_font_value(font_style):
    for key, val in FONT_STYLE_VALUES.items():
        if font_style in key or key in font_style:
            return val
    return "8"


async def search_and_scrape(browser, char, font_style, scroll_passes=6):
    """搜索单个汉字并返回所有有效结果"""
    page = await browser.new_page()
    results = []

    try:
        await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)

        font_val = get_font_value(font_style)
        try:
            select = page.locator("#sort").first
            await select.select_option(value=font_val)
        except Exception:
            pass

        search_box = page.locator("#wd").first
        await search_box.clear()
        await search_box.fill(char)
        await search_box.press("Enter")
        await asyncio.sleep(2)

        try:
            await page.wait_for_selector("div.j", timeout=15000)
        except PWTimeout:
            return results

        last_height = 0
        for _ in range(scroll_passes):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.6)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        items = page.locator("div.j")
        count = await items.count()

        for i in range(min(count, 80)):
            try:
                item = items.nth(i)

                author_el = item.locator("a.btnSFJ")
                author = "未知"
                if await author_el.count() > 0:
                    author = (await author_el.inner_text()).strip()

                img_el = item.locator("div.mbpho a img")
                link_el = item.locator("div.mbpho a")

                img_src = None
                detail_url = None
                if await img_el.count() > 0:
                    img_src = await img_el.get_attribute("src")
                if await link_el.count() > 0:
                    detail_url = await link_el.get_attribute("href")

                if not img_src:
                    continue
                # app下载提示图
                if "app.png" in img_src or "image/app" in img_src:
                    continue
                # 集字/诗词等多字结果路径（黑名单兜底）
                if "/png/" in img_src or "jizi" in img_src.lower() or "shici" in img_src.lower():
                    continue
                # 字形集锦网格图（/shufa6/ 路径通常是多个字形/多个书法家对比）
                if "/shufa6/" in img_src:
                    continue
                # 白名单：只接受 /gq/ 路径的真实单字图
                if "/gq/" not in img_src:
                    continue

                results.append({
                    "author": author,
                    "img_src": img_src,
                    "detail_url": detail_url or img_src,
                    "font_style": font_style,
                })
            except Exception:
                continue

    except Exception as e:
        print(f"  ❌ 搜索异常: {e}")
    finally:
        await page.close()

    return results


def download_image_bytes(url):
    if not url:
        return None
    if not url.startswith("http"):
        url = "https:" + url if url.startswith("//") else f"{BASE_URL}/{url.lstrip('/')}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": BASE_URL,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"    ❌ 下载失败: {e}")
        return None


def is_single_char_image(img_bytes):
    """检测图片是否为单字（非多字图片）。
    先 auto_crop 裁掉留白，再按裁切后的宽高比判断。
    多字图片裁切后通常宽高比 > 6.0（多个字横排）。
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # 用较低阈值裁切，确保能检测到墨迹
        cropped = auto_crop(img, threshold=200)
        w, h = cropped.size
        if w < 10 or h < 10:
            return False
        ratio = w / h if h > 0 else 999
        # 多字横排通常 ratio > 6.0；单字一般在 0.3~5.0 之间
        if ratio > 6.0:
            return False
        if w * h < 100:
            return False
        return True
    except Exception:
        return False


def auto_crop(img, threshold=240):
    gray = img.convert("L")
    pixels = gray.load()
    w, h = gray.size
    left, top, right, bottom = w, h, 0, 0
    step = max(1, min(w, h) // 100)  # 自适应步长，至少1
    for y in range(0, h, step):
        for x in range(0, w, step):
            if pixels[x, y] < threshold:
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    pad = 5
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w, right + pad)
    bottom = min(h, bottom + pad)
    if right > left and bottom > top:
        return img.crop((left, top, right, bottom))
    return img


def process_char_image(img_bytes, target_size=CHAR_SIZE):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None

    img = auto_crop(img)
    img.thumbnail((target_size, target_size), Image.LANCZOS)

    canvas = Image.new("RGB", (target_size, target_size), BG_COLOR)
    x = (target_size - img.width) // 2
    y = (target_size - img.height) // 2
    canvas.paste(img, (x, y))

    enhancer = ImageEnhance.Contrast(canvas)
    canvas = enhancer.enhance(1.3)
    return canvas


def compose_layout(char_images, layout, text):
    n = len(char_images)
    if n == 0:
        return Image.new("RGB", (200, 200), BG_COLOR)

    if layout == "横排":
        total_w = MARGIN * 2 + n * CHAR_SIZE + (n - 1) * PADDING
        total_h = MARGIN * 2 + CHAR_SIZE
        canvas = Image.new("RGB", (total_w, total_h), BG_COLOR)
        for i, (img, _) in enumerate(char_images):
            canvas.paste(img, (MARGIN + i * (CHAR_SIZE + PADDING), MARGIN))
        return canvas

    elif layout == "竖排":
        total_w = MARGIN * 2 + CHAR_SIZE
        total_h = MARGIN * 2 + n * CHAR_SIZE + (n - 1) * PADDING
        canvas = Image.new("RGB", (total_w, total_h), BG_COLOR)
        for i, (img, _) in enumerate(char_images):
            canvas.paste(img, (MARGIN, MARGIN + i * (CHAR_SIZE + PADDING)))
        return canvas

    elif layout == "斗方":
        cols = max(1, int(n ** 0.5))
        rows = (n + cols - 1) // cols
        total_w = MARGIN * 2 + cols * CHAR_SIZE + (cols - 1) * PADDING
        total_h = MARGIN * 2 + rows * CHAR_SIZE + (rows - 1) * PADDING
        canvas = Image.new("RGB", (total_w, total_h), BG_COLOR)
        for i, (img, _) in enumerate(char_images):
            row, col = i // cols, i % cols
            canvas.paste(img, (MARGIN + col * (CHAR_SIZE + PADDING),
                               MARGIN + row * (CHAR_SIZE + PADDING)))
        return canvas

    elif layout == "对联":
        mid = (n + 1) // 2
        left_chars = char_images[:mid]
        right_chars = char_images[mid:]
        max_col = max(len(left_chars), len(right_chars))
        col_h = MARGIN * 2 + max_col * CHAR_SIZE + (max_col - 1) * PADDING
        gap = CHAR_SIZE + PADDING * 2
        total_w = MARGIN * 2 + CHAR_SIZE * 2 + gap
        total_h = col_h
        canvas = Image.new("RGB", (total_w, total_h), BG_COLOR)
        for i, (img, _) in enumerate(left_chars):
            canvas.paste(img, (MARGIN, MARGIN + i * (CHAR_SIZE + PADDING)))
        for i, (img, _) in enumerate(right_chars):
            canvas.paste(img, (MARGIN + CHAR_SIZE + gap,
                               MARGIN + i * (CHAR_SIZE + PADDING)))
        return canvas

    return compose_layout(char_images, "横排", text)


def add_seal(canvas):
    seal_size = 60
    sm = 25
    x = canvas.width - seal_size - sm
    y = canvas.height - seal_size - sm

    seal = Image.new("RGBA", (seal_size, seal_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(seal)
    d.rectangle([2, 2, seal_size - 2, seal_size - 2], outline=(200, 30, 30), width=3)
    d.rectangle([8, 8, seal_size - 8, seal_size - 8], outline=(200, 30, 30), width=1)

    try:
        for fp in ["C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/simsun.ttc"]:
            if os.path.exists(fp):
                font = ImageFont.truetype(fp, 18)
                d.text((seal_size // 2, seal_size // 2), "集\n字",
                       fill=(200, 30, 30), font=font, anchor="mm")
                break
    except Exception:
        pass

    canvas = canvas.convert("RGBA")
    canvas.paste(seal, (x, y), seal)
    return canvas.convert("RGB")


def img_to_b64(img, size=None):
    """PIL Image → base64 string"""
    if size:
        img = img.copy()
        img.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def generate_html(char_infos, layout, text, output_path):
    valid = [(info["image"], info) for info in char_infos if info.get("image")]
    if not valid:
        print("❌ 无有效图片可合成")
        return None

    canvas = compose_layout(valid, layout, text)
    canvas = add_seal(canvas)
    artwork_b64 = img_to_b64(canvas)

    # 构建单字详情卡片（含多版本）
    char_cards_html = ""
    for info in char_infos:
        versions = info.get("versions", [])
        has_any = len(versions) > 0

        if not has_any:
            char_cards_html += f"""
    <div class="char-card missing">
        <div class="placeholder">{info['char']}</div>
        <div class="char-label">{info['char']}</div>
        <div class="char-meta">⚠ 未找到<br>{info.get('note', '')}</div>
    </div>"""
            continue

        # 主图
        main_b64 = img_to_b64(info["image"], 120) if info.get("image") else ""
        main_author = info.get("author", versions[0].get("author", "未知"))
        main_font = info.get("fallback_font", "")
        font_note = f" ({main_font})" if main_font else ""

        variants_html = ""
        if len(versions) > 1:
            variants_html = '<div class="variants-label">备选版本</div><div class="variants-row">'
            for v in versions[1:]:
                if v.get("image"):
                    v_b64 = img_to_b64(v["image"], 70)
                    v_author = v.get("author", "未知")
                    v_font = v.get("font_style", "")
                    variants_html += f"""<div class="variant">
                        <img src="data:image/png;base64,{v_b64}" alt="">
                        <span>{v_author}</span>
                    </div>"""
                else:
                    variants_html += f"""<div class="variant missing-variant">
                        <span>—</span><span>{v.get('note', '')}</span>
                    </div>"""
            variants_html += '</div>'

        char_cards_html += f"""
    <div class="char-card">
        <img src="data:image/png;base64,{main_b64}" alt="{info['char']}">
        <div class="char-label">{info['char']}{font_note}</div>
        <div class="char-meta">{main_author}</div>
        {variants_html}
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>集字作品 - {text}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family:"楷体","KaiTi","STKaiti","SimKai",serif;
    background:#f0e6d3; min-height:100vh;
    display:flex; flex-direction:column; align-items:center;
    padding:30px 20px;
}}
h1 {{ font-size:28px; color:#3d2b1f; margin-bottom:8px; letter-spacing:4px; }}
.subtitle {{ font-size:14px; color:#8b7355; margin-bottom:25px; }}
.artwork-container {{
    background:#fffef5; border:1px solid #d4c5a9;
    box-shadow:0 4px 20px rgba(0,0,0,0.12);
    padding:40px; border-radius:2px; max-width:95vw; overflow-x:auto;
}}
.artwork-container img {{ display:block; max-width:100%; height:auto; }}
.char-grid {{
    display:flex; flex-wrap:wrap; gap:20px;
    justify-content:center; margin-top:30px; max-width:1100px;
}}
.char-card {{
    background:#fffef5; border:1px solid #ddd5c0; border-radius:4px;
    padding:15px; text-align:center; box-shadow:0 2px 8px rgba(0,0,0,0.06);
    width:200px;
}}
.char-card.missing {{
    border:1px dashed #d4a; opacity:0.7;
}}
.char-card img {{ width:120px; height:120px; object-fit:contain; background:{f"#{BG_COLOR[0]:02x}{BG_COLOR[1]:02x}{BG_COLOR[2]:02x}"}; border-radius:2px; }}
.char-card .placeholder {{
    width:120px; height:120px; margin:0 auto;
    background:{f"#{BG_COLOR[0]:02x}{BG_COLOR[1]:02x}{BG_COLOR[2]:02x}"};
    border:2px dashed #c0b090; border-radius:2px;
    display:flex; align-items:center; justify-content:center;
    font-size:48px; color:#c0b090;
}}
.char-label {{ font-size:20px; font-weight:bold; color:#3d2b1f; margin:8px 0 4px; }}
.char-meta {{ font-size:12px; color:#8b7355; line-height:1.5; margin-bottom:6px; }}
.variants-label {{ font-size:11px; color:#a09080; margin-top:6px; border-top:1px dotted #ddd5c0; padding-top:6px; }}
.variants-row {{ display:flex; gap:8px; justify-content:center; flex-wrap:wrap; margin-top:4px; }}
.variant {{ text-align:center; }}
.variant img {{ width:60px; height:60px; border-radius:2px; background:{f"#{BG_COLOR[0]:02x}{BG_COLOR[1]:02x}{BG_COLOR[2]:02x}"}; }}
.variant span {{ display:block; font-size:10px; color:#a09080; margin-top:2px; }}
.variant.missing-variant span {{ color:#d4a; }}
.footer {{ margin-top:30px; font-size:13px; color:#a09080; text-align:center; }}
</style>
</head>
<body>
<h1>集字作品</h1>
<p class="subtitle">原文「{text}」 · 排版：{layout}</p>
<div class="artwork-container">
    <img src="data:image/png;base64,{artwork_b64}" alt="集字作品 - {text}">
</div>
<h2 style="margin-top:35px;font-size:20px;color:#3d2b1f;">单字详情</h2>
<div class="char-grid">
{char_cards_html}
</div>
<p class="footer">由 WorkBuddy 书法集字工具自动生成 · 字源来自 shufazidian.com</p>
</body>
</html>"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


async def try_collect_versions(browser, ch, font_style, author, n_versions=MAX_VERSIONS):
    """
    尝试为一个字收集多个版本。
    先按指定书体搜索，不够再回退到相似书体。
    返回: list of version dicts
    """
    versions = []
    tried_fonts = set()

    # 第一阶段：主书体搜索
    results = await search_and_scrape(browser, ch, font_style, scroll_passes=6)
    tried_fonts.add(font_style)
    print(f"    [{font_style}] {len(results)} 个结果")

    if results:
        versions.extend(_pick_best_versions(results, author, ch, n_versions, font_style))

    # 第二阶段：不够的话回退到相似书体
    fallback_fonts = FONT_FALLBACK.get(font_style, [])
    for fb_font in fallback_fonts:
        if len(versions) >= n_versions:
            break
        if fb_font in tried_fonts:
            continue
        tried_fonts.add(fb_font)

        fb_results = await search_and_scrape(browser, ch, fb_font, scroll_passes=4)
        print(f"    ↳ 回退 [{fb_font}] {len(fb_results)} 个结果")
        if fb_results:
            new_versions = _pick_best_versions(fb_results, author, ch,
                                               n_versions - len(versions), fb_font)
            versions.extend(new_versions)

    # 第三阶段：相似字形（如 楫→揖）
    if not versions and ch in SIMILAR_CHARS:
        for sim_char in SIMILAR_CHARS[ch]:
            if versions:
                break
            print(f"    ↳ 相似字「{sim_char}」代替「{ch}」")
            for fb_font in [font_style] + FONT_FALLBACK.get(font_style, []):
                if fb_font in tried_fonts:
                    continue
                tried_fonts.add(fb_font)
                sim_results = await search_and_scrape(browser, sim_char, fb_font, scroll_passes=4)
                if sim_results:
                    new_versions = _pick_best_versions(sim_results, author, ch,
                                                       n_versions - len(versions), fb_font)
                    # 标记这些版本为替代字
                    for v in new_versions:
                        v["substitute_for"] = ch
                        v["substitute_char"] = sim_char
                    versions.extend(new_versions)
                    break

    return versions[:n_versions]


def _pick_best_versions(results, author, ch, n, font_style):
    """从搜索结果中挑选最佳版本（优先匹配作者，过滤多字图）"""
    if not results:
        return []

    # 匹配作者优先，其他在后
    author_matches = [r for r in results if match_author(r["author"], author)]
    others = [r for r in results if not match_author(r["author"], author)]

    # 交错排列：作者匹配 + 其他，确保多样性
    candidates = []
    max_len = max(len(author_matches), len(others))
    for i in range(max_len):
        if i < len(author_matches):
            candidates.append(author_matches[i])
        if i < len(others) and len(candidates) < n * 2:
            candidates.append(others[i])

    versions = []
    for item in candidates:
        if len(versions) >= n:
            break

        img_bytes = download_image_bytes(item["img_src"])
        if not img_bytes:
            continue

        # 过滤多字图片
        if not is_single_char_image(img_bytes):
            continue

        processed = process_char_image(img_bytes)
        if not processed:
            continue

        versions.append({
            "image": processed,
            "author": item["author"],
            "font_style": font_style,
            "img_src": item["img_src"],
        })

    return versions


async def collect_characters(text, font_style, author):
    """收集所有字的书法图片（含多版本）"""
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf']
    if not chars:
        print("❌ 未找到有效汉字")
        return []
    chars = list(dict.fromkeys(chars))

    print(f"\n🔍 开始集字: 「{text}」")
    print(f"   书体: {font_style} | 书法家: {author} | 字符数: {len(chars)}")
    print("-" * 50)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome", headless=True,
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
    )

    char_infos = []
    try:
        for idx, ch in enumerate(chars):
            print(f"\n[{idx+1}/{len(chars)}] 查询: 「{ch}」")

            versions = await try_collect_versions(browser, ch, font_style, author)

            if not versions:
                print(f"  ⚠ 所有方式均未找到")
                char_infos.append({
                    "char": ch, "image": None, "author": "未找到",
                    "versions": [], "note": "所有书体及相似字均无结果",
                })
                continue

            # 主版本（第一个）
            primary = versions[0]
            fallback_font = ""
            if primary.get("substitute_char"):
                fallback_font = f"≈{primary['substitute_char']}"
            elif primary["font_style"] != font_style:
                fallback_font = primary["font_style"]

            note_parts = []
            if primary.get("substitute_char"):
                note_parts.append(f"用「{primary['substitute_char']}」代替")
            if primary["font_style"] != font_style:
                note_parts.append(f"书体: {primary['font_style']}")

            char_infos.append({
                "char": ch,
                "image": primary["image"],
                "author": primary["author"],
                "versions": versions,
                "fallback_font": fallback_font,
                "note": "; ".join(note_parts) if note_parts else "",
            })

            status = "✅"  # Default
            if primary.get("substitute_char"):
                status = "🔄"
            elif primary["font_style"] != font_style:
                status = "↳"
            ver_info = f" ({len(versions)}版本)" if len(versions) > 1 else ""
            print(f"  {status} 成功 - {primary['author']}{ver_info}")

            await asyncio.sleep(random.uniform(1.0, 2.5))
    finally:
        await browser.close()
        await pw.stop()

    success = sum(1 for c in char_infos if c["image"] is not None)
    print(f"\n{'='*50}")
    print(f"📊 集字完成: {success}/{len(chars)}")
    return char_infos


async def main():
    parser = argparse.ArgumentParser(description="书法集字作品生成器 v2")
    parser.add_argument("--text", required=True, help="文本内容")
    parser.add_argument("--font", default="行书", help="书体")
    parser.add_argument("--author", default="王羲之", help="书法家")
    parser.add_argument("--layout", default="横排", choices=["横排", "竖排", "斗方", "对联"])
    parser.add_argument("--size", type=int, default=200, help="单字尺寸")
    parser.add_argument("--output", default="output/calligraphy_collection.html")
    args = parser.parse_args()

    global CHAR_SIZE
    CHAR_SIZE = args.size

    char_infos = await collect_characters(args.text, args.font, args.author)

    if not any(c["image"] for c in char_infos):
        print("\n❌ 未能获取任何字符图片")
        sys.exit(1)

    output_path = generate_html(char_infos, args.layout, args.text, args.output)
    if output_path:
        print(f"\n🎉 完成! 打开: {output_path.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
