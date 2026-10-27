"""Focused offline checks: synthetic fixtures only; no HTTP or persistent state."""
import datetime
import html
import json
from unittest.mock import patch

import state_rosters as r


class Today(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 25)


SESSION = {'year_start': 2025, 'year_end': 2026, 'session_name': '114th General Assembly', 'state_id': 1}


def url(st, index=0):
    value = r.SOURCES[st][index]
    for key, item in dict(start=2025, end=2026, short_end=26, year=2026, term=114).items():
        value = value.replace('{' + key + '}', str(item))
    return value


def roster(st, bodies, needed=None, session=SESSION):
    if isinstance(bodies, str):
        bodies = {url(st): bodies}
    calls = []
    def fetch(u, data=None, headers=None):
        calls.append((u, data, headers))
        value = bodies.get(u, '')
        if isinstance(value, Exception):
            raise value
        return value
    with patch.object(r.datetime, 'date', Today):
        rows = r.members(st, session, fetch, needed)
    assert all(isinstance(k, str) and isinstance(n, str) and (d is None or r.member_key(st, d)) for k, d, n in rows)
    return rows, calls


def names(st, body):
    return [n for _, _, n in roster(st, body)[0]]


def nextdata(data):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'pageProps': data}}) + '</script>'


assert r.member_name('Sen. Doe, Jane A., Jr.') == r.member_name('Jane A. Doe Jr.')
assert r.member_name('Doe Jr., Jane A.') == r.member_name('Jane A. Doe, Jr.')
assert r.member_name('Representative\u200b Jane A. Doe') == 'jane a doe'
assert r.member_name('Jane Ann Doe') != r.member_name('Jane Doe')
assert r.member_name('Jane A. Doe') != r.member_name('Jane B. Doe')
assert r.member_name('William Doe') != r.member_name('Bill Doe')
assert r.member_name('Doe, Rep. Jane') == 'jane doe'
for bad in ['javascript:alert(1)', '//example.gov/a', 'https://user@example.gov/a', 'https://example.gov:444/a',
            'https://example.gov:80/a', 'https://example.gov/a\nb', 'https://example.gov\\@evil.test/a',
            'https://example.gov/a?id=1&id=2']:
    assert r.member_key('XX', bad) is None, bad
assert isinstance(r.member_key('XX', 'https://example.gov/a?id=1'), str)
assert r.member_key('XX', 'https://example.gov/a?id=1') != r.member_key('XX', 'https://example.gov/a?id=2')
assert r.member_key('XX', 'https://example.gov/#/person/1') != r.member_key('XX', 'https://example.gov/#/person/2')
assert r.member_key('XX', 'https://example.gov/?id=A') != r.member_key('XX', 'https://example.gov/?id=a')
assert r.member_key('XX', 'https://example.gov/?id=A&x=B') == r.member_key('XX', 'http://www.example.gov/?x=B&id=A')
assert r.member_key('MO', 'https://house.mo.gov/MemberDetails.aspx?district=017') == r.member_key('MO', 'https://house.mo.gov/MemberDetails.aspx?year=2026&code=R&district=17')
assert r.member_key('MO', 'https://house.mo.gov/MemberDetails.aspx?district=017') != r.member_key('MO', 'https://house.mo.gov/MemberDetails.aspx?district=018')
assert r.member_key('TN', 'https://capitol.tn.gov/house/members/h07.html') == r.member_key('TN', 'https://wapp.capitol.tn.gov/apps/LegislatorInfo/Member?district=H7&ga=114')
assert r.member_key('ID', 'https://legislature.idaho.gov/house/membership/2026/id12/') == r.member_key('ID', 'https://legislature.idaho.gov/house/membership/#modal-popup-12')
assert r.member_key('ID', 'https://legislature.idaho.gov/house/membership/') != r.member_key('ID', 'https://legislature.idaho.gov/house/membership/#modal-popup-12')
assert r.member_key('ID', 'https://legislature.idaho.gov/legislators/membership/2026/id12/') == r.member_key('ID', 'https://legislature.idaho.gov/house/membership/#modal-popup-12')
assert r.member_key('FL', 'https://flsenate.gov/Senators/S1') == r.member_key('FL', 'https://www.flsenate.gov/Senators/2024-2026/S1')
assert r.member_key('VA', 'https://lis.virginia.gov/session-details/20262/member-information/S0080/member-details') == r.member_key('VA', 'https://apps.senate.virginia.gov/Senator/memberpage.php?id=S80')
assert len(r.SOURCES) == 50
for state in r.SOURCES:
    assert roster(state, {})[0] == []
assert roster('MN', {}, session={**SESSION, 'year_end': 2024}) == ([], [])
assert roster('TN', {}, session={**SESSION, 'session_name': 'Unknown'}) == ([], [])
assert roster('MN', {}, session={**SESSION, 'year_start': 2027, 'year_end': 2028}) == ([], [])

# Independent chambers and no retained evidence after an outage.
mn = json.dumps({'members': [{'mem_id': 7, 'preferred_full_name': 'Jane A. Doe'}]})
rows, _ = roster('MN', {url('MN'): OSError('TLS failed'), url('MN', 1): mn})
assert len(rows) == 1 and rows[0][1].endswith('mem_id=7')
assert roster('MN', {})[0] == []
assert names('MN', '<a href="/members/profile/1">Patty Doe (45B, DFL)</a>') == ['Patty Doe']

# Same district URL with two different current names is ambiguous, never choose one.
mo = '<table><tr><td><a href="MemberDetails.aspx?district=017">Doe</a></td><td><a href="MemberDetails.aspx?district=017">Jane</a></td></tr></table>'
assert names('MO', mo) == ['Jane Doe']
assert names('MO', mo + mo.replace('Jane', 'Janet')) == []
assert names('MO', mo.replace('</td><td>', '</td></tr><tr><td>')) == []

ar = '<div class="row tableRow"><a href="/Legislators/Detail?member=J.Doe">Jane Doe</a>{}</div>'
assert names('AR', ar.format('') + ar.replace('J.Doe', 'J.Roe').replace('Jane Doe', 'Jane Roe').format('Resigned')) == ['Jane Doe']
co = '<table class="leg-data"><tr><th data-label="Name"><a href="/legislators/jane-doe">Doe, Jane</a></th>{}</tr></table>'
assert names('CO', co.format('')) == ['Doe, Jane']
assert names('CO', co.format('Resigned 2/13/26')) == []
ia = '<table id="sortableTable"><tr><td><a href="/legislators/legislator?ga=91&amp;personID=1">Jane Doe</a></td></tr></table>'
assert names('IA', ia + ia.replace('sortableTable', 'formerMembers').replace('Jane Doe', 'Retired Person')) == ['Jane Doe']
fl = '<table id="Senators"><tr><td><a class="senatorLink" href="/Senators/2024-2026/S1">Doe, Jane</a></td></tr></table>'
assert names('FL', fl + fl.replace('id="Senators"', 'id="Former"').replace('Doe, Jane', 'Former Person')) == ['Doe, Jane']
fl_house = '<a href="/Sections/Representatives/details.aspx?MemberId=7&amp;LegislativeTermId=91"><h5>Doe, Jane</h5><p>{}</p></a>'
assert names('FL', {url('FL', 1): fl_house.format('11/06/24 - 11/03/26')}) == ['Doe, Jane']
for period in ('11/04/26 - 11/07/28', '11/06/24 - 08/01/26', 'Unknown'):
    assert names('FL', {url('FL', 1): fl_house.format(period)}) == []
assert names('FL', {url('FL', 1): '<h1>Representatives who served a partial term</h1>' + fl_house.format('11/06/24 - 11/03/26')}) == []
assert r.member_key('FL', 'https://www.flhouse.gov/Sections/Representatives/details.aspx?MemberId=7') == r.member_key('FL', 'https://www.flhouse.gov/Sections/Representatives/details.aspx?MemberId=7&LegislativeTermId=91')
hi_person = dict(memberKey='7', year='2026', firstName='Jane', lastName='Doe', surName='', chamber='H', active='True', resigned='', termNotes='')
hi_js = 'CURRENT_YEAR_NUM:2026; REACT_APP_ALGOLIA_APP_ID }("TESTAPP1"); REACT_APP_ALGOLIA_SEARCH_KEY }("PUBLICSEARCHKEY");'
hi_endpoint = 'https://TESTAPP1-dsn.algolia.net/1/indexes/*/queries'
hi = {url('HI'): '<script src="/static/js/main.test.js"></script>', 'https://search.capitol.hawaii.gov/static/js/main.test.js': hi_js,
      hi_endpoint: json.dumps({'results': [{'nbPages': 1, 'nbHits': 1, 'hits': [hi_person]}]})}
assert names('HI', hi) == ['Jane Doe']
assert roster('HI', hi)[0][0][1] == url('HI')  # Directory, not a possibly obsolete deep link.
assert r.member_key('HI', 'https://www.capitol.hawaii.gov/legislature/memberpage.aspx?member=7&year=2024') == roster('HI', hi)[0][0][0]
for change in [{'resigned': '2/13/2026'}, {'active': 'False'}, {'year': '2027'}, {'termNotes': 'Appointed 10/26/2026'}, {'termNotes': 'Unknown status'}]:
    assert names('HI', {**hi, hi_endpoint: json.dumps({'results': [{'nbPages': 1, 'nbHits': 1, 'hits': [{**hi_person, **change}]}]})}) == []
assert names('HI', {**hi, hi_endpoint: json.dumps({'results': [{'nbPages': 2, 'nbHits': 101, 'hits': [hi_person]}]})}) == []
me = '<table id="membersTable"><tr><td data-order="Doe"><a href="/house/MemberProfiles/Details/7">Jane Doe</a></td>{}</tr></table>'
for status in ['Resigned', 'Deceased', 'Tribal Representative', 'Vacancy', 'Retired']:
    assert names('ME', me.format(status)) == []
assert names('ME', me.format('')) == ['Jane Doe']
assert names('LA', '<a class="legnamelink" href="members.aspx?ID=7">Vacant — District 7</a>') == []

# Actual embedded data shapes and active/current flags.
tx = [{'link': '/members/1', 'member_name': 'Jane Doe', 'active': 1},
      {'link': '/members/2', 'member_name': 'Old Member', 'active': 0}]
assert names('TX', '<get-members :members="' + html.escape(json.dumps(tx), quote=True) + '"></get-members>') == ['Jane Doe']
nj = {'legrosterData': [[{'BioLink': '/legislative-roster/1/name', 'Full_Name': 'Jane Doe', 'VacantSort': 0},
                       {'BioLink': '/legislative-roster/2/name', 'Full_Name': 'Vacancy', 'VacantSort': 1}], [{'irrelevant': 'town data'}]]}
assert names('NJ', nextdata(nj)) == ['Jane Doe']
ok = {'isCurrent': True, 'selectedLegislature': {'current': True}, 'members': {'legislatureMembers': [
      {'attributes': {'slug': 'jane-doe', 'firstName': 'Jane', 'lastName': 'Doe', 'suffix': 'Jr.'}}]}}
assert names('OK', nextdata(ok)) == ['Jane Doe Jr.']
assert names('OK', nextdata({**ok, 'isCurrent': False})) == []
vt = {'data': [{'PersonID': 1, 'FirstName': 'Jane', 'LastName': 'Doe', 'TerminationReason': ''},
               {'PersonID': 2, 'FirstName': 'Jane', 'LastName': 'Roe', 'TerminationReason': 'Resigned'}]}
assert names('VT', json.dumps(vt)) == ['Jane Doe']
assert names('WY', json.dumps([{'legID': 1, 'name': 'Jane Doe', 'legStatus': 'C'},
                              {'legID': 2, 'name': 'Jane Roe', 'legStatus': 'H'}])) == ['Jane Doe']
ut = {'legislators': [{'id': 'DOEJA', 'house': 'H', 'formatName': 'Jane A. Doe', 'legislation': 'https://le.utah.gov/bills/billSearch.jsp?sess=2026GS'}]}
rows, _ = roster('UT', json.dumps(ut))
assert rows == [(r.member_key('UT', 'https://house.utleg.gov/rep/DOEJA/'), None, 'Jane A. Doe')]
assert names('UT', json.dumps(ut).replace('2026GS', '2027GS')) == []

# MT active flag alone must not accept future or expired office terms.
mt = {'id': 7, 'type': 'LEGISLATOR', 'isActive': True, 'startDate': '2025-01-01', 'endDate': '2026-10-26',
      'firstName': 'Jane', 'middleName': 'A.', 'lastName': 'Doe', 'links': {'profileUrl': 'https://legislators.legmt.gov/#/legislator/44'}}
rows, _ = roster('MT', json.dumps({'content': [mt]}))
assert len(rows) == 2 and all(n == 'Jane A. Doe' for _, _, n in rows)
assert rows[0][0] == r.member_key('MT', 'https://www.legmt.gov/legislators/7')
for change in [{'isActive': False}, {'startDate': '2026-10-26'}, {'endDate': '2026-10-25'}, {'type': 'STAFF'}]:
    assert names('MT', json.dumps({'content': [{**mt, **change}]})) == []
sd_session = {'SessionId': 71, 'CurrentSession': True, 'SpecialSession': False, 'Year': '2026', 'StartDate': '2026-01-13T00:00:00'}
sd = {'SessionId': 71, 'SessionMemberId': 8, 'MemberType': 'H', 'Name': 'Doe, Jane A.', 'InactiveDate': None}
sd_url = 'https://sdlegislature.gov/api/SessionMembers/Session/71'
assert names('SD', {url('SD'): json.dumps([sd_session]), sd_url: json.dumps([sd])}) == ['Doe, Jane A.']
for change in [{'InactiveDate': '2026-10-25T00:00:00'}, {'SessionId': 70}]:
    assert names('SD', {url('SD'): json.dumps([sd_session]), sd_url: json.dumps([{**sd, **change}])}) == []
assert names('SD', json.dumps([{**sd_session, 'Year': '2027'}])) == []

# SOAP status/chamber filtering and exact official URL/name association.
soap = '<root xmlns:z="#RowsetSchema"><z:row ows_ActiveFlag="1" ows_chamber="House" ows_legislator_bioURL="https://www.rilegislature.gov/representatives/doe/Pages/Biography.aspx, Bio" ows_legislator_name="Jane Doe"/>{}</root>'
rows, calls = roster('RI', soap.format('<z:row ows_ActiveFlag="0" ows_chamber="House" ows_legislator_name="Old Member"/>'))
assert len(rows) == 1 and rows[0][2] == 'Jane Doe'
assert calls[0][1] and b'GetListItems' in calls[0][1] and calls[0][2]['Content-Type'].startswith('text/xml')
assert 'SOAPAction' not in calls[0][2]
ms = '<LEGISLATURE><TERM>2024-2027</TERM><MEMBER><M1_NAME>Jane Doe</M1_NAME><M1_LINK>house/doe.xml</M1_LINK></MEMBER><CHAIR><CHAIR_NAME>Jane Roe</CHAIR_NAME><CHAIR_LINK>senate/roe.xml</CHAIR_LINK></CHAIR></LEGISLATURE>'
assert roster('MS', {'https://billstatus.ls.state.ms.us/members/hr_membs.xml': ms})[0] == []  # Term membership alone is insufficient.

# KS requests only roster-established IDs explicitly needed by the published records.
ks = '<a class="rep-card" href="/b2025_26/legislators/rep_doe_jane_1/"><span class="name">Rep. Doe</span></a>'
profile = 'https://www.kslegislature.gov/b2025_26/legislators/rep_doe_jane_1/'
key = r.member_key('KS', 'https://kslegislature.gov/li/b2025_26/members/rep_doe_jane_1/')
rows, calls = roster('KS', {url('KS'): ks, profile: '<h1>Government</h1><h2 class="leg-hero-title">Jane A. Doe</h2><h1>Resources</h1>'}, {key})
assert len(rows) == 1 and rows[0][0] == key and len(calls) == 2
assert roster('KS', {url('KS'): ks}, set())[0] == []
assert len(roster('KS', {url('KS'): ks})[1]) == 1
assert roster('KS', {url('KS'): ks, profile: '<h2 class="leg-hero-title">Doe</h2>'}, {key})[0] == []

# Pagination stays on the actual same-origin path and stops cycles.
nd = '<div class="member-wrapper"><a href="/biography/jane-doe">Representative Jane Doe</a></div><a rel="next" href="?page=1">Next</a>'
second = '<div class="member-wrapper"><a href="/biography/jane-roe">Representative Jane Roe</a></div><a rel="next" href="?page=1">Next</a>'
rows, calls = roster('ND', {url('ND'): nd, url('ND') + '?page=1': second})
assert len(rows) == 2 and len(calls) == 2
rows, calls = roster('ND', nd.replace('?page=1', 'https://evil.test/next'))
assert len(rows) == 1 and len(calls) == 1
for status in ('Resigned 8/4/2026', 'Effective through 4/9/25', 'Effective 1/7/25 - 8/4/26', 'Active 10/26/2026'):
    assert names('ND', nd.replace('</div>', '<div class="member-status">' + status + '</div></div>')) == []
for status in ('Effective 6/2/26', 'Active 3/10/2025'):
    assert names('ND', nd.replace('</div>', '<div class="member-status">' + status + '</div></div>')) == ['Representative Jane Doe']
# TN default/future ga=115 must not leak into explicit ga=114.
tn = '<a href="Member?district=H7&amp;ga=114">Doe, Jane</a><a href="Member?district=H8&amp;ga=115">Future Person</a>'
assert names('TN', tn) == ['Doe, Jane']

# Structural names exclude unrelated labels and preserve reused district keys.
assert names('SC', '<a href="/member.php?code=1">District 1</a><a class="membername" href="/member.php?code=1">Representative Jane Doe</a>') == ['Representative Jane Doe']
assert names('WI', '<a href="https://legis.wisconsin.gov/assembly/1/doe">Doe, Jane</a><a href="https://legis.wisconsin.gov/assembly/1/doe">Website</a>') == ['Doe, Jane']
assert names('NV', '<span style="padding-left: 0.2rem"><a href="/App/Legislator/A/Assembly/Current/1">Doe, Jane A.<br>Majority Whip</a></span>') == ['Doe, Jane A.']
assert names('NY', '<a href="/mem/leadership">View Assembly Leadership</a><a href="/mem/Jane-Doe">Jane Doe District 1</a>') == ['Jane Doe']

# Indiana's advertised base URL applies to both member and pagination hrefs.
inbase = 'https://www.indianahouserepublicans.com/'
inpage = '<base href="' + inbase + '"><a class="member-link" href="members/jane-doe"><h2>Jane Doe</h2></a><a href="index.php?view=members&amp;pos=30,30,70">Next</a>'
insecond = '<a class="member-link" href="/members/jane-roe"><h2>Jane Roe</h2></a>'
rows, calls = roster('IN', {url('IN', 3): inpage, inbase + 'index.php?view=members&pos=30,30,70': insecond})
assert len(rows) == 2 and rows[0][1] == inbase + 'members/jane-doe'
assert len(calls) == 5
assert r.member_key('TN', 'https://capitol.tn.gov/house/members/h07.html#one') != r.member_key('TN', 'https://capitol.tn.gov/house/members/h07.html#two')
assert r.member_key('ID', 'https://legislature.idaho.gov/house/membership/2026/id12/#modal-popup-13') is None
al_member = {'id': 12, 'fullName': 'Jane Doe', 'body': 'House', 'active': True, 'elected': True}
al = {'data': {'currentSession': {'abbreviation': '2026RS'}, 'legislativeMembers': {'data': [al_member]}}}
rows, _ = roster('AL', json.dumps(al))
assert rows[0][0] == r.member_key('AL', 'https://www.legislature.state.al.us/aliswww/ISD/ALRepresentative.aspx?OID_SPONSOR=12')
assert rows[0][0] != r.member_key('AL', rows[0][1]), 'shared directory is not a person identity'
assert names('AL', json.dumps(al).replace('2026RS', '2027RS')) == []
assert names('AL', json.dumps(al).replace('"elected": true', '"elected": false')) == []
mi = [{'website': 'https://senator.example.gov', 'firstName': 'Jane', 'middleInitial': 'A.', 'lastName': 'Doe', 'suffix': 'Jr.'}]
assert names('MI', '<div senatorInfo="' + html.escape(json.dumps(mi), quote=True) + '"></div>') == ['Jane A. Doe Jr.']
or_data = {'Row': [{'MemberContact': '<h3><a href="/doe">Representative Jane Doe</a></h3><a href="/map">Map</a>'}]}
assert names('OR', '<script>var WPQ3ListData = ' + json.dumps(or_data) + ';</script>') == ['Representative Jane Doe']
nh = '<select id="pageBody_ddlReps"><option value="0">Select</option><option value="7">Doe, Jane</option></select>'
rows, _ = roster('NH', nh)
assert rows == [(r.member_key('NH', 'https://gc.nh.gov/house/members/member.aspx?pid=7'), None, 'Doe, Jane')]
ct = '<table><tr><td>1</td><td><a href="/bio">Doe, Jane</a></td><td><a href="https://housedems.ct.gov/doe">Website</a></td></tr></table>'
rows, _ = roster('CT', ct)
assert rows[0][1:] == ('https://housedems.ct.gov/doe', 'Doe, Jane')
ca = '<li class="members-list__member"><h2 class="members-list__name">Doe, Jane</h2><a class="members-list__details" href="/assemblymembers/1">Details</a></li>'
assert names('CA', ca) == ['Doe, Jane']
assert names('CA', ca + ca.replace('Doe, Jane', 'Roe, Jane')) == []
assert names('MN', {url('MN'): '<a href="/members/profile/1">Jane Doe</a>', url('MN', 1): '{malformed'}) == ['Jane Doe']
# Session end is not the end of office; MT verifies actual office dates instead.
assert len(roster('MT', json.dumps({'content': [mt]}), session={**SESSION, 'year_end': 2025})[0]) == 2
classic = '<table summary="RepresentativeList "><tr><td><h3><a href="/doe">Representative Jane Doe</a></h3></td></tr></table>'
assert names('OR', classic) == ['Representative Jane Doe']
assert names('OR', classic.replace('RepresentativeList', 'FormerMembers')) == []
assert names('MO', {url('MO', 1): '<a href="/Senators/Member?id=1">Senator Jane Doe District 1</a>'}) == ['Senator Jane Doe']

# Georgia's public frontend identifies the current session and explicitly marks vacated offices.
ga_sessions = [{'id': 1033, 'isCurrent': True, 'description': '2025-2026 Regular Session'}]
ga_member = {'id': 7, 'sessionId': 1033, 'district': {'chamberType': 1}, 'fullName': 'Jane Doe', 'dateVacated': None}
ga_members = [ga_member, {**ga_member, 'id': 8, 'dateVacated': '2026-10-25T00:00:00'},
              {**ga_member, 'id': 9, 'sessionId': 1031}]
def ga_fetch(target, data=None, headers=None):
    if '/authentication/token?' in target:
        return json.dumps('anonymous-test-token')
    assert headers == {'Authorization': 'Bearer anonymous-test-token'}
    return json.dumps(ga_sessions if target.endswith('/sessions') else ga_members)
with patch.object(r.datetime, 'date', Today):
    rows = r.members('GA', SESSION, ga_fetch)
    assert len(rows) == 1 and rows[0][2] == 'Jane Doe'
    assert rows[0][0] == r.member_key('GA', 'https://www.legis.ga.gov/members/house/7?session=1031')
    ga_sessions[0]['description'] = '2027-2028 Regular Session'
    assert r.members('GA', SESSION, ga_fetch) == []
az = '<table id="HouseRoster"><tr><td><a class="roster-tooltip" href="https://www.azleg.gov/House/House-member/?legislature=57&amp;session=130&amp;legislator=7">Jane Doe -- Speaker</a></td>{}</tr></table>'
assert names('AZ', az.format('')) == ['Jane Doe']
assert names('AZ', az.format('<td>Resigned April 17</td>')) == []
assert r.member_key('AZ', 'https://azleg.gov/house-member/?legislature=57&session=129&legislator=7') == roster('AZ', az.format(''))[0][0][0]
assert r.member_key('AZ', 'https://azleg.gov/house-member/?legislature=56&session=128&legislator=7') != roster('AZ', az.format(''))[0][0][0]
ny = '<div class="view-display-id-senators_list"><a href="https://www.nysenate.gov/senators/jane-doe"><div class="c-senator-block"><h2 class="nys-senator--name">Mr. Jane Doe</h2></div></a></div>'
assert names('NY', {url('NY', 1): ny}) == ['Mr. Jane Doe']
assert r.member_name('Mr. Jane Doe') == 'jane doe'
nh_senate = '<div class="card"><h5 class="card-header">District 14 - Senator Jane Doe</h5><a href="/senate/members/webpages/district14.aspx">Senator Doe</a></div>'
assert names('NH', {url('NH', 1): nh_senate}) == ['Jane Doe']
nh_nested = '<div class="card">' + nh_senate + nh_senate.replace('district14', 'district15').replace('Jane Doe', 'Jane Roe') + '</div>'
assert names('NH', {url('NH', 1): nh_nested}) == ['Jane Doe', 'Jane Roe']
mi_house = '<p>current term expiring on December 31, 2026.</p><li class="page-search-container"><a class="page-search-target" href="/repdetail/rep1">Doe, Rep. Jane (Democrat) District-1</a></li>'
assert names('MI', {url('MI', 1): mi_house}) == ['Doe, Jane']
assert names('MI', {url('MI', 1): mi_house.replace('2026', '2028')}) == []
il = '<h1>104th General Assembly (2025-2026)</h1><table id="myTable"><tr><td><a href="/House/Members/Details/7">Jane Doe</a></td></tr></table><h2>Former Members</h2><table id="myTable"><tr><td><a href="/House/Members/Details/8">Jane Roe</a></td></tr></table>'
assert names('IL', il) == ['Jane Doe']
assert names('IL', il.replace('2025-2026', '2027-2028')) == []
assert r.member_key('IL', 'https://ilga.gov/House/Members/Details/7#:~:text=Bills') == roster('IL', il)[0][0][0]
assert r.member_key('IL', 'https://ilga.gov/Senate/Members/Details/7') != roster('IL', il)[0][0][0]
ms_frame = 'https://www.legislature.ms.gov/member/?fiscalyear=26&chamber=H'
ms_xml = 'https://billstatus.ls.state.ms.us/members/house/Doe.xml'
ms_card = '<div class="card active-member" data-fiscal_year="26" data-chamber="H" data-mem_name="DOE  " data-district=" 7" data-customURL=""><div class="flex-grow-1">Jane Doe</div></div>'
ms_bodies = {url('MS'): '<iframe src="' + ms_frame.replace('&', '&amp;') + '"></iframe>', ms_frame: '<h1>2026 Regular Session</h1>' + ms_card,
             ms_xml: '<MEMBINFO><U_MEM_NAM>DOE</U_MEM_NAM><DISP_NAME>Jane Doe</DISP_NAME><DISTRICT>7</DISTRICT></MEMBINFO>'}
ms_key = r.member_key('MS', ms_xml)
rows, calls = roster('MS', ms_bodies, {ms_key})
assert len(rows) == 2 and rows[1][0] == ms_key and all(n == 'Jane Doe' for _, _, n in rows)
assert len(roster('MS', ms_bodies, set())[0]) == 1  # No unneeded biography request.
for change in [ms_bodies[ms_xml].replace('DOE', 'OTHER'), ms_bodies[ms_xml].replace('Jane Doe', 'Jane Q. Doe'), ms_bodies[ms_xml].replace('>7<', '>8<')]:
    assert all(key != ms_key for key, _, _ in roster('MS', {**ms_bodies, ms_xml: change}, {ms_key})[0])
for change in [ms_card.replace('active-member', 'vacancy'), ms_card.replace('data-customURL=""', 'data-customURL="https://executive.example.gov"'), ms_card.replace('data-district=" 7"', 'data-district="0"')]:
    assert names('MS', {**ms_bodies, ms_frame: '<h1>2026 Regular Session</h1>' + change}) == []
print('state_rosters: all checks passed')
