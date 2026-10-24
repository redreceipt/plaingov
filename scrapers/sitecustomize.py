# Mounted into Open States' scraper containers (PYTHONPATH) so they identify as PlainGov: some legislatures (UT)
# reject the default python-requests user agent. USER_AGENT is also what some state scrapers (UT) read directly.
# The 60-second request timeout becomes 180 (SCRAPER_TIMEOUT) for slow sites (WV).
import os

try:
    import scrapelib
    scrapelib._user_agent = os.environ.get("USER_AGENT", scrapelib._user_agent)
    from openstates import settings
    settings.SCRAPELIB_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT", 180))
except ImportError:  # a python in the container without the scraper packages (e.g. California's database loader)
    pass
