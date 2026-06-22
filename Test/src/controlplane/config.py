from dataclasses import dataclass


@dataclass
class ControlPlaneConfig:
    temporal_host: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "control-plane"
