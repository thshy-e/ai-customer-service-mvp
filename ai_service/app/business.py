from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class TimeRange(BaseModel):
    start: str
    end: str


class BusinessConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    schedule: dict[str, list[TimeRange]] = Field(default_factory=dict)
    closed_dates: list[date] = Field(default_factory=list)
    open_message: str
    closed_message: str


class BusinessStatus(BaseModel):
    is_open: bool
    timezone: str
    message: str
    next_open_at: datetime | None = None


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


class BusinessHours:
    def __init__(self, config: BusinessConfig) -> None:
        self.config = config
        self.timezone = ZoneInfo(config.timezone)

    @classmethod
    def from_file(cls, path: Path) -> "BusinessHours":
        with path.open(encoding="utf-8") as file:
            return cls(BusinessConfig.model_validate(yaml.safe_load(file)))

    def status(self, at: datetime | None = None) -> BusinessStatus:
        current = at.astimezone(self.timezone) if at else datetime.now(self.timezone)
        intervals = self._intervals_for(current.date())

        for start_at, end_at in intervals:
            if start_at <= current < end_at:
                return BusinessStatus(
                    is_open=True,
                    timezone=self.config.timezone,
                    message=f"{self.config.open_message} 今日营业至 {end_at:%H:%M}。",
                )

        next_open = self._next_open(current)
        suffix = ""
        if next_open:
            suffix = f" 下次人工在线时间：{WEEKDAYS_ZH[next_open.weekday()]} {next_open:%m月%d日 %H:%M}。"
        return BusinessStatus(
            is_open=False,
            timezone=self.config.timezone,
            message=f"{self.config.closed_message}{suffix}",
            next_open_at=next_open,
        )

    def _intervals_for(self, day: date) -> list[tuple[datetime, datetime]]:
        if day in self.config.closed_dates:
            return []
        ranges = self.config.schedule.get(WEEKDAYS[day.weekday()], [])
        return [
            (
                datetime.combine(day, _parse_time(item.start), self.timezone),
                datetime.combine(day, _parse_time(item.end), self.timezone),
            )
            for item in ranges
        ]

    def _next_open(self, current: datetime) -> datetime | None:
        for offset in range(15):
            day = current.date() + timedelta(days=offset)
            for start_at, _ in self._intervals_for(day):
                if start_at > current:
                    return start_at
        return None

