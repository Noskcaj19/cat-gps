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


@dataclass
class HeatmapBin:
    grid_x: int
    grid_y: int
    count: int


class TimeSeriesDB(ABC):
    @abstractmethod
    async def write_position(self, point: PositionPoint) -> None:
        raise NotImplementedError

    @abstractmethod
    async def query_positions(self, hours: int = 24) -> list[PositionPoint]:
        raise NotImplementedError

    @abstractmethod
    async def query_heatmap(
        self, hours: int = 24, cell_size: float = 0.5, device_id: str | None = None
    ) -> list[HeatmapBin]:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class NoopTimeSeriesDB(TimeSeriesDB):
    async def write_position(self, point: PositionPoint) -> None:
        pass

    async def query_positions(self, hours: int = 24) -> list[PositionPoint]:
        return []

    async def query_heatmap(
        self, hours: int = 24, cell_size: float = 0.5, device_id: str | None = None
    ) -> list[HeatmapBin]:
        return []


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

    async def query_positions(self, hours: int = 24) -> list[PositionPoint]:
        query = f"""
            SELECT time, device_id, device_name, x, y
            FROM cat_position
            WHERE time >= now() - interval '{hours} hours'
        """
        table = self._client.query(query=query, language="sql")
        results = []
        for batch in table.to_batches():
            for i in range(batch.num_rows):
                results.append(PositionPoint(
                    device_id=str(batch.column("device_id")[i]),
                    device_name=str(batch.column("device_name")[i]),
                    x=float(batch.column("x")[i].as_py()),
                    y=float(batch.column("y")[i].as_py()),
                    timestamp=batch.column("time")[i].as_py(),
                ))
        return results

    async def query_heatmap(
        self, hours: int = 24, cell_size: float = 0.5, device_id: str | None = None
    ) -> list[HeatmapBin]:
        device_filter = f"AND device_id = '{device_id}'" if device_id else ""
        query = f"""
            SELECT
                CAST(FLOOR(x / {cell_size}) AS INT) AS grid_x,
                CAST(FLOOR(y / {cell_size}) AS INT) AS grid_y,
                COUNT(*) AS count
            FROM cat_position
            WHERE time >= now() - interval '{hours} hours'
            {device_filter}
            GROUP BY
                CAST(FLOOR(x / {cell_size}) AS INT),
                CAST(FLOOR(y / {cell_size}) AS INT)
        """
        table = self._client.query(query=query, language="sql")
        results = []
        for batch in table.to_batches():
            for i in range(batch.num_rows):
                results.append(HeatmapBin(
                    grid_x=int(batch.column("grid_x")[i].as_py()),
                    grid_y=int(batch.column("grid_y")[i].as_py()),
                    count=int(batch.column("count")[i].as_py()),
                ))
        return results

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

    
    print("Failed to select TSDB type")
    return NoopTimeSeriesDB()
