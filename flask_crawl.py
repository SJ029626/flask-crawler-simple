"""Web crawler demo: fetch a URL, extract its metadata, and rank its topics.

Exposes a small Flask application with two routes: a browser UI at ``/`` and a
JSON endpoint at ``/api/crawl`` that the UI calls. Given a URL, the app fetches
the page, parses the HTML with BeautifulSoup, and returns the title, meta
description, a short body preview, and the most frequent content words.

Run locally with ``python flask_crawl.py`` (serves on port 5002). The same
``app`` object is served on AWS Lambda through ``lambda_handler.py``.

Scope and limitations, since this is a demo rather than a production crawler:

* No SSRF protection. Any URL the caller supplies is fetched as-is, including
  private, loopback and link-local addresses. On a public endpoint that makes
  this an open fetch proxy.
* No robots.txt handling. Pages are fetched regardless of what the host's
  robots.txt says.
* Topic ranking is raw term frequency against a tiny stop-word list, so common
  site furniture ("sign", "account") competes with real content words.
"""

from flask import Flask, request, jsonify, render_template
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
from collections import Counter

app = Flask(__name__)

def extract_topics(text, top_n=5):
    """Rank the most frequent content words in a block of text.

    Words are lowercased, and anything shorter than four characters or
    containing non-letters is dropped before counting. That cheaply removes
    most articles, prepositions and numeric noise, so ``stop_words`` only has
    to catch the common four-plus letter words that survive the filter.

    Args:
        text: Plain text to analyse, typically the page body.
        top_n: How many topics to return.

    Returns:
        Up to ``top_n`` words, most to least frequent. Ties break by first
        appearance, which is ``Counter.most_common`` behaviour. Empty list if
        ``text`` holds no qualifying words.
    """
    candidate_words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    stop_words = {'this', 'that', 'with', 'from', 'your', 'have', 'more', 'about', 'they', 'that', 'will'}
    content_words = [word for word in candidate_words if word not in stop_words]
    ranked_words = Counter(content_words).most_common(top_n)
    return [word for word, _frequency in ranked_words]

def build_crawler_session():
    """Build a requests session with connection pooling and bounded retries.

    Retries are capped at three attempts with exponential backoff, and only for
    GET requests failing with a rate-limit or server-side status. Client errors
    such as 404 are not retried, since repeating them will not help.

    Worst-case wall time matters when this runs on Lambda: three retries at
    ``backoff_factor=1`` wait 0s, 2s and 4s between attempts, and each attempt
    can spend up to the 15 second read timeout set in ``crawl_url``. Budget
    roughly 50 seconds before the function timeout.

    Returns:
        A configured ``requests.Session``. Callers are meant to reuse it rather
        than build one per request, so TCP and TLS connections are pooled
        across invocations.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    retry_adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", retry_adapter)
    session.mount("https://", retry_adapter)
    return session

# One session for the whole application lifetime, so connections are pooled.
crawler_session = build_crawler_session()

def crawl_url(url, session):
    """Fetch a URL and extract its metadata and topics.

    Sends a browser-like User-Agent, because a number of sites serve a
    challenge page or an outright block to the default requests agent. Timeouts
    are five seconds to connect and fifteen to read.

    Never raises: every failure is caught and returned as a dict, so the caller
    can hand it straight back to the client. Check for the presence of
    ``error_type`` to tell the two shapes apart.

    Args:
        url: Absolute URL to fetch, including the scheme.
        session: Session to fetch through, normally the module-level
            ``crawler_session`` so connections are reused.

    Returns:
        On success, a dict with ``url``, ``status_code``, ``title``,
        ``description``, ``relevant_topics`` and ``body_preview``. ``title`` and
        ``description`` fall back to "No Title" and "No Description" when the
        page omits them.

        On failure, a dict with ``url``, ``error_type`` (one of "HTTP Error",
        "Connection Error", "Timeout Error" or "Unexpected Error") and
        ``details`` carrying the underlying exception text.
    """
    request_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = session.get(url, headers=request_headers, timeout=(5, 15))
        response.raise_for_status()

        parsed_page = BeautifulSoup(response.text, 'html.parser')

        page_title = parsed_page.title.string.strip() if parsed_page.title and parsed_page.title.string else "No Title"
        description_tag = parsed_page.find('meta', attrs={'name': 'description'})
        page_description = description_tag['content'].strip() if description_tag and description_tag.has_attr('content') else "No Description"

        body_text = parsed_page.body.get_text(separator=' ', strip=True) if parsed_page.body else ""
        topics = extract_topics(body_text)

        return {
            "url": url,
            "status_code": response.status_code,
            "title": page_title,
            "description": page_description,
            "relevant_topics": topics,
            "body_preview": body_text[:200] + "..." if body_text else ""
        }

    except requests.exceptions.HTTPError as http_error:
        return {"url": url, "error_type": "HTTP Error", "details": str(http_error)}
    except requests.exceptions.ConnectionError as connection_error:
        return {"url": url, "error_type": "Connection Error", "details": str(connection_error)}
    except requests.exceptions.Timeout as timeout_error:
        return {"url": url, "error_type": "Timeout Error", "details": str(timeout_error)}
    except Exception as unexpected_error:
        return {"url": url, "error_type": "Unexpected Error", "details": str(unexpected_error)}

@app.route('/')
def index():
    """Serve the single-page UI.

    Returns:
        The rendered ``templates/index.html``, which posts to ``/api/crawl``
        from the browser and renders the JSON response in place.
    """
    return render_template('index.html')

@app.route('/api/crawl', methods=['POST'])
def api_crawl():
    """Crawl the URL in the request body and return the result as JSON.

    Expects a JSON body of the form ``{"url": "..."}``. A bare host or path
    with no scheme is treated as HTTPS, so "example.com" is accepted and
    fetched as "https://example.com".

    Returns:
        400 and ``{"error": "No URL provided"}`` when ``url`` is missing or
        empty. Otherwise 200 and whatever ``crawl_url`` produced.

        Note that a failed fetch is still a 200: the crawl failed, not this
        request, so the error travels in the body as ``error_type`` and
        ``details``.
    """
    request_payload = request.get_json()
    target_url = request_payload.get('url')

    if not target_url:
        return jsonify({"error": "No URL provided"}), 400

    # Ensure the URL has a scheme before handing it to requests.
    if not target_url.startswith('http://') and not target_url.startswith('https://'):
        target_url = 'https://' + target_url

    crawl_result = crawl_url(target_url, crawler_session)
    return jsonify(crawl_result)

if __name__ == '__main__':
    app.run(debug=True, port=5002)
