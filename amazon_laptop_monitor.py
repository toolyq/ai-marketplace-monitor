#!/usr/bin/env python3
"""Standalone Amazon laptop/computer monitor with Telegram notifications.

Usage example:
    python amazon_laptop_monitor.py \
        --query "laptop computer" \
        --min-price 300 \
        --max-price 1200 \
        --domain amazon.com \
        --max-pages 2 \
        --interval 600 \
        --telegram-bot-token "<token>" \
        --telegram-chat-id "<chat_id>"

Notes:
- This script is independent from the main monitor pipeline.
- It uses Playwright to fetch Amazon search result pages.
- Respect Amazon terms of use and local laws before running at scale.
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

from playwright.sync_api import Page, sync_playwright


DEFAULT_KEYWORDS = ["laptop", "notebook", "computer", "pc"]
DEFAULT_FILTER_FILE = "amazon_laptop_filters.json"


@dataclass(frozen=True)
class Product:
    asin: str
    title: str
    price: float | None
    url: str
    rating: str | None = None
    reviews: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Amazon computers/laptops and send notifications."
    )
    parser.add_argument("--query", default="laptop computer", help="Amazon search keyword.")
    parser.add_argument("--domain", default="amazon.ca", help="Amazon domain, e.g. amazon.ca")
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="Only notify items whose title contains one of these keywords.",
    )
    parser.add_argument(
        "--exclude-keywords",
        nargs="*",
        default=["bag", "sleeve", "stand", "sticker", "case", "charger only"],
        help="Exclude items whose title contains these keywords.",
    )
    parser.add_argument(
        "--brands",
        nargs="*",
        default=["lenovo", "dell", "hp", "asus", "acer", "msi", "apple", "huawei"],
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
    parser.add_argument("--max-pages", type=int, default=1, help="How many pages to crawl.")
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
        default=".amazon_seen_items.json",
        help="File used to avoid duplicate notifications.",
    )
    parser.add_argument("--telegram-bot-token", default="", help="Telegram bot token.")
    parser.add_argument("--telegram-chat-id", default="", help="Telegram chat ID.")
    return parser.parse_args()


def build_search_url(domain: str, query: str, page: int) -> str:
    params = {"k": query, "page": page}
    return f"https://{domain}/s?{urlencode(params)}"


def parse_price(text: str) -> float | None:
    cleaned = re.sub(r"[^0-9.,]", "", text)
    if not cleaned:
        return None

    # Convert common formats: 1,299.99 and 1.299,99 to a float parseable string.
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


def extract_products(page: Page, domain: str) -> list[Product]:
    products: list[Product] = []

    # Primary selector used by most Amazon layouts
    cards = page.locator("[data-component-type='s-search-result']")
    count = cards.count()

    # Fallback: some Amazon layouts wrap results in div.s-result-item with data-asin
    if count == 0:
        cards = page.locator("div.s-result-item[data-asin]")
        count = cards.count()
    if count == 0:
        # Last resort: any element with a non-empty data-asin
        cards = page.locator("[data-asin]:not([data-asin=''])")
        count = cards.count()

    print(f"  -> Card elements found: {count}")

    for idx in range(count):
        card = cards.nth(idx)
        asin = (card.get_attribute("data-asin") or "").strip()
        title = card.locator("h2 span").first.inner_text().strip() if card.locator("h2 span").count() else ""
        link = card.locator("h2 a").first.get_attribute("href") if card.locator("h2 a").count() else None
        if not asin or not title or not link:
            continue

        price_text = (
            card.locator(".a-price .a-offscreen").first.inner_text().strip()
            if card.locator(".a-price .a-offscreen").count()
            else ""
        )
        rating_text = (
            card.locator("span.a-icon-alt").first.inner_text().strip()
            if card.locator("span.a-icon-alt").count()
            else None
        )
        review_text = (
            card.locator("span.a-size-base.s-underline-text").first.inner_text().strip()
            if card.locator("span.a-size-base.s-underline-text").count()
            else None
        )
        price = parse_price(price_text)

        full_url = f"https://{domain}{link}" if link.startswith("/") else link
        products.append(
            Product(
                asin=asin,
                title=title,
                price=price,
                url=full_url,
                rating=rating_text,
                reviews=review_text,
            )
        )

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


def format_message(items: list[Product], query: str, domain: str) -> str:
    lines = [f"Amazon new matches for query: {query} ({domain})", ""]
    for idx, item in enumerate(items, start=1):
        price_display = f"${item.price:.2f}" if item.price is not None else "N/A"
        lines.append(f"{idx}. {item.title}")
        lines.append(f"Price: {price_display}")
        if item.rating:
            lines.append(f"Rating: {item.rating}")
        if item.reviews:
            lines.append(f"Reviews: {item.reviews}")
        lines.append(f"URL: {item.url}")
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(bot_token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode(
        "utf-8"
    )
    req = Request(url, data=payload, method="POST")
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
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            browser = p.chromium.launch(headless=not args.headful)
            context = browser.new_context()
        page = context.new_page()

        for page_num in range(1, args.max_pages + 1):
            search_url = build_search_url(args.domain, args.query, page_num)
            print(f"[Page {page_num}] {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)

            # Wait for actual search result cards to appear in the DOM
            result_selector = "[data-component-type='s-search-result']"
            try:
                page.wait_for_selector(result_selector, timeout=15_000)
                print("  -> Search results loaded")
            except Exception:
                # Fallback: maybe the layout uses a different container
                print("  -> Timed out waiting for search result cards, trying fallback...")
                page.wait_for_timeout(5000)

            products = extract_products(page, args.domain)
            print(f"  -> Scraped {len(products)} raw products")
            if not products:
                # Dump a snippet of page text for diagnostics
                snippet = page.inner_text("body")[:500] if page.locator("body").count() else "(empty)"
                print(f"  -> Page snippet: {snippet}")
                continue

            skipped = {"seen": 0, "keyword": 0, "excluded": 0, "group": 0, "gpu": 0, "price": 0}
            for product in products:
                if product.asin in seen:
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
                # GPU is optional in many office laptops; apply only if explicit GPU terms are set.
                if args.gpu_keywords and not title_matches_keywords(product.title, args.gpu_keywords):
                    skipped["gpu"] += 1
                    continue
                if not in_price_range(product.price, args.min_price, args.max_price):
                    skipped["price"] += 1
                    continue
                all_candidates.append(product)
            print(f"  -> Skipped: {skipped}  |  Matched so far: {len(all_candidates)}")

        context.close()
        browser.close()

    if not all_candidates:
        print("No new matching laptop/computer items found.")
        return 0

    # Avoid duplicates across pages in one run.
    unique_by_asin: dict[str, Product] = {item.asin: item for item in all_candidates}
    matches = list(unique_by_asin.values())

    message = format_message(matches, args.query, args.domain)
    print(message)

    if args.telegram_bot_token and args.telegram_chat_id:
        send_telegram(args.telegram_bot_token, args.telegram_chat_id, message)
        print(f"Telegram message sent: {len(matches)} items.")
    else:
        print("Telegram not configured, only printed to stdout.")

    seen.update(item.asin for item in matches)
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
