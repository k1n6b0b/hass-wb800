from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import List, Optional

import aiohttp
import httpx
from bs4 import BeautifulSoup


@dataclass
class OutletInfo:
    number: int
    name: str
    is_on: bool
    is_reset_only: bool
    watts: Optional[float]
    amps: Optional[float]


@dataclass
class DeviceMetrics:
    voltage: Optional[float]
    total_watts: Optional[float]
    total_amps: Optional[float]


class _AuthExpiredError(RuntimeError):
    """Raised when the device requires re-authentication."""


class WattBoxClient:
    _AUTH_RECHECK_SECONDS = 120.0
    _MAIN_CACHE_TTL_SECONDS = 5.0

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        verify_ssl: bool = True,
        request_timeout_seconds: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
        self._httpx_timeout = request_timeout_seconds
        self._session = session
        self._own_session = session is None
        self._lock = asyncio.Lock()
        self._main_fetch_lock = asyncio.Lock()
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._basic_auth: Optional[aiohttp.BasicAuth] = None
        self._auth_checked_monotonic: Optional[float] = None
        self._main_html_cache: Optional[str] = None
        self._main_cache_monotonic: Optional[float] = None

    async def __aenter__(self) -> "WattBoxClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": "wb800-ha-client/0.1"},
            )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None

    def _invalidate_main_cache(self) -> None:
        self._main_html_cache = None
        self._main_cache_monotonic = None

    def _mark_auth_checked(self) -> None:
        self._auth_checked_monotonic = time.monotonic()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": "wb800-ha-client/0.1"},
            )
            self._own_session = True
        return self._session

    async def _ensure_logged_in(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and self._auth_checked_monotonic is not None
            and (now - self._auth_checked_monotonic) < self._AUTH_RECHECK_SECONDS
        ):
            return

        # Some firmwares use HTTP Basic, others Digest, others a login form.
        # Strategy: try Basic; if 401 with Digest challenge, switch to Digest using httpx;
        # if redirect to /login, try form login.
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._auth_checked_monotonic is not None
                and (now - self._auth_checked_monotonic) < self._AUTH_RECHECK_SECONDS
            ):
                return

            session = await self._get_session()
            auth = aiohttp.BasicAuth(self._username, self._password)

            async with session.get(
                f"{self._base_url}/main",
                auth=auth,
                ssl=self._verify_ssl,
                allow_redirects=False,
            ) as resp:
                if resp.status in (200, 304):
                    self._basic_auth = auth
                    self._mark_auth_checked()
                    return

                location = resp.headers.get("Location", "")
                if "/login" in location:
                    async with session.get(f"{self._base_url}/login", ssl=self._verify_ssl):
                        pass
                    async with session.post(
                        f"{self._base_url}/login",
                        data={"username": self._username, "password": self._password},
                        ssl=self._verify_ssl,
                        allow_redirects=True,
                    ) as login_resp:
                        if login_resp.status not in (200, 302):
                            text = await login_resp.text()
                            raise RuntimeError(
                                f"Login failed: HTTP {login_resp.status}: {text[:200]}"
                            )
                    async with session.get(f"{self._base_url}/main", ssl=self._verify_ssl) as resp2:
                        if resp2.status != 200:
                            raise RuntimeError(
                                f"Login did not grant access: HTTP {resp2.status}"
                            )
                    self._basic_auth = None
                    self._mark_auth_checked()
                    return

                if resp.status == 401:
                    www = resp.headers.get("WWW-Authenticate", "")
                    if "Digest" in www:
                        if self._httpx_client is not None:
                            await self._httpx_client.aclose()
                        self._httpx_client = httpx.AsyncClient(
                            base_url=self._base_url,
                            auth=httpx.DigestAuth(self._username, self._password),
                            verify=self._verify_ssl,
                            timeout=self._httpx_timeout,
                            headers={"User-Agent": "wb800-ha-client/0.1"},
                            follow_redirects=True,
                        )
                        r = await self._httpx_client.get("/main", follow_redirects=False)
                        if r.status_code in (200, 304):
                            self._basic_auth = None
                            self._mark_auth_checked()
                            return
                        if r.is_redirect and "/login" in r.headers.get("Location", ""):
                            r2 = await self._httpx_client.post(
                                "/login",
                                data={"username": self._username, "password": self._password},
                            )
                            if r2.status_code not in (200, 302):
                                raise RuntimeError(
                                    f"Login failed after Digest: HTTP {r2.status_code}"
                                )
                            r3 = await self._httpx_client.get("/main")
                            if r3.status_code != 200:
                                raise RuntimeError(
                                    "Login did not grant access after Digest: "
                                    f"HTTP {r3.status_code}"
                                )
                            self._mark_auth_checked()
                            return
                    raise RuntimeError(
                        "Unauthorized (401). Device requires Digest or credentials are wrong."
                    )

                self._basic_auth = auth
                self._mark_auth_checked()

    async def _fetch_main_html_once(self) -> str:
        await self._ensure_logged_in()

        if self._httpx_client is not None:
            resp = await self._httpx_client.get("/main")
            if resp.status_code in (401, 403):
                raise _AuthExpiredError(f"Auth failed with HTTP {resp.status_code}")
            if resp.is_redirect and "/login" in resp.headers.get("Location", ""):
                raise _AuthExpiredError("Redirected to login page")
            resp.raise_for_status()
            return resp.text

        session = await self._get_session()
        kwargs = {"ssl": self._verify_ssl}
        if self._basic_auth is not None:
            kwargs["auth"] = self._basic_auth

        async with session.get(f"{self._base_url}/main", **kwargs) as resp:
            if resp.status in (401, 403):
                raise _AuthExpiredError(f"Auth failed with HTTP {resp.status}")
            if resp.status in (301, 302, 303, 307, 308) and "/login" in resp.headers.get(
                "Location", ""
            ):
                raise _AuthExpiredError("Redirected to login page")
            resp.raise_for_status()
            return await resp.text()

    async def async_fetch_main_html(self) -> str:
        now = time.monotonic()
        if (
            self._main_html_cache is not None
            and self._main_cache_monotonic is not None
            and (now - self._main_cache_monotonic) < self._MAIN_CACHE_TTL_SECONDS
        ):
            return self._main_html_cache

        async with self._main_fetch_lock:
            now = time.monotonic()
            if (
                self._main_html_cache is not None
                and self._main_cache_monotonic is not None
                and (now - self._main_cache_monotonic) < self._MAIN_CACHE_TTL_SECONDS
            ):
                return self._main_html_cache

            for attempt in range(2):
                try:
                    html = await self._fetch_main_html_once()
                    self._main_html_cache = html
                    self._main_cache_monotonic = time.monotonic()
                    return html
                except _AuthExpiredError:
                    if attempt == 0:
                        self._auth_checked_monotonic = None
                        await self._ensure_logged_in(force=True)
                        continue
                    raise

            raise RuntimeError("Failed to fetch main page after re-authentication")

    async def async_fetch_outlets(self) -> List[OutletInfo]:
        html = await self.async_fetch_main_html()
        return self.parse_outlets_from_html(html)

    async def async_fetch_metrics(self) -> DeviceMetrics:
        html = await self.async_fetch_main_html()
        metrics = self.parse_metrics_from_html(html)
        # Fallback: if total watts/amps missing, sum per-outlet values
        if metrics.total_watts is None or metrics.total_amps is None:
            outlets = self.parse_outlets_from_html(html)
            watts_sum = sum(o.watts for o in outlets if o.watts is not None)
            amps_sum = sum(o.amps for o in outlets if o.amps is not None)
            if metrics.total_watts is None:
                metrics.total_watts = round(watts_sum, 2)
            if metrics.total_amps is None:
                metrics.total_amps = round(amps_sum, 2)
        return metrics

    async def _send_outlet_command(self, path: str, outlet_number: int) -> None:
        await self._ensure_logged_in()

        for attempt in range(2):
            try:
                if self._httpx_client is not None:
                    resp = await self._httpx_client.get(
                        path,
                        params={"o": outlet_number},
                        follow_redirects=False,
                    )
                    if resp.status_code in (401, 403):
                        raise _AuthExpiredError(f"Auth failed with HTTP {resp.status_code}")
                    if resp.status_code not in (200, 302):
                        resp.raise_for_status()
                else:
                    session = await self._get_session()
                    kwargs = {"ssl": self._verify_ssl}
                    if self._basic_auth is not None:
                        kwargs["auth"] = self._basic_auth
                    async with session.get(
                        f"{self._base_url}{path}",
                        params={"o": outlet_number},
                        **kwargs,
                    ) as resp:
                        if resp.status in (401, 403):
                            raise _AuthExpiredError(f"Auth failed with HTTP {resp.status}")
                        resp.raise_for_status()

                self._invalidate_main_cache()
                return
            except _AuthExpiredError:
                if attempt == 0:
                    self._auth_checked_monotonic = None
                    await self._ensure_logged_in(force=True)
                    continue
                raise

    async def async_turn_on(self, outlet_number: int) -> None:
        await self._send_outlet_command("/outlet/on", outlet_number)

    async def async_turn_off(self, outlet_number: int) -> None:
        await self._send_outlet_command("/outlet/off", outlet_number)

    async def async_reset(self, outlet_number: int) -> None:
        await self._send_outlet_command("/outlet/reset", outlet_number)

    @staticmethod
    def parse_outlets_from_html(html: str) -> List[OutletInfo]:
        soup = BeautifulSoup(html, "html.parser")
        outlets: List[OutletInfo] = []

        for block in soup.select("div.grid-grey > div.grid-block"):
            # Each block corresponds to an outlet card
            number_el = block.select_one(".grid-index-label > span")
            name_el = block.select_one("ul.grid-list > li.grid-head")
            input_el = block.select_one("input[id^='outlet']")

            if not number_el or not name_el or not input_el:
                continue

            try:
                number = int(number_el.get_text(strip=True))
            except ValueError:
                continue

            name = name_el.get_text(strip=True)
            is_on = input_el.has_attr("checked")
            is_reset_only = input_el.has_attr("disabled")

            watts_val: Optional[float] = None
            amps_val: Optional[float] = None

            # Energy stats are two <p> values inside a following div; parse floats ending with W/A
            stat_ps = block.select("div[style*='margin-top'] p")
            if len(stat_ps) >= 2:
                w_text = stat_ps[0].get_text(strip=True).replace("W", "").strip()
                a_text = stat_ps[1].get_text(strip=True).replace("A", "").strip()
                try:
                    watts_val = float(w_text)
                except ValueError:
                    watts_val = None
                try:
                    amps_val = float(a_text)
                except ValueError:
                    amps_val = None

            outlets.append(
                OutletInfo(
                    number=number,
                    name=name,
                    is_on=is_on,
                    is_reset_only=is_reset_only,
                    watts=watts_val,
                    amps=amps_val,
                )
            )

        # Sort by outlet number to ensure stable ordering
        outlets.sort(key=lambda o: o.number)
        return outlets

    @staticmethod
    def parse_metrics_from_html(html: str) -> DeviceMetrics:
        soup = BeautifulSoup(html, "html.parser")
        # Total power/current block
        total_watts: Optional[float] = None
        total_amps: Optional[float] = None
        power_blocks = soup.select("div.grid-block div.grid-text ul.primary-text li table td")
        # Find a pair where the left cell has 'POWER' and 'CURRENT'
        for td in power_blocks:
            text = td.get_text(" ", strip=True)
            if "POWER" in text and "CURRENT" in text:
                # Next sibling td should contain values
                tr = td.parent
                if tr and tr.find_next_sibling("td"):
                    val_td = tr.find_next_sibling("td")
                    lines = [l.strip() for l in val_td.get_text("\n", strip=True).split("\n")]
                    if len(lines) >= 2:
                        try:
                            total_watts = float(lines[0].replace("W", "").strip())
                        except ValueError:
                            total_watts = None
                        try:
                            total_amps = float(lines[1].replace("A", "").strip())
                        except ValueError:
                            total_amps = None
                break

        # Voltage block (green)
        voltage: Optional[float] = None
        volt_spans = soup.select("div.grid-block[style*='background'] span")
        for sp in volt_spans:
            txt = sp.get_text(strip=True)
            if txt.endswith("V"):
                try:
                    voltage = float(txt.replace("V", "").strip())
                except ValueError:
                    voltage = None
                break

        return DeviceMetrics(voltage=voltage, total_watts=total_watts, total_amps=total_amps)
