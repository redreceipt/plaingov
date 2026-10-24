# Mounted into Open States' scraper containers (PYTHONPATH) so they identify as PlainGov: some legislatures (UT)
# reject the default python-requests user agent.
import os

try:
    import scrapelib
    scrapelib._user_agent = os.environ.get("SCRAPER_USER_AGENT", scrapelib._user_agent)
except ImportError:  # a python in the container without scrapelib (e.g. California's database loader)
    pass
