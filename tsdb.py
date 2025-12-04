from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from influxdb_client_3 import InfluxDBClient3, Point, WritePrecision


@dataclass
class PositionPoint:
    device_id: str
    device_name: str
    x: float
    y: float
    timestamp: datetime


class TimeSeriesDB(ABC):
    @abstractmethod
    async def write_position(self, point: PositionPoint) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class NoopTimeSeriesDB(TimeSeriesDB):
    async def write_position(self, point: PositionPoint) -> None:
        pass


class InfluxTimeSeriesDB(TimeSeriesDB):
    def __init__(self, host: str, port: int, database: str, token: str | None = None):
        self._client = InfluxDBClient3(
            host=f"http://{host}:{port}",
            database=database,
            token=token,
        )
        self._database = database

    async def write_position(self, point: PositionPoint) -> None:
        p = (
            Point("cat_position")
            .tag("device_id", point.device_id)
            .tag("device_name", point.device_name)
            .field("x", float(point.x))
            .field("y", float(point.y))
            .time(point.timestamp, WritePrecision.NS)
        )
        self._client.write(record=p)

    async def aclose(self) -> None:
        self._client.close()


def create_tsdb_from_env() -> TimeSeriesDB:
    tsdb_type = os.getenv("TSDB_TYPE", "").lower()

    if tsdb_type == "influx":
        host = os.environ["TSDB_HOST"]
        port = int(os.environ.get("TSDB_PORT", "8181"))
        database = os.environ["TSDB_DATABASE"]
        token = os.environ.get("TSDB_TOKEN")
        return InfluxTimeSeriesDB(host=host, port=port, database=database, token=token)

    return NoopTimeSeriesDB()
