"""
Token budget ledger for noema.

All arms of an experiment draw from a single shared token pool so that runs are
compared at equal spend. Accounting is split per account ("mutation" vs
"coordination") so ablations can report where the tokens went, and optional
per-account caps allow bounding one side independently.

Enforcement model (see PLAN.md section 3.2): ``charge()`` never raises — tokens
reported by the API have already been spent, and discarding a paid-for response
would waste budget. Instead callers run ``ensure(account)`` *before* each request;
it raises ``BudgetExhausted`` once the pool (or the account's cap) is used up. The
response that crosses the cap is therefore still used, and the next call stops the
run cleanly.
"""

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

MUTATION_ACCOUNT = "mutation"
COORDINATION_ACCOUNT = "coordination"


class BudgetExhausted(Exception):
    """Raised on pre-flight check when the token budget is used up"""

    def __init__(self, account: str, spent: int, cap: int):
        self.account = account
        self.spent = spent
        self.cap = cap
        super().__init__(f"Budget exhausted for account '{account}': spent {spent} of cap {cap}")


@dataclass
class CallRecord:
    """One metered LLM call (a single logical call; retries are folded into `attempts`)"""

    account: str  # e.g. "mutation" | "coordination"
    tag: str  # e.g. "mutate", "hifo.extract_insights"
    model: str
    prompt_tokens: int
    completion_tokens: int
    attempts: int = 1  # billed requests, including failed retries
    latency_s: float = 0.0
    iteration: int = -1
    timestamp: float = field(default_factory=time.time)
    estimated: bool = False  # True if token counts are a counted estimate, not server-reported usage
    finish_reason: str = ""  # "stop" | "length" | "content_filter" | "" (local servers)
    reasoning_tokens: int = 0  # thinking tokens (DeepSeek R1, o1, etc.)
    cost: float = 0.0  # dollar cost (OpenRouter x-openrouter-cost header or 0.0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenLedger:
    """
    Shared token pool with per-account accounting.

    Args:
        total_budget_tokens: Hard cap on prompt+completion tokens across all accounts.
        account_caps: Optional per-account sub-caps (an account may also be capped
            below the shared pool, e.g. to bound coordination overhead).
        log_path: Optional JSONL file; every CallRecord is appended as one line.
    """

    def __init__(
        self,
        total_budget_tokens: int,
        account_caps: Optional[Dict[str, int]] = None,
        log_path: Optional[str] = None,
    ):
        if total_budget_tokens <= 0:
            raise ValueError("total_budget_tokens must be positive")
        self.total_budget_tokens = total_budget_tokens
        self.account_caps: Dict[str, int] = dict(account_caps or {})
        self.log_path = log_path
        self._records: List[CallRecord] = []
        self._spent_by_account: Dict[str, int] = {}
        self._lock = threading.Lock()

    def spent(self, account: Optional[str] = None) -> int:
        """Tokens spent by one account, or in total if account is None"""
        with self._lock:
            if account is None:
                return sum(self._spent_by_account.values())
            return self._spent_by_account.get(account, 0)

    def remaining(self, account: Optional[str] = None) -> int:
        """
        Tokens still available to an account: the shared pool remainder, further
        limited by the account's own cap if one is set. May be negative after the
        call that crosses the cap.
        """
        pool_left = self.total_budget_tokens - self.spent()
        if account is None or account not in self.account_caps:
            return pool_left
        account_left = self.account_caps[account] - self.spent(account)
        return min(pool_left, account_left)

    def ensure(self, account: str) -> None:
        """Pre-flight check: raise BudgetExhausted if `account` has no budget left"""
        if self.remaining(account) <= 0:
            if (
                account in self.account_caps
                and self.account_caps[account] - self.spent(account) <= 0
            ):
                raise BudgetExhausted(account, self.spent(account), self.account_caps[account])
            raise BudgetExhausted(account, self.spent(), self.total_budget_tokens)

    def charge(self, record: CallRecord) -> int:
        """
        Record a completed call. Never raises — the tokens are already spent.

        Returns the account's remaining budget (may be negative once the cap has
        been crossed; the next ensure() will then raise).
        """
        with self._lock:
            self._records.append(record)
            self._spent_by_account[record.account] = (
                self._spent_by_account.get(record.account, 0) + record.total_tokens
            )
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(record)) + "\n")
        return self.remaining(record.account)

    @property
    def records(self) -> List[CallRecord]:
        with self._lock:
            return list(self._records)

    def snapshot(self) -> Dict[str, Any]:
        """JSON-serializable state for checkpoints and the run log"""
        with self._lock:
            return {
                "total_budget_tokens": self.total_budget_tokens,
                "account_caps": dict(self.account_caps),
                "spent_by_account": dict(self._spent_by_account),
                "num_calls": len(self._records),
                "records": [asdict(r) for r in self._records],
            }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        """Restore accounting state from a snapshot() dict (for checkpoint resume)"""
        with self._lock:
            self.total_budget_tokens = snapshot["total_budget_tokens"]
            self.account_caps = dict(snapshot.get("account_caps", {}))
            self._records = [CallRecord(**r) for r in snapshot.get("records", [])]
            self._spent_by_account = dict(snapshot["spent_by_account"])
