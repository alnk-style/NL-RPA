from __future__ import annotations

import asyncio
import logging
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from desktop_agent.client import AgentWebSocketClient
from desktop_agent.config import AgentConfig
from desktop_agent.executor import TaskExecutor
from runtime_guard import watch_parent_process

logger = logging.getLogger(__name__)

INITIAL_RECONNECT_DELAY_SECONDS = 1
MAX_RECONNECT_DELAY_SECONDS = 60
AGENT_ERROR_LOG = Path(tempfile.gettempdir()) / "nl-rpa-agent.log"


def _write_agent_failure(exc: BaseException) -> None:
    try:
        with AGENT_ERROR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{datetime.now().isoformat()}] "
                f"{type(exc).__name__}: {exc}\n"
            )
            handle.write(traceback.format_exc())
            handle.write("\n")
    except OSError:
        pass


async def main() -> None:
    watch_parent_process()
    reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
    while True:
        client = None
        try:
            config = AgentConfig.load()
            if not config.token:
                raise RuntimeError(
                    "Desktop Agent is not paired. "
                    "Set NL_RPA_AGENT_PAIRING_CODE or configure "
                    "the Agent from the desktop settings page.",
                )

            client = AgentWebSocketClient(config)
            executor = TaskExecutor(config, client)
            client.executor = executor
            await client.connect()
            await client.run()
            reconnect_delay = INITIAL_RECONNECT_DELAY_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Desktop Agent connection lost; reconnecting",
                exc_info=True,
            )
            _write_agent_failure(exc)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(
                        "Failed to close Desktop Agent connection",
                        exc_info=True,
                    )

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(
            reconnect_delay * 2,
            MAX_RECONNECT_DELAY_SECONDS,
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
