import pytest
import asyncio


@pytest.fixture
def temp_event_loop() -> asyncio.AbstractEventLoop:
    default_loop = asyncio.get_event_loop()
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    #
    yield new_loop
    #
    new_loop.close()
    asyncio.set_event_loop(default_loop)
