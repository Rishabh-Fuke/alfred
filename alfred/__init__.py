"""Alfred: an RL environment and eval harness for private, local personal agents."""

__version__ = "0.1.0"

from .env import Environment, Observation  # noqa: E402
from .reward import compute_reward  # noqa: E402
from .tasks import TEMPLATES, Task, generate_tasks  # noqa: E402
from .world import World  # noqa: E402

__all__ = ["Environment", "Observation", "Task", "TEMPLATES", "World", "compute_reward", "generate_tasks"]
