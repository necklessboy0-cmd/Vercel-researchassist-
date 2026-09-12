from flask import Flask, jsonify, request, send_from_directory
import os
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse

app = Flask(__name__)

@app.get("/")
def frontend():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")
TIMEOUT = 12
MAX_RESULTS = 10
MAX_PAGE_CHARS = 7000
MAX_SOURCE_CHARS = 45000
HEADERS = {"User-Agent": "Mozilla/5.0 Research Guide/1.0"}


def build_query(query, filters):
    parts = [query.strip()] if query.strip() else []
    domains = {
        "Google Scholar": "scholar.google.com", "IEEE Xplore": "ieeexplore.ieee.org",
        "PubMed": "pubmed.ncbi.nlm.nih.gov", "Crossref": "crossref.org", "arXiv": "arxiv.org",
        "Semantic Scholar": "semanticscholar.org", "DOAJ": "doaj.org", "JSTOR": "jstor.org",
        "ScienceDirect": "sciencedirect.com", "SpringerLink": "link.springer.com",
        "Wiley": "onlinelibrary.wiley.com", "ACM Digital Library": "dl.acm.org",
    }
    db = filters.get("database", "Any database")
    if db in domains: parts.append(f"site:{domains[db]}")
    source_terms = {
        "Journal article": '"journal article"', "Conference paper": '"conference paper"',
        "Book": 'book', "Book chapter": '"book chapter"', "Thesis / dissertation": 'thesis dissertation',
        "Preprint": 'preprint', "Technical report": '"technical report"',
        "Government report": '"government report"', "Dataset": 'dataset', "Patent": 'patent',
        "News article": '"news article"', "Systematic review": '"systematic review"',
        "Meta-analysis": '"meta-analysis"'
    }
    types = filters.get("source_types", [])
    if types:
        parts.append("(" + " OR ".join(source_terms[x] for x in types if x in source_terms) + ")")
    for key in ("author", "publisher", "journal", "location"):
        if filters.get(key): parts.append(f'"{filters[key].strip()}"')
    language = filters.get("language", "Any language")
    if language != "Any language": parts.append(language)
    methods = filters.get("methods", [])
    if methods: parts.append("(" + " OR ".join(f'"{m}"' for m in methods) + ")")
    if filters.get("peer_reviewed"): parts.append('"peer reviewed"')
    if filters.get("open_access"): parts.append('"open access"')
    if filters.get("q1"): parts.append('"Q1"')
    if filters.get("date_from"): parts.append(f'after:{filters["date_from"]}')
    if filters.get("date_to"): parts.append(f'before:{filters["date_to"]}')
    return " ".join(parts)


def search_text(query, filters):
    with DDGS(timeout=TIMEOUT) as ddgs:
        items = ddgs.text(build_query(query, filters), region="wt-wt", safesearch="moderate", max_results=MAX_RESULTS, backend="auto")
        return [{"title": i.get("title", "Untitled"), "url": i.get("href", ""), "snippet": i.get("body", "")} for i in items]


def search_news(query):
    with DDGS(timeout=TIMEOUT) as ddgs:
        return list(ddgs.news(query, region="wt-wt", safesearch="moderate", max_results=8))


def search_images(query):
    with DDGS(timeout=TIMEOUT) as ddgs:
        return list(ddgs.images(query, region="wt-wt", safesearch="moderate", max_results=8))


def extract_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "").lower(): return ""
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe"]): tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:MAX_PAGE_CHARS]
    except Exception:
        return ""


def sentences(text):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", text or "") if len(x.strip()) > 35]


def analysis(query, source_text):
    ss = sentences(source_text)
    evidence = [s for s in ss if any(k in s.lower() for k in ["according to", "official", "government", "study", "research", "researchers", "scientists", "report", "data", "confirmed", "published", "university", "survey", "evidence", "announced"])][:8]
    warnings = [s for s in ss if any(k in s.lower() for k in ["false", "fake", "hoax", "misleading", "debunked", "incorrect", "rumor", "rumour", "not true", "unverified", "fabricated", "disinformation", "misinformation"])][:8]
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9']+", query) if len(w) > 2}
    scored = []
    for s in ss:
        sw = set(re.findall(r"[A-Za-z0-9']+", s.lower()))
        score = 2 * len(sw & words) + len(sw & {"study", "research", "official", "evidence", "report", "data"})
        if score: scored.append((score, s))
    scored.sort(reverse=True)
    answer = " ".join([s for _, s in scored[:5]]) or "The available sources do not contain enough readable text to produce a grounded answer."
    return {"answer": answer, "facts": evidence, "warnings": warnings}


@app.get("/api")
def home():
    return jsonify({"name": "Research Guide", "status": "ok"})


@app.post("/api/search")
def search():
    data = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()
    if not query: return jsonify({"error": "Enter something to search."}), 400
    filters = data.get("filters") or {}
    try:
        results = search_text(query, filters)
        pieces, status = [], []
        for idx, result in enumerate(results[:6], 1):
            if result.get("snippet"): pieces.append(result["snippet"])
            page = extract_page(result.get("url", ""))
            status.append({"index": idx, "read": bool(page)})
            if page: pieces.append(page)
        source_text = "\n".join(pieces)[:MAX_SOURCE_CHARS]
        return jsonify({"query": query, "results": results, "analysis": analysis(query, source_text), "page_status": status})
    except Exception as exc:
        return jsonify({"error": f"Search error: {exc}"}), 502


@app.post("/api/news")
def news():
    query = str((request.get_json(silent=True) or {}).get("query", "")).strip()
    if not query: return jsonify({"error": "Enter something to search."}), 400
    try: return jsonify({"items": search_news(query)})
    except Exception as exc: return jsonify({"error": str(exc)}), 502


@app.post("/api/images")
def images():
    query = str((request.get_json(silent=True) or {}).get("query", "")).strip()
    if not query: return jsonify({"error": "Enter something to search."}), 400
    try: return jsonify({"items": search_images(query)})
    except Exception as exc: return jsonify({"error": str(exc)}), 502
