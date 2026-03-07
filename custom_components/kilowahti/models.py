"""Data models for the Kilowahti integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import IntEnum


class PriceResolution(IntEnum):
    MIN15 = 15
    HOUR = 60

    @property
    def slots_per_day(self) -> int:
        return 24 * 60 // self.value


@dataclass
class PriceSlot:
    dt_utc: datetime   # always UTC
    price_no_tax: float  # snt/kWh, excl. VAT
    rank: int           # 1 = cheapest; max 96 (15-min) or 24 (1-hour)

    def to_dict(self) -> dict:
        return {
            "dt_utc": self.dt_utc.isoformat(),
            "price_no_tax": self.price_no_tax,
            "rank": self.rank,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PriceSlot":
        dt = datetime.fromisoformat(data["dt_utc"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return cls(
            dt_utc=dt.astimezone(timezone.utc),
            price_no_tax=data["price_no_tax"],
            rank=data["rank"],
        )


@dataclass
class TransferTier:
    label: str
    price: float        # snt/kWh, consistent with VAT toggle
    months: list[int]   # 1–12
    weekdays: list[int] # 0=Mon … 6=Sun
    hour_start: int     # 0–23
    hour_end: int       # 1–24  (exclusive upper bound)
    priority: int       # lower = evaluated first; first match wins

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "price": self.price,
            "months": self.months,
            "weekdays": self.weekdays,
            "hour_start": self.hour_start,
            "hour_end": self.hour_end,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransferTier":
        return cls(**data)

    def matches(self, month: int, weekday: int, hour: int) -> bool:
        return (
            month in self.months
            and weekday in self.weekdays
            and self.hour_start <= hour < self.hour_end
        )


@dataclass
class TransferGroup:
    id: str
    label: str
    active: bool
    tiers: list[TransferTier] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "active": self.active,
            "tiers": [t.to_dict() for t in self.tiers],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransferGroup":
        return cls(
            id=data["id"],
            label=data["label"],
            active=data["active"],
            tiers=[TransferTier.from_dict(t) for t in data.get("tiers", [])],
        )

    def price_at(self, month: int, weekday: int, hour: int) -> float | None:
        """Return price for the first matching tier, or None if no match."""
        for tier in sorted(self.tiers, key=lambda t: t.priority):
            if tier.matches(month, weekday, hour):
                return tier.price
        return None


@dataclass
class FixedPeriod:
    id: str             # uuid4
    label: str
    start_date: date
    end_date: date      # inclusive
    price: float        # snt/kWh, gross (VAT included)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "price": self.price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FixedPeriod":
        return cls(
            id=data["id"],
            label=data["label"],
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            price=data["price"],
        )

    def is_active_on(self, d: date) -> bool:
        return self.start_date <= d <= self.end_date


@dataclass
class ScoreProfile:
    id: str
    label: str
    meters: list[str] = field(default_factory=list)  # HA entity IDs

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "meters": self.meters}

    @classmethod
    def from_dict(cls, data: dict) -> "ScoreProfile":
        return cls(
            id=data["id"],
            label=data["label"],
            meters=data.get("meters", []),
        )
