import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_config():
    from config import Config, Device, Floor, MQTTConfig, Node, Point2D, Point3D, Room

    return Config(
        mqtt=MQTTConfig(host="localhost", port=1883, username="", password=""),
        devices=[Device(id="cat1", name="Mittens")],
        floors=[
            Floor(
                id="floor1",
                name="Main Floor",
                bounds=(Point3D(0, 0, 0), Point3D(10, 10, 3)),
                rooms=[
                    Room(
                        name="Living Room",
                        points=[Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5)],
                    )
                ],
            )
        ],
        nodes=[Node(name="node1", point=Point3D(2.5, 2.5, 1), floors=["floor1"])],
    )


@pytest.fixture
def client(mock_config):
    import main

    original_queue = main.position_queue

    with (
        patch("main.config", mock_config),
        patch("main.device_by_id", {d.id: d for d in mock_config.devices}),
        patch("main.mqtt_client.Client") as mock_mqtt,
        patch("main.create_tsdb_from_env", return_value=None),
    ):
        mock_mqtt.return_value = MagicMock()
        main.position_queue = asyncio.Queue()

        with TestClient(main.app) as test_client:
            yield test_client

        main.position_queue = original_queue


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_map_page_returns_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_map_page_contains_room(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Living Room" in response.text


def test_websocket_positions(client):
    with client.websocket_connect("/ws/positions") as websocket:
        pass


def test_heatmap_endpoint_no_tsdb(client):
    response = client.get("/api/heatmap")
    assert response.status_code == 200
    assert response.json() == {"bins": [], "cell_size": 0.5}


def test_heatmap_endpoint_with_data(mock_config):
    import main
    from tsdb import HeatmapBin, NoopTimeSeriesDB

    class MockTSDB(NoopTimeSeriesDB):
        async def query_heatmap(
            self, hours: int = 24, cell_size: float = 0.5, device_id: str | None = None
        ) -> list[HeatmapBin]:
            return [
                HeatmapBin(grid_x=5, grid_y=6, count=10),
                HeatmapBin(grid_x=5, grid_y=7, count=5),
            ]

    mock_tsdb = MockTSDB()
    original_queue = main.position_queue

    with (
        patch("main.config", mock_config),
        patch("main.device_by_id", {d.id: d for d in mock_config.devices}),
        patch("main.mqtt_client.Client") as mock_mqtt,
        patch("main.create_tsdb_from_env", return_value=mock_tsdb),
    ):
        mock_mqtt.return_value = MagicMock()
        main.position_queue = asyncio.Queue()

        with TestClient(main.app) as test_client:
            response = test_client.get("/api/heatmap")
            assert response.status_code == 200
            data = response.json()
            assert len(data["bins"]) == 2
            assert data["bins"][0]["grid_x"] == 5
            assert data["bins"][0]["grid_y"] == 6
            assert data["bins"][0]["count"] == 10
            assert data["cell_size"] == 0.5

        main.position_queue = original_queue


def test_map_page_contains_mode_selector(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "mode-realtime" in response.text
    assert "mode-heatmap" in response.text
