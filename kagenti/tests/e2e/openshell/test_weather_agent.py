"""
Weather Agent E2E Tests (OpenShell PoC)

Tests the weather-agent deployed via OpenShell manifests.
This agent is a simple A2A agent that does NOT require an LLM --
it uses the Open-Meteo API directly via MCP weather-tool.

Usage:
    pytest kagenti/tests/e2e/openshell/test_weather_agent.py -v -m openshell
"""

import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text


pytestmark = [pytest.mark.openshell, pytest.mark.asyncio]

# Weather data keywords that indicate the agent called the tool successfully
WEATHER_KEYWORDS = [
    "weather",
    "temperature",
    "degrees",
    "sunny",
    "cloudy",
    "rain",
    "forecast",
    "conditions",
    "humidity",
    "wind",
]


class TestWeatherAgentA2A:
    """Test the weather agent via A2A message/send."""

    async def test_weather_query_london(self, weather_agent_url):
        """Send a weather query for London and verify weather data in response."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                weather_agent_url,
                "What's the weather in London?",
            )

        # Verify JSON-RPC response structure
        assert "error" not in resp, f"A2A returned error: {resp.get('error')}"
        assert "result" in resp, f"A2A response missing 'result': {resp}"

        text = extract_a2a_text(resp)
        assert text, f"Empty response from weather agent. Full response: {resp}"
        assert len(text) > 10, f"Response too short: {text}"

        text_lower = text.lower()
        has_weather = any(kw in text_lower for kw in WEATHER_KEYWORDS)
        assert has_weather, f"Response does not contain weather data. Response: {text}"

    async def test_weather_query_multi_city(self, weather_agent_url):
        """Send a multi-city weather query and verify structured response."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                weather_agent_url,
                "Compare the weather in Tokyo and New York.",
                request_id="test-multi-city",
            )

        assert "error" not in resp, f"A2A returned error: {resp.get('error')}"

        text = extract_a2a_text(resp)
        assert text, f"Empty response from weather agent. Full response: {resp}"

        text_lower = text.lower()

        # The response should mention at least one city
        cities_mentioned = sum(
            1 for city in ["tokyo", "new york"] if city in text_lower
        )
        assert cities_mentioned >= 1, (
            f"Response does not reference the requested cities. Response: {text}"
        )

        # Should contain weather data
        has_weather = any(kw in text_lower for kw in WEATHER_KEYWORDS)
        assert has_weather, f"Response does not contain weather data. Response: {text}"
