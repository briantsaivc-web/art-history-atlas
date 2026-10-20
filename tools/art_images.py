#!/usr/bin/env python3
"""art_images: find images for works with empty `img` (resumable; all HTTP GETs cached).

Stages (run all by default):  python tools/art_images.py [match|download|patch|all]
  match    : Wikidata (P170 + title) -> P18 / Commons SDC (P6243/P180) / Wikipedia pageimages
             -> museum open APIs (Met, AIC, Cleveland, SMK) ; writes _work/art_images/results.json
  download : high-confidence -> img/800 + img/thumb ; medium -> _work/art_images/review/
  patch    : writes tools/patches/100_art_images.json and 110_art_links.json
Data is read from `git show HEAD:data/...` so concurrent test-applies by others don't matter.
"""
import json, os, re, sys, time, hashlib, subprocess, unicodedata, threading, io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote
import requests
from rapidfuzz import fuzz

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = r'C:\Users\Carrie\.gemini\antigravity\scratch\atlas-work\_work\art_images'
CACHE = os.path.join(W, 'cache')
os.makedirs(CACHE, exist_ok=True)
UA = 'AtlasEnhancer/1.0 (briantsai.vc@gmail.com)'
TODAY = '2026-10-20'
S = requests.Session()
S.headers['User-Agent'] = UA
_sem = threading.Semaphore(4)


def gitjson(name):
    out = subprocess.run(['git', 'show', 'HEAD:data/' + name], capture_output=True, cwd=R).stdout
    return json.loads(out.decode('utf-8'))


def get(url, params=None, kind='json', data=None, tries=6):
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True) + str(data)).encode()).hexdigest()
    fp = os.path.join(CACHE, key[:2], key + '.json')
    if os.path.exists(fp):
        with open(fp, encoding='utf-8') as f:
            return json.load(f)
    res = None
    for i in range(tries):
        try:
            with _sem:
                if data is not None:
                    r = S.post(url, data=data, params=params, timeout=90)
                else:
                    r = S.get(url, params=params, timeout=60)
            if r.status_code in (429, 503, 502, 504):
                wait = int(r.headers.get('Retry-After', 0) or 0) or (5 * 2 ** i)
                time.sleep(min(wait, 120)); continue
            if r.status_code == 404:
                res = None; break
            if r.status_code >= 400:
                res = {'__error__': r.status_code}; break
            res = r.json() if kind == 'json' else r.text
            break
        except Exception as e:
            time.sleep(3 * (i + 1)); res = {'__error__': str(e)}
    if isinstance(res, dict) and '__error__' in res:
        return res  # don't cache errors
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    return res


def sparql(q):
    r = get('https://query.wikidata.org/sparql', data={'query': q, 'format': 'json'})
    if not r or '__error__' in r:
        return None
    return r['results']['bindings']


# ---------------------------------------------------------------- normalisation
STOP = {'the', 'a', 'an', 'of', 'le', 'la', 'les', 'de', 'du', 'des', 'il', 'lo', 'el', 'der', 'die', 'das',
        'het', 'een', 'and', 'et', 'e', 'y', 'und', 'en', 'in', 'at', 'with', 'on', 'painting'}


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"[’'`]", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(t for t in s.split() if t not in STOP)


def title_variants(t):
    v = {t}
    v.add(re.sub(r'\s*\([^)]*\)', '', t))          # drop parenthetical
    for sep in [' – ', ' - ', ': ', ', or ', ' or ']:
        if sep in t:
            v.update(p for p in t.split(sep))
    return {norm(x) for x in v if norm(x)}


def tsim(ours, labels):
    best = 0
    ov = title_variants(ours)
    for l in labels:
        nl = norm(l)
        if not nl:
            continue
        for o in ov:
            s = max(fuzz.ratio(o, nl), fuzz.token_sort_ratio(o, nl))
            # token_set only when both are multi-word (avoid "portrait" ⊂ "portrait of x")
            if len(o.split()) >= 2 and len(nl.split()) >= 2:
                ts = fuzz.token_set_ratio(o, nl)
                if min(len(o.split()), len(nl.split())) / max(len(o.split()), len(nl.split())) >= 0.6:
                    s = max(s, ts - 3)
            best = max(best, s)
    return best


def years(y):
    if y is None:
        return None
    if isinstance(y, (list, tuple)):
        ys = [int(x) for x in y if x is not None]
        return (min(ys), max(ys)) if ys else None
    s = str(y)
    bc = bool(re.search(r'\bBC|BCE|前', s))
    nums = [int(n) for n in re.findall(r'\d{3,4}', s)]
    # 1885–86 style
    m = re.search(r'(\d{4})\s*[–-]\s*(\d{2})\b(?!\d)', s)
    if m:
        nums = [int(m.group(1)), int(m.group(1)[:2] + m.group(2))]
    if not nums:
        c = re.search(r'(\d{1,2})(st|nd|rd|th) century', s)
        if c:
            n = int(c.group(1)); nums = [(n - 1) * 100, n * 100]
    if not nums:
        return None
    if bc:
        nums = [-n for n in nums]
    return (min(nums), max(nums))


def ydiff(a, b):
    if not a or not b:
        return None
    if a[1] < b[0]:
        return b[0] - a[1]
    if b[1] < a[0]:
        return a[0] - b[1]
    return 0


MU_STOP = {'museum', 'musee', 'museo', 'museu', 'museums', 'musees', 'gallery', 'galleria', 'galerie', 'gallerie',
           'national', 'nationale', 'nazionale', 'nacional', 'of', 'the', 'de', 'du', 'des', 'del', 'della', 'di',
           'art', 'arts', 'fine', 'beaux', 'kunst', 'and', 'for', 'collection', 'city', 'royal', 'state', 'staatliche',
           'kunstmuseum', 'pinacoteca', 'modern', 'moderna', 'contemporary', 'new', 'york', 'paris', 'london', 'rome',
           'madrid', 'florence', 'firenze', 'venice', 'museum', 'mus', 'stedelijk', 'galleries', 'muzeum'}
ALIAS = {'moma': 'museum of modern art', 'louvre museum': 'louvre', 'musee du louvre': 'louvre', 'uffizi': 'uffizi',
         'met': 'metropolitan'}


def mu_norm(s, strip=True):
    s = norm(s.split(',')[0] if s else '')
    s = ALIAS.get(s, s)
    return ' '.join(t for t in s.split() if t not in MU_STOP) if strip else s


def mu_match(ours, colls):
    """'ok' / 'bad' / 'na'"""
    if not ours or re.search(r'private|unknown|lost|destroyed|various', ours, re.I) or not colls:
        return 'na'
    o_full, o = mu_norm(ours, False), mu_norm(ours)
    best = 0
    for c in colls:
        c_full, c2 = mu_norm(c, False), mu_norm(c)
        s = fuzz.ratio(o_full, c_full)
        if o and c2:
            s = max(s, fuzz.ratio(o, c2), 100 if (o in c2.split() or c2 in o.split()) and len(min(o, c2, key=len)) > 4 else 0)
        best = max(best, s)
    if best >= 85:
        return 'ok'
    if best < 45:
        return 'bad'
    return 'na'


# ---------------------------------------------------------------- data
works = gitjson('works.json')
artists = gitjson('artists.json')
WID = {w['id']: w for w in works}
targets = [w for w in works if not w.get('img')]
REMOVED = {w['id'] for w in targets if '經 Wikidata 比對為其他作品' in (w.get('verif') or '')}


def load_j(name, default):
    fp = os.path.join(W, name)
    if os.path.exists(fp):
        with open(fp, encoding='utf-8') as f:
            return json.load(f)
    return default


def save_j(name, obj):
    fp = os.path.join(W, name)
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)


# ---------------------------------------------------------------- artist QIDs
def resolve_artists():
    amap = load_j('artist_qids.json', {})
    need = sorted({w['a'] for w in targets} - set(amap))
    if not need:
        return amap
    # vote from existing wd.q of all works of each artist
    qs = {}
    for w in works:
        if w['a'] in need and w.get('wd', {}).get('q'):
            qs.setdefault(w['a'], []).append(w['wd']['q'])
    allq = sorted({q for l in qs.values() for q in l})
    p170 = {}
    for i in range(0, len(allq), 300):
        vals = ' '.join('wd:' + q for q in allq[i:i + 300])
        b = sparql(f'SELECT ?w ?a WHERE {{ VALUES ?w {{ {vals} }} ?w wdt:P170 ?a }}') or []
        for x in b:
            p170.setdefault(x['w']['value'].rsplit('/', 1)[1], []).append(x['a']['value'].rsplit('/', 1)[1])
    from collections import Counter
    for a in need:
        c = Counter(aq for q in qs.get(a, []) for aq in p170.get(q, []))
        if c and c.most_common(1)[0][1] >= 2:
            amap[a] = {'q': c.most_common(1)[0][0], 'how': 'vote'}
    # remaining: search + birth/death check
    for a in need:
        if a in amap:
            continue
        info = artists.get(a, {})
        r = get('https://www.wikidata.org/w/api.php', {'action': 'wbsearchentities', 'search': a, 'language': 'en',
                                                        'type': 'item', 'limit': 10, 'format': 'json'})
        cands = [x['id'] for x in (r or {}).get('search', [])]
        if not cands:
            amap[a] = {'q': None, 'how': 'notfound'}; continue
        ents = item_info(cands)
        best = None
        for q in cands:
            e = ents.get(q, {})
            if 'Q5' not in claim_vals(e, 'P31'):
                continue
            bys = [v for v in claim_vals(e, 'P569') if isinstance(v, int)]
            dys = [v for v in claim_vals(e, 'P570') if isinstance(v, int)]
            okb = not info.get('birth') or any(abs(v - info['birth']) <= 3 for v in bys)
            okd = not info.get('death') or any(abs(v - info['death']) <= 3 for v in dys)
            if okb and okd and (bys or dys or not info.get('birth')):
                best = q; break
        amap[a] = {'q': best, 'how': 'search' if best else 'nomatch'}
    save_j('artist_qids.json', amap)
    return amap


def artist_works(aq):
    """all Wikidata items with P170 = aq (cached)."""
    q = f'''SELECT ?w (GROUP_CONCAT(DISTINCT ?lab;separator="|") AS ?labs) (SAMPLE(?img) AS ?p18)
      (GROUP_CONCAT(DISTINCT STR(YEAR(?inc));separator="|") AS ?yrs) WHERE {{
      ?w wdt:P170 wd:{aq} .
      OPTIONAL{{?w rdfs:label|skos:altLabel ?lab FILTER(LANG(?lab) IN ("en","fr","de","it","es","nl","mul","pt","ca"))}}
      OPTIONAL{{?w wdt:P18 ?img}} OPTIONAL{{?w wdt:P571 ?inc}} }} GROUP BY ?w'''
    b = sparql(q)
    if b is None:
        return None
    q2 = f'''SELECT ?w (GROUP_CONCAT(DISTINCT ?cl;separator="|") AS ?colls) WHERE {{ ?w wdt:P170 wd:{aq} ; wdt:P195 ?c .
      ?c rdfs:label ?cl FILTER(LANG(?cl) IN ("en","mul")) }} GROUP BY ?w'''
    b2 = sparql(q2) or []
    colls = {x['w']['value'].rsplit('/', 1)[1]: x['colls']['value'].split('|') for x in b2}
    out = []
    for x in b:
        qid = x['w']['value'].rsplit('/', 1)[1]
        out.append({'q': qid, 'labels': [l for l in x.get('labs', {}).get('value', '').split('|') if l],
                    'p18': unquote(x['p18']['value'].rsplit('/', 1)[1]) if 'p18' in x else None,
                    'years': [int(v) for v in x.get('yrs', {}).get('value', '').split('|') if re.fullmatch(r'-?\d+', v)],
                    'colls': colls.get(qid, [])})
    return out


def item_info(qids):
    """labels/claims/sitelinks for given qids (wbgetentities, cached in batches)."""
    out = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 50):
        r = get('https://www.wikidata.org/w/api.php', {'action': 'wbgetentities', 'ids': '|'.join(qids[i:i + 50]),
                                                        'props': 'labels|aliases|claims|sitelinks', 'format': 'json'})
        out.update((r or {}).get('entities', {}))
    return out


def claim_vals(ent, p):
    vals = []
    for c in ent.get('claims', {}).get(p, []):
        dv = c.get('mainsnak', {}).get('datavalue', {}).get('value')
        if isinstance(dv, dict) and 'id' in dv:
            vals.append(dv['id'])
        elif isinstance(dv, dict) and 'time' in dv:
            m = re.match(r'([+-]\d+)-', dv['time'])
            if m:
                vals.append(int(m.group(1)))
        elif dv is not None:
            vals.append(dv)
    return vals


def labels_of(qids):
    ents = item_info(qids)
    return {q: (e.get('labels', {}).get('en') or e.get('labels', {}).get('mul') or next(iter(e.get('labels', {}).values()), {})).get('value') for q, e in ents.items()}


# ---------------------------------------------------------------- Commons helpers
def commons_by_statement(q):
    res = []
    for prop in ('P6243', 'P180'):
        r = get('https://commons.wikimedia.org/w/api.php', {'action': 'query', 'list': 'search', 'srnamespace': 6,
                                                             'srsearch': f'haswbstatement:{prop}={q}', 'srlimit': 10, 'format': 'json'})
        for x in (r or {}).get('query', {}).get('search', []):
            res.append((prop, x['title'][5:]))
    return res


def file_meta(fn):
    r = get('https://commons.wikimedia.org/w/api.php', {'action': 'query', 'titles': 'File:' + fn, 'prop': 'imageinfo',
                                                         'iiprop': 'url|size|mime|extmetadata', 'format': 'json'})
    for p in (r or {}).get('query', {}).get('pages', {}).values():
        if 'imageinfo' in p:
            ii = p['imageinfo'][0]
            em = {k: re.sub('<[^>]+>', '', str(v.get('value', ''))) for k, v in ii.get('extmetadata', {}).items()}
            return {'url': ii['url'], 'w': ii.get('width'), 'h': ii.get('height'), 'mime': ii.get('mime'), 'em': em}
    return None


def commons_text_search(w):
    r = get('https://commons.wikimedia.org/w/api.php', {'action': 'query', 'list': 'search', 'srnamespace': 6,
                                                         'srsearch': f'{w["t"]} {w["a"]}', 'srlimit': 10, 'format': 'json'})
    return [x['title'][5:] for x in (r or {}).get('query', {}).get('search', [])]


def file_sdc(fns):
    """{filename: {'P6243':[..],'P180':[..],'P170':[..]}}"""
    out = {}
    for i in range(0, len(fns), 40):
        r = get('https://commons.wikimedia.org/w/api.php', {'action': 'wbgetentities', 'sites': 'commonswiki',
                                                             'titles': '|'.join('File:' + f for f in fns[i:i + 40]), 'format': 'json'})
        for e in (r or {}).get('entities', {}).values():
            t = e.get('title', '')[5:]
            st = e.get('statements') or {}
            if isinstance(st, list):
                st = {}
            out[t] = {p: [c['mainsnak'].get('datavalue', {}).get('value', {}).get('id') for c in st.get(p, [])
                          if isinstance(c['mainsnak'].get('datavalue', {}).get('value'), dict)] for p in ('P6243', 'P180', 'P170')}
    return out


BADFILE = re.compile(r'detail|ausschnitt|d[ée]tail|particolare|frame|copy|copie|kopie|after |nach |study|sketch|'
                     r'esquisse|print|engraving|gravure|stich|lithograph|replica|x-?ray|infrared|reverse|verso|back|'
                     r'room|hall|saal|installation|exhibition|museum interior|crop', re.I)


def pageimage(ent):
    for lang in ('en', 'fr', 'it', 'de', 'es', 'nl'):
        sl = ent.get('sitelinks', {}).get(lang + 'wiki')
        if not sl:
            continue
        r = get(f'https://{lang}.wikipedia.org/w/api.php', {'action': 'query', 'titles': sl['title'], 'prop': 'pageprops',
                                                              'ppprop': 'page_image_free', 'format': 'json'})
        for p in (r or {}).get('query', {}).get('pages', {}).values():
            f = p.get('pageprops', {}).get('page_image_free')
            if f:
                return f.replace('_', ' '), f'https://{lang}.wikipedia.org/wiki/' + quote(sl['title'].replace(' ', '_'))
    return None, None


# ---------------------------------------------------------------- museum APIs
def amatch(ours, theirs):
    if not theirs:
        return False
    o, t = norm(ours), norm(theirs)
    last = o.split()[-1] if o else ''
    return fuzz.token_set_ratio(o, t) >= 85 or (len(last) > 3 and last in t.split())


def museum_cands(w):
    mu = (w.get('mu') or '').lower()
    out = []
    if 'metropolitan' in mu:
        r = get('https://collectionapi.metmuseum.org/public/collection/v1/search', {'q': w['t'], 'hasImages': 'true'})
        for oid in ((r or {}).get('objectIDs') or [])[:25]:
            o = get(f'https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}')
            if not o or '__error__' in o or not amatch(w['a'], o.get('artistDisplayName')):
                continue
            out.append({'src': 'Met', 'title': o.get('title'), 'artist': o.get('artistDisplayName'),
                        'years': [o.get('objectBeginDate'), o.get('objectEndDate')], 'pd': o.get('isPublicDomain'),
                        'img': o.get('primaryImage'), 'page': o.get('objectURL'), 'mu': 'Metropolitan Museum of Art'})
    if 'art institute of chicago' in mu:
        r = get('https://api.artic.edu/api/v1/artworks/search', {'q': w['t'], 'limit': 20,
                'fields': 'id,title,artist_title,artist_display,date_start,date_end,is_public_domain,image_id'})
        for o in (r or {}).get('data', []):
            if not amatch(w['a'], o.get('artist_title') or o.get('artist_display')):
                continue
            out.append({'src': 'AIC', 'title': o.get('title'), 'artist': o.get('artist_title'),
                        'years': [o.get('date_start'), o.get('date_end')], 'pd': o.get('is_public_domain'),
                        'img': f"https://www.artic.edu/iiif/2/{o['image_id']}/full/843,/0/default.jpg" if o.get('image_id') else None,
                        'page': f"https://www.artic.edu/artworks/{o['id']}", 'mu': 'Art Institute of Chicago'})
    if 'cleveland' in mu:
        r = get('https://openaccess-api.clevelandart.org/api/artworks/', {'q': w['t'], 'limit': 20})
        for o in (r or {}).get('data', []):
            cr = '; '.join(c.get('description', '') for c in o.get('creators', []))
            if not amatch(w['a'], cr):
                continue
            out.append({'src': 'Cleveland', 'title': o.get('title'), 'artist': cr,
                        'years': [o.get('creation_date_earliest'), o.get('creation_date_latest')],
                        'pd': o.get('share_license_status') == 'CC0',
                        'img': ((o.get('images') or {}).get('web') or {}).get('url'), 'page': o.get('url'),
                        'mu': 'Cleveland Museum of Art'})
    if 'statens museum' in mu or 'smk' in mu:
        r = get('https://api.smk.dk/api/v1/art/search/', {'keys': w['t'], 'offset': 0, 'rows': 20, 'lang': 'en'})
        for o in (r or {}).get('items', []) or []:
            cr = '; '.join(p.get('name', '') for p in o.get('production', []) or [])
            if not amatch(w['a'], cr):
                continue
            ttl = [t.get('title') for t in o.get('titles', []) or []]
            pr = (o.get('production_date') or [{}])[0]
            out.append({'src': 'SMK', 'title': ' | '.join(t for t in ttl if t), 'artist': cr,
                        'years': [int(pr['start'][:4]) if pr.get('start') else None, int(pr['end'][:4]) if pr.get('end') else None],
                        'pd': o.get('public_domain'), 'img': o.get('image_native') or o.get('image_thumbnail'),
                        'page': f"https://open.smk.dk/artwork/image/{o.get('object_number')}", 'mu': 'Statens Museum for Kunst'})
    return out


# ---------------------------------------------------------------- matching
def classify(w, t, yd, mm, strict):
    yr = 'na' if yd is None else ('ok' if yd <= 10 else 'bad')
    score = t + {'ok': 10, 'bad': -25, 'na': 0}[yr] + {'ok': 10, 'bad': -15, 'na': 0}[mm]
    if strict:
        high = t >= 88 and yr == 'ok' and mm == 'ok'
    else:
        high = t >= 88 and yr != 'bad' and mm != 'bad' and (yr == 'ok' or mm == 'ok')
        high = high or (t >= 80 and yr == 'ok' and mm == 'ok')
    lvl = 'high' if high else ('medium' if t >= 70 and score >= 72 else 'low')
    return lvl, score, yr


def match_work(w, amap, awcache):
    strict = w['id'] in REMOVED
    wy = years(w.get('y'))
    res = {'id': w['id'], 'a': w['a'], 't': w['t'], 'y': w.get('y'), 'mu': w.get('mu'), 'pd': w['pd'],
           'strict': strict, 'q': None, 'q_level': None, 'cands': []}
    aq = (amap.get(w['a']) or {}).get('q')
    res['artist_q'] = aq
    # --- 1. Wikidata item
    qcands = []
    known = (w.get('wd') or {}).get('q')
    if aq and aq in awcache and awcache[aq]:
        for it in awcache[aq]:
            t = tsim(w['t'], it['labels'])
            if t < 65 and it['q'] != known:
                continue
            yd = ydiff(wy, years(it['years']) if it['years'] else None)
            mm = mu_match(w.get('mu'), it['colls'])
            lvl, score, yr = classify(w, t, yd, mm, strict)
            if it['q'] == known:
                lvl, score = ('high' if t >= 60 and yr != 'bad' else lvl), score + 30
            qcands.append({'q': it['q'], 'label': it['labels'][:3], 't': t, 'yr': yr, 'yd': yd, 'mu': mm,
                           'colls': it['colls'][:3], 'years': it['years'], 'p18': it['p18'], 'lvl': lvl, 'score': score})
    elif known:
        qcands.append({'q': known, 'label': [], 't': 100, 'yr': 'na', 'mu': 'na', 'p18': None, 'lvl': 'high', 'score': 130,
                       'years': [], 'colls': [], 'unverified_artist': True})
    qcands.sort(key=lambda c: -c['score'])
    if qcands:
        top = qcands[0]
        if top['lvl'] == 'high' and len(qcands) > 1 and qcands[1]['score'] > top['score'] - 8 and top['q'] != known:
            top['lvl'] = 'medium'; top['why'] = f"ambiguous with {qcands[1]['q']}"
        if top['lvl'] in ('high', 'medium'):
            res['q'], res['q_level'] = top['q'], top['lvl']
        res['qcands'] = qcands[:4]
    # --- 2. image for the item
    if res['q']:
        top = qcands[0]
        ent = item_info([res['q']]).get(res['q'], {})
        p18 = claim_vals(ent, 'P18')
        if p18:
            res['cands'].append({'via': 'Wikidata P18', 'file': p18[0], 'lvl': res['q_level']})
        else:
            for prop, fn in commons_by_statement(res['q']):
                if BADFILE.search(fn):
                    continue
                lv = res['q_level'] if prop == 'P6243' else 'medium'
                res['cands'].append({'via': f'Commons SDC {prop}={res["q"]}', 'file': fn, 'lvl': lv})
            if not res['cands']:
                f, page = pageimage(ent)
                if f and not BADFILE.search(f):
                    res['cands'].append({'via': f'Wikipedia pageimage {page}', 'file': f,
                                         'lvl': res['q_level'] if not strict else 'medium'})
    # --- 3. Commons text search (no Q, or Q without image)
    if not res['cands']:
        fns = [f for f in commons_text_search(w) if re.search(r'\.(jpe?g|png|tiff?)$', f, re.I)]
        sdc = file_sdc(fns) if fns else {}
        for fn in fns[:8]:
            if BADFILE.search(fn):
                continue
            m = file_meta(fn)
            if not m:
                continue
            em = m['em']
            s = sdc.get(fn, {})
            art_ok = (aq and aq in s.get('P170', [])) or amatch(w['a'], em.get('Artist', '')) or amatch(w['a'], fn)
            if not art_ok:
                continue
            cap = re.sub(r'\.\w+$', '', fn)
            t = max(tsim(w['t'], [em.get('ObjectName', '')]), tsim(w['t'], [cap]) - 5)
            dy = years(em.get('DateTimeOriginal', '')) or years(cap)
            yd = ydiff(wy, dy)
            desc = ' '.join([em.get('ImageDescription', ''), em.get('Credit', ''), cap, em.get('Categories', '')])
            mm = 'ok' if (w.get('mu') and mu_norm(w['mu']) and mu_norm(w['mu']) in norm(desc)) else 'na'
            lvl, score, yr = classify(w, t, yd, mm, True)   # text-only: always strict
            if s.get('P6243') and res['q'] and res['q'] in s['P6243']:
                lvl = res['q_level']
            if lvl == 'low':
                continue
            if lvl == 'high' and not (t >= 92):
                lvl = 'medium'
            res['cands'].append({'via': 'Commons search', 'file': fn, 'lvl': lvl, 't': t, 'yr': yr, 'mu': mm,
                                 'score': score, 'objname': em.get('ObjectName', '')[:120]})
        res['cands'].sort(key=lambda c: (c['lvl'] != 'high', -c.get('score', 0)))
        hs = [c for c in res['cands'] if c['lvl'] == 'high']
        if len(hs) > 1:  # several different "high" files: keep only if titles agree, otherwise downgrade
            pass
    # --- 4. museum APIs
    if not any(c['lvl'] == 'high' for c in res['cands']):
        for mc in museum_cands(w):
            if not mc.get('img') or not mc.get('pd'):
                continue
            t = tsim(w['t'], [x for x in (mc['title'] or '').split(' | ')])
            yd = ydiff(wy, years([y for y in mc['years'] if y]))
            lvl, score, yr = classify(w, t, yd, 'ok', strict)
            if lvl == 'low':
                continue
            res['cands'].append({'via': mc['src'] + ' API', 'src': mc['src'], 'img': mc['img'], 'page': mc['page'],
                                 'title': mc['title'], 'lvl': lvl, 't': t, 'yr': yr, 'score': score})
    res['cands'].sort(key=lambda c: (c['lvl'] != 'high', c['lvl'] != 'medium', -c.get('score', 0)))
    best = res['cands'][0] if res['cands'] else None
    res['level'] = best['lvl'] if best else 'none'
    return res


def stage_match():
    amap = resolve_artists()
    results = load_j('results.json', {})
    todo = [w for w in targets if w['id'] not in results]
    print(f'targets {len(targets)}, done {len(results)}, todo {len(todo)}', flush=True)
    aqs = sorted({(amap.get(w['a']) or {}).get('q') for w in todo} - {None})
    awcache = {}

    def fetch(aq):
        awcache[aq] = artist_works(aq)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(fetch, aqs))
    print('artist works fetched', len(awcache), 'failed', [a for a, v in awcache.items() if v is None], flush=True)
    lock = threading.Lock()
    n = [0]

    def run(w):
        try:
            r = match_work(w, amap, awcache)
        except Exception as e:
            import traceback; traceback.print_exc()
            return
        with lock:
            results[w['id']] = r
            n[0] += 1
            if n[0] % 25 == 0:
                save_j('results.json', results); print('matched', n[0], flush=True)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(run, todo))
    save_j('results.json', results)
    from collections import Counter
    print(Counter(r['level'] for r in results.values()))


# ---------------------------------------------------------------- download
def commons_url(fn, width=1000):
    return 'https://commons.wikimedia.org/wiki/Special:FilePath/' + quote(fn.replace('_', ' ')) + f'?width={width}'


def fetch_bytes(url):
    for i in range(6):
        try:
            with _sem:
                r = S.get(url, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * 2 ** i); continue
            if r.status_code != 200:
                return None
            return r.content
        except Exception:
            time.sleep(5)
    return None


def to_webp(data, out800, outthumb):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im = im.convert('RGB')
    for path, side, q in ((out800, 800, 80), (outthumb, 260, 75)):
        if not path:
            continue
        c = im.copy(); c.thumbnail((side, side), Image.LANCZOS)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        c.save(path, 'WEBP', quality=q)


def pd_ok(r):
    w = WID[r['id']]
    if w['pd']:
        return True
    d = artists.get(w['a'], {}).get('death')
    return bool(d) and d <= 1955


def stage_download():
    results = load_j('results.json', {})
    dl = load_j('downloads.json', {})
    rev = os.path.join(W, 'review')
    jobs = []
    for wid, r in results.items():
        if wid in dl and dl[wid].get('ok'):
            continue
        w = WID[wid]
        best = r['cands'][0] if r['cands'] else None
        # v10 items already carrying an img_ext from Wikidata P18
        if not best and w.get('img_ext') and r.get('q') == (w.get('wd') or {}).get('q'):
            best = {'via': 'existing img_ext (Wikidata P18)', 'file': unquote(w['img_ext'].split('FilePath/')[1].split('?')[0]),
                    'lvl': r.get('q_level') or 'medium'}
        if not best or best['lvl'] not in ('high', 'medium'):
            continue
        if not pd_ok(r):
            continue
        jobs.append((wid, best))

    def run(job):
        wid, best = job
        url = commons_url(best['file']) if best.get('file') else best['img']
        if best['lvl'] == 'high':
            data = fetch_bytes(url)
            if not data:
                return wid, {'ok': False, 'err': 'download', 'url': url}
            try:
                to_webp(data, os.path.join(R, 'img', '800', wid + '.webp'), os.path.join(R, 'img', 'thumb', wid + '.webp'))
            except Exception as e:
                return wid, {'ok': False, 'err': str(e), 'url': url}
            return wid, {'ok': True, 'lvl': 'high', 'url': url, 'best': best}
        else:
            u = commons_url(best['file'], 400) if best.get('file') else best['img']
            data = fetch_bytes(u)
            if data:
                try:
                    to_webp(data, os.path.join(rev, wid + '.webp'), None)
                except Exception:
                    pass
            return wid, {'ok': True, 'lvl': 'medium', 'url': url, 'best': best}
    lock = threading.Lock()
    with ThreadPoolExecutor(4) as ex:
        for i, (wid, v) in enumerate(ex.map(run, jobs)):
            with lock:
                dl[wid] = v
                if i % 20 == 0:
                    save_j('downloads.json', dl); print('dl', i, len(jobs), flush=True)
    save_j('downloads.json', dl)
    from collections import Counter
    print(Counter((v.get('lvl'), v.get('ok')) for v in dl.values()))


# ---------------------------------------------------------------- patches
COLL_ID_RE = re.compile(r'(artwork|object|work|collection|painting|item|inventory|catalog(ue)?) (ID|identifier|number)', re.I)
COLL_ID_BAD = re.compile(r'RKD|Joconde|Art UK|WikiArt|Europeana|Google Arts|BALaT|Bildindex|Catalog of|Wikimedia|'
                         r'Bridgeman|Web Gallery|Smarthistory|Artsy|Zeri|Mutual Art|ArtUK|Pop|Gazette|DACS', re.I)


def best_link(q, mu):
    ent = item_info([q]).get(q, {})
    p973 = [v for v in claim_vals(ent, 'P973') if isinstance(v, str) and v.startswith('http')]
    if p973:
        return p973[0], 'Wikidata P973（described at URL）'
    ext = [p for p, cl in ent.get('claims', {}).items() if cl and cl[0].get('mainsnak', {}).get('datatype') == 'external-id']
    if ext:
        pinfo = item_info(ext)
        scored = []
        for p in ext:
            pe = pinfo.get(p, {})
            lab = (pe.get('labels', {}).get('en') or {}).get('value', '')
            fmt = claim_vals(pe, 'P1630')
            if not fmt or not COLL_ID_RE.search(lab) or COLL_ID_BAD.search(lab):
                continue
            val = claim_vals(ent, p)[0]
            s = fuzz.token_set_ratio(mu_norm(mu or ''), mu_norm(lab)) if mu else 0
            scored.append((s, lab, fmt[0].replace('$1', quote(str(val), safe=''))))
        scored.sort(reverse=True)
        if scored and (scored[0][0] >= 60 or len(scored) == 1):
            return scored[0][2], f'Wikidata {scored[0][1]}'
    for lang in ('en', 'fr', 'it', 'de', 'es', 'nl'):
        sl = ent.get('sitelinks', {}).get(lang + 'wiki')
        if sl:
            return f'https://{lang}.wikipedia.org/wiki/' + quote(sl['title'].replace(' ', '_')), f'{lang}.wikipedia 條目'
    return f'https://www.wikidata.org/wiki/{q}', 'Wikidata 頁面'


def wd_record(q, w):
    ent = item_info([q]).get(q, {})
    lab = (ent.get('labels', {}).get('en') or ent.get('labels', {}).get('mul') or {}).get('value') or w['t']
    ys = [v for v in claim_vals(ent, 'P571') if isinstance(v, int)]
    colls = labels_of([c for c in claim_vals(ent, 'P195') if isinstance(c, str)]) if claim_vals(ent, 'P195') else {}
    rec = dict(w.get('wd') or {})
    rec.update({'q': q, 'label': lab, 'y': ys[:2], 'mu': [v for v in colls.values() if v][:3]})
    return rec


SRC_NAME = {'Met': ('Met', 'https://www.metmuseum.org'), 'AIC': ('AIC', ''), 'Cleveland': ('Cleveland', ''), 'SMK': ('SMK', '')}


def stage_patch():
    results = load_j('results.json', {})
    dl = load_j('downloads.json', {})
    p100 = {'file': 'works.json', 'key': 'id', 'description': 'art_images: 替無圖作品補圖（高信心配對）；含複核後應改 pd=true 者',
            'set': {}, 'evidence': {}}
    p110 = {'file': 'works.json', 'key': 'id', 'description': 'art_links: pd=false 作品改用更好的來源連結並補 wd.q',
            'set': {}, 'evidence': {}}
    review, pdfix = [], []
    for wid, r in sorted(results.items()):
        w = WID[wid]
        d = dl.get(wid)
        death = artists.get(w['a'], {}).get('death')
        if not w['pd'] and death and death <= 1955:
            pdfix.append(wid)
        if d and d.get('ok') and d['lvl'] == 'high':
            b = d['best']
            fields = {'local': True, 'img': f'img/800/{wid}.webp', 'thumb': f'img/thumb/{wid}.webp'}
            if b.get('file'):
                fn = b['file'].replace('_', ' ')
                fields.update({'src': 'Commons', 'src_url': 'https://commons.wikimedia.org/wiki/File:' + quote(fn.replace(' ', '_')),
                               'img_ext': commons_url(fn)})
            else:
                fields.update({'src': b['src'], 'src_url': b['page'], 'img_ext': b['img']})
            how = {'Wikidata P18': f'Wikidata 作品條目 {r["q"]}（作者 P170 + 題名比對）的 P18 圖檔'}.get(b['via'], f'{b["via"]}')
            qd = (r.get('qcands') or [{}])[0]
            chk = f"題名相似度 {qd.get('t', b.get('t', '?'))}、年代{'相符' if qd.get('yr', b.get('yr')) == 'ok' else '未能比對'}、館藏{'相符' if qd.get('mu', b.get('mu')) == 'ok' else '未能比對'}"
            fields['verif'] = f'自動補圖（{TODAY}）：{how}；{chk}，判為高信心同一作品'
            if r.get('q') and r.get('q_level') == 'high':
                fields['wd'] = wd_record(r['q'], w)
            if not w['pd']:
                fields['pd'] = True
                fields['verif'] += f'；畫家卒於 {death}，已逝世滿 70 年，改為公有領域'
            p100['set'][wid] = fields
            p100['evidence'][wid] = (fields['src_url'] + (f' ; https://www.wikidata.org/wiki/{r["q"]}' if r.get('q') else ''))
        elif d and d.get('ok') and d['lvl'] == 'medium':
            review.append(wid)
        elif r['cands'] and r['cands'][0]['lvl'] == 'medium':
            review.append(wid)
        # ---- links for pd=false
        if not w['pd'] and wid not in p100['set'] and r.get('q') and r.get('q_level') == 'high':
            url, how = best_link(r['q'], w.get('mu'))
            fields = {'wd': wd_record(r['q'], w)}
            if 'index.php?search=' in (w.get('src_url') or ''):
                fields['src_url'] = url
            p110['set'][wid] = fields
            p110['evidence'][wid] = f'https://www.wikidata.org/wiki/{r["q"]} ；連結來源：{how}'
    os.makedirs(os.path.join(R, 'tools', 'patches'), exist_ok=True)
    for name, p in (('100_art_images.json', p100), ('110_art_links.json', p110)):
        with open(os.path.join(R, 'tools', 'patches', name), 'w', encoding='utf-8') as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
    save_j('summary.json', {'review': review, 'pdfix': pdfix, 'n100': len(p100['set']), 'n110': len(p110['set'])})
    print('100:', len(p100['set']), '110:', len(p110['set']), 'review:', len(review), 'pdfix:', pdfix)


if __name__ == '__main__':
    st = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if st in ('match', 'all'):
        stage_match()
    if st in ('download', 'all'):
        stage_download()
    if st in ('patch', 'all'):
        stage_patch()
