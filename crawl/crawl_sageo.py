"""
Rebuild the SAGEO Arena corpus from its released Google search results.

Reuses SAGEO's own extraction and filtering functions (src/crawling/crawler.py)
so the output matches their `documents_filtered.jsonl` / `crawl_errors.jsonl`
format, but changes how pages are fetched:
    - each unique URL is fetched once (SAGEO re-fetches per (query_id, url))
    - httpx requests run concurrently (SAGEO's sync httpx call serializes them)
    - direct requests first (as SAGEO did); pages that come back blocked
      (403/429, anti-bot challenge, bot text) get one retry through a rotating
      proxy (Decodo, US exit IPs); transient errors are retried up to 2 times
    - extracted page data is saved at fetch time; HTML is kept gzip-compressed
      with <script>/<style> removed (JSON-LD kept) for later re-extraction
      (e.g. markdown), only for pages that passed the filters
    - Playwright retry (with playwright-stealth, as in SAGEO) blocks images,
      media and fonts to save proxy bandwidth

Phases:
    fetch    httpx pass over all unique URLs (+ extraction/filters)
    browser  Playwright retry for URLs that failed the httpx pass
    build    write SAGEO-format documents_filtered.jsonl / crawl_errors.jsonl

Usage:
    python crawl/crawl_sageo.py --phase fetch   [--limit 500]
    python crawl/crawl_sageo.py --phase browser [--limit 500]
    python crawl/crawl_sageo.py --phase build
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import socket
import threading

import dns.resolver
import httpx

ROOT = Path(__file__).resolve().parent.parent
SAGEO_ROOT = ROOT / "third_party" / "SAGEO_Arena"
sys.path.insert(0, str(SAGEO_ROOT))

from src.crawling.crawler import (  # noqa: E402  (SAGEO's own logic)
    build_document,
    extract_page_data,
    is_blocked,
    is_bot_body,
    is_short,
)

DATA_DIR = ROOT / "data" / "sageo"
SEARCH_RESULTS = DATA_DIR / "google_search_results" / "google_search_responses.jsonl"
CRAWL_DIR = DATA_DIR / "crawl"
RAW_DIR = CRAWL_DIR / "raw_html"
CORPUS_DIR = DATA_DIR / "corpus"
FETCH_LOG = CRAWL_DIR / "fetch_httpx.jsonl"
BROWSER_LOG = CRAWL_DIR / "fetch_browser.jsonl"
PAGES = {"httpx": CRAWL_DIR / "pages_httpx.jsonl", "browser": CRAWL_DIR / "pages_browser.jsonl"}

# Same request settings as SAGEO's fetch_html / crawl_with_browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
TIMEOUT = 60
MAX_BYTES = 5_000_000          # stop downloading huge files (PDFs, videos)
BLOCKED_RESOURCES = {"image", "media", "font"}
BROWSER_URL_TIMEOUT = 90       # hard cap per URL in the browser phase


# ---------------------------------------------------------------------------
# DNS: WSL's default resolver (the Windows host) stalls for ~20 s under many
# concurrent lookups, so resolve IPv4 through public resolvers and cache per host.
# ---------------------------------------------------------------------------
_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
_resolver.timeout, _resolver.lifetime = 3, 8
_dns_cache: dict[str, list[str] | None] = {}
_dns_lock = threading.Lock()
_system_getaddrinfo = socket.getaddrinfo


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if isinstance(host, bytes):            # anyio (httpx async) passes IDNA-encoded bytes
        host = host.decode("ascii", errors="ignore")
    if isinstance(port, bytes):
        port = port.decode()
    if not isinstance(host, str) or host in ("localhost",) or host.replace(".", "").isdigit() or ":" in host:
        return _system_getaddrinfo(host, port, family, type, proto, flags)
    with _dns_lock:
        cached = _dns_cache.get(host, False)
    if cached is False:
        try:
            cached = [r.address for r in _resolver.resolve(host, "A")]
        except Exception:
            cached = None
        with _dns_lock:
            _dns_cache[host] = cached
    if not cached:
        raise socket.gaierror(socket.EAI_NONAME, f"cannot resolve {host}")
    port = int(port) if port is not None else 0
    return [(socket.AF_INET, type or socket.SOCK_STREAM, proto or socket.IPPROTO_TCP, "", (ip, port))
            for ip in cached]


socket.getaddrinfo = _cached_getaddrinfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def raw_path(url: str, method: str) -> Path:
    k = url_key(url)
    return RAW_DIR / method / k[:2] / f"{k}.html.gz"


def save_raw(url: str, method: str, html: str) -> None:
    p = raw_path(url, method)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(html)


_SCRIPT_RE = re.compile(r"<script\b(?![^>]*application/ld\+json)[^>]*>[\s\S]*?</script>", re.I)
_DROP_RE = re.compile(r"<style\b[^>]*>[\s\S]*?</style>|<noscript\b[^>]*>[\s\S]*?</noscript>|<!--[\s\S]*?-->", re.I)


def strip_html(html: str) -> str:
    """Drop scripts (except JSON-LD), styles, noscript and comments; keep markup."""
    return _DROP_RE.sub("", _SCRIPT_RE.sub("", html))


def load_raw(url: str, method: str) -> str | None:
    p = raw_path(url, method)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return f.read()


def load_records() -> list[dict]:
    with SEARCH_RESULTS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def unique_urls(records: list[dict]) -> list[tuple[str, str]]:
    """(url, fallback_title) per unique URL, in file order."""
    seen, out = set(), []
    for r in records:
        url = r.get("link")
        if url and url not in seen:
            seen.add(url)
            out.append((url, r.get("title", "")))
    return out


def done_urls(log: Path) -> set[str]:
    if not log.exists():
        return set()
    with log.open(encoding="utf-8") as f:
        return {json.loads(line)["url"] for line in f if line.strip()}


def evaluate(html: str | None, status: int, url: str, fallback_title: str) -> tuple[dict | None, str | None]:
    """SAGEO's checks in their order: anti-bot -> extraction -> bot body -> length."""
    if not html:
        return None, "empty_response"
    blocked, reason = is_blocked(status, html)
    if blocked:
        return None, reason
    page_data, error = extract_page_data(html, url, fallback_title)
    if not page_data:
        return None, error
    if is_bot_body(page_data.get("body_text", ""), page_data.get("title", "")):
        return None, "bot_pattern"
    body = page_data.get("body_text", "")
    if is_short(body):
        return None, f"too_short ({len(body.split())} words)"
    return page_data, None


def _process_worker(args):
    """Evaluate the full HTML with SAGEO's filters; keep stripped HTML for passing pages."""
    url, method, status, fallback_title, html = args
    page_data, reason = evaluate(html, status, url, fallback_title)
    if page_data:
        save_raw(url, method, strip_html(html))
    return url, page_data, reason


# ---------------------------------------------------------------------------
# Phase 1: concurrent httpx fetch
# ---------------------------------------------------------------------------
TRANSIENT_ERRORS = ("httpx_timeout", "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
                    "RemoteProtocolError", "PoolTimeout", "WriteError")
RETRY_BACKOFF = (2, 5)
HOST_INTERVAL = 1.0                    # overwritten by --host_interval
_host_last: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _host_wait(host: str) -> None:
    """Keep at least HOST_INTERVAL seconds between request starts to the same host."""
    async with _host_locks[host]:
        wait = _host_last.get(host, 0) + HOST_INTERVAL - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _host_last[host] = time.time()                 # seconds before the 2nd and 3rd attempt
NO_PROXY_RETRY = ("no_body_text", "garbled_text", "too_short", "too_large")  # same result on any IP


def _is_transient(rec: dict) -> bool:
    if rec["error"]:
        return any(t in rec["error"] for t in TRANSIENT_ERRORS)
    return rec["status"] in (500, 502, 504)


async def _get(client, url, route, retries):
    """Fetch with retries on transient failures (timeouts, connection errors, 5xx)."""
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(retries + 1):
        await _host_wait(host)
        rec = await _fetch_with(client, url, route)
        rec["attempts"] = attempt + 1
        if not _is_transient(rec) or attempt == retries:
            return rec
        await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])


async def _evaluate_rec(rec, title, pool, loop):
    if rec["error"]:
        return None, rec["error"]
    _, page_data, reason = await loop.run_in_executor(
        pool, _process_worker, (rec["url"], "httpx", rec["status"], title, rec.pop("html")))
    return page_data, reason


async def process_url(url, title, direct, proxied, host_sems, global_sem, pool, loop):
    """Direct fetch first (as SAGEO did); on blocks, one more try through the rotating proxy."""
    host = urllib.parse.urlparse(url).netloc
    # Host slot first, then a global slot: URLs queued behind a busy host must not hold global slots
    async with host_sems[host], global_sem:
        rec = await _get(direct, url, "direct", retries=2)
        page_data, reason = await _evaluate_rec(rec, title, pool, loop)
        if page_data is None and proxied is not None and not reason.startswith(NO_PROXY_RETRY):
            rec2 = await _get(proxied, url, "proxy", retries=1)
            page_data2, reason2 = await _evaluate_rec(rec2, title, pool, loop)
            if page_data2 is not None:
                rec, page_data, reason = rec2, page_data2, reason2
            else:
                rec["proxy_reason"] = reason2
        rec.pop("html", None)
        rec["ok"] = page_data is not None
        rec["reason"] = reason
        return rec, page_data


async def _fetch_with(client, url, route):
    t0 = time.time()
    try:
        async with client.stream("GET", url) as resp:
            ctype = resp.headers.get("content-type", "")
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    return {"url": url, "route": route, "status": resp.status_code, "error": "too_large",
                            "ctype": ctype, "bytes": size, "sec": round(time.time() - t0, 2)}
            body = b"".join(chunks)
            encoding = resp.charset_encoding or "utf-8"
            try:
                html = body.decode(encoding, errors="replace")
            except LookupError:
                html = body.decode("utf-8", errors="replace")
            return {"url": url, "route": route, "status": resp.status_code, "error": None,
                    "ctype": ctype, "bytes": size, "final_url": str(resp.url),
                    "sec": round(time.time() - t0, 2), "html": html}
    except httpx.TimeoutException:
        return {"url": url, "route": route, "status": 0, "error": "httpx_timeout",
                "sec": round(time.time() - t0, 2)}
    except Exception as e:  # network errors, bad TLS, invalid URLs
        return {"url": url, "route": route, "status": 0, "error": f"httpx_error ({type(e).__name__})",
                "sec": round(time.time() - t0, 2)}


async def phase_fetch(limit: int | None, concurrency: int, per_host: int, proxy: str | None, workers: int):
    records = load_records()
    urls = unique_urls(records)
    done = done_urls(FETCH_LOG)
    todo = [(u, t) for u, t in urls if u not in done]
    if limit:
        todo = todo[:limit]
    print(f"unique urls {len(urls)} | already fetched {len(done)} | to fetch {len(todo)} "
          f"| proxy fallback {'on' if proxy else 'off'}", flush=True)

    host_sems = defaultdict(lambda: asyncio.Semaphore(per_host))
    global_sem = asyncio.Semaphore(concurrency)
    CRAWL_DIR.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    pool = ProcessPoolExecutor(max_workers=workers)
    stats = Counter()
    t_start = time.time()
    kw = dict(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    # Direct: reuse connections per host. Proxy: no keep-alive, so every retry gets a new exit IP.
    direct_limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    proxy_limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=0)
    async with httpx.AsyncClient(limits=direct_limits, **kw) as direct, \
               httpx.AsyncClient(proxy=proxy, limits=proxy_limits, **kw) as proxied:
        with FETCH_LOG.open("a", encoding="utf-8") as log, PAGES["httpx"].open("a", encoding="utf-8") as pages:
            pending = [process_url(u, t, direct, proxied if proxy else None, host_sems, global_sem, pool, loop)
                       for u, t in todo]
            for i, coro in enumerate(asyncio.as_completed(pending), 1):
                rec, page_data = await coro
                if page_data:
                    pages.write(json.dumps({"url": rec["url"], "page_data": page_data}, ensure_ascii=False) + "\n")
                stats["ok" if rec["ok"] else "fail"] += 1
                stats[rec["route"]] += 1
                log.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if i % 500 == 0 or i == len(todo):
                    # One shared client would otherwise keep cookies from every site it has seen,
                    # and httpx scans the whole jar on each request (SAGEO used a fresh client per URL)
                    direct.cookies.clear()
                    proxied.cookies.clear()
                    log.flush()
                    pages.flush()
                    el = time.time() - t_start
                    print(f"[{i}/{len(todo)}] ok {stats['ok']} fail {stats['fail']} "
                          f"(via proxy {stats['proxy']}) | {i / el:.1f} url/s | elapsed {el / 60:.1f} min",
                          flush=True)
    pool.shutdown()


# ---------------------------------------------------------------------------
# Phase 2: Playwright retry (stealth, as in SAGEO)
# ---------------------------------------------------------------------------
def proxy_for_playwright(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    p = urllib.parse.urlparse(proxy)
    return {"server": f"{p.scheme}://{p.hostname}:{p.port}", "username": p.username, "password": p.password}


async def _block_heavy(route):
    if route.request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


async def _browser_get(browser, url, proxy, nbytes=None):
    """One Playwright visit in a fresh context (fresh cookies, fresh proxy exit IP).
    nbytes: optional one-item list accumulating transferred bytes (to watch proxy traffic)."""
    nbytes = nbytes if nbytes is not None else [0]
    from playwright_stealth import Stealth
    context = await browser.new_context(
        user_agent=HEADERS["User-Agent"], viewport={"width": 1920, "height": 1080},
        proxy=proxy_for_playwright(proxy))
    try:
        await Stealth().apply_stealth_async(context)
        await context.route("**/*", _block_heavy)
        page = await context.new_page()
        tasks = []

        async def _count(req):
            try:
                sz = await req.sizes()
                nbytes[0] += sz.get("responseBodySize", 0) + sz.get("responseHeadersSize", 0)
            except Exception:
                pass

        page.on("requestfinished", lambda req: tasks.append(asyncio.ensure_future(_count(req))))

        async def visit():
            resp = await page.goto(url, timeout=TIMEOUT * 1000, wait_until="load")
            await page.wait_for_timeout(3000)
            return await page.content(), (resp.status if resp else 0)

        html, status = await asyncio.wait_for(visit(), BROWSER_URL_TIMEOUT)
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        return html, status, None
    except asyncio.TimeoutError:
        return None, 0, "hard_timeout"
    except Exception as e:
        return None, 0, str(e).split("\n")[0]
    finally:
        try:
            await asyncio.wait_for(context.close(), 15)
        except Exception:
            pass


async def browser_worker(idx, queue, pw, proxy, log, pages, skipped, lock, stats, total, t_start, pool, loop):
    browser = await pw.chromium.launch(headless=True)
    try:
        while True:
            if STOP.get("reason"):
                return
            try:
                url, title = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            t0 = time.time()
            rec = {"url": url}
            routes = [("proxy", proxy), ("direct", None)] if proxy else [("direct", None)]
            host = urllib.parse.urlparse(url).netloc
            ok_n, tried_n = _browser_host_stats[host]
            if tried_n >= BROWSER_PROBE and ok_n == 0:
                # Host blocks the browser too; leave its URLs for a later proxy/other attempt
                async with lock:
                    skipped.write(json.dumps({"url": url, "host": host}) + "\n")
                    stats["skipped"] += 1
                continue
            async with _host_slot(host):                       # limited concurrent visits per host
                wait = _browser_host_last.get(host, 0) + BROWSER_HOST_INTERVAL - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                nb = [0]
                for route, px in routes:
                    html, status, err = await _browser_get(browser, url, px, nb)
                    if err and "ERR_TUNNEL_CONNECTION_FAILED" in err and route == "proxy":
                        continue                                  # proxy refused this host
                    break
                _browser_host_last[host] = time.time()
            rec["route"] = route
            if err:
                rec.update(status=0, ok=False, reason=f"playwright_error ({err[:80]})")
            else:
                _, page_data, reason = await loop.run_in_executor(
                    pool, _process_worker, (url, "browser", status, title, html))
                rec.update(status=status, ok=page_data is not None, reason=reason)
                if page_data:
                    async with lock:
                        pages.write(json.dumps({"url": url, "page_data": page_data}, ensure_ascii=False) + "\n")
            rec["sec"] = round(time.time() - t0, 2)
            rec["bytes"] = nb[0]
            stats["bytes"] += nb[0]
            if err and any(m in err for m in PROXY_DEAD_MARKERS):
                stats["proxy_err_streak"] += 1
            else:
                stats["proxy_err_streak"] = 0
            if BUDGET_BYTES and stats["bytes"] > BUDGET_BYTES:
                STOP["reason"] = f"traffic budget reached ({stats['bytes'] / 1e9:.2f} GB)"
            elif stats["proxy_err_streak"] >= 15:
                STOP["reason"] = f"proxy failing {stats['proxy_err_streak']}x in a row (quota used up or auth error?): {err[:80]}"
            _browser_host_stats[host][0] += int(rec["ok"])
            _browser_host_stats[host][1] += 1
            async with lock:
                stats["ok" if rec["ok"] else "fail"] += 1
                log.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n = stats["ok"] + stats["fail"]
                if n % 50 == 0 or n == total:
                    log.flush()
                    pages.flush()
                    el = time.time() - t_start
                    print(f"[{n}/{total}] ok {stats['ok']} fail {stats['fail']} skipped {stats['skipped']} "
                          f"| traffic {stats['bytes'] / 1e9:.2f} GB | {n / el:.2f} url/s "
                          f"| elapsed {el / 60:.1f} min", flush=True)
    finally:
        await browser.close()


BLOCK_MARKERS = ("Cloudflare", "PerimeterX", "DataDome", "Imperva", "Akamai", "Kasada", "Sucuri",
                 "Access Denied", "HTTP 403", "HTTP 429", "HTTP 503", "bot_pattern", "Network security",
                 "Blocked", "blocked", "Request unsuccessful")
EMPTY_MARKERS = ("no_body_text", "too_short", "Structural", "Near-empty")
BROWSER_HOST_INTERVAL = 3.0     # seconds between browser visits to the same host
BROWSER_PROBE = 8               # give up on a host after this many browser tries with no success
BROWSER_SKIPPED = CRAWL_DIR / "browser_skipped.jsonl"
BUDGET_BYTES = 0                # browser phase: stop after this much traffic (0 = no limit)
STOP: dict[str, str] = {}
PROXY_DEAD_MARKERS = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY", "407", "ERR_NO_SUPPORTED_PROXIES")
_browser_host_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # host -> [ok, tried]
BROWSER_PER_HOST = 1            # concurrent browser visits per host (overridable per host)
BROWSER_PER_HOST_OVERRIDE: dict[str, int] = {}
_browser_host_locks: dict[str, asyncio.Semaphore] = {}


def _host_slot(host: str) -> asyncio.Semaphore:
    if host not in _browser_host_locks:
        _browser_host_locks[host] = asyncio.Semaphore(BROWSER_PER_HOST_OVERRIDE.get(host, BROWSER_PER_HOST))
    return _browser_host_locks[host]
_browser_host_last: dict[str, float] = {}


def _browser_target(reason: str | None) -> bool:
    """Failures a real browser may fix: anti-bot blocks and empty/JS-rendered bodies."""
    reason = reason or ""
    return any(m in reason for m in BLOCK_MARKERS + EMPTY_MARKERS)


def _round_robin_by_host(urls: list[str]) -> list[str]:
    by_host = defaultdict(list)
    for u in urls:
        by_host[urllib.parse.urlparse(u).netloc].append(u)
    queues = sorted(by_host.values(), key=len, reverse=True)
    out = []
    while queues:
        out.extend(q.pop(0) for q in queues)
        queues = [q for q in queues if q]
    return out


def _non_html(ctype: str) -> bool:
    ctype = ctype.lower()
    return bool(ctype) and not any(t in ctype for t in ("html", "xml", "text/plain"))


async def phase_browser(limit: int | None, num_browsers: int, proxy: str | None, workers: int,
                        top_hosts: int = 0, hosts: list[str] | None = None, per_host_limit: int = 0):
    from playwright.async_api import async_playwright
    titles = dict(unique_urls(load_records()))
    status = {}
    with FETCH_LOG.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["url"] not in status or r["ok"]:
                status[r["url"]] = r
    # Only blocks and empty bodies; non-HTML responses (PDFs, downloads) fail in a browser too
    failed = [u for u, r in status.items() if not r["ok"] and _browser_target(r.get("reason"))
              and not _non_html(r.get("ctype", ""))]
    if hosts:
        failed = [u for u in failed if urllib.parse.urlparse(u).netloc in set(hosts)]
    if top_hosts:
        counts = Counter(urllib.parse.urlparse(u).netloc for u in failed)
        keep = {h for h, _ in counts.most_common(top_hosts)}
        failed = [u for u in failed if urllib.parse.urlparse(u).netloc in keep]
    done = done_urls(BROWSER_LOG)
    if BROWSER_LOG.exists():                      # resume per-host success counts
        with BROWSER_LOG.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                h = urllib.parse.urlparse(r["url"]).netloc
                _browser_host_stats[h][0] += int(r["ok"])
                _browser_host_stats[h][1] += 1
    todo = [u for u in failed if u not in done]
    if per_host_limit:
        seen_host = Counter()
        keep = []
        for u in todo:
            h = urllib.parse.urlparse(u).netloc
            if seen_host[h] < per_host_limit:
                keep.append(u)
                seen_host[h] += 1
        todo = keep
    todo = _round_robin_by_host(todo)
    if limit:
        todo = todo[:limit]
    print(f"browser targets {len(failed)} | already retried {len(done)} | to retry {len(todo)} "
          f"| hosts {len({urllib.parse.urlparse(u).netloc for u in todo})}", flush=True)

    queue = asyncio.Queue()
    for u in todo:
        queue.put_nowait((u, titles.get(u, "")))
    lock, stats, t_start = asyncio.Lock(), Counter(), time.time()
    loop = asyncio.get_running_loop()
    pool = ProcessPoolExecutor(max_workers=workers)
    async with async_playwright() as pw:
        with BROWSER_LOG.open("a", encoding="utf-8") as log, PAGES["browser"].open("a", encoding="utf-8") as pages, \
             BROWSER_SKIPPED.open("w", encoding="utf-8") as skipped:
            await asyncio.gather(*[
                browser_worker(i, queue, pw, proxy, log, pages, skipped, lock, stats, len(todo), t_start, pool, loop)
                for i in range(num_browsers)])
    pool.shutdown()
    if STOP.get("reason"):
        print(f"STOPPED: {STOP['reason']}", flush=True)
    print(f"done | traffic {stats['bytes'] / 1e9:.2f} GB | ok {stats['ok']} fail {stats['fail']}", flush=True)


# ---------------------------------------------------------------------------
# Phase 3: build SAGEO-format corpus
# ---------------------------------------------------------------------------
def _final_status() -> dict[str, tuple[str | None, int, str | None]]:
    """url -> (method that succeeded, status, failure reason)."""
    status = {}
    logs = [(FETCH_LOG, "httpx")] + [(p, "browser") for p in sorted(CRAWL_DIR.glob("fetch_browser*.jsonl"))]
    for log, method in logs:
        if not log.exists():
            continue
        with log.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                prev = status.get(r["url"])
                if prev and prev[0]:          # keep an earlier success
                    continue
                status[r["url"]] = (method if r["ok"] else None, r.get("status", 0), r.get("reason"))
    return status


def phase_build():
    records = load_records()
    status = _final_status()
    titles = dict(unique_urls(records))

    page_data_by_url = {}
    for path in sorted(CRAWL_DIR.glob("pages_*.jsonl")):     # httpx, browser, browser_<tag>
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    page_data_by_url.setdefault(r["url"], r["page_data"])

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    n_ok = n_err = n_dup = 0
    seen = set()
    with (CORPUS_DIR / "documents_filtered.jsonl").open("w", encoding="utf-8") as out, \
         (CORPUS_DIR / "crawl_errors.jsonl").open("w", encoding="utf-8") as err:
        for r in records:
            key = (r.get("query_id"), r.get("link"))
            if key in seen:
                n_dup += 1
                continue
            seen.add(key)
            url = r.get("link")
            if url in page_data_by_url:
                # Some search results have "pagemap": null, which SAGEO's build_document cannot read
                r = dict(r, page_data=page_data_by_url[url], pagemap=r.get("pagemap") or {})
                out.write(json.dumps(build_document(r), ensure_ascii=False) + "\n")
                n_ok += 1
            else:
                reason = status.get(url, (None, 0, "not_fetched"))[2]
                err.write(json.dumps({"query_id": r.get("query_id"), "query": r.get("query"),
                                      "link": url, "reason": reason}, ensure_ascii=False) + "\n")
                n_err += 1
    print(f"documents {n_ok} | errors {n_err} | dup records {n_dup} | "
          f"unique urls ok {len(page_data_by_url)}/{len(titles)}")


def export_failures():
    """CSV of URLs that failed every attempt so far, for a later re-crawl."""
    import csv
    logs = {}
    sources = [(FETCH_LOG, "httpx")] + [(p, "browser") for p in sorted(CRAWL_DIR.glob("fetch_browser*.jsonl"))]
    for log, phase in sources:
        if log.exists():
            with log.open(encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    prev = logs.setdefault(r["url"], {}).get(phase)
                    if prev is None or (r["ok"] and not prev["ok"]):   # keep a success if any run had one
                        logs[r["url"]][phase] = r
    skipped_urls = set()
    for path in CRAWL_DIR.glob("browser_skipped*.jsonl"):
        with path.open(encoding="utf-8") as f:
            skipped_urls |= {json.loads(line)["url"] for line in f if line.strip()}
    queries = defaultdict(list)
    for r in load_records():
        queries[r.get("link")].append(str(r.get("query_id")))
    path = CRAWL_DIR / "failed_urls.csv"
    n = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "host", "query_ids", "httpx_reason", "httpx_status", "httpx_route",
                    "httpx_attempts", "proxy_reason", "browser_reason", "browser_status"])
        for url, ph in logs.items():
            h, b = ph.get("httpx", {}), ph.get("browser", {})
            if h.get("ok") or b.get("ok"):
                continue
            w.writerow([url, urllib.parse.urlparse(url).netloc, ";".join(sorted(set(queries[url]))),
                        h.get("reason", ""), h.get("status", ""), h.get("route", ""), h.get("attempts", ""),
                        h.get("proxy_reason", ""),
                        b.get("reason", "") or ("skipped_host (browser blocked on probe)" if url in skipped_urls else ""),
                        b.get("status", "")])
            n += 1
    print(f"failed urls: {n} -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["fetch", "browser", "build", "failures"], required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument("--per_host", type=int, default=2)
    ap.add_argument("--host_interval", type=float, default=1.0, help="min seconds between requests to one host")
    ap.add_argument("--num_browsers", type=int, default=4)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no_proxy", action="store_true")
    ap.add_argument("--top_hosts", type=int, default=0, help="browser phase: only the N hosts with most failures")
    ap.add_argument("--hosts", type=str, default="", help="browser phase: comma-separated hosts to retry")
    ap.add_argument("--per_host_limit", type=int, default=0, help="browser phase: at most N URLs per host (pilot)")
    ap.add_argument("--proxy_env", type=str, default="DECODO_PROXY_URL_US", help="env var holding the proxy URL")
    ap.add_argument("--tag", type=str, default="", help="separate log/page files for this run, e.g. resi")
    ap.add_argument("--budget_gb", type=float, default=0, help="browser phase: stop after this much traffic")
    ap.add_argument("--browser_interval", type=float, default=3.0, help="browser phase: seconds between visits to a host")
    ap.add_argument("--host_concurrency", type=str, default="",
                    help="browser phase: per-host overrides, e.g. pmc.ncbi.nlm.nih.gov=2")
    args = ap.parse_args()

    global HOST_INTERVAL, BROWSER_LOG, BROWSER_SKIPPED, BUDGET_BYTES, BROWSER_HOST_INTERVAL
    HOST_INTERVAL = args.host_interval
    BROWSER_HOST_INTERVAL = args.browser_interval
    for item in filter(None, args.host_concurrency.split(",")):
        h, n = item.split("=")
        BROWSER_PER_HOST_OVERRIDE[h.strip()] = int(n)
    BUDGET_BYTES = int(args.budget_gb * 1e9)
    if args.tag:                                   # separate logs so host stats/resume don't mix runs
        BROWSER_LOG = CRAWL_DIR / f"fetch_browser_{args.tag}.jsonl"
        BROWSER_SKIPPED = CRAWL_DIR / f"browser_skipped_{args.tag}.jsonl"
        PAGES[f"browser_{args.tag}"] = CRAWL_DIR / f"pages_browser_{args.tag}.jsonl"
        PAGES["browser"] = PAGES[f"browser_{args.tag}"]
    load_env(ROOT / ".env.local")
    proxy = None if args.no_proxy else os.environ.get(args.proxy_env)

    if args.phase == "fetch":
        asyncio.run(phase_fetch(args.limit, args.concurrency, args.per_host, proxy, args.workers))
    elif args.phase == "browser":
        hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
        asyncio.run(phase_browser(args.limit, args.num_browsers, proxy, args.workers, args.top_hosts,
                                  hosts, args.per_host_limit))
    elif args.phase == "build":
        phase_build()
        export_failures()
    else:
        export_failures()


if __name__ == "__main__":
    main()
