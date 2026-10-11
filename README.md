# flask-crawler-simple

A small Flask app that fetches a URL, parses the HTML, and returns the page's
title, meta description, a short body preview, and the most frequent content
words. There's a one-page browser UI at `/` and a JSON endpoint at
`/api/crawl` that the UI calls.

Same `app` object runs locally and on AWS Lambda via `lambda_handler.py`.

## Live demo

https://hjvmsd4lol4kw7xzlcy5brn3hu0llqzb.lambda-url.us-east-1.on.aws/

Running on AWS Lambda behind a Function URL. It's an open demo with no auth
and no rate limiting, so treat it as a throwaway — see
[Known limitations](#known-limitations) before pointing it at anything.

## Requirements

- Python 3.9 or newer (3.12 is what the Lambda build targets)
- Outbound HTTPS, since the crawler fetches whatever URL you give it

## Setup

```bash
git clone <your-repo-url>
cd flask-crawler-simple

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Run

```bash
python flask_crawl.py
```

Serves on http://127.0.0.1:5002 with debug mode on. Open that in a browser,
paste a URL, and the page renders the result in place.

To use a different port, change the `app.run(...)` call at the bottom of
`flask_crawl.py`.

## API

`POST /api/crawl`

```bash
curl -X POST http://127.0.0.1:5002/api/crawl \
  -H 'Content-Type: application/json' \
  -d '{"url": "example.com"}'
```

A URL with no scheme is treated as HTTPS, so `example.com` becomes
`https://example.com`.

Success (200):

```json
{
  "url": "https://example.com",
  "status_code": 200,
  "title": "Example Domain",
  "description": "No Description",
  "relevant_topics": ["example", "domain", "information"],
  "body_preview": "Example Domain This domain is for use in ..."
}
```

Missing or empty `url` returns 400 with `{"error": "No URL provided"}`.

A failed fetch still returns 200 — the crawl failed, not the request — with
`url`, `error_type` (`HTTP Error`, `Connection Error`, `Timeout Error` or
`Unexpected Error`) and `details`.

## How it works

- `requests` session with connection pooling and bounded retries: 3 attempts,
  exponential backoff, GET only, retried on 429/500/502/503/504. Timeouts are
  5s connect / 15s read.
- BeautifulSoup (`html.parser`) pulls `<title>`, the `description` meta tag,
  and body text.
- Topics are raw term frequency: words of 4+ letters, lowercased, minus a
  short stop-word list, top 5 by count.

## Files

| File | What it's for |
|---|---|
| `flask_crawl.py` | The app — routes, fetch, parse, topic ranking |
| `templates/index.html` | The browser UI |
| `requirements.txt` | Deps for running locally |
| `lambda_handler.py` | Lambda entrypoint (Mangum + a2wsgi) |
| `requirements-lambda.txt` | Deps for the Lambda package |

## Known limitations

This is a demo, not a production crawler.

- **No SSRF protection.** Any URL supplied is fetched as-is, including private,
  loopback and link-local addresses. Exposed publicly without auth, this is an
  open fetch proxy — don't deploy it that way without an allowlist, a
  private-IP check, or at minimum a shared-secret header.
- **No robots.txt handling.** Pages are fetched regardless of what the host's
  robots.txt says.
- **The live demo above is this exact code, unauthenticated.** Anyone with the
  link can make it fetch any URL.
- **Debug mode is on** in `flask_crawl.py`. Fine locally, not for anything
  reachable by others.
- **Topic ranking is naive.** Site furniture ("sign", "account") competes with
  real content words.
