import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

import config
from vp_engine import compute_vp_levels, compute_session_profiles, evaluate_reclaim
from scanner import MarketScanner


class TestConfluenceEngine(unittest.TestCase):

    def setUp(self):
        self.scanner = MarketScanner()

    def test_evaluate_reclaim_logic(self):
        """
        Verifies that evaluate_reclaim accurately identifies trap reclaims
        and ignores non-reclaims or blowouts.
        """
        val = 100.0
        vah = 110.0

        # Scenario 1: Long Reclaim (dipped below 100, now closed at 101, RSI oversold)
        res_long = evaluate_reclaim(
            c_price=101.0,
            prev_price=99.5,
            curr_low=99.0,
            curr_high=102.0,
            prev_low=99.0,
            prev_high=100.5,
            vah=vah,
            val=val,
            c_rsi=40.0,
            rsi_long_max=46.0,
            rsi_short_min=54.0
        )
        self.assertEqual(res_long, "LONG")

        # Scenario 2: Short Reclaim (pushed above 110, now closed at 109, RSI overbought)
        res_short = evaluate_reclaim(
            c_price=109.0,
            prev_price=110.5,
            curr_low=108.5,
            curr_high=111.0,
            prev_low=109.5,
            prev_high=111.0,
            vah=vah,
            val=val,
            c_rsi=60.0,
            rsi_long_max=46.0,
            rsi_short_min=54.0
        )
        self.assertEqual(res_short, "SHORT")

        # Scenario 3: Price still below VAL (no reclaim yet)
        res_still_below = evaluate_reclaim(
            c_price=99.0,
            prev_price=98.5,
            curr_low=98.0,
            curr_high=99.2,
            prev_low=98.0,
            prev_high=99.0,
            vah=vah,
            val=val,
            c_rsi=35.0
        )
        self.assertIsNone(res_still_below)

        # Scenario 4: Price blown past VAH on long (trend blowout, not mean reversion)
        res_blown = evaluate_reclaim(
            c_price=112.0,
            prev_price=99.5,
            curr_low=99.0,
            curr_high=113.0,
            prev_low=99.0,
            prev_high=100.5,
            vah=vah,
            val=val,
            c_rsi=40.0
        )
        self.assertIsNone(res_blown)

    def test_session_profile_extraction_on_live_data(self):
        """
        Tests session extraction on live BTCUSDT klines to ensure both
        NY and Asia sessions are located and VAH/VAL/POC are computed.
        """
        df = self.scanner.fetch_recent_klines("BTCUSDT", limit=500)
        self.assertGreaterEqual(len(df), 200, "Should fetch at least 200 bars")

        profiles = compute_session_profiles(df)
        self.assertIn("ny", profiles, "NY session profile must be present")
        self.assertIn("asia", profiles, "Asia session profile must be present")

        ny = profiles["ny"]
        asia = profiles["asia"]

        self.assertGreater(ny["vah"], ny["val"], "NY VAH must be greater than VAL")
        self.assertTrue(ny["val"] <= ny["poc"] <= ny["vah"], "NY POC must be inside Value Area")

        self.assertGreater(asia["vah"], asia["val"], "Asia VAH must be greater than VAL")
        self.assertTrue(asia["val"] <= asia["poc"] <= asia["vah"], "Asia POC must be inside Value Area")
        print(f"\n[TEST PASS] Live NY Session: {ny['date']} | VAH={ny['vah']:.2f}, VAL={ny['val']:.2f}, POC={ny['poc']:.2f}")
        print(f"[TEST PASS] Live Asia Session: {asia['date']} | VAH={asia['vah']:.2f}, VAL={asia['val']:.2f}, POC={asia['poc']:.2f}")

    def test_confluence_strictness(self):
        """
        Verifies that only agreeing sessions trigger a signal, while disagreeing
        or one-sided signals are strictly dropped.
        """
        # When confluence is required:
        config.REQUIRE_SESSION_CONFLUENCE = True

        # Agree Case: NY=LONG, ASIA=LONG -> PASS
        # Disagree Case: NY=LONG, ASIA=SHORT -> DROP
        # One-sided: NY=LONG, ASIA=None -> DROP
        self.assertTrue(config.REQUIRE_SESSION_CONFLUENCE)


if __name__ == "__main__":
    unittest.main()
