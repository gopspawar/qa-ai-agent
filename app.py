from datetime import datetime

import streamlit as st
from google.genai import errors

from agent import (
    BROKEN_LINKS_FILE,
    BUGS_FILE,
    BULK_URL_REPORTS_FILE,
    CONTENT_VERIFICATION_REPORTS_FILE,
    CROSS_BROWSER_REPORTS_FILE,
    MEMORY_FILE,
    PROMPT_WEB_CHECKS_FILE,
    SMOKE_TESTS_FILE,
    TEST_CASES_FILE,
    check_bulk_urls,
    generate_bug_report,
    generate_standup,
    generate_test_cases,
    load_json_file,
    run_cross_browser_test,
    run_prompt_web_check,
    run_smoke_test,
    save_broken_link_report,
    save_bug,
    save_bulk_url_report,
    save_content_verification_report,
    save_cross_browser_report,
    save_memory,
    save_prompt_web_check_report,
    save_smoke_test_report,
    save_test_cases,
    scan_web_application,
    verify_content_reflection,
)


st.set_page_config(
    page_title="QA AI Agent",
    page_icon="QA",
    layout="wide",
)


def handle_ai_error(exc):
    if isinstance(exc, errors.ClientError) and exc.code == 429:
        st.error("Gemini API quota is exhausted. Please wait and try again later, or use another API key.")
        return
    if isinstance(exc, errors.ServerError) and exc.code == 503:
        st.error("Gemini models are temporarily busy. Please try again in a few minutes.")
        return
    st.error(str(exc))


def save_with_date(save_function, payload):
    save_function({
        "date": str(datetime.now()),
        **payload,
    })


def render_history(title, file_path):
    with st.expander(title):
        data = load_json_file(file_path)
        if not data:
            st.info("No saved records yet.")
            return
        st.json(data[-5:])


def render_result_table(title, rows):
    st.subheader(title)
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.success("No records found.")


st.title("QA AI Agent")
st.caption("Generate QA updates, reports, test cases, and web validation results from one place.")

tabs = st.tabs([
    "Stand-up",
    "Bug Report",
    "Test Cases",
    "Broken Links",
    "Smoke Test",
    "Bulk URLs",
    "Prompt Check",
    "Cross Browser",
    "Content Match",
    "Saved Reports",
])

with tabs[0]:
    st.header("Stand-up Update")
    with st.form("standup_form"):
        yesterday = st.text_area("Yesterday")
        today = st.text_area("Today")
        blockers = st.text_area("Blockers", value="No blockers")
        submitted = st.form_submit_button("Generate Stand-up")

    if submitted:
        if not yesterday.strip() or not today.strip():
            st.warning("Yesterday and Today are required.")
        else:
            with st.spinner("Generating stand-up update..."):
                try:
                    result = generate_standup(yesterday, today, blockers or "No blockers")
                    st.success("Stand-up generated.")
                    st.text_area("Result", result, height=220)
                    save_with_date(save_memory, {
                        "yesterday": yesterday,
                        "today": today,
                        "blockers": blockers or "No blockers",
                        "update": result,
                    })
                except (errors.ClientError, errors.ServerError) as exc:
                    handle_ai_error(exc)

with tabs[1]:
    st.header("Bug Report")
    with st.form("bug_form"):
        summary = st.text_area("Bug Summary")
        submitted = st.form_submit_button("Create Bug Report")

    if submitted:
        if not summary.strip():
            st.warning("Bug summary is required.")
        else:
            with st.spinner("Creating bug report..."):
                try:
                    result = generate_bug_report(summary)
                    st.success("Bug report created.")
                    st.text_area("Result", result, height=320)
                    save_with_date(save_bug, {
                        "summary": summary,
                        "bug_report": result,
                    })
                except (errors.ClientError, errors.ServerError) as exc:
                    handle_ai_error(exc)

with tabs[2]:
    st.header("Generate Test Cases")
    with st.form("test_case_form"):
        requirement = st.text_area("Requirement / Feature Summary")
        submitted = st.form_submit_button("Generate Test Cases")

    if submitted:
        if not requirement.strip():
            st.warning("Requirement or feature summary is required.")
        else:
            with st.spinner("Generating test cases..."):
                try:
                    result = generate_test_cases(requirement)
                    st.success("Test cases generated.")
                    st.text_area("Result", result, height=420)
                    save_with_date(save_test_cases, {
                        "requirement": requirement,
                        "test_cases": result,
                    })
                except (errors.ClientError, errors.ServerError) as exc:
                    handle_ai_error(exc)

with tabs[3]:
    st.header("Broken Links and Images")
    with st.form("broken_links_form"):
        environment_url = st.text_input("Environment URL", placeholder="www.example.com")
        submitted = st.form_submit_button("Scan Web Application")

    if submitted:
        if not environment_url.strip():
            st.warning("Environment URL is required.")
        else:
            with st.spinner("Scanning pages, links, and images..."):
                try:
                    report = scan_web_application(environment_url)
                    save_with_date(save_broken_link_report, report)
                    st.success("Scan completed.")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Scanned Pages", len(report["scanned_pages"]))
                    col2.metric("Broken Links", len(report["broken_links"]))
                    col3.metric("Broken Images", len(report["broken_images"]))
                    render_result_table("Broken Links", report["broken_links"])
                    render_result_table("Broken Images", report["broken_images"])
                except ValueError as exc:
                    st.error(str(exc))

with tabs[4]:
    st.header("Web App Smoke Test")
    with st.form("smoke_form"):
        web_app_url = st.text_input("Web App URL", placeholder="www.example.com")
        submitted = st.form_submit_button("Run Smoke Test")

    if submitted:
        if not web_app_url.strip():
            st.warning("Web app URL is required.")
        else:
            with st.spinner("Running smoke test..."):
                try:
                    report = run_smoke_test(web_app_url)
                    save_with_date(save_smoke_test_report, report)
                    if report["overall_status"] == "PASS":
                        st.success("Smoke test passed.")
                    else:
                        st.error("Smoke test failed.")
                    st.metric("Overall Status", report["overall_status"])
                    if report["title"]:
                        st.write(f"Page title: {report['title']}")
                    st.dataframe(report["checks"], use_container_width=True)
                    if report["warnings"]:
                        st.warning("\n".join(report["warnings"]))
                    st.json(report["sample_results"])
                except ValueError as exc:
                    st.error(str(exc))

with tabs[5]:
    st.header("Bulk URL Failure and Redirect Check")
    with st.form("bulk_url_form"):
        raw_urls = st.text_area(
            "URLs",
            placeholder="www.example.com\nhttps://www.example.com/login\nhttps://www.example.com/missing-page",
            height=180,
        )
        submitted = st.form_submit_button("Check URLs")

    if submitted:
        urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
        if not urls:
            st.warning("At least one URL is required.")
        else:
            with st.spinner("Checking URLs..."):
                report = check_bulk_urls(urls)
                save_with_date(save_bulk_url_report, report)
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total", report["total_urls"])
                col2.metric("Passed", report["passed"])
                col3.metric("Failed", report["failed"])
                col4.metric("Redirected", report["redirected"])

                failed = [item for item in report["results"] if item["result"] == "FAIL"]
                redirected = [item for item in report["results"] if item["redirected"]]
                render_result_table("Failed URLs", failed)
                render_result_table("Redirected URLs", redirected)
                render_result_table("All Results", report["results"])

with tabs[6]:
    st.header("Prompt-Based Web Page Check")
    with st.form("prompt_web_check_form"):
        user_prompt = st.text_area(
            "Prompt",
            placeholder='Verify the build landing page for GLB on mbusa.com',
            height=120,
        )
        submitted = st.form_submit_button("Run Prompt Check")

    if submitted:
        if not user_prompt.strip():
            st.warning("Prompt is required.")
        else:
            with st.spinner("Finding and checking the best matching page..."):
                try:
                    report = run_prompt_web_check(user_prompt)
                    save_with_date(save_prompt_web_check_report, report)
                    if report["overall_status"] == "PASS":
                        st.success("Prompt check passed.")
                    else:
                        st.error("Prompt check needs review.")
                    st.subheader("Summary")
                    st.text_area("QA Result", report["summary"], height=220)
                    if report["best_match"]:
                        best_match = report["best_match"]
                        st.subheader("Best Matching Page")
                        st.write(f"URL: {best_match['final_url']}")
                        st.write(f"Title: {best_match['title']}")
                        st.write(f"Matched keywords: {', '.join(best_match['matched_keywords'])}")
                        st.dataframe(best_match["checks"], use_container_width=True)
                        st.json(best_match["sample_results"])
                    render_result_table("Checked Candidate Pages", report["checked_pages"])
                except ValueError as exc:
                    st.error(str(exc))
                except (errors.ClientError, errors.ServerError) as exc:
                    handle_ai_error(exc)

with tabs[7]:
    st.header("Cross Browser Web App Test")
    with st.form("cross_browser_form"):
        web_app_url = st.text_input("Web App URL", placeholder="www.example.com")
        browser_options = st.multiselect(
            "Browsers",
            ["chromium", "firefox", "webkit"],
            default=["chromium", "firefox", "webkit"],
        )
        submitted = st.form_submit_button("Run Cross Browser Test")

    if submitted:
        if not web_app_url.strip():
            st.warning("Web app URL is required.")
        elif not browser_options:
            st.warning("Select at least one browser.")
        else:
            with st.spinner("Running browser checks..."):
                try:
                    report = run_cross_browser_test(web_app_url, browser_options)
                    save_with_date(save_cross_browser_report, report)
                    if report["overall_status"] == "PASS":
                        st.success("Cross browser test passed.")
                    elif report["overall_status"] == "SETUP_REQUIRED":
                        st.warning(report["setup_message"])
                    else:
                        st.error("Cross browser test failed.")
                    st.metric("Overall Status", report["overall_status"])
                    render_result_table("Browser Results", report["results"])
                    for result in report["results"]:
                        with st.expander(f"{result['browser']} details"):
                            st.json(result)
                except ValueError as exc:
                    st.error(str(exc))

with tabs[8]:
    st.header("Verify Expected Content on UI")
    st.caption("Compare a UI page against a Confluence page, another web page, or pasted expected content.")
    with st.form("content_verification_form"):
        ui_url = st.text_input("UI Page URL", placeholder="https://www.example.com/page")
        expected_source_url = st.text_input(
            "Expected Content Page URL",
            placeholder="https://your-domain.atlassian.net/wiki/spaces/...",
        )
        expected_text = st.text_area(
            "Expected Content",
            placeholder="Paste expected headings, labels, copy, disclaimers, CTA text, or acceptance content here if the source page needs login.",
            height=180,
        )
        submitted = st.form_submit_button("Verify Content")

    if submitted:
        if not ui_url.strip():
            st.warning("UI page URL is required.")
        elif not expected_source_url.strip() and not expected_text.strip():
            st.warning("Provide an expected content page URL or paste expected content.")
        else:
            with st.spinner("Comparing expected content with UI page..."):
                try:
                    report = verify_content_reflection(ui_url, expected_source_url, expected_text)
                    save_with_date(save_content_verification_report, report)
                    if report["overall_status"] == "PASS":
                        st.success("Expected content is reflected on the UI.")
                    else:
                        st.error("Some expected content is missing or only partially reflected.")

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Reflected", f"{report['reflected_percent']}%")
                    col2.metric("Matched", report["matched_count"])
                    col3.metric("Partial", report["partial_count"])
                    col4.metric("Missing", report["missing_count"])

                    st.subheader("UI Page")
                    st.write(f"URL: {report['ui_page']['final_url']}")
                    st.write(f"Title: {report['ui_page']['title']}")

                    missing = [item for item in report["comparisons"] if item["status"] == "MISSING"]
                    partial = [item for item in report["comparisons"] if item["status"] == "PARTIAL"]
                    matched = [item for item in report["comparisons"] if item["status"] == "MATCHED"]
                    render_result_table("Missing Content", missing)
                    render_result_table("Partially Reflected Content", partial)
                    render_result_table("Matched Content", matched)
                except (ValueError, OSError) as exc:
                    st.error(str(exc))

with tabs[9]:
    st.header("Saved Reports")
    render_history("Recent stand-ups", MEMORY_FILE)
    render_history("Recent bug reports", BUGS_FILE)
    render_history("Recent test cases", TEST_CASES_FILE)
    render_history("Recent broken link reports", BROKEN_LINKS_FILE)
    render_history("Recent smoke tests", SMOKE_TESTS_FILE)
    render_history("Recent bulk URL reports", BULK_URL_REPORTS_FILE)
    render_history("Recent prompt web checks", PROMPT_WEB_CHECKS_FILE)
    render_history("Recent cross browser tests", CROSS_BROWSER_REPORTS_FILE)
    render_history("Recent content verification reports", CONTENT_VERIFICATION_REPORTS_FILE)
