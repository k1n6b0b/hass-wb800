from __future__ import annotations

import pytest

from custom_components.wb800.client import WattBoxClient


SAMPLE_HTML = """
<html>
  <body>
    <div class="grid-grey">
      <div class="grid-block">
        <div class="grid-index-label"><span>2</span></div>
        <ul class="grid-list"><li class="grid-head">Amp B</li></ul>
        <input id="outlet2" type="checkbox" checked>
        <div style="margin-top: 8px"><p>88.5W</p><p>0.73A</p></div>
      </div>
      <div class="grid-block">
        <div class="grid-index-label"><span>1</span></div>
        <ul class="grid-list"><li class="grid-head">Amp A</li></ul>
        <input id="outlet1" type="checkbox" disabled>
        <div style="margin-top: 8px"><p>42W</p><p>0.35A</p></div>
      </div>
    </div>

    <div class="grid-block">
      <div class="grid-text">
        <ul class="primary-text">
          <li>
            <table>
              <tr>
                <td>POWER CURRENT</td>
                <td>130.5W\n1.08A</td>
              </tr>
            </table>
          </li>
        </ul>
      </div>
    </div>

    <div class="grid-block" style="background: #3cb371"><span>120.1V</span></div>
  </body>
</html>
"""


@pytest.mark.asyncio
async def test_main_html_cache_reuses_single_fetch() -> None:
    client = WattBoxClient("http://example", "user", "pass", verify_ssl=False)

    calls = 0

    async def fake_fetch() -> str:
        nonlocal calls
        calls += 1
        return "<html>ok</html>"

    client._fetch_main_html_once = fake_fetch  # type: ignore[attr-defined]

    first = await client.async_fetch_main_html()
    second = await client.async_fetch_main_html()

    assert first == "<html>ok</html>"
    assert second == "<html>ok</html>"
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_invalidates_after_command() -> None:
    client = WattBoxClient("http://example", "user", "pass", verify_ssl=False)

    calls = 0

    async def fake_fetch() -> str:
        nonlocal calls
        calls += 1
        return f"<html>{calls}</html>"

    async def fake_login(*, force: bool = False) -> None:
        return None

    class FakeHTTPX:
        async def get(self, *_args, **_kwargs):
            class R:
                status_code = 200

                def raise_for_status(self):
                    return None

            return R()

    client._fetch_main_html_once = fake_fetch  # type: ignore[attr-defined]
    client._ensure_logged_in = fake_login  # type: ignore[attr-defined]
    client._httpx_client = FakeHTTPX()  # type: ignore[assignment]

    assert await client.async_fetch_main_html() == "<html>1</html>"
    await client.async_turn_on(1)
    assert await client.async_fetch_main_html() == "<html>2</html>"


@pytest.mark.asyncio
async def test_parse_outlets_and_metrics() -> None:
    client = WattBoxClient("http://example", "user", "pass", verify_ssl=False)

    outlets = client.parse_outlets_from_html(SAMPLE_HTML)
    metrics = client.parse_metrics_from_html(SAMPLE_HTML)

    assert [o.number for o in outlets] == [1, 2]
    assert outlets[0].name == "Amp A"
    assert outlets[0].is_reset_only is True
    assert outlets[0].is_on is False
    assert outlets[0].watts == 42.0
    assert outlets[1].name == "Amp B"
    assert outlets[1].is_on is True
    assert outlets[1].amps == 0.73

    assert metrics.voltage == 120.1
    assert metrics.total_watts == 130.5
    assert metrics.total_amps == 1.08
