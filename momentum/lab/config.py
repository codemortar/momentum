"""Paper-lab settings. Costs are deliberately pessimistic."""

from ..config import STATE_DIR

JOURNAL = STATE_DIR / "lab_journal.jsonl"

UNDERLYING = "SPY"
START_CAPITAL = 100_000.0         # per method; one cash-secured SPY put needs ~$70k
EQUITY_SLIPPAGE_BPS = 2.0         # per side, on a very liquid ETF
OPTION_COMMISSION = 0.65          # per contract, IBKR-like
OPTION_MULTIPLIER = 100

ASSESSMENT_END = "2027-01-01"
