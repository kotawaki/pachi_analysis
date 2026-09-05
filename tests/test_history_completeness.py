import unittest

from tools.pscube_cdp_capture_common import extract_history


def _row(bonus_id):
    return [str(bonus_id), "10:00", "1", "継続"]


class _Button:
    def __init__(self, page):
        self.page = page

    def click(self, timeout=5000):
        self.page.clicks += 1


class _Locator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def click(self, timeout=5000):
        return _Button(self.page).click(timeout=timeout)


class _Page:
    def __init__(self, initial_rows, expanded_rows=None, more=True, more_after_click=None, timeout=False):
        self.initial_rows = initial_rows
        self.expanded_rows = expanded_rows if expanded_rows is not None else initial_rows
        self.more = more
        self.more_after_click = more if more_after_click is None else more_after_click
        self.timeout = timeout
        self.clicks = 0

    def evaluate(self, _script):
        expanded = self.clicks > 0
        rows = self.expanded_rows if expanded else self.initial_rows
        return {
            "rows": rows,
            "moreExists": self.more_after_click if expanded else self.more,
            "moreVisible": self.more_after_click if expanded else self.more,
            "moreDisabled": False,
        }

    def locator(self, _selector):
        return _Locator(self)

    def wait_for_function(self, _script, arg=None, timeout=None):
        if self.timeout:
            raise TimeoutError("wait timeout")


class HistoryCompletenessTest(unittest.TestCase):
    def test_source_gap_is_warning_after_more_finishes(self):
        rows, meta = extract_history(
            _Page([_row(i) for i in range(1, 21)], [_row(i) for i in range(1, 7)] + [_row(i) for i in range(8, 33)], more=True, more_after_click=False),
            expand_more=True,
        )
        self.assertEqual(len(rows), 31)
        self.assertTrue(meta["history_complete"])
        self.assertTrue(meta["history_sequence_warning"])
        self.assertEqual(meta["history_missing_bonus_ids"], [7])

    def test_contiguous_history_remains_complete(self):
        _rows, meta = extract_history(_Page([_row(i) for i in range(1, 6)], more=False), expand_more=True)
        self.assertTrue(meta["history_complete"])
        self.assertFalse(meta["history_sequence_warning"])

    def test_visible_more_button_without_progress_is_incomplete(self):
        page = _Page([_row(i) for i in range(1, 6)], more=True)
        _rows, meta = extract_history(page, expand_more=True)
        self.assertFalse(meta["history_complete"])
        self.assertIn("did not increase", meta["history_error"])

    def test_wait_timeout_is_incomplete(self):
        page = _Page([_row(i) for i in range(1, 6)], [_row(i) for i in range(1, 7)], more=True, timeout=True)
        _rows, meta = extract_history(page, expand_more=True)
        self.assertFalse(meta["history_complete"])
        self.assertFalse(meta["history_sequence_warning"])

    def test_dom_parse_loss_is_incomplete(self):
        invalid = ["7", "not-a-time", "1", "継続"]
        page = _Page([_row(i) for i in range(1, 7)] + [invalid], more=False)
        _rows, meta = extract_history(page, expand_more=True)
        self.assertFalse(meta["history_complete"])
        self.assertEqual(meta["history_parse_errors"], 1)


if __name__ == "__main__":
    unittest.main()
