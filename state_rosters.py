"""Primary current rosters; IDs establish identity and full names must confirm it.

Transport and its <=24-hour cache belong to the caller. A missing source never
falls back to a previous roster, an aggregator, or a reachable biography.
"""
import datetime
import hashlib
import time
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

SOURCES = {
    "MN": ["https://www.house.mn.gov/members/hmem.asp", "https://www.senate.mn/api/members"],
    "MO": ["https://house.mo.gov/MemberGridCluster.aspx", "https://www.senate.mo.gov/senators"],
    "NE": ["https://nebraskalegislature.gov/senators/senator_list.php"],
    "NM": ["https://www.nmlegis.gov/Members/Legislator_List?T=R", "https://www.nmlegis.gov/Members/Legislator_List?T=S"],
    "NY": ["https://nyassembly.gov/mem/", "https://www.nysenate.gov/senators-committees"],
    "NC": ["https://www.ncleg.gov/Members/MemberTable/H", "https://www.ncleg.gov/Members/MemberTable/S"],
    "ND": ["https://www.ndlegis.gov/assembly/{term}-{start}/regular/members"],
    "OH": ["https://www.ohiohouse.gov/members/member-directory", "https://www.ohiosenate.gov/senators"],
    "OK": ["https://www.okhouse.gov/representatives", "https://oksenate.gov/senators"],
    "RI": ["https://www.rilegislature.gov/_vti_bin/Lists.asmx"],
    "SC": ["https://www.scstatehouse.gov/member.php?chamber=H", "https://www.scstatehouse.gov/member.php?chamber=S"],
    "TN": ["https://wapp.capitol.tn.gov/apps/LegislatorInfo/Directory?chamber=H&ga={term}", "https://wapp.capitol.tn.gov/apps/LegislatorInfo/Directory?chamber=S&ga={term}"],
    "TX": ["https://house.texas.gov/members", "https://senate.texas.gov/members.php"],
    "WA": ["https://leg.wa.gov/legislators?activeView=MemberList"],
    "WV": ["https://www.wvlegislature.gov/House/roster.cfm", "https://www.wvlegislature.gov/Senate1/roster.cfm"],
    "WI": ["https://docs.legis.wisconsin.gov/{start}/legislators/assembly", "https://docs.legis.wisconsin.gov/{start}/legislators/senate"],
}


SOURCES.update({
    'AK': ['https://akleg.gov/house.php', 'https://akleg.gov/senate.php'],
    'AL': ['https://alison.legislature.state.al.us/graphql?' + urllib.parse.urlencode({'query':
           'query { currentSession: session(where: {current: {eq: true}}) {name abbreviation} legislativeMembers(where: {active: {eq: true}, elected: {eq: true}}) { data {id fullName body district active elected} } }'})],
    'AR': ['https://www.arkleg.state.ar.us/Legislators/List'],
    'AZ': ['https://www.azleg.gov/memberroster/'],
    'CA': ['https://www.assembly.ca.gov/assemblymembers', 'https://www.senate.ca.gov/senators'],
    'CO': ['https://leg.colorado.gov/legislators'],
    'CT': ['https://www.cga.ct.gov/asp/menu/hlist.asp', 'https://www.cga.ct.gov/asp/menu/slist.asp'],
    'DE': [f'https://{c}.delaware.gov/members/' for c in ('housedems', 'housegop', 'senatedems', 'senategop')],
    'FL': ['https://www.flsenate.gov/Senators', 'https://www.flhouse.gov/Sections/Representatives/representatives.aspx'],
    'GA': ['https://www.legis.ga.gov/api/sessions'],
    'HI': ['https://search.capitol.hawaii.gov/?type=legislator-addresses'],
    'IA': ['https://www.legis.iowa.gov/legislators/house', 'https://www.legis.iowa.gov/legislators/senate'],
    'ID': ['https://legislature.idaho.gov/house/membership/', 'https://legislature.idaho.gov/senate/membership/'],
    'IL': ['https://my.ilga.gov/House/Members/List', 'https://my.ilga.gov/Senate/Members/List'],
    'MS': ['https://www.legislature.ms.gov/legislators/representatives/', 'https://www.legislature.ms.gov/legislators/senators/'],
    'IN': ['https://www.indianasenaterepublicans.com/senators', 'https://indianasenatedemocrats.org/senators/',
           'https://www.indianahousedemocrats.org/members', 'https://www.indianahouserepublicans.com/members/'],
    'KS': ['https://www.kslegislature.gov/b{start}_{short_end}/legislators/?page=1&sort=name&dir=asc&per_page=all'],
    'KY': ['https://legislature.ky.gov/Legislators/house-of-representatives', 'https://legislature.ky.gov/Legislators/senate'],
    'LA': ['https://house.louisiana.gov/H_Reps/H_Reps_FullInfo', 'https://senate.la.gov/Senators_FullInfo'],
    'MD': ['https://mgaleg.maryland.gov/mgawebsite/Members/Index/house', 'https://mgaleg.maryland.gov/mgawebsite/Members/Index/senate'],
    'ME': ['https://legislature.maine.gov/house/MemberProfiles/List?sort=alpha', 'https://legislature.maine.gov/senate/senators/9536'],
    'MI': ['https://senate.michigan.gov/senators/all-senators/', 'https://www.house.mi.gov/AllRepresentatives'],
    'MT': ['https://www.legmt.gov/wp-json/legmt/v1/people/legislators'],
    'NH': ['https://gc.nh.gov/house/members/', 'https://gc.nh.gov/senate/members/senate_roster.aspx'],
    'NJ': ['https://www.njleg.state.nj.us/legislative-roster'],
    'NV': ['https://www.leg.state.nv.us/App/Legislator/A/Assembly/Current', 'https://www.leg.state.nv.us/App/Legislator/A/Senate/Current'],
    'OR': ['https://www.oregonlegislature.gov/house/Pages/RepresentativesAll.aspx', 'https://www.oregonlegislature.gov/senate/Pages/SenatorsAll.aspx'],
    'PA': ['https://www.palegis.us/house/members', 'https://www.palegis.us/senate/members'],
    'SD': ['https://sdlegislature.gov/api/Sessions'],
    'UT': ['https://le.utah.gov/data/legislators.json'],
    'VA': ['https://house.vga.virginia.gov/members', 'https://apps.senate.virginia.gov/Senator/index.php'],
    'VT': ['https://legislature.vermont.gov/people/loadAll/{end}'],
    'WY': ['https://api.wyoleg.gov/v1/Legislator/{year}/H', 'https://api.wyoleg.gov/v1/Legislator/{year}/S'],
    'MA': [],  # MA has its own Presenter/directory gate.
})


def member_name(value):
    """Formatting normalization only: preserve middle names, initials and suffixes."""
    value = ' '.join(html.unescape(value).replace('\u200b', '').replace('\ufeff', '').split())
    title = r'(?:(?:State|The Honorable)\s+)?(?:Senator|Sen\.?|Representative|Rep\.?|Assemblymember|Assemblyman|Assemblywoman|Asm\.?|Delegate|Del\.?|Mr\.|Ms\.|Mrs\.|Dr\.)\s+'
    value = re.sub('^' + title, '', value, flags=re.I)
    parts = [p.strip() for p in value.split(',')]
    suffix = r'(?:jr\.?|sr\.?|ii|iii|iv|v)'
    if len(parts) > 1 and not re.fullmatch(suffix, parts[1], re.I):
        value = ' '.join([parts[1], parts[0]] + parts[2:])
    value = re.sub('^' + title, '', value, flags=re.I)
    return ' '.join(re.sub(r'[^\w\s]', ' ', value.casefold()).split())


def member_key(st, value):
    """String namespace for explicit URLs/IDs, never a name-derived identity."""
    if not isinstance(value, str) or re.search(r'[\x00-\x1f\\]', value):
        return None
    try:
        u = urllib.parse.urlsplit(html.unescape(value))
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
                or u.password is not None or u.port not in (None, 80 if u.scheme == 'http' else 443)):
            return None
        host, path = u.hostname.removeprefix('www.'), u.path.rstrip('/').replace(' ', '%20')
        pairs = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        query = dict(pairs)
        if len(pairs) != len(query):  # Duplicate query keys have server-dependent meaning.
            return None
    except ValueError:
        return None
    def key(*parts):
        return json.dumps([st, *parts], separators=(',', ':'), ensure_ascii=False)
    if u.fragment and st != 'ID' and not (st == 'IL' and u.fragment.startswith(':~:text=')):
        return key(host, path, sorted(pairs), u.fragment)
    if st == 'AL' and host == 'legislature.state.al.us' and path.lower() in ('/aliswww/isd/alrepresentative.aspx', '/aliswww/isd/alsenator.aspx') and query.get('OID_SPONSOR', '').isdigit():
        return key('sponsor', query['OID_SPONSOR'])
    if st == 'AZ' and host == 'azleg.gov' and (m := re.fullmatch(r'/(?:(house|senate)/)?(house|senate)-member', path, re.I)):
        if (not m[1] or m[1].lower() == m[2].lower()) and all(re.fullmatch('[1-9][0-9]*', query.get(k, '')) for k in ('legislature', 'legislator')):
            return key(m[2].lower(), query['legislature'], query['legislator'])
    if st == 'FL' and host == 'flhouse.gov' and path.lower() == '/sections/representatives/details.aspx':
        q = {k.lower(): v for k, v in pairs}
        if len(q) == len(pairs) and set(q) <= {'memberid', 'legislativetermid'} and q.get('memberid', '').isdigit() and int(q['memberid']) > 0:
            return key('house', str(int(q['memberid'])))
        return None
    if st == 'FL' and host == 'flsenate.gov' and (m := re.fullmatch(r'/Senators/(?:\d{4}-\d{4}/)?S(\d+)', path, re.I)):
        return key('senate', str(int(m[1])))
    if st == 'GA' and host == 'legis.ga.gov' and (m := re.fullmatch(r'/members/(house|senate)/(\d+)', path)):
        return key(m[1], m[2])
    if st == 'HI' and host == 'capitol.hawaii.gov' and path.lower() == '/legislature/memberpage.aspx' and set(query) <= {'member', 'year'} and query.get('member', '').isdigit():
        return key('member', query['member'])
    if st == 'IL' and host in ('ilga.gov', 'my.ilga.gov') and not query and (m := re.fullmatch(r'/(House|Senate)/Members/Details/(\d+)', path, re.I)):
        return key(m[1].lower(), m[2])
    if st == 'MS' and host == 'legislature.ms.gov' and path.lower() == '/member/home/memberbio':
        q = {k.lower(): v for k, v in pairs}
        if len(q) == len(pairs) and set(q) <= {'fiscalyear', 'chamber', 'mem_name', 'district'} and q.get('chamber') in ('H', 'S') and q.get('mem_name', '').strip() and q.get('district', '').strip().isdigit() and int(q['district']) > 0:
            return key(q['chamber'], q['mem_name'].strip(), str(int(q['district'])))
        return None
    if st == 'MO' and host == 'house.mo.gov' and path.lower() == '/memberdetails.aspx' and query.get('district', '').isdigit():
        return key('house', str(int(query['district'])))
    if st == 'TN':
        district = query.get('district', '') if host == 'wapp.capitol.tn.gov' and path.lower() in ('/apps/legislatorinfo/member', '/apps/legislatorinfo/member.aspx') else ''
        if host == 'capitol.tn.gov' and (m := re.fullmatch(r'/(house|senate)/members/([hs])([0-9]+)\.html', path, re.I)):
            if (m[1].lower(), m[2].lower()) in (('house', 'h'), ('senate', 's')):
                district = m[2] + m[3]
        if re.fullmatch(r'[HS][0-9]+', district, re.I):
            return key(district[0].upper(), str(int(district[1:])))
    if st == 'KS' and host == 'kslegislature.gov' and (m := re.fullmatch(r'/(?:li/)?(b\d{4}_\d{2})/(?:members|legislators)/((?:rep|sen)_[a-z0-9_]+)', path)):
        return key(m[1], m[2])
    if st == 'ID' and host == 'legislature.idaho.gov':
        m = re.fullmatch(r'/(house|senate|legislators)/membership(?:/\d{4}/id(\d+))?', path)
        popup = re.fullmatch(r'modal-popup-(\d+)', u.fragment)
        if m and m[2] and u.fragment and (not popup or m[2] != popup[1]):
            return None
        if m and (m[2] or popup):
            return key('member', m[2] or popup[1])
    if st == 'UT':
        if (host, path.split('/')[1:2]) in (('house.utleg.gov', ['rep']), ('senate.utah.gov', ['sen'])):
            if m := re.fullmatch(r'/(rep|sen)/([A-Z0-9]+)', path):
                return key(m[1], m[2])
    if st == 'VA':
        ident = None
        if host == 'lis.virginia.gov' and (m := re.fullmatch(r'/session-details/\d+/member-information/([HS]\d+)/member-details', path)):
            ident = m[1]
        elif host == 'house.vga.virginia.gov' and (m := re.fullmatch(r'/members/(H\d+)', path)):
            ident = m[1]
        elif host == 'apps.senate.virginia.gov' and path == '/Senator/memberpage.php':
            ident = query.get('id')
        if ident and re.fullmatch('[HS][0-9]+', ident):
            return key(ident[0], str(int(ident[1:])))
    if st == 'ME' and host == 'legislature.maine.gov':
        path = path.replace('/house/house/MemberProfiles/', '/house/MemberProfiles/')
        if re.fullmatch(r'/district\d+', path, re.I):
            path = path.lower()
    # Everything not explicitly bridged retains query values, case and SPA fragments.
    return key(host, path, sorted(pairs), u.fragment)


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.a, self.children = tag, dict(attrs), []

    def text(self):
        return ' '.join(' '.join(c if isinstance(c, str) else c.text() for c in self.children).split())

    def all(self, tag=None):
        for c in self.children:
            if isinstance(c, Node):
                if tag is None or c.tag == tag:
                    yield c
                yield from c.all(tag)

    def cls(self, value):
        return value in self.a.get('class', '').split()


class DOM(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def links(node, base, pattern=None):
    for a in node.all('a'):
        href = a.a.get('href', '').strip()
        if href and (pattern is None or re.search(pattern, href)):
            yield urllib.parse.urljoin(base, href), a.text()


def named(node, tag=None, cls=None):
    return next((n.text() for n in node.all(tag) if cls is None or n.cls(cls)), '')


def inactive(text):
    return bool(re.search(r'\b(?:resigned|deceased|died|vacant|vacancy|retired|former member|term of office ended|tribal representative)\b', text, re.I))


def members(st, session, fetch, needed=None):
    """Return (key, destination or None, full displayed name) from current primary lists."""
    today = datetime.date.today()
    try:
        start, end = int(session['year_start']), int(session['year_end'])
        # MT/NV regular sessions end a year before their members' terms; their live rosters stand alone.
        if start > today.year or end < start or (st not in ('MT', 'NV') and end < today.year):
            return []
    except (KeyError, ValueError, TypeError):
        return []
    term = re.match(r'(\d+)(?:st|nd|rd|th)\b', session.get('session_name', ''), re.I)
    if st in ('TN', 'ND') and not term:
        return []
    result = []
    for source in SOURCES.get(st, []):
        # GraphQL has literal braces; only substitute the known URL placeholders.
        url = source
        for k, v in dict(start=start, end=end, short_end=str(end)[-2:], year=today.year, term=term[1] if term else '').items():
            url = url.replace('{' + k + '}', str(v))
        try:
            rows = _source(st, url, session, fetch, needed, today)
            for identity, destination, name in rows:
                if identity and name and not inactive(name) and (destination is None or member_key(st, destination)):
                    result.append((identity, destination, name.strip()))
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration, ET.ParseError, OSError):
            continue
    # Conflicting current names at one identity are not evidence for either person.
    names = {}
    for key, _, name in result:
        names.setdefault(key, set()).add(member_name(name))
    return list(dict.fromkeys(r for r in result if len(names[r[0]]) == 1))


def _source(st, url, session, fetch, needed, today):
    out = []
    def add(href, name, destination=True):
        href = urllib.parse.urljoin(url, href.strip()).replace(' ', '%20')
        out.append((member_key(st, href), href if destination is True else destination, name))
    def addlinks(node, pattern=None):
        for href, name in links(node, url, pattern):
            add(href, name)
    if st == 'RI':
        soap = (b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
                b'<GetListItems xmlns="http://schemas.microsoft.com/sharepoint/soap/"><listName>legislationInformation</listName>'
                b'<rowLimit>300</rowLimit></GetListItems></soap:Body></soap:Envelope>')
        # Match the public frontend; adding SOAPAction changes this to a 401 response.
        root = ET.fromstring(fetch(url, data=soap, headers={'Content-Type': 'text/xml; charset="utf-8"'}))
        for r in root.iter('{#RowsetSchema}row'):
            if r.get('ows_ActiveFlag') == '1' and r.get('ows_chamber') in ('House', 'Senate'):
                add(r.get('ows_legislator_bioURL', '').split(',')[0], r.get('ows_legislator_name', ''))
        return out
    if st == 'OK' and 'oksenate.gov' in url:
        return []  # No authenticated, usable roster structure was available for these chambers.
    if st == 'GA':
        # Public frontend guest-token protocol, also used by the existing Open States GA scraper.
        timestamp = str(int(time.time() * 1000))
        key = hashlib.sha512(('QFpCwKfd7fjVEXFFwSu36BwwcP83xYgxLAhLYmKkletvarconst' + timestamp).encode()).hexdigest()
        token = json.loads(fetch(f'https://www.legis.ga.gov/api/authentication/token?key={key}&ms={timestamp}'))
        headers = {'Authorization': 'Bearer ' + token}
        current = [s for s in json.loads(fetch(url, headers=headers)) if s['isCurrent'] is True]
        if len(current) != 1:
            return []
        current = current[0]
        years = re.match(r'(\d{4})(?:-(\d{4}))?\b', current['description'])
        if not years or not int(years[1]) <= today.year <= int(years[2] or years[1]):
            return []
        for r in json.loads(fetch(f"https://www.legis.ga.gov/api/members/list/{current['id']}", headers=headers)):
            chamber = {1: 'house', 2: 'senate'}.get(r['district']['chamberType'])
            if chamber and r['sessionId'] == current['id'] and (not r['dateVacated'] or r['dateVacated'][:10] > today.isoformat()):
                add(f"https://www.legis.ga.gov/members/{chamber}/{r['id']}?session={current['id']}", r['fullName'])
        return out
    body = fetch(url)
    if not body:
        return []
    if st == 'HI':
        scripts = re.findall(r'src="(/static/js/main\.[^"]+\.js)"', body)
        if len(scripts) != 1:
            return []
        js = fetch(urllib.parse.urljoin(url, scripts[0]))
        if not re.search(fr'CURRENT_YEAR_NUM:{today.year}\b', js):
            return []
        app = re.search(r'REACT_APP_ALGOLIA_APP_ID.*?}\("([A-Za-z0-9]+)"\)', js, re.S)[1]
        token = re.search(r'REACT_APP_ALGOLIA_SEARCH_KEY.*?}\("([A-Za-z0-9]+)"\)', js, re.S)[1]
        payload = {'requests': [{'indexName': 'legislators', 'query': '', 'filters': f'year:{today.year}', 'hitsPerPage': 100, 'page': 0}]}
        data = json.loads(fetch(f'https://{app}-dsn.algolia.net/1/indexes/*/queries', data=json.dumps(payload).encode(),
                               headers={'Content-Type': 'application/json', 'X-Algolia-Application-Id': app, 'X-Algolia-API-Key': token}))['results'][0]
        # shortcut: the 76-seat current lookup fits one 100-row page; paginate if it grows.
        if data['nbPages'] != 1 or data['nbHits'] != len(data['hits']):
            return []
        for r in data['hits']:
            note = (r['termNotes'] or '').strip()
            if r['active'] != 'True' or r['resigned'] or r['year'] != str(today.year) or r['chamber'] not in ('H', 'S'):
                continue
            if note and (not re.fullmatch(r'Appointed \d{1,2}/\d{1,2}/\d{4}', note) or datetime.datetime.strptime(note[10:], '%m/%d/%Y').date() > today):
                continue
            name = ' '.join(r.get(k, '') for k in ('firstName', 'lastName', 'surName')).strip()
            # Require an existing matching numeric ID and full name; never remap the eight disagreeing Senate IDs.
            add(f"https://www.capitol.hawaii.gov/legislature/memberpage.aspx?member={r['memberKey']}&year={today.year}", name, destination=url)
        return out
    if st == 'AL':
        data = json.loads(body)['data']
        if data['currentSession']['abbreviation'] != f'{today.year}RS':
            return []
        for r in data['legislativeMembers']['data']:
            if r['active'] is True and r['elected'] is True and r['body'] in ('House', 'Senate'):
                # Old explicit sponsor IDs are bridged below; a directory is never an identity.
                out.append((json.dumps([st, 'sponsor', str(r['id'])], separators=(',', ':')),
                            f"https://alison.legislature.state.al.us/{r['body'].lower()}-leaders-members?tab=1", r['fullName']))
        return out
    if st == 'MN' and url.endswith('/api/members'):
        for r in json.loads(body)['members']:
            add(f"https://www.senate.mn/members/member_bio.html?mem_id={r['mem_id']}", r['preferred_full_name'])
        return out
    if st == 'MT':
        for r in json.loads(body)['content']:
            if (r['type'] == 'LEGISLATOR' and r['isActive'] is True and r['startDate'] and r['endDate']
                    and r['startDate'] <= today.isoformat() < r['endDate']):
                name = r.get('displayName') or ' '.join(r.get(k) or '' for k in ('firstName', 'middleName', 'lastName', 'nameSuffix'))
                dest = r['links']['profileUrl']
                add(f"https://www.legmt.gov/legislators/{r['id']}", name, dest)
                add(dest, name)
        return out
    if st == 'SD':
        current = [r for r in json.loads(body) if r['CurrentSession'] is True and not r['SpecialSession']
                   and str(r['Year']) == str(today.year) and r['StartDate'][:10] <= today.isoformat()]
        if len(current) != 1:
            return []
        sid = current[0]['SessionId']
        for r in json.loads(fetch(f'https://sdlegislature.gov/api/SessionMembers/Session/{sid}')):
            if r['SessionId'] == sid and r['MemberType'] in ('H', 'S') and (not r['InactiveDate'] or r['InactiveDate'][:10] > today.isoformat()):
                add(f"https://sdlegislature.gov/Legislators/Profile/{r['SessionMemberId']}", r['Name'])
        return out
    if st == 'UT':
        for r in json.loads(body)['legislators']:
            if r['house'] in ('H', 'S') and urllib.parse.parse_qs(urllib.parse.urlsplit(r['legislation']).query).get('sess') == [f'{today.year}GS']:
                base = 'https://house.utleg.gov/rep/' if r['house'] == 'H' else 'https://senate.utah.gov/sen/'
                add(base + r['id'], r['formatName'], None)
        return out
    if st == 'VT':
        for r in json.loads(body)['data']:
            if not r['TerminationReason'].strip():
                add(f"https://legislature.vermont.gov/people/single/{session['year_end']}/{r['PersonID']}",
                    ' '.join(r.get(k) or '' for k in ('FirstName', 'LastName', 'NameSuffix')))
        return out
    if st == 'WY':
        for r in json.loads(body):
            if r['legStatus'] == 'C' and not r.get('deceased'):
                add(f"https://wyoleg.gov/Legislators/{today.year}/{url[-1]}/{r['legID']}", r['name'])
        return out
    root = DOM(body).root
    if st in ('OK', 'NJ'):
        data = json.loads(next(n for n in root.all('script') if n.a.get('id') == '__NEXT_DATA__').text())['props']['pageProps']
        if st == 'NJ':
            for r in data['legrosterData'][0]:
                if r['BioLink'] and not r['VacantSort']:
                    add(r['BioLink'], r['Full_Name'])
        elif 'okhouse.gov' in url:
            if data['isCurrent'] is not True or data['selectedLegislature']['current'] is not True:
                return []
            for row in data['members']['legislatureMembers']:
                r = row['attributes']
                add('https://www.okhouse.gov/representatives/' + r['slug'], ' '.join(r.get(k) or '' for k in ('firstName', 'lastName', 'suffix')))
        return out
    if st == 'TX' and 'house.texas.gov' in url:
        for n in root.all('get-members'):
            for r in json.loads(n.a[':members']):
                if r['active'] == 1:
                    add(r['link'], r['member_name'])
        return out
    if st == 'MI' and 'senate.michigan.gov' in url:
        for n in root.all():
            if 'senatorinfo' in n.a:
                for r in json.loads(n.a['senatorinfo']):
                    add(r['website'], ' '.join(r.get(k) or '' for k in ('firstName', 'middleInitial', 'lastName', 'suffix')))
        return out
    if st == 'OR':
        for table in root.all('table'):
            if table.a.get('summary', '').strip() in ('RepresentativeList', 'SenatorList'):
                for row in table.all('tr'):
                    if not inactive(row.text()):
                        for h in row.all('h3'):
                            addlinks(h)
        field = 'MemberContact' if '/house/' in url else 'Quick_x0020_Contact_x0020_Inform'
        for m in re.finditer(r'var WPQ\d+ListData\s*=\s*', body):
            data = json.JSONDecoder().raw_decode(body[m.end():])[0]
            for r in data.get('Row', []):
                if field in r:
                    for h in DOM(r[field]).root.all('h3'):
                        for href, name in links(h, url):
                            add(href, name)
        return out
    if st == 'IL':
        heading = re.search(r'(\d+)th General Assembly\s*\((\d{4})[–-](\d{4})\)', root.text())
        if not heading or not int(heading[2]) <= today.year <= int(heading[3]):
            return []
        current = re.split(r'<h[24][^>]*>\s*Former Members\s*</h[24]>', body, flags=re.I)[0]
        chamber = 'House' if '/House/' in url else 'Senate'
        for row in DOM(current).root.all('tr'):
            if not inactive(row.text()):
                addlinks(row, fr'/{chamber}/Members/Details/\d+$')
        return out
    if st == 'MS':
        chamber = 'H' if '/representatives/' in url else 'S'
        frames = [urllib.parse.urljoin(url, n.a.get('src', '')) for n in root.all('iframe')]
        frames = [f for f in frames if urllib.parse.urlsplit(f).hostname == 'www.legislature.ms.gov' and urllib.parse.urlsplit(f).path == '/member/']
        if len(frames) != 1:
            return []
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(frames[0]).query)
        if q.get('fiscalyear') != [str(today.year % 100)] or q.get('chamber') != [chamber]:
            return []
        gallery = DOM(fetch(frames[0])).root
        if f'{today.year} Regular Session' not in gallery.text():
            return []
        current = {}
        for card in gallery.all('div'):
            a = card.a
            if not card.cls('active-member') or a.get('data-customurl') or a.get('data-chamber') != chamber or a.get('data-fiscal_year') != str(today.year % 100):
                continue
            name = named(card, cls='flex-grow-1')
            query = urllib.parse.urlencode(dict(fiscalYear=a['data-fiscal_year'], chamber=chamber, mem_name=a['data-mem_name'], district=a['data-district']))
            href = 'https://www.legislature.ms.gov/member/Home/MemberBio?' + query
            identity = member_key(st, href)
            if identity and name:
                row = (href, name)
                current[identity] = row if identity not in current or current[identity] == row else None
        for identity, row in current.items():
            if row:
                add(*row)
        # Old XML supplies only the explicit native key/name, never evidence of current office.
        for identity in sorted(needed or []):
            parts = json.loads(identity)
            if len(parts) != 5 or parts[1] != 'billstatus.ls.state.ms.us' or parts[3:] != [[], '']:
                continue
            m = re.fullmatch(r'/members/(house|senate)/[^/]+\.xml', parts[2], re.I)
            if not m or ('H' if m[1].lower() == 'house' else 'S') != chamber:
                continue
            try:
                info = ET.fromstring(fetch('https://' + parts[1] + parts[2]))
                district = info.findtext('DISTRICT', '').strip()
                key = json.dumps([st, chamber, info.findtext('U_MEM_NAM', '').strip(), str(int(district))], separators=(',', ':'), ensure_ascii=False)
                row = current.get(key)
                if row and member_name(info.findtext('DISP_NAME', '')) == member_name(row[1]):
                    out.append((identity, row[0], row[1]))
            except (ValueError, ET.ParseError, OSError):
                continue
        return out
    if st == 'FL' and 'flhouse.gov' in url:
        for node in root.all():
            if node.tag == 'h1' and 'who served a partial term' in node.text().lower():
                break
            if node.tag != 'a' or not re.search(r'/Sections/Representatives/details\.aspx\?', node.a.get('href', ''), re.I):
                continue
            periods = [m for p in node.all('p') if (m := re.fullmatch(r'(\d{2}/\d{2}/\d{2}) - (\d{2}/\d{2}/\d{2})', p.text()))]
            if len(periods) == 1 and not inactive(node.text()):
                start, end = (datetime.datetime.strptime(v, '%m/%d/%y').date() for v in periods[0].groups())
                if start <= today <= end:
                    add(node.a['href'], named(node, 'h5'))
        return out
    if st == 'NY' and 'nysenate.gov' in url:
        for n in root.all('div'):
            if n.cls('view-display-id-senators_list'):
                for a in n.all('a'):
                    if name := named(a, 'h2', 'nys-senator--name'):
                        add(a.a['href'], name)
        return out
    if st == 'AZ':
        for table in root.all('table'):
            if table.a.get('id') in ('HouseRoster', 'SenateRoster'):
                for row in table.all('tr'):
                    if not inactive(row.text()):
                        for a in row.all('a'):
                            if a.cls('roster-tooltip'):
                                add(a.a['href'], a.text().split(' -- ')[0])
        return out
    if st == 'AK':
        for href, name in links(root, url, r'/basis/Member/Detail/\d+\?code='):
            if name and not re.search(r'\b(?:District|City:|View More)\b', name):
                add(href, name)
        return out
    if st == 'AR':
        for n in root.all('div'):
            if (n.cls('tableRow') or n.cls('tableRowAlt')) and not inactive(n.text()):
                addlinks(n, r'/Legislators/Detail\?')
    elif st == 'CA':
        for n in root.all():
            if n.cls('members-list__member') or n.cls('page-members__member'):
                name = named(n, cls='members-list__name') or named(n, cls='member__name')
                for a in n.all('a'):
                    if '/assemblymembers/' in (a.a.get('href') or '') or a.text() == 'Homepage':
                        add(a.a['href'], name)
    elif st == 'CO':
        for t in root.all('table'):
            if t.cls('leg-data'):
                for row in t.all('tr'):
                    if not inactive(row.text()):
                        addlinks(row, r'^/legislators/[a-z-]+$')
    elif st == 'CT':
        for row in root.all('tr'):
            cells = list(row.all('td'))
            if len(cells) >= 3:
                names, sites = list(links(cells[1], url)), list(links(cells[2], url))
                if names and sites and not inactive(row.text()):
                    add(sites[0][0], names[0][1])
    elif st == 'DE':
        for href, name in links(root, url, r'/members/(house|senate)-district-\d+/$'):
            if not name.startswith('Portrait of '):
                add(href, name)
    elif st in ('FL', 'IA', 'ME'):
        ident = {'FL': 'Senators', 'IA': 'sortableTable', 'ME': 'membersTable'}[st]
        for t in root.all('table'):
            if t.a.get('id') != ident:
                continue
            for row in t.all('tr'):
                if inactive(row.text()):
                    continue
                for a in row.all('a'):
                    href = a.a.get('href') or ''
                    if ((st == 'FL' and a.cls('senatorLink')) or (st == 'IA' and 'personID=' in href)
                            or (st == 'ME' and '/MemberProfiles/Details/' in href)):
                        add(href, a.text())
        if st == 'ME' and '/senate/' in url:
            addlinks(root, r'(?i)^/district\d+$')
    elif st == 'ID':
        for n in root.all('div'):
            if n.cls('hcode-inner-row'):
                for a in n.all('a'):
                    if (a.a.get('href') or '').startswith('#modal-popup-'):
                        add(a.a['href'], named(n, 'strong'))
    elif st == 'IN':
        seen, pending = set(), [url]
        while pending and len(seen) < 10:
            page = pending.pop(0)
            if page in seen:
                continue
            seen.add(page)
            doc = root if page == url else DOM(fetch(page)).root
            base = next((n.a['href'] for n in doc.all('base') if n.a.get('href')), page)
            if urllib.parse.urlsplit(base).netloc != urllib.parse.urlsplit(page).netloc:
                base = page
            for n in doc.all():
                if n.cls('senator-item'):
                    a = next(n.all('a'))
                    add(urllib.parse.urljoin(base, a.a['href']), named(n, 'h3'))
                elif n.tag == 'li' and n.cls('list-item'):
                    for href, _ in links(n, base, '^/members/'):
                        add(href, named(n, cls='list-item-content__title'))
                elif n.tag == 'a' and n.cls('member-link'):
                    add(urllib.parse.urljoin(base, n.a['href']), named(n, 'h2'))
                elif n.tag == 'a' and '/senator/' in (n.a.get('href') or ''):
                    for img in n.all('img'):
                        if img.a.get('alt', '').startswith('State Senator '):
                            add(n.a['href'], img.a['alt'])
            if 'indianahouserepublicans.com' in url:
                for href, _ in links(doc, base):
                    u = urllib.parse.urlsplit(href)
                    q = urllib.parse.parse_qs(u.query)
                    if u.netloc == urllib.parse.urlsplit(url).netloc and u.path == '/index.php' and q.get('view') == ['members'] and 'pos' in q:
                        if href not in seen and href not in pending:
                            pending.append(href)
    elif st == 'KS':
        for a in root.all('a'):
            if not a.cls('rep-card') or inactive(a.text()):
                continue
            href = urllib.parse.urljoin(url, a.a['href'])
            key = member_key(st, href)
            if needed is None or key not in needed:
                continue
            # The current roster establishes ID membership before this bounded name-only lookup.
            target = urllib.parse.urlsplit(href)
            if target.netloc != urllib.parse.urlsplit(url).netloc or not re.fullmatch(r'/b\d{4}_\d{2}/legislators/(?:rep|sen)_[a-z0-9_]+/', target.path):
                continue
            try:
                profile = DOM(fetch(href)).root
            except (OSError, ValueError):
                continue
            names = [n.text() for n in profile.all('h2') if n.cls('leg-hero-title')]
            if len(names) == 1 and len(member_name(names[0]).split()) >= 2:
                add(href, names[0])
    elif st == 'KY':
        for a in root.all('a'):
            if 'Legislator-Profile.aspx?' in (a.a.get('href') or ''):
                add(a.a['href'], named(a, 'h3'))
    elif st == 'LA':
        for a in root.all('a'):
            if a.cls('legnamelink') and not inactive(a.text()):
                add(a.a['href'], a.text())
    elif st == 'MD':
        for a in root.all('a'):
            if '/Members/Details/' in (a.a.get('href') or '') and a.a.get('aria-hidden') != 'true':
                add(a.a['href'], a.text())
    elif st == 'MI':
        if f"current term expiring on December 31, {session['year_end']}" not in root.text():
            return []
        for n in root.all('li'):
            if n.cls('page-search-container') and not inactive(n.text()):
                for a in n.all('a'):
                    if a.cls('page-search-target'):
                        name = re.sub(r'\s*\((?:Republican|Democrat|Independent)\)\s*District-\d+.*$', '', a.text())
                        add(a.a['href'], name.replace(', Rep. ', ', '))
    elif st == 'MO' and 'house.mo.gov' in url:
        for row in root.all('tr'):
            if inactive(row.text()):
                continue
            pairs = list(links(row, url, 'MemberDetails.aspx'))
            if len(pairs) == 2 and pairs[0][0] == pairs[1][0]:
                add(pairs[0][0], pairs[1][1] + ' ' + pairs[0][1])
    elif st == 'NH':
        if '/house/' in url:
            for select in root.all('select'):
                if select.a.get('id') == 'pageBody_ddlReps':
                    for n in select.all('option'):
                        if re.fullmatch('[1-9][0-9]*', n.a.get('value', '')):
                            add('https://gc.nh.gov/house/members/member.aspx?pid=' + n.a['value'], n.text(), None)
        else:
            for card in root.all('div'):
                if card.cls('card'):
                    headings = [n.text() for n in card.all('h5') if n.cls('card-header')]
                    targets = {href for href, _ in links(card, url, r'/senate/members/webpages/district\d+\.aspx')}
                    if len(headings) == len(targets) == 1 and 'Senator ' in headings[0] and not inactive(card.text()):
                        add(targets.pop(), headings[0].split('Senator ', 1)[1])
    elif st == 'NV':
        for n in root.all('span'):
            if n.a.get('style') == 'padding-left: 0.2rem':
                for a in n.all('a'):
                    name = []
                    for child in a.children:
                        if isinstance(child, Node) and child.tag == 'br':
                            break
                        name.append(child if isinstance(child, str) else child.text())
                    add(a.a['href'], ''.join(name))
    elif st == 'VA':
        for href, name in links(root, url, r'/members/H\d+|memberpage\.php'):
            if name != 'View Profile':
                add(href, name)
    elif st == 'ND':
        seen = set()
        while url not in seen and len(seen) < 20:
            seen.add(url)
            for member in root.all('div'):
                if not member.cls('member-wrapper'):
                    continue
                current = True
                for status in (n.text() for n in member.all() if n.cls('member-status') and n.text()):
                    start = re.fullmatch(r'(?:Active|Effective) (\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))', status)
                    if not start:
                        current = False
                        break
                    fmt = '%m/%d/%Y' if len(start[1].split('/')[-1]) == 4 else '%m/%d/%y'
                    if datetime.datetime.strptime(start[1], fmt).date() > today:
                        current = False
                        break
                if current:
                    addlinks(member, r'/biography/')
            next_links = [urllib.parse.urljoin(url, a.a['href']) for a in root.all('a')
                          if 'next' in (a.a.get('rel') or '').split() and a.a.get('href')]
            if len(set(next_links)) != 1:
                break
            nxt = next_links[0]
            a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(nxt)
            if nxt in seen or (a.scheme, a.netloc, a.path) != (b.scheme, b.netloc, b.path):
                break
            url = nxt
            root = DOM(fetch(url)).root
    elif st == 'OH':
        for n in root.all('div'):
            if n.cls('media-container') and not inactive(n.text()):
                for href, _ in links(n, url):
                    add(href, named(n, cls='media-overlay-caption-text-line-1'))
    elif st == 'TN':
        ga = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['ga']
        for href, name in links(root, url, r'Member\?district='):
            if urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get('ga') == ga:
                add(href, name)
    else:
        _simple_html(st, url, root, add)
    return out


def _simple_html(st, url, root, add):
    patterns = {
        'MN': r'/members/profile/\d+|/members/members\.asp\?id=',
        'MO': r'/Senators/member\?id=\d+',
        'NE': r'/senators/landing-pages/index\.php\?District=\d+',
        'NM': r'Legislator\?SponCode=',
        'NC': r'/Members/Biography/[HS]/\d+',
        'NY': r'^/mem/[A-Z][A-Za-z-]+/?$',
        'OH': r'^/(members|senators)/[a-z-]+$',
        'PA': r'/(house|senate)/members/bio/\d+/',
        'SC': r'/member\.php\?code=',
        'TX': r'^member\.php\?d=\d+$',
        'WA': r'/legislators/member/',
        'WV': r'lawmaker\.cfm\?member=',
        'WI': r'legis\.wisconsin\.gov/(assembly|senate)/\d+/[a-z-]+',
    }
    pattern = patterns.get(st)
    if not pattern:
        return
    for a in root.all('a'):
        href = (a.a.get('href') or '').strip()
        if not re.search(pattern, href, re.I):
            continue
        name = a.text()
        if st == 'SC' and not a.cls('membername'):
            continue
        if st == 'WI' and name == 'Website':
            continue
        if st == 'NY' and not re.fullmatch(r'/mem/[A-Z][A-Za-z-]+/?', href):
            continue
        if st == 'MN':
            name = re.sub(r'\s*\(\d+[AB], (?:DFL|R|D|I)\)$', '', name)
        elif st == 'MO':
            name = re.sub(r'\s+District \d+$', '', name)
        elif st == 'NE':
            name = re.sub(r'\s+\d+$', '', name)
        elif st == 'WA':
            name = re.sub(r'\s*\([DRI]\)$', '', name)
        elif st == 'NM':
            name = re.sub(r'\s*-\s*\([DRI]\)\s*District:\s*\d+.*$', '', name)
        elif st == 'NY':
            name = re.sub(r'\s+District \d+$', '', name)
        elif st == 'PA':
            name = re.sub(r'\s+(?:Democrat|Republican|Independent)\s+District \d+$', '', name)
        elif st == 'OH':
            name = named(a, 'h3') or named(a, 'h2') or name
        if name:
            add(href, name)
