// Where-to-vote lookup, in the browser: address -> Google Civic Information API; location -> address via OpenStreetMap.
const form = document.getElementById("where"), out = document.getElementById("result");
const el = (tag, text, props) => Object.assign(document.createElement(tag), {textContent: text || "", ...props});
const say = (...nodes) => out.replaceChildren(...nodes);
const day = d => new Date(d + "T12:00").toLocaleDateString("en-US", {month: "short", day: "numeric"});
const found = r => r && ["pollingLocations", "earlyVoteSites", "dropOffLocations"].some(k => r[k] && r[k].length);

function place(p) {
  const a = p.address || {}, where = [a.line1, a.line2, a.line3, [a.city, a.state, a.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  const li = el("li");
  li.append(el("strong", a.locationName || p.name || where), el("span", where, {className: "meta"}));
  const dates = p.startDate && p.endDate && p.startDate !== p.endDate ? day(p.startDate) + " – " + day(p.endDate) : "";
  for (const t of [dates, p.pollingHours, p.notes]) if (t) li.append(el("span", t, {className: "meta"}));
  li.append(el("a", "Directions →", {href: "https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(where)}));
  return li;
}

function show(r) {
  const input = (r && r.normalizedInput) || {}, st = input.state || "";
  const body = ((r && r.state) || [{}])[0].electionAdministrationBody || {};
  const official = (document.getElementById("vote-" + st) || {}).href || body.votingLocationFinderUrl || body.electionInfoUrl;
  const check = official ? el("a", "your state's official lookup", {href: official}) : "your local election office";
  if (!found(r)) {
    const p = el("p", r ? "We don't have voting locations for that address yet. Try " : "We couldn't find that address. Check it, or try ");
    p.append(check, ".");
    return say(p);
  }
  const nodes = [el("p", "For " + [input.line1, input.city, st].filter(Boolean).join(", ") + ". Not your home address? Change it above.", {className: "meta"})];
  const today = new Date().toLocaleDateString("en-CA");  // YYYY-MM-DD, local
  for (let [title, list] of [["Your polling place on Election Day", r.pollingLocations], ["Vote early", r.earlyVoteSites], ["Drop off a mail ballot", r.dropOffLocations]]) {
    list = (list || []).filter(p => !p.endDate || p.endDate >= today);
    if (!list.length) continue;
    const ul = el("ul", "", {className: "list"});
    ul.append(...list.slice(0, 3).map(place));
    nodes.push(el("h2", title), ul);
  }
  const p = el("p", "Locations can change. Double-check with ", {className: "meta"});
  p.append(check, ".");
  say(...nodes, p);
}

async function lookup(address, electionId) {
  const q = new URLSearchParams({address, key: form.dataset.key});
  if (electionId) q.set("electionId", electionId);
  const res = await fetch("https://www.googleapis.com/civicinfo/v2/voterinfo?" + q);
  return res.ok ? res.json() : null;
}

form.onsubmit = async ev => {
  ev.preventDefault();
  say(el("p", "Looking up where you vote…"));
  try {
    let r = await lookup(form.address.value);
    // Several elections on the same day return no locations until one is picked.
    if (r && !found(r) && r.otherElections && r.election) r = await lookup(form.address.value, r.election.id);
    show(r);
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
      // shortcut: OpenStreetMap's free geocoder allows ~1 request/second; switch to a paid geocoder if traffic outgrows it.
      const q = new URLSearchParams({format: "jsonv2", addressdetails: 1, zoom: 18, lat: pos.coords.latitude, lon: pos.coords.longitude});
      const a = (await (await fetch("https://nominatim.openstreetmap.org/reverse?" + q)).json()).address;
      const st = (a["ISO3166-2-lvl4"] || "").replace("US-", "");
      form.address.value = [[a.house_number, a.road].filter(Boolean).join(" "), a.city || a.town || a.village || a.hamlet, [st, a.postcode].filter(Boolean).join(" ")].filter(Boolean).join(", ");
      if (a.house_number) return form.requestSubmit();
      say(el("p", "Add your house number, then tap “Find where I vote.”"));
      form.address.focus();
      form.address.setSelectionRange(0, 0);
    } catch {
      fail();
    }
  }, fail, {timeout: 15000, maximumAge: 600000});
};
