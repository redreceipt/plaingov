// Where-to-vote lookup, in the browser: address -> Google Civic Information API; address <-> coordinates via OpenStreetMap.
const form = document.getElementById("where"), out = document.getElementById("result");
const el = (tag, text, props) => Object.assign(document.createElement(tag), {textContent: text || "", ...props});
const say = (...nodes) => { out.replaceChildren(...nodes); out.scrollIntoView({block: "nearest", behavior: "smooth"}); };  // on phones the results start below the fold
let coords = null;  // the reader's location, to list the nearest of a county's many vote centers first

// shortcut: OpenStreetMap's free geocoder allows ~1 request/second; switch to a paid geocoder if traffic outgrows it.
const osm = async (path, q) => (await fetch(`https://nominatim.openstreetmap.org/${path}?` + new URLSearchParams({format: "jsonv2", addressdetails: 1, ...q}))).json();
const state = a => (a["ISO3166-2-lvl4"] || "").replace("US-", "");
// An OpenStreetMap address as one line Google can read: "315 South Main Street, Greenville, SC 29601".
const line = a => [[a.house_number, a.road].filter(Boolean).join(" "), a.city || a.town || a.village || a.hamlet, [state(a), a.postcode].filter(Boolean).join(" ")].filter(Boolean).join(", ");
const unparsed = r => r.error?.errors?.[0]?.reason === "parseError";  // Google only reads street addresses, not ZIP codes or towns

// Hours come one day per line ("Mon, Oct 19: 8:30 am - 6:00 pm"); keep today and later. Null when every dated day has passed.
function upcoming(hours) {
  const today = new Date().setHours(0, 0, 0, 0), lines = (hours || "").split("\n").filter(Boolean);
  const left = lines.filter(l => { const m = l.match(/^\w{3}, (\w{3} \d{1,2}):/); return !m || new Date(`${m[1]}, ${new Date().getFullYear()}`) >= today; });
  return lines.length && !left.length ? null : left.slice(0, 3).join("\n") + (left.length > 3 ? "\n…and more days" : "");
}

function place(p) {
  const a = p.address || {}, where = [a.line1, a.line2, a.line3, [a.city, a.state, a.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  const li = el("li");
  li.append(el("strong", a.locationName || p.name || where), el("span", where, {className: "meta"}));
  if (p.hours) li.append(el("span", p.hours, {className: "meta"}));
  if (p.notes) li.append(el("span", p.notes, {className: "meta"}));
  li.append(el("a", "Directions →", {href: "https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(where)}));
  return li;
}

function nearest(list) {
  const dist = p => coords && p.latitude ? (p.latitude - coords[0]) ** 2 + ((p.longitude - coords[1]) * Math.cos(coords[0] * Math.PI / 180)) ** 2 : 0;
  return (list || []).map(p => ({...p, hours: upcoming(p.pollingHours)})).filter(p => p.hours !== null).sort((a, b) => dist(a) - dist(b)).slice(0, 3);
}

// near: the ZIP code or town typed, when we only know the area (Election Day polling places depend on the exact address).
function show(r, st, near) {
  r = r || {};
  const input = r.normalizedInput || {};
  st = input.state || st;
  const body = (r.state || [{}])[0].electionAdministrationBody || {};
  const links = document.querySelectorAll("a[id^=vote-]");
  const official = (document.getElementById("vote-" + st) || (links.length === 1 && links[0]) || {}).href || body.votingLocationFinderUrl;
  const check = official ? el("a", "your state's official lookup", {href: official}) : "your state's official lookup below";
  const sections = [!near && ["Your polling place on Election Day", r.pollingLocations], ["Vote early", r.earlyVoteSites], ["Drop off a mail ballot", r.dropOffLocations]]
    .filter(Boolean).map(([title, list]) => [title, nearest(list), (list || []).length]).filter(([, list]) => list.length);
  if (!sections.length) {
    const p = el("p", near ? "We don't have early voting or drop box locations near there. Enter your full street address, or try "
      : unparsed(r) ? "We couldn't find that address. Check it, or try " : "We don't have voting locations for that address yet. Try ");
    p.append(check, ".");
    return say(p);
  }
  const nodes = [el("p", near ? `Near ${near}. For your Election Day polling place, enter your street address above.`
    : "For " + [input.line1, input.city, st].filter(Boolean).join(", ") + ". Not your home address? Change it above.", {className: "meta"})];
  for (const [title, list, total] of sections) {
    const ul = el("ul", "", {className: "list"});
    ul.append(...list.map(place));
    nodes.push(el("h2", title + (total > list.length ? ` (${coords ? "nearest" : "first"} ${list.length} of ${total})` : "")), ul);
  }
  const p = el("p", "Locations can change. Double-check with ", {className: "meta"});
  p.append(check, ".");
  say(...nodes, p);
}

async function lookup(address, electionId) {
  const q = new URLSearchParams({address, key: form.dataset.key});
  if (electionId) q.set("electionId", electionId);
  return (await fetch("https://www.googleapis.com/civicinfo/v2/voterinfo?" + q)).json();  // {error} when there's no data or it isn't an address
}

async function find(address) {
  const r = await lookup(address);
  // Several elections on the same day return no locations until one is picked.
  return !r.pollingLocations && !r.earlyVoteSites && !r.dropOffLocations && r.otherElections && r.election ? lookup(address, r.election.id) : r;
}

form.address.oninput = () => coords = null;
form.onsubmit = async ev => {
  ev.preventDefault();
  form.address.blur();  // closes the phone keyboard so the results show
  say(el("p", "Looking up where you vote…"));
  const address = form.address.value.trim();
  const geo = coords ? null : osm("search", {q: address, countrycodes: "us", limit: 1}).then(([m]) => m).catch(() => null);
  try {
    const first = find(address), m = await geo;
    if (m) coords = [+m.lat, +m.lon];
    let r = await first;
    // A ZIP code or town: look up the street address at its middle for the early voting and drop boxes that serve the area.
    const near = unparsed(r) && coords && address;
    if (near) r = await find(line((await osm("reverse", {zoom: 18, lat: coords[0], lon: coords[1]})).address));
    show(r, m && state(m.address), near);
  } catch {
    show(null);
  }
};

document.getElementById("locate").onclick = () => {
  const fail = () => say(el("p", "We couldn't find your location. Type your address instead."));
  if (!navigator.geolocation) return fail();
  say(el("p", "Finding your address…"));
  navigator.geolocation.getCurrentPosition(async pos => {
    try {
      const a = (await osm("reverse", {zoom: 18, lat: pos.coords.latitude, lon: pos.coords.longitude})).address;
      form.address.value = line(a);
      coords = [pos.coords.latitude, pos.coords.longitude];
      if (a.house_number) return form.requestSubmit();
      say(el("p", "Add your house number, then tap “Find where I vote.”"));
      form.address.focus();
      form.address.setSelectionRange(0, 0);
    } catch {
      fail();
    }
  }, fail, {timeout: 15000, maximumAge: 600000});
};
