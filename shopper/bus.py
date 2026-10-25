"""Message bus: the market agent and the plate agent talk here before the verdict is final."""
import json
from dataclasses import asdict, dataclass, field


@dataclass
class Message:
    sender: str
    to: str
    kind: str          # ask | reply | flag | draft | final | ack
    text: str
    data: dict = field(default_factory=dict)


class Bus:
    def __init__(self):
        self.messages = []

    def post(self, sender, to, kind, text, **data):
        msg = Message(sender, to, kind, text, data)
        self.messages.append(msg)
        return msg

    def inbox(self, agent, kind=None):
        return [m for m in self.messages if m.to == agent and (kind is None or m.kind == kind)]

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(m) for m in self.messages], f, indent=2, ensure_ascii=False)
