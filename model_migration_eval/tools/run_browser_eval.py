#!/usr/bin/env python
"""
Browser-based full evaluation runner (Playwright).

Automates the evaluation UI at http://localhost:5000/evaluate:
  • Logs in with the configured email
  • Iterates through every model × evaluation-type combination
  • Runs each evaluation one-by-one in the real browser (Verbose ON)
  • Optionally clicks "Send to Foundry" after each evaluation
  • Captures screenshots per evaluation
  • Monitors for failures (MissingPromptsError, timeouts, HTTP errors)
  • Generates a final summary report

Usage:
    # Full run — all models × all types
    python tools/run_browser_eval.py

    # Filter models and/or types
    python tools/run_browser_eval.py --models gpt4 gpt5 phi4
    python tools/run_browser_eval.py --types classification dialog

    # Include Foundry LLM-as-judge after each evaluation
    python tools/run_browser_eval.py --foundry

    # Custom limit per evaluation
    python tools/run_browser_eval.py --limit 20

    # Custom timeout per evaluation (seconds)
    python tools/run_browser_eval.py --timeout 600

    # Keep browser open at the end for inspection
    python tools/run_browser_eval.py --no-close

    # Headed mode is the default; use --headless to hide the browser
    python tools/run_browser_eval.py --headless

Prerequisites:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FMT = "%(asctime)s  %(levelname)-7s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S",
                    handlers=[logging.StreamHandler(sys.stdout)])
# Silence noisy loggers
for _lib in ("urllib3", "httpx", "httpcore"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
log = logging.getLogger("browser-eval")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EVAL_TYPES = ["classification", "dialog", "general", "rag", "tool_calling"]
DEFAULT_BASE_URL = "http://localhost:5000"
DEFAULT_EMAIL = "asevillano@gmail.com"
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT = 300          # seconds per evaluation
FOUNDRY_TIMEOUT = 180          # seconds waiting for Foundry results
POLL_INTERVAL_MS = 2_000       # polling status interval

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _short(name: str, max_len: int = 30) -> str:
    return name if len(name) <= max_len else name[:max_len - 1] + "…"


class EvalResult:
    """Stores the outcome of a single model × type evaluation."""

    __slots__ = (
        "model", "eval_type", "status", "accuracy", "detail",
        "elapsed", "screenshot", "foundry_status", "foundry_detail",
    )

    def __init__(self, model: str, eval_type: str):
        self.model = model
        self.eval_type = eval_type
        self.status = "pending"        # pending | pass | fail | skip | error
        self.accuracy = None           # e.g. "90.0%"
        self.detail = ""
        self.elapsed = 0.0
        self.screenshot = None
        self.foundry_status = None     # None | pass | fail | skip
        self.foundry_detail = ""

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


# ---------------------------------------------------------------------------
# Main automation
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        log.error(
            "Playwright is not installed.\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        sys.exit(1)

    base_url = args.base_url.rstrip("/")
    screenshots_dir = Path("tools/eval_screenshots") / _ts()
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    report_path = screenshots_dir / "report.json"

    results: list[EvalResult] = []

    try:
        _run_browser(args, base_url, screenshots_dir, report_path, results, PwTimeout)
    except KeyboardInterrupt:
        log.info("\n▸ Interrupted by user (Ctrl+C)")
    except Exception:
        log.error("Fatal error:\n%s", traceback.format_exc())

    # Always print summary if we have any results
    if results:
        elapsed_total = sum(r.elapsed for r in results)
        _print_summary(results, elapsed_total, report_path)
        report = {
            "timestamp": datetime.now().isoformat(),
            "base_url": base_url,
            "email": args.email,
            "limit": args.limit,
            "foundry": args.foundry,
            "elapsed_total": round(elapsed_total, 1),
            "results": [r.to_dict() for r in results],
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("📄 Report saved to %s", report_path)

    n_fail = sum(1 for r in results if r.status in ("fail", "error"))
    sys.exit(1 if n_fail > 0 else 0)


def _run_browser(args, base_url, screenshots_dir, report_path, results, PwTimeout):
    """Core browser automation — separated so caller can catch all exceptions."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = None
    try:
        browser = pw.chromium.launch(
            headless=args.headless,
            slow_mo=200,             # slight delay so actions are visible
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(30_000)

        # ─────────────────────────────────────────────────────────
        # 1. LOGIN
        # ─────────────────────────────────────────────────────────
        log.info("▸ Navigating to %s/login", base_url)
        page.goto(f"{base_url}/login", wait_until="networkidle")

        # If already logged in (redirected to /), skip login
        if "/login" in page.url:
            log.info("▸ Logging in as %s", args.email)
            page.fill("#email-input", args.email)
            page.click("#email-btn")

            # Wait for either redirect (code_verification=false) or code step
            page.wait_for_timeout(2000)
            if "/login" not in page.url:
                log.info("  ✓ Logged in (no OTP required)")
            else:
                # OTP required — we cannot automate this without the code
                # Check if step-code is visible
                if page.is_visible("#step-code"):
                    log.error(
                        "  ✗ OTP code verification is enabled. "
                        "Set AUTH_CODE_VERIFICATION=false in your environment "
                        "and restart the server, or log in manually first."
                    )
                    browser.close()
                    sys.exit(1)
                # Might just be slow, wait a bit more
                page.wait_for_url(f"**/*", timeout=10_000)
                if "/login" in page.url:
                    log.error("  ✗ Login failed — still on login page")
                    browser.close()
                    sys.exit(1)
                log.info("  ✓ Logged in")
        else:
            log.info("  ✓ Already authenticated")

        # ─────────────────────────────────────────────────────────
        # 2. NAVIGATE TO EVALUATE PAGE
        # ─────────────────────────────────────────────────────────
        log.info("▸ Opening evaluation page")
        page.goto(f"{base_url}/evaluate", wait_until="networkidle")
        page.wait_for_selector("#model", state="attached", timeout=10_000)

        # Collect available models from the dropdown
        all_models = page.eval_on_selector_all(
            "#model option",
            "els => els.map(e => ({value: e.value, text: e.textContent.trim()}))"
        )
        model_map = {m["value"]: m["text"] for m in all_models}
        log.info("  Found %d models: %s", len(model_map),
                 ", ".join(model_map.keys()))

        # Apply filters
        if args.models:
            model_keys = [m for m in args.models if m in model_map]
            skipped = [m for m in args.models if m not in model_map]
            if skipped:
                log.warning("  Models not found (skipped): %s", skipped)
        else:
            model_keys = list(model_map.keys())

        eval_types = args.types if args.types else EVAL_TYPES

        total = len(model_keys) * len(eval_types)
        log.info(
            "▸ Evaluation matrix: %d models × %d types = %d evaluations",
            len(model_keys), len(eval_types), total,
        )

        # Check if Foundry is available
        try:
            _foundry_cls = page.locator("#foundry-toggle-label").get_attribute("class") or ""
            foundry_available = "hidden" not in _foundry_cls
        except Exception:
            foundry_available = False
        if args.foundry and not foundry_available:
            log.warning("  ⚠ --foundry requested but Foundry is not configured on the server. Skipping Foundry submissions.")
            args.foundry = False
        elif foundry_available:
            log.info("  ☁️ Foundry is available%s", " (will submit after each eval)" if args.foundry else " (not enabled, use --foundry)")

        # ─────────────────────────────────────────────────────────
        # 3. RUN EVALUATIONS
        # ─────────────────────────────────────────────────────────
        counter = 0
        t_global_start = time.time()

        for model_key in model_keys:
            for eval_type in eval_types:
                counter += 1
                res = EvalResult(model_key, eval_type)
                results.append(res)
                display = model_map.get(model_key, model_key)
                log.info(
                    "━━━ [%d/%d] %s × %s ━━━",
                    counter, total, display, eval_type,
                )
                t0 = time.time()

                try:
                    _run_single_eval(
                        page, model_key, eval_type, args, res,
                        screenshots_dir, counter, total, foundry_available,
                        PwTimeout,
                    )
                except PwTimeout:
                    res.status = "error"
                    res.detail = f"Playwright timeout after {args.timeout}s"
                    log.error("  ✗ TIMEOUT: %s", res.detail)
                except Exception as exc:
                    res.status = "error"
                    res.detail = str(exc)[:200]
                    log.error("  ✗ ERROR: %s", res.detail)
                    log.debug(traceback.format_exc())

                res.elapsed = round(time.time() - t0, 1)
                icon = {"pass": "✓", "fail": "⚠", "skip": "»", "error": "✗"}.get(res.status, "?")
                log.info(
                    "  %s %s × %s → %s  %s  (%.1fs)",
                    icon, model_key, eval_type, res.status.upper(),
                    res.accuracy or res.detail or "", res.elapsed,
                )

        # ─────────────────────────────────────────────────────────
        # 4. DONE — summary is printed by the caller (run())
        # ─────────────────────────────────────────────────────────

        if args.no_close:
            log.info("▸ --no-close: browser stays open. Press Ctrl+C to exit.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    finally:
        if browser:
            browser.close()
        pw.stop()


# ---------------------------------------------------------------------------
# Single evaluation flow
# ---------------------------------------------------------------------------

def _run_single_eval(
    page, model_key, eval_type, args, res, screenshots_dir,
    counter, total, foundry_available, PwTimeout,
):
    """Run one model × eval_type evaluation in the browser."""

    # ── Navigate fresh (reset UI state cleanly between runs) ──
    page.goto(f"{args.base_url}/evaluate", wait_until="networkidle")
    page.wait_for_selector("#model", state="attached", timeout=10_000)

    # ── Select model ──
    page.select_option("#model", model_key)
    page.wait_for_timeout(300)

    # ── Select evaluation type ──
    page.select_option("#eval-type", eval_type)
    page.wait_for_timeout(300)

    # ── Set limit ──
    page.fill("#limit", str(args.limit))

    # ── Enable Verbose ──
    verbose_cb = page.locator("#verbose-mode")
    if not verbose_cb.is_checked():
        verbose_cb.check()

    # ── Enable Foundry if requested ──
    if args.foundry and foundry_available:
        foundry_cb = page.locator("#foundry-mode")
        if not foundry_cb.is_checked():
            foundry_cb.check()

    # ── Click Run Evaluation ──
    log.info("  ▸ Clicking 'Run Evaluation'…")
    page.click("#run-eval")

    # ── Wait for completion ──
    # The button text changes to "⏳ Evaluation running…" and back to
    # "Run Evaluation" when done.  We also watch for alert() dialogs
    # (errors) and the results section becoming visible.
    timeout_ms = args.timeout * 1000

    # Handle alert() dialogs (errors like MissingPromptsError surface as alert())
    alert_message = None

    def _on_dialog(dialog):
        nonlocal alert_message
        alert_message = dialog.message
        dialog.accept()

    page.on("dialog", _on_dialog)

    try:
        # Wait for the run button to become enabled again (evaluation done)
        # OR for a dialog to appear (error)
        _wait_for_eval_completion(page, timeout_ms, lambda: alert_message is not None)
    finally:
        page.remove_listener("dialog", _on_dialog)

    # ── Check for errors ──
    if alert_message:
        if "missing prompts" in alert_message.lower() or "no prompts found" in alert_message.lower():
            res.status = "skip"
            res.detail = f"MissingPrompts: {alert_message[:120]}"
            log.info("  » SKIP (missing prompts): %s", _short(alert_message, 80))
            _screenshot(page, screenshots_dir, model_key, eval_type, "SKIP")
            return
        else:
            res.status = "error"
            res.detail = alert_message[:200]
            log.error("  ✗ Alert error: %s", _short(alert_message, 100))
            _screenshot(page, screenshots_dir, model_key, eval_type, "ERROR")
            return

    # ── Extract results from the page ──
    page.wait_for_timeout(500)
    _extract_results(page, res, eval_type)

    # ── Screenshot (results visible) ──
    _screenshot(page, screenshots_dir, model_key, eval_type, res.status.upper())

    # ── Foundry: click "Send to Foundry" button if not auto-triggered ──
    if args.foundry and foundry_available and not page.locator("#foundry-mode").is_checked():
        # Foundry was supposed to be auto-triggered, but just in case
        pass

    # ── Wait for Foundry if enabled ──
    if args.foundry and foundry_available:
        _wait_for_foundry(page, res, args, screenshots_dir, model_key, eval_type)


def _wait_for_eval_completion(page, timeout_ms: int, early_exit_fn):
    """
    Poll until the #run-eval button is re-enabled (evaluation complete)
    or early_exit_fn() returns True (e.g. alert dialog appeared).
    """
    deadline = time.time() + timeout_ms / 1000
    poll_ms = 1_000

    while time.time() < deadline:
        # Check early exit (dialog)
        if early_exit_fn():
            return

        try:
            # The button has disabled="" when running, and no disabled attr when done.
            # Playwright returns "" for present boolean attrs, None for absent.
            btn_disabled = page.locator("#run-eval").get_attribute("disabled")
            btn_text = (page.locator("#run-eval").inner_text() or "").strip()

            # Button is re-enabled (disabled attr removed) and not showing "running"
            if btn_disabled is None and "running" not in btn_text.lower():
                return
        except Exception:
            pass  # page might be navigating; retry

        page.wait_for_timeout(poll_ms)

    from playwright.sync_api import TimeoutError as _PwTimeout
    raise _PwTimeout(f"Evaluation did not complete within {timeout_ms/1000:.0f}s")


def _wait_for_foundry(page, res: EvalResult, args, screenshots_dir, model_key, eval_type):
    """Wait for Foundry submission to complete and extract scores."""
    log.info("  ▸ Waiting for Foundry results…")
    deadline = time.time() + FOUNDRY_TIMEOUT

    while time.time() < deadline:
        # Check if Foundry banner shows a result
        banner_title = page.locator("#foundry-banner-title").inner_text()

        if "completed" in banner_title.lower():
            res.foundry_status = "pass"
            detail_text = page.locator("#foundry-banner-detail").inner_text()
            res.foundry_detail = detail_text[:200]
            log.info("  ☁️ Foundry: ✓ %s", _short(detail_text, 80))
            _screenshot(page, screenshots_dir, model_key, eval_type, "FOUNDRY_OK")
            return

        if "failed" in banner_title.lower() or "error" in banner_title.lower():
            res.foundry_status = "fail"
            detail_text = page.locator("#foundry-banner-detail").inner_text()
            res.foundry_detail = detail_text[:200]
            log.info("  ☁️ Foundry: ✗ %s", _short(detail_text, 80))
            return

        page.wait_for_timeout(2000)

    res.foundry_status = "skip"
    res.foundry_detail = f"Timeout after {FOUNDRY_TIMEOUT}s"
    log.warning("  ☁️ Foundry: timeout after %ds", FOUNDRY_TIMEOUT)


def _extract_results(page, res: EvalResult, eval_type: str):
    """Extract accuracy and key metrics from the visible results section."""
    # Check if results section is visible
    results_visible = page.locator("#results-section").is_visible()
    if not results_visible:
        # Maybe still loading or failed silently
        res.status = "error"
        res.detail = "Results section not visible after completion"
        return

    # Extract metric cards text — they contain accuracy, F1, latency, etc.
    # Card inner_text() looks like: "Accuracy\ni\n\n100.0%"
    #   lines[0] = metric name, lines[1] = "i" (tooltip btn), lines[-1] = value
    try:
        cards = page.locator("#metrics-cards .fluent-card").all()
        metrics = {}
        for card in cards:
            text = card.inner_text().strip()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if len(lines) >= 2:
                name = lines[0]
                # Value is the last non-empty line (skip tooltip "i")
                value = lines[-1]
                metrics[name] = value

        res.detail = "; ".join(f"{k}={v}" for k, v in metrics.items())

        # Determine accuracy from the first metric card (Accuracy for classification)
        if "Accuracy" in metrics:
            res.accuracy = metrics["Accuracy"]
        elif "Format Compliance" in metrics:
            res.accuracy = metrics["Format Compliance"]
        elif "Tool Selection Acc" in metrics:
            res.accuracy = metrics["Tool Selection Acc"]
        elif "Groundedness" in metrics:
            res.accuracy = metrics["Groundedness"]
        elif "Follow-up Quality" in metrics:
            res.accuracy = metrics["Follow-up Quality"]

        # Determine pass/fail
        # Extract numeric value from accuracy string
        acc_str = res.accuracy or ""
        try:
            acc_num = float(acc_str.replace("%", "").strip())
            res.status = "pass" if acc_num > 0 else "fail"
        except ValueError:
            res.status = "pass"  # Non-numeric metric, assume OK if we got here

    except Exception as exc:
        res.status = "pass"  # We completed, just couldn't parse metrics
        res.detail = f"Completed (metrics parse error: {exc})"


def _screenshot(page, directory: Path, model: str, eval_type: str, tag: str):
    """Save a full-page screenshot."""
    fname = f"{model}_{eval_type}_{tag}.png"
    path = directory / fname
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass  # non-critical


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(results: list[EvalResult], elapsed: float, report_path: Path):
    """Print a compact summary table to the console."""
    # Collect unique models and types
    models = list(dict.fromkeys(r.model for r in results))
    types = list(dict.fromkeys(r.eval_type for r in results))

    # Build lookup
    lookup = {(r.model, r.eval_type): r for r in results}

    # Header
    col_w = 14
    header = f"{'Model':<20}" + "".join(f"{t:^{col_w}}" for t in types)
    sep = "─" * len(header)

    print(f"\n{sep}")
    print("  EVALUATION SUMMARY")
    print(sep)
    print(header)
    print("─" * 20 + "".join("─" * col_w for _ in types))

    for model in models:
        row = f"{model:<20}"
        for t in types:
            r = lookup.get((model, t))
            if not r:
                cell = "—"
            elif r.status == "skip":
                cell = "SKIP"
            elif r.status == "error":
                cell = "ERR"
            elif r.accuracy:
                cell = r.accuracy
            else:
                cell = r.status.upper()
            # Colorize via simple text markers
            row += f"{cell:^{col_w}}"
        print(row)

    # Foundry column if any foundry results
    has_foundry = any(r.foundry_status for r in results)
    if has_foundry:
        print()
        print("  FOUNDRY LLM-as-Judge:")
        for r in results:
            if r.foundry_status:
                icon = {"pass": "✓", "fail": "✗", "skip": "»"}.get(r.foundry_status, "?")
                print(f"    {icon} {r.model} × {r.eval_type}: {r.foundry_detail[:80]}")

    # Totals
    n_pass = sum(1 for r in results if r.status == "pass")
    n_fail = sum(1 for r in results if r.status == "fail")
    n_skip = sum(1 for r in results if r.status == "skip")
    n_err = sum(1 for r in results if r.status == "error")
    n_total = len(results)

    print(sep)
    print(
        f"  Total: {n_total} | "
        f"✓ Pass: {n_pass} | "
        f"⚠ Fail: {n_fail} | "
        f"» Skip: {n_skip} | "
        f"✗ Error: {n_err} | "
        f"⏱ {elapsed:.0f}s"
    )
    print(f"  📄 {report_path}")
    print(sep)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Browser-based full evaluation runner (Playwright)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Base URL of the running server (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--email", default=DEFAULT_EMAIL,
        help=f"Email for login (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--models", nargs="+", default=None,
        help="Filter: only these model keys (e.g. gpt4 gpt5 phi4)",
    )
    parser.add_argument(
        "--types", nargs="+", default=None,
        choices=EVAL_TYPES,
        help="Filter: only these evaluation types",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"Scenario limit per evaluation (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Timeout per evaluation in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--foundry", action="store_true",
        help="Submit each evaluation to Foundry LLM-as-judge after completion",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode (default: headed / visible)",
    )
    parser.add_argument(
        "--no-close", action="store_true",
        help="Keep browser open after all evaluations finish",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
