from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml


class Point2D(NamedTuple):
    x: float
    y: float


class Point3D(NamedTuple):
    x: float
    y: float
    z: float


@dataclass
class Room:
    name: str
    points: list[Point2D]


@dataclass
class Floor:
    id: str
    name: str
    bounds: tuple[Point3D, Point3D]
    rooms: list[Room]


@dataclass
class Device:
    id: str
    name: str


@dataclass
class Node:
    name: str
    point: Point3D
    floors: list[str]


@dataclass
class MQTTConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass
class Config:
    mqtt: MQTTConfig
    devices: list[Device]
    floors: list[Floor]
    nodes: list[Node]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        if path is None:
            path = Path(__file__).parent / "config.yml"
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls._parse(data)

    @classmethod
    def _parse(cls, data: dict) -> "Config":
        mqtt = cls._parse_mqtt(data.get("mqtt_server", {}))
        devices = cls._parse_devices(data.get("devices", []))
        floors = cls._parse_floors(data.get("floors", []))
        nodes = cls._parse_nodes(data.get("nodes", []))
        return cls(mqtt=mqtt, devices=devices, floors=floors, nodes=nodes)

    @staticmethod
    def _parse_mqtt(data: dict) -> MQTTConfig:
        return MQTTConfig(
            host=data.get("host", "localhost"),
            port=data.get("port", 1883),
            username=data.get("username", ""),
            password=data.get("password", ""),
        )

    @staticmethod
    def _parse_devices(devices_data: list[dict]) -> list[Device]:
        return [Device(id=d["id"], name=d["name"]) for d in devices_data]

    @staticmethod
    def _parse_floors(floors_data: list[dict]) -> list[Floor]:
        floors = []
        for floor_data in floors_data:
            rooms = [
                Room(name=room["name"], points=[Point2D(*p) for p in room["points"]])
                for room in floor_data.get("rooms", [])
            ]
            bounds = (
                Point3D(*floor_data["bounds"][0]),
                Point3D(*floor_data["bounds"][1]),
            )
            floors.append(
                Floor(
                    id=floor_data["id"],
                    name=floor_data["name"],
                    bounds=bounds,
                    rooms=rooms,
                )
            )
        return floors

    @staticmethod
    def _parse_nodes(nodes_data: list[dict]) -> list[Node]:
        return [
            Node(
                name=n["name"],
                point=Point3D(*n["point"]),
                floors=n.get("floors", []),
            )
            for n in nodes_data
        ]
