# Mounted into Open States' scraper containers (PYTHONPATH) so they identify as PlainGov: some legislatures (UT)
# reject the default python-requests user agent. USER_AGENT is also what some state scrapers (UT) read directly.
# The 60-second request timeout becomes 180 (SCRAPER_TIMEOUT) for slow sites (WV).
import os

try:
    import scrapelib
    scrapelib._user_agent = os.environ.get("USER_AGENT", scrapelib._user_agent)
    from openstates import settings
    settings.SCRAPELIB_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT", 180))

    from requests.adapters import HTTPAdapter
    from urllib.parse import urlsplit

    _send = HTTPAdapter.send

    def send(self, request, **kwargs):
        # Upstream still disables TLS for old IN/VA certificate problems; both now have valid chains.
        url = urlsplit(request.url)
        # Session discovery uses an upstream hardcoded web key; use our activated partner key instead.
        if url.hostname == "lis.virginia.gov" and "WebAPIKey" in request.headers and os.environ.get("VA_API_KEY"):
            request.headers["WebAPIKey"] = os.environ["VA_API_KEY"]
        for key, header, host in (("INDIANA_API_KEY", "x-api-key", "api.iga.in.gov"), ("VA_API_KEY", "WebAPIKey", "lis.virginia.gov")):
            if os.environ.get(key) and request.headers.get(header) == os.environ[key] and (url.scheme != "https" or url.hostname != host):
                raise ValueError("State API credential left its HTTPS origin")
        if url.hostname in {"api.iga.in.gov", "iga.in.gov", "lis.virginia.gov"}:
            if url.scheme != "https":
                raise ValueError("State API requires HTTPS")
            kwargs["verify"] = True
        return _send(self, request, **kwargs)

    HTTPAdapter.send = send
except ImportError:  # a python in the container without the scraper packages (e.g. California's database loader)
    pass
