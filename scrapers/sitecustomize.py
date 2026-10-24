# Mounted into Open States' scraper containers (PYTHONPATH) so they identify as PlainGov: some legislatures (UT)
# reject the default python-requests user agent. USER_AGENT is also what some state scrapers (UT) read directly.
# SCRAPER_TIMEOUT raises the 60-second request timeout for slow sites (WV).
import os

try:
    import scrapelib
    scrapelib._user_agent = os.environ.get("USER_AGENT", scrapelib._user_agent)
    from openstates import settings
    settings.SCRAPELIB_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT", settings.SCRAPELIB_TIMEOUT))
except ImportError:  # a python in the container without the scraper packages (e.g. California's database loader)
    pass
