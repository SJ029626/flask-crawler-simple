"""AWS Lambda entrypoint for the Flask crawler.

Lambda Function URLs deliver a payload format 2.0 event, which Mangum knows how
to translate into an ASGI call. Flask speaks WSGI, so ``a2wsgi.WSGIMiddleware``
sits between them and runs the WSGI app in a worker thread.

``flask_crawl`` is imported unchanged, so the same application object serves
both ``python flask_crawl.py`` locally and this function in Lambda. Nothing in
the app is Lambda-aware.

Configure the function with handler ``lambda_handler.handler``. ``templates/``
has to ship inside the deployment package so ``render_template`` can resolve
``index.html`` relative to ``/var/task``.

Lifespan is disabled because WSGI has no startup or shutdown protocol, and
leaving it on makes Mangum wait on events that never arrive.
"""
from __future__ import annotations

from a2wsgi import WSGIMiddleware
from mangum import Mangum

from flask_crawl import app as flask_app

#: Lambda entrypoint. Takes the Function URL event and context, returns the
#: HTTP response Lambda sends back to the client.
handler = Mangum(WSGIMiddleware(flask_app), lifespan="off")
