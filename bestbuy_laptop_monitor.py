#!/usr/bin/env python3
"""Standalone Best Buy Canada laptop/computer monitor with Telegram notifications.

Usage example:
    python bestbuy_laptop_monitor.py \
        --query "laptop" \
        --min-price 500 \
        --max-price 2000 \
        --max-pages 3 \
        --interval 600 \
        --cdp-url "http://127.0.0.1:9222" \
        --telegram-bot-token "<token>" \
        --telegram-chat-id "<chat_id>"

Notes:
- Requires a running Chromium/Chrome browser with CDP enabled.
  Start Chrome with: chrome.exe --remote-debugging-port=9222
- Can also run headless (omit --cdp-url) or headful (add --headful).
- Products are extracted by DOM scraping after the page fully loads.
- Respect Best Buy Canada's terms of use and local laws before running at scale.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

from playwright.sync_api import Page, sync_playwright


BESTBUY_BASE = "https://www.bestbuy.ca"
DEFAULT_KEYWORDS = ["laptop", "notebook", "computer", "pc", "macbook", "mac mini", "mac studio", "imac"]
DEFAULT_FILTER_FILE = "bestbuy_laptop_filters.json"


@dataclass(frozen=True)
class Product:
    sku: str
    title: str
    price: float | None
    url: str
    rating: str | None = None
    reviews: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Best Buy Canada computers/laptops and send notifications."
    )
    parser.add_argument("--query", default="laptop computer", help="Best Buy search keyword.")
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="Only notify items whose title contains one of these keywords.",
    )
    parser.add_argument(
        "--exclude-keywords",
        nargs="*",
        default=["bag", "sleeve", "stand", "sticker", "case", "charger only", "mouse", "keyboard", "webcam"],
        help="Exclude items whose title contains these keywords.",
    )
    parser.add_argument(
        "--brands",
        nargs="*",
        default=["lenovo", "dell", "hp", "asus", "acer", "msi", "apple", "huawei", "samsung", "razer"],
        help="At least one brand keyword should appear in title (set empty to disable).",
    )
    parser.add_argument(
        "--cpu-keywords",
        nargs="*",
        default=["i5", "i7", "i9", "ryzen 5", "ryzen 7", "ryzen 9", "ultra 5", "ultra 7", "ultra 9", "m1", "m2", "m3", "m4"],
        help="At least one CPU keyword should appear in title (set empty to disable).",
    )
    parser.add_argument(
        "--ram-keywords",
        nargs="*",
        default=["16gb", "32gb", "64gb"],
        help="At least one RAM keyword should appear in title (set empty to disable).",
    )
    parser.add_argument(
        "--storage-keywords",
        nargs="*",
        default=["512gb", "1tb", "2tb", "ssd"],
        help="At least one storage keyword should appear in title (set empty to disable).",
    )
    parser.add_argument(
        "--gpu-keywords",
        nargs="*",
        default=["rtx", "nvidia", "radeon", "arc"],
        help="At least one GPU keyword should appear in title (set empty to disable).",
    )
    parser.add_argument(
        "--filter-file",
        default=DEFAULT_FILTER_FILE,
        help="Optional JSON file to override include/exclude/brand/cpu/ram/storage/gpu filters.",
    )
    parser.add_argument("--min-price", type=float, default=None, help="Minimum price filter.")
    parser.add_argument("--max-price", type=float, default=None, help="Maximum price filter.")
    parser.add_argument("--max-pages", type=int, default=3, help="How many pages to crawl.")
    parser.add_argument("--headful", action="store_true", help="Run browser in non-headless mode.")
    parser.add_argument(
        "--cdp-url",
        default="",
        help="Connect to an existing Chromium browser via CDP, e.g. http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--cdp-timeout",
        type=int,
        default=30_000,
        help="CDP connection timeout in milliseconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Polling interval in seconds. 0 means run once and exit.",
    )
    parser.add_argument(
        "--state-file",
        default=".bestbuy_seen_items.json",
        help="File used to avoid duplicate notifications.",
    )
    parser.add_argument("--telegram-bot-token", default="", help="Telegram bot token.")
    parser.add_argument("--telegram-chat-id", default="", help="Telegram chat ID.")

    # AI evaluation
    parser.add_argument("--ai-base-url", default="", help="OpenAI-compatible API base URL, e.g. http://localhost:1234/v1")
    parser.add_argument("--ai-api-key", default="ollama", help="API key (use any string for local models).")
    parser.add_argument("--ai-model", default="", help="Model name, e.g. deepseek-r1:14b")
    parser.add_argument("--ai-description", default="", help="What you're looking for (used in AI prompt).")
    parser.add_argument("--ai-extra-prompt", default="", help="Extra instructions appended to the AI prompt.")
    parser.add_argument(
        "--min-rating",
        type=int,
        default=0,
        help="Minimum AI rating (1-5) to send notification. 0 = disable AI filter.",
    )
    return parser.parse_args()


def build_search_url(query: str, page: int) -> str:
    params = {"search": query, "page": page}
    return f"{BESTBUY_BASE}/en-ca/search?{urlencode(params)}"


def parse_price(text: str) -> float | None:
    cleaned = re.sub(r"[^0-9.,]", "", text)
    if not cleaned:
        return None

    if cleaned.count(",") > 0 and cleaned.count(".") > 0:
        if cleaned.rfind(",") > cleaned.rfind("."):
            normalized = cleaned.replace(".", "").replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    elif cleaned.count(",") > 0:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            normalized = cleaned.replace(".", "").replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    else:
        normalized = cleaned

    try:
        return float(normalized)
    except ValueError:
        return None


def title_matches_keywords(title: str, keywords: Iterable[str]) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in keywords)


def title_excluded(title: str, exclude_keywords: Iterable[str]) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in exclude_keywords)


def title_matches_all_groups(title: str, groups: dict[str, list[str]]) -> bool:
    t = title.lower()
    for values in groups.values():
        if not values:
            continue
        if not any(v.lower() in t for v in values):
            return False
    return True


def in_price_range(price: float | None, min_price: float | None, max_price: float | None) -> bool:
    if price is None:
        return False
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


def extract_products_from_dom(page: Page) -> list[Product]:
    """DOM scraping fallback: extract products by finding Best Buy product links on the page."""
    try:
        raw: list[dict] = page.evaluate(r"""() => {
            const results = [];
            const seen = new Set();
            // Product page links contain /en-ca/product/ and end with a numeric SKU
            const links = document.querySelectorAll('a[href*="/en-ca/product/"]');
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                // SKU is the last numeric segment (5+ digits) before query string / hash
                const m = href.match(/\/(\d{5,})(?:[/?#]|$)/);
                if (!m) continue;
                const sku = m[1];
                if (seen.has(sku)) continue;
                seen.add(sku);

                // Walk up the DOM to find the product card container
                // (the first ancestor that contains a '$' price string)
                let card = link;
                for (let i = 0; i < 8; i++) {
                    const parent = card.parentElement;
                    if (!parent) break;
                    card = parent;
                    const txt = card.innerText || '';
                    if (txt.includes('$') && txt.length > 30) break;
                }

                // Title: prefer nearest heading; fall back to link text
                const headingEl = card.querySelector('h2,h3,h4,h5,[class*="Name"],[class*="name"],[class*="title"]');
                const title = headingEl
                    ? (headingEl.innerText || headingEl.textContent || '').trim()
                    : (link.innerText || link.textContent || '').trim();
                if (!title || title.length < 5) continue;

                // Price: find the first text node that starts with '$'
                let price = null;
                const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = (node.textContent || '').trim();
                    if (/^\$[0-9]/.test(t)) {
                        const pv = parseFloat(t.replace(/[^0-9.]/g, ''));
                        if (pv > 1 && pv < 100000) { price = pv; break; }
                    }
                }

                const cleanUrl = 'https://www.bestbuy.ca' + href.split('?')[0].split('#')[0];
                results.push({ sku, title, price, url: cleanUrl });
            }
            return results;
        }""")
    except Exception as exc:
        print(f"  [WARN] DOM extraction failed: {exc}", file=sys.stderr)
        return []

    products: list[Product] = []
    for p in raw:
        if p.get("sku") and p.get("title"):
            products.append(Product(
                sku=p["sku"],
                title=p["title"],
                price=p.get("price"),
                url=p["url"],
            ))
    return products


def load_seen(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return set()


def save_seen(state_file: Path, seen: set[str]) -> None:
    state_file.write_text(
        json.dumps(sorted(seen), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def merge_list_option(current: list[str], override: object) -> list[str]:
    if not isinstance(override, list):
        return current
    return [str(x) for x in override]


def apply_filter_file(args: argparse.Namespace) -> None:
    filter_path = Path(args.filter_file)
    if not filter_path.exists():
        return

    try:
        raw = json.loads(filter_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(raw, dict):
        return

    args.keywords = merge_list_option(args.keywords, raw.get("keywords"))
    args.exclude_keywords = merge_list_option(args.exclude_keywords, raw.get("exclude_keywords"))
    args.brands = merge_list_option(args.brands, raw.get("brands"))
    args.cpu_keywords = merge_list_option(args.cpu_keywords, raw.get("cpu_keywords"))
    args.ram_keywords = merge_list_option(args.ram_keywords, raw.get("ram_keywords"))
    args.storage_keywords = merge_list_option(args.storage_keywords, raw.get("storage_keywords"))
    args.gpu_keywords = merge_list_option(args.gpu_keywords, raw.get("gpu_keywords"))


@dataclass
class AIResult:
    score: int
    comment: str

    SCORE_LABELS: dict = None  # type: ignore

    def __post_init__(self) -> None:
        object.__setattr__(self, "SCORE_LABELS", {
            1: "No match", 2: "Possible", 3: "Average", 4: "Good", 5: "Great deal"
        })

    @property
    def label(self) -> str:
        return self.SCORE_LABELS.get(self.score, str(self.score))  # type: ignore


def build_ai_prompt(
    title: str,
    price: float | None,
    query: str,
    description: str,
    extra_prompt: str,
    min_price: float | None,
    max_price: float | None,
) -> str:
    price_str = f"${price:.2f}" if price is not None else "unknown"
    prompt = f"用户想在 Best Buy Canada 购买：{query}。\n"
    if description:
        prompt += f"需求描述：{description}。\n"
    if max_price and min_price:
        prompt += f"价格范围：${min_price:.0f} 到 ${max_price:.0f}。\n"
    elif max_price:
        prompt += f"最高价：${max_price:.0f}。\n"
    prompt += f"\n商品标题：{title}\n价格：{price_str}\n"
    if extra_prompt:
        prompt += f"\n{extra_prompt.strip()}\n"
    prompt += (
        "\n请按 1 到 5 分评估该商品与用户需求的匹配度：\n"
        "1 - 不匹配：规格不符或明显不是用户想要的。\n"
        "2 - 可能匹配：信息不足，需确认。\n"
        "3 - 一般：部分符合，有明显不足。\n"
        "4 - 较好：大部分符合，规格清晰。\n"
        "5 - 非常好：高度匹配，性价比突出。\n"
        "最后一行必须使用格式：\n"
        '"Rating <1-5>: <30字以内建议>"'
    )
    return prompt


def evaluate_with_ai(
    title: str,
    price: float | None,
    query: str,
    args: argparse.Namespace,
) -> AIResult | None:
    if not _OPENAI_AVAILABLE:
        print("[AI] openai package not available, skipping AI evaluation.", file=sys.stderr)
        return None
    if not args.ai_base_url or not args.ai_model:
        return None
    prompt = build_ai_prompt(
        title, price, query,
        args.ai_description, args.ai_extra_prompt,
        args.min_price, args.max_price,
    )
    try:
        client = OpenAI(base_url=args.ai_base_url, api_key=args.ai_api_key)
        response = client.chat.completions.create(
            model=args.ai_model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that evaluates whether a Best Buy Canada product matches the user's requirements."},
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        answer = response.choices[0].message.content or ""
        score = 1
        comment = ""
        rating_line_idx = None
        for idx, line in enumerate(answer.split("\n")):
            m = re.match(r".*Rating[^1-5]*([1-5])[:\s]*(.*)", line)
            if m:
                score = int(m.group(1))
                comment = m.group(2).strip()
                rating_line_idx = idx
        lines = answer.split("\n")
        if not comment.strip() and rating_line_idx is not None and rating_line_idx > 0:
            comment = lines[rating_line_idx - 1]
        comment = " ".join(comment.split()).strip()
        return AIResult(score=score, comment=comment)
    except Exception as exc:
        print(f"[AI] Evaluation failed: {exc}", file=sys.stderr)
        return None


def format_product_message(item: Product, query: str, ai_result: "AIResult | None" = None) -> str:
    price_display = f"${item.price:.2f}" if item.price is not None else "N/A"
    lines = [
        f"[Best Buy CA] {query}",
        item.title,
        f"Price: {price_display}",
    ]
    if item.rating:
        lines.append(f"Rating: {item.rating}")
    if ai_result is not None:
        lines.append(f"AI [{ai_result.score}/5 {ai_result.label}]: {ai_result.comment}")
    lines.append(item.url)
    return "\n".join(lines)


def send_telegram(bot_token: str, chat_id: str, message: str) -> None:
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode("utf-8")
    req = Request(api_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, timeout=20) as resp:
        status = getattr(resp, "status", 200)
        if status >= 300:
            raise RuntimeError(f"Telegram API returned status {status}.")


def run_once(args: argparse.Namespace) -> int:
    apply_filter_file(args)
    state_file = Path(args.state_file)
    seen = load_seen(state_file)
    all_candidates: list[Product] = []

    with sync_playwright() as p:
        if args.cdp_url:
            browser = p.chromium.connect_over_cdp(args.cdp_url, timeout=args.cdp_timeout)
            if browser.contexts:
                context = browser.contexts[0]
                own_context = False
            else:
                context = browser.new_context()
                own_context = True
        else:
            browser = p.chromium.launch(headless=not args.headful)
            context = browser.new_context()
            own_context = True
        page = context.new_page()

        for page_num in range(1, args.max_pages + 1):
            search_url = build_search_url(args.query, page_num)
            print(f"[Page {page_num}] {search_url}")

            page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)

            # Wait for product links to appear in the DOM
            product_link_sel = 'a[href*="/en-ca/product/"]'
            try:
                page.wait_for_selector(product_link_sel, timeout=20_000)
                print("  -> Search results loaded")
            except Exception:
                print("  -> Timed out waiting for product links, proceeding anyway...")
            # Allow lazy-loaded content to settle
            page.wait_for_timeout(2_000)

            products = extract_products_from_dom(page)
            print(f"  -> DOM scraped: {len(products)} products")

            if not products:
                snippet = page.inner_text("body")[:500] if page.locator("body").count() else "(empty)"
                print(f"  -> Page snippet: {snippet}")
                continue

            skipped = {"seen": 0, "keyword": 0, "excluded": 0, "group": 0, "gpu": 0, "price": 0}
            for product in products:
                if product.sku in seen:
                    skipped["seen"] += 1
                    continue
                if not title_matches_keywords(product.title, args.keywords):
                    skipped["keyword"] += 1
                    continue
                if title_excluded(product.title, args.exclude_keywords):
                    skipped["excluded"] += 1
                    continue
                if not title_matches_all_groups(
                    product.title,
                    {
                        "brands": args.brands,
                        "cpu_keywords": args.cpu_keywords,
                        "ram_keywords": args.ram_keywords,
                        "storage_keywords": args.storage_keywords,
                    },
                ):
                    skipped["group"] += 1
                    continue
                # GPU filter is optional; only applied when terms are explicitly set
                if args.gpu_keywords and not title_matches_keywords(product.title, args.gpu_keywords):
                    skipped["gpu"] += 1
                    continue
                if not in_price_range(product.price, args.min_price, args.max_price):
                    skipped["price"] += 1
                    continue
                all_candidates.append(product)
            print(f"  -> Skipped: {skipped}  |  Matched so far: {len(all_candidates)}")

        page.close()
        if own_context:
            context.close()
        browser.close()

    if not all_candidates:
        print("No new matching laptop/computer items found.")
        return 0

    # Deduplicate across pages in one run
    unique_by_sku: dict[str, Product] = {item.sku: item for item in all_candidates}
    matches = list(unique_by_sku.values())

    use_ai = bool(args.ai_base_url and args.ai_model)
    sent = 0
    for item in matches:
        ai_result: AIResult | None = None
        if use_ai:
            print(f"  [AI] Evaluating: {item.title[:80]}...")
            ai_result = evaluate_with_ai(item.title, item.price, args.query, args)
            if ai_result:
                print(f"  [AI] Score {ai_result.score}/5 - {ai_result.comment}")
            if args.min_rating > 0 and (ai_result is None or ai_result.score < args.min_rating):
                score_str = str(ai_result.score) if ai_result else "N/A"
                print(f"  [AI] Skipped (score {score_str} < min {args.min_rating}): {item.title[:60]}")
                continue

        msg = format_product_message(item, args.query, ai_result)
        print(msg)
        print()
        if args.telegram_bot_token and args.telegram_chat_id:
            try:
                send_telegram(args.telegram_bot_token, args.telegram_chat_id, msg)
                sent += 1
                time.sleep(0.5)  # avoid Telegram rate limit
            except Exception as exc:
                print(f"  [WARN] Failed to send Telegram for {item.sku}: {exc}", file=sys.stderr)

    if args.telegram_bot_token and args.telegram_chat_id:
        print(f"Telegram: sent {sent}/{len(matches)} messages.")
    else:
        print("Telegram not configured, only printed to stdout.")

    seen.update(item.sku for item in matches)
    save_seen(state_file, seen)
    return 0


def main() -> int:
    args = parse_args()

    while True:
        try:
            code = run_once(args)
        except KeyboardInterrupt:
            print("Stopped by user.")
            return 130
        except Exception as exc:  # broad catch for resilient long-running monitoring
            print(f"Run failed: {exc}", file=sys.stderr)
            code = 1

        if args.interval <= 0:
            return code

        print(f"Sleeping {args.interval} seconds before next run...")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
