from google import genai
from google.genai import errors
import json
import os
import re
import time
from http.client import InvalidURL
from datetime import datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urldefrag, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from config import API_KEY, MODEL, FALLBACK_MODELS

if os.name == "nt":
    try:
        import asyncio

        if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except (AttributeError, RuntimeError):
        pass

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    PlaywrightError = None
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None

client = genai.Client(api_key=API_KEY)

MEMORY_FILE = "memory.json"
BUGS_FILE = "bugs.json"
TEST_CASES_FILE = "test_cases.json"
BROKEN_LINKS_FILE = "broken_links.json"
SMOKE_TESTS_FILE = "smoke_tests.json"
BULK_URL_REPORTS_FILE = "bulk_url_reports.json"
PROMPT_WEB_CHECKS_FILE = "prompt_web_checks.json"
CROSS_BROWSER_REPORTS_FILE = "cross_browser_reports.json"
CONTENT_VERIFICATION_REPORTS_FILE = "content_verification_reports.json"
MAX_PAGES_TO_SCAN = 25
REQUEST_TIMEOUT = 10
MAX_PROMPT_CHECK_PAGES = 20
BROWSER_TEST_TIMEOUT_MS = 30000

def load_json_file(file_path):
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r") as f:
        return json.load(f)

def save_json_file(file_path, entry):
    data = load_json_file(file_path)
    data.append(entry)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

def load_memory():
    return load_json_file(MEMORY_FILE)

def save_memory(entry):
    save_json_file(MEMORY_FILE, entry)

def save_bug(entry):
    save_json_file(BUGS_FILE, entry)

def save_test_cases(entry):
    save_json_file(TEST_CASES_FILE, entry)

def save_broken_link_report(entry):
    save_json_file(BROKEN_LINKS_FILE, entry)

def save_smoke_test_report(entry):
    save_json_file(SMOKE_TESTS_FILE, entry)

def save_bulk_url_report(entry):
    save_json_file(BULK_URL_REPORTS_FILE, entry)

def save_prompt_web_check_report(entry):
    save_json_file(PROMPT_WEB_CHECKS_FILE, entry)

def save_cross_browser_report(entry):
    save_json_file(CROSS_BROWSER_REPORTS_FILE, entry)

def save_content_verification_report(entry):
    save_json_file(CONTENT_VERIFICATION_REPORTS_FILE, entry)

class PageAssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.forms = []
        self.scripts = []
        self.stylesheets = []
        self.title = ""
        self.text_parts = []
        self._is_title = False
        self._ignored_tag_depth = 0

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._ignored_tag_depth += 1

        if tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])
        elif tag == "img":
            for attr_name in ("src", "data-src", "data-lazy-src"):
                if attr_map.get(attr_name):
                    self.images.append(attr_map[attr_name])
            if attr_map.get("srcset"):
                self.images.extend(parse_srcset(attr_map["srcset"]))
        elif tag == "source" and attr_map.get("srcset"):
            self.images.extend(parse_srcset(attr_map["srcset"]))
        elif tag == "form":
            self.forms.append(attr_map)
        elif tag == "script" and attr_map.get("src"):
            self.scripts.append(attr_map["src"])
        elif tag == "link" and attr_map.get("rel") and attr_map.get("href"):
            rel_values = attr_map["rel"]
            if isinstance(rel_values, str) and "stylesheet" in rel_values.lower():
                self.stylesheets.append(attr_map["href"])
        elif tag == "title":
            self._is_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._is_title = False
        if tag in ("script", "style", "noscript") and self._ignored_tag_depth:
            self._ignored_tag_depth -= 1

    def handle_data(self, data):
        clean_data = " ".join(data.split())
        if not clean_data:
            return
        if self._is_title:
            self.title += clean_data
        elif not self._ignored_tag_depth:
            self.text_parts.append(clean_data)

    def page_text(self):
        return " ".join(self.text_parts)

def parse_srcset(srcset):
    urls = []
    for item in srcset.split(","):
        parts = item.strip().split()
        if parts:
            urls.append(parts[0])
    return urls

def generate_with_fallback(prompt):
    model_names = [MODEL, *FALLBACK_MODELS]
    last_error = None

    for model_name in model_names:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text
        except errors.ServerError as exc:
            last_error = exc
            if exc.code != 503:
                raise
        except errors.ClientError as exc:
            last_error = exc
            if exc.code != 429:
                raise

    raise last_error

def generate_standup(yesterday, today, blockers):
    history = load_memory()

    context = ""
    if history:
        last = history[-1]
        context = f"Previous Stand-up:\n{last['update']}"

    prompt = f"""
Act as a Senior QA Lead.

{context}

Generate a professional stand-up update.

Yesterday: {yesterday}
Today: {today}
Blockers: {blockers}

Keep it short and impactful.
"""

    return generate_with_fallback(prompt)

def generate_bug_report(summary):
    prompt = f"""
Act as a Senior QA Engineer.

Create a clear bug report from the bug summary below.

Bug Summary: {summary}

Use this exact format:
Title:
Severity:
Priority:
Environment:
Steps to Reproduce:
Expected Result:
Actual Result:
Impact:
Suggested Owner:

If any detail is missing, write "Needs confirmation" for that field.
Keep it professional and ready to paste into a bug tracker.
"""

    return generate_with_fallback(prompt)

def generate_test_cases(requirement):
    prompt = f"""
Act as a Senior QA Engineer.

Generate detailed test cases from the requirement or feature summary below.

Requirement / Feature Summary: {requirement}

Use this exact format for each test case:
Test Case ID:
Title:
Type:
Priority:
Preconditions:
Test Data:
Steps:
Expected Result:

Include positive, negative, boundary, and edge case scenarios where relevant.
If any detail is missing, write "Needs confirmation" for that field.
Keep the output professional and ready to paste into a test management tool.
"""

    return generate_with_fallback(prompt)

def normalize_url(base_url, raw_url):
    absolute_url = urljoin(base_url, raw_url.strip())
    clean_url, _fragment = urldefrag(absolute_url)
    parsed_url = urlparse(clean_url)
    safe_path = quote(parsed_url.path, safe="/%")
    safe_query = quote(parsed_url.query, safe="=&?/:+,%")
    return urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        safe_path,
        parsed_url.params,
        safe_query,
        ""
    ))

def normalize_entered_url(raw_url):
    url = raw_url.strip()
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = "https://" + url
    return normalize_url(url, url)

def is_http_url(url):
    return urlparse(url).scheme in ("http", "https")

def is_same_domain(url, base_domain):
    return urlparse(url).netloc == base_domain

def check_url(url):
    headers = {"User-Agent": "QA-Link-Checker/1.0"}

    for method in ("HEAD", "GET"):
        try:
            request = Request(url, method=method, headers=headers)
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                status = response.getcode()
                return {
                    "ok": status < 400,
                    "status": status,
                    "error": ""
                }
        except HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405):
                continue
            return {
                "ok": exc.code < 400,
                "status": exc.code,
                "error": str(exc.reason)
            }
        except URLError as exc:
            return {
                "ok": False,
                "status": None,
                "error": str(exc.reason)
            }
        except TimeoutError:
            return {
                "ok": False,
                "status": None,
                "error": "Request timed out"
            }
        except (InvalidURL, OSError, ValueError) as exc:
            return {
                "ok": False,
                "status": None,
                "error": str(exc)
            }

    return {
        "ok": False,
        "status": None,
        "error": "Unable to check URL"
    }

def fetch_page_response(url):
    headers = {"User-Agent": "QA-Smoke-Tester/1.0"}
    request = Request(normalize_url(url, url), headers=headers)
    start_time = time.perf_counter()

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status = response.getcode()
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
        return {
            "status": status,
            "final_url": final_url,
            "content_type": content_type,
            "response_time_ms": elapsed_ms,
            "body": body
        }

def fetch_page_html(url):
    headers = {"User-Agent": "QA-Link-Checker/1.0"}
    request = Request(normalize_url(url, url), headers=headers)

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return ""
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")

def scan_web_application(start_url):
    parsed_start_url = urlparse(start_url)
    if not parsed_start_url.scheme:
        start_url = "https://" + start_url
        parsed_start_url = urlparse(start_url)

    if not parsed_start_url.netloc:
        raise ValueError("Please enter a valid environment URL.")

    start_url = normalize_url(start_url, start_url)
    base_domain = parsed_start_url.netloc
    queue = [start_url]
    visited_pages = set()
    checked_links = set()
    checked_images = set()
    broken_links = []
    broken_images = []

    while queue and len(visited_pages) < MAX_PAGES_TO_SCAN:
        current_url = queue.pop(0)
        if current_url in visited_pages:
            continue

        visited_pages.add(current_url)
        print(f"Scanning page: {current_url}")

        try:
            html = fetch_page_html(current_url)
        except (HTTPError, URLError, TimeoutError, InvalidURL, OSError, ValueError) as exc:
            broken_links.append({
                "page": current_url,
                "url": current_url,
                "status": getattr(exc, "code", None),
                "error": str(exc)
            })
            continue

        if not html:
            continue

        parser = PageAssetParser()
        parser.feed(html)

        for raw_link in parser.links:
            link_url = normalize_url(current_url, raw_link)
            if not is_http_url(link_url):
                continue

            link_is_ok = True
            if link_url not in checked_links:
                checked_links.add(link_url)
                result = check_url(link_url)
                link_is_ok = result["ok"]
                if not result["ok"]:
                    broken_links.append({
                        "page": current_url,
                        "url": link_url,
                        "status": result["status"],
                        "error": result["error"]
                    })

            if (
                link_is_ok
                and
                is_same_domain(link_url, base_domain)
                and link_url not in visited_pages
                and link_url not in queue
                and len(visited_pages) + len(queue) < MAX_PAGES_TO_SCAN
            ):
                queue.append(link_url)

        for raw_image in parser.images:
            image_url = normalize_url(current_url, raw_image)
            if not is_http_url(image_url) or image_url in checked_images:
                continue

            checked_images.add(image_url)
            result = check_url(image_url)
            if not result["ok"]:
                broken_images.append({
                    "page": current_url,
                    "url": image_url,
                    "status": result["status"],
                    "error": result["error"]
                })

    return {
        "start_url": start_url,
        "scanned_pages": sorted(visited_pages),
        "broken_links": broken_links,
        "broken_images": broken_images
    }

def create_check(name, passed, details):
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "details": details
    }

def run_smoke_test(environment_url):
    parsed_url = urlparse(environment_url)
    if not parsed_url.scheme:
        environment_url = "https://" + environment_url
        parsed_url = urlparse(environment_url)

    if not parsed_url.netloc:
        raise ValueError("Please enter a valid web application URL.")

    environment_url = normalize_url(environment_url, environment_url)
    checks = []
    warnings = []
    parser = PageAssetParser()
    response = None

    try:
        response = fetch_page_response(environment_url)
    except HTTPError as exc:
        checks.append(create_check("Application is reachable", False, f"HTTP {exc.code}: {exc.reason}"))
    except (URLError, TimeoutError, InvalidURL, OSError, ValueError) as exc:
        checks.append(create_check("Application is reachable", False, str(exc)))

    if response:
        html = response["body"]
        parser.feed(html)
        lower_html = html.lower()
        parsed_final_url = urlparse(response["final_url"])
        same_domain = parsed_final_url.netloc == urlparse(environment_url).netloc
        has_server_error_text = any(
            phrase in lower_html
            for phrase in (
                "internal server error",
                "service unavailable",
                "bad gateway",
                "gateway timeout",
                "application error",
                "stack trace",
                "traceback"
            )
        )

        checks.append(create_check(
            "Application is reachable",
            response["status"] < 400,
            f"Status {response['status']} from {response['final_url']}"
        ))
        checks.append(create_check(
            "Response time is acceptable",
            response["response_time_ms"] <= 5000,
            f"{response['response_time_ms']} ms"
        ))
        checks.append(create_check(
            "Response is HTML",
            "text/html" in response["content_type"],
            response["content_type"] or "No content type returned"
        ))
        checks.append(create_check(
            "Page title is present",
            bool(parser.title.strip()),
            parser.title.strip() or "No title found"
        ))
        checks.append(create_check(
            "No obvious server error text",
            not has_server_error_text,
            "No server error text found" if not has_server_error_text else "Server error text found in page HTML"
        ))
        checks.append(create_check(
            "Redirect stays on same domain",
            same_domain,
            response["final_url"]
        ))
        checks.append(create_check(
            "HTTPS is used",
            parsed_final_url.scheme == "https",
            response["final_url"]
        ))

        if not parser.links:
            warnings.append("No links found on the landing page.")
        if not parser.images:
            warnings.append("No images found on the landing page.")
        if not parser.forms:
            warnings.append("No forms found on the landing page.")

    link_samples = []
    image_samples = []
    script_samples = []
    stylesheet_samples = []

    if response:
        for raw_link in parser.links[:10]:
            link_url = normalize_url(response["final_url"], raw_link)
            if is_http_url(link_url):
                link_samples.append({
                    "url": link_url,
                    **check_url(link_url)
                })

        for raw_image in parser.images[:10]:
            image_url = normalize_url(response["final_url"], raw_image)
            if is_http_url(image_url):
                image_samples.append({
                    "url": image_url,
                    **check_url(image_url)
                })

        for raw_script in parser.scripts[:10]:
            script_url = normalize_url(response["final_url"], raw_script)
            if is_http_url(script_url):
                script_samples.append({
                    "url": script_url,
                    **check_url(script_url)
                })

        for raw_stylesheet in parser.stylesheets[:10]:
            stylesheet_url = normalize_url(response["final_url"], raw_stylesheet)
            if is_http_url(stylesheet_url):
                stylesheet_samples.append({
                    "url": stylesheet_url,
                    **check_url(stylesheet_url)
                })

        checks.append(create_check(
            "Sample links are reachable",
            all(item["ok"] for item in link_samples),
            f"Checked {len(link_samples)} link(s)"
        ))
        checks.append(create_check(
            "Sample images are reachable",
            all(item["ok"] for item in image_samples),
            f"Checked {len(image_samples)} image(s)"
        ))
        checks.append(create_check(
            "Sample scripts are reachable",
            all(item["ok"] for item in script_samples),
            f"Checked {len(script_samples)} script(s)"
        ))
        checks.append(create_check(
            "Sample stylesheets are reachable",
            all(item["ok"] for item in stylesheet_samples),
            f"Checked {len(stylesheet_samples)} stylesheet(s)"
        ))

    failed_checks = [check for check in checks if check["status"] == "FAIL"]

    return {
        "environment_url": environment_url,
        "overall_status": "PASS" if not failed_checks else "FAIL",
        "title": parser.title.strip(),
        "checks": checks,
        "warnings": warnings,
        "sample_results": {
            "links": link_samples,
            "images": image_samples,
            "scripts": script_samples,
            "stylesheets": stylesheet_samples
        }
    }

def read_bulk_urls():
    print("Enter URLs one per line. Press Enter on a blank line to start checking.")
    urls = []

    while True:
        url = input("URL: ").strip()
        if not url:
            break
        urls.append(url)

    return urls

def detect_error_page(url, html):
    lower_url = url.lower()
    lower_html = html.lower()
    error_url_keywords = (
        "404",
        "500",
        "error",
        "not-found",
        "notfound",
        "unavailable"
    )
    error_text_keywords = (
        "404 not found",
        "page not found",
        "not found",
        "internal server error",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "application error",
        "something went wrong",
        "access denied",
        "forbidden"
    )

    url_matches = [keyword for keyword in error_url_keywords if keyword in lower_url]
    text_matches = [keyword for keyword in error_text_keywords if keyword in lower_html]

    return {
        "is_error_page": bool(url_matches or text_matches),
        "url_signals": url_matches,
        "text_signals": text_matches[:5]
    }

def check_single_web_url(raw_url):
    normalized_url = normalize_entered_url(raw_url)
    if not normalized_url or not is_http_url(normalized_url):
        return {
            "input_url": raw_url,
            "url": normalized_url,
            "final_url": "",
            "status": None,
            "response_time_ms": None,
            "redirected": False,
            "result": "FAIL",
            "issue": "Invalid URL",
            "error": "Only http and https URLs are supported."
        }

    try:
        response = fetch_page_response(normalized_url)
    except HTTPError as exc:
        return {
            "input_url": raw_url,
            "url": normalized_url,
            "final_url": exc.url or normalized_url,
            "status": exc.code,
            "response_time_ms": None,
            "redirected": bool(exc.url and exc.url != normalized_url),
            "result": "FAIL",
            "issue": "HTTP error",
            "error": str(exc.reason)
        }
    except (URLError, TimeoutError, InvalidURL, OSError, ValueError) as exc:
        return {
            "input_url": raw_url,
            "url": normalized_url,
            "final_url": "",
            "status": None,
            "response_time_ms": None,
            "redirected": False,
            "result": "FAIL",
            "issue": "Request failed",
            "error": str(exc)
        }

    final_url = normalize_entered_url(response["final_url"])
    redirected = final_url != normalized_url
    error_page = detect_error_page(final_url, response["body"])
    status_failed = response["status"] >= 400
    redirected_to_error = redirected and error_page["is_error_page"]
    result = "FAIL" if status_failed or redirected_to_error or error_page["is_error_page"] else "PASS"

    issue = ""
    if status_failed:
        issue = "HTTP status failed"
    elif redirected_to_error:
        issue = "Redirected to an error page"
    elif error_page["is_error_page"]:
        issue = "Error page content detected"

    parser = PageAssetParser()
    parser.feed(response["body"])

    return {
        "input_url": raw_url,
        "url": normalized_url,
        "final_url": final_url,
        "status": response["status"],
        "response_time_ms": response["response_time_ms"],
        "redirected": redirected,
        "result": result,
        "issue": issue,
        "error": "",
        "title": parser.title.strip(),
        "content_type": response["content_type"],
        "error_page_signals": error_page
    }

def check_bulk_urls(urls):
    results = []

    for index, raw_url in enumerate(urls, start=1):
        print(f"Checking {index}/{len(urls)}: {raw_url}")
        results.append(check_single_web_url(raw_url))

    failed_results = [item for item in results if item["result"] == "FAIL"]
    redirected_results = [item for item in results if item["redirected"]]

    return {
        "total_urls": len(results),
        "passed": len(results) - len(failed_results),
        "failed": len(failed_results),
        "redirected": len(redirected_results),
        "results": results
    }

def extract_domain_from_prompt(prompt):
    match = re.search(r"(?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", prompt)
    if not match:
        return ""
    return match.group(0).strip(".,)")

def extract_prompt_keywords(prompt, domain):
    stop_words = {
        "a", "an", "and", "any", "are", "as", "at", "be", "by", "check",
        "for", "from", "in", "is", "it", "landing", "of", "on", "or",
        "page", "please", "site", "that", "the", "this", "to", "url",
        "verify", "web", "website", "with"
    }
    domain_parts = set(re.split(r"[^a-zA-Z0-9]+", domain.lower()))
    words = re.findall(r"[a-zA-Z0-9]+", prompt.lower())
    keywords = []

    for word in words:
        if len(word) < 3 or word in stop_words or word in domain_parts:
            continue
        if word not in keywords:
            keywords.append(word)

    return keywords

def build_prompt_url_candidates(base_url, keywords):
    parsed_url = urlparse(base_url)
    base = f"{parsed_url.scheme}://{parsed_url.netloc}"
    candidates = [base_url]

    for keyword in keywords:
        candidates.append(f"{base}/en/{keyword}")

    if "build" in keywords:
        candidates.append(f"{base}/en/vehicles/build")

    vehicle_keywords = [word for word in keywords if len(word) <= 5 and word not in ("build",)]
    for vehicle in vehicle_keywords:
        candidates.extend([
            f"{base}/en/vehicles/build/{vehicle}",
            f"{base}/en/vehicles/build/{vehicle}/suv",
            f"{base}/en/vehicles/class/{vehicle}",
            f"{base}/en/vehicles/class/{vehicle}/suv",
        ])

    unique_candidates = []
    for candidate in candidates:
        normalized_candidate = normalize_entered_url(candidate)
        if normalized_candidate not in unique_candidates:
            unique_candidates.append(normalized_candidate)

    return unique_candidates

def score_page_match(url, title, text, keywords):
    haystack = f"{url} {title} {text[:5000]}".lower()
    score = 0
    matched_keywords = []

    for keyword in keywords:
        if keyword in haystack:
            score += 3 if keyword in url.lower() else 1
            matched_keywords.append(keyword)

    return score, matched_keywords

def analyze_prompt_page(prompt, url, response, keywords):
    parser = PageAssetParser()
    parser.feed(response["body"])
    page_text = parser.page_text()
    error_page = detect_error_page(response["final_url"], response["body"])
    score, matched_keywords = score_page_match(
        response["final_url"],
        parser.title,
        page_text,
        keywords
    )
    sample_links = []
    sample_images = []

    for raw_link in parser.links[:10]:
        link_url = normalize_url(response["final_url"], raw_link)
        if is_http_url(link_url):
            sample_links.append({
                "url": link_url,
                **check_url(link_url)
            })

    for raw_image in parser.images[:10]:
        image_url = normalize_url(response["final_url"], raw_image)
        if is_http_url(image_url):
            sample_images.append({
                "url": image_url,
                **check_url(image_url)
            })

    checks = [
        create_check("Page is reachable", response["status"] < 400, f"Status {response['status']}"),
        create_check("Response is HTML", "text/html" in response["content_type"], response["content_type"]),
        create_check("Page title is present", bool(parser.title.strip()), parser.title.strip() or "No title found"),
        create_check("Prompt keywords found", bool(matched_keywords), ", ".join(matched_keywords) or "No prompt keywords found"),
        create_check("No error page detected", not error_page["is_error_page"], str(error_page)),
        create_check("Sample links are reachable", all(item["ok"] for item in sample_links), f"Checked {len(sample_links)} link(s)"),
        create_check("Sample images are reachable", all(item["ok"] for item in sample_images), f"Checked {len(sample_images)} image(s)"),
    ]
    failed_checks = [check for check in checks if check["status"] == "FAIL"]

    return {
        "prompt": prompt,
        "url": url,
        "final_url": normalize_entered_url(response["final_url"]),
        "status": response["status"],
        "response_time_ms": response["response_time_ms"],
        "content_type": response["content_type"],
        "title": parser.title.strip(),
        "match_score": score,
        "matched_keywords": matched_keywords,
        "overall_status": "PASS" if not failed_checks else "FAIL",
        "checks": checks,
        "sample_results": {
            "links": sample_links,
            "images": sample_images
        },
        "page_text_preview": page_text[:1000]
    }

def generate_prompt_check_summary(report):
    prompt = f"""
Act as a Senior QA Engineer.

The user asked:
{report['prompt']}

Evidence from the page check:
Target URL: {report['url']}
Final URL: {report['final_url']}
HTTP Status: {report['status']}
Title: {report['title']}
Matched Keywords: {', '.join(report['matched_keywords'])}
Overall Status: {report['overall_status']}
Checks: {json.dumps(report['checks'], indent=2)}
Page Text Preview: {report['page_text_preview']}

Write a short QA result with:
Verdict:
Evidence:
Risks / Gaps:
Next Action:
"""
    return generate_with_fallback(prompt)

def run_prompt_web_check(user_prompt):
    domain = extract_domain_from_prompt(user_prompt)
    if not domain:
        raise ValueError("Please include a website or domain in the prompt, for example: mbusa.com")

    base_url = normalize_entered_url(domain)
    base_domain = urlparse(base_url).netloc
    keywords = extract_prompt_keywords(user_prompt, domain)
    candidates = build_prompt_url_candidates(base_url, keywords)
    queue = list(candidates)
    visited = set()
    page_results = []

    while queue and len(visited) < MAX_PROMPT_CHECK_PAGES:
        current_url = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)
        print(f"Checking prompt candidate: {current_url}")

        try:
            response = fetch_page_response(current_url)
        except (HTTPError, URLError, TimeoutError, InvalidURL, OSError, ValueError):
            continue

        page_result = analyze_prompt_page(user_prompt, current_url, response, keywords)
        page_results.append(page_result)

        parser = PageAssetParser()
        parser.feed(response["body"])
        for raw_link in parser.links:
            link_url = normalize_url(response["final_url"], raw_link)
            if not is_http_url(link_url) or not is_same_domain(link_url, base_domain):
                continue
            link_score, _matched = score_page_match(link_url, "", "", keywords)
            if link_score and link_url not in visited and link_url not in queue:
                queue.append(link_url)

    if not page_results:
        return {
            "prompt": user_prompt,
            "domain": domain,
            "overall_status": "FAIL",
            "summary": "No reachable HTML page could be found for this prompt.",
            "best_match": None,
            "checked_pages": []
        }

    best_match = sorted(
        page_results,
        key=lambda item: (item["match_score"], item["overall_status"] == "PASS"),
        reverse=True
    )[0]

    try:
        summary = generate_prompt_check_summary(best_match)
    except (errors.ClientError, errors.ServerError):
        summary = (
            f"Verdict: {best_match['overall_status']}\n"
            f"Evidence: Checked {best_match['final_url']} and matched keywords: "
            f"{', '.join(best_match['matched_keywords']) or 'none'}.\n"
            "Risks / Gaps: AI summary could not be generated.\n"
            "Next Action: Review the checks and page evidence."
        )

    return {
        "prompt": user_prompt,
        "domain": domain,
        "overall_status": best_match["overall_status"],
        "summary": summary,
        "best_match": best_match,
        "checked_pages": [
            {
                "url": item["url"],
                "final_url": item["final_url"],
                "title": item["title"],
                "status": item["status"],
                "match_score": item["match_score"],
                "matched_keywords": item["matched_keywords"],
                "overall_status": item["overall_status"]
            }
            for item in page_results
        ]
    }

def run_browser_test(playwright, browser_name, url):
    browser_launcher = getattr(playwright, browser_name)
    browser = None
    page = None
    console_errors = []
    page_errors = []
    failed_requests = []
    start_time = time.perf_counter()

    try:
        browser = browser_launcher.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", lambda request: failed_requests.append({
            "url": request.url,
            "failure": request.failure
        }))

        response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TEST_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=10000)

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status = response.status if response else None
        final_url = normalize_entered_url(page.url)
        title = page.title()
        body_text = page.locator("body").inner_text(timeout=5000) if page.locator("body").count() else ""
        error_page = detect_error_page(final_url, body_text)
        has_body = bool(body_text.strip())
        result = (
            "PASS"
            if status is not None
            and status < 400
            and has_body
            and not console_errors
            and not page_errors
            and not error_page["is_error_page"]
            else "FAIL"
        )

        return {
            "browser": browser_name,
            "result": result,
            "status": status,
            "final_url": final_url,
            "title": title,
            "load_time_ms": elapsed_ms,
            "has_visible_body_text": has_body,
            "console_errors": console_errors[:10],
            "page_errors": page_errors[:10],
            "failed_requests": failed_requests[:10],
            "error_page_signals": error_page,
            "error": ""
        }
    except PlaywrightTimeoutError as exc:
        return {
            "browser": browser_name,
            "result": "FAIL",
            "status": None,
            "final_url": url,
            "title": "",
            "load_time_ms": round((time.perf_counter() - start_time) * 1000, 2),
            "has_visible_body_text": False,
            "console_errors": console_errors[:10],
            "page_errors": page_errors[:10],
            "failed_requests": failed_requests[:10],
            "error_page_signals": {},
            "error": f"Timed out: {exc}"
        }
    except PlaywrightError as exc:
        return {
            "browser": browser_name,
            "result": "FAIL",
            "status": None,
            "final_url": url,
            "title": "",
            "load_time_ms": round((time.perf_counter() - start_time) * 1000, 2),
            "has_visible_body_text": False,
            "console_errors": console_errors[:10],
            "page_errors": page_errors[:10],
            "failed_requests": failed_requests[:10],
            "error_page_signals": {},
            "error": str(exc)
        }
    finally:
        if page:
            page.close()
        if browser:
            browser.close()

def run_cross_browser_test(environment_url, browser_names=None):
    if sync_playwright is None:
        return {
            "environment_url": environment_url,
            "overall_status": "SETUP_REQUIRED",
            "setup_message": "Install Playwright with: pip install playwright && python -m playwright install",
            "results": []
        }

    normalized_url = normalize_entered_url(environment_url)
    if not normalized_url or not is_http_url(normalized_url):
        raise ValueError("Please enter a valid web application URL.")

    selected_browsers = browser_names or ["chromium", "firefox", "webkit"]
    results = []

    try:
        with sync_playwright() as playwright:
            for browser_name in selected_browsers:
                print(f"Running {browser_name} test: {normalized_url}")
                results.append(run_browser_test(playwright, browser_name, normalized_url))
    except (NotImplementedError, PermissionError, OSError, PlaywrightError) as exc:
        return {
            "environment_url": normalized_url,
            "overall_status": "SETUP_REQUIRED",
            "setup_message": (
                "Playwright is installed, but it could not start the browser driver. "
                "On Windows, restart the Streamlit app after this update. If it still fails, "
                "reinstall browsers with: python -m playwright install"
            ),
            "results": [{
                "browser": ", ".join(selected_browsers),
                "result": "FAIL",
                "status": None,
                "final_url": normalized_url,
                "title": "",
                "load_time_ms": 0,
                "has_visible_body_text": False,
                "console_errors": [],
                "page_errors": [],
                "failed_requests": [],
                "error_page_signals": {},
                "error": str(exc)
            }]
        }

    failed_results = [item for item in results if item["result"] == "FAIL"]
    return {
        "environment_url": normalized_url,
        "overall_status": "PASS" if not failed_results else "FAIL",
        "setup_message": "",
        "results": results
    }

def normalize_text_for_compare(text):
    return re.sub(r"\s+", " ", text.lower()).strip()

def split_expected_content_blocks(text):
    raw_blocks = re.split(r"[\r\n]+|(?<=[.!?])\s+", text)
    blocks = []

    for block in raw_blocks:
        clean_block = re.sub(r"\s+", " ", block).strip()
        word_count = len(clean_block.split())
        if word_count < 3 or len(clean_block) < 12:
            continue
        if clean_block.lower() not in [item.lower() for item in blocks]:
            blocks.append(clean_block)

    return blocks[:100]

def extract_keywords_for_block(block):
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "of", "on", "or", "that", "the",
        "this", "to", "was", "were", "with", "you", "your"
    }
    words = re.findall(r"[a-zA-Z0-9]+", block.lower())
    return [word for word in words if len(word) > 3 and word not in stop_words]

def score_content_block(block, target_text):
    normalized_block = normalize_text_for_compare(block)
    normalized_target = normalize_text_for_compare(target_text)

    if normalized_block in normalized_target:
        return {
            "status": "MATCHED",
            "match_percent": 100,
            "matched_keywords": extract_keywords_for_block(block),
            "missing_keywords": []
        }

    keywords = extract_keywords_for_block(block)
    if not keywords:
        return {
            "status": "MISSING",
            "match_percent": 0,
            "matched_keywords": [],
            "missing_keywords": []
        }

    matched_keywords = [keyword for keyword in keywords if keyword in normalized_target]
    missing_keywords = [keyword for keyword in keywords if keyword not in normalized_target]
    match_percent = round((len(matched_keywords) / len(keywords)) * 100)
    status = "PARTIAL" if match_percent >= 50 else "MISSING"

    return {
        "status": status,
        "match_percent": match_percent,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords
    }

def fetch_visible_content(url):
    response = fetch_page_response(normalize_entered_url(url))
    parser = PageAssetParser()
    parser.feed(response["body"])
    return {
        "url": normalize_entered_url(url),
        "final_url": normalize_entered_url(response["final_url"]),
        "status": response["status"],
        "title": parser.title.strip(),
        "content_type": response["content_type"],
        "text": parser.page_text()
    }

def verify_content_reflection(ui_url, expected_source_url="", expected_text=""):
    if not ui_url.strip():
        raise ValueError("UI page URL is required.")
    if not expected_source_url.strip() and not expected_text.strip():
        raise ValueError("Provide a Confluence/web page URL or paste expected content.")

    ui_content = fetch_visible_content(ui_url)

    if expected_source_url.strip():
        expected_content = fetch_visible_content(expected_source_url)
        expected_text_value = expected_content["text"]
        expected_source = {
            "type": "url",
            "url": expected_content["url"],
            "final_url": expected_content["final_url"],
            "title": expected_content["title"],
            "status": expected_content["status"]
        }
    else:
        expected_text_value = expected_text
        expected_source = {
            "type": "pasted_text",
            "url": "",
            "final_url": "",
            "title": "Pasted Expected Content",
            "status": None
        }

    expected_blocks = split_expected_content_blocks(expected_text_value)
    comparisons = []

    for block in expected_blocks:
        score = score_content_block(block, ui_content["text"])
        comparisons.append({
            "expected_content": block,
            **score
        })

    matched = [item for item in comparisons if item["status"] == "MATCHED"]
    partial = [item for item in comparisons if item["status"] == "PARTIAL"]
    missing = [item for item in comparisons if item["status"] == "MISSING"]
    total = len(comparisons)
    reflected_percent = round(((len(matched) + (len(partial) * 0.5)) / total) * 100) if total else 0
    overall_status = "PASS" if total and not missing and reflected_percent >= 90 else "FAIL"

    return {
        "ui_page": {
            "url": ui_content["url"],
            "final_url": ui_content["final_url"],
            "title": ui_content["title"],
            "status": ui_content["status"],
            "content_type": ui_content["content_type"]
        },
        "expected_source": expected_source,
        "overall_status": overall_status,
        "reflected_percent": reflected_percent,
        "total_blocks": total,
        "matched_count": len(matched),
        "partial_count": len(partial),
        "missing_count": len(missing),
        "comparisons": comparisons
    }

def create_standup():
    yesterday = input("Yesterday: ")
    today = input("Today: ")
    blockers = input("Blockers: ")

    if not blockers.strip():
        blockers = "No blockers"

    result = generate_standup(yesterday, today, blockers)

    print("\n=== Stand-up Update ===\n")
    print(result)

    save_memory({
        "date": str(datetime.now()),
        "yesterday": yesterday,
        "today": today,
        "blockers": blockers,
        "update": result
    })

def create_bug():
    summary = input("Bug Summary: ")

    if not summary.strip():
        print("\nBug summary is required.")
        return

    result = generate_bug_report(summary)

    print("\n=== Bug Report ===\n")
    print(result)

    save_bug({
        "date": str(datetime.now()),
        "summary": summary,
        "bug_report": result
    })

def create_test_cases():
    requirement = input("Requirement / Feature Summary: ")

    if not requirement.strip():
        print("\nRequirement or feature summary is required.")
        return

    result = generate_test_cases(requirement)

    print("\n=== Test Cases ===\n")
    print(result)

    save_test_cases({
        "date": str(datetime.now()),
        "requirement": requirement,
        "test_cases": result
    })

def create_broken_link_report():
    environment_url = input("Environment URL: ").strip()

    if not environment_url:
        print("\nEnvironment URL is required.")
        return

    try:
        report = scan_web_application(environment_url)
    except ValueError as exc:
        print(f"\n{exc}")
        return

    print("\n=== Broken Link and Image Report ===\n")
    print(f"Scanned Pages: {len(report['scanned_pages'])}")
    print(f"Broken Links: {len(report['broken_links'])}")
    print(f"Broken Images: {len(report['broken_images'])}")

    if report["broken_links"]:
        print("\nBroken Links:")
        for item in report["broken_links"]:
            print(f"- {item['url']} | Page: {item['page']} | Status: {item['status']} | Error: {item['error']}")

    if report["broken_images"]:
        print("\nBroken Images:")
        for item in report["broken_images"]:
            print(f"- {item['url']} | Page: {item['page']} | Status: {item['status']} | Error: {item['error']}")

    save_broken_link_report({
        "date": str(datetime.now()),
        **report
    })

def create_smoke_test_report():
    environment_url = input("Web App URL: ").strip()

    if not environment_url:
        print("\nWeb app URL is required.")
        return

    try:
        report = run_smoke_test(environment_url)
    except ValueError as exc:
        print(f"\n{exc}")
        return

    print("\n=== Smoke Test Report ===\n")
    print(f"URL: {report['environment_url']}")
    print(f"Overall Status: {report['overall_status']}")
    if report["title"]:
        print(f"Page Title: {report['title']}")

    print("\nChecks:")
    for check in report["checks"]:
        print(f"- {check['status']} | {check['name']} | {check['details']}")

    if report["warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")

    failed_samples = []
    for asset_type, items in report["sample_results"].items():
        for item in items:
            if not item["ok"]:
                failed_samples.append((asset_type, item))

    if failed_samples:
        print("\nFailed Sample Assets:")
        for asset_type, item in failed_samples:
            print(f"- {asset_type}: {item['url']} | Status: {item['status']} | Error: {item['error']}")

    save_smoke_test_report({
        "date": str(datetime.now()),
        **report
    })

def create_bulk_url_report():
    urls = read_bulk_urls()

    if not urls:
        print("\nAt least one URL is required.")
        return

    report = check_bulk_urls(urls)

    print("\n=== Bulk URL Check Report ===\n")
    print(f"Total URLs: {report['total_urls']}")
    print(f"Passed: {report['passed']}")
    print(f"Failed: {report['failed']}")
    print(f"Redirected: {report['redirected']}")

    failed_results = [item for item in report["results"] if item["result"] == "FAIL"]
    redirected_results = [item for item in report["results"] if item["redirected"]]

    if failed_results:
        print("\nFailed URLs:")
        for item in failed_results:
            print(f"- {item['input_url']} | Status: {item['status']} | Issue: {item['issue']} | Final: {item['final_url']} | Error: {item['error']}")

    if redirected_results:
        print("\nRedirected URLs:")
        for item in redirected_results:
            print(f"- {item['input_url']} -> {item['final_url']} | Result: {item['result']} | Issue: {item['issue']}")

    save_bulk_url_report({
        "date": str(datetime.now()),
        **report
    })

def create_prompt_web_check_report():
    user_prompt = input("Web Check Prompt: ").strip()

    if not user_prompt:
        print("\nPrompt is required.")
        return

    try:
        report = run_prompt_web_check(user_prompt)
    except ValueError as exc:
        print(f"\n{exc}")
        return

    print("\n=== Prompt Web Check Report ===\n")
    print(f"Prompt: {report['prompt']}")
    print(f"Domain: {report['domain']}")
    print(f"Overall Status: {report['overall_status']}")
    print("\nSummary:")
    print(report["summary"])

    if report["best_match"]:
        best_match = report["best_match"]
        print("\nBest Matching Page:")
        print(f"URL: {best_match['final_url']}")
        print(f"Title: {best_match['title']}")
        print(f"Matched Keywords: {', '.join(best_match['matched_keywords'])}")
        print("\nChecks:")
        for check in best_match["checks"]:
            print(f"- {check['status']} | {check['name']} | {check['details']}")

    save_prompt_web_check_report({
        "date": str(datetime.now()),
        **report
    })

def create_cross_browser_report():
    environment_url = input("Web App URL: ").strip()

    if not environment_url:
        print("\nWeb app URL is required.")
        return

    try:
        report = run_cross_browser_test(environment_url)
    except ValueError as exc:
        print(f"\n{exc}")
        return

    print("\n=== Cross Browser Test Report ===\n")
    print(f"URL: {report['environment_url']}")
    print(f"Overall Status: {report['overall_status']}")

    if report["setup_message"]:
        print(report["setup_message"])

    for result in report["results"]:
        print(
            f"- {result['browser']} | {result['result']} | "
            f"Status: {result['status']} | Load: {result['load_time_ms']} ms | "
            f"Final: {result['final_url']}"
        )
        if result["error"]:
            print(f"  Error: {result['error']}")
        if result["console_errors"]:
            print(f"  Console Errors: {len(result['console_errors'])}")
        if result["page_errors"]:
            print(f"  Page Errors: {len(result['page_errors'])}")

    save_cross_browser_report({
        "date": str(datetime.now()),
        **report
    })

def create_content_verification_report():
    ui_url = input("UI Page URL: ").strip()
    expected_source_url = input("Expected Content Page URL (optional): ").strip()
    expected_text = ""

    if not expected_source_url:
        print("Paste expected content. Enter a blank line to finish.")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        expected_text = "\n".join(lines)

    try:
        report = verify_content_reflection(ui_url, expected_source_url, expected_text)
    except (HTTPError, URLError, TimeoutError, InvalidURL, OSError, ValueError) as exc:
        print(f"\n{exc}")
        return

    print("\n=== Content Reflection Verification Report ===\n")
    print(f"UI URL: {report['ui_page']['final_url']}")
    print(f"Expected Source: {report['expected_source']['title']}")
    print(f"Overall Status: {report['overall_status']}")
    print(f"Reflected: {report['reflected_percent']}%")
    print(f"Matched: {report['matched_count']}")
    print(f"Partial: {report['partial_count']}")
    print(f"Missing: {report['missing_count']}")

    missing_items = [item for item in report["comparisons"] if item["status"] == "MISSING"]
    if missing_items:
        print("\nMissing Content:")
        for item in missing_items[:20]:
            print(f"- {item['expected_content']}")

    save_content_verification_report({
        "date": str(datetime.now()),
        **report
    })

def main():
    print("=== AI Stand-up Agent ===")
    print("1. Create Stand-up Update")
    print("2. Create Bug Report")
    print("3. Generate Test Cases")
    print("4. Check Broken Links and Images")
    print("5. Run Web App Smoke Test")
    print("6. Bulk URL Failure and Redirect Check")
    print("7. Prompt-Based Web Page Check")
    print("8. Cross Browser Web App Test")
    print("9. Verify Expected Content on UI")

    choice = input("Choose an option (1/2/3/4/5/6/7/8/9): ").strip()

    try:
        if choice == "1":
            create_standup()
        elif choice == "2":
            create_bug()
        elif choice == "3":
            create_test_cases()
        elif choice == "4":
            create_broken_link_report()
        elif choice == "5":
            create_smoke_test_report()
        elif choice == "6":
            create_bulk_url_report()
        elif choice == "7":
            create_prompt_web_check_report()
        elif choice == "8":
            create_cross_browser_report()
        elif choice == "9":
            create_content_verification_report()
        else:
            print("\nInvalid option. Please choose 1, 2, 3, 4, 5, 6, 7, 8, or 9.")
    except errors.ClientError as exc:
        if exc.code == 429:
            print("\nGemini API quota is exhausted. Please wait and try again later, or use another API key.")
            return
        raise
    except errors.ServerError as exc:
        if exc.code == 503:
            print("\nGemini models are temporarily busy. Please try again in a few minutes.")
            return
        raise

if __name__ == "__main__":
    main()
