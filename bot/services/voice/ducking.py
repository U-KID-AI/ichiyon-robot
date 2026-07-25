from dataclasses import dataclass


@dataclass
class DuckingConfig:
    enabled: bool = False
    music_gain: float = 0.5
    attack_ms: int = 100
    release_ms: int = 300


class DuckingEnvelope:
    def __init__(self, config: DuckingConfig) -> None:
        self.config = config
        self.current_gain = 1.0
        self.target_gain = 1.0

    def set_tts_active(self, active: bool) -> None:
        if not self.config.enabled:
            self.target_gain = 1.0
            return
        self.target_gain = max(0.0, min(1.0, self.config.music_gain if active else 1.0))

    def reset(self) -> None:
        self.current_gain = 1.0
        self.target_gain = 1.0

    def step(self) -> float:
        if self.current_gain == self.target_gain:
            return self.current_gain
        if self.target_gain < self.current_gain and self.config.attack_ms <= 0:
            self.current_gain = self.target_gain
            return max(0.0, min(1.0, self.current_gain))
        if self.target_gain > self.current_gain and self.config.release_ms <= 0:
            self.current_gain = self.target_gain
            return max(0.0, min(1.0, self.current_gain))
        frames = 5
        if self.target_gain < self.current_gain and self.config.attack_ms > 0:
            frames = max(1, int(self.config.attack_ms / 20))
        if self.target_gain > self.current_gain and self.config.release_ms > 0:
            frames = max(1, int(self.config.release_ms / 20))
        delta = (self.target_gain - self.current_gain) / frames
        if abs(delta) < 0.001:
            self.current_gain = self.target_gain
        else:
            self.current_gain += delta
        return max(0.0, min(1.0, self.current_gain))
