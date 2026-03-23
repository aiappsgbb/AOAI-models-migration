#!/usr/bin/env python
"""
Browser-based model comparison runner (Playwright).

Automates the comparison UI at http://localhost:5000/compare:
  • Logs in with the configured email
  • For each evaluation type, selects Model A and ALL remaining
    models as Model B (batch comparison)
  • Runs the comparison in the real browser (Verbose ON)
  • Optionally enables Foundry LLM-as-judge scoring
  • Captures full-page screenshots after each comparison
  • Monitors batch progress bar and extracts winner/dimensions/impact
  • Generates a final summary report (JSON + console table)

Usage:
    # FULL MATRIX — every model as A vs the rest × all eval types
    python tools/run_browser_compare.py

    # Single Model A vs rest × all eval types
    python tools/run_browser_compare.py --model-a gpt4o

    # Specific eval types only
    python tools/run_browser_compare.py --model-a gpt4o --types classification rag

    # Include Foundry LLM-as-judge
    python tools/run_browser_compare.py --model-a gpt4o --foundry

    # Limit Model B list
    python tools/run_browser_compare.py --model-a gpt4o --models-b gpt5 phi4 mistral_large_3

    # Keep browser open at the end for inspection
    python tools/run_browser_compare.py --model-a gpt4o --no-close

    # Headless mode
    python tools/run_browser_compare.py --headless

Prerequisites:
    pip install playwright
    playwright install chromium
"""

import argparse
import csv
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
log = logging.getLogger("browser_compare")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EVAL_TYPES = ["classification", "dialog", "general", "rag", "tool_calling"]


# ---------------------------------------------------------------------------
# Dataclass for comparison results
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass(slots=True)
class CompareResult:
    """Outcome of one Model-A × eval-type batch comparison."""
    model_a: str
    eval_type: str
    status: str = "pending"         # pass | fail | error | skip
    winner: str = ""                # model key or "tie"
    dimensions: int = 0
    high_impact: int = 0
    models_b_count: int = 0
    detail: str = ""
    elapsed: float = 0.0
    screenshot: Optional[str] = None
    foundry_status: Optional[str] = None
    foundry_detail: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _cli():
    p = argparse.ArgumentParser(
        description="Run batch model comparisons through the browser UI (Playwright).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--model-a", default=None,
                   help="Baseline model key (e.g. gpt4o). "
                        "If omitted, every available model is used as A in turn.")
    p.add_argument("--models-b", nargs="*", default=None,
                   help="Model B keys (default: all models except Model A)")
    p.add_argument("--types", nargs="*", default=None,
                   help="Evaluation types (default: all 5)")
    p.add_argument("--timeout", type=int, default=1800,
                   help="Max seconds per comparison run (default: 1800 = 30 min)")
    p.add_argument("--foundry", action="store_true",
                   help="Enable Foundry LLM-as-judge scoring")
    p.add_argument("--headless", action="store_true",
                   help="Run Chromium without visible window")
    p.add_argument("--no-close", action="store_true",
                   help="Keep browser open after completion")
    p.add_argument("--base-url", default="http://localhost:5000",
                   help="Flask server base URL")
    p.add_argument("--email", default="asevillano@gmail.com",
                   help="Login email address")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run():
    """Top-level runner — catches all exceptions, always prints summary."""
    # Early check for Playwright
    try:
        from playwright.sync_api import TimeoutError as PwTimeout  # noqa: F401
    except ImportError:
        log.error(
            "Playwright is not installed.\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        sys.exit(1)

    args = _cli()
    base_url = args.base_url.rstrip("/")
    screenshots_dir = Path("tools/compare_screenshots") / _ts()
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    report_path = screenshots_dir / "report.csv"

    results: list[CompareResult] = []

    try:
        _run_browser(args, base_url, screenshots_dir, report_path, results, PwTimeout)
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
    except Exception as exc:
        log.error("Fatal error: %s", exc)
        log.debug(traceback.format_exc())

    # ── Summary ──────────────────────────────────────────────
    model_a_label = args.model_a or "ALL models"
    _print_summary(results, model_a_label)
    _save_report(results, report_path, args)


def _run_browser(args, base_url, screenshots_dir, report_path, results, PwTimeout):
    """Core browser automation — separated so caller can catch all exceptions."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = None
    try:
        browser = pw.chromium.launch(
            headless=args.headless,
            slow_mo=200,
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

        if "/login" in page.url:
            log.info("▸ Logging in as %s", args.email)
            page.fill("#email-input", args.email)
            page.click("#email-btn")
            page.wait_for_timeout(2000)
            if "/login" not in page.url:
                log.info("  ✓ Logged in (no OTP required)")
            else:
                if page.is_visible("#step-code"):
                    log.error(
                        "  ✗ OTP code verification is enabled. "
                        "Set AUTH_CODE_VERIFICATION=false and restart the server."
                    )
                    sys.exit(1)
                page.wait_for_url("**/*", timeout=10_000)
                if "/login" in page.url:
                    log.error("  ✗ Login failed — still on login page")
                    sys.exit(1)
                log.info("  ✓ Logged in")
        else:
            log.info("  ✓ Already authenticated")

        # ─────────────────────────────────────────────────────────
        # 2. NAVIGATE TO COMPARE PAGE & DISCOVER MODELS
        # ─────────────────────────────────────────────────────────
        log.info("▸ Opening comparison page")
        page.goto(f"{base_url}/compare", wait_until="networkidle")
        page.wait_for_selector("#model-a", state="attached", timeout=10_000)

        # Collect available models from Model A dropdown
        all_models = page.eval_on_selector_all(
            "#model-a option",
            "els => els.map(e => ({value: e.value, text: e.textContent.trim()}))"
        )
        model_map = {m["value"]: m["text"] for m in all_models if m["value"]}
        log.info("  Found %d models: %s", len(model_map),
                 ", ".join(model_map.keys()))

        # Build list of Model A's to iterate
        if args.model_a:
            if args.model_a not in model_map:
                log.error("  ✗ Model A '%s' not found in available models: %s",
                          args.model_a, list(model_map.keys()))
                sys.exit(1)
            model_a_list = [args.model_a]
        else:
            model_a_list = list(model_map.keys())
            log.info("  ℹ No --model-a specified → will use ALL %d models as A",
                     len(model_a_list))

        eval_types = args.types if args.types else EVAL_TYPES

        # Check if Foundry is available
        try:
            _foundry_cls = page.locator("#foundry-toggle-label").get_attribute("class") or ""
            foundry_available = "hidden" not in _foundry_cls
        except Exception:
            foundry_available = False
        if args.foundry and not foundry_available:
            log.warning("  ⚠ --foundry requested but Foundry not configured. Skipping.")
            args.foundry = False
        elif foundry_available:
            log.info("  ☁️ Foundry is available%s",
                     " (will enable)" if args.foundry else " (not enabled, use --foundry)")

        # ─────────────────────────────────────────────────────────
        # 3. RUN COMPARISONS  (loop over Model A × eval types)
        # ─────────────────────────────────────────────────────────
        total = len(model_a_list) * len(eval_types)
        counter = 0

        log.info(
            "▸ Comparison plan: %d Model A's × %d eval types = %d total runs",
            len(model_a_list), len(eval_types), total,
        )

        for model_a in model_a_list:
            # Determine Model B list for this Model A
            if args.models_b:
                models_b = [m for m in args.models_b if m in model_map and m != model_a]
                skipped = [m for m in args.models_b if m not in model_map or m == model_a]
                if skipped:
                    log.warning("  Models B skipped (not found or same as A): %s", skipped)
            else:
                models_b = [m for m in model_map.keys() if m != model_a]

            log.info(
                "▸ %s (A) vs %d models (B) × %d types",
                model_map.get(model_a, model_a), len(models_b), len(eval_types),
            )
            log.info("  Models B: %s", ", ".join(models_b))

            for eval_type in eval_types:
                counter += 1
                res = CompareResult(model_a, eval_type, models_b_count=len(models_b))
                results.append(res)
                log.info(
                    "━━━ [%d/%d] %s vs %d models × %s ━━━",
                    counter, total,
                    model_map.get(model_a, model_a),
                    len(models_b), eval_type,
                )
                t0 = time.time()

                try:
                    _run_single_comparison(
                        page, model_a, models_b, eval_type,
                        args, res, screenshots_dir, counter, total,
                        foundry_available, PwTimeout, model_map,
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
                    "  %s %s × %s → %s  winner=%s  dims=%d  (%.1fs)",
                    icon, model_a, eval_type, res.status.upper(),
                    res.winner or "-", res.dimensions, res.elapsed,
                )

        # ─────────────────────────────────────────────────────────
        # 4. DONE
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
# Single comparison flow (Model A vs all B's for one eval type)
# ---------------------------------------------------------------------------

def _run_single_comparison(
    page, model_a, models_b, eval_type,
    args, res, screenshots_dir, counter, total,
    foundry_available, PwTimeout, model_map,
):
    """Run one batch comparison (Model A vs all Model B's) for a given eval type."""
    base_url = args.base_url.rstrip("/")

    # ── Navigate fresh ──
    page.goto(f"{base_url}/compare", wait_until="networkidle")
    page.wait_for_selector("#model-a", state="attached", timeout=10_000)

    # ── Select Model A ──
    page.select_option("#model-a", model_a)
    log.info("  ▸ Model A: %s", model_map.get(model_a, model_a))

    # Wait for Model B options to re-render after Model A change
    # (the page filters by modality — realtime models won't appear for text A)
    page.wait_for_timeout(800)

    # Read which Model B checkboxes actually exist in the DOM
    available_b = page.evaluate(
        "() => Array.from(document.querySelectorAll('#model-b-options input[type=checkbox]'))"
        ".map(cb => cb.value)"
    )
    # Intersect desired models_b with what the page actually offers
    actual_models_b = [m for m in models_b if m in available_b] if models_b else available_b
    if len(actual_models_b) < len(models_b):
        skipped_modality = set(models_b) - set(available_b)
        if skipped_modality:
            log.info("  ℹ %d model(s) hidden by modality filter: %s",
                     len(skipped_modality), ", ".join(sorted(skipped_modality)))

    # ── Select Model B's via JavaScript (reliable, avoids UI race conditions) ──
    actual_b_json = json.dumps(actual_models_b)
    page.evaluate(f"""() => {{
        // Uncheck all B checkboxes first
        document.querySelectorAll('#model-b-options input[type=checkbox]')
            .forEach(cb => {{ cb.checked = false; }});
        // Uncheck select-all
        const sa = document.getElementById('model-b-select-all');
        if (sa) sa.checked = false;
        // Check desired models
        const wanted = {actual_b_json};
        wanted.forEach(m => {{
            const cb = document.querySelector('#model-b-options input[value="' + m + '"]');
            if (cb) cb.checked = true;
        }});
        // Trigger display update
        if (typeof updateModelBDisplay === 'function') updateModelBDisplay();
    }}""")
    page.wait_for_timeout(300)

    # Verify selection via display text
    display_text = page.locator("#model-b-display").inner_text()
    log.info("  ▸ Models B (%d): %s", len(actual_models_b), display_text)

    # Verify correct count
    selected_count = page.evaluate(
        "() => document.querySelectorAll('#model-b-options input[type=checkbox]:checked').length"
    )
    if selected_count != len(actual_models_b):
        log.warning("  ⚠ Expected %d Model B's selected, got %d", len(actual_models_b), selected_count)

    # ── Select eval type ──
    page.select_option("#eval-type", eval_type)

    # ── Enable verbose ──
    verbose_cb = page.locator("#verbose-mode")
    if not verbose_cb.is_checked():
        verbose_cb.check()

    # ── Enable Foundry if requested ──
    if args.foundry and foundry_available:
        foundry_cb = page.locator("#foundry-mode")
        if not foundry_cb.is_checked():
            foundry_cb.check()

    # ── Click "Run Comparison" ──
    run_btn = page.locator("#run-comparison")
    log.info("  ▸ Clicking 'Run Comparison'…")
    run_btn.click()

    # ── Wait for completion ──
    _wait_for_comparison_completion(page, args.timeout, len(actual_models_b), model_map, model_a)

    # ── Check for errors ──
    error_visible = page.locator("#error-state").is_visible()
    if error_visible:
        error_msg = page.locator("#error-message").inner_text()
        res.status = "error"
        res.detail = error_msg[:200]
        log.error("  ✗ Comparison error: %s", res.detail)
        _screenshot(page, screenshots_dir, model_a, eval_type, "ERROR")
        return

    # ── Extract results ──
    res.models_b_count = len(actual_models_b)
    _extract_comparison_results(page, res, eval_type, len(actual_models_b))

    # ── Screenshot ──
    _screenshot(page, screenshots_dir, model_a, eval_type, res.status.upper())
    res.screenshot = f"{model_a}_{eval_type}_{res.status.upper()}.png"


def _wait_for_comparison_completion(page, timeout_secs, num_models_b, model_map, model_a):
    """Poll the UI until the comparison finishes."""
    deadline = time.time() + timeout_secs
    is_batch = num_models_b > 1
    last_pct = ""

    # First, confirm the button became disabled (comparison started)
    page.wait_for_timeout(2000)

    while time.time() < deadline:
        time.sleep(2.5)

        # Check if results or error are visible (comparison done)
        results_visible = page.locator("#results-section").is_visible()
        error_visible = page.locator("#error-state").is_visible()
        if results_visible or error_visible:
            log.info("    ✓ Comparison completed")
            return

        # Check if button is re-enabled AND no loading spinner
        btn_disabled = page.locator("#run-comparison").get_attribute("disabled")
        loading_visible = page.locator("#loading-state").is_visible()
        if btn_disabled is None and not loading_visible:
            # Button enabled + no loading = might be done or errored
            page.wait_for_timeout(1000)
            results_visible = page.locator("#results-section").is_visible()
            error_visible = page.locator("#error-state").is_visible()
            if results_visible or error_visible:
                log.info("    ✓ Comparison completed")
                return

        # For batch: log progress
        if is_batch:
            try:
                pct_el = page.locator("#batch-progress-pct")
                if pct_el.is_visible():
                    pct = pct_el.inner_text()
                    if pct != last_pct:
                        label = page.locator("#batch-progress-label").inner_text()
                        log.info("    ⏳ %s — %s", pct, label)
                        last_pct = pct
            except Exception:
                pass

    raise TimeoutError(f"Comparison did not complete within {timeout_secs}s")


def _extract_comparison_results(page, res: CompareResult, eval_type: str, num_models_b: int):
    """Extract winner, dimensions, and high-impact counts from the results section."""
    results_visible = page.locator("#results-section").is_visible()
    if not results_visible:
        res.status = "error"
        res.detail = "Results section not visible after completion"
        return

    try:
        # For batch comparisons, the first tab's results are shown by default
        # Extract from the summary cards
        winner_text = page.locator("#winner-name").inner_text().strip()
        dimensions_text = page.locator("#dimensions-count").inner_text().strip()
        high_impact_text = page.locator("#high-impact-count").inner_text().strip()

        res.winner = winner_text
        res.detail = f"Winner={winner_text}; Dimensions={dimensions_text}; High Impact={high_impact_text}"

        try:
            res.dimensions = int(dimensions_text)
        except (ValueError, TypeError):
            pass

        try:
            res.high_impact = int(high_impact_text)
        except (ValueError, TypeError):
            pass

        # Determine pass/fail
        res.status = "pass"

        # For batch: check if batch tabs are visible and extract summary info
        if num_models_b > 1:
            try:
                batch_tabs = page.locator("#batch-tabs-bar")
                if batch_tabs.is_visible():
                    tab_count = page.locator("#batch-tabs button").count()
                    res.detail += f"; Batch tabs={tab_count}"
            except Exception:
                pass

        # Check for Foundry results
        try:
            foundry_section = page.locator("#foundry-compare-section")
            if foundry_section.is_visible():
                foundry_meta = page.locator("#foundry-compare-meta").inner_text().strip()
                res.foundry_status = "completed"
                res.foundry_detail = foundry_meta
        except Exception:
            pass

    except Exception as exc:
        res.status = "pass"
        res.detail = f"Completed (result extraction error: {exc})"


def _screenshot(page, directory: Path, model_a: str, eval_type: str, tag: str):
    """Save a full-page screenshot."""
    fname = f"{model_a}_vs_all_{eval_type}_{tag}.png"
    path = directory / fname
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Summary & Report
# ---------------------------------------------------------------------------

def _print_summary(results: list[CompareResult], model_a_label: str):
    """Print a console summary table, grouped by Model A."""
    if not results:
        log.warning("No comparison results to summarise.")
        return

    # Group results by model_a
    model_a_keys = list(dict.fromkeys(r.model_a for r in results))

    col_a = 16
    col_w = 18
    multi_a = len(model_a_keys) > 1
    if multi_a:
        header = f"{'Model A':<{col_a}}{'Eval Type':<{col_w}}{'Winner':<{col_w}}{'Dims':>6}{'Impact':>8}{'Time':>8}"
    else:
        header = f"{'Eval Type':<{col_w}}{'Winner':<{col_w}}{'Dims':>6}{'Impact':>8}{'Time':>8}"
    sep = "─" * len(header)

    print(f"\n{sep}")
    print(f"  COMPARISON SUMMARY — Model A: {model_a_label}")
    print(sep)
    print(header)
    print(sep)

    pass_count = fail_count = skip_count = error_count = 0
    total_elapsed = 0.0
    prev_a = None

    for r in results:
        winner_display = r.winner if r.winner else "-"
        if len(winner_display) > col_w - 2:
            winner_display = winner_display[:col_w - 4] + "…"

        if multi_a:
            a_col = r.model_a if r.model_a != prev_a else ""
            if r.model_a != prev_a and prev_a is not None:
                print(f"{'':─<{len(header)}}")
            prev_a = r.model_a
            print(
                f"{a_col:<{col_a}}"
                f"{r.eval_type:<{col_w}}"
                f"{winner_display:<{col_w}}"
                f"{r.dimensions:>6}"
                f"{r.high_impact:>8}"
                f"{r.elapsed:>7.0f}s"
            )
        else:
            print(
                f"{r.eval_type:<{col_w}}"
                f"{winner_display:<{col_w}}"
                f"{r.dimensions:>6}"
                f"{r.high_impact:>8}"
                f"{r.elapsed:>7.0f}s"
            )

        total_elapsed += r.elapsed
        if r.status == "pass":
            pass_count += 1
        elif r.status == "fail":
            fail_count += 1
        elif r.status == "skip":
            skip_count += 1
        else:
            error_count += 1

    print(sep)
    print(
        f"  Total: {len(results)} | "
        f"✓ Pass: {pass_count} | "
        f"⚠ Fail: {fail_count} | "
        f"» Skip: {skip_count} | "
        f"✗ Error: {error_count} | "
        f"⏱ {total_elapsed:.0f}s"
    )
    print(sep)
    print()


def _save_report(results: list[CompareResult], report_path: Path, args):
    """Save a CSV report."""
    fieldnames = [
        "timestamp", "model_a", "eval_type", "status", "winner",
        "dimensions", "high_impact", "models_b_count", "detail",
        "elapsed", "screenshot", "foundry_status", "foundry_detail",
        "email", "base_url", "foundry_enabled",
    ]
    ts = datetime.now().isoformat()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "timestamp": ts,
                "model_a": r.model_a,
                "eval_type": r.eval_type,
                "status": r.status,
                "winner": r.winner,
                "dimensions": r.dimensions,
                "high_impact": r.high_impact,
                "models_b_count": r.models_b_count,
                "detail": r.detail,
                "elapsed": r.elapsed,
                "screenshot": r.screenshot or "",
                "foundry_status": r.foundry_status or "",
                "foundry_detail": r.foundry_detail,
                "email": args.email,
                "base_url": args.base_url,
                "foundry_enabled": args.foundry,
            })

    log.info("📄 Report saved to %s", report_path)
    print(f"  📄 {report_path}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run()
