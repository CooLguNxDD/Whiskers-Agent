"""Boot phase registry for dynamic phase registration."""
import logging
from typing import List, Callable, Awaitable
from core.bootstrap.types import Phase, BootContext

logger = logging.getLogger("whiskers")

class BootPhaseRegistry:
    """A registry that allows dynamic contribution of initialization phases."""
    def __init__(self):
        self._phases: List[Phase] = []

    def register(self, phase: Phase, index: int | None = None):
        """Register a phase. If index is None, append to the end."""
        if index is not None:
            self._phases.insert(index, phase)
        else:
            self._phases.append(phase)

    def get_phases(self) -> List[Phase]:
        """Return the current sequence of phases."""
        return list(self._phases)

    def set_phases(self, phases: List[Phase]):
        """Overwrite the current sequence with a new list."""
        self._phases = list(phases)

    async def run_pipeline(self, ctx: BootContext) -> None:
        """Run all startup phases sequentially, logging progress and handling fatal errors."""
        for phase in self._phases:
            logger.info("-" * 60)
            logger.info("Executing: %s", phase.name)
            logger.info("-" * 60)
            try:
                await phase.run(ctx)
            except Exception as exc:
                if phase.fatal:
                    logger.critical("Fatal error in phase '%s': %s", phase.name, exc, exc_info=True)
                    raise exc
                else:
                    logger.error("Non-fatal error in phase '%s': %s", phase.name, exc, exc_info=True)
