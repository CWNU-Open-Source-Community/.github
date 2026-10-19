from datetime import date
import unittest
from unittest.mock import patch

from scripts.community_metrics import repository_activity, streaks, wakatime


class CommunityMetricsTests(unittest.TestCase):
    def test_wakatime_stale_zero_is_processing_instead_of_recorded_zero(self):
        with patch("scripts.community_metrics.request", return_value=(200, {"data": {"is_up_to_date": False, "total_seconds": 0}})):
            self.assertEqual(wakatime("test-token")["state"], "processing")

    def test_streak_keeps_yesterday_when_today_has_no_commits(self):
        days = {"2026-10-16": 2, "2026-10-17": 3, "2026-10-18": 1}
        current, best, highest, average = streaks(days, date(2026, 10, 19), date(2026, 10, 15))
        self.assertEqual((current, best, highest), (3, 3, 3))
        self.assertAlmostEqual(average, 6 / 5)

    def test_streak_stops_at_a_gap_and_ignores_outside_the_window(self):
        days = {"2026-10-14": 500, "2026-10-15": 1, "2026-10-16": 2, "2026-10-19": 1}
        current, best, highest, average = streaks(days, date(2026, 10, 19), date(2026, 10, 15))
        self.assertEqual((current, best, highest), (1, 2, 2))
        self.assertAlmostEqual(average, 4 / 5)

    def test_reviews_and_comments_include_later_pages_without_duplicating_totals(self):
        class FakeGitHub:
            def graphql(self, query, **variables):
                if "isPrivate" in query:
                    return {"repository": {"isPrivate": False, "defaultBranchRef": None}}
                field = "pullRequests" if "pullRequests(" in query else "issues"
                second_page = variables["cursor"] is not None
                nodes = [{"comments": {"totalCount": 3 if second_page else 2}}]
                if field == "pullRequests":
                    nodes[0]["reviews"] = {"totalCount": 2 if second_page else 0}
                return {"repository": {field: {
                    "totalCount": 2, "nodes": nodes,
                    "pageInfo": {"hasNextPage": not second_page, "endCursor": "next"},
                }}}
        result = repository_activity(FakeGitHub(), {"full_name": "community/project"}, "2026-04-20T00:00:00+08:00")
        self.assertEqual(result["activity"], {"commits": 0, "issues": 2, "prs": 2, "reviewed": 1, "comments": 10})
        self.assertEqual(result["days"], {})


if __name__ == "__main__":
    unittest.main()
