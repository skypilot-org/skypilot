"""Helpers for driving a dashboard page from a test."""

from playwright.sync_api import Page


def stop_auto_refresh(page: Page) -> None:
    """Clear every timer the page has running, e.g. the tables' periodic
    refresh.

    Call it once the page has loaded and is idle, before an action whose
    requests a test counts (typing in a filter): a periodic refresh that
    happens to fire meanwhile is not a refetch caused by the action. Timers
    the page creates afterwards (a debounce, a refetch per keystroke) still
    run.
    """
    page.evaluate('''() => {
        const last = setTimeout(() => {}, 0);
        for (let id = 1; id <= last; id++) {
            clearTimeout(id);
            clearInterval(id);
        }
    }''')
