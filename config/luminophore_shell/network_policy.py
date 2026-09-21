from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


NetworkPurpose = Literal["weather", "city_search", "google_calendar"]


class OfflineError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfflinePolicy:
    offline: bool = False

    def require(self, purpose: NetworkPurpose) -> None:
        if self.offline:
            raise OfflineError(f"offline:{purpose}")

    def allows(self, purpose: NetworkPurpose) -> bool:
        try:
            self.require(purpose)
        except OfflineError:
            return False
        return True
