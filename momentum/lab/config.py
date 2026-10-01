"""Paper-lab settings. Costs are deliberately pessimistic."""

from ..config import STATE_DIR

JOURNAL = STATE_DIR / "lab_journal.jsonl"
SNAPSHOT_DIR = STATE_DIR / "lab_snapshots"

UNDERLYING = "SPY"
FTSE = "ISF.L"                    # iShares Core FTSE 100, ISA-eligible
START_CAPITAL = 100_000.0         # per method, in the instrument's currency (USD/GBP)
PILOT_CAPITAL = 1_300.0           # ~£1,000 in USD: methods sized to the real-money pilot

# Per-side slippage; London ETF spreads are wider than SPY's. Unknown symbols
# get the pessimistic default.
SLIPPAGE_BPS = {UNDERLYING: 2.0, FTSE: 8.0}
DEFAULT_SLIPPAGE_BPS = 10.0
OPTION_COMMISSION = 0.65          # per contract, IBKR-like
OPTION_MULTIPLIER = 100

ASSESSMENT_END = "2027-01-01"
